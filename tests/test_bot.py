"""Режим бота без сети: нормализация aiogram-сообщений, живая склейка, BotHub на имитации Bot API."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import ForwardMessage
from aiogram.types import Chat, ForumTopicCreated, Message, User

from study_radar.ingest.bot_listener import LiveBuffer, from_aiogram
from study_radar.telegram.board import refresh_board
from study_radar.telegram.hub import BotHub, topic_specs

from conftest import msg

FORUM = Chat(id=-100123, type="supergroup", title="Кейс-чемпионат", is_forum=True)
TEACHER = User(id=1, is_bot=False, first_name="Юрий", last_name="Громов")


def test_from_aiogram_forum_topic_and_reply(services):
    now = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)
    root = Message(message_id=5, date=now, chat=FORUM, forum_topic_created=ForumTopicCreated(name="Задание", icon_color=1))
    m = Message(message_id=7, date=now, chat=FORUM, from_user=TEACHER, text="Описание задания:",
                message_thread_id=5, is_topic_message=True, reply_to_message=root)
    cm = from_aiogram(m, services.db, services.s.tz)
    assert cm.topic == "Задание" and cm.reply_to is None and cm.author == "Юрий Громов"
    assert cm.date.utcoffset() == timedelta(hours=3)
    m2 = Message(message_id=8, date=now, chat=FORUM, from_user=TEACHER, text="ок", message_thread_id=5,
                 is_topic_message=True, reply_to_message=m)
    cm2 = from_aiogram(m2, services.db, services.s.tz)
    assert cm2.topic == "Задание" and cm2.reply_to == 7          # имя темы запомнено
    assert cm2.link == "https://t.me/c/123/8"


async def test_live_buffer_debounce(settings):
    settings.bursts.live_debounce_seconds = 0.05
    q: asyncio.Queue = asyncio.Queue()
    buf = LiveBuffer(settings, q)
    t = "Громов"
    buf.add(msg(settings, -1, 1, "Описание задания:", t, "2026-10-26T12:00"))
    buf.add(msg(settings, -1, 2, "1. Локально", t, "2026-10-26T12:01"))
    assert q.empty()
    await asyncio.sleep(0.15)
    post, trigger = q.get_nowait()
    assert post.message_ids == [1, 2] and trigger == "new"


class FakeBot:
    """Имитация aiogram.Bot: forward запрещён (protected content), copy работает."""

    def __init__(self):
        self.calls = []
        self.next_id = 1000
        self.next_thread = 10

    def _msg(self):
        self.next_id += 1
        return SimpleNamespace(message_id=self.next_id)

    async def create_forum_topic(self, chat_id, name):
        self.calls.append(("create_forum_topic", name))
        self.next_thread += 1
        return SimpleNamespace(message_thread_id=self.next_thread)

    async def forward_message(self, **kw):
        self.calls.append(("forward_message", kw["message_thread_id"]))
        raise TelegramBadRequest(ForwardMessage(chat_id=1, from_chat_id=2, message_id=3),
                                 "Bad Request: message has protected content and can't be forwarded")

    async def copy_message(self, **kw):
        self.calls.append(("copy_message", kw["message_thread_id"]))
        return self._msg()

    async def send_message(self, chat_id, text, message_thread_id=None, **kw):
        self.calls.append(("send_message", message_thread_id))
        return self._msg()

    async def edit_message_text(self, text, chat_id, message_id, **kw):
        self.calls.append(("edit_message_text", message_id))

    async def pin_chat_message(self, chat_id, message_id, **kw):
        self.calls.append(("pin_chat_message", message_id))


async def test_bothub_topics_forward_fallback_and_board(settings, services):
    bot = FakeBot()
    hub = BotHub(settings, services.db, chat_id=-100999, bot=bot)
    await hub.ensure_topics()
    created = [c for c in bot.calls if c[0] == "create_forum_topic"]
    assert len(created) == len(topic_specs(settings))
    await hub.ensure_topics()                                     # повторно темы не создаются
    assert len([c for c in bot.calls if c[0] == "create_forum_topic"]) == len(created)

    m = msg(settings, -1001, 5, "ДЗ №3 до пятницы", "Орлов Д. В.")
    services.db.upsert_message(m)
    ids = await hub.forward("physics", services.db.get_messages(-1001, [5]))
    thread = services.db.hub_topics()["physics"]["thread_id"]
    assert ("forward_message", thread) in bot.calls and ("copy_message", thread) in bot.calls
    assert services.db.hub_message(ids[0])["tg_message_id"]

    await refresh_board(hub, services.db, settings)              # первая отрисовка: отправить + закрепить
    await refresh_board(hub, services.db, settings)              # без изменений — без лишних правок
    pins = [c for c in bot.calls if c[0] == "pin_chat_message"]
    assert len(pins) == 1
