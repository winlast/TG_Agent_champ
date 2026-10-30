"""Генератор СИНТЕТИЧЕСКИХ фикстур в формате Telegram Desktop JSON-экспорта + размеченного датасета.

Все имена вымышлены. Реалистичность — по наблюдениям из раздела 4 ТЗ: дробление объявлений,
уточнения позже, относительные сроки, переносы, отмены, шум, сленг, вопросы + ответы, файлы.

    python scripts/make_fixtures.py   →  data/fixtures/*.json, data/eval/labeled.jsonl

Генератор сам проверяет, что разметка совпадает со склейкой очередей парсера.
"""

from __future__ import annotations

import json
import sys
import zlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from study_radar.ingest.bursts import group_bursts  # noqa: E402
from study_radar.ingest.telegram_export import parse_chat  # noqa: E402

TZ = ZoneInfo("Europe/Moscow")
FILE_STUB = "(File not included. Change data exporting settings to download.)"


def N(**tags):
    return {"is_important": False, "subject": None, "category": "noise", "due": None, **tags}


def I(cat, subj=None, due=None, **tags):  # noqa: E743
    return {"is_important": True, "subject": subj, "category": cat,
            "due": f"2026-{due}" if due else None, **tags}


# (когда "ДД ЧЧ:ММ", автор, текст | [серия] | None, метка, опции)
# опции: key, reply, topic, file, fmt ("array" | "rich"), edited, service, sticker
STAROSTA = "Мария Иванова (староста)"
ORLOV, KOVALEV, GROMOV = "Орлов Д. В.", "Ковалёв А. П.", "Громов Ю. Е. (АСОИУ)"

