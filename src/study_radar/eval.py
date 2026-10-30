"""Оценка качества агента на размеченных синтетических фикстурах + бейзлайн «поиск по ключевым словам».

Фикстуры проигрываются через ВЕСЬ пайплайн в хронологическом порядке (как живой поток), чтобы RAG-контекст,
переносы и дубли работали честно. Метрики: P/R/F1 важности, точность предмета и категории, точность дат
(совпадение дня), доля MCP-действий без фолбэка, доля корректных вызовов LLM, средняя задержка.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from study_radar.agent.dates import default_time_for, parse_due
from study_radar.config import ROOT, Settings
from study_radar.ingest.telegram_export import load_export

KEYWORDS = re.compile(r"(лаб|\bдз\b|\bрк\b|\bкр\b|сдач|сдать|защит|дедлайн|срок|перенес|перенос|отмен|не будет|"
                      r"аудитор|задани|экзамен|зач[её]т|консультац|контест|отч[её]т|методичк|\[файл\])", re.I)


# ---------------------------------------------------------------------------------------------- бейзлайн
def baseline_predict(settings: Settings, text: str, date: datetime) -> dict[str, Any]:
    """Наивный поиск по ключевым словам и алиасам предметов (без понимания смысла)."""
    low = text.lower()
    subject = None
    for key, s in settings.subjects.items():
        if any(a.lower() in low for a in s.aliases):   # подстрока: «физ» найдётся и в «физра»
            subject = key
            break
    important = bool(KEYWORDS.search(low)) or subject is not None
    if not important:
        return {"is_important": False, "subject": None, "category": "noise", "due": None}
    if re.search(r"перенес|перенос|продлева|продлил", low):
        cat = "deadline_update"
    elif re.search(r"отмен|не будет", low):
        cat = "cancellation"
    elif "[файл]" in low:
        cat = "materials"
    elif re.search(r"защит|\bрк\b|экзамен|консультац|чемпионат", low):
        cat = "event"
    elif re.search(r"сдать|сдач|срок|дедлайн|\bдо\b", low):
        cat = "deadline"
    elif re.search(r"задани|\bдз\b", low):
        cat = "assignment"
    else:
        cat = "announcement"
    due = parse_due(text, date, settings.tz, default_time_for(cat))
    return {"is_important": True, "subject": subject, "category": cat,
            "due": due.dt.date().isoformat() if due.ok else None}


# ---------------------------------------------------------------------------------------------- метрики
def score(rows: list[dict[str, Any]]) -> dict[str, float]:
    tp = sum(1 for r in rows if r["gold"]["is_important"] and r["pred"]["is_important"])
    fp = sum(1 for r in rows if not r["gold"]["is_important"] and r["pred"]["is_important"])
    fn = sum(1 for r in rows if r["gold"]["is_important"] and not r["pred"]["is_important"])
    tn = len(rows) - tp - fp - fn
    p = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * rc / (p + rc) if p + rc else 0.0
    imp = [r for r in rows if r["gold"]["is_important"]]
    subj = sum(1 for r in imp if r["pred"]["subject"] == r["gold"]["subject"]) / len(imp) if imp else 0.0
    cat = sum(1 for r in imp if r["pred"]["category"] in [r["gold"]["category"], *r.get("alt", [])]) / len(imp) \
        if imp else 0.0
    dated = [r for r in imp if r["gold"].get("due")]
    date_acc = sum(1 for r in dated if r["pred"].get("due") == r["gold"]["due"]) / len(dated) if dated else 0.0
    return {"precision": round(p, 3), "recall": round(rc, 3), "f1": round(f1, 3),
            "accuracy": round((tp + tn) / len(rows), 3) if rows else 0.0, "subject_acc": round(subj, 3),
            "category_acc": round(cat, 3), "date_acc": round(date_acc, 3), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "n_important": len(imp), "n_dated": len(dated)}


def _pred_from_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if state is None or state.get("route") == "dropped" or state.get("result") is None:
        return {"is_important": False, "subject": None, "category": "noise", "due": None}
    c = state["result"].classification
    return {"is_important": c.is_important, "subject": c.subject, "category": c.category.value,
            "due": c.due_at.date().isoformat() if c.due_at else None, "confidence": c.confidence,
            "reason": c.reason}


# ---------------------------------------------------------------------------------------------- прогон
async def run_eval(settings: Settings, labels_path: Path, fixtures_dir: Path, out: Path, limit: int = 0,
                   act_sample: int = 25, console: Console | None = None) -> Path:
    from study_radar.agent.graph import AgentRuntime
    from study_radar.doctor import hardware_summary
    from study_radar.services import Services
    from study_radar.simulate import Player

    console = console or Console()
    labels = [json.loads(line) for line in labels_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = {r["post_id"]: r for r in labels}

    work = ROOT / "data/runtime/eval"
    shutil.rmtree(work, ignore_errors=True)
    s = settings.model_copy(deep=True)
    s.paths.db = str(work / "eval.sqlite")
    s.paths.chroma = str(work / "chroma")
    s.hub.journal = False
    user_act_mode = s.agent.act_mode
    sv = Services(s, "fake")

    messages = []
    for path in sorted(fixtures_dir.glob("*.json")):
        for chat in load_export(path, s.tz):
            sv.db.upsert_chat(chat.chat_id, chat.title, chat.kind, chat.is_forum)
            messages += chat.messages
    messages.sort(key=lambda m: (m.date, m.chat_id, m.message_id))
    if limit:
        keep_ids = set(sorted(gold, key=lambda pid: gold[pid]["date"])[:limit])
        last_date = max(gold[p]["date"] for p in keep_ids)
        messages = [m for m in messages if m.date.isoformat() <= last_date]
    n_total = sum(1 for m in messages if not m.is_service)
    console.print(f"[bold]Оценка:[/] {len(labels)} размеченных постов, сообщений в прогоне: {n_total}; "
                  f"LLM: {sv.llm.label}; act: {user_act_mode}"
                  + (f" (LLM вызывает инструменты для первых {act_sample} важных, дальше — детерминированно)"
                     if user_act_mode == "llm" and act_sample else ""))

    results: dict[str, dict[str, Any]] = {}
    acted = {"n": 0}
    t_start = time.perf_counter()

    def on_result(state, trigger):
        pid = state["post"].post_id
        results[pid] = state
        if state.get("route") in {"act", "review"}:
            acted["n"] += 1
            if user_act_mode == "llm" and act_sample and acted["n"] >= act_sample:
                s.agent.act_mode = "deterministic"
        g = gold.get(pid)
        pred = _pred_from_state(state)
        ok = g is not None and g["label"]["is_important"] == pred["is_important"]
        mark = "[green]✓[/]" if ok else "[red]✗[/]"
        ms = sum((state.get("timings") or {}).values())
        console.print(f"{mark} {len(results):>3} {state['post'].chat_title[:14]:<14} "
                      f"«{state['post'].text[:50].replace(chr(10), ' ')}» → {pred['category']}"
                      f"{'/' + str(pred['subject']) if pred['is_important'] else ''} ({ms / 1000:.1f} с)")

    async with AgentRuntime(s, "fake", sv) as rt:
        player = Player(rt, on_result)
        for m in messages:
            await player.feed(m)
        await player.flush_all()
    s.agent.act_mode = user_act_mode
    wall = time.perf_counter() - t_start

    # ---------------- сопоставление с разметкой
    rows, base_rows = [], []
    for pid, g in gold.items():
        if pid not in results:
            continue
        row = {"post_id": pid, "text": g["text"], "chat": g["chat"], "tag": g.get("tag"), "gold": g["label"],
               "alt": g.get("category_alt", []), "pred": _pred_from_state(results[pid]),
               "route": results[pid].get("route")}
        rows.append(row)
        date = datetime.fromisoformat(g["date"])
        base_rows.append({**row, "pred": baseline_predict(s, g["text"], date)})
    metrics = score(rows)
    baseline = score(base_rows)

    decisions = sv.db.decisions(100000)
    acts = [d for d in decisions if d["route"] in {"act", "review"}]
    llm_acts = [d for d in acts if d["fallback"] or any(c["by"] == "llm" for c in json.loads(d["tool_calls"] or "[]"))]
    llm_calls = [c for d in acts for c in json.loads(d["tool_calls"] or "[]") if c["by"] == "llm"]
    lat_all = [d["latency_ms"] for d in decisions if d["latency_ms"] is not None]
    lat_llm = [d["latency_ms"] for d in decisions if d["route"] != "dropped" and d["latency_ms"] is not None]
    metrics.update({
        "mcp_no_fallback": round(sum(1 for d in llm_acts if not d["fallback"]) / len(llm_acts), 3) if llm_acts else 0.0,
        "mcp_llm_acts": len(llm_acts),
        "mcp_call_ok": round(sum(1 for c in llm_calls if c["ok"]) / len(llm_calls), 3) if llm_calls else 0.0,
        "mcp_llm_calls": len(llm_calls),
        "latency_avg_s": round(sum(lat_llm) / len(lat_llm) / 1000, 2) if lat_llm else 0.0,
        "latency_all_avg_s": round(sum(lat_all) / len(lat_all) / 1000, 2) if lat_all else 0.0,
        "prefilter_dropped": sum(1 for r in rows if r["route"] == "dropped"),
        "prefilter_lost_important": sum(1 for r in rows if r["route"] == "dropped" and r["gold"]["is_important"]),
        "wall_s": round(wall),
    })
    deadlines = sv.db.all_deadlines()
    metrics["deadlines_total"] = len(deadlines)
    metrics["deadlines_multi_source"] = sum(1 for d in deadlines if len(d.sources) > 1)

    tags: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r, b in zip(rows, base_rows):
        if r["tag"]:
            tags[r["tag"]].append((r, b))

    def correct(r):
        g, p = r["gold"], r["pred"]
        if g["is_important"] != p["is_important"]:
            return False
        return not g["is_important"] or (p["subject"] == g["subject"] and p["category"] in [g["category"], *r["alt"]])

    tag_table = [{"tag": t, "n": len(v), "llm": sum(correct(r) for r, _ in v), "baseline": sum(correct(b) for _, b in v)}
                 for t, v in sorted(tags.items())]
    errors = [{"text": r["text"][:90], "gold": f"{r['gold']['category']}/{r['gold']['subject']}/{r['gold'].get('due')}",
               "pred": f"{r['pred']['category']}/{r['pred']['subject']}/{r['pred'].get('due')}",
               "why": (r["pred"].get("reason") or r["route"] or "")[:120]}
              for r in rows if not correct(r) or (r["gold"].get("due") and r["pred"].get("due") != r["gold"]["due"])]

    report = {
        "model": sv.llm.label if not sv.llm.is_fake else "FakeLLM (правила, НЕ модель)",
        "embedder": sv.embedder.name, "hardware": hardware_summary(), "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "act_mode": user_act_mode, "act_sample": act_sample, "n_posts": len(rows), "metrics": metrics,
        "baseline": baseline, "tags": tag_table, "errors": errors[:30], "fake": sv.llm.is_fake,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    out.write_text(render_md(report), encoding="utf-8")
    console.print(render_console(report))
    return out


TAG_RU = {"fizra": "«физра» ≠ физика", "subject_noise": "предмет упомянут, но это шум", "team": "названия команд",
          "sarcasm": "сарказм", "meme": "мемы", "numbers": "шум с числами/временем", "answer": "ответ на вопрос",
          "burst": "серия сообщений (склейка)", "dedup": "дубль из другого чата", "edited": "отредактированное",
          "reply_transfer": "перенос ответом без предмета", "location_update": "уточнение аудитории",
          "condition_change": "смена условия", "no_date": "срок без даты («к следующей паре»)"}


def render_md(r: dict[str, Any]) -> str:
    m, b = r["metrics"], r["baseline"]
    warn = "\n> ⚠️ **FakeLLM** — это детерминированные правила для проверки кода, а не модель. Не использовать в презентации.\n" \
        if r.get("fake") else ""
    lines = [
        "# Отчёт об оценке StudyRadar", warn,
        f"- **Модель:** {r['model']} · эмбеддинги: {r['embedder']}",
        f"- **Железо:** {r['hardware']}",
        f"- **Дата:** {r['date']} · постов в датасете: {r['n_posts']} (data/eval/labeled.jsonl, синтетические чаты)",
        f"- **Режим act:** {r['act_mode']}" + (f" (LLM сама вызывает MCP-инструменты для первых {r['act_sample']} "
                                               f"важных постов)" if r["act_mode"] == "llm" else ""),
        f"- **Время прогона:** {m['wall_s'] // 60} мин {m['wall_s'] % 60} с", "",
        "## Качество: StudyRadar vs поиск по ключевым словам", "",
        "| Метрика | StudyRadar (LLM) | Бейзлайн: ключевые слова |", "|---|---|---|",
        f"| Precision (важность) | **{m['precision']:.2f}** | {b['precision']:.2f} |",
        f"| Recall (важность) | **{m['recall']:.2f}** | {b['recall']:.2f} |",
        f"| F1 (важность) | **{m['f1']:.2f}** | {b['f1']:.2f} |",
        f"| Точность предмета (на важных) | **{m['subject_acc']:.2f}** | {b['subject_acc']:.2f} |",
        f"| Точность категории (на важных) | **{m['category_acc']:.2f}** | {b['category_acc']:.2f} |",
        f"| Точность даты, совпадение дня ({m['n_dated']} сроков) | **{m['date_acc']:.2f}** | {b['date_acc']:.2f} |",
        "", f"Матрица важности (LLM): TP={m['tp']}, FP={m['fp']}, FN={m['fn']}, TN={m['tn']}; "
            f"бейзлайн: TP={b['tp']}, FP={b['fp']}, FN={b['fn']}, TN={b['tn']}.", "",
        "## Агент и MCP", "",
        f"- Действий через LLM (tool calling): {m['mcp_llm_acts']}; **без фолбэка: {m['mcp_no_fallback']:.0%}**",
        f"- Вызовов инструментов от LLM: {m['mcp_llm_calls']}; **корректных: {m['mcp_call_ok']:.0%}**",
        f"- Дедлайнов создано: {m['deadlines_total']}, из них с несколькими источниками (дедупликация): "
        f"{m['deadlines_multi_source']}",
        f"- Средняя задержка на пост, прошедший префильтр: **{m['latency_avg_s']:.1f} с**; "
        f"по всем постам: {m['latency_all_avg_s']:.1f} с",
        f"- Префильтр отсеял {m['prefilter_dropped']} постов, из них важных (потеряно): {m['prefilter_lost_important']}",
        "", "## Сложные случаи (верно / всего)", "", "| Случай | n | StudyRadar | Ключевые слова |", "|---|---|---|---|",
    ]
    lines += [f"| {TAG_RU.get(t['tag'], t['tag'])} | {t['n']} | {t['llm']} | {t['baseline']} |" for t in r["tags"]]
    if r["errors"]:
        lines += ["", "## Ошибки модели (материал для слайда «Трудности»)", "",
                  "| Текст | Разметка | Предсказание | Объяснение модели |", "|---|---|---|---|"]
        lines += [f"| {e['text'].replace('|', '/')} | {e['gold']} | {e['pred']} | {e['why'].replace('|', '/')} |"
                  for e in r["errors"]]
    lines += ["", "_Отчёт сгенерирован командой `make eval` (study-radar eval)._", ""]
    return "\n".join(lines)


def render_console(r: dict[str, Any]) -> str:
    m, b = r["metrics"], r["baseline"]
    return (f"\n[bold]Итог[/] ({r['model']}):\n"
            f"  F1 важности: {m['f1']:.2f} (ключевые слова: {b['f1']:.2f}) · P {m['precision']:.2f} / R {m['recall']:.2f}\n"
            f"  предмет: {m['subject_acc']:.2f} ({b['subject_acc']:.2f}) · категория: {m['category_acc']:.2f} "
            f"({b['category_acc']:.2f}) · дата: {m['date_acc']:.2f} ({b['date_acc']:.2f})\n"
            f"  MCP без фолбэка: {m['mcp_no_fallback']:.0%} · корректных вызовов LLM: {m['mcp_call_ok']:.0%} · "
            f"задержка: {m['latency_avg_s']:.1f} с/пост")
