"""Дедупликация: один дедлайн в нескольких чатах = один дедлайн с несколькими источниками.

Критерии: совпадение предмета + похожесть названия по эмбеддингам + близость дат.
Четвёртый критерий — подтверждение LLM: классификатор видит кандидатов и сам указывает
related_deadline_id (тогда агент вызывает update_deadline вместо create_deadline).
"""

from __future__ import annotations

import re
from datetime import datetime

from study_radar.config import Settings
from study_radar.index.embedder import Embedder, cosine
from study_radar.models import Deadline
from study_radar.storage.db import Database


def _norm_title(t: str) -> str:
    return re.sub(r"[^\w]+", " ", (t or "").lower().replace("ё", "е")).strip()


def find_duplicate(db: Database, embedder: Embedder, settings: Settings, subject: str | None, title: str,
                   due_at: datetime | None, kind: str | None = None) -> tuple[Deadline | None, float]:
    tol = settings.dedup.date_tolerance_hours * 3600
    candidates = [d for d in db.list_deadlines(subject=subject) if d.subject == subject]
    if not candidates:
        return None, 0.0
    close: list[Deadline] = []
    for d in candidates:
        if due_at is None or d.due_at is None:
            if due_at is None and d.due_at is None:
                close.append(d)
            continue
        if abs((d.due_at - due_at).total_seconds()) <= tol:
            close.append(d)
    if not close:
        return None, 0.0
    q = embedder.embed_one(title)
    best, best_sim = None, 0.0
    vecs = embedder.embed([c.title for c in close])
    for d, v in zip(close, vecs):
        sim = 1.0 if _norm_title(d.title) == _norm_title(title) else cosine(q, v)
        if sim > best_sim:
            best, best_sim = d, sim
    if best is not None and best_sim >= settings.dedup.title_similarity:
        return best, round(best_sim, 3)
    return None, round(best_sim, 3)
