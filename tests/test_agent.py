"""Сквозные тесты агента: LangGraph + реальный MCP-сервер (stdio) + FakeHub + FakeLLM.

FakeLLM проверяет проводку пайплайна (контекст → JSON → MCP-вызовы → эффекты), а не качество модели.
"""

import json

import pytest

from study_radar.agent.graph import AgentRuntime
from study_radar.ingest.bursts import make_post
from study_radar.llm.client import FakeLLM
from study_radar.simulate import Edit, Player

from conftest import msg

ELTEH, PHYS, FLOOD, GENERAL = -1001000000003, -1001000000002, -1001000000005, -1001000000001
TEACHER = "Ковалёв А. П."


async def feed(rt, *items):
    player = Player(rt)
    for it in items:
        await player.feed(it)
    await player.flush_all()
    return player.results


class StubLLM(FakeLLM):
    """Возвращает заданную классификацию — для проверки валидации и маршрутов."""

    def __init__(self, settings, payload):
        super().__init__(settings)
        self.payload = payload

    async def json(self, system, user, schema, *, hint=None):
        return json.dumps(self.payload, ensure_ascii=False)


async def test_noise_examples(settings, services):
    async with AgentRuntime(settings, "fake", services) as rt:
        res = await feed(rt, msg(settings, FLOOD, 1, "физика — говно", "Кирилл"),
                         msg(settings, GENERAL, 2, "кто на физру?", "Олег", "2026-10-27T08:00"))
    for st in res:
        assert st["route"] in {"skip", "dropped"}
        assert not st.get("tool_calls")
    assert not services.db.hub_messages("physics")


async def test_transfer_by_reply_updates_not_duplicates(settings, services):
    async with AgentRuntime(settings, "fake", services) as rt:
        res = await feed(
            rt,
            msg(settings, ELTEH, 10, "Защита ЛР2 в пятницу 30.10 в 10:15, ауд. 512", TEACHER, "2026-10-26T10:15",
                "Элтех лабы"),
            msg(settings, ELTEH, 11, "лабу перенесли на среду", TEACHER, "2026-10-28T09:05", "Элтех лабы", reply_to=10),
        )
    upd = res[-1]
    c = upd["result"].classification
    assert c.category.value == "deadline_update" and c.subject == "electro"
    deadlines = services.db.list_deadlines()
    assert len(deadlines) == 1                                  # обновили, а не создали дубль
    d = deadlines[0]
    assert d.due_at.strftime("%d.%m") == "04.11" and d.prev_due_at.strftime("%d.%m") == "30.10"
    assert any(h["action"] == "updated" for h in services.db.history(d.id))
    hub = services.db.hub_messages("electro")
    assert any(m["kind"] == "update" and "Перенос" in m["text"] for m in hub)
    assert any(m["kind"] == "note" and "<s>" in m["text"] for m in hub)   # старая дата зачёркнута
    board = services.db.board_pages()[0]["text"]
    assert "было: пт 30.10" in board
    tools = [x["tool"] for x in upd["tool_calls"]]
    assert tools[:2] == ["forward_to_topic", "update_deadline"] and "refresh_deadlines_board" in tools
    assert not upd["fallback"]


async def test_dedup_same_deadline_in_two_chats(settings, services):
    async with AgentRuntime(settings, "fake", services) as rt:
        await feed(rt,
                   msg(settings, GENERAL, 1, "ЛР3 по БД сдаём до 5 ноября", "Мария (староста)", "2026-10-27T09:00"),
                   msg(settings, FLOOD, 1, "лаба 3 по БД до 5 ноября, не забудьте", "Аня", "2026-10-27T09:20"))
    ds = services.db.list_deadlines()
    assert len(ds) == 1 and len(ds[0].sources) == 2


async def test_edit_changes_deadline(settings, services):
    async with AgentRuntime(settings, "fake", services) as rt:
        m = msg(settings, GENERAL, 5, "ДЗ по вебу сдать до 3 ноября", "Мария (староста)", "2026-10-26T14:00")
        from datetime import datetime

        res = await feed(rt, m, Edit(GENERAL, 5, datetime(2026, 10, 29, 20, 0, tzinfo=settings.tz),
                                     "ДЗ по вебу сдать до 6 ноября (продлили)"))
    assert res[-1]["trigger"] == "edit"
    ds = services.db.list_deadlines()
    assert len(ds) == 1 and ds[0].due_at.strftime("%d.%m") == "06.11"
    assert ds[0].prev_due_at.strftime("%d.%m") == "03.11"
    assert any("отредактировано" in m["text"] for m in services.db.hub_messages("web"))


