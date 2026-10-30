"""make doctor — понятный чек-лист окружения с подсказками, что исправить."""

from __future__ import annotations

import ctypes
import json
import platform
import shutil
import sys
import time
from typing import Any

import httpx
from rich.console import Console
from rich.table import Table

from study_radar.config import Settings


def ollama_status(s: Settings) -> dict[str, Any]:
    try:
        r = httpx.get(f"{s.llm.host.rstrip('/')}/api/tags", timeout=5)
        r.raise_for_status()
        models = [m.get("name") or m.get("model") for m in r.json().get("models", [])]
        reachable = True
    except Exception:
        models, reachable = [], False

    def has(name: str) -> bool:
        return name in models or f"{name}:latest" in models or any(m.split(":")[0] == name for m in models if ":" not in name)

    return {"reachable": reachable, "models": models, "has": has}


def total_ram_gb() -> float | None:
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        return int(line.split()[1]) / 1024 / 1024
        if sys.platform == "win32":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))  # type: ignore[attr-defined]
            return st.ullTotalPhys / 1024 ** 3
        if sys.platform == "darwin":
            import subprocess

            return int(subprocess.check_output(["sysctl", "-n", "hw.memsize"])) / 1024 ** 3
    except Exception:
        return None
    return None


def hardware_summary() -> str:
    ram = total_ram_gb()
    gpu = "нет NVIDIA GPU (CPU)"
    if shutil.which("nvidia-smi"):
        import subprocess

        try:
            gpu = subprocess.check_output(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                                          text=True, timeout=5).strip().splitlines()[0]
        except Exception:
            gpu = "NVIDIA GPU (nvidia-smi не ответил)"
    cpu = platform.processor() or platform.machine()
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        cpu = line.split(":", 1)[1].strip()
                        break
    except Exception:
        pass
    ram_s = f"{ram:.0f} ГБ RAM" if ram else "RAM ?"
    return f"{platform.system()} {platform.release()} · {cpu} · {ram_s} · {gpu}"


async def run_doctor(s: Settings, console: Console, deep: bool = False) -> bool:
    rows: list[tuple[str, str, str]] = []   # (статус, проверка, подсказка)

    def add(ok: bool | None, what: str, hint: str = "") -> None:
        mark = "[green]✓[/]" if ok else ("[yellow]⚠[/]" if ok is None else "[red]✗[/]")
        rows.append((mark, what, hint))

    v = sys.version_info
    add(v >= (3, 11), f"Python {v.major}.{v.minor}", "" if v >= (3, 11) else "Нужен Python 3.11+")
    try:
        import chromadb  # noqa: F401
        import langgraph  # noqa: F401
        import mcp  # noqa: F401
        import streamlit  # noqa: F401
        import aiogram  # noqa: F401

        add(True, "Зависимости установлены")
    except ImportError as e:
        add(False, f"Зависимости: {e}", "make setup")

    console.print(f"[dim]{hardware_summary()}[/]")
    ram = total_ram_gb()
    if ram and ram < 14 and "8b" in s.llm.model:
        add(None, f"RAM {ram:.0f} ГБ, модель {s.llm.model}", "Для 8 ГБ RAM: в .env LLM_MODEL=qwen3:4b и ollama pull qwen3:4b")

    st = ollama_status(s)
    add(st["reachable"], f"Ollama доступна ({s.llm.host})",
        "" if st["reachable"] else "Установите Ollama (README) и запустите: `ollama serve` (WSL) или приложение Ollama (Windows)")
    if st["reachable"]:
        add(st["has"](s.llm.model), f"LLM {s.llm.model} скачана", f"ollama pull {s.llm.model}")
        add(st["has"](s.embeddings.model), f"Эмбеддинги {s.embeddings.model} скачаны", f"ollama pull {s.embeddings.model}")
        if deep and st["has"](s.llm.model):
            try:
                import ollama

                client = ollama.Client(host=s.llm.host, timeout=600)
                t0 = time.perf_counter()
                r = client.chat(model=s.llm.model, think=False, options={"temperature": 0},
                                messages=[{"role": "user", "content": "Ответь JSON {\"ok\": true, \"word\": \"привет\"}"}],
                                format="json")
                dt = time.perf_counter() - t0
                tps = (r.eval_count or 0) / ((r.eval_duration or 1) / 1e9)
                json.loads(r.message.content)
                add(True, f"Тест генерации: {dt:.1f} с (с загрузкой модели), {tps:.1f} ток/с",
                    f"Ориентировочно {max(5, 350 / max(tps, 0.1)):.0f}–{max(10, 900 / max(tps, 0.1)):.0f} с на важное сообщение")
            except Exception as e:
                add(False, f"Тест генерации: {e}", "Проверьте, что модель скачана и хватает памяти")

    try:
        from study_radar.agent.mcp_client import MCPToolbox

        async with MCPToolbox(s) as tb:
            n = len(tb.tools)
        add(n == 9, f"MCP-сервер запускается (stdio), инструментов: {n}")
    except Exception as e:
        add(False, f"MCP-сервер: {e}", "Переустановите зависимости: make setup")

    try:
        s.db_path.parent.mkdir(parents=True, exist_ok=True)
        add(True, f"Папка данных доступна: {s.db_path.parent}")
    except Exception as e:
        add(False, f"Папка данных: {e}")

    # ---------------- Telegram (нужно только для make bot)
    if not s.telegram.token:
        add(None, "TELEGRAM_BOT_TOKEN не задан", "Нужен только для режима бота: @BotFather → /newbot → токен в .env")
    else:
        await _check_telegram(s, add)

    t = Table(show_header=True, header_style="bold", title="StudyRadar doctor")
    t.add_column("")
    t.add_column("Проверка")
    t.add_column("Что сделать", style="cyan")
    for r in rows:
        t.add_row(*r)
    console.print(t)
    critical_ok = all("✗" not in r[0] for r in rows)
    console.print("[bold green]Всё готово для демо.[/]" if critical_ok else "[bold red]Есть проблемы — см. подсказки.[/]")
    return critical_ok


