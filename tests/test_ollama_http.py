"""Настоящие OllamaLLM и OllamaEmbedder против HTTP-заглушки Ollama API.

Проверяет формат запросов (think=false, format=JSON Schema, temperature=0, tools, сообщения с tool_calls
и ответами инструментов) и разбор ответов — без скачивания моделей. Качество модели здесь НЕ проверяется.
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from study_radar.agent.ask import Asker
from study_radar.agent.graph import AgentRuntime
from study_radar.index.embedder import HashEmbedder
from study_radar.simulate import Player

from conftest import msg

PHYS = -1001000000002
_hash = HashEmbedder()


class StubOllama(BaseHTTPRequestHandler):
    requests: list = []

    def log_message(self, *a):
        pass

    def _send(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send({"models": [{"name": "qwen3:8b", "model": "qwen3:8b"},
                                   {"name": "bge-m3:latest", "model": "bge-m3:latest"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        StubOllama.requests.append((self.path, body))
        if self.path == "/api/embed":
            inp = body["input"] if isinstance(body["input"], list) else [body["input"]]
            return self._send({"model": body["model"], "embeddings": _hash.embed(inp)})
        msgs = body["messages"]
        message = {"role": "assistant", "content": ""}
        if body.get("format"):
            noise = "говно" in msgs[-1]["content"]
            message["content"] = json.dumps({
                "is_important": not noise, "subject": None if noise else "physics",
                "category": "noise" if noise else "deadline", "importance": 1 if noise else 5,
                "title": "Шум" if noise else "ДЗ №3", "summary": "—", "due_text": None if noise else "до пятницы",
                "due_at": None if noise else "2026-10-30T23:59:00+03:00", "location": None,
                "related_deadline_id": None, "confidence": 0.9, "reason": "заглушка"}, ensure_ascii=False)
        elif body.get("tools"):
            if msgs[-1]["role"] == "tool":
                message["content"] = "Готово"
            else:
                user = msgs[-1]["content"]
                chat = int(re.search(r"source_chat_id: (-?\d+)", user).group(1))
                ids = json.loads(re.search(r"message_ids: (\[.*?\])", user).group(1))
                refs = json.loads(re.search(r"source_refs: (\[.*?\])", user).group(1))
                note = user.split("# Пометка (передай в note без изменений)\n", 1)[1].split("\n", 1)[0]
                message["tool_calls"] = [
                    {"function": {"name": "forward_to_topic", "arguments": {
                        "subject": "physics", "source_chat_id": chat, "message_ids": ids, "note": note}}},
                    {"function": {"name": "create_deadline", "arguments": {
                        "subject": "physics", "title": "ДЗ №3", "due_at": "2026-10-30T23:59:00+03:00",
                        "due_text": "до пятницы", "location": None, "source_refs": refs, "kind": "deadline"}}},
                    {"function": {"name": "refresh_deadlines_board", "arguments": {}}}]
        else:
            message["content"] = "ДЗ №3 по физике нужно сдать до пт 30.10, 23:59 [1]."
        self._send({"model": body["model"], "created_at": "2026-10-30T12:00:00Z", "message": message,
                    "done": True, "done_reason": "stop", "eval_count": 10, "eval_duration": 10**9})


@pytest.fixture()
def ollama_stub():
    StubOllama.requests = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), StubOllama)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


async def test_real_clients_against_ollama_api(settings, ollama_stub):
    from study_radar.services import Services

    settings.llm.backend = "ollama"
    settings.embeddings.backend = "ollama"
    settings.llm.host = ollama_stub
    sv = Services(settings, "fake")
    async with AgentRuntime(settings, "fake", sv) as rt:
        player = Player(rt)
        await player.feed(msg(settings, PHYS, 1, "ДЗ №3 сдать до пятницы, в 23:59 закрываю приём", "Орлов Д. В."))
        await player.feed(msg(settings, PHYS, 2, "физика — говно", "Кирилл", "2026-10-26T11:00"))
        await player.flush_all()
        st = player.results[0]

    # агент отработал через настоящие клиенты: LLM-вызовы инструментов без фолбэка
    assert st["route"] == "act" and not st["fallback"]
    assert [c["tool"] for c in st["tool_calls"] if c["by"] == "llm"] == [
        "forward_to_topic", "create_deadline", "refresh_deadlines_board"]
    assert all(c["ok"] for c in st["tool_calls"])
    assert len(sv.db.list_deadlines()) == 1
    assert player.results[1]["route"] == "skip"

    chats = [b for p, b in StubOllama.requests if p == "/api/chat"]
    cls_req = next(b for b in chats if b.get("format"))
    assert cls_req["think"] is False and cls_req["options"]["temperature"] == 0
    assert cls_req["format"]["properties"]["category"]["enum"][0] == "deadline"
    assert cls_req["messages"][0]["role"] == "system" and "Справочник предметов" in cls_req["messages"][0]["content"]
    act_reqs = [b for b in chats if b.get("tools")]
    tool_names = {t["function"]["name"] for t in act_reqs[0]["tools"]}
    assert tool_names == set(settings.agent.act_tools)
    follow = act_reqs[1]["messages"]       # второй шаг ReAct: модель видит свои вызовы и ответы инструментов
    assert any(m["role"] == "assistant" and m.get("tool_calls") for m in follow)
    assert any(m["role"] == "tool" and m.get("tool_name") == "create_deadline" for m in follow)
    assert any(p == "/api/embed" for p, _ in StubOllama.requests)

    res = await Asker(sv, sv.llm).ask("когда сдавать ДЗ №3 по физике?")
    assert "[1]" in res.answer and res.sources


def test_doctor_sees_models(settings, ollama_stub):
    from study_radar.doctor import ollama_status

    settings.llm.host = ollama_stub
    st = ollama_status(settings)
    assert st["reachable"] and st["has"]("qwen3:8b") and st["has"]("bge-m3") and not st["has"]("qwen3:4b")
