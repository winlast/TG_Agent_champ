"""Префильтр — экономия ресурсов LLM, НЕ основа решения. Порог щедрый: лучше пропустить мусор
в LLM, чем потерять дедлайн.

Отбрасывает: пустые и служебные, только эмодзи/стикеры, короткие «спасибо / ок / +» без дат и чисел,
а также сообщения, далёкие по смыслу (косинус эмбеддингов) от учебных прототипов и справочника предметов.
Маркеры дат, времени, чисел и учебных слов всегда пропускают сообщение дальше.
"""

from __future__ import annotations

import re

from study_radar.config import Settings
from study_radar.index.embedder import Embedder, cosine
from study_radar.ingest.normalize import is_emoji_only
from study_radar.models import Post, PrefilterResult

PROTOTYPES = [
    "дедлайн по лабораторной работе",
    "срок сдачи домашнего задания",
    "перенос сдачи на другой день",
    "задание на дом к следующей паре",
    "аудитория и время защиты",
    "рубежный контроль в пятницу",
    "пары не будет, занятие отменяется",
    "консультация перед экзаменом",
    "требования к отчёту и оформлению",
    "выложены материалы лекции и методичка",
    "ссылка на задание и варианты",
    "кейс-чемпионат, выступление команд, регламент",
]

DATE_MARKERS = re.compile(
    r"\d|\b(сегодня|завтра|послезавтра|понедельник\w*|вторник\w*|сред[ауы]|четверг\w*|пятниц\w*|суббот\w*|"
    r"воскресень\w*|пн|вт|ср|чт|пт|сб|недел\w*|январ\w*|феврал\w*|март\w*|апрел\w*|ма[яй]|июн\w*|июл\w*|"
    r"август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)\b", re.I)
STUDY_MARKERS = re.compile(
    r"(лаб|\bдз\b|домашк|\bрк\b|\bкр\b|сдач|сдать|сдаём|сдаем|защит|дедлайн|\bдед\b|перенес|перенос|отмен|"
    r"аудитор|\bауд\b|задани|экзамен|зач[её]т|консультац|\bпар[аыу]\b|лекци|семинар|контрольн|отч[её]т|"
    r"вариант|курсов|\bсрок|не будет|методичк|презентац|чемпионат)", re.I)
THANKS = re.compile(r"^(спасибо|спс|сяб|благодарю|ок|окей|ok|ага|угу|да|нет|понял|поняла|ясно|ладно|"
                    r"класс|супер|круто|ура|\++|ахах\w*|хах\w*|лол|кек)[\s!.)(]*$", re.I)


class Prefilter:
    def __init__(self, settings: Settings, embedder: Embedder):
        self.s = settings
        self.embedder = embedder
        self._proto: list[tuple[str, list[float]]] | None = None

    def _prototypes(self) -> list[tuple[str, list[float]]]:
        if self._proto is None:
            labels = list(PROTOTYPES) + [f"{s.name}: {', '.join(s.aliases)}" for s in self.s.subjects.values()]
            self._proto = list(zip(labels, self.embedder.embed(labels)))
        return self._proto

    def _mentions_subject(self, text: str) -> str | None:
        low = text.lower()
        for s in self.s.subjects.values():
            for a in s.aliases + s.not_aliases:
                if re.search(rf"(?<!\w){re.escape(a.lower())}", low):
                    return a
        return None

    def check(self, post: Post, embedding: list[float] | None = None, force: bool = False) -> PrefilterResult:
        text = (post.text or "").strip()
        if force:
            return PrefilterResult(passed=True, score=1.0, reason="повторная проверка (редактирование)")
        if not text:
            return PrefilterResult(passed=False, reason="пустое сообщение")
        if is_emoji_only(text):
            return PrefilterResult(passed=False, reason="только эмодзи / стикер")
        words = re.findall(r"\w+", text)
        has_date = bool(DATE_MARKERS.search(text))
        study = STUDY_MARKERS.search(text)
        if len(words) < self.s.prefilter.min_words and THANKS.match(text) and not has_date:
            return PrefilterResult(passed=False, reason="короткая реакция («спасибо / ок / +»)")
        if has_date:
            return PrefilterResult(passed=True, score=1.0, reason="есть дата / время / число")
        if study:
            return PrefilterResult(passed=True, score=1.0, reason=f"учебный маркер «{study.group(0)}»")
        if text.startswith("[файл]"):
            return PrefilterResult(passed=True, score=1.0, reason="файл (возможно, материалы)")
        if self.s.is_trusted(post.author):
            return PrefilterResult(passed=True, score=1.0, reason="доверенный автор")
        alias = self._mentions_subject(text)
        if alias:
            return PrefilterResult(passed=True, score=1.0, reason=f"упоминается предмет («{alias}») — решит LLM")
        if len(words) < self.s.prefilter.min_words:
            return PrefilterResult(passed=False, reason="короткое сообщение без дат и учебных слов")
        emb = embedding or self.embedder.embed_one(text)
        best_label, best = max(((lbl, cosine(emb, v)) for lbl, v in self._prototypes()), key=lambda x: x[1])
        passed = best >= self.s.prefilter.threshold
        reason = (f"близко к «{best_label}» ({best:.2f})" if passed
                  else f"далеко от учебных тем (макс. {best:.2f} < {self.s.prefilter.threshold})")
        return PrefilterResult(passed=passed, score=round(best, 3), reason=reason, best_match=best_label)