CHATS = {
    "general": {"name": "ИУ5-31Б общий", "id": 1000000001, "items": [
        ("19 09:00", "Полина Зайцева", None, None, {"service": "join_group_by_link"}),
        ("19 09:12", STAROSTA, "Всем привет! Закрепила расписание на семестр, проверяйте аудитории перед парами.",
         I("announcement"), {"key": "sched"}),
        ("19 09:13", STAROSTA, None, None, {"service": "pin_message"}),
        ("19 09:20", "Олег Петров", "спасибо", N()),
        ("19 09:25", "Кирилл Лебедев", "а где расписание то", N(), {"key": "q_sched"}),
        ("19 09:31", STAROSTA, "в закрепе 👆", N(), {"reply": "q_sched"}),
        ("19 12:40", STAROSTA, "Смирнова: ЛР1 по моделям данных защищаем до 23 октября, отчёт распечатать",
         I("deadline", "data_models", "10-23")),
        ("19 12:45", "Денис Ким", "😭", N()),
        ("19 18:05", "Саша Миронов", "кто идёт в столовку после пар?", N()),
        ("20 08:15", "Полина Зайцева", "кто на физру сегодня?", N(tag="fizra")),
        ("20 08:16", "Лёша Новиков", "я", N()),
        ("20 10:02", STAROSTA, "Пары по ТВиМС завтра не будет, Белова на конференции",
         I("cancellation", "probability", "10-21")),
        ("20 10:03", "Вика Белкина", "ура", N()),
        ("20 10:05", "Олег Петров", "а ДЗ по терверу тогда когда сдавать?", N(), {"key": "q_tv"}),
        ("20 10:20", STAROSTA, "Белова сказала: типовой расчёт по ТВиМС сдать до 30 октября в ЭУ",
         I("deadline", "probability", "10-30", tag="answer"), {"reply": "q_tv"}),
        ("20 14:30", "Аня Соколова", "ребят, кто понял тему по линалу?", N()),
        ("20 19:40", "Кирилл Лебедев", "матан опять скучный был", N()),
        ("21 09:00", STAROSTA, "Напоминаю: в пятницу 23.10 в 12:00 собрание группы по поводу практики, ауд. 395",
         I("event", None, "10-23")),
        ("21 09:10", "Денис Ким", "а обязательно?", N(), {"key": "q_obyaz"}),
        ("21 09:12", STAROSTA, "да", N(), {"reply": "q_obyaz"}),
        ("21 13:00", "Лёша Новиков", "Los Pollos Hermanos набирают ещё одного человека в команду на чемпионат, пишите в лс",
         N(tag="team")),
        ("21 15:30", "Вика Белкина", "Морозов выложил ДЗ по вебу: сверстать форму регистрации, сдать до 28.10",
         I("deadline", "web", "10-28")),
        ("21 15:35", "Саша Миронов", "опять вёрстка 🙄", N()),
        ("22 11:00", STAROSTA, "Ковалёв просил передать: защита ЛР1 по элтеху — во вторник 27.10 на паре, без отчёта не пустит",
         I("event", "electro", "10-27", tag="dedup")),
        ("22 11:20", "Олег Петров", "а отчёт по шаблону?", N(), {"key": "q_tpl"}),
        ("22 11:25", STAROSTA, "Шаблон отчёта по элтеху в папке группы: https://disk.example.ru/iu5-31b/elteh",
         I("announcement", "electro"), {"reply": "q_tpl", "fmt": "array"}),
        ("22 17:00", "Денис Ким", "кто-нибудь скиньте конспект по алгосам", N()),
        ("22 17:10", "Полина Зайцева", None, I("materials", "algorithms"), {"file": "АиСД_лекция_5_деревья.pdf"}),
        ("22 17:12", "Денис Ким", "спасибо!!", N()),
        ("23 09:30", STAROSTA, "Захаров: контест по АиСД открыт до воскресенья 23:59, задачи на деревья",
         I("deadline", "algorithms", "10-25")),
        ("23 12:00", "Кирилл Лебедев", "кто выиграет чемпионат? ставлю на Петрушу", N(tag="team")),
        ("23 16:00", "Аня Соколова", "ахахах", N()),
        ("24 13:00", "Саша Миронов", "всем хороших выходных!", N()),
        ("26 08:40", STAROSTA, "Доброе утро! Сегодня ко второй паре, первой по УРПО нет — Никитин заболел",
         I("cancellation", "sw_mgmt", "10-26")),
        ("26 08:41", "Олег Петров", "кайф", N()),
        ("26 14:00", STAROSTA, "ДЗ по веб-технологиям: сверстать лендинг по макету, сдать до 6 ноября (продлили).",
         I("deadline", "web", "11-06", tag="edited", category_alt=["deadline_update"]), {"edited": "29 20:00"}),
        ("26 16:20", "Вика Белкина", "а по урпо будет презентация?", N(), {"key": "q_urpo"}),
        ("26 16:45", STAROSTA, "Никитин сказал: презентацию по УРПО защищаем 9 ноября, группы по 3 человека",
         I("event", "sw_mgmt", "11-09", tag="answer"), {"reply": "q_urpo"}),
        ("27 08:02", "Олег Петров", "физра сегодня в большом зале?", N(tag="fizra")),
        ("27 09:00", STAROSTA, "Смирнова просила напомнить: ЛР3 по БД сдаём до 5-го, отчёт в ЭУ.",
         I("deadline", "data_models", "11-05", tag="dedup")),
        ("27 09:05", "Денис Ким", "+", N()),
        ("27 12:00", STAROSTA, ["Важно по ТВиМС:", "1. РК по ТВиМС — 6 ноября на паре",
                                "2. Консультация перед РК — 5 ноября в 17:00, ауд. 304", "3. С собой калькулятор"],
         I("event", "probability", "11-06", tag="burst")),
        ("27 18:30", "Саша Миронов", "кто в кино в субботу?", N()),
        ("28 10:30", "Полина Зайцева", "у кого есть методичка по физике? лабы 3-4", N(), {"key": "q_metod"}),
        ("28 10:40", "Аня Соколова", None, I("materials", "physics"),
         {"file": "Методичка_физика_ЛР3-4.pdf", "reply": "q_metod"}),
        ("29 10:00", "Вика Белкина", "Кто знает, веб сегодня будет?", N(), {"key": "q_web"}),
        ("29 10:05", STAROSTA, "Будет, но в 395 аудитории, а не в 224.",
         I("announcement", "web", tag="answer", category_alt=["event"]), {"reply": "q_web"}),
        ("29 19:10", STAROSTA, "Консультации по ТВиМС 5 ноября не будет, Белова заболела. РК остаётся 6 ноября.",
         I("cancellation", "probability", "11-05")),
        ("29 19:15", "Кирилл Лебедев", "😢", N()),
        ("30 08:30", "Лёша Новиков", "сегодня все на чемпионат? болеем за наших!", N()),
        ("30 09:00", STAROSTA, "Напоминаю: сегодня кейс-чемпионат в 501ю с 16:00, после него пар нет",
         I("event", "asoiu", "10-30", tag="dedup")),
        ("30 09:30", "Денис Ким", "ВСЕХ ЖДЕМ!!!", N()),
        ("19 21:00", "Аня Соколова", "кто-нибудь знает, когда стипендия?", N()),
        ("20 12:00", "Саша Миронов", "кто потерял зарядку в 395? лежит у меня", N()),
        ("21 18:00", "Олег Петров", "го в субботу на каток", N()),
        ("21 18:02", "Вика Белкина", "я за", N()),
        ("22 20:00", "Кирилл Лебедев", "у кого есть ответы на тест по истории?", N()),
        ("23 10:00", STAROSTA, "Напоминаю: до 30 октября сдать заявления на общежитие в деканат, 2 этаж",
         I("deadline", None, "10-30")),
        ("24 11:00", "Денис Ким", "кто-нибудь идёт на день открытых дверей помогать?", N()),
        ("25 20:00", "Полина Зайцева", "завтра первая пара есть?", N(), {"key": "q_first"}),
        ("25 20:15", STAROSTA, "Есть, ТВиМС в 8:30, ауд. 304", I("announcement", "probability", tag="answer",
                                                                  category_alt=["event"]), {"reply": "q_first"}),
        ("27 15:00", "Лёша Новиков", "кто-нибудь видел мой пропуск?", N()),
        ("28 13:00", "Вика Белкина", "в столовой опять очередь на полчаса", N()),
        ("29 12:30", "Саша Миронов", "кто сделал лабу по БД, она сложная?", N(tag="subject_noise")),
    ]},
    "physics": {"name": "Физика ИУ5-31Б", "id": 1000000002, "items": [
        ("19 10:00", ORLOV, "Здравствуйте! Это чат для вопросов по физике. Задачник и методички — в ЭУ.",
         I("announcement", "physics")),
        ("19 10:02", "Кирилл Лебедев", "здравствуйте!", N()),
        ("19 10:03", "Аня Соколова", "спасибо", N()),
        ("20 11:00", ORLOV, "ДЗ №2: задачи 2.1–2.12, сдать до четверга 22.10 в 23:59", I("deadline", "physics", "10-22")),
        ("20 11:05", "Денис Ким", "это жесть", N()),
        ("20 11:30", "Олег Петров", "физос опять завалит", N()),
        ("21 16:00", "Аня Соколова", "А в ДЗ №2 задачу 2.7 нужно с рисунком?", N(), {"key": "q_27"}),
        ("21 16:30", ORLOV, "Да, рисунок обязателен", I("announcement", "physics", tag="answer"), {"reply": "q_27"}),
        ("21 21:00", "Кирилл Лебедев", "физика — говно", N(tag="subject_noise")),
        ("22 09:00", ORLOV, "Лабораторные 3 и 4 выполняем на следующей неделе, допуск — по теории",
         I("announcement", "physics", category_alt=["event", "assignment"])),
        ("22 09:20", "Лёша Новиков", "а теория где?", N(), {"key": "q_teor"}),
        ("22 09:25", ORLOV, "В методичке, разделы 3.1–3.4", I("announcement", "physics", tag="answer",
                                                                   category_alt=["materials"]), {"reply": "q_teor"}),
        ("23 14:00", "Полина Зайцева", "кто-нибудь решил 2.11?", N()),
        ("23 14:10", "Саша Миронов", "неа", N()),
        ("23 14:15", "Полина Зайцева", "ладно", N()),
        ("26 09:05", ORLOV, "Добрый день! ДЗ №3 (задачи 3.14–3.20 из задачника) сдать до пятницы, в 23:59 закрываю приём в ЭУ.",
         I("deadline", "physics", "10-30"), {"key": "dz3"}),
        ("26 09:07", "Кирилл Лебедев", "спасибо!", N()),
        ("26 20:00", "Аня Соколова", "у кого получился ответ в 3.15? у меня 4.2 м/с", N(tag="numbers"), {"key": "q_315"}),
        ("26 20:10", "Олег Петров", "у меня 4.5", N(), {"reply": "q_315"}),
        ("27 13:30", "Аня Соколова", "А можно сдать ДЗ в понедельник?", N(), {"key": "q_mon"}),
        ("27 13:45", ORLOV, "Можно, срок ДЗ №3 продлеваю до понедельника 02.11, 23:59.",
         I("deadline_update", "physics", "11-02", tag="answer"), {"reply": "q_mon"}),
        ("27 13:50", "Вика Белкина", "спасибо огромное!!!", N()),
        ("28 12:00", ORLOV, "Темы РК-1:\n• механика\n• молекулярная физика\nРК-1 пройдёт 13 ноября на паре",
         I("event", "physics", "11-13"), {"fmt": "rich"}),
        ("28 12:05", "Денис Ким", "😱", N()),
        ("29 15:00", "Саша Миронов", "кто на физру завтра?", N(tag="fizra")),
        ("29 15:30", "Лёша Новиков", "физ-ра в 9 утра это бесчеловечно", N(tag="fizra")),
        ("30 09:00", ORLOV, "Лабораторную №3 сдаём к следующей паре, бланки у лаборанта",
         I("deadline", "physics", tag="no_date")),
        ("30 09:02", "Денис Ким", None, N(), {"sticker": "👍"}),
        ("20 18:00", "Кирилл Лебедев", "кто-нибудь понял задачу 2.5 про блоки?", N()),
        ("20 18:20", "Аня Соколова", "там через второй закон Ньютона", N()),
        ("22 19:00", "Олег Петров", "физос сегодня огонь был, наконец-то понял импульс", N(tag="subject_noise")),
        ("24 12:00", "Денис Ким", "скиньте фотку доски с последней пары", N()),
        ("24 12:30", "Полина Зайцева", None, I("materials", "physics"), {"file": "доска_физика_23.10.jpg"}),
        ("28 19:00", "Саша Миронов", "РК-1 сложный будет?", N(), {"key": "q_rk"}),
        ("28 19:30", ORLOV, "Не сложнее ДЗ. Консультация перед РК — 12 ноября в 17:00, ауд. 241",
         I("event", "physics", "11-12", tag="answer"), {"reply": "q_rk"}),
    ]},
    "elteh": {"name": "Элтех лабы", "id": 1000000003, "items": [
        ("19 11:00", KOVALEV, "Коллеги, график такой: защита ЛР1 — 27.10 на паре. Остальные сроки сообщу позже.",
         I("event", "electro", "10-27")),
        ("19 11:05", "Лёша Новиков", "спасибо", N()),
        ("20 15:00", "Саша Миронов", "элтех это вообще жесть, ничего не понятно", N(tag="subject_noise")),
        ("20 15:20", "Полина Зайцева", "кто сделал предварительный расчёт к ЛР1?", N()),
        ("20 15:30", "Аня Соколова", "я, скину вечером", N()),
        ("20 21:00", "Аня Соколова", None, I("materials", "electro"), {"file": "ЛР1_расчет_пример.xlsx"}),
        ("22 10:00", KOVALEV, ["По ЛР2:", "1. Собрать схему по варианту", "2. Снять ВАХ диода",
                               "3. Отчёт — по шаблону кафедры"], I("assignment", "electro", tag="burst")),
        ("22 10:10", "Денис Ким", "а варианты где?", N(), {"key": "q_var"}),
        ("22 10:15", KOVALEV, "Варианты в ЭУ, вариант = номер в журнале", I("announcement", "electro", tag="answer"),
         {"reply": "q_var"}),
        ("23 18:00", "Кирилл Лебедев", "сделал схему, она дымится 🔥😂", N()),
        ("26 10:15", KOVALEV, "Защита ЛР2 в пятницу 30.10 в 10:15, ауд. 512. Без отчёта не допускаю.",
         I("event", "electro", "10-30"), {"key": "lab2"}),
        ("26 10:20", "Олег Петров", "ок", N()),
        ("27 08:30", KOVALEV, "Сегодня защита ЛР1, начинаем в 10:15, не опаздывайте", I("event", "electro", "10-27",
                                                                                        tag="dedup")),
        ("27 12:00", "Лёша Новиков", "защитил!!! 🎉", N()),
        ("28 09:05", KOVALEV, "лабу перенесли на среду", I("deadline_update", "electro", "11-04", tag="reply_transfer"),
         {"reply": "lab2"}),
        ("28 09:10", "Лёша Новиков", "ура!!!", N()),
        ("28 09:12", "Денис Ким", "лучший препод", N()),
        ("29 14:00", "Полина Зайцева", "а ЛР3 когда?", N(), {"key": "q_lr3"}),
        ("29 14:30", KOVALEV, "ЛР3 сдать до 13 ноября, задание выложу в понедельник",
         I("deadline", "electro", "11-13", tag="answer"), {"reply": "q_lr3"}),
        ("29 14:35", "Полина Зайцева", "спасибо!", N()),
        ("29 22:00", "Саша Миронов", "кто-нибудь знает формулу для мощности в ЛР2? P = UI или что", N(), {"key": "q_p"}),
        ("29 22:05", "Аня Соколова", "P=UI cos φ", N(), {"reply": "q_p"}),
        ("19 18:00", "Кирилл Лебедев", "кто-нибудь знает, лабы в 512 или 514?", N(), {"key": "q_aud"}),
        ("19 18:30", KOVALEV, "Лабораторные проходят в 512", I("announcement", "electro", tag="answer"),
         {"reply": "q_aud"}),
        ("21 10:00", "Денис Ким", "кто уже собрал стенд?", N()),
        ("23 09:00", "Полина Зайцева", "ребят, мультиметр в 512 сломан, берите из 514", N()),
        ("26 12:00", "Саша Миронов", "кто пойдёт защищать первым?", N()),
        ("27 12:05", "Денис Ким", "+1, тоже защитил", N()),
        ("30 11:00", "Аня Соколова", "кто знает, Ковалёв сегодня принимает долги по ЛР1?", N()),
    ]},
    "champ": {"name": "Кейс-чемпионат", "id": 1000000004, "forum": ["Объявления", "Задание", "Книги", "Общение"], "items": [
        ("19 10:05", GROMOV, "Добро пожаловать в чат кейс-чемпионата по созданию ИИ-агентов! Здесь будут объявления и задание.",
         I("announcement", "asoiu"), {"topic": "Объявления"}),
        ("19 10:10", "Вика Белкина", "всем привет!", N(), {"topic": None}),
        ("21 16:19", GROMOV, ["Описание задания:", "1. Разработать ИИ-агента по варианту А или Б.",
                              "2. Защита — презентация со скриншотами работы агента.", "3. Возможны варианты:",
                              "А. Индексация + RAG + инструменты через MCP", "Б. Мультиагентная система",
                              "До сдачи у вас 1,5 недели."],
         I("assignment", "asoiu", tag="burst", category_alt=["deadline"]), {"topic": "Задание", "edited": "21 16:24"}),
        ("21 16:40", GROMOV, None, I("materials", "asoiu"), {"topic": "Книги", "file": "RAG_и_генеративный_ИИ.pdf"}),
        ("21 16:50", GROMOV, None, I("materials", "asoiu"), {"topic": "Книги", "file": "Агенты_на_LangGraph.pdf"}),
        ("21 17:00", "Денис Ким", "а что за победу дают?", N(), {"topic": "Общение"}),
        ("21 17:05", "Кирилл Лебедев", "автомат наверное 😏", N(), {"topic": "Общение"}),
        ("22 12:00", GROMOV, "Кейс-чемпионат пройдёт 30 октября (пятница) с 15:55 до 20:45. Номера аудиторий будут уточняться.",
         I("event", "asoiu", "10-30"), {"topic": "Объявления", "key": "champ"}),
        ("22 12:01", GROMOV, None, None, {"service": "pin_message", "topic": "Объявления"}),
        ("22 12:30", "Вика Белкина", "Los Pollos Hermanos в деле 🔥", N(tag="team"), {"topic": "Общение"}),
        ("22 12:35", "Лёша Новиков", "Петруша тоже", N(tag="team"), {"topic": "Общение"}),
        ("23 10:00", "Олег Петров", "А если команда не может 30 октября?", N(), {"topic": "Общение", "key": "q_day"}),
        ("23 11:30", GROMOV, "Можно выступить в другой день, напишите мне заранее",
         I("announcement", "asoiu", tag="answer"), {"topic": "Общение", "reply": "q_day"}),
        ("26 11:00", GROMOV, "Требования к презентации:\n• титульный слайд\n• состав команды с ФИО\n• архитектура\n"
                             "• скриншоты работы\n• ссылка на репозиторий",
         I("announcement", "asoiu"), {"topic": "Объявления", "fmt": "rich"}),
        ("26 15:00", "Саша Миронов", "кто уже сделал RAG? у нас эмбеддинги не грузятся", N(), {"topic": "Общение"}),
        ("26 15:10", "Аня Соколова", "попробуйте bge-m3 через ollama", N(), {"topic": "Общение"}),
        ("27 11:00", GROMOV, "Чемпионат будет проходить в аудитории 501ю, 30 октября с 16:00.",
         I("deadline_update", "asoiu", "10-30", tag="location_update"), {"topic": "Объявления"}),
        ("27 11:05", "Лёша Новиков", "а что за победу дают?", N(), {"topic": "Общение"}),
        ("28 18:30", GROMOV, "Уточнение: выступить в другой день нельзя, только 30-го.",
         I("announcement", "asoiu", tag="condition_change", category_alt=["deadline_update"]),
         {"topic": "Общение", "reply": "q_day"}),
        ("29 09:00", GROMOV, "Порядок выступлений опубликован: https://docs.example.ru/champ-order",
         I("announcement", "asoiu"), {"topic": "Объявления", "fmt": "array"}),
        ("29 09:30", "Денис Ким", "мы последние 😭", N(), {"topic": "Общение"}),
        ("29 20:00", "Вика Белкина", "ВСЕХ ЖДЕМ!!!", N(), {"topic": None}),
        ("30 08:00", GROMOV, "Напоминаю: сегодня в 16:00, ауд. 501ю. Регламент — 10 минут на команду.",
         I("event", "asoiu", "10-30", tag="dedup"), {"topic": "Объявления"}),
        ("30 08:10", "Кирилл Лебедев", "волнуюсь", N(), {"topic": "Общение"}),
        ("30 08:20", "Полина Зайцева", "удачи всем!", N(), {"topic": "Общение"}),
        ("20 19:00", "Саша Миронов", "у кого какие темы агентов? у нас сортировка почты", N(), {"topic": "Общение"}),
        ("20 19:10", "Аня Соколова", "у нас чат-бот для расписания", N(), {"topic": "Общение"}),
        ("24 16:00", "Кирилл Лебедев", "а можно использовать облачные модели?", N(),
         {"topic": "Общение", "key": "q_cloud"}),
        ("24 17:00", GROMOV, "Нет, только локальные модели через Ollama, порядка 7–8 млрд параметров",
         I("announcement", "asoiu", tag="answer"), {"topic": "Общение", "reply": "q_cloud"}),
        ("27 18:00", "Вика Белкина", "у кого qwen3 нормально вызывает инструменты?", N(), {"topic": "Общение"}),
        ("29 21:00", "Денис Ким", "презентация готова, осталось отрепетировать", N(), {"topic": "Общение"}),
    ]},
    "flood": {"name": "ИУ5-31Б Флудилка", "id": 1000000005, "items": [
        ("19 20:00", "Кирилл Лебедев", "кто играет в доту сегодня?", N()),
        ("19 20:05", "Денис Ким", "я", N()),
        ("19 20:06", "Олег Петров", "+", N()),
        ("20 13:00", "Лёша Новиков", None, N(), {"sticker": "😂"}),
        ("20 13:05", "Вика Белкина", "мем дня: студент перед дедлайном 🤡", N(tag="meme")),
        ("20 22:30", "Аня Соколова", "кто-нибудь спит вообще?", N()),
        ("21 08:00", "Саша Миронов", "доброе утро страна", N()),
        ("21 12:00", "Кирилл Лебедев", "столовка сегодня норм", N()),
        ("21 23:10", "Денис Ким", "обожаю дедлайны в 3 ночи, лучший универ 🙃", N(tag="sarcasm")),
        ("22 14:00", "Полина Зайцева", "кто знает где 501ю?", N(), {"key": "q_501"}),
        ("22 14:05", "Лёша Новиков", "в учебно-лабораторном корпусе, 5 этаж", N(), {"reply": "q_501"}),
        ("23 19:00", "Олег Петров", "пятница!!!", N()),
        ("23 19:01", "Вика Белкина", "🎉🎉🎉", N()),
        ("24 15:00", "Кирилл Лебедев", "кто на хакатон в субботу?", N()),
        ("25 21:00", "Аня Соколова", "завтра опять в 8:30 вставать 😩", N(tag="numbers")),
        ("26 09:30", "Кирилл Лебедев", "физика — говно", N(tag="subject_noise")),
        ("26 09:31", "Денис Ким", "😂😂😂", N()),
        ("26 13:00", "Саша Миронов", "кто с нами в бильярд после пар", N()),
        ("27 09:20", "Аня Соколова", "напоминалка: лаба 3 по базам данных до 5 ноября, кто ещё не начал — пора",
         I("deadline", "data_models", "11-05", tag="dedup")),
        ("27 09:25", "Олег Петров", "спасибо что напомнила", N()),
        ("27 20:00", "Денис Ким", "линал это боль", N()),
        ("28 10:00", "Денис Ким", "матан опять скучный, кто-нибудь понял что было?", N(tag="subject_noise")),
        ("28 16:00", "Вика Белкина", "ахахаха смотрите что в чате потока творится", N()),
        ("28 22:00", "Лёша Новиков", "кто-нибудь сделал ДЗ по вебу? скиньте посмотреть", N(tag="subject_noise")),
        ("29 08:00", "Кирилл Лебедев", "кто на физру?", N(tag="fizra")),
        ("29 12:00", "Саша Миронов", "Петруша vs Los Pollos Hermanos — ставки принимаются", N(tag="team")),
        ("29 18:00", "Полина Зайцева", "кстати, Белова отменила консультацию 5-го, мне староста сказала",
         I("cancellation", "probability", "11-05", tag="dedup")),
        ("29 18:05", "Аня Соколова", "жаль", N()),
        ("30 07:50", "Олег Петров", "всем удачи на чемпионате!", N()),
        ("30 08:30", "Кирилл Лебедев", "Los Pollos Hermanos — лучшая команда, без вариантов", N(tag="team")),
        ("30 10:00", "Денис Ким", "го в столовку", N()),
        ("19 23:00", "Аня Соколова", "спокойной ночи всем", N()),
        ("20 09:00", "Саша Миронов", "кто проспал первую пару? 🙋", N()),
        ("22 23:30", "Кирилл Лебедев", "сделал ДЗ по физике за 20 минут, чувствую себя гением", N(tag="subject_noise")),
        ("24 18:00", "Вика Белкина", None, N(), {"sticker": "😎"}),
        ("25 12:00", "Олег Петров", "кто смотрел матч вчера?", N()),
        ("27 23:00", "Лёша Новиков", "напоминаю всем, что сон для слабых", N()),
        ("28 08:10", "Полина Зайцева", "автобус опять опоздал, буду к 9:00", N(tag="numbers")),
        ("30 12:00", "Саша Миронов", "после чемпионата идём отмечать?", N()),
    ]},
}


