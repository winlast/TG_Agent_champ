"""Очистка текста и общие утилиты нормализации сообщений."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_WS = re.compile(r"[ \t ]+")
_MANY_NL = re.compile(r"\n{3,}")


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("​", "")
    text = _WS.sub(" ", text)
    text = _MANY_NL.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def is_emoji_only(text: str) -> bool:
    """True, если в тексте нет ни букв, ни цифр (эмодзи, «+», «!!!», стикеры)."""
    stripped = re.sub(r"\[(стикер|sticker)\]", "", text or "", flags=re.I)
    for ch in stripped:
        cat = unicodedata.category(ch)
        if cat.startswith("L") or cat.startswith("N"):
            return False
    return True


def flatten_text(node: Any) -> str:
    """Telegram-экспорт: text бывает строкой или массивом строк и объектов {type, text}."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(flatten_text(x) for x in node)
    if isinstance(node, dict):
        if "text" in node:
            return flatten_text(node["text"])
        if "items" in node:
            return "\n".join(flatten_text(i) for i in node["items"])
    return ""


def flatten_rich(rich: dict[str, Any] | None) -> str:
    """rich_message.blocks (абзацы, списки, заголовки) → плоский текст."""
    if not rich:
        return ""
    lines: list[str] = []
    for block in rich.get("blocks") or []:
        btype = block.get("type")
        if btype == "list":
            ordered = block.get("kind") in {"ordered", "numbered"}
            for i, item in enumerate(block.get("items") or [], 1):
                prefix = f"{i}. " if ordered else "• "
                lines.append(prefix + flatten_text(item.get("text", item)))
        else:
            lines.append(flatten_text(block.get("text", block)))
    return "\n".join(x for x in lines if x is not None)


def to_tz(dt: datetime, tz: ZoneInfo) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def bot_api_chat_id(export_id: int, chat_type: str) -> int:
    """Экспорт хранит «голый» id; Bot API использует -100… для супергрупп и каналов."""
    if export_id < 0:
        return export_id
    if chat_type in {"private_supergroup", "public_supergroup", "private_channel", "public_channel"}:
        return int(f"-100{export_id}")
    if chat_type == "private_group":
        return -export_id
    return export_id


def message_link(chat_id: int, message_id: int, username: str | None = None) -> str:
    if username:
        return f"https://t.me/{username}/{message_id}"
    s = str(chat_id)
    internal = s[4:] if s.startswith("-100") else s.lstrip("-")
    return f"https://t.me/c/{internal}/{message_id}"
