"""Сбор контекста для классификации (часть RAG): reply, соседние сообщения, похожие важные
сообщения (гибридный поиск), активные дедлайны-кандидаты, справочник предметов."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from study_radar.agent.dates import fmt_due, human_date
from study_radar.config import Settings, Subject
from study_radar.models import Post
from study_radar.services import Services


def subject_mentioned(subj: Subject, text: str | None) -> bool:
    """Есть ли в тексте алиас предмета, который не является «ложным другом» («физ» в «физру» — нет)."""
    low = (text or "").lower()
    for a in subj.aliases:
        for m in re.finditer(rf"(?<!\w){re.escape(a.lower())}", low):
            word = re.match(r"[\w.]*", low[m.start():]).group(0)
            if not any(word.startswith(n.lower()) for n in subj.not_aliases):
                return True
    return False


def guess_subject(settings: Settings, *texts: str | None) -> str | None:
    """Подсказка по алиасам (с учётом «физра ≠ физика»). Это лишь подсказка для LLM."""
    for text in texts:
        for key, s in settings.subjects.items():
            if subject_mentioned(s, text):
                return key
    return None


def _brief(row: dict[str, Any] | None, limit: int = 400) -> dict[str, Any] | None:
    if not row:
        return None
    return {"author": row.get("author"), "date": row.get("date"), "text": (row.get("text") or "")[:limit],
            "message_id": row.get("message_id")}


def build_context(sv: Services, post: Post, embedding: list[float] | None = None) -> dict[str, Any]:
    s = sv.s
    db = sv.db
    cfg = s.context
    msgs = db.get_messages(post.chat_id, post.message_ids)
    prev_text = "\n".join(m["prev_text"] for m in msgs if m.get("prev_text")) or None
    reply = db.get_message(post.chat_id, post.reply_to) if post.reply_to else None
    previous = db.messages_before(post.chat_id, post.topic, post.date, post.date - timedelta(minutes=cfg.prev_window_minutes),
                                  cfg.prev_messages, exclude_ids=post.message_ids)
    guess = guess_subject(s, post.text, (reply or {}).get("text"), post.topic, post.chat_title)

    # RAG: похожие ранее важные сообщения
    similar = []
    try:
        hits = sv.retriever.search(post.text, k=cfg.similar_k, only_important=True, exclude={post.post_id},
                                   until=post.date, query_embedding=embedding)
        for i, h in enumerate(hits, 1):
            similar.append({"n": i, "post_id": h.id, "chat_title": h.meta["chat_title"], "author": h.meta["author"],
                            "date": h.meta["date"], "text": h.text[:300], "subject": h.meta["subject"],
                            "category": h.meta["category"], "score": round(h.score, 4),
                            "vector_rank": h.vector_rank, "bm25_rank": h.bm25_rank})
    except Exception as e:  # индекс может быть пуст
        similar = [{"error": str(e)}]

    # кандидаты на обновление: из этого же поста (редактирование), из reply, затем гибридный поиск
    candidates: list[dict[str, Any]] = []
    seen: set[int] = set()

    def add(d, origin: str, sim: float | None = None):
        if d is None or d.id in seen or d.status != "active":
            return
        seen.add(d.id)
        candidates.append({
            "id": d.id, "subject": d.subject,
            "subject_name": s.subjects[d.subject].name if d.subject in s.subjects else "—",
            "title": d.title, "kind": d.kind, "due_at": d.due_at.isoformat() if d.due_at else None,
            "due": fmt_due(d.due_at), "due_text": d.due_text, "location": d.location, "origin": origin,
            "from_this_post": origin == "это же сообщение", "similarity": sim,
        })

    for did in db.deadlines_for_messages(post.chat_id, post.message_ids):
        add(db.get_deadline(did), "это же сообщение")
    if reply:
        reply_post = db.post_id_for_message(post.chat_id, reply["message_id"])
        reply_ids = [reply["message_id"]]
        if reply_post and (rp := db.get_post(reply_post)):
            reply_ids = rp.message_ids
        for did in db.deadlines_for_messages(post.chat_id, reply_ids):
            add(db.get_deadline(did), "сообщение, на которое ответили")
    query = post.text + ("\n" + reply["text"] if reply and reply.get("text") else "")
    try:
        for d, sim in sv.retriever.search_deadlines(query, k=cfg.deadlines_k, subject=guess):
            add(d, "поиск по дедлайнам", round(sim, 3))
    except Exception:
        pass

    return {
        "post": {
            "post_id": post.post_id, "chat_id": post.chat_id, "chat_title": post.chat_title, "topic": post.topic,
            "author": post.author, "trusted": s.is_trusted(post.author), "date": post.date.isoformat(),
            "date_human": human_date(post.date), "text": post.text, "message_ids": post.message_ids,
            "edited": post.edited or bool(prev_text), "prev_text": prev_text, "burst": len(post.message_ids) > 1,
        },
        "reply": _brief(reply),
        "previous": [_brief(r, 200) for r in previous],
        "similar": similar,
        "candidates": candidates[: cfg.deadlines_k + 2],
        "subject_guess": guess,
    }


def render_user_prompt(ctx: dict[str, Any]) -> str:
    """Обогащённый запрос для классификации: сообщение + контекст + кандидаты."""
    p = ctx["post"]
    where = f"Чат «{p['chat_title']}»" + (f", топик «{p['topic']}»" if p.get("topic") else "")
    author = p["author"] + (" (доверенный автор)" if p.get("trusted") else "")
    lines = ["# Сообщение для классификации", f"{where}. Автор: {author}.",
             f"Дата сообщения: {p['date_human']} (ISO {p['date']})."]
    if p.get("burst"):
        lines.append(f"Это серия из {len(p['message_ids'])} сообщений автора подряд — классифицируй как одно целое.")
    if p.get("prev_text"):
        lines.append("Сообщение ОТРЕДАКТИРОВАНО. Было:\n«" + p["prev_text"][:600] + "»\nСтало:")
    lines.append("Текст:\n«" + p["text"][:2500] + "»")
    if ctx.get("reply"):
        r = ctx["reply"]
        lines += ["", "# Ответ на сообщение", f"{r['author']} · {_short_date(r['date'])}: «{r['text']}»"]
    if ctx.get("previous"):
        lines += ["", "# Предыдущие сообщения в чате/топике (до 30 минут)"]
        lines += [f"- {r['author']} · {_short_date(r['date'])}: «{r['text']}»" for r in ctx["previous"]]
    sim = [x for x in ctx.get("similar") or [] if "error" not in x]
    if sim:
        lines += ["", "# Похожие ранее важные сообщения (RAG)"]
        lines += [f"[{x['n']}] {x['chat_title']} · {_short_date(x['date'])} · {x['author']} · "
                  f"предмет: {x['subject'] or '—'} · {x['category'] or '—'}: «{x['text']}»" for x in sim]
    lines += ["", "# Известные активные дедлайны (кандидаты на обновление)"]
    if ctx.get("candidates"):
        for c in ctx["candidates"]:
            loc = f" · место: {c['location']}" if c.get("location") else ""
            lines.append(f"- id={c['id']} · {c['subject_name']} · {c['title']} · {c['kind']} · срок: {c['due']}"
                         f"{loc} · найден: {c['origin']}")
    else:
        lines.append("- нет")
    if ctx.get("subject_guess"):
        lines += ["", f"Подсказка по алиасам (может быть неверной): предмет «{ctx['subject_guess']}»."]
    lines += ["", "Верни JSON классификации."]
    return "\n".join(lines)


def _short_date(iso: str | None) -> str:
    if not iso:
        return ""
    return fmt_due(datetime.fromisoformat(iso))
