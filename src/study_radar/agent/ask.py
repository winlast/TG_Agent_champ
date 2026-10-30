"""Модуль 2 — RAG-пайплайн ответов на вопросы по переписке.

вопрос → гибридный поиск (сообщения + дедлайны) → обогащённый промпт (вопрос + контекст +
инструкции + требование ссылок) → LLM → ответ со ссылками [n] на исходные сообщения.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from study_radar.agent.classify import load_prompt
from study_radar.agent.context import guess_subject
from study_radar.agent.dates import fmt_due, human_date
from study_radar.llm.client import LLMClient
from study_radar.services import Services

log = logging.getLogger(__name__)

NO_DATA = "В переписке нет информации об этом."


@dataclass
class AskResult:
    question: str
    answer: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    prompt: str = ""
    ms: int = 0

    def format(self, html_links: bool = False) -> str:
        if not self.sources:
            return self.answer
        lines = [self.answer, "", "Источники:"]
        for s in self.sources:
            label = f"[{s['n']}] {s['label']}"
            if s.get("link"):
                label = f'<a href="{s["link"]}">{label}</a>' if html_links else f"{label} — {s['link']}"
            lines.append(label)
        return "\n".join(lines)


class Asker:
    def __init__(self, sv: Services, llm: LLMClient, k: int | None = None, min_similarity: float = 0.45):
        self.sv = sv
        self.llm = llm
        self.k = k or sv.s.retrieval.k
        self.min_similarity = min_similarity
        self.system = load_prompt("ask.md")

    def retrieve(self, question: str) -> list[dict[str, Any]]:
        r = self.sv.retriever
        emb = self.sv.embedder.embed_one(question)
        hits = r.search(question, k=self.k, only_important=True, query_embedding=emb)
        seen = {h.id for h in hits}
        hits += [h for h in r.search(question, k=3, query_embedding=emb) if h.id not in seen]
        # отсекаем совсем нерелевантное: нет общих слов (BM25) и низкая векторная близость
        hits = [h for h in hits if h.bm25_rank is not None or (h.similarity or 0) >= self.min_similarity]
        sources: list[dict[str, Any]] = []
        for h in hits[: self.k]:
            dt = datetime.fromisoformat(h.meta["date"])
            sources.append({
                "kind": "message", "text": h.text[:500], "link": h.meta.get("link"), "date": h.meta["date"],
                "label": f"{h.meta['chat_title']}, {fmt_due(dt)}, {h.meta['author']}",
                "header": f"Сообщение · чат «{h.meta['chat_title']}»"
                          + (f", топик «{h.meta['topic']}»" if h.meta.get('topic') else "")
                          + f" · {human_date(dt)} · {h.meta['author']}",
            })
        subject = guess_subject(self.sv.s, question)
        for d, sim in r.search_deadlines(question, k=4, subject=subject, query_embedding=emb):
            if sim < self.min_similarity and subject and d.subject != subject:
                continue
            subj = self.sv.s.subjects[d.subject].name if d.subject in self.sv.s.subjects else "—"
            extra = f"; было: {fmt_due(d.prev_due_at)}" if d.prev_due_at else ""
            loc = f"; место: {d.location}" if d.location else ""
            link = d.sources[-1]["link"] if d.sources else None
            sources.append({
                "kind": "deadline", "link": link, "date": d.due_at.isoformat() if d.due_at else None,
                "text": f"{d.title} ({subj}) — срок {fmt_due(d.due_at)}{loc}{extra}; источников: {len(d.sources)}",
                "label": f"дедлайн «{d.title}» ({subj}) из базы агента",
                "header": "Дедлайн из базы агента (актуальное состояние после всех переносов)",
            })
        for i, s in enumerate(sources, 1):
            s["n"] = i
        return sources

    async def ask(self, question: str) -> AskResult:
        t0 = time.perf_counter()
        question = question.strip()
        sources = self.retrieve(question) if question else []
        now = self.sv.db.now()
        ctx = "\n\n".join(f"[{s['n']}] {s['header']}\n{s['text']}" for s in sources) or "(ничего не найдено)"
        user = (f"Сегодня: {human_date(now)}.\n\n# Контекст из переписки\n{ctx}\n\n# Вопрос\n{question}\n\n"
                f"Ответь по правилам, со ссылками [n].")
        if not sources:
            answer = NO_DATA
        else:
            answer = (await self.llm.text(self.system, user, hint={"sources": sources})).strip() or NO_DATA
            if not re.search(r"\[\d+\]", answer) and "нет информации" not in answer.lower():
                answer += " " + " ".join(f"[{s['n']}]" for s in sources[:2])
        used = {int(n) for n in re.findall(r"\[(\d+)\]", answer)}
        shown = [s for s in sources if s["n"] in used] or ([] if "нет информации" in answer.lower() else sources[:3])
        ms = int((time.perf_counter() - t0) * 1000)
        res = AskResult(question, answer, shown, user, ms)
        self.sv.db.add_ask(question=question, answer=answer, sources=shown, prompt=user, backend=self.llm.name,
                           model=self.llm.model, latency_ms=ms)
        return res
