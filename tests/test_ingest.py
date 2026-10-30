"""Парсер Telegram-экспорта (все особенности раздела 4.1) и склейка очередей."""

import json
from datetime import datetime, timedelta

from study_radar.ingest.bursts import group_bursts
from study_radar.ingest.normalize import flatten_rich, flatten_text, is_emoji_only
from study_radar.ingest.telegram_export import load_export, parse_chat

from conftest import msg

EXPORT = {
    "name": "Кейс-чемпионат", "type": "private_supergroup", "id": 4361773304,
    "messages": [
        {"id": 22, "type": "service", "date": "2026-10-21T16:12:35", "date_unixtime": "1792588355",
         "actor": "Громов", "action": "topic_created", "title": "Задание", "text": ""},
        {"id": 30, "type": "message", "date": "2026-10-21T16:19:00", "from": "Громов Ю. Е.", "from_id": "user1",
         "reply_to_message_id": 22, "text": "Описание задания:"},
        {"id": 31, "type": "message", "date": "2026-10-21T16:19:32", "edited": "2026-10-21T16:24:07",
         "from": "Громов Ю. Е.", "from_id": "user1", "reply_to_message_id": 22,
         "text": "А. Реализация всех возможностей", "text_entities": []},
        {"id": 32, "type": "message", "date": "2026-10-21T16:30:00", "from": "Студент", "from_id": "user2",
         "reply_to_message_id": 31, "text": ["Вот ссылка - ", {"type": "link", "text": "https://t.me/x"}, ""]},
        {"id": 2, "type": "message", "date": "2026-10-20T10:00:00", "from": "Студент", "from_id": "user2",
         "text": ["Вот ссылка - ", {"type": "link", "text": "https://t.me/abc"}, ""]},
        {"id": 159, "type": "message", "date": "2026-10-22T10:00:00", "from": "Громов Ю. Е.", "from_id": "user1",
         "text": "", "rich_message": {"blocks": [
             {"type": "paragraph", "text": {"type": "plain", "text": "Требования:"}},
             {"type": "list", "kind": "bullet", "items": [{"text": {"type": "plain", "text": "локально"}},
                                                          {"text": {"type": "plain", "text": "MCP"}}]}]}},
        {"id": 9, "type": "message", "date": "2026-10-21T16:40:00", "from": "Громов Ю. Е.", "from_id": "user1",
         "file": "(File not included...)", "file_name": "RAG.pdf", "text": ""},
        {"id": 10, "type": "service", "date": "2026-10-21T16:41:00", "actor": "Громов", "action": "pin_message",
         "text": ""},
    ],
}


def test_flatten_variants():
    assert flatten_text("abc") == "abc"
    assert flatten_text(["Вот ссылка - ", {"type": "link", "text": "https://t.me/"}, ""]) == "Вот ссылка - https://t.me/"
    rich = EXPORT["messages"][5]["rich_message"]
    assert flatten_rich(rich) == "Требования:\n• локально\n• MCP"
    assert is_emoji_only("😂😂 !!!") and not is_emoji_only("ок 👍")


def test_parse_export_features(settings):
    chat = parse_chat(EXPORT, settings.tz)
    by_id = {m.message_id: m for m in chat.messages}
    assert chat.chat_id == -1004361773304 and chat.is_forum
    # топик по цепочке reply_to → topic_created; ответ на создание топика — не reply
    assert by_id[30].topic == "Задание" and by_id[30].reply_to is None
    assert by_id[32].topic == "Задание" and by_id[32].reply_to == 31
    assert by_id[2].topic == "General"
    assert by_id[31].edited is not None
    assert by_id[32].text == "Вот ссылка - https://t.me/x"
    assert "• MCP" in by_id[159].text
    assert by_id[9].text == "[файл] RAG.pdf"
    assert by_id[22].is_service and by_id[22].service_action == "topic_created"
    assert by_id[30].date.tzinfo is not None
    assert by_id[30].link == "https://t.me/c/4361773304/30"


def test_full_account_export(tmp_path, settings):
    p = tmp_path / "result.json"
    p.write_text(json.dumps({"chats": {"list": [EXPORT, {**EXPORT, "id": 1, "name": "Второй"}]}}), encoding="utf-8")
    chats = load_export(p, settings.tz)
    assert [c.title for c in chats] == ["Кейс-чемпионат", "Второй"]


def test_bursts_glue_teacher_series(settings):
    chat = parse_chat(EXPORT, settings.tz)
    posts = group_bursts(chat.messages, 5)
    first = next(p for p in posts if 30 in p.message_ids)
    assert first.message_ids == [30, 31]          # серия преподавателя склеена
    assert "Описание задания:\nА. Реализация" in first.text
    assert all(not any(i in p.message_ids for i in (22, 10)) for p in posts)  # служебные не классифицируются


def test_bursts_split_by_gap_author_and_reply(settings):
    base = "2026-10-26T10:00"
    m = [msg(settings, -1, 1, "раз", "A", base), msg(settings, -1, 2, "два", "A", "2026-10-26T10:04"),
         msg(settings, -1, 3, "три", "A", "2026-10-26T10:11"), msg(settings, -1, 4, "чужое", "B", "2026-10-26T10:12"),
         msg(settings, -1, 5, "ответ", "B", "2026-10-26T10:13", reply_to=1)]
    posts = group_bursts(m, 5)
    assert [p.message_ids for p in posts] == [[1, 2], [3], [4], [5]]


def test_index_export_is_idempotent(tmp_path, services):
    p = tmp_path / "chat.json"
    p.write_text(json.dumps(EXPORT, ensure_ascii=False), encoding="utf-8")
    st1 = services.indexer.index_export(p)
    n1 = services.store.count("messages")
    st2 = services.indexer.index_export(p)
    assert st1.indexed == n1 > 0
    assert st2.indexed == 0 and services.store.count("messages") == n1
    assert services.store.count("subjects") == len(services.s.subjects)
