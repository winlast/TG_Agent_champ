"""Эмбеддинги: OllamaEmbedder (bge-m3, настоящий) и HashEmbedder (детерминированный, только для тестов)."""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from functools import lru_cache

_WORD = re.compile(r"\w+", re.UNICODE)


class Embedder(ABC):
    name: str

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


class OllamaEmbedder(Embedder):
    def __init__(self, model: str = "bge-m3", host: str = "http://localhost:11434", keep_alive: str = "30m"):
        import ollama

        self.model = model
        self.name = f"ollama-{model}"
        self.keep_alive = keep_alive
        self.client = ollama.Client(host=host, timeout=300)
        self._cache: dict[str, list[float]] = {}

    def embed(self, texts: list[str]) -> list[list[float]]:
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        for i in range(0, len(missing), 32):
            batch = missing[i:i + 32]
            resp = self.client.embed(model=self.model, input=[t[:4000] or " " for t in batch],
                                     keep_alive=self.keep_alive)
            for t, v in zip(batch, resp["embeddings"]):
                self._cache[t] = list(v)
        if len(self._cache) > 5000:
            self._cache.clear()
            return self.embed(texts)
        return [self._cache[t] for t in texts]


class HashEmbedder(Embedder):
    """Хеширование слов и символьных 3-грамм в вектор фиксированной длины. ТОЛЬКО для тестов."""

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.name = f"hash-{dim}"

    @staticmethod
    @lru_cache(maxsize=65536)
    def _bucket(token: str, dim: int) -> tuple[int, float]:
        h = hashlib.md5(token.encode("utf-8")).digest()
        return int.from_bytes(h[:4], "little") % dim, 1.0 if h[4] & 1 else -1.0

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        words = _WORD.findall(text.lower().replace("ё", "е"))
        for w in words:
            stem = w[:6]
            i, s = self._bucket("w:" + stem, self.dim)
            v[i] += 2.0 * s
            padded = f"#{w}#"
            for k in range(len(padded) - 2):
                i, s = self._bucket("g:" + padded[k:k + 3], self.dim)
                v[i] += 0.5 * s
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def make_embedder(backend: str, model: str, host: str) -> Embedder:
    if backend == "hash":
        return HashEmbedder()
    if backend == "ollama":
        return OllamaEmbedder(model=model, host=host)
    raise ValueError(f"Неизвестный backend эмбеддингов: {backend}")
