# Статус проекта

ТЗ — `CLAUDE.md`. Ход работы и трудности — `docs/devlog.md`, принятые решения — `docs/decisions.md`.

## Готово
- Все модули по ТЗ: парсер экспорта, склейка очередей, Модуль 1 (Chroma + bge-m3), Модуль 2 (гибридный RAG, `/ask`),
  Модуль 3 (MCP-сервер на FastMCP, stdio; LLM сама вызывает инструменты; фолбэк), граф LangGraph, доска дедлайнов,
  переносы, отмены, редактирования, дедупликация, бот на aiogram (с прокси для VPN), Streamlit UI, `doctor`, `eval`
  с бейзлайном.
- Прогон настоящей моделью `qwen3:8b` на видеокарте ноутбука: `make simulate`, `make eval-quick` → `docs/eval_report.md`.
- Скриншоты интерфейса (`make screenshots`) и из настоящего Telegram (`docs/screenshots/tg_*.png`).
- Презентация `docs/StudyRadar_presentation.pptx` (+ PDF, + `docs/presentation.md`) — 22 слайда.
- 41 автотест (`make test`).

## Можно сделать дополнительно
- Полный `make eval` на всех 195 постах (сейчас в отчёте 80 постов `eval-quick`), затем `make slides`.
- Склейка сообщений в боте: не продолжать очередь после ответа (reply) или вопроса (см. `docs/devlog.md`).
- `scripts/screenshots.py`: ждать любую ссылку `[n]` в ответе вкладки «Спросить», а не только «[1]».

## Правила для Claude Code в этом проекте
- Не трогать ничего вне папки проекта: `~/.bashrc`, `/etc/*`, `.wslconfig`, systemd, другие процессы.
- Не запускать и не перезапускать Ollama, не трогать CUDA/GPU — Ollama запускает человек.
- Не использовать `sudo`, `wsl`, `pkill`, `killall`, `systemctl` (запреты в `.claude/settings.json`).
- Долгие команды (`make simulate`, `make eval`) лучше запускать человеку в отдельном терминале.
