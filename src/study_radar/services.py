"""Сборка зависимостей (БД, эмбеддер, Chroma, retriever, хаб, LLM) по настройкам."""

from __future__ import annotations

from functools import cached_property

from study_radar.config import Settings
from study_radar.index.embedder import Embedder, make_embedder
from study_radar.storage.db import Database


class Services:
    """Ленивая сборка компонентов: тяжёлые (Chroma, Ollama) создаются при первом обращении."""

    def __init__(self, settings: Settings, hub_backend: str = "fake", bot=None):
        self.s = settings
        self.hub_backend = hub_backend   # fake | bot
        self._bot = bot

    @cached_property
    def db(self) -> Database:
        return Database(self.s.db_path, self.s.tz)

    @cached_property
    def embedder(self) -> Embedder:
        return make_embedder(self.s.embeddings.backend, self.s.embeddings.model, self.s.llm.host)

    @cached_property
    def store(self):
        from study_radar.index.vector_store import ChromaStore

        return ChromaStore(self.s.chroma_path, self.embedder.name)

    @cached_property
    def retriever(self):
        from study_radar.retrieval.hybrid import HybridRetriever

        return HybridRetriever(self.db, self.store, self.embedder, self.s.retrieval.rrf_k)

    @cached_property
    def indexer(self):
        from study_radar.index.indexer import Indexer

        return Indexer(self.s, self.db, self.store, self.embedder)

    @cached_property
    def llm(self):
        from study_radar.llm.client import make_llm

        return make_llm(self.s)

    def hub_chat_id(self) -> int | None:
        if self.s.telegram.hub_chat_id:
            return self.s.telegram.hub_chat_id
        raw = self.db.get_meta("hub_chat_id")
        return int(raw) if raw else None

    @cached_property
    def hub(self):
        from study_radar.telegram.hub import BotHub, FakeHub

        if self.hub_backend == "bot":
            chat_id = self.hub_chat_id()
            if not chat_id:
                raise RuntimeError("HUB_CHAT_ID не задан: впишите его в .env или напишите /sethub в чате-хабе")
            return BotHub(self.s, self.db, chat_id, bot=self._bot)
        return FakeHub(self.s, self.db)
