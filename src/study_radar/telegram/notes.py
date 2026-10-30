"""Тексты пометок агента в хабе."""

from __future__ import annotations

import html
from datetime import datetime

from study_radar.agent.dates import fmt_due
from study_radar.config import Settings
from study_radar.models import CATEGORY_EMOJI, CATEGORY_RU, Category, Deadline

KIND_RU = {"deadline": ("🗓", "Дедлайн"), "event": ("📌", "Событие"), "assignment": ("📝", "Задание")}


def subject_name(settings: Settings, key: str | None) -> str:
    return settings.subjects[key].name if key and key in settings.subjects else "Общее"


def suggested_note(settings: Settings, category: Category, subject: str | None, title: str,
                   due_at: datetime | None, location: str | None, confidence: float) -> str:
    """«🗓 Дедлайн · Физика · ДЗ №3 — до чт 05.11, 23:59 · уверенность 0.92»."""
    parts = [f"{CATEGORY_EMOJI[category]} {CATEGORY_RU[category]}", subject_name(settings, subject), title]
    s = " · ".join(p for p in parts if p)
    if due_at:
        s += f" — {'до ' if category in {Category.DEADLINE, Category.ASSIGNMENT} else ''}{fmt_due(due_at)}"
    if location:
        s += f" · {location}"
    return f"{s} · уверенность {confidence:.2f}"


def deadline_note(settings: Settings, d: Deadline, old: Deadline | None = None) -> str:
    """Пометка, привязанная к дедлайну; при изменении старое значение зачёркивается."""
    emoji, kind = KIND_RU.get(d.kind, KIND_RU["deadline"])
    head = f"{emoji} {kind} · {html.escape(subject_name(settings, d.subject))} · {html.escape(d.title)}"
    if d.status == "cancelled":
        return f"❌ <s>{head}</s> — отменено" + (f": {html.escape(d.close_reason)}" if d.close_reason else "")
    if d.status == "done":
        return f"✅ <s>{head}</s> — выполнено"
    due = fmt_due(d.due_at) if d.due_at else (html.escape(d.due_text) if d.due_text else "срок уточняется")
    if old is not None and old.due_at != d.due_at and old.due_at is not None:
        due = f"<s>{fmt_due(old.due_at)}</s> → {due}"
    s = f"{head} — {due}"
    if d.location:
        loc = html.escape(d.location)
        if old is not None and old.location and old.location != d.location:
            loc = f"<s>{html.escape(old.location)}</s> → {loc}"
        s += f" · {loc}"
    return s
