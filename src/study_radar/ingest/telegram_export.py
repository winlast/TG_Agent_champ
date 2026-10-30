"""Парсер Telegram Desktop JSON-экспорта (Export chat history → JSON).

Учитывает: text строкой и массивом, rich_message.blocks, служебные сообщения, форумы
(топик по цепочке reply_to_message_id до topic_created), edited, файлы/фото без текста.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from study_radar.ingest.normalize import (
    bot_api_chat_id,
    clean_text,
    flatten_rich,
    flatten_text,
    message_link,
    to_tz,
)
from study_radar.models import ChatMessage

GENERAL_TOPIC = "General"


@dataclass
class ExportedChat:
    chat_id: int
    title: str
    kind: str
    is_forum: bool
    messages: list[ChatMessage] = field(default_factory=list)


def _parse_date(raw: dict[str, Any], key: str, tz: ZoneInfo) -> datetime | None:
    unix = raw.get(f"{key}_unixtime")
    if unix:
        return datetime.fromtimestamp(int(unix), tz=timezone.utc).astimezone(tz)
    if raw.get(key):
        return to_tz(datetime.fromisoformat(raw[key]), tz)
    return None


def _message_text(raw: dict[str, Any]) -> str:
    text = flatten_text(raw.get("text"))
    if not text.strip() and raw.get("rich_message"):
        text = flatten_rich(raw["rich_message"])
    text = clean_text(text)
    if not text:
        if raw.get("file_name"):
            text = f"[файл] {raw['file_name']}"
        elif raw.get("media_type") == "sticker":
            text = f"[стикер] {raw.get('sticker_emoji', '')}".strip()
        elif raw.get("photo"):
            text = "[фото]"
        elif raw.get("media_type"):
            text = f"[{raw['media_type']}]"
    elif raw.get("file_name"):
        text = f"{text}\n[файл] {raw['file_name']}"
    return text


def parse_chat(data: dict[str, Any], tz: ZoneInfo) -> ExportedChat:
    kind = data.get("type", "")
    chat_id = bot_api_chat_id(int(data.get("id", 0)), kind)
    title = data.get("name") or str(chat_id)
    raws: list[dict[str, Any]] = data.get("messages") or []
    by_id = {r.get("id"): r for r in raws}
    topics = {r["id"]: (r.get("title") or "Без названия") for r in raws
              if r.get("type") == "service" and r.get("action") == "topic_created"}
    is_forum = bool(topics) or "forum" in kind

    cache: dict[int, str | None] = {}

    def topic_of(mid: int, depth: int = 0) -> str | None:
        """Топик по цепочке reply_to_message_id до служебного topic_created."""
        if mid in cache:
            return cache[mid]
        if mid in topics:
            return topics[mid]
        raw = by_id.get(mid)
        res: str | None = None
        if raw is not None and depth < 50:
            parent = raw.get("reply_to_message_id")
            if parent is not None:
                res = topic_of(parent, depth + 1)
        cache[mid] = res
        return res

    chat = ExportedChat(chat_id=chat_id, title=title, kind=kind, is_forum=is_forum)
    for raw in raws:
        mid = raw.get("id")
        date = _parse_date(raw, "date", tz)
        if mid is None or date is None:
            continue
        is_service = raw.get("type") == "service"
        topic = topic_of(mid) if is_forum else None
        if is_forum and topic is None:
            topic = GENERAL_TOPIC
        reply_to = raw.get("reply_to_message_id")
        if reply_to in topics:   # «ответ» на создание топика = просто сообщение в топике
            reply_to = None
        author = raw.get("from") or raw.get("actor") or ""
        text = clean_text(flatten_text(raw.get("text"))) if is_service else _message_text(raw)
        if is_service and raw.get("action") == "topic_created":
            text = f"Создан топик «{raw.get('title', '')}»"
        chat.messages.append(ChatMessage(
            chat_id=chat_id, message_id=int(mid), chat_title=title, topic=topic,
            thread_id=None, author=author or "", author_id=raw.get("from_id") or raw.get("actor_id"),
            date=date, edited=_parse_date(raw, "edited", tz), text=text, reply_to=reply_to,
            file_name=raw.get("file_name"), is_service=is_service,
            service_action=raw.get("action") if is_service else None,
            link=message_link(chat_id, int(mid)),
        ))
    return chat


def load_export(path: Path | str, tz: ZoneInfo) -> list[ExportedChat]:
    """Один чат (result.json одного чата) или полный экспорт аккаунта (chats.list)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "messages" in data:
        return [parse_chat(data, tz)]
    chats = (data.get("chats") or {}).get("list") or []
    return [parse_chat(c, tz) for c in chats if c.get("messages")]
