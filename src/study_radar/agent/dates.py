"""Разбор сроков в свободной форме. Относительные даты считаются ОТ ДАТЫ СООБЩЕНИЯ.

Сначала — собственные правила для типичных учебных формулировок («до пятницы», «завтра»,
«30 октября (пятница) с 15:55», «через 1,5 недели», «05.11 23:59»), затем — dateparser
(RELATIVE_BASE = дата сообщения, язык ru).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
MONTH_RE = r"(январ[яь]|феврал[яь]|марта?|апрел[яь]|ма[яй]|июн[яь]|июл[яь]|августа?|сентябр[яь]|октябр[яь]|ноябр[яь]|декабр[яь])"
WEEKDAYS = [
    (r"понедельник\w*", "пн"), (r"вторник\w*", "вт"), (r"сред[ауые]\b", "ср"), (r"четверг\w*", "чт"),
    (r"пятниц[аеуы]\b", "пт"), (r"суббот[аеуы]\b", "сб"), (r"воскресень[еяю]\b", "вс"),
]
WEEKDAY_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
WEEKDAY_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTH_GEN = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
             "октября", "ноября", "декабря"]

_TIME_RE = re.compile(r"(?<![\d.:])([01]?\d|2[0-3]):([0-5]\d)(?![\d.:])")
_TIME_DOT_RE = re.compile(r"\b(?:в|к|до|с|со)\s+([01]?\d|2[0-3])\.([0-5]\d)(?![\d.])")
_DATEISH_RE = re.compile(r"янв|фев|мар|апр|мая|май|июн|июл|авг|сен|окт|ноя|дек|недел|месяц|числ|\d{1,2}\.\d{1,2}")
_TIME_H_RE = re.compile(r"\b(?:в|к|до|с)\s+([01]?\d|2[0-3])\s*(?:ч\b|час|утра|вечера|дня)")
_DM_RE = re.compile(r"(?<![\d:])(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?(?![\d:])")
_D_MONTH_RE = re.compile(r"(?<!\d)(\d{1,2})(?:-?го)?\s+" + MONTH_RE)
_ONLY_DAY_RE = re.compile(r"\b(?:до|к|на)\s+(\d{1,2})(?:-?го|-?е)?\b(?!\s*[:.]|\s*(?:час|ч\b|мин|дн|нед|лаб|задан|вариант|пар|балл|челов|задач|вопрос|раз|%))")
_IN_N_RE = re.compile(r"через\s+(\d+(?:[.,]\d+)?|пару|неделю|день|две|три)\s*(дн\w*|недел\w*|ден\w*)?")
_N_WEEKS_RE = re.compile(r"(\d+(?:[.,]\d+)?|полторы)\s+недел\w*\s+(?:до|на)")


@dataclass
class DueParse:
    dt: datetime | None
    has_time: bool = False
    exact: bool = False          # явная дата/день недели — правило уверено
    matched: str = ""

    @property
    def ok(self) -> bool:
        return self.dt is not None


def _year_for(month: int, day: int, base: datetime) -> int:
    """Без года: ближайшая дата не раньше чем ~2 месяца назад от base."""
    year = base.year
    try:
        cand = base.replace(year=year, month=month, day=day)
    except ValueError:
        return year
    if cand < base - timedelta(days=60):
        return year + 1
    return year


def _dot_time_spans(text: str) -> list[tuple[int, int, time]]:
    """«в 16.00», «к 10.15» — время через точку (если в тексте нет времени через двоеточие)."""
    if _TIME_RE.search(text):
        return []
    out = []
    for m in _TIME_DOT_RE.finditer(text):
        h, mi = int(m.group(1)), int(m.group(2))
        if mi > 12 or mi % 5 == 0 and 8 <= h <= 21 and mi in {0, 10, 15, 20, 30, 40, 45, 50, 55}:
            out.append((m.start(1), m.end(2), time(h, mi)))
    return out


def _find_time(text: str) -> time | None:
    m = _TIME_RE.search(text)
    if m:
        return time(int(m.group(1)), int(m.group(2)))
    spans = _dot_time_spans(text)
    if spans:
        return spans[0][2]
    m = _TIME_H_RE.search(text)
    if m:
        h = int(m.group(1))
        if "вечера" in m.group(0) and h < 12:
            h += 12
        return time(h, 0)
    return None


def _valid_dm(text: str) -> list[re.Match]:
    time_spans = [(a, b) for a, b, _ in _dot_time_spans(text)]
    out = []
    for m in _DM_RE.finditer(text):
        d, mo = int(m.group(1)), int(m.group(2))
        if not (1 <= d <= 31 and 1 <= mo <= 12):
            continue
        if any(a <= m.start() < b for a, b in time_spans):
            continue
        out.append(m)
    return out


_TARGET_PREFIX = re.compile(r"(?:\bна|\bдо|\bк|\bко|\bв|\bво|\bпо)\s+(?:\w+\s+)?$")


def _pick(matches: list[re.Match], text: str) -> re.Match | None:
    """Из нескольких дат выбрать «целевую»: «перенесли с 30.10 на 02.11» → 02.11."""
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    targeted = [m for m in matches if _TARGET_PREFIX.search(text[max(0, m.start() - 20):m.start()])]
    if targeted:
        return targeted[-1]
    return matches[0]


def _weekday_in(text: str) -> tuple[int, str] | None:
    found: list[tuple[re.Match, int]] = []
    for idx, (pattern, short) in enumerate(WEEKDAYS):
        for m in re.finditer(rf"\b(?:{pattern})|\b{short}\b", text):
            found.append((m, idx))
    if not found:
        return None
    found.sort(key=lambda x: x[0].start())
    best = _pick([m for m, _ in found], text)
    idx = next(i for m, i in found if m is best)
    return idx, best.group(0)


def parse_due(text: str | None, base: datetime, tz: ZoneInfo | None = None,
              default_time: time | None = None) -> DueParse:
    """Возвращает дату срока, вычисленную от base (дата сообщения)."""
    if not text:
        return DueParse(None)
    tz = tz or base.tzinfo  # type: ignore[assignment]
    base = base.astimezone(tz) if base.tzinfo else base.replace(tzinfo=tz)
    t = text.lower().replace("ё", "е")
    tm = _find_time(t)
    day: datetime | None = None
    exact = False
    matched = ""

    if m := _pick(list(_D_MONTH_RE.finditer(t)), t):
        d = int(m.group(1))
        month = next(v for k, v in MONTHS.items() if m.group(2).startswith(k))
        try:
            day = base.replace(year=_year_for(month, d, base), month=month, day=d)
            exact, matched = True, m.group(0)
        except ValueError:
            day = None
    if day is None and (m := _pick(_valid_dm(t), t)):
        d, mo = int(m.group(1)), int(m.group(2))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            y = m.group(3)
            year = (2000 + int(y) if y and len(y) == 2 else int(y)) if y else _year_for(mo, d, base)
            try:
                day = base.replace(year=year, month=mo, day=d)
                exact, matched = True, m.group(0)
            except ValueError:
                day = None
    if day is None:
        if re.search(r"\bпослезавтра\b", t):
            day, exact, matched = base + timedelta(days=2), True, "послезавтра"
        elif re.search(r"\bзавтра\b", t):
            day, exact, matched = base + timedelta(days=1), True, "завтра"
        elif re.search(r"\bсегодня\b|\bсейчас\b", t):
            day, exact, matched = base, True, "сегодня"
    if day is None and (wd := _weekday_in(t)):
        idx, word = wd
        delta = (idx - base.weekday()) % 7
        if delta == 0:
            delta = 7
        if re.search(r"следующ\w*\s+" + re.escape(word), t) and delta < 7 and base.weekday() < idx:
            # «в следующую среду», сказанное в понедельник, — обычно среда следующей недели
            delta += 7
        day, exact, matched = base + timedelta(days=delta), True, word
    if day is None and (m := _IN_N_RE.search(t)):
        num_raw, unit = m.group(1), (m.group(2) or "")
        words = {"пару": 2, "две": 2, "три": 3, "неделю": 1, "день": 1}
        n = words.get(num_raw) or float(num_raw.replace(",", "."))
        if num_raw == "неделю" or unit.startswith("недел"):
            day = base + timedelta(days=round(7 * n))
        else:
            day = base + timedelta(days=round(n))
        matched = m.group(0)
    if day is None and (m := _N_WEEKS_RE.search(t)):
        n = 1.5 if m.group(1) == "полторы" else float(m.group(1).replace(",", "."))
        day, matched = base + timedelta(days=round(7 * n)), m.group(0)
    if day is None and re.search(r"на этой неделе|до конца недели", t):
        day = base + timedelta(days=6 - base.weekday())
        matched = "на этой неделе"
    if day is None and re.search(r"на следующей неделе", t):
        day = base + timedelta(days=7 - base.weekday() + 4)  # пятница следующей недели
        matched = "на следующей неделе"
    if day is None and (m := _ONLY_DAY_RE.search(t)):
        d = int(m.group(1))
        if 1 <= d <= 31:
            month, year = base.month, base.year
            if d < base.day:
                month += 1
                if month > 12:
                    month, year = 1, year + 1
            try:
                day, exact, matched = base.replace(year=year, month=month, day=d), True, m.group(0)
            except ValueError:
                day = None
    if day is None and tm is None and _DATEISH_RE.search(t):
        day = _dateparser_fallback(text, base, tz)
        if day is not None and not (base - timedelta(days=60) <= day <= base + timedelta(days=240)):
            day = None
        if day is not None:
            has_time = not (day.hour == 0 and day.minute == 0)
            return DueParse(day, has_time=has_time, exact=False, matched="dateparser")
    if day is None and tm is not None:
        # только время («к 16:00») — сегодня, если ещё не прошло, иначе завтра
        day = base
        if datetime.combine(base.date(), tm, tzinfo=tz) <= base:
            day = base + timedelta(days=1)
        matched = "время"
    if day is None:
        return DueParse(None)

    use_time = tm or default_time
    if use_time is None:
        return DueParse(datetime.combine(day.date(), time(0, 0), tzinfo=tz), has_time=False, exact=exact,
                        matched=matched)
    return DueParse(datetime.combine(day.date(), use_time, tzinfo=tz), has_time=tm is not None, exact=exact,
                    matched=matched)


def _dateparser_fallback(text: str, base: datetime, tz) -> datetime | None:
    try:
        import dateparser
        from dateparser.search import search_dates
    except ImportError:  # pragma: no cover
        return None
    settings = {
        "RELATIVE_BASE": base.replace(tzinfo=None),
        "PREFER_DATES_FROM": "future",
        "TIMEZONE": str(tz),
        "RETURN_AS_TIMEZONE_AWARE": True,
        "DATE_ORDER": "DMY",
    }
    cleaned = re.sub(r"\b(до|к|ко|в|во|на|с|со)\s+", " ", text.lower())
    dt = dateparser.parse(cleaned, languages=["ru"], settings=settings)
    if dt is None:
        try:
            found = search_dates(text, languages=["ru"], settings=settings)
        except Exception:  # dateparser иногда падает на экзотике
            found = None
        if found:
            dt = found[0][1]
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def default_time_for(kind: str) -> time | None:
    """Время не указано: для дедлайна — 23:59, для события — неизвестно (00:00 = «время не указано»)."""
    return time(23, 59) if kind in {"deadline", "assignment", "deadline_update"} else None


def fmt_due(dt: datetime | None, with_weekday: bool = True) -> str:
    """«чт 05.11, 23:59» или «чт 05.11» если время не указано (00:00)."""
    if dt is None:
        return "без срока"
    wd = WEEKDAY_SHORT[dt.weekday()] + " " if with_weekday else ""
    s = f"{wd}{dt:%d.%m}"
    if not (dt.hour == 0 and dt.minute == 0):
        s += f", {dt:%H:%M}"
    return s


def human_date(dt: datetime) -> str:
    """«пятница, 30 октября 2026, 16:19» — для промптов."""
    return f"{WEEKDAY_FULL[dt.weekday()]}, {dt.day} {MONTH_GEN[dt.month]} {dt.year}, {dt:%H:%M}"


def same_day(a: datetime | None, b: datetime | None) -> bool:
    return a is not None and b is not None and a.date() == b.date()
