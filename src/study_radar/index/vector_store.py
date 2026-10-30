"""Векторная БД Chroma (persistent, локально). Коллекции: messages, subjects, deadlines.

Эмбеддинги считаем сами (Embedder) и передаём в Chroma явно, поэтому встроенная
embedding-функция Chroma не используется. Имя коллекции включает имя эмбеддера,
чтобы смена модели не ломала размерность.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class VectorHit:
    id: str
    document: str
    metadata: dict[str, Any]
    distance: float

    @property
    def similarity(self) -> float:
        return 1.0 - self.distance


def _clean_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Chroma принимает только str/int/float/bool; None → пустая строка."""
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if v is None:
            out[k] = ""
        elif isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


class ChromaStore:
    COLLECTIONS = ("messages", "subjects", "deadlines")

    def __init__(self, path: Path | str, embedder_name: str):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        Path(path).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(path), settings=ChromaSettings(anonymized_telemetry=False))
        suffix = re.sub(r"[^a-zA-Z0-9_-]+", "-", embedder_name).strip("-")[:40]
        self._cols = {
            name: self.client.get_or_create_collection(
                name=f"{name}__{suffix}", metadata={"hnsw:space": "cosine"}, embedding_function=None)
            for name in self.COLLECTIONS
        }

    def col(self, name: str):
        return self._cols[name]

    def upsert(self, collection: str, ids: list[str], documents: list[str], metadatas: list[dict[str, Any]],
               embeddings: list[list[float]]) -> None:
        if not ids:
            return
        self.col(collection).upsert(ids=ids, documents=documents, metadatas=[_clean_meta(m) for m in metadatas],
                                    embeddings=embeddings)

    def update_metadata(self, collection: str, doc_id: str, meta: dict[str, Any]) -> None:
        col = self.col(collection)
        got = col.get(ids=[doc_id], include=["metadatas"])
        if not got["ids"]:
            return
        merged = {**(got["metadatas"][0] or {}), **_clean_meta(meta)}
        col.update(ids=[doc_id], metadatas=[merged])

    def delete(self, collection: str, ids: list[str]) -> None:
        if ids:
            self.col(collection).delete(ids=ids)

    def count(self, collection: str) -> int:
        return self.col(collection).count()

    def query(self, collection: str, embedding: list[float], k: int,
              where: dict[str, Any] | None = None) -> list[VectorHit]:
        col = self.col(collection)
        n = col.count()
        if n == 0:
            return []
        try:
            res = col.query(query_embeddings=[embedding], n_results=min(k, n), where=where or None,
                            include=["documents", "metadatas", "distances"])
        except Exception as e:  # пустой результат фильтра и т. п.
            log.debug("chroma query failed: %s", e)
            return []
        hits = []
        for i, doc_id in enumerate(res["ids"][0]):
            hits.append(VectorHit(id=doc_id, document=res["documents"][0][i] or "",
                                  metadata=res["metadatas"][0][i] or {}, distance=float(res["distances"][0][i])))
        return hits