def when(s: str) -> datetime:
    day, hm = s.split()
    h, m = hm.split(":")
    month = 10 if int(day) >= 15 else 11
    return datetime(2026, month, int(day), int(h), int(m), tzinfo=TZ)


def uid(name: str) -> str:
    return f"user{zlib.crc32(name.encode()) % 900000000 + 100000000}"


def text_field(text: str, fmt: str | None):
    if fmt == "array" and "https://" in text:
        head, _, rest = text.partition("https://")
        url, _, tail = ("https://" + rest).partition(" ")
        return [head, {"type": "link", "text": url}, (" " + tail) if tail else ""]
    return text


def rich(text: str) -> dict:
    blocks, bullets = [], []
    for line in text.split("\n"):
        if line.startswith("• "):
            bullets.append({"text": {"type": "plain", "text": line[2:]}})
            continue
        if bullets:
            blocks.append({"type": "list", "kind": "bullet", "items": bullets})
            bullets = []
        blocks.append({"type": "paragraph", "text": {"type": "plain", "text": line}})
    if bullets:
        blocks.append({"type": "list", "kind": "bullet", "items": bullets})
    return {"blocks": blocks}


def stamp(d: datetime) -> dict:
    return {"date": d.strftime("%Y-%m-%dT%H:%M:%S"), "date_unixtime": str(int(d.timestamp()))}


