"""Сборка презентации: docs/StudyRadar_presentation.pptx + docs/presentation.md (make slides).

Один источник содержимого для обоих файлов. Подставляет:
  * состав команды из docs/team.yaml;
  * схему docs/architecture.png;
  * скриншоты docs/screenshots/*.png (снимаются `make screenshots` после прогона настоящей моделью);
  * метрики из docs/eval_report.json (`make eval`); бейзлайн по ключевым словам считается без LLM.
Если скриншотов/метрик ещё нет — на их месте видна понятная заглушка.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import yaml
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
DOCS = ROOT / "docs"
SHOTS = DOCS / "screenshots"

FONT = "Segoe UI"
INK = RGBColor(0x1F, 0x2A, 0x44)
MUTED = RGBColor(0x5B, 0x64, 0x72)
ACCENT = RGBColor(0x2F, 0x6F, 0xB0)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
C_INDEX = RGBColor(0xDC, 0xE9, 0xF7)
C_STORE = RGBColor(0xCF, 0xE8, 0xE3)
C_ONLINE = RGBColor(0xF6, 0xD9, 0xC0)
C_LLM = RGBColor(0xD9, 0xEA, 0xD3)
C_MCP = RGBColor(0xE4, 0xDA, 0xF2)
C_RED = RGBColor(0xFD, 0xE8, 0xE8)
C_GREEN = RGBColor(0xE6, 0xF4, 0xEA)
C_GRAY = RGBColor(0xF3, 0xF5, 0xF8)

W, H = Inches(13.333), Inches(7.5)


# ---------------------------------------------------------------------------------------------- данные
def load_team() -> dict:
    return yaml.safe_load((DOCS / "team.yaml").read_text(encoding="utf-8"))


def load_metrics() -> dict | None:
    p = DOCS / "eval_report.json"
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        return None if data.get("fake") else data
    return None


def baseline_only() -> dict:
    """Метрики бейзлайна «ключевые слова» — без LLM, честно считаются в любой среде."""
    from study_radar.config import load_settings
    from study_radar.eval import baseline_predict, score
    from datetime import datetime

    s = load_settings(use_env=False)
    rows = []
    for line in (ROOT / "data/eval/labeled.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        rows.append({"gold": r["label"], "alt": r.get("category_alt", []),
                     "pred": baseline_predict(s, r["text"], datetime.fromisoformat(r["date"]))})
    return score(rows)


def shot(name: str) -> Path | None:
    p = SHOTS / name
    return p if p.exists() else None


# ---------------------------------------------------------------------------------------------- примитивы
def text_box(slide, x, y, w, h, paras, size=16, color=INK, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
             spacing=6):
    """paras: str | list[str | (text, {size, bold, color})]."""
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(0.05)
    items = paras if isinstance(paras, list) else [paras]
    for i, item in enumerate(items):
        text, opt = (item, {}) if isinstance(item, str) else item
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = opt.get("align", align)
        p.space_after = Pt(opt.get("spacing", spacing))
        r = p.add_run()
        r.text = text
        f = r.font
        f.name = opt.get("font", FONT)
        f.size = Pt(opt.get("size", size))
        f.bold = opt.get("bold", bold)
        f.italic = opt.get("italic", False)
        f.color.rgb = opt.get("color", color)
    return tb


def box(slide, x, y, w, h, fill, line=None, shape=MSO_SHAPE.ROUNDED_RECTANGLE, dashed=False):
    s = slide.shapes.add_shape(shape, x, y, w, h)
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(1.25)
        if dashed:
            from pptx.enum.dml import MSO_LINE_DASH_STYLE

            s.line.dash_style = MSO_LINE_DASH_STYLE.DASH
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = 0.08
    s.shadow.inherit = False
    return s


def card(slide, x, y, w, h, fill, title, body, title_size=17, body_size=13):
    box(slide, x, y, w, h, fill)
    text_box(slide, x + Inches(0.18), y + Inches(0.12), w - Inches(0.36), Inches(0.5), title, size=title_size, bold=True)
    text_box(slide, x + Inches(0.18), y + Inches(0.62), w - Inches(0.36), h - Inches(0.7),
             body if isinstance(body, list) else [body], size=body_size, color=INK, spacing=4)


def header(slide, title, n, sub=None):
    box(slide, 0, 0, W, Inches(0.12), ACCENT, shape=MSO_SHAPE.RECTANGLE)
    text_box(slide, Inches(0.6), Inches(0.35), Inches(12), Inches(0.8), title, size=30, bold=True)
    if sub:
        text_box(slide, Inches(0.62), Inches(1.05), Inches(12), Inches(0.5), sub, size=15, color=MUTED)
    text_box(slide, Inches(0.6), Inches(7.05), Inches(9), Inches(0.35), "StudyRadar · команда SuperPuper · ИУ5-31Б",
             size=10, color=MUTED)
    text_box(slide, Inches(12.0), Inches(7.05), Inches(0.8), Inches(0.35), str(n), size=10, color=MUTED,
             align=PP_ALIGN.RIGHT)


def picture_fit(slide, path: Path | None, x, y, w, h, placeholder: str):
    if path is None:
        b = box(slide, x, y, w, h, C_GRAY, line=RGBColor(0xB8, 0xC0, 0xCC), dashed=True)
        text_box(slide, x + Inches(0.3), y + h / 2 - Inches(0.6), w - Inches(0.6), Inches(1.2),
                 [("Скриншот появится здесь", {"size": 18, "bold": True, "color": MUTED, "align": PP_ALIGN.CENTER}),
                  (placeholder, {"size": 13, "color": MUTED, "align": PP_ALIGN.CENTER})])
        return b
    from PIL import Image

    with Image.open(path) as im:
        iw, ih = im.size
    scale = min(w / iw, h / ih)
    pw, ph = int(iw * scale), int(ih * scale)
    return slide.shapes.add_picture(str(path), x + (w - pw) // 2, y + (h - ph) // 2, pw, ph)


def table(slide, x, y, w, rows, col_w, header_fill=C_INDEX, size=13, row_h=0.42):
    shp = slide.shapes.add_table(len(rows), len(rows[0]), x, y, w, Inches(row_h * len(rows)))
    tbl = shp.table
    for j, cw in enumerate(col_w):
        tbl.columns[j].width = Inches(cw)
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            cell = tbl.cell(i, j)
            cell.text = ""
            cell.margin_left = cell.margin_right = Inches(0.08)
            cell.fill.solid()
            cell.fill.fore_color.rgb = header_fill if i == 0 else (WHITE if i % 2 else C_GRAY)
            p = cell.text_frame.paragraphs[0]
            r = p.add_run()
            r.text = str(val)
            r.font.name = FONT
            r.font.size = Pt(size)
            r.font.bold = i == 0
            r.font.color.rgb = INK
    return tbl


def notes(slide, text: str) -> None:
    slide.notes_slide.notes_text_frame.text = text


# ---------------------------------------------------------------------------------------------- слайды
def build() -> tuple[Presentation, list[str]]:
    team = load_team()
    metrics = load_metrics()
    md: list[str] = ["# StudyRadar — содержимое слайдов", "",
                     "> Файл генерируется `make slides` вместе с `docs/StudyRadar_presentation.pptx`. Состав команды — "
                     "`docs/team.yaml`, скриншоты — `docs/screenshots/` (`make screenshots`), метрики — "
                     "`docs/eval_report.json` (`make eval`).", "",
                     "> **Честность:** скриншоты и метрики модели — только с настоящей LLM (Ollama). Результаты "
                     "FakeLLM (правила для тестов) в презентацию не попадают: скрипты отказываются их использовать.", ""]
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    blank = prs.slide_layouts[6]
    n = 0

    def new(title: str, sub: str | None = None):
        nonlocal n
        n += 1
        s = prs.slides.add_slide(blank)
        header(s, title, n, sub)
        md.extend([f"## {n}. {title}", ""] + ([f"_{sub}_", ""] if sub else []))
        return s

    # 1. Титул
    n += 1
    s = prs.slides.add_slide(blank)
    box(s, 0, 0, Inches(4.6), H, ACCENT, shape=MSO_SHAPE.RECTANGLE)
    text_box(s, Inches(0.55), Inches(2.6), Inches(3.8), Inches(2.0),
             [("📡", {"size": 66, "color": WHITE}), ("StudyRadar", {"size": 34, "bold": True, "color": WHITE})])
    text_box(s, Inches(5.1), Inches(1.6), Inches(7.8), Inches(2.4),
             [("StudyRadar — ИИ-агент для сортировки учебных сообщений и контроля дедлайнов",
               {"size": 32, "bold": True})], spacing=10)
    text_box(s, Inches(5.1), Inches(4.0), Inches(7.8), Inches(2.6), [
        (f"Команда {team['team_name']} · группа {team['group']}", {"size": 18, "bold": True, "color": ACCENT}),
        ("МГТУ им. Н. Э. Баумана · кафедра ИУ5", {"size": 16}),
        ("Кейс-чемпионат по созданию ИИ-агентов · дисциплина «Архитектура АСОИУ»", {"size": 16}),
        (str(date.today().year), {"size": 16, "color": MUTED}),
    ])
    notes(s, "Представляемся: команда SuperPuper, ИУ5-31Б. Наш агент StudyRadar читает учебные чаты, сам решает, "
             "что важно, раскладывает по предметам и ведёт доску дедлайнов.")
    md += ["## 1. Титульный слайд", "", "**StudyRadar — ИИ-агент для сортировки учебных сообщений и контроля дедлайнов**",
           "", f"Команда {team['team_name']} · {team['group']} · МГТУ им. Н. Э. Баумана, кафедра ИУ5 · "
               f"кейс-чемпионат по созданию ИИ-агентов, «Архитектура АСОИУ» · {date.today().year}", ""]

    # 2. Команда
    s = new("Состав команды", f"Все участники — группа {team['group']}")
    rows = [["ФИО", "Группа", "Роль в проекте"]] + [[m["name"], team["group"], m["role"]] for m in team["members"]]
    table(s, Inches(0.6), Inches(1.7), Inches(12.1), rows, [4.2, 1.4, 6.5], size=15, row_h=0.62)
    md += ["| ФИО | Группа | Роль |", "|---|---|---|"] + [f"| {m['name']} | {team['group']} | {m['role']} |"
                                                      for m in team["members"]] + [""]
    notes(s, "Каждый отвечал за свой модуль архитектуры — роли совпадают с блоками на схеме.")

    # 3. Постановка задачи
    s = new("Постановка задачи", "У студента 5+ учебных чатов — важное тонет во флуде")
    text_box(s, Inches(0.6), Inches(1.7), Inches(5.6), Inches(4.8), [
        ("Проблема", {"size": 19, "bold": True, "color": ACCENT}),
        "• дедлайны, задания, переносы и отмены теряются среди мемов, «спасибо» и болтовни",
        "• преподаватель пишет объявление серией коротких сообщений",
        "• сроки — в свободной форме: «до пятницы», «через 1,5 недели», «к следующей паре»",
        "• информация уточняется и меняется позже (аудитория, время, «можно» → «нельзя»)",
        ("Почему не поиск по ключевым словам", {"size": 19, "bold": True, "color": ACCENT}),
        "• слово есть, а смысла нет; смысл есть, а слова нет — нужен анализ по смыслу (LLM)",
    ], size=15)
    card(s, Inches(6.5), Inches(1.75), Inches(6.2), Inches(2.05), C_RED, "«физика — это боль»",
         ["Ключевые слова: «физика» → важное ❌", "StudyRadar: шум — предмет упомянут, учебной информации нет ✅"])
    card(s, Inches(6.5), Inches(4.0), Inches(6.2), Inches(2.25), C_GREEN, "«лабу перенесли на среду» (ответом)",
         ["Ключевые слова: предмет не найден ❌",
          "StudyRadar: перенос защиты ЛР2 по электротехнике с пт 30.10 на ср 04.11 — по сообщению, на которое "
          "ответили; дедлайн обновлён, а не продублирован ✅"])
    md += ["**Проблема.** Дедлайны, задания, переносы, отмены и объявления теряются во флуде нескольких учебных чатов. "
           "Преподаватели дробят объявления на серии сообщений, сроки пишут в свободной форме, информация уточняется позже.",
           "", "**Почему не ключевые слова:**", "",
           "- «физика — это боль» — слово «физика» есть, но сообщение не важно (агент: шум);",
           "- «лабу перенесли на среду» — названия предмета нет, но это перенос защиты ЛР2 по электротехнике "
           "(агент понимает по сообщению, на которое ответили, и обновляет существующий дедлайн).", ""]
    notes(s, "Главная мысль: ключевые слова ошибаются в обе стороны. Нужен анализ по смыслу и контекст переписки.")

    # 4. Что делает агент
    s = new("Что делает агент", "Не чатбот «спроси — ответь», а автономный агент: реагирует на события сам")
    steps = [("1", "Читает учебные чаты", "Telegram-бот: новые и отредактированные сообщения + история из JSON-экспорта", C_INDEX),
             ("2", "Понимает смысл", "LLM решает: важно ли, предмет, категория, срок (от даты сообщения)", C_LLM),
             ("3", "Раскладывает по темам", "пересылает в тему предмета чата-хаба с пометкой агента", C_ONLINE),
             ("4", "Ведёт доску дедлайнов", "одно закреплённое сообщение, сортировка по сроку, правится при изменениях", C_ONLINE),
             ("5", "Отслеживает изменения", "перенос, отмена, уточнение, редактирование → обновляет, а не дублирует", C_MCP),
             ("6", "Отвечает на вопросы", "/ask — ответ по переписке со ссылками на исходные сообщения (RAG)", C_STORE)]
    for i, (num, t, d, col) in enumerate(steps):
        cx = Inches(0.6 + (i % 3) * 4.15)
        cy = Inches(1.75 + (i // 3) * 2.45)
        card(s, cx, cy, Inches(3.95), Inches(2.2), col, f"{num}. {t}", d, title_size=17, body_size=14)
    md += [f"{num}. **{t}** — {d}." for num, t, d, _ in steps] + [
        "", "**Агент, а не чатбот:** сам реагирует на события, принимает решения, вызывает инструменты (MCP) и "
            "исправляет ранее внесённые данные.", ""]
    notes(s, "Подчеркнуть автономность: никто не просит агента — он сам решает и действует через инструменты.")

    # 5. Архитектура
    s = new("Архитектура", "Вариант А: индексация + RAG + инструменты через MCP")
    picture_fit(s, DOCS / "architecture.png", Inches(0.3), Inches(1.5), Inches(8.3), Inches(5.55), "docs/architecture.png")
    mods = [("Модуль 1 · Индексация", "сообщения и справочник предметов → bge-m3 → Chroma (messages, subjects, deadlines)", C_INDEX),
            ("Модуль 2 · RAG-пайплайн", "вопрос → гибридный поиск (Chroma + BM25, RRF) → обогащённый промпт → LLM → ответ со ссылками", C_ONLINE),
            ("Модуль 3 · Инструменты через MCP", "LLM сама вызывает 9 инструментов MCP-сервера (FastMCP, stdio): пересылка, дедлайны, доска", C_MCP),
            ("Оркестратор LangGraph", "ingest → prefilter → build_context → classify → act → verify → log", C_LLM)]
    for i, (t, d, col) in enumerate(mods):
        card(s, Inches(8.75), Inches(1.45 + i * 1.4), Inches(4.25), Inches(1.28), col, t, d, title_size=15, body_size=12)
    md += ["![Архитектура](architecture.png)", ""] + [f"- **{t}** — {d}." for t, d, _ in mods] + [""]
    notes(s, "Схема в стиле схемы преподавателя: те же цвета и нумерация блоков. Три модуля варианта А явно подписаны.")

    # 6. Модули в коде
    s = new("Три модуля варианта А в коде", "Каждый модуль легко найти по имени папки")
    cols = [("Модуль 1. Индексация", C_INDEX, [
        "src/study_radar/index/", "• парсер Telegram-экспорта: text строкой и массивом, rich_message, темы форума, edited, файлы",
        "• склейка «очередей» преподавателя (≤ 5 мин)", "• bge-m3 через Ollama → Chroma", "• идемпотентно: id = пост"]),
        ("Модуль 2. RAG", C_ONLINE, [
            "src/study_radar/retrieval/hybrid.py", "src/study_radar/agent/ask.py",
            "• векторный поиск + BM25 → RRF", "• фильтры: предмет, чат, даты", "• промпт требует ссылки [n]; нет данных — "
            "«в переписке нет информации»"]),
        ("Модуль 3. MCP", C_MCP, [
            "src/study_radar/mcp_server/server.py", "src/study_radar/agent/graph.py (узел act)",
            "• FastMCP, stdio, 9 инструментов", "• клиент: langchain-mcp-adapters", "• LLM сама выбирает инструменты;",
            "  2 неудачные попытки → фолбэк через те же MCP-инструменты"])]
    for i, (t, col, body) in enumerate(cols):
        card(s, Inches(0.6 + i * 4.15), Inches(1.7), Inches(3.95), Inches(5.0), col, t, body, body_size=13)
        md += [f"**{t}**", ""] + [f"- {b.strip('• ').strip()}" for b in body] + [""]
    notes(s, "Можно открыть репозиторий и показать папки. Модуль 3 — реальный MCP по протоколу (JSON-RPC через stdio).")

    # 7. Технологии
    s = new("Технологии", "Всё работает локально на ноутбуке; облачные LLM не используются")
    tech = [["Компонент", "Выбор", "Почему"],
            ["LLM", "Ollama + Qwen3 8B / 4B", "локально; 16 ГБ RAM → 8B, 8 ГБ → 4B (одна строка в .env); tool calling, JSON"],
            ["Эмбеддинги", "bge-m3 (Ollama)", "мультиязычная, хорошо понимает русский; тот же Ollama"],
            ["Векторная БД", "Chroma (persistent)", "локально, без сервера, фильтры по метаданным"],
            ["Retriever", "Chroma + rank_bm25 → RRF", "смысл + точные слова (номера ЛР, аудитории)"],
            ["Оркестратор", "LangGraph", "явный граф узлов агента, условные переходы"],
            ["MCP", "FastMCP (stdio) + langchain-mcp-adapters", "стандартный протокол инструментов"],
            ["Telegram", "aiogram 3 (бот)", "без userbot — не рискуем личным аккаунтом"],
            ["Хранилище", "SQLite", "дедлайны, история изменений, решения агента"],
            ["Интерфейс", "Streamlit + Typer CLI", "дашборд для демо и трассировка агента"]]
    table(s, Inches(0.6), Inches(1.6), Inches(12.1), tech, [2.0, 3.8, 6.3], size=13, row_h=0.5)
    md += ["| " + " | ".join(r) + " |" for r in tech[:1]] + ["|---|---|---|"] + ["| " + " | ".join(r) + " |" for r in tech[1:]] + [""]
    notes(s, "Стек — из рекомендованного преподавателем. Qwen3 — с выключенным thinking ради скорости.")

    # 8. Ход разработки
    s = new("Ход разработки", "Итерации: после каждой — рабочее состояние и зелёные тесты")
    its = [("1", "MVP-ядро", "парсер экспорта, склейка очередей, SQLite, классификация, доска"),
           ("2", "Модули А", "Chroma, гибридный RAG, MCP-сервер, LangGraph + фолбэк"),
           ("3", "Обновления", "переносы, редактирования, дедупликация, история"),
           ("4", "Telegram", "бот на aiogram, темы хаба, закреп-доска, /ask"),
           ("5", "Интерфейс", "Streamlit: лента, хаб, дедлайны, трассировка"),
           ("6", "Оценка", "195 размеченных постов, бейзлайн по ключевым словам"),
           ("7", "Документация", "README, схема, сценарий демо, презентация")]
    for i, (num, t, d) in enumerate(its):
        x = Inches(0.6 + i * 1.77)
        box(s, x, Inches(2.0), Inches(1.6), Inches(0.75), ACCENT)
        text_box(s, x, Inches(2.05), Inches(1.6), Inches(0.65), f"{num}. {t}", size=14, bold=True, color=WHITE,
                 align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        text_box(s, x, Inches(2.9), Inches(1.6), Inches(2.4), d, size=12, align=PP_ALIGN.CENTER)
    text_box(s, Inches(0.6), Inches(5.3), Inches(12), Inches(1.5), [
        "Код покрыт тестами (pytest): парсер, даты, склейка, MCP через stdio, перенос/дубль/редактирование, фолбэк, RAG.",
        "Для тестов без Ollama и Telegram — заглушки FakeLLM / HashEmbedder / FakeHub (только для проверки кода)."],
        size=14, color=MUTED)
    md += [f"{num}. **{t}** — {d}." for num, t, d in its] + [""]
    notes(s, "Разработка шла итерациями; на каждом шаге — тесты.")

    # 9. Трудности
    s = new("Трудности и решения", "Из журнала разработки (docs/devlog.md)")
    diff = [["Трудность", "Решение"],
            ["Преподаватель дробит объявление на 5–7 сообщений", "склейка «очередей» (автор, тема, ≤ 5 мин); в боте — пауза 45 с"],
            ["Сроки словами: «до пятницы», «1,5 недели»", "расчёт от даты сообщения + перепроверка кодом и dateparser"],
            ["«05.11» читалось как время 05:11, «16:00.» — не время", "отдельный разбор дат и времени, тесты на каждый случай"],
            ["«физ» находилось внутри «физру»", "проверка целого слова и списка «ложных друзей» предмета"],
            ["8B-модель может ошибиться в вызове инструмента (не тот, битые аргументы)", "проверка плана, 2 попытки, детерминированный фолбэк через MCP"],
            ["Telegram не умеет пересортировать сообщения", "одно закреплённое сообщение-доска, editMessageText"],
            ["Один дедлайн пишут в двух чатах", "дедупликация: предмет + похожесть названия + дата + решение LLM"],
            ["Бот не видит сообщения в группах", "privacy mode → Disable в @BotFather, проверка в make doctor"],
            ["Ноутбук без GPU: qwen3:8b на CPU слишком медленный", "qwen3:4b для CPU; модели выгружаются через 5 мин; лимит RAM для WSL"]]
    table(s, Inches(0.6), Inches(1.5), Inches(12.1), diff, [5.6, 6.5], size=13, row_h=0.52)
    md += ["| Трудность | Решение |", "|---|---|"] + [f"| {a} | {b} |" for a, b in diff[1:]] + [""]
    notes(s, "Это реальные проблемы из devlog. После прогона на ноутбуке добавить 1–2 примера ошибок модели из "
             "docs/eval_report.md (раздел «Ошибки модели»).")

    # 10–14. Экранные формы
    screens = [
        ("Экранная форма: лента решений", "Каждое сообщение и решение агента: категория, предмет, уверенность, причина",
         "01_feed.png", "make simulate → make screenshots: вкладка «Лента»"),
        ("Экранная форма: чат-хаб и доска дедлайнов", "Закреплённая доска, отсортированная по сроку; переносы — «было: …»",
         "02_hub_board.png", "вкладка «Хаб» → тема «📅 Дедлайны»"),
        ("Экранная форма: тема предмета", "Пересланное сообщение + пометка агента; при переносе старая дата зачёркнута",
         "03_hub_subject.png", "вкладка «Хаб» → «⚡ Электротехника»"),
        ("Экранная форма: трассировка агента (1/2)", "Сообщение → префильтр → RAG-контекст → JSON-классификация",
         "05_trace.png", "вкладка «Трассировка агента» (перенос ЛР2)"),
        ("Экранная форма: трассировка агента (2/2)", "LLM сама вызывает инструменты MCP-сервера (stdio) → verify",
         "05b_trace_mcp.png", "вкладка «Трассировка агента», раздел «MCP-вызовы»"),
        ("Экранная форма: дедлайны и история", "Таблица по сроку и история изменений: было → стало, по какому сообщению",
         "04_deadlines.png", "вкладка «Дедлайны»"),
        ("Экранная форма: вопрос по переписке (RAG)", "Ответ со ссылками на исходные сообщения",
         "06_ask.png", "вкладка «Спросить»: «Когда сдавать лабу по элтеху?»"),
    ]
    test_mode = (SHOTS / ".fake").exists()
    mode_note = ("Тестовый режим: вместо LLM — FakeLLM (правила), отсюда жёлтая плашка. "
                 "С моделью Qwen3 через Ollama — make simulate.")
    md += ["## Экранные формы", "", "Снимаются автоматически: `make simulate` (настоящая модель) → `make screenshots`. "
           "Если нужен настоящий Telegram — см. `docs/demo_script.md` (кадры tg_*.png).", ""]
    if test_mode:
        md += [f"> **Важно:** {mode_note}", ""]
    for title, sub, fname, hint in screens:
        s = new(title, sub)
        bottom = Inches(5.15) if test_mode else Inches(5.4)
        if fname == "05b_trace_mcp.png":
            picture_fit(s, shot(fname), Inches(0.5), Inches(1.5), Inches(6.9), bottom, hint)
            text_box(s, Inches(7.7), Inches(1.7), Inches(5.1), Inches(4.8), [
                ("Модуль 3 в действии", {"size": 19, "bold": True, "color": ACCENT}),
                "Сообщение «Чемпионат будет проходить в аудитории 501ю, 30 октября с 16:00» — уточнение известного "
                "события.",
                "1. forward_to_topic — переслать в тему «Архитектура АСОИУ» с пометкой агента",
                "2. update_deadline — время 15:55 → 16:00, место → ауд. 501ю; история сохранена",
                "3. refresh_deadlines_board — перерисовать закреплённую доску",
                "Verify проверяет эффекты; если LLM ошиблась дважды — фолбэк через те же MCP-инструменты.",
            ], size=14, spacing=8)
        else:
            picture_fit(s, shot(fname), Inches(0.6), Inches(1.5), Inches(12.1), bottom, hint)
        if test_mode:
            text_box(s, Inches(0.6), Inches(6.68), Inches(12.1), Inches(0.35), "⚠ " + mode_note, size=10,
                     color=RGBColor(0x8A, 0x5A, 0x00))
        md += [f"![{title}](screenshots/{fname})", f"Кадр: `docs/screenshots/{fname}` — {hint}.", ""]
    tg = [p for p in (shot("tg_hub.png"), shot("tg_board.png"), shot("tg_ask.png")) if p]
    if tg:
        s = new("Работа в настоящем Telegram", "Режим бота: темы хаба, закреп-доска, /ask")
        w = Inches(12.1 / len(tg) - 0.15)
        for i, p in enumerate(tg):
            picture_fit(s, p, Inches(0.6) + i * (w + Inches(0.15)), Inches(1.55), w, Inches(5.4), "")
        md += [f"![Telegram]({'screenshots/' + p.name})" for p in tg] + [""]

    # Результаты
    s = new("Результаты", "195 размеченных постов из 5 синтетических чатов (data/eval/labeled.jsonl)")
    b = baseline_only()
    m = (metrics or {}).get("metrics")
    fmt = (lambda v: f"{v:.2f}") if m else (lambda v: "—")
    rows = [["Метрика", "StudyRadar (LLM)", "Ключевые слова"],
            ["F1 важности", fmt(m["f1"]) if m else "—", f"{b['f1']:.2f}"],
            ["Precision / Recall", f"{m['precision']:.2f} / {m['recall']:.2f}" if m else "—",
             f"{b['precision']:.2f} / {b['recall']:.2f}"],
            ["Точность предмета", fmt(m["subject_acc"]) if m else "—", f"{b['subject_acc']:.2f}"],
            ["Точность категории", fmt(m["category_acc"]) if m else "—", f"{b['category_acc']:.2f}"],
            ["Точность даты (день)", fmt(m["date_acc"]) if m else "—", f"{b['date_acc']:.2f}"]]
    table(s, Inches(0.6), Inches(1.7), Inches(7.4), rows, [3.0, 2.2, 2.2], size=15, row_h=0.6)
    if m:
        side = [(f"{metrics['model']}", {"size": 15, "bold": True, "color": ACCENT}),
                (metrics["hardware"], {"size": 11, "color": MUTED}),
                (f"MCP-действия без фолбэка: {m['mcp_no_fallback']:.0%}", {"size": 16, "bold": True}),
                (f"Корректных вызовов инструментов LLM: {m['mcp_call_ok']:.0%}", {"size": 15}),
                (f"Средняя задержка: {m['latency_avg_s']:.1f} с на пост", {"size": 15}),
                (f"Дедлайнов с несколькими источниками (дубли объединены): {m['deadlines_multi_source']}", {"size": 14})]
    else:
        side = [("Метрики модели появятся после `make eval`", {"size": 16, "bold": True, "color": ACCENT}),
                ("Считаются только на настоящей LLM через Ollama — на ноутбуке команды. Бейзлайн по ключевым словам "
                 "посчитан уже сейчас (он не использует LLM).", {"size": 14, "color": MUTED}),
                ("Видно, почему ключевые слова не работают: низкая точность предмета и много ложных «важных».",
                 {"size": 14})]
    text_box(s, Inches(8.3), Inches(1.7), Inches(4.5), Inches(5.0), side, spacing=10)
    md += ["| " + " | ".join(r) + " |" for r in rows[:1]] + ["|---|---|---|"] + ["| " + " | ".join(r) + " |"
                                                                             for r in rows[1:]] + [""]
    if m:
        md += [f"- Модель: {metrics['model']}; железо: {metrics['hardware']}",
               f"- MCP-действия без фолбэка: {m['mcp_no_fallback']:.0%}; корректных вызовов LLM: {m['mcp_call_ok']:.0%}",
               f"- Средняя задержка: {m['latency_avg_s']:.1f} с на пост", ""]
    else:
        md += ["_Метрики модели появятся после `make eval` на ноутбуке (только настоящая LLM)._", ""]
    notes(s, "Сравнение с бейзлайном показывает, зачем нужна LLM: ключевые слова путают «физру» с физикой и не "
             "видят переносов без названия предмета.")

    # Как запустить + репозиторий
    s = new("Как запустить и где код", "Локально, на обычном ноутбуке")
    text_box(s, Inches(0.6), Inches(1.7), Inches(7.0), Inches(5.0), [
        ("Демо за 10 минут", {"size": 19, "bold": True, "color": ACCENT}),
        ("ollama pull qwen3:8b && ollama pull bge-m3", {"size": 15, "font": "Consolas"}),
        ("make setup && make doctor", {"size": 15, "font": "Consolas"}),
        ("make simulate      # демо-сценарий настоящей моделью", {"size": 15, "font": "Consolas"}),
        ("make ui            # http://localhost:8501", {"size": 15, "font": "Consolas"}),
        ("make bot           # режим Telegram-бота", {"size": 15, "font": "Consolas"}),
        ("8 ГБ RAM: LLM_MODEL=qwen3:4b в .env", {"size": 14, "color": MUTED}),
    ])
    box(s, Inches(8.0), Inches(2.0), Inches(4.7), Inches(2.6), C_INDEX)
    text_box(s, Inches(8.2), Inches(2.2), Inches(4.3), Inches(2.3), [
        ("Репозиторий", {"size": 18, "bold": True}), (team["repo"], {"size": 15, "color": ACCENT}),
        ("README → «Как запустить демо за 10 минут»", {"size": 13, "color": MUTED})])
    md += ["```bash", "ollama pull qwen3:8b && ollama pull bge-m3", "make setup && make doctor", "make simulate",
           "make ui", "make bot", "```", "", f"**Репозиторий:** {team['repo']}", ""]

    # Спасибо
    n += 1
    s = prs.slides.add_slide(blank)
    box(s, 0, 0, W, H, ACCENT, shape=MSO_SHAPE.RECTANGLE)
    text_box(s, Inches(0.8), Inches(2.6), Inches(11.7), Inches(2.5), [
        ("Спасибо за внимание!", {"size": 44, "bold": True, "color": WHITE, "align": PP_ALIGN.CENTER}),
        ("Готовы ответить на вопросы", {"size": 22, "color": WHITE, "align": PP_ALIGN.CENTER}),
        (team["repo"], {"size": 16, "color": WHITE, "align": PP_ALIGN.CENTER})])
    md += [f"## {n}. Спасибо за внимание!", "", "Вопросы.", ""]
    return prs, md


def main() -> None:
    prs, md = build()
    out = DOCS / "StudyRadar_presentation.pptx"
    prs.save(out)
    (DOCS / "presentation.md").write_text("\n".join(md), encoding="utf-8")
    shots = sorted(p.name for p in SHOTS.glob("*.png")) if SHOTS.exists() else []
    print(f"✓ {out.relative_to(ROOT)} ({len(prs.slides)} слайдов); скриншотов: {len(shots)}; "
          f"метрики модели: {'есть' if load_metrics() else 'нет (make eval)'}")
    print("✓ docs/presentation.md")


if __name__ == "__main__":
    main()
