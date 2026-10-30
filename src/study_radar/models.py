"""Pydantic-модели на границах модулей."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ChatMessage(BaseModel):
    """Нормализованное сообщение из любого источника (экспорт, Bot API, сценарий)."""

    chat_id: int
    message_id: int
    chat_title: str = ""
    topic: str | None = None          # название топика исходного форума (или None)
    thread_id: int | None = None
    author: str = ""
    author_id: str | None = None
    date: datetime
    edited: datetime | None = None
    text: str = ""
    reply_to: int | None = None       # id сообщения, на которое ответили (не топик)
    file_name: str | None = None
    is_service: bool = False
    service_action: str | None = None
    link: str | None = None


class Post(BaseModel):
    """Логический пост = одно сообщение или склеенная «очередь» сообщений одного автора."""

    post_id: str
    chat_id: int
    chat_title: str = ""
    topic: str | None = None
    thread_id: int | None = None
    author: str = ""
    author_id: str | None = None
    date: datetime
    message_ids: list[int]
    text: str
    reply_to: int | None = None
    link: str | None = None
    edited: bool = False

    @property
    def source_refs(self) -> list[str]:
        return [f"{self.chat_id}:{m}" for m in self.message_ids]


class Category(str, Enum):
    DEADLINE = "deadline"                # срок сдачи чего-либо
    ASSIGNMENT = "assignment"            # выдано задание (срок может отсутствовать)
    DEADLINE_UPDATE = "deadline_update"  # перенос / уточнение срока известной задачи
    CANCELLATION = "cancellation"        # отмена задания / пары / события
    EVENT = "event"                      # событие с датой/местом: защита, рк, консультация, чемпионат
    ANNOUNCEMENT = "announcement"        # важное объявление без срока
    MATERIALS = "materials"              # учебные материалы (файлы, книги, ссылки)
    NOISE = "noise"                      # не важно


CATEGORY_RU = {
    Category.DEADLINE: "Дедлайн",
    Category.ASSIGNMENT: "Задание",
    Category.DEADLINE_UPDATE: "Перенос/уточнение",
    Category.CANCELLATION: "Отмена",
    Category.EVENT: "Событие",
    Category.ANNOUNCEMENT: "Объявление",
    Category.MATERIALS: "Материалы",
    Category.NOISE: "Шум",
}

CATEGORY_EMOJI = {
    Category.DEADLINE: "🗓",
    Category.ASSIGNMENT: "📝",
    Category.DEADLINE_UPDATE: "⚠️",
    Category.CANCELLATION: "❌",
    Category.EVENT: "📌",
    Category.ANNOUNCEMENT: "📢",
    Category.MATERIALS: "📎",
    Category.NOISE: "💤",
}


class Classification(BaseModel):
    """Строгий JSON, который возвращает LLM (Ollama format=<JSON Schema>)."""

    is_important: bool
    subject: str | None = None
    category: Category
    importance: int = Field(ge=1, le=5)
    title: str
    summary: str
    due_text: str | None = None
    due_at: datetime | None = None
    location: str | None = None
    related_deadline_id: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str

    @field_validator("subject", "due_text", "location", mode="before")
    @classmethod
    def _empty_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip().lower() in {"", "null", "none", "нет", "-"}:
            return None
        return v

    @field_validator("due_at", mode="before")
    @classmethod
    def _due_empty(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip().lower() in {"", "null", "none"}:
            return None
        return v

    @field_validator("importance", mode="before")
    @classmethod
    def _clamp_importance(cls, v: Any) -> Any:
        try:
            return max(1, min(5, int(v)))
        except (TypeError, ValueError):
            return 1

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_conf(cls, v: Any) -> Any:
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.0


def classification_json_schema() -> dict[str, Any]:
    """JSON Schema для Ollama structured output — плоская, без $ref (надёжнее для грамматики)."""
    nullable_str = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    return {
        "type": "object",
        "properties": {
            "is_important": {"type": "boolean"},
            "subject": nullable_str,
            "category": {"type": "string", "enum": [c.value for c in Category]},
            "importance": {"type": "integer", "minimum": 1, "maximum": 5},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "due_text": nullable_str,
            "due_at": nullable_str,
            "location": nullable_str,
            "related_deadline_id": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
        },
        "required": [
            "is_important", "subject", "category", "importance", "title", "summary",
            "due_text", "due_at", "location", "related_deadline_id", "confidence", "reason",
        ],
    }


class Deadline(BaseModel):
    id: int
    subject: str | None
    title: str
    kind: str = "deadline"           # deadline | event | assignment
    due_at: datetime | None = None
    due_text: str | None = None
    location: str | None = None
    status: str = "active"           # active | cancelled | done
    prev_due_at: datetime | None = None
    prev_location: str | None = None
    close_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)

    def describe(self, subjects: dict | None = None) -> str:
        subj = self.subject or "—"
        if subjects and self.subject in subjects:
            subj = subjects[self.subject].name
        due = self.due_at.strftime("%a %d.%m %H:%M") if self.due_at else (self.due_text or "без срока")
        loc = f" · {self.location}" if self.location else ""
        return f"id={self.id} · {subj} · {self.title} · {self.kind} · срок: {due}{loc}"


class ToolCallRecord(BaseModel):
    tool: str
    args: dict[str, Any]
    result: Any = None
    ok: bool = True
    by: str = "llm"          # llm | fallback | verify
    attempt: int = 1
    ms: int = 0


class PrefilterResult(BaseModel):
    passed: bool
    score: float = 0.0
    reason: str = ""
    best_match: str | None = None
