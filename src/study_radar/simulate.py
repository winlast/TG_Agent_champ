"""Режим симуляции: «проигрывает» сообщения сценария (или фикстур) через весь пайплайн агента.

Работает без Telegram (FakeHub). Склейка очередей — как в живом режиме: пост обрабатывается,
когда очередь автора завершилась (пришло сообщение другого автора или прошло > gap минут).
Часы агента = время сценария, поэтому «завтра» и «на этой неделе» на доске считаются от него.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

from study_radar.agent.graph import AgentRuntime, AgentState
from study_radar.ingest.bursts import make_post
from study_radar.ingest.normalize import clean_text, message_link
from study_radar.models import ChatMessage, Post

log = logging.getLogger(__name__)


@dataclass
class Edit:
    chat_id: int
    message_id: int
    at: datetime
    text: str


@dataclass
class Scenario:
    name: str
    chats: dict[str, dict[str, Any]]
    events: list[ChatMessage | Edit]
    end_clock: datetime | None


def load_scenario(path: Path | str, tz) -> Scenario:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    chats = data["chats"]
    next_id: dict[str, int] = {k: int(v.get("first_message_id", 100)) for k, v in chats.items()}
    keys: dict[str, tuple[int, int]] = {}
    events: list[ChatMessage | Edit] = []

    def when(raw: Any) -> datetime:
        dt = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
        return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)

    for ev in data["events"]:
        if "edit" in ev:
            chat_id, mid = keys[ev["edit"]]
            events.append(Edit(chat_id, mid, when(ev["at"]), clean_text(ev["text"])))
            continue
        chat = chats[ev["chat"]]
        mid = next_id[ev["chat"]]
        next_id[ev["chat"]] += 1
        text = clean_text(ev.get("text", ""))
        if ev.get("file"):
            text = (text + "\n" if text else "") + f"[файл] {ev['file']}"
        reply_to = keys[ev["reply"]][1] if ev.get("reply") else None
        msg = ChatMessage(
            chat_id=chat["id"], message_id=mid, chat_title=chat["title"],
            topic=ev.get("topic") or ("General" if chat.get("forum") else None), author=ev["from"],
            author_id=ev.get("from_id") or "user_" + ev["from"], date=when(ev["at"]), text=text, reply_to=reply_to,
            file_name=ev.get("file"), link=message_link(chat["id"], mid),
        )
        if ev.get("key"):
            keys[ev["key"]] = (chat["id"], mid)
        events.append(msg)
    end = when(data["end_clock"]) if data.get("end_clock") else None
    return Scenario(name=data.get("name", Path(path).stem), chats=chats, events=events, end_clock=end)


def post_from_db(db, post_id: str) -> Post | None:
    """Пересобрать пост из текущих версий сообщений (после редактирования)."""
    row = db.get_post_row(post_id)
    if not row:
        return None
    import json

    rows = db.get_messages(row["chat_id"], json.loads(row["message_ids"]))
    msgs = [ChatMessage.model_validate(r) for r in rows]
    return make_post(msgs) if msgs else None


OnResult = Callable[[AgentState, str], Awaitable[None] | None]


class Player:
    """Подаёт сообщения в агента с живой склейкой очередей."""

    def __init__(self, runtime: AgentRuntime, on_result: OnResult | None = None, delay: float = 0.0):
        self.rt = runtime
        self.db = runtime.sv.db
        self.gap = timedelta(minutes=runtime.s.bursts.gap_minutes)
        self.pending: dict[tuple[int, str | None], list[ChatMessage]] = {}
        self.on_result = on_result
        self.delay = delay
        self.results: list[AgentState] = []

    async def _process(self, post: Post, trigger: str) -> None:
        state = await self.rt.process(post, trigger)
        self.results.append(state)
        if self.on_result:
            r = self.on_result(state, trigger)
            if asyncio.iscoroutine(r):
                await r
        if self.delay:
            await asyncio.sleep(self.delay)

    async def _flush(self, key) -> None:
        msgs = self.pending.pop(key, None)
        if msgs:
            self.db.set_clock(msgs[-1].date + timedelta(seconds=5))
            await self._process(make_post(msgs), "new")

    async def flush_older(self, now: datetime) -> None:
        for key, msgs in sorted(self.pending.items(), key=lambda kv: kv[1][-1].date):
            if now - msgs[-1].date > self.gap:
                await self._flush(key)

    async def flush_all(self) -> None:
        for key, _ in sorted(self.pending.items(), key=lambda kv: kv[1][-1].date):
            await self._flush(key)

    async def feed(self, item: ChatMessage | Edit) -> None:
        if isinstance(item, Edit):
            await self.flush_all()
            self.db.set_clock(item.at)
            row = self.db.get_message(item.chat_id, item.message_id)
            if row is None:
                return
            msg = ChatMessage.model_validate({**row, "text": item.text, "edited": item.at.isoformat()})
            self.db.upsert_message(msg)
            post_id = row["post_id"]
            post = post_from_db(self.db, post_id) if post_id else None
            if post is not None:
                await self._process(post, "edit")
            return
        await self.flush_older(item.date)
        if item.is_service:
            self.db.upsert_message(item)
            return
        self.db.set_clock(item.date)
        self.db.upsert_message(item)
        key = (item.chat_id, item.topic)
        burst = self.pending.get(key)
        if burst:
            prev = burst[-1]
            same_author = (prev.author_id or prev.author) == (item.author_id or item.author)
            ext_reply = item.reply_to is not None and item.reply_to not in {m.message_id for m in burst}
            if same_author and item.date - prev.date <= self.gap and not ext_reply:
                burst.append(item)
                return
            await self._flush(key)
        self.pending[key] = [item]


async def run_scenario(runtime: AgentRuntime, scenario: Scenario, on_result: OnResult | None = None,
                       delay: float = 0.0) -> Player:
    db = runtime.sv.db
    for key, chat in scenario.chats.items():
        db.upsert_chat(chat["id"], chat["title"], "forum" if chat.get("forum") else "group", bool(chat.get("forum")))
    player = Player(runtime, on_result, delay)
    for ev in scenario.events:
        await player.feed(ev)
    await player.flush_all()
    if scenario.end_clock:
        db.set_clock(scenario.end_clock)
    await runtime.toolbox.call("refresh_deadlines_board", {})
    return player