def build_chat(key: str, spec: dict):
    msgs, labels, keys = [], [], {}
    next_id = 1
    topics: dict[str, int] = {}
    for title in spec.get("forum", []):
        d = when("19 10:00")
        msgs.append({"id": next_id, "type": "service", **stamp(d), "actor": GROMOV, "actor_id": uid(GROMOV),
                     "action": "topic_created", "title": title, "text": "", "text_entities": []})
        topics[title] = next_id
        next_id += 1
    for item in sorted(spec["items"], key=lambda it: when(it[0])):
        ts, author, text, label = item[:4]
        opts = item[4] if len(item) > 4 else {}
        d = when(ts)
        topic_root = topics.get(opts.get("topic")) if spec.get("forum") else None
        if opts.get("service"):
            m = {"id": next_id, "type": "service", **stamp(d), "actor": author, "actor_id": uid(author),
                 "action": opts["service"], "text": "", "text_entities": []}
            if opts["service"] == "pin_message":
                m["message_id"] = next_id - 1
            if topic_root:
                m["reply_to_message_id"] = topic_root
            msgs.append(m)
            next_id += 1
            continue
        parts = text if isinstance(text, list) else [text]
        first_id = next_id
        for i, part in enumerate(parts):
            dd = d.replace(minute=d.minute) if i == 0 else d.fromtimestamp(d.timestamp() + 40 * i, TZ)
            m = {"id": next_id, "type": "message", **stamp(dd), "from": author, "from_id": uid(author)}
            if opts.get("reply") and i == 0:
                m["reply_to_message_id"] = keys[opts["reply"]]
            elif topic_root:
                m["reply_to_message_id"] = topic_root
            if opts.get("edited") and i == min(1, len(parts) - 1):
                e = when(opts["edited"])
                m["edited"] = e.strftime("%Y-%m-%dT%H:%M:%S")
                m["edited_unixtime"] = str(int(e.timestamp()))
            if opts.get("file") and i == 0:
                m.update({"file": FILE_STUB, "file_name": opts["file"], "mime_type": "application/octet-stream"})
            if opts.get("sticker"):
                m.update({"file": FILE_STUB, "media_type": "sticker", "sticker_emoji": opts["sticker"],
                          "width": 512, "height": 512})
            if part is None:
                m["text"] = ""
            elif opts.get("fmt") == "rich":
                m["text"] = ""
                m["rich_message"] = rich(part)
            else:
                m["text"] = text_field(part, opts.get("fmt"))
            m["text_entities"] = [] if not part else [{"type": "plain", "text": part}]
            msgs.append(m)
            next_id += 1
        if opts.get("key"):
            keys[opts["key"]] = first_id
        if label is not None:
            labels.append({"first_id": first_id, "ids": list(range(first_id, next_id)), "label": label})
    data = {"name": spec["name"], "type": "private_supergroup", "id": spec["id"], "messages": msgs}
    return data, labels


