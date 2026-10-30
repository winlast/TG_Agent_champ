"""Клиенты LLM.

* OllamaLLM — настоящая локальная модель через Ollama (qwen3:8b / qwen3:4b), think=False, temperature=0.
* FakeLLM   — детерминированные правила, ТОЛЬКО для тестов и проверки кода без Ollama.
  Его результаты никогда не выдаются за работу модели (в UI и отчётах — явная пометка).

Аргумент ``hint`` — структурированный контекст, который видит только FakeLLM (настоящая модель
получает только текст промпта).
"""

from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

_THINK = re.compile(r"<think>.*?</think>", re.S)


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass
class LLMReply:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    ms: int = 0


class LLMClient(ABC):
    name: str = "llm"
    model: str = ""
    is_fake: bool = False

    @abstractmethod
    async def json(self, system: str, user: str, schema: dict[str, Any], *, hint: Any = None) -> str:
        """Структурированный вывод: вернуть строку JSON, соответствующую schema."""

    @abstractmethod
    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *,
                   hint: Any = None) -> LLMReply:
        """Чат с инструментами (tool calling)."""

    @abstractmethod
    async def text(self, system: str, user: str, *, hint: Any = None) -> str:
        ...

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}" if self.model else self.name


class OllamaLLM(LLMClient):
    name = "ollama"

    def __init__(self, model: str, host: str = "http://localhost:11434", temperature: float = 0.0,
                 think: bool = False, num_ctx: int = 8192, timeout_s: float = 900, keep_alive: str = "5m"):
        import ollama

        self.model = model
        self.client = ollama.AsyncClient(host=host, timeout=timeout_s)
        self.options = {"temperature": temperature, "num_ctx": num_ctx}
        self.think = think
        self.keep_alive = keep_alive
        self._think_supported = True

    async def _chat(self, **kw: Any):
        if self._think_supported:
            try:
                return await self.client.chat(model=self.model, think=self.think, options=self.options,
                                              keep_alive=self.keep_alive, **kw)
            except Exception as e:  # модели без поддержки think
                if "think" not in str(e).lower():
                    raise
                log.info("Модель %s не поддерживает параметр think — отключаю его", self.model)
                self._think_supported = False
        return await self.client.chat(model=self.model, options=self.options, keep_alive=self.keep_alive, **kw)

    @staticmethod
    def _clean(text: str | None) -> str:
        return _THINK.sub("", text or "").strip()

    async def json(self, system: str, user: str, schema: dict[str, Any], *, hint: Any = None) -> str:
        resp = await self._chat(messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                                format=schema)
        return self._clean(resp.message.content)

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *,
                   hint: Any = None) -> LLMReply:
        t0 = time.perf_counter()
        resp = await self._chat(messages=messages, tools=tools or None)
        calls = []
        for tc in resp.message.tool_calls or []:
            args = tc.function.arguments
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            calls.append(ToolCall(name=tc.function.name, args=dict(args or {})))
        return LLMReply(content=self._clean(resp.message.content), tool_calls=calls,
                        ms=int((time.perf_counter() - t0) * 1000))

    async def text(self, system: str, user: str, *, hint: Any = None) -> str:
        resp = await self._chat(messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return self._clean(resp.message.content)


class FakeLLM(LLMClient):
    """Детерминированная «модель» на правилах. ТОЛЬКО для тестов: проверяет код, а не качество.

    Классифицирует по ключевым словам и структурированному контексту (hint), в act-режиме
    вызывает инструменты строго по плану (hint["plan"]). Режим broken=True имитирует маленькую
    модель, которая ошибается в вызовах, — чтобы тестировать фолбэк.
    """

    name = "fake"
    is_fake = True

    NOISE = re.compile(r"говн|скучн|ахах|хаха|лол|кек|мем|спасибо|спс|благодар|ура|желаем|ждем|ждём|"
                       r"кто на|физр|физкульт|бесит|ненавиж|петруш|pollos|победу дают|😂|🤣|👍")
    UPDATE = re.compile(r"перенес|перенос|сдвинул|сдвига|вместо|изменил|поменял|уточн|теперь|будет проходить|"
                        r"нельзя|не в \w+, а")
    CANCEL = re.compile(r"отмен|не будет")
    DEADLINE = re.compile(r"дедлайн|сдать|сдача|сдаём|сдаем|срок|до \w+|прислать|загрузить|отправить")
    EVENT = re.compile(r"защит|рк\b|рубежн|консультац|экзамен|зач[её]т|чемпионат|коллоквиум|аудитор")
    ASSIGN = re.compile(r"задани|дз\b|домашн|лабораторн|лаб[уаы]?\b|вариант|типов")
    ANNOUNCE = re.compile(r"внимание|важно|объявлен|правил|требовани|ссылк|https?://|расписани|формат")

    def __init__(self, settings: Any = None, broken: bool = False):
        self.settings = settings
        self.model = "rules"
        self.broken = broken

    # ------------------------------------------------------------------ classify
    def _subject(self, text: str) -> str | None:
        if self.settings is None:
            return None
        low = text.lower()
        for key, subj in self.settings.subjects.items():
            for alias in subj.aliases:
                a = alias.lower()
                if re.search(rf"(?<!\w){re.escape(a)}", low) and not any(n.lower() in low for n in subj.not_aliases
                                                                          if a in n.lower()):
                    return key
        return None

    def classify(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from datetime import datetime

        from study_radar.agent.dates import default_time_for, parse_due

        post = ctx["post"]
        text = post["text"]
        low = text.lower()
        reply = (ctx.get("reply") or {}).get("text") or ""
        base = datetime.fromisoformat(post["date"])
        cands = ctx.get("candidates") or []
        subject = (self._subject(text) or self._subject(reply) or self._subject(post.get("topic") or "")
                   or self._subject(post.get("chat_title") or "") or ctx.get("subject_guess"))
        trusted = bool(post.get("trusted"))
        result: dict[str, Any] = {
            "is_important": False, "subject": subject, "category": "noise", "importance": 1, "title": "Шум",
            "summary": "Не содержит учебной информации.", "due_text": None, "due_at": None, "location": None,
            "related_deadline_id": None, "confidence": 0.9, "reason": "FakeLLM: нет учебных маркеров",
        }
        if self.NOISE.search(low) and not (self.UPDATE.search(low) or self.CANCEL.search(low)) and len(low) < 120:
            result["reason"] = "FakeLLM: шумовые маркеры"
            return result
        if low.startswith("[файл]") or "[файл]" in low:
            result.update(is_important=True, category="materials", importance=2, title=text.split("]", 1)[-1].strip()[:60],
                          summary="Учебные материалы.", reason="FakeLLM: файл", confidence=0.7)
            return result

        same_post = [c for c in cands if c.get("from_this_post")]
        related = (same_post or [c for c in cands if not subject or c.get("subject") in (None, subject)] or [None])[0]
        kind = None
        if self.CANCEL.search(low):
            kind = "cancellation"
        elif (self.UPDATE.search(low) or (post.get("edited") and same_post)) and related:
            kind = "deadline_update"
        elif self.EVENT.search(low):
            kind = "event"
        elif self.DEADLINE.search(low) and (self.ASSIGN.search(low) or subject):
            kind = "deadline"
        elif self.ASSIGN.search(low):
            kind = "assignment"
        elif self.ANNOUNCE.search(low) or (trusted and len(low) > 40):
            kind = "announcement"
        if kind is None:
            return result

        dp = parse_due(text, base, default_time=default_time_for(kind))
        if kind == "deadline_update" and related is not None and related.get("subject"):
            subject = subject or related["subject"]
        loc = re.search(r"(ауд(?:итори[яиюе])?\.?\s*№?\s*[\w-]*\d+\w*)", text, re.I)
        title = re.sub(r"\s+", " ", text.split("\n")[0])[:50]
        if related is not None and kind in {"deadline_update", "cancellation"}:
            title = related["title"]
        rel_id = related["id"] if (related is not None and kind in {"deadline_update", "cancellation"}) else None
        if kind in {"deadline", "event"} and related is not None and dp.dt is not None and related.get("due_at"):
            if datetime.fromisoformat(related["due_at"]).date() == dp.dt.date() and related.get("subject") == subject:
                rel_id = related["id"]   # тот же дедлайн из другого чата
        result.update(
            is_important=True, subject=subject, category=kind, importance=4 if trusted else 3, title=title,
            summary=text[:120], due_text=dp.matched or None, due_at=dp.dt.isoformat() if dp.dt else None,
            location=loc.group(1) if loc else None, related_deadline_id=rel_id,
            confidence=0.85 if subject else 0.45, reason=f"FakeLLM: правило «{kind}»",
        )
        return result

    async def json(self, system: str, user: str, schema: dict[str, Any], *, hint: Any = None) -> str:
        if isinstance(hint, dict) and "post" in hint:
            return json.dumps(self.classify(hint), ensure_ascii=False)
        return json.dumps({k: None for k in schema.get("properties", {})})

    # ------------------------------------------------------------------ act
    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *,
                   hint: Any = None) -> LLMReply:
        if messages and messages[-1].get("role") == "tool":
            return LLMReply(content="Готово.")
        plan = (hint or {}).get("plan") or []
        if self.broken:
            return LLMReply(content="", tool_calls=[ToolCall("forward_to_topic", {"subject": "???"}),
                                                    ToolCall("make_coffee", {})])
        return LLMReply(content="", tool_calls=[ToolCall(c["tool"], dict(c["args"])) for c in plan])

    # ------------------------------------------------------------------ ask
    async def text(self, system: str, user: str, *, hint: Any = None) -> str:
        sources = (hint or {}).get("sources") or []
        if not sources:
            return "В переписке нет информации об этом."
        first = sources[0]
        return f"По данным переписки: {first['text'][:200]} [1]"


def make_llm(settings) -> LLMClient:
    cfg = settings.llm
    if cfg.backend == "fake":
        return FakeLLM(settings)
    if cfg.backend == "ollama":
        return OllamaLLM(model=cfg.model, host=cfg.host, temperature=cfg.temperature, think=cfg.think,
                         num_ctx=cfg.num_ctx, timeout_s=cfg.timeout_s, keep_alive=cfg.keep_alive)
    raise ValueError(f"Неизвестный backend LLM: {cfg.backend}")