async def _check_telegram(s: Settings, add) -> None:
    from aiogram import Bot

    bot = Bot(s.telegram.token)
    try:
        me = await bot.get_me()
        add(True, f"Бот @{me.username} отвечает")
        privacy_ok = bool(me.can_read_all_group_messages)
        add(privacy_ok if privacy_ok else None, "Privacy mode выключен" if privacy_ok else "Privacy mode ВКЛЮЧЁН",
            "" if privacy_ok else "@BotFather → /setprivacy → бот → Disable; затем удалите бота из чатов и добавьте снова "
                                  "(или сделайте его админом в чатах-источниках)")
        from study_radar.storage.db import Database

        hub = s.telegram.hub_chat_id
        if not hub:
            raw = Database(s.db_path, s.tz).get_meta("hub_chat_id")
            hub = int(raw) if raw else None
        if not hub:
            add(None, "Хаб не задан", "Создайте группу с темами, добавьте бота админом и напишите в ней /sethub "
                                      "(после make bot) — или впишите HUB_CHAT_ID в .env")
        else:
            try:
                chat = await bot.get_chat(hub)
                add(bool(chat.is_forum), f"Хаб «{chat.title}» (темы {'включены' if chat.is_forum else 'ВЫКЛЮЧЕНЫ'})",
                    "" if chat.is_forum else "Настройки группы → Темы (Topics) → включить")
                member = await bot.get_chat_member(hub, me.id)
                admin = member.status in {"administrator", "creator"}
                topics = bool(getattr(member, "can_manage_topics", False))
                pin = bool(getattr(member, "can_pin_messages", False))
                add(admin and topics and pin, "Бот — админ хаба с правами «управление темами» и «закрепление»",
                    "" if admin and topics and pin else "Группа → Администраторы → бот → включить «Управление темами» и «Закрепление сообщений»")
            except Exception as e:
                add(False, f"Хаб {hub}: {e}", "Проверьте HUB_CHAT_ID и что бот добавлен в группу")
        for cid in s.telegram.source_chat_ids:
            try:
                chat = await bot.get_chat(cid)
                add(True, f"Источник «{chat.title}» ({cid})")
            except Exception as e:
                add(False, f"Источник {cid}: {e}", "Добавьте бота в этот чат")
    except Exception as e:
        add(False, f"Telegram: {e}", "Проверьте токен и доступ к api.telegram.org (VPN/прокси)")
    finally:
        await bot.session.close()
