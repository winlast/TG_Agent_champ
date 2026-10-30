"""LLM-классификация: строгий JSON (Ollama format=<JSON Schema>, temperature=0) + валидация кодом."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from study_radar.agent.context import render_user_prompt, subject_mentioned
from study_radar.agent.dates import default_time_for, fmt_due, parse_due, same_day
from study_radar.config import ROOT, Settings
from study_radar.llm.client import LLMClient
from study_radar.models import Category, Classification, classification_json_schema

log = logging.getLogger(__name__)
PROMPTS = ROOT / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def subjects_block(settings: Settings) -> str:
    return "\n".join(f"- {s.describe()}" for s in settings.subjects.values())


@dataclass
class ClassifyResult:
    classification: Classification
    raw: str
    system: str
    user: str
    notes: list[str] = field(default_factory=list)
    route: str = "act"          # skip | act | review
    llm_ms: int = 0


def _extract_json(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            return json.loads(m.group(0))
        raise


class Classifier:
    def __init__(self, settings: Settings, llm: LLMClient):
        self.s = settings
        self.llm = llm
        self.system = load_prompt("classify.md").replace("{{subjects}}", subjects_block(settings))
        self.schema = classification_json_schema()

    async def classify(self, ctx: dict[str, Any]) -> ClassifyResult:
        import time

        user = render_user_prompt(ctx)
        t0 = time.perf_counter()
        raw = await self.llm.json(self.system, user, self.schema, hint=ctx)
        notes: list[str] = []
        try:
            cls = Classification.model_validate(_extract_json(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            log.warning("LLM вернула невалидный JSON, повторяю: %s", e)
            notes.append("первый ответ LLM — невалидный JSON, повтор")
            raw = await self.llm.json(self.system, user + "\n\nВерни строго валидный JSON по схеме.", self.schema,
                                      hint=ctx)
            try:
                cls = Classification.model_validate(_extract_json(raw))
            except (json.JSONDecodeError, ValidationError) as e2:
                notes.append(f"LLM не смогла вернуть JSON: {e2}")
                cls = Classification(is_important=True, subject=None, category=Category.ANNOUNCEMENT, importance=2,
                                     title="Не удалось разобрать", summary=ctx["post"]["text"][:120], confidence=0.1,
                                     reason="Ошибка разбора ответа LLM — на ручную проверку")
        ms = int((time.perf_counter() - t0) * 1000)
        cls, more, route = validate(cls, ctx, self.s)
        return ClassifyResult(cls, raw, self.system, user, notes + more, route, ms)


def validate(cls: Classification, ctx: dict[str, Any], s: Settings) -> tuple[Classification, list[str], str]:
    """Валидация решения LLM кодом. Возвращает (исправленная классификация, заметки, маршрут)."""
    notes: list[str] = []
    c = cls.model_copy(deep=True)
    post = ctx["post"]
    base = datetime.fromisoformat(post["date"])
    text_low = post["text"].lower()

    # 1. согласованность важности и категории
    if c.category == Category.NOISE and c.is_important:
        c.is_important = False
        notes.append("category=noise → is_important=false")
    if not c.is_important and c.category != Category.NOISE:
        notes.append(f"is_important=false при категории {c.category.value} → noise")
        c.category = Category.NOISE
    if not c.is_important:
        c.due_at = c.due_text = c.location = None
        c.related_deadline_id = None
        return c, notes, "skip"

    # 2. предмет — только из справочника
    if c.subject is not None and c.subject not in s.subjects:
        by_name = {v.name.lower(): k for k, v in s.subjects.items()}
        if c.subject.lower() in by_name:
            c.subject = by_name[c.subject.lower()]
        else:
            notes.append(f"предмета «{c.subject}» нет в справочнике → null, на проверку")
            c.subject = None
            c.confidence = min(c.confidence, s.classify.review_confidence - 0.01)
    if c.subject in s.subjects:
        subj = s.subjects[c.subject]
        hit_alias = subject_mentioned(subj, post["text"])
        hit_not = any(n.lower() in text_low for n in subj.not_aliases)
        if hit_not and not hit_alias:
            notes.append(f"в тексте «{', '.join(subj.not_aliases)}», а не {subj.name} → предмет сброшен")
            c.subject = None
            c.confidence = min(c.confidence, 0.4)

    # 3. related_deadline_id — только из известных кандидатов
    cand_ids = {x["id"] for x in ctx.get("candidates") or []}
    if c.related_deadline_id is not None and c.related_deadline_id not in cand_ids:
        notes.append(f"related_deadline_id={c.related_deadline_id} не из кандидатов → null")
        c.related_deadline_id = None
    if c.category in {Category.DEADLINE_UPDATE, Category.CANCELLATION} and c.related_deadline_id is None:
        own = [x for x in ctx.get("candidates") or [] if x.get("from_this_post")]
        if own and c.category == Category.DEADLINE_UPDATE:
            c.related_deadline_id = own[0]["id"]
            notes.append(f"редактирование своего сообщения → related_deadline_id={own[0]['id']}")
    if c.category == Category.DEADLINE_UPDATE and c.related_deadline_id is None:
        notes.append("перенос без известного дедлайна → будет создан новый дедлайн")
        c.category = Category.DEADLINE if c.due_at else Category.ANNOUNCEMENT
    if c.related_deadline_id is not None and c.subject is None:
        cand = next(x for x in ctx["candidates"] if x["id"] == c.related_deadline_id)
        c.subject = cand["subject"]

    # 4. дата: окно допустимых значений + перепроверка по due_text (dateparser / правила)
    kind = c.category.value
    if c.due_at is not None:
        if c.due_at.tzinfo is None:
            c.due_at = c.due_at.replace(tzinfo=s.tz)
        c.due_at = c.due_at.astimezone(s.tz)
        lo, hi = base - timedelta(days=s.classify.max_days_past), base + timedelta(days=s.classify.max_days_future)
        if not (lo <= c.due_at <= hi):
            notes.append(f"дата {c.due_at:%d.%m.%Y} вне окна → сброшена")
            c.due_at = None
            c.confidence *= 0.8
    if c.due_text:
        parsed = parse_due(c.due_text, base, s.tz, default_time_for(kind))
        if not parsed.ok:
            parsed = parse_due(post["text"], base, s.tz, default_time_for(kind)) if c.due_at is None else parsed
        if parsed.ok:
            if c.due_at is None:
                c.due_at = parsed.dt
                notes.append(f"дата вычислена кодом по «{c.due_text}»: {fmt_due(parsed.dt)}")
            elif not same_day(c.due_at, parsed.dt):
                c.confidence = round(c.confidence * 0.75, 3)
                if parsed.exact:
                    notes.append(f"расхождение дат: LLM {fmt_due(c.due_at)}, по «{c.due_text}» — {fmt_due(parsed.dt)}; "
                                 f"беру вычисленную кодом, confidence понижена")
                    c.due_at = parsed.dt if parsed.has_time else parsed.dt.replace(hour=c.due_at.hour,
                                                                                   minute=c.due_at.minute)
                else:
                    notes.append(f"расхождение дат: LLM {fmt_due(c.due_at)}, по «{c.due_text}» — {fmt_due(parsed.dt)}; "
                                 f"оставляю дату LLM, confidence понижена")
    explicit_time = bool(re.search(r"\d{1,2}[:.]\d{2}", f"{c.due_text or ''} {post['text']}"))
    if c.category == Category.DEADLINE_UPDATE and c.related_deadline_id and c.due_at is not None and not explicit_time:
        cand = next(x for x in ctx["candidates"] if x["id"] == c.related_deadline_id)
        if cand["kind"] == "event":
            c.due_at = c.due_at.replace(hour=0, minute=0)
            notes.append("перенос события без указания времени → время неизвестно")
        elif cand.get("due_at"):
            old = datetime.fromisoformat(cand["due_at"])
            c.due_at = c.due_at.replace(hour=old.hour, minute=old.minute)
    if c.due_at is not None and c.category in {Category.DEADLINE, Category.ASSIGNMENT} and \
            (c.due_at.hour, c.due_at.minute) == (0, 0) and not re.search(r"\d:\d\d|\d\.\d\d", c.due_text or ""):
        c.due_at = c.due_at.replace(hour=23, minute=59)
        notes.append("время не указано → 23:59 для дедлайна")

    # 5. доверенный автор повышает приоритет (но не меняет смысл)
    if post.get("trusted") and c.importance < 5:
        c.importance += 1
        notes.append("доверенный автор → важность +1")

    route = "review" if c.confidence < s.classify.review_confidence else "act"
    if route == "review":
        notes.append(f"уверенность {c.confidence:.2f} < {s.classify.review_confidence} → «🤔 На проверку»")
    return c, notes, route
