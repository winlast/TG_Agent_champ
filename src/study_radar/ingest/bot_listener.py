"""Режим бота (aiogram 3): слушает чаты-источники (message + edited_message) и ведёт хаб.

Команды:
  /sethub     — в супергруппе с темами: сделать её хабом (бот создаст темы и доску);
  /chatid     — показать id чата (для .env);
  /deadlines  — показать доску дедлайнов;
  /ask вопрос — ответ по переписке со ссылками (Модуль 2, RAG);
  /help.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from study_radar.config import Settings
from study_radar.ingest.bursts import make_post
from study_radar.ingest.normalize import clean_text, message_link
from study_radar.models import ChatMessage, Post
from study_radar.storage.db import Database

log = logging.getLogger(__name__)

HELP = (
    "🤖 <b>StudyRadar</b> — сортирует учебные сообщения и ведёт доску дедлайнов.\n\n"
    "• Добавьте меня в учебные чаты (privacy mode у бота должен быть выключен).\n"
    "• В группе-хабе с темами напишите /sethub — я создам темы предметов и доску.\n"
    "• /deadlines — доска дедлайнов\n"
    "• /ask когда сдавать лабу по элтеху? — ответ по переписке со ссылками\n"
    "• /chatid — id этого чата"
)


def from_aiogram(m: Any, db: Database, tz) -> ChatMessage:
    """aiogram Message → ChatMessage."""
    chat = m.chat
    text = m.text or m.caption or ""
    file_name = m.document.file_name if m.document else None
    if not text:
        if file_name:
            text = f"[файл] {file_name}"
        elif m.photo:
            text = "[фото]"
        elif m.sticker:
            text = f"[стикер] {m.sticker.emoji or ''}".strip()
    elif file_name:
        text = f"{text}\n[файл] {file_name}"
    user = m.from_user
    if user:
        author = " ".join(x for x in (user.first_name, user.last_name) if x) or (user.username or str(user.id))
        author_id = f"user{user.id}"
    else:
        author = (m.sender_chat.title if m.sender_chat else "") or "канал"
        author_id = f"chat{m.sender_chat.id}" if m.sender_chat else None
    thread_id = m.message_thread_id if getattr(m, "is_topic_message", False) else None
    topic = None
    rt = m.reply_to_message
    if chat.is_forum:
        if rt is not None and rt.forum_topic_created is not None and thread_id:
            db.set_source_topic(chat.id, thread_id, rt.forum_topic_created.name)
        topic = db.get_source_topic(chat.id, thread_id) if thread_id else "General"
        topic = topic or f"Тема {thread_id}"
    reply_to = None
    if rt is not None and rt.forum_topic_created is None and rt.message_id != thread_id:
        reply_to = rt.message_id
    link = message_link(chat.id, m.message_id, chat.username)
    return ChatMessage(
        chat_id=chat.id, message_id=m.message_id, chat_title=chat.title or chat.full_name or str(chat.id), topic=topic,
        thread_id=thread_id, author=author, author_id=author_id, date=m.date.astimezone(tz),
        edited=m.edit_date.astimezone(tz) if m.edit_date else None, text=clean_text(text), reply_to=reply_to,
        file_name=file_name, link=link,
    )


class LiveBuffer:
    """Живая склейка очередей: ждём паузу debounce секунд, потом отдаём пост на обработку."""

    def __init__(self, settings: Settings, queue: asyncio.Queue):
        self.gap = timedelta(minutes=settings.bursts.gap_minutes)
        self.debounce = settings.bursts.live_debounce_seconds
        self.queue = queue
        self.pending: dict[tuple[int, str | None], list[ChatMessage]] = {}
        self.timers: dict[tuple[int, str | None], asyncio.Task] = {}

    def _schedule(self, key) -> None:
        if (t := self.timers.get(key)) is not None:
            t.cancel()
        self.timers[key] = asyncio.create_task(self._later(key))

    async def _later(self, key) -> None:
        try:
            await asyncio.sleep(self.debounce)
        except asyncio.CancelledError:
            return
        self.flush(key)

    def flush(self, key) -> None:
        msgs = self.pending.pop(key, None)
        self.timers.pop(key, None)
        if msgs:
            self.queue.put_nowait((make_post(msgs), "new"))

    def add(self, m: ChatMessage) -> None:
        key = (m.chat_id, m.topic)
        burst = self.pending.get(key)
        if burst:
            prev = burst[-1]
            ext_reply = m.reply_to is not None and m.reply_to not in {x.message_id for x in burst}
            if (prev.author_id or prev.author) == (m.author_id or m.author) and m.date - prev.date <= self.gap \
                    and not ext_reply:
                burst.append(m)
                self._schedule(key)
                return
            if (t := self.timers.get(key)) is not None:
                t.cancel()
            self.flush(key)
        self.pending[key] = [m]
        self._schedule(key)

    def update_pending(self, m: ChatMessage) -> bool:
        """Редактирование сообщения, которое ещё ждёт в очереди: просто заменить текст."""
        for msgs in self.pending.values():
            for i, x in enumerate(msgs):
                if x.chat_id == m.chat_id and x.message_id == m.message_id:
                    msgs[i] = m
                    return True
        return False


async def run_bot(settings: Settings) -> None:
    from aiogram import Bot, Dispatcher, F, Router
    from aiogram.client.default import DefaultBotProperties
    from aiogram.filters import Command, CommandObject
    from aiogram.types import Message

    from study_radar.agent.ask import Asker
    from study_radar.agent.graph import AgentRuntime
    from study_radar.services import Services
    from study_radar.simulate import post_from_db

    bot = Bot(settings.telegram.token, default=DefaultBotProperties(parse_mode="HTML"))
    me = await bot.get_me()
    sv = Services(settings, "bot", bot=bot)
    db = sv.db
    db.set_clock(None)   # живой режим — реальное время (часы сценария только в simulate)
    queue: asyncio.Queue[tuple[Post, str]] = asyncio.Queue()
    buffer = LiveBuffer(settings, queue)
    state: dict[str, Any] = {"rt": None}
    router = Router()

    if not me.can_read_all_group_messages:
        log.warning("Privacy mode у @%s ВКЛЮЧЁН — бот не видит обычные сообщения в группах. "
                    "@BotFather → /setprivacy → Disable, затем удалите и снова добавьте бота в чаты.", me.username)

    async def start_runtime() -> None:
        if state["rt"] is None and sv.hub_chat_id():
            rt = AgentRuntime(settings, "bot", sv)
            await rt.__aenter__()
            state["rt"] = rt
            log.info("Агент запущен. Хаб: %s", sv.hub_chat_id())

    def is_source(chat_id: int) -> bool:
        if chat_id == sv.hub_chat_id():
            return False
        ids = settings.telegram.source_chat_ids
        return not ids or chat_id in ids

    @router.message(Command("start", "help"))
    async def cmd_help(m: Message) -> None:
        await m.answer(HELP)

    @router.message(Command("chatid"))
    async def cmd_chatid(m: Message) -> None:
        forum = " · темы включены" if m.chat.is_forum else ""
        await m.answer(f"id этого чата: <code>{m.chat.id}</code> ({m.chat.type}{forum})")

    @router.message(Command("sethub"))
    async def cmd_sethub(m: Message) -> None:
        if not m.chat.is_forum:
            await m.answer("Хабом может быть только супергруппа с включёнными темами: "
                           "Настройки группы → Темы → включить. Затем снова /sethub.")
            return
        member = await bot.get_chat_member(m.chat.id, me.id)
        if member.status not in {"administrator", "creator"} or not getattr(member, "can_manage_topics", False):
            await m.answer("Сделайте меня администратором с правами «Управление темами» и «Закрепление сообщений», "
                           "затем снова /sethub.")
            return
        db.set_meta("hub_chat_id", str(m.chat.id))
        settings.telegram.hub_chat_id = m.chat.id
        sv.__dict__.pop("hub", None)
        await start_runtime()
        await m.answer("✅ Этот чат — хаб StudyRadar. Темы предметов и доска «📅 Дедлайны» созданы.\n"
                       f"Если хотите зафиксировать: HUB_CHAT_ID=<code>{m.chat.id}</code> в .env")

    @router.message(Command("deadlines"))
    async def cmd_deadlines(m: Message) -> None:
        if state["rt"] is not None:
            await state["rt"].toolbox.call("refresh_deadlines_board", {})
        pages = db.board_pages()
        await m.answer(pages[0]["text"] if pages else "Доска пока пуста.", disable_web_page_preview=True)

    @router.message(Command("ask"))
    async def cmd_ask(m: Message, command: CommandObject) -> None:
        q = (command.args or "").strip()
        if not q:
            await m.answer("Напишите вопрос: /ask когда сдавать лабу по элтеху?")
            return
        await bot.send_chat_action(m.chat.id, "typing", message_thread_id=m.message_thread_id)
        res = await Asker(sv, sv.llm).ask(q)
        await m.reply(res.format(html_links=True), disable_web_page_preview=True)

    @router.message(F.forum_topic_created)
    async def on_topic(m: Message) -> None:
        if m.message_thread_id:
            db.set_source_topic(m.chat.id, m.message_thread_id, m.forum_topic_created.name)

    @router.message()
    async def on_message(m: Message) -> None:
        if m.chat.type not in {"group", "supergroup"} or not is_source(m.chat.id):
            return
        if m.from_user and m.from_user.id == me.id:
            return
        cm = from_aiogram(m, db, settings.tz)
        if not cm.text:
            return
        db.upsert_chat(m.chat.id, cm.chat_title, m.chat.type, bool(m.chat.is_forum))
        db.upsert_message(cm)
        buffer.add(cm)

    @router.edited_message()
    async def on_edit(m: Message) -> None:
        if m.chat.type not in {"group", "supergroup"} or not is_source(m.chat.id):
            return
        cm = from_aiogram(m, db, settings.tz)
        db.upsert_message(cm)
        if buffer.update_pending(cm):
            return
        post_id = db.post_id_for_message(cm.chat_id, cm.message_id)
        post = post_from_db(db, post_id) if post_id else None
        if post is not None:
            await queue.put((post, "edit"))

    async def worker() -> None:
        while True:
            post, trigger = await queue.get()
            try:
                if state["rt"] is None:
                    await start_runtime()
                if state["rt"] is None:
                    log.warning("Хаб не задан — сообщение сохранено, но не обработано. Напишите /sethub в хабе.")
                    continue
                st = await state["rt"].process(post, trigger)
                c = st.get("result")
                log.info("%s · %s → %s", post.chat_title, post.text[:50].replace("\n", " "),
                         c.classification.category.value if c else st.get("route"))
            except Exception:
                log.exception("Ошибка обработки поста %s", post.post_id)

    dp = Dispatcher()
    dp.include_router(router)
    await start_runtime()
    if state["rt"] is None:
        log.warning("Хаб не задан: создайте группу с темами, добавьте @%s админом и напишите там /sethub", me.username)
    task = asyncio.create_task(worker())
    log.info("Бот @%s слушает чаты (message, edited_message). Ctrl+C — остановить.", me.username)
    try:
        await dp.start_polling(bot, allowed_updates=["message", "edited_message"])
    finally:
        task.cancel()
        if state["rt"] is not None:
            await state["rt"].__aexit__(None, None, None)
        await bot.session.close()