async def test_fallback_when_llm_makes_bad_calls(settings, services):
    services.__dict__["llm"] = FakeLLM(settings, broken=True)
    async with AgentRuntime(settings, "fake", services) as rt:
        res = await feed(rt, msg(settings, PHYS, 1, "ДЗ №3 по физике сдать до пятницы", "Орлов Д. В."))
    st = res[0]
    assert st["fallback"] is True and "попыток" in st["fallback_reason"]
    by = {x["by"] for x in st["tool_calls"]}
    assert by >= {"llm", "fallback"}
    assert any(not x["ok"] for x in st["tool_calls"] if x["by"] == "llm")   # битые вызовы LLM записаны
    assert len(services.db.list_deadlines()) == 1                           # но эффект достигнут
    dec = services.db.decisions()[0]
    assert dec["fallback"] == 1 and dec["fallback_reason"]


async def test_low_confidence_goes_to_review(settings, services):
    services.__dict__["llm"] = StubLLM(settings, {
        "is_important": True, "subject": "химия", "category": "announcement", "importance": 3,
        "title": "Что-то", "summary": "Непонятно", "due_text": None, "due_at": None, "location": None,
        "related_deadline_id": None, "confidence": 0.9, "reason": "тест"})
    async with AgentRuntime(settings, "fake", services) as rt:
        res = await feed(rt, msg(settings, GENERAL, 1, "Завтра всё по-старому, но с нюансами", "Олег"))
    st = res[0]
    assert st["route"] == "review"
    assert any("нет в справочнике" in n for n in st["result"].notes)
    assert services.db.hub_messages("review")


async def test_validation_fizra_is_not_physics(settings, services):
    services.__dict__["llm"] = StubLLM(settings, {
        "is_important": True, "subject": "physics", "category": "event", "importance": 2,
        "title": "Физра", "summary": "Физра", "due_text": "сегодня", "due_at": None, "location": None,
        "related_deadline_id": None, "confidence": 0.8, "reason": "тест"})
    async with AgentRuntime(settings, "fake", services) as rt:
        res = await feed(rt, msg(settings, GENERAL, 1, "кто на физру сегодня?", "Олег"))
    c = res[0]["result"].classification
    assert c.subject is None and res[0]["route"] == "review"


async def test_date_mismatch_is_corrected(settings, services):
    services.__dict__["llm"] = StubLLM(settings, {
        "is_important": True, "subject": "physics", "category": "deadline", "importance": 4,
        "title": "ДЗ №3", "summary": "ДЗ", "due_text": "до пятницы", "due_at": "2026-10-31T23:59:00+03:00",
        "location": None, "related_deadline_id": None, "confidence": 0.9, "reason": "тест"})
    async with AgentRuntime(settings, "fake", services) as rt:
        res = await feed(rt, msg(settings, PHYS, 1, "ДЗ №3 сдать до пятницы", "Орлов Д. В."))
    c = res[0]["result"].classification
    assert c.due_at.strftime("%d.%m %H:%M") == "30.10 23:59" and c.confidence < 0.9
    assert any("расхождение дат" in n for n in res[0]["result"].notes)


async def test_teacher_series_forwarded_together(settings, services):
    t = "Громов Ю. Е. (АСОИУ)"
    async with AgentRuntime(settings, "fake", services) as rt:
        res = await feed(rt, *[msg(settings, -1004, i, txt, t, f"2026-10-26T12:0{i}", "Кейс-чемпионат", "Задание")
                               for i, txt in enumerate(["Описание задания:", "1. Агент локально", "2. Защита — презентация"])])
    assert len(res) == 1 and res[0]["post"].message_ids == [0, 1, 2]
    fwd = [m for m in services.db.hub_messages() if m["kind"] == "forward"]
    assert len(fwd) == 3


async def test_trace_is_logged(settings, services):
    async with AgentRuntime(settings, "fake", services) as rt:
        await feed(rt, msg(settings, PHYS, 1, "ДЗ №3 по физике сдать до пятницы", "Орлов Д. В."))
    d = services.db.decisions()[0]
    ctx = json.loads(d["context"])
    assert ctx["post"]["text"] and "candidates" in ctx
    assert "Сообщение для классификации" in d["prompt"]
    calls = json.loads(d["tool_calls"])
    assert {c["tool"] for c in calls} >= {"forward_to_topic", "create_deadline", "refresh_deadlines_board"}
    assert services.db.hub_messages("journal")
