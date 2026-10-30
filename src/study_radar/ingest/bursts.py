"""Склейка «очередей»: преподаватель дробит объявление на серию коротких сообщений.

Сообщения одного автора подряд в одном чате/топике с интервалом ≤ gap минут → один логический пост.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Iterable

from study_radar.models import ChatMessage, Post


def make_post(msgs: list[ChatMessage]) -> Post:
    first = msgs[0]
    return Post(
        post_id=f"{first.chat_id}:{first.message_id}",
        chat_id=first.chat_id,
        chat_title=first.chat_title,
        topic=first.topic,
        thread_id=first.thread_id,
        author=first.author,
        author_id=first.author_id,
        date=first.date,
        message_ids=[m.message_id for m in msgs],
        text="\n".join(m.text for m in msgs if m.text),
        reply_to=first.reply_to,
        link=first.link,
        edited=any(m.edited for m in msgs),
    )


def _same_author(a: ChatMessage, b: ChatMessage) -> bool:
    if a.author_id and b.author_id:
        return a.author_id == b.author_id
    return a.author == b.author


def group_bursts(messages: Iterable[ChatMessage], gap_minutes: float = 5) -> list[Post]:
    """Группирует сообщения в посты. Служебные сообщения пропускаются."""
    gap = timedelta(minutes=gap_minutes)
    streams: dict[tuple[int, str | None], list[ChatMessage]] = defaultdict(list)
    for m in messages:
        if not m.is_service:
            streams[(m.chat_id, m.topic)].append(m)

    posts: list[Post] = []
    for stream in streams.values():
        stream.sort(key=lambda m: (m.date, m.message_id))
        burst: list[ChatMessage] = []
        for m in stream:
            if burst:
                prev = burst[-1]
                ids = {x.message_id for x in burst}
                replies_outside = m.reply_to is not None and m.reply_to not in ids
                if _same_author(prev, m) and m.date - prev.date <= gap and not replies_outside:
                    burst.append(m)
                    continue
                posts.append(make_post(burst))
            burst = [m]
        if burst:
            posts.append(make_post(burst))
    posts.sort(key=lambda p: (p.date, p.chat_id, p.message_ids[0]))
    return posts
