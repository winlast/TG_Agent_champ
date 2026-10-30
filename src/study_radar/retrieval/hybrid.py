"""Гибридный retriever: векторный поиск (Chroma) + BM25 (rank_bm25), слияние через RRF.

Фильтры: предмет, чат, период, «только важные». Используется и при сборе контекста
для классификации, и в Модуле 2 (RAG-ответы на вопросы), и в MCP-инструменте search_messages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rank_bm25 import BM25Okapi

from study_radar.index.embedder import Embedder
from study_radar.index.vector_store import ChromaStore
from study_radar.models import Deadline
from study_radar.storage.db import Database

_TOKEN = re.compile(r"\w+", re.UNICODE)
_STOP = {"и", "в", "во", "на", "по", "с", "со", "к", "ко", "а", "но", "что", "как", "это", "у", "о", "об", "от",
         "до", "за", "из", "не", "ли", "же", "бы", "то", "the", "мы", "вы", "я", "он", "она", "они", "все", "всё"}


def tokenize(text: str) -> list[str]:
    """Простая «стемминг-по-префиксу» токенизация для русского: первые 5 символов слова."""
    out = []
    for w in _TOKEN.findall((text or "").lower().replace("ё", "е")):
        if w in _STOP:
            continue
        out.append(w[:5] if len(w) > 5 else w)
    return out


@dataclass
class Hit:
    id: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    vector_rank: int | None = None
    bm25_rank: int | None = None
    similarity: float | None = None


def rrf_merge(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


class HybridRetriever:
    def __init__(self, db: Database, store: ChromaStore, embedder: Embedder, rrf_k: int = 60):
        self.db = db
        self.store = store
        self.embedder = embedder
        self.rrf_k = rrf_k

    # ---------------------------------------------------------------- messages
    def search(self, query: str, k: int = 6, subject: str | None = None, chat_id: int | None = None,
               since: datetime | None = None, until: datetime | None = None, only_important: bool = False,
               exclude: set[str] | None = None, query_embedding: list[float] | None = None) -> list[Hit]:
        exclude = exclude or set()
        pool = max(k * 4, 20)

        # 1) векторный поиск в Chroma с фильтрами по метаданным
        conds: list[dict[str, Any]] = []
        if subject:
            conds.append({"subject": subject})
        if chat_id is not None:
            conds.append({"chat_id": chat_id})
        if only_important:
            conds.append({"is_important": True})
        if since:
            conds.append({"ts": {"$gte": int(since.timestamp())}})
        if until:
            conds.append({"ts": {"$lte": int(until.timestamp())}})
        where = conds[0] if len(conds) == 1 else ({"$and": conds} if conds else None)
        emb = query_embedding or self.embedder.embed_one(query)
        vhits = [h for h in self.store.query("messages", emb, pool, where) if h.id not in exclude]
        vec_rank = [h.id for h in vhits]
        vec_by_id = {h.id: h for h in vhits}

        # 2) BM25 по тем же постам из SQLite (те же фильтры)
        rows = [r for r in self.db.list_posts(subject=subject, chat_id=chat_id, since=since, until=until,
                                              only_important=only_important) if r["post_id"] not in exclude]
        bm_rank: list[str] = []
        rows_by_id = {r["post_id"]: r for r in rows}
        q_tokens = tokenize(query)
        if rows and q_tokens:
            corpus = [tokenize(f"{r['topic'] or ''} {r['text']}") or ["_"] for r in rows]
            bm25 = BM25Okapi(corpus)
            scores = bm25.get_scores(q_tokens)
            order = sorted(range(len(rows)), key=lambda i: scores[i], reverse=True)
            bm_rank = [rows[i]["post_id"] for i in order[:pool] if scores[i] > 0]

        # 3) Reciprocal Rank Fusion
        fused = rrf_merge([vec_rank, bm_rank], self.rrf_k)
        hits: list[Hit] = []
        for doc_id, score in sorted(fused.items(), key=lambda x: x[1], reverse=True)[:k]:
            row = rows_by_id.get(doc_id) or self.db.get_post_row(doc_id)
            if row is None:
                continue
            v = vec_by_id.get(doc_id)
            hits.append(Hit(
                id=doc_id, text=row["text"], score=score,
                vector_rank=vec_rank.index(doc_id) + 1 if doc_id in vec_by_id else None,
                bm25_rank=bm_rank.index(doc_id) + 1 if doc_id in bm_rank else None,
                similarity=round(v.similarity, 3) if v else None,
                meta={"chat_id": row["chat_id"], "chat_title": row["chat_title"], "topic": row["topic"],
                      "author": row["author"], "date": row["date"], "link": row["link"],
                      "subject": row["subject"], "category": row["category"],
                      "is_important": bool(row["is_important"]), "message_ids": row["message_ids"]},
            ))
        return hits

    # ---------------------------------------------------------------- deadlines
    def search_deadlines(self, query: str, k: int = 5, subject: str | None = None,
                         query_embedding: list[float] | None = None) -> list[tuple[Deadline, float]]:
        active = self.db.list_deadlines()
        if not active:
            return []
        by_id = {str(d.id): d for d in active}
        emb = query_embedding or self.embedder.embed_one(query)
        where = {"subject": subject} if subject else None
        vhits = [h for h in self.store.query("deadlines", emb, max(k * 3, 10), where) if h.id in by_id]
        if subject and len(vhits) < k:   # предмет мог быть угадан неверно — добираем без фильтра
            vhits += [h for h in self.store.query("deadlines", emb, max(k * 3, 10)) if h.id in by_id
                      and h.id not in {x.id for x in vhits}]
        vec_rank = [h.id for h in vhits]
        sims = {h.id: h.similarity for h in vhits}

        corpus = [tokenize(f"{d.title} {d.subject or ''} {d.due_text or ''} {d.location or ''}") or ["_"]
                  for d in active]
        bm_rank: list[str] = []
        q_tokens = tokenize(query)
        if q_tokens:
            scores = BM25Okapi(corpus).get_scores(q_tokens)
            order = sorted(range(len(active)), key=lambda i: scores[i], reverse=True)
            bm_rank = [str(active[i].id) for i in order if scores[i] > 0]
        fused = rrf_merge([vec_rank, bm_rank], self.rrf_k)
        ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:k]
        return [(by_id[i], sims.get(i, 0.0)) for i, _ in ranked]
