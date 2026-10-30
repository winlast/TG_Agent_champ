# Статус проекта и что осталось (для команды и для Claude Code)

ТЗ — `CLAUDE.md`. Здесь — что уже сделано и что осталось довести на ноутбуке команды.

## Готово
- Все модули по ТЗ: парсер экспорта, склейка очередей, Модуль 1 (Chroma + bge-m3), Модуль 2 (гибридный RAG, `/ask`),
  Модуль 3 (MCP-сервер на FastMCP, stdio; LLM сама вызывает инструменты; фолбэк), LangGraph-граф, доска дедлайнов,
  переносы, отмены, редактирования, дедупликация, бот на aiogram, Streamlit UI, `doctor`, `eval` с бейзлайном.
- 39 автотестов (`make test`), включая проверку настоящих клиентов Ollama против HTTP-заглушки API.
- Презентация `docs/StudyRadar_presentation.pptx` (+ PDF, + `docs/presentation.md`) со скриншотами.

## Честно о текущих скриншотах
Скриншоты в `docs/screenshots/` сняты в **тестовом режиме** (FakeLLM — правила вместо модели), это отмечено на слайдах.
Маркер — файл `docs/screenshots/.fake`. Причина: на ноутбуке без видеокарты `qwen3:8b` работает только на CPU.

## Осталось (по порядку)
1. **Прогон настоящей моделью** (лучше `qwen3:4b` на CPU):
   - Ollama должна быть запущена (`ollama serve` в отдельном окне) на адресе из `.env` (`OLLAMA_HOST`).
   - `LLM_MODEL=qwen3:4b make simulate` — 10–40 минут на CPU.
2. **Скриншоты с моделью**: один раз `sudo .venv/bin/python -m playwright install-deps chromium` (это делает человек — нужен пароль),
   затем `make screenshots`, затем удалить `docs/screenshots/.fake` и `make slides`.
3. **Метрики**: `LLM_MODEL=qwen3:4b make eval-quick` → `docs/eval_report.md`, затем `make slides`.
4. **Отчества** в `docs/team.yaml` → `make slides`.
5. Коммит и пуш.

## Правила для Claude Code в этом проекте
- Не трогать ничего вне папки проекта: `~/.bashrc`, `/etc/*`, `.wslconfig`, systemd, другие процессы.
- Не запускать и не перезапускать Ollama, не трогать CUDA/GPU — Ollama запускает человек.
- Не использовать `sudo`, `wsl`, `pkill`, `killall`, `systemctl` (запреты в `.claude/settings.json`).
- Долгие команды (`make simulate`, `make eval`) лучше запускать человеку в отдельном терминале (`tmux`),
  а Claude Code — смотреть результат и чинить ошибки в коде.