def main() -> None:
    out_dir = ROOT / "data/fixtures"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, total_msgs, important = [], 0, 0
    for key, spec in CHATS.items():
        data, labels = build_chat(key, spec)
        (out_dir / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        chat = parse_chat(data, TZ)
        posts = {p.message_ids[0]: p for p in group_bursts(chat.messages, 5)}
        assert len(posts) == len(labels), f"{key}: постов {len(posts)}, меток {len(labels)}"
        for lb in labels:
            p = posts.get(lb["first_id"])
            assert p is not None and p.message_ids == lb["ids"], f"{key}: склейка не совпала с разметкой {lb}"
            label = {k: v for k, v in lb["label"].items() if k not in {"tag", "category_alt"}}
            rows.append({"post_id": p.post_id, "chat": p.chat_title, "topic": p.topic, "author": p.author,
                         "date": p.date.isoformat(), "text": p.text, "label": label,
                         "category_alt": lb["label"].get("category_alt", []), "tag": lb["label"].get("tag")})
            important += int(label["is_important"])
        total_msgs += len(data["messages"])
    rows.sort(key=lambda r: r["date"])
    eval_dir = ROOT / "data/eval"
    eval_dir.mkdir(parents=True, exist_ok=True)
    with (eval_dir / "labeled.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Фикстуры: {len(CHATS)} чатов, {total_msgs} сообщений (включая служебные); "
          f"размечено постов: {len(rows)}, важных: {important} ({important / len(rows):.0%})")


if __name__ == "__main__":
    main()
