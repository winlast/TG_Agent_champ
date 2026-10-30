"""Автоматические скриншоты интерфейса для презентации (make screenshots).

Запускать ПОСЛЕ `make simulate` с настоящей моделью: скрипт откажется снимать данные FakeLLM.
Поднимает Streamlit на порту 8502, открывает его в headless Chromium (Playwright) и сохраняет
кадры в docs/screenshots/. Вопрос во вкладке «Спросить» задаётся настоящей модели (до ~5 мин на CPU).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "docs/screenshots"
PORT = 8502
QUESTION = "Когда сдавать лабу по элтеху?"


def launch_kwargs() -> dict:
    exe = os.environ.get("PW_CHROMIUM") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists()
                                             else None)
    return {"executable_path": exe} if exe else {}


async def shoot(ask: bool) -> list[Path]:
    from playwright.async_api import async_playwright

    saved: list[Path] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs())
        page = await browser.new_page(viewport={"width": 1700, "height": 950}, device_scale_factor=1.5)
        await page.goto(f"http://localhost:{PORT}/?shot=1", wait_until="networkidle")
        await page.wait_for_selector("text=StudyRadar", timeout=60000)
        await page.wait_for_timeout(3000)

        async def snap(name: str) -> None:
            await page.wait_for_timeout(1500)
            path = OUT / name
            await page.screenshot(path=str(path))
            saved.append(path)
            print("  ✓", path.relative_to(ROOT))

        async def tab(label: str) -> None:
            await page.get_by_role("tab", name=label).click()
            await page.wait_for_timeout(2500)

        async def scroll_to(text: str) -> None:
            await page.get_by_text(text, exact=False).first.evaluate("e => e.scrollIntoView({block: 'start'})")
            await page.wait_for_timeout(1000)

        await snap("01_feed.png")
        await tab("Хаб")
        await snap("02_hub_board.png")
        await page.locator('[data-testid="stRadio"] label').filter(has_text="Электротехника").first.click()
        await page.wait_for_timeout(2000)
        await snap("03_hub_subject.png")
        await tab("Дедлайны")
        await snap("04_deadlines.png")
        await tab("Трассировка")
        await snap("05_trace.png")
        await scroll_to("5. MCP-вызовы")
        await snap("05b_trace_mcp.png")
        if ask:
            await tab("Спросить")
            box = page.get_by_placeholder("Ваш вопрос по переписке…")
            await box.fill(QUESTION)
            await box.press("Enter")
            print(f"  … спрашиваю модель: «{QUESTION}» (на CPU может занять несколько минут)")
            answer = page.locator('[data-testid="stChatMessage"] >> text=/\\[1\\]|нет информации|Не удалось получить ответ/ '
                                  '>> visible=true')
            await answer.first.wait_for(timeout=600000)
            await page.wait_for_timeout(2000)
            await snap("06_ask.png")
        await tab("Метрики")
        await snap("07_metrics.png")
        await browser.close()
    return saved


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ask", action="store_true", help="не задавать вопрос модели")
    ap.add_argument("--allow-fake", action="store_true", help="снять и данные FakeLLM (НЕ для презентации)")
    args = ap.parse_args()

    from study_radar.config import load_settings
    from study_radar.storage.db import Database

    s = load_settings()
    backend = Database(s.db_path, s.tz).get_meta("run_backend")
    global OUT
    if backend == "fake":
        if not args.allow_fake:
            sys.exit("✗ Последний прогон был с FakeLLM — такие скриншоты нельзя показывать как работу модели.\n"
                     "  Запустите `make simulate` с Ollama и повторите.")
        OUT = ROOT / "data/runtime/screens_fake"
    if backend is None:
        sys.exit("✗ Нет данных демо. Сначала: make simulate")
    OUT.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(ROOT / "src/study_radar/ui/app.py"),
                             "--server.port", str(PORT), "--server.headless", "true",
                             "--browser.gatherUsageStats", "false", "--theme.base", "light"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                if httpx.get(f"http://localhost:{PORT}/_stcore/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        print(f"Снимаю экран интерфейса ({backend}) → {OUT.relative_to(ROOT)}")
        asyncio.run(shoot(ask=not args.no_ask))
    finally:
        proc.terminate()
    print("Готово. Дальше: make slides — скриншоты попадут в презентацию.")


if __name__ == "__main__":
    main()
