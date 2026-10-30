"""Модуль 1 — индексация текстов предметной области в векторную БД.

* messages  — посты (сообщения / склеенные очереди) с метаданными;
* subjects  — справочник предметов из config/subjects.yaml;
* deadlines — текстовые описания дедлайнов (для сопоставления обновлений).

Идемпотентно: id документа = post_id, повторная загрузка не плодит дубли.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from study_radar.config import Settings
from study_radar.index.embedder import Embedder
from study_radar.index.vector_store import ChromaStore
from study_radar.ingest.bursts import group_bursts
from study_radar.ingest.telegram_export import load_export
from study_radar.models import ChatMessage, Classification, Deadline, Post
from study_radar.storage.db import Database, text_hash

log = logging.getLogger(__name__)


@dataclass
class IndexStats:
    chats: int = 0
    messages: int = 0
    posts: int = 0
    indexed: int = 0
    skipped: int = 0


def post_document(p: Post) -> str:
    topic = f"[{p.topic}] " if p.topic and p.topic != "General" else ""
    return f"{topic}{p.text}"


def deadline_document(d: Deadline, settings: Settings) -> str:
    subj = settings.subjects[d.subject].name if d.subject in settings.subjects else "без предмета"
    parts = [d.title, subj, d.kind]
    if d.due_text:
        parts.append(d.due_text)
    if d.location:
        parts.append(d.location)
    return " · ".join(parts)


class Indexer:
    def __init__(self, settings: Settings, db: Database, store: ChromaStore, embedder: Embedder):
        self.s = settings
        self.db = db
        self.store = store
        self.embedder = embedder

    # ---------------------------------------------------------------- messages
    def store_messages(self, msgs: list[ChatMessage]) -> int:
        for m in msgs:
            self.db.upsert_message(m)
        return len(msgs)

    def index_posts(self, posts: list[Post], embeddings: list[list[float]] | None = None) -> tuple[int, int]:
        """Сохраняет посты в SQLite и Chroma. Возвращает (проиндексировано, пропущено)."""
        todo: list[Post] = []
        for p in posts:
            self.db.upsert_post(p)
            row = self.db.get_post_row(p.post_id)
            if row and row["indexed_hash"] == text_hash(post_document(p)):
                continue
            todo.append(p)
        if not todo:
            return 0, len(posts)
        docs = [post_document(p) for p in todo]
        embs = embeddings if embeddings is not None and len(embeddings) == len(todo) else self.embedder.embed(docs)
        metas = []
        for p in todo:
            row = self.db.get_post_row(p.post_id) or {}
            metas.append({
                "chat_id": p.chat_id, "chat_title": p.chat_title, "topic": p.topic, "author": p.author,
                "message_ids": ",".join(map(str, p.message_ids)), "date": p.date.isoformat(),
                "ts": int(p.date.timestamp()), "edited": p.edited, "link": p.link,
                "subject": row.get("subject"), "category": row.get("category"),
                "is_important": bool(row.get("is_important")),
            })
        self.store.upsert("messages", [p.post_id for p in todo], docs, metas, embs)
        for p, d in zip(todo, docs):
            self.db.set_post_indexed(p.post_id, text_hash(d))
        return len(todo), len(posts) - len(todo)

    def mark_post(self, post_id: str, cls: Classification | None) -> None:
        """После классификации — дописать предмет/категорию в метаданные документа."""
        if cls is None:
            meta = {"subject": None, "category": "noise", "is_important": False}
        else:
            meta = {"subject": cls.subject, "category": cls.category.value, "is_important": cls.is_important}
        self.store.update_metadata("messages", post_id, meta)

    # ---------------------------------------------------------------- subjects / deadlines
    def index_subjects(self) -> int:
        subs = list(self.s.subjects.values())
        docs = [f"{s.name}. Также называют: {', '.join(s.aliases)}. Преподаватели: {', '.join(s.teachers)}. "
                f"Типичные работы: {', '.join(s.works)}." for s in subs]
        self.store.upsert("subjects", [s.key for s in subs], docs, [{"name": s.name} for s in subs],
                          self.embedder.embed(docs))
        return len(subs)

    def sync_deadlines(self) -> int:
        """Синхронизирует изменённые дедлайны из SQLite в коллекцию deadlines (пишет только агент)."""
        changed = self.db.deadlines_to_index()
        if not changed:
            return 0
        active = [d for d in changed if d.status == "active"]
        closed = [d for d in changed if d.status != "active"]
        if active:
            docs = [deadline_document(d, self.s) for d in active]
            metas = [{"subject": d.subject, "kind": d.kind, "status": d.status,
                      "due_at": d.due_at.isoformat() if d.due_at else None} for d in active]
            self.store.upsert("deadlines", [str(d.id) for d in active], docs, metas, self.embedder.embed(docs))
        self.store.delete("deadlines", [str(d.id) for d in closed])
        for d in changed:
            self.db.mark_deadline_indexed(d.id)
        return len(changed)

    # ---------------------------------------------------------------- export
    def index_export(self, path: Path | str) -> IndexStats:
        stats = IndexStats()
        for chat in load_export(path, self.s.tz):
            stats.chats += 1
            self.db.upsert_chat(chat.chat_id, chat.title, chat.kind, chat.is_forum)
            stats.messages += self.store_messages(chat.messages)
            posts = group_bursts(chat.messages, self.s.bursts.gap_minutes)
            stats.posts += len(posts)
            done, skipped = self.index_posts(posts)
            stats.indexed += done
            stats.skipped += skipped
            log.info("Чат «%s»: %d сообщений → %d постов (новых в индексе: %d)",
                     chat.title, len(chat.messages), len(posts), done)
        self.index_subjects()
        return stats
