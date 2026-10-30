"""Сроки в свободной форме считаются от даты СООБЩЕНИЯ."""

from datetime import datetime

import pytest

from study_radar.agent.dates import default_time_for, fmt_due, parse_due


@pytest.fixture()
def base(settings):
    return datetime(2026, 10, 26, 10, 0, tzinfo=settings.tz)   # понедельник


@pytest.mark.parametrize("text,expected", [
    ("до пятницы", "пт 30.10, 23:59"),
    ("лабу перенесли на среду", "ср 28.10, 23:59"),
    ("30 октября (пятница) с 15:55 до 20:45", "пт 30.10, 15:55"),
    ("у нас 1,5 недели до сдачи", "чт 05.11, 23:59"),
    ("завтра", "вт 27.10, 23:59"),
    ("до 5-го", "чт 05.11, 23:59"),
    ("05.11 23:59", "чт 05.11, 23:59"),
    ("перенесли с 30.10 на 02.11", "пн 02.11, 23:59"),
    ("с пятницы на понедельник", "пн 02.11, 23:59"),
    ("защита 2.11 в 10.15", "пн 02.11, 10:15"),
    ("на этой неделе", "вс 01.11, 23:59"),
    ("Чемпионат пройдёт 30 октября с 16:00.", "пт 30.10, 16:00"),
])
def test_parse_due(settings, base, text, expected):
    r = parse_due(text, base, settings.tz, default_time_for("deadline"))
    assert fmt_due(r.dt) == expected


def test_relative_to_message_date_not_today(settings):
    # то же «до пятницы», но сообщение написано в субботу → следующая пятница
    sat = datetime(2026, 10, 31, 12, 0, tzinfo=settings.tz)
    assert fmt_due(parse_due("до пятницы", sat, settings.tz).dt) == "пт 06.11"


def test_unparseable(settings, base):
    assert not parse_due("к следующей паре", base, settings.tz).ok
    assert not parse_due("на 2 лабы", base, settings.tz).ok
