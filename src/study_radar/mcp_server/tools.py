"""Реализация MCP-инструментов (работает через TelegramHub и SQLite).

Обёртки FastMCP — в server.py. Здесь — логика, которую удобно тестировать напрямую.
Все методы возвращают dict с ключом ok; ошибки — понятным текстом, чтобы LLM могла исправиться.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Any

from study_radar.agent.dates import fmt_due
from study_radar.agent.dedup import find_duplicate
from study_radar.ingest.normalize import message_link
from study_radar.services import Services
from study_radar.storage.db import text_hash
from study_radar.telegram.board import refresh_board
from study_radar.telegram.notes import deadline_note, subject_name

log = logging.getLogger(__name__)

KINDS = {"deadline", "event", "assignment"}
EDIT_MARK = "✏️ Сообщение отредактировано · "


class ToolError(ValueError):
    pass


class RadarTools:
    def __init__(self, services: Services):
        self.sv = services
        self.s = services.s
        self.db = services.db

    @property
    def hub(self):
        return self.sv.hub

    # ------------------------------------------------------------------ helpers
    def _topic_key(self, subject: str | None) -> str:
        if subject is None or str(subject).strip().lower() in {"", "none", "null", "general", "общее"}:
            return "general"
        subject = str(subject).strip()
        if subject in self.s.subjects:
            return subject
        for key, subj in self.s.subjects.items():   # LLM могла передать название вместо ключа
            if subject.lower() in {subj.name.lower(), *(a.lower() for a in subj.aliases)}:
                return key
        raise ToolError(f"Неизвестный предмет «{subject}». Допустимые ключи: {', '.join(self.s.subjects)} "
                        f"или null для «Общее».")

    def _subject_or_none(self, subject: str | None) -> str | None:
        key = self._topic_key(subject)
        return None if key == "general" else key

    def _parse_due(self, due_at: str | None) -> datetime | None:
        if due_at is None or str(due_at).strip().lower() in {"", "null", "none"}:
            return None
        try:
            dt = datetime.fromisoformat(str(due_at).strip().replace("Z", "+00:00"))
        except ValueError as e:
            raise ToolError(f"due_at должен быть в ISO 8601, например 2026-10-30T16:00:00+03:00 (получено {due_at!r})") from e
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.s.tz)
        return dt.astimezone(self.s.tz)

    @staticmethod
    def _parse_ref(ref: Any) -> tuple[int, int]:
        if isinstance(ref, dict):
            return int(ref["chat_id"]), int(ref["message_id"])
        chat, _, mid = str(ref).rpartition(":")
        if not chat:
            raise ToolError(f"source_ref должен иметь вид «chat_id:message_id», получено {ref!r}")
        return int(chat), int(mid)

    def _link(self, chat_id: int, message_id: int) -> str:
        row = self.db.get_message(chat_id, message_id)
        return (row or {}).get("link") or message_link(chat_id, message_id)

    def _deadline_dict(self, d) -> dict[str, Any]:
        return {"id": d.id, "subject": d.subject, "title": d.title, "kind": d.kind,
                "due_at": d.due_at.isoformat() if d.due_at else None, "due": fmt_due(d.due_at),
                "due_text": d.due_text, "location": d.location, "status": d.status,
                "sources": len(d.sources)}

    async def _link_notes(self, deadline_id: int, refs: list[tuple[int, int]]) -> None:
        for chat_id, mid in refs:
            for n in self.db.hub_notes_for_sources(chat_id, [mid]):
                if not n["deadline_id"]:
                    self.db.update_hub_message(n["id"], deadline_id=deadline_id)

    async def _refresh_notes(self, deadline_id: int, old) -> int | None:
        d = self.db.get_deadline(deadline_id)
        notes = self.db.hub_notes_for_deadline(deadline_id)
        for n in notes:
            prefix = EDIT_MARK if (n["text"] or "").startswith(EDIT_MARK) else ""
            await self.hub.edit(n["id"], prefix + deadline_note(self.s, d, old))
        return notes[0]["id"] if notes else None

    # ------------------------------------------------------------------ tools
    async def forward_to_topic(self, subject: str | None, source_chat_id: int, message_ids: list[int],
                               note: str) -> dict[str, Any]:
        key = self._topic_key(subject)
        rows = self.db.get_messages(int(source_chat_id), [int(m) for m in message_ids])
        if not rows:
            raise ToolError(f"Сообщения {message_ids} в чате {source_chat_id} не найдены")
        todo, re_forward = [], False
        for r in rows:
            prev = self.db.hub_forwards_of(r["chat_id"], r["message_id"], key)
            if prev and prev[-1]["source_text_hash"] == text_hash(r["text"] or ""):
                continue
            re_forward = re_forward or bool(prev)
            todo.append(r)
        title = self.hub.topic_title(key)
        if not todo:
            return {"ok": True, "topic": title, "forwarded": 0, "note": "уже переслано ранее"}
        ids = await self.hub.forward(key, todo)
        dl = self.db.deadlines_for_messages(int(source_chat_id), [r["message_id"] for r in rows])
        note_text = html.escape((note or "").strip()[:400]) or "🤖 Важное сообщение"
        if re_forward:
            note_text = EDIT_MARK + note_text
        note_id = await self.hub.send(
            key, note_text, kind="note", reply_to=ids[0], source_chat_id=rows[0]["chat_id"],
            source_message_id=rows[0]["message_id"], source_link=rows[0]["link"], deadline_id=dl[0] if dl else None)
        return {"ok": True, "topic": title, "forwarded": len(ids), "note_id": note_id}

    async def create_deadline(self, subject: str | None, title: str, due_at: str | None, due_text: str | None,
                              location: str | None, source_refs: list[str], kind: str = "deadline") -> dict[str, Any]:
        subj = self._subject_or_none(subject)
        due = self._parse_due(due_at)
        kind = kind if kind in KINDS else "deadline"
        title = (title or "").strip()[:120] or "Без названия"
        refs = [self._parse_ref(r) for r in (source_refs or [])]
        dup, sim = find_duplicate(self.db, self.sv.embedder, self.s, subj, title, due, kind)
        if dup is not None:
            added = False
            for chat_id, mid in refs:
                added |= self.db.add_deadline_source(dup.id, chat_id, mid, self._link(chat_id, mid))
            if added:
                c, m = refs[0]
                self.db.add_history(dup.id, "source_added", {}, c, m, self._link(c, m),
                                    f"Тот же дедлайн найден в другом сообщении (похожесть {sim})")
                self.db.update_deadline_fields(dup.id)
            await self._link_notes(dup.id, refs)
            return {"ok": True, "id": dup.id, "created": False, "duplicate_of": dup.id, "similarity": sim,
                    "message": "Такой дедлайн уже есть — добавлен источник, дубль не создан"}
        new_id = self.db.create_deadline(subj, title, kind, due, (due_text or None), (location or None))
        for chat_id, mid in refs:
            self.db.add_deadline_source(new_id, chat_id, mid, self._link(chat_id, mid))
        c, m = refs[0] if refs else (None, None)
        self.db.add_history(new_id, "created", {"due_at": [None, due.isoformat() if due else None]}, c, m,
                            self._link(c, m) if c else None, f"Создан: {title}")
        await self._link_notes(new_id, refs)
        return {"ok": True, "id": new_id, "created": True, "due": fmt_due(due)}

    async def update_deadline(self, deadline_id: int, due_at: str | None = None, title: str | None = None,
                              location: str | None = None, status: str | None = None,
                              source_ref: str | None = None, change_note: str | None = None) -> dict[str, Any]:
        old = self.db.get_deadline(int(deadline_id))
        if old is None:
            raise ToolError(f"Дедлайн id={deadline_id} не найден. Посмотрите list_deadlines.")
        fields: dict[str, Any] = {}
        changes: dict[str, list[Any]] = {}
        due = self._parse_due(due_at)
        if due is not None and due != old.due_at:
            fields.update(due_at=due, prev_due_at=old.due_at)
            changes["due_at"] = [old.due_at.isoformat() if old.due_at else None, due.isoformat()]
        if title and title.strip() and title.strip() != old.title:
            fields["title"] = title.strip()[:120]
            changes["title"] = [old.title, fields["title"]]
        if location and location.strip() and location.strip() != (old.location or ""):
            fields.update(location=location.strip(), prev_location=old.location)
            changes["location"] = [old.location, location.strip()]
        if status and status in {"active", "cancelled", "done"} and status != old.status:
            fields["status"] = status
            changes["status"] = [old.status, status]
        ref = self._parse_ref(source_ref) if source_ref else None
        link = self._link(*ref) if ref else None
        if ref:
            new_src = self.db.add_deadline_source(old.id, ref[0], ref[1], link)
            await self._link_notes(old.id, [ref])
        else:
            new_src = False
        if not fields:
            if new_src:
                self.db.add_history(old.id, "source_added", {}, ref[0], ref[1], link,
                                    change_note or "Подтверждение того же срока")
                self.db.update_deadline_fields(old.id)
            return {"ok": True, "id": old.id, "changed": False, "message": "Изменений нет (источник учтён)"}

        self.db.update_deadline_fields(old.id, **fields)
        self.db.add_history(old.id, "updated", changes, ref[0] if ref else None, ref[1] if ref else None, link,
                            change_note)
        first_note = await self._refresh_notes(old.id, old)
        new = self.db.get_deadline(old.id)
        bits = []
        if "due_at" in changes:
            bits.append(f"было {fmt_due(old.due_at)} → стало <b>{fmt_due(new.due_at)}</b>")
        if "location" in changes:
            bits.append(f"место: {html.escape(old.location or '—')} → <b>{html.escape(new.location or '')}</b>")
        if "title" in changes:
            bits.append(f"название: {html.escape(new.title)}")
        if "status" in changes:
            bits.append(f"статус: {new.status}")
        head = "⚠️ Перенос" if "due_at" in changes else "📍 Уточнение"
        text = f"{head}: {html.escape(new.title)} ({html.escape(subject_name(self.s, new.subject))}) — " + "; ".join(bits)
        if change_note:
            text += f"\n<i>{html.escape(change_note[:200])}</i>"
        if link:
            text += f'\n<a href="{link}">↗ источник</a>'
        await self.hub.send(self._topic_key(new.subject), text, kind="update", reply_to=first_note,
                            deadline_id=new.id, source_chat_id=ref[0] if ref else None,
                            source_message_id=ref[1] if ref else None, source_link=link)
        return {"ok": True, "id": new.id, "changed": True, "changes": changes}

    async def close_deadline(self, deadline_id: int, reason: str, status: str = "cancelled") -> dict[str, Any]:
        old = self.db.get_deadline(int(deadline_id))
        if old is None:
            raise ToolError(f"Дедлайн id={deadline_id} не найден")
        status = status if status in {"cancelled", "done"} else "cancelled"
        if old.status == status:
            return {"ok": True, "id": old.id, "changed": False}
        self.db.update_deadline_fields(old.id, status=status, close_reason=(reason or "")[:300])
        self.db.add_history(old.id, "closed", {"status": [old.status, status]}, note=reason)
        first_note = await self._refresh_notes(old.id, old)
        word = "Отменено" if status == "cancelled" else "Выполнено"
        await self.hub.send(self._topic_key(old.subject),
                            f"❌ {word}: {html.escape(old.title)} — {html.escape(reason or '')}",
                            kind="update", reply_to=first_note, deadline_id=old.id)
        return {"ok": True, "id": old.id, "changed": True, "status": status}

    async def list_deadlines(self, subject: str | None = None, include_past: bool = False) -> dict[str, Any]:
        subj = self._subject_or_none(subject) if subject else None
        since = None if include_past else self.db.now()
        items = [self._deadline_dict(d) for d in self.db.list_deadlines(subject=subj, since=since)]
        return {"ok": True, "count": len(items), "deadlines": items}

    async def search_messages(self, query: str, subject: str | None = None, limit: int = 5) -> dict[str, Any]:
        subj = self._subject_or_none(subject) if subject else None
        hits = self.sv.retriever.search(query, k=max(1, min(int(limit), 10)), subject=subj)
        return {"ok": True, "results": [
            {"n": i + 1, "chat": h.meta["chat_title"], "author": h.meta["author"], "date": h.meta["date"],
             "text": h.text[:400], "link": h.meta["link"], "subject": h.meta["subject"]}
            for i, h in enumerate(hits)]}

    async def get_message_context(self, chat_id: int, message_id: int) -> dict[str, Any]:
        msg = self.db.get_message(int(chat_id), int(message_id))
        if msg is None:
            raise ToolError("Сообщение не найдено")
        reply = self.db.get_message(int(chat_id), msg["reply_to"]) if msg["reply_to"] else None
        brief = lambda r: {"id": r["message_id"], "author": r["author"], "date": r["date"],  # noqa: E731
                           "text": (r["text"] or "")[:400]}
        return {"ok": True, "message": brief(msg), "reply_to": brief(reply) if reply else None,
                "neighbors": [brief(r) for r in self.db.neighbors(int(chat_id), int(message_id))]}

    async def refresh_deadlines_board(self) -> dict[str, Any]:
        res = await refresh_board(self.hub, self.db, self.s)
        return {"ok": True, **res}

    async def flag_for_review(self, chat_id: int, message_ids: list[int], reason: str) -> dict[str, Any]:
        rows = self.db.get_messages(int(chat_id), [int(m) for m in message_ids])
        if not rows:
            raise ToolError("Сообщения не найдены")
        if any(self.db.hub_forwards_of(r["chat_id"], r["message_id"], "review") for r in rows):
            return {"ok": True, "topic": self.hub.topic_title("review"), "note": "уже на проверке"}
        ids = await self.hub.forward("review", rows)
        await self.hub.send("review", f"🤔 На проверку: {html.escape(reason or 'низкая уверенность')}", kind="note",
                            reply_to=ids[0], source_chat_id=rows[0]["chat_id"],
                            source_message_id=rows[0]["message_id"], source_link=rows[0]["link"])
        return {"ok": True, "topic": self.hub.topic_title("review")}
