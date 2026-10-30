"""Чат-хаб: супергруппа с топиками. Две реализации интерфейса TelegramHub:

* BotHub  — настоящий Telegram через aiogram (forwardMessage / copyMessage / editMessageText / pin);
* FakeHub — пишет в SQLite; Streamlit показывает это «как чат Telegram» (демо без Telegram).

Обе реализации записывают каждое сообщение хаба в таблицу hub_messages — это общий журнал,
по которому строится вкладка «Хаб» и находятся пометки для редактирования.
"""

from __future__ import annotations

import html
import logging
from abc import ABC, abstractmethod
from typing import Any

from study_radar.config import Settings
from study_radar.storage.db import Database, text_hash

log = logging.getLogger(__name__)

SERVICE_TOPICS_HEAD = [("deadlines", "📅 Дедлайны")]
SERVICE_TOPICS_TAIL = [("general", "📢 Общее"), ("review", "🤔 На проверку"), ("journal", "🧾 Журнал агента")]


def topic_specs(settings: Settings) -> list[tuple[str, str]]:
    specs = list(SERVICE_TOPICS_HEAD)
    specs += [(s.key, s.title) for s in settings.subjects.values()]
    specs += [t for t in SERVICE_TOPICS_TAIL if t[0] != "journal" or settings.hub.journal]
    return specs


def make_bot(settings: Settings):
    """aiogram.Bot с parse_mode=HTML; через прокси, если он задан (TELEGRAM_PROXY или https_proxy)."""
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties

    session = None
    if settings.telegram.proxy:
        from aiogram.client.session.aiohttp import AiohttpSession

        session = AiohttpSession(proxy=settings.telegram.proxy)
    return Bot(settings.telegram.token, session=session, default=DefaultBotProperties(parse_mode="HTML"))


def quote_html(row: dict[str, Any], max_len: int = 1500) -> str:
    """Текстовая «пересылка» (если forward/copy невозможны): автор, чат, дата, текст, ссылка."""
    text = html.escape((row.get("text") or "")[:max_len])
    head = f"<b>{html.escape(row.get('author') or '')}</b> · {html.escape(row.get('chat_title') or '')}"
    if row.get("topic") and row.get("topic") != "General":
        head += f" › {html.escape(row['topic'])}"
    link = f'\n<a href="{row["link"]}">↗ оригинал</a>' if row.get("link") else ""
    return f"{head}\n{text}{link}"


class TelegramHub(ABC):
    def __init__(self, settings: Settings, db: Database):
        self.s = settings
        self.db = db

    def topics(self) -> dict[str, dict[str, Any]]:
        return self.db.hub_topics()

    def topic_title(self, key: str) -> str:
        t = self.topics().get(key)
        return t["title"] if t else key

    @abstractmethod
    async def ensure_topics(self) -> dict[str, int | None]:
        ...

    @abstractmethod
    async def forward(self, topic_key: str, rows: list[dict[str, Any]]) -> list[int]:
        """Переслать исходные сообщения (строки таблицы messages) в топик. Вернуть id в hub_messages."""

    @abstractmethod
    async def send(self, topic_key: str, text: str, *, kind: str = "note", reply_to: int | None = None,
                   **meta: Any) -> int:
        ...

    @abstractmethod
    async def edit(self, hub_id: int, text: str) -> None:
        ...

    @abstractmethod
    async def pin(self, hub_id: int) -> None:
        ...

    def _record_forward(self, topic_key: str, row: dict[str, Any], tg_id: int | None, text: str) -> int:
        return self.db.add_hub_message(
            topic_key=topic_key, kind="forward", text=text, source_chat_id=row["chat_id"],
            source_message_id=row["message_id"], source_chat_title=row.get("chat_title"),
            source_author=row.get("author"), source_date=row.get("date"), source_link=row.get("link"),
            source_text_hash=text_hash(row.get("text") or ""), tg_message_id=tg_id,
        )


class FakeHub(TelegramHub):
    """Хаб без Telegram: всё пишется в SQLite (hub_messages)."""

    async def ensure_topics(self) -> dict[str, int | None]:
        existing = self.topics()
        for pos, (key, title) in enumerate(topic_specs(self.s)):
            if key not in existing or existing[key]["title"] != title:
                self.db.set_hub_topic(key, title, -(pos + 1), pos)
        return {k: v["thread_id"] for k, v in self.topics().items()}

    async def forward(self, topic_key: str, rows: list[dict[str, Any]]) -> list[int]:
        return [self._record_forward(topic_key, r, None, html.escape(r.get("text") or "")) for r in rows]

    async def send(self, topic_key: str, text: str, *, kind: str = "note", reply_to: int | None = None,
                   **meta: Any) -> int:
        return self.db.add_hub_message(topic_key=topic_key, kind=kind, text=text, reply_to=reply_to, **meta)

    async def edit(self, hub_id: int, text: str) -> None:
        self.db.update_hub_message(hub_id, text=text)

    async def pin(self, hub_id: int) -> None:
        self.db.update_hub_message(hub_id, pinned=1)


