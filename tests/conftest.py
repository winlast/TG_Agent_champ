"""Общие фикстуры: изолированные настройки (tmp БД и Chroma), FakeLLM + HashEmbedder.

ВАЖНО: FakeLLM — детерминированные правила для проверки КОДА, а не качества модели.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from study_radar.config import load_settings
from study_radar.models import ChatMessage

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "fixtures"


@pytest.fixture()
def settings(tmp_path: Path):
    s = load_settings(use_env=False)
    s.paths.db = str(tmp_path / "radar.sqlite")
    s.paths.chroma = str(tmp_path / "chroma")
    s.llm.backend = "fake"
    s.embeddings.backend = "hash"
    s.prefilter.threshold = 0.2
    s.hub.journal = True
    return s


@pytest.fixture()
def services(settings):
    from study_radar.services import Services

    return Services(settings, "fake")


def msg(settings, chat_id: int, mid: int, text: str, author: str = "Студент", at: str = "2026-10-26T10:00",
        chat_title: str = "Тестовый чат", topic: str | None = None, reply_to: int | None = None) -> ChatMessage:
    dt = datetime.fromisoformat(at).replace(tzinfo=settings.tz)
    return ChatMessage(chat_id=chat_id, message_id=mid, chat_title=chat_title, topic=topic, author=author,
                       author_id=f"user_{author}", date=dt, text=text, reply_to=reply_to,
                       link=f"https://t.me/c/{abs(chat_id)}/{mid}")
