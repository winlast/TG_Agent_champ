"""Доска дедлайнов: одно (или несколько) закреплённых сообщений в топике «📅 Дедлайны».

Telegram не умеет пересортировывать сообщения, поэтому бот держит закреп и редактирует его
(editMessageText) при каждом изменении. Лимит 4096 символов → страницы.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta

from study_radar.agent.dates import WEEKDAY_SHORT, fmt_due
from study_radar.config import Settings
from study_radar.models import Deadline
from study_radar.storage.db import Database
from study_radar.telegram.hub import TelegramHub

PAGE_LIMIT = 3800


def _subject_name(settings: Settings, key: str | None) -> str:
    if key and key in settings.subjects:
        return settings.subjects[key].name
    return ""


def _time_or_day(dt: datetime) -> str:
    return "весь день" if (dt.hour, dt.minute) == (0, 0) else f"{dt:%H:%M}"


def _line(d: Deadline, settings: Settings, short: bool) -> str:
    when = _time_or_day(d.due_at) if (short and d.due_at) else fmt_due(d.due_at)
    mark = "📌 " if d.kind == "event" else ""
    title = html.escape(d.title)
    subj = _subject_name(settings, d.subject)
    body = f"{mark}{title}" + (f" ({html.escape(subj)})" if subj else "")
    if d.status == "cancelled":
        body = f"<s>{body}</s> — отменено"
    parts = [f"• {when} — {body}"]
    if d.location:
        parts.append(html.escape(d.location))
    if d.prev_due_at and d.status == "active" and d.prev_due_at != d.due_at:
        parts.append(f"было: {fmt_due(d.prev_due_at)}")
    elif d.prev_location and d.status == "active" and d.prev_location != d.location:
        parts.append(f"было: {html.escape(d.prev_location)}")
    line = " · ".join(parts)
    links = [s["link"] for s in d.sources if s.get("link")]
    if links:
        line += " " + " ".join(f'<a href="{html.escape(u)}">↗</a>' for u in links[:3])
    return line


def render_board(deadlines: list[Deadline], now: datetime, settings: Settings) -> list[str]:
    today = now.date()
    sections: dict[str, list[Deadline]] = {k: [] for k in ("today", "tomorrow", "week", "later", "nodate", "past")}
    for d in deadlines:
        if d.status == "done":
            continue
        if d.status == "cancelled" and (d.updated_at is None or now - d.updated_at > timedelta(days=3)):
            continue
        if d.due_at is None:
            sections["nodate"].append(d)
            continue
        day = d.due_at.date()
        ends = d.due_at if (d.due_at.hour, d.due_at.minute) != (0, 0) else d.due_at + timedelta(days=1)
        if ends < now:
            sections["past"].append(d)
        elif day == today:
            sections["today"].append(d)
        elif day == today + timedelta(days=1):
            sections["tomorrow"].append(d)
        elif day <= today + timedelta(days=7):
            sections["week"].append(d)
        else:
            sections["later"].append(d)

    key = lambda d: (d.due_at or now, d.id)  # noqa: E731
    lines = [f"📅 <b>Дедлайны</b> (обновлено {WEEKDAY_SHORT[now.weekday()]} {now:%d.%m %H:%M})", ""]
    titles = [("today", "🔥 <b>Сегодня</b>", True), ("tomorrow", "⏳ <b>Завтра</b>", True),
              ("week", "📆 <b>На этой неделе</b>", False), ("later", "🗂 <b>Позже</b>", False),
              ("nodate", "❔ <b>Без точного срока</b>", False)]
    empty = True
    for sec, title, short in titles:
        items = sorted(sections[sec], key=key)
        if not items:
            continue
        empty = False
        lines.append(title)
        lines += [_line(d, settings, short) for d in items]
        lines.append("")
    if empty:
        lines += ["Пока нет активных дедлайнов 🎉", ""]
    past = sorted(sections["past"], key=key, reverse=True)
    if past and settings.hub.past_mode == "collapse":
        lines.append(f"✅ <b>Прошедшие</b> ({len(past)})")
        lines += [f"• <s>{fmt_due(d.due_at)} — {html.escape(d.title)}</s>" for d in past[:5]]
        lines.append("")
    lines.append("<i>Сортировка по сроку · 📌 событие · ↗ источник · обновляет StudyRadar</i>")

    pages: list[str] = []
    cur = ""
    for line in lines:
        if len(cur) + len(line) + 1 > PAGE_LIMIT and cur:
            pages.append(cur.rstrip())
            cur = "📅 <b>Дедлайны (продолжение)</b>\n\n"
        cur += line + "\n"
    if cur.strip():
        pages.append(cur.rstrip())
    return pages


async def refresh_board(hub: TelegramHub, db: Database, settings: Settings) -> dict:
    """Перерисовать закреп-доску: редактировать существующие страницы, недостающие — создать и закрепить."""
    now = db.now()
    deadlines = db.list_deadlines(statuses=("active", "cancelled"),
                                  since=now - timedelta(days=14))
    pages = render_board(deadlines, now, settings)
    existing = {p["page"]: p for p in db.board_pages()}
    for i, text in enumerate(pages):
        cur = existing.get(i)
        if cur and db.hub_message(cur["hub_message_id"]):
            if cur["text"] != text:
                await hub.edit(cur["hub_message_id"], text)
            db.set_board_page(i, cur["hub_message_id"], text)
        else:
            hub_id = await hub.send("deadlines", text, kind="board")
            await hub.pin(hub_id)
            db.set_board_page(i, hub_id, text)
    for i, cur in existing.items():   # лишние страницы — опустошаем
        if i >= len(pages) and cur["text"] != "—":
            await hub.edit(cur["hub_message_id"], "—")
            db.set_board_page(i, cur["hub_message_id"], "—")
    db.set_meta("board_updated_at", now.isoformat())
    db.set_meta("board_refresh_seq", str(int(db.get_meta("board_refresh_seq", "0") or 0) + 1))
    active = [d for d in deadlines if d.status == "active"]
    return {"pages": len(pages), "active_deadlines": len(active)}