class BotHub(TelegramHub):
    """Настоящий хаб в Telegram (aiogram 3)."""

    def __init__(self, settings: Settings, db: Database, chat_id: int, bot: Any = None):
        super().__init__(settings, db)
        self.chat_id = chat_id
        self._own_bot = bot is None
        self.bot = bot or make_bot(settings)

    async def close(self) -> None:
        if self._own_bot:
            await self.bot.session.close()

    def _thread(self, topic_key: str) -> int | None:
        t = self.topics().get(topic_key)
        return t["thread_id"] if t and t["thread_id"] and t["thread_id"] > 0 else None

    async def ensure_topics(self) -> dict[str, int | None]:
        existing = self.topics()
        for pos, (key, title) in enumerate(topic_specs(self.s)):
            cur = existing.get(key)
            if cur and cur["thread_id"] and cur["thread_id"] > 0:
                if cur["title"] != title:
                    try:
                        await self.bot.edit_forum_topic(self.chat_id, cur["thread_id"], name=title)
                    except Exception as e:  # не критично
                        log.warning("Не удалось переименовать топик %s: %s", key, e)
                    self.db.set_hub_topic(key, title, cur["thread_id"], pos)
                continue
            topic = await self.bot.create_forum_topic(self.chat_id, name=title)
            self.db.set_hub_topic(key, title, topic.message_thread_id, pos)
            log.info("Создан топик %s (thread_id=%s)", title, topic.message_thread_id)
        return {k: v["thread_id"] for k, v in self.topics().items()}

    async def _recreate_topic(self, topic_key: str) -> None:
        title = dict(topic_specs(self.s)).get(topic_key, topic_key)
        topic = await self.bot.create_forum_topic(self.chat_id, name=title)
        pos = (self.topics().get(topic_key) or {}).get("position") or 0
        self.db.set_hub_topic(topic_key, title, topic.message_thread_id, pos)

    async def _call_in_topic(self, topic_key: str, fn):
        from aiogram.exceptions import TelegramBadRequest

        try:
            return await fn(self._thread(topic_key))
        except TelegramBadRequest as e:
            if "thread not found" in str(e).lower() or "topic_deleted" in str(e).lower():
                log.warning("Топик %s удалён в хабе — создаю заново", topic_key)
                await self._recreate_topic(topic_key)
                return await fn(self._thread(topic_key))
            raise

    async def forward(self, topic_key: str, rows: list[dict[str, Any]]) -> list[int]:
        from aiogram.exceptions import TelegramAPIError

        ids = []
        for r in rows:
            tg_id: int | None = None
            shown = html.escape(r.get("text") or "")
            try:
                msg = await self._call_in_topic(topic_key, lambda th: self.bot.forward_message(
                    chat_id=self.chat_id, from_chat_id=r["chat_id"], message_id=r["message_id"],
                    message_thread_id=th))
                tg_id = msg.message_id
            except TelegramAPIError as e:
                log.info("forwardMessage не удался (%s) — пробую copyMessage", e)
                try:
                    res = await self._call_in_topic(topic_key, lambda th: self.bot.copy_message(
                        chat_id=self.chat_id, from_chat_id=r["chat_id"], message_id=r["message_id"],
                        message_thread_id=th))
                    tg_id = res.message_id
                except TelegramAPIError as e2:
                    log.info("copyMessage не удался (%s) — отправляю текст со ссылкой", e2)
                    shown = quote_html(r)
                    msg = await self._call_in_topic(topic_key, lambda th: self.bot.send_message(
                        self.chat_id, shown, message_thread_id=th, disable_web_page_preview=True))
                    tg_id = msg.message_id
            ids.append(self._record_forward(topic_key, r, tg_id, shown))
        return ids

    async def send(self, topic_key: str, text: str, *, kind: str = "note", reply_to: int | None = None,
                   **meta: Any) -> int:
        from aiogram.types import ReplyParameters

        reply_params = None
        if reply_to:
            parent = self.db.hub_message(reply_to)
            if parent and parent.get("tg_message_id"):
                reply_params = ReplyParameters(message_id=parent["tg_message_id"], allow_sending_without_reply=True)
        msg = await self._call_in_topic(topic_key, lambda th: self.bot.send_message(
            self.chat_id, text, message_thread_id=th, reply_parameters=reply_params, disable_web_page_preview=True))
        return self.db.add_hub_message(topic_key=topic_key, kind=kind, text=text, reply_to=reply_to,
                                       tg_message_id=msg.message_id, **meta)

    async def edit(self, hub_id: int, text: str) -> None:
        from aiogram.exceptions import TelegramBadRequest

        row = self.db.hub_message(hub_id)
        if row and row.get("tg_message_id"):
            try:
                await self.bot.edit_message_text(text, chat_id=self.chat_id, message_id=row["tg_message_id"],
                                                 disable_web_page_preview=True)
            except TelegramBadRequest as e:
                if "not modified" not in str(e).lower():
                    raise
        self.db.update_hub_message(hub_id, text=text)

    async def pin(self, hub_id: int) -> None:
        row = self.db.hub_message(hub_id)
        if row and row.get("tg_message_id"):
            await self.bot.pin_chat_message(self.chat_id, row["tg_message_id"], disable_notification=True)
        self.db.update_hub_message(hub_id, pinned=1)
