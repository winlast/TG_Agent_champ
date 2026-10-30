"""CLI StudyRadar (Typer): doctor, index, simulate, bot, ask, eval, ui, board, reset."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from study_radar.config import ROOT, Settings, load_settings, setup_logging

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="StudyRadar — ИИ-агент для учебных Telegram-чатов (локально, Ollama).")
console = Console()

FAKE_WARNING = ("[bold yellow]⚠ Режим FakeLLM + HashEmbedder: это правила для проверки кода, а НЕ работа модели. "
                "Не используйте эти результаты в презентации.[/]")


def _settings(fake: bool = False, act_mode: str | None = None, workspace: str = "demo") -> Settings:
    s = load_settings()
    s.workspace = workspace
    setup_logging(s.log_level)
    if fake:
        s.llm.backend = "fake"
        s.embeddings.backend = "hash"
        s.prefilter.threshold = 0.2
    if act_mode:
        s.agent.act_mode = act_mode
    return s


def _check_ollama_or_exit(s: Settings) -> None:
    if s.llm.backend != "ollama":
        return
    from study_radar.doctor import ollama_status

    st = ollama_status(s)
    if not st["reachable"]:
        console.print(f"[red]✗ Ollama недоступна по адресу {s.llm.host}.[/] Запустите Ollama (`ollama serve` или "
                      f"приложение Ollama) и повторите. Проверка всего окружения: [bold]make doctor[/].")
        raise typer.Exit(1)
    missing = [m for m in (s.llm.model, s.embeddings.model) if not st["has"](m)]
    if missing:
        console.print("[red]✗ Не скачаны модели:[/] " + ", ".join(missing))
        for m in missing:
            console.print(f"   ollama pull {m}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------------------------- doctor
@app.command()
def doctor(deep: bool = typer.Option(False, help="Дополнительно замерить скорость модели (≈1 мин).")) -> None:
    """Проверить окружение: Python, Ollama, модели, токен бота, доступ к хабу."""
    from study_radar.doctor import run_doctor

    s = load_settings()
    s.workspace = "live"   # хаб, заданный через /sethub, хранится в рабочем пространстве бота
    setup_logging("WARNING")
    ok = asyncio.run(run_doctor(s, console, deep=deep))
    raise typer.Exit(0 if ok else 1)


# ---------------------------------------------------------------------------------------------- index
@app.command()
def index(export: list[Path] = typer.Option(..., "--export", "-e", help="Telegram JSON-экспорт (result.json)."),
          fake: bool = typer.Option(False, help="Hash-эмбеддинги вместо Ollama (только для проверки)."),
          workspace: str = typer.Option("live", help="live — для бота (по умолчанию), demo — к демо-данным.")) -> None:
    """Модуль 1: загрузить историю из JSON-экспорта в SQLite и векторную БД (идемпотентно)."""
    s = _settings(fake, workspace=workspace)
    if not fake:
        _check_ollama_or_exit(s)
    from study_radar.services import Services

    sv = Services(s)
    for path in export:
        st = sv.indexer.index_export(path)
        console.print(f"[green]✓[/] {path}: чатов {st.chats}, сообщений {st.messages}, постов {st.posts}, "
                      f"в индекс добавлено {st.indexed}, уже были {st.skipped}")
    console.print(f"Документов в коллекции messages: {sv.store.count('messages')}, subjects: {sv.store.count('subjects')}")


# ---------------------------------------------------------------------------------------------- simulate
def _print_result(state, trigger: str) -> None:
    from study_radar.models import CATEGORY_EMOJI, CATEGORY_RU

    post = state["post"]
    res = state.get("result")
    route = state.get("route", "dropped")
    text = post.text.replace("\n", " ")
    text = text[:60] + ("…" if len(text) > 60 else "")
    when = post.date.strftime("%a %d.%m %H:%M")
    if route == "dropped":
        verdict = f"[dim]· префильтр: {state['prefilter']['reason']}[/]"
    else:
        c = res.classification
        color = {"skip": "dim", "act": "green", "review": "yellow"}[route]
        verdict = f"[{color}]{CATEGORY_EMOJI[c.category]} {CATEGORY_RU[c.category]}"
        if c.is_important:
            verdict += f" · {c.subject or 'общее'} · {c.confidence:.2f}"
        verdict += "[/]"
        calls = state.get("tool_calls") or []
        if calls:
            verdict += " [cyan]MCP:[/] " + ", ".join(
                x["tool"] + ("[red]✗[/]" if not x["ok"] else "") + ("[magenta]⟲[/]" if x["by"] == "fallback" else "")
                for x in calls)
    edit = "[magenta]✏️ [/]" if trigger == "edit" else ""
    ms = sum((state.get("timings") or {}).values())
    console.print(f"[dim]{when}[/] [bold]{post.chat_title}[/] {edit}{post.author}: «{text}»\n    → {verdict} "
                  f"[dim]({ms / 1000:.1f} с)[/]")


@app.command()
def simulate(scenario: Path = typer.Option(ROOT / "data/scenarios/demo.yaml", help="YAML-сценарий."),
             delay: float = typer.Option(0.0, help="Пауза между сообщениями, с (для наблюдения в UI)."),
             keep: bool = typer.Option(False, help="Не очищать БД перед прогоном."),
             act_mode: Optional[str] = typer.Option(None, help="llm | deterministic"),
             fake: bool = typer.Option(False, help="FakeLLM + hash-эмбеддинги (ТОЛЬКО проверка кода)."),
             model: Optional[str] = typer.Option(None, help="Модель Ollama, например qwen3:4b.")) -> None:
    """Проиграть демо-сценарий через весь пайплайн агента (FakeHub, без Telegram)."""
    s = _settings(fake, act_mode)
    if model:
        s.llm.model = model
    if fake:
        console.print(FAKE_WARNING)
    else:
        _check_ollama_or_exit(s)
    from study_radar.agent.graph import AgentRuntime
    from study_radar.services import Services
    from study_radar.simulate import load_scenario, run_scenario

    sv = Services(s, "fake")
    if not keep:
        reset_runtime(s, sv)
    sc = load_scenario(scenario, s.tz)
    sv.db.set_meta("run_backend", "fake" if fake else f"ollama:{s.llm.model}")
    sv.db.set_meta("run_embedder", sv.embedder.name)
    sv.db.set_meta("run_status", "running")
    sv.db.set_meta("run_total", str(len(sc.events)))
    console.print(f"[bold]Сценарий:[/] {sc.name} — событий: {len(sc.events)}; LLM: {sv.llm.label}; "
                  f"эмбеддинги: {sv.embedder.name}; act: {s.agent.act_mode}")

    async def main():
        async with AgentRuntime(s, "fake", sv) as rt:
            await run_scenario(rt, sc, on_result=_print_result, delay=delay)

    import time

    t0 = time.perf_counter()
    try:
        asyncio.run(main())
    finally:
        sv.db.set_meta("run_status", "done")
    _summary(sv, time.perf_counter() - t0)


def _summary(sv, seconds: float) -> None:
    db = sv.db
    rows = db.decisions(10000)
    routes = {}
    for r in rows:
        routes[r["route"]] = routes.get(r["route"], 0) + 1
    acts = [r for r in rows if r["route"] in {"act", "review"}]
    fb = sum(1 for r in acts if r["fallback"])
    console.print()
    console.print(f"[bold green]Готово за {seconds:.0f} с.[/] Постов: {len(rows)} · важных: {routes.get('act', 0)} · "
                  f"на проверку: {routes.get('review', 0)} · шум (LLM): {routes.get('skip', 0)} · "
                  f"отсеяно префильтром: {routes.get('dropped', 0)} · фолбэков: {fb}/{len(acts)}")
    console.print(f"Дедлайнов активных: {len(db.list_deadlines())}. Доска:")
    pages = db.board_pages()
    if pages:
        import re

        console.print(re.sub(r"<[^>]+>", "", pages[0]["text"]))
    console.print("\nОткройте интерфейс: [bold]make ui[/] → http://localhost:8501")


def reset_runtime(s: Settings, sv=None) -> None:
    from study_radar.services import Services

    sv = sv or Services(s)
    sv.db.reset()
    for col in ("messages", "deadlines"):
        try:
            got = sv.store.col(col).get(include=[])
            if got["ids"]:
                sv.store.delete(col, got["ids"])
        except Exception:
            pass


@app.command()
def reset(yes: bool = typer.Option(False, "--yes", "-y"),
          workspace: str = typer.Option("all", help="demo | live | all")) -> None:
    """Очистить рабочие данные агента (сообщения, решения, дедлайны, хаб)."""
    s = _settings()
    targets = ["demo", "live"] if workspace == "all" else [workspace]
    if not yes and not typer.confirm(f"Удалить рабочие данные ({', '.join(targets)})?"):
        raise typer.Exit()
    for ws in targets:
        s.workspace = ws
        shutil.rmtree(s.workspace_dir(), ignore_errors=True)
    console.print(f"[green]✓ Рабочие данные удалены: {', '.join(targets)}[/]")


# ---------------------------------------------------------------------------------------------- ask / board
@app.command()
def ask(question: str = typer.Argument(..., help="Вопрос по переписке."),
        live: bool = typer.Option(False, "--live", help="Искать в данных бота, а не демо-сценария."),
        fake: bool = typer.Option(False, help="FakeLLM (только проверка кода).")) -> None:
    """Модуль 2: ответ на вопрос по переписке (RAG) со ссылками на источники."""
    s = _settings(fake, workspace="live" if live else "demo")
    if not fake:
        _check_ollama_or_exit(s)
    from study_radar.agent.ask import Asker
    from study_radar.services import Services

    sv = Services(s)
    res = asyncio.run(Asker(sv, sv.llm).ask(question))
    console.print(res.format())
    console.print(f"[dim]{sv.llm.label} · {res.ms / 1000:.1f} с[/]")


@app.command()
def board(live: bool = typer.Option(False, "--live", help="Доска бота, а не демо-сценария.")) -> None:
    """Показать текущую доску дедлайнов."""
    import re

    s = _settings(workspace="live" if live else "demo")
    from study_radar.services import Services

    pages = Services(s).db.board_pages()
    if not pages:
        console.print("Доска пуста — запустите make simulate или make bot")
    for p in pages:
        console.print(re.sub(r"<[^>]+>", "", p["text"]))


# ---------------------------------------------------------------------------------------------- eval
@app.command("eval")
def eval_cmd(labels: Path = typer.Option(ROOT / "data/eval/labeled.jsonl", help="Размеченный датасет."),
             fixtures: Path = typer.Option(ROOT / "data/fixtures", help="Папка с синтетическими экспортами."),
             act_mode: str = typer.Option("llm", help="llm — честная проверка MCP-вызовов; deterministic — быстрее."),
             limit: int = typer.Option(0, help="Ограничить число постов (0 = все)."),
             act_sample: int = typer.Option(25, help="Для скольких важных постов LLM сама вызывает MCP (0 = для всех)."),
             out: Path = typer.Option(ROOT / "docs/eval_report.md", help="Куда писать отчёт."),
             fake: bool = typer.Option(False, help="FakeLLM (проверка кода; отчёт пишется в data/runtime)."),
             model: Optional[str] = typer.Option(None, help="Модель Ollama, например qwen3:4b.")) -> None:
    """Оценка качества: P/R/F1 важности, точность предмета/категории/дат, MCP без фолбэка, задержка + бейзлайн."""
    s = _settings(fake, act_mode)
    if model:
        s.llm.model = model
    if fake:
        console.print(FAKE_WARNING)
        out = ROOT / "data/runtime/eval_report_fake.md"
    else:
        _check_ollama_or_exit(s)
    from study_radar.eval import run_eval

    report = asyncio.run(run_eval(s, labels, fixtures, out, limit=limit, act_sample=act_sample, console=console))
    console.print(f"[green]✓ Отчёт:[/] {report}")


# ---------------------------------------------------------------------------------------------- bot / ui
@app.command()
def bot() -> None:
    """Режим бота: слушать чаты-источники в Telegram и вести хаб."""
    s = _settings(workspace="live")
    if not s.telegram.token:
        console.print("[red]✗ TELEGRAM_BOT_TOKEN не задан.[/] Создайте бота у @BotFather и впишите токен в .env "
                      "(инструкция — README, раздел «Режим бота»).")
        raise typer.Exit(1)
    _check_ollama_or_exit(s)
    from study_radar.ingest.bot_listener import run_bot

    asyncio.run(run_bot(s))


@app.command()
def ui(port: int = typer.Option(8501)) -> None:
    """Запустить Streamlit-интерфейс (http://localhost:8501)."""
    app_path = Path(__file__).with_name("ui") / "app.py"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    cmd = [sys.executable, "-m", "streamlit", "run", str(app_path), "--server.port", str(port),
           "--server.headless", "true", "--browser.gatherUsageStats", "false", "--theme.base", "light"]
    console.print(f"Интерфейс: [bold]http://localhost:{port}[/]  (Ctrl+C — остановить)")
    raise typer.Exit(subprocess.call(cmd, env=env))


if __name__ == "__main__":
    app()
