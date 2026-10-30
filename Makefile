# StudyRadar — команды для запуска (Linux / macOS / WSL2).
# Нативный Windows без make: powershell -ExecutionPolicy Bypass -File scripts\win.ps1 <команда>

PY ?= python3
VENV := .venv
BIN := $(VENV)/bin
RADAR := $(BIN)/study-radar
EXPORT ?= data/private/result.json

.PHONY: help setup doctor doctor-deep index simulate simulate-fast ui bot ask test eval eval-quick screenshots slides \
        presentation diagram fixtures reset clean

help:
	@echo "make setup        — venv + зависимости (+ .env из примера)"
	@echo "make doctor       — проверка окружения: Ollama, модели, токен, хаб"
	@echo "make simulate     — демо-сценарий через весь пайплайн настоящей моделью (без Telegram)"
	@echo "make ui           — интерфейс http://localhost:8501"
	@echo "make bot          — режим Telegram-бота"
	@echo "make index EXPORT=path/result.json — загрузить историю из JSON-экспорта"
	@echo "make ask Q='когда сдавать лабу по элтеху?'"
	@echo "make test         — тесты"
	@echo "make eval         — оценка качества на размеченном датасете → docs/eval_report.md"
	@echo "make presentation — simulate + скриншоты + слайды (всё для защиты одной командой)"

setup:
	@if command -v uv >/dev/null 2>&1; then \
	  test -x $(BIN)/python || uv venv -p 3.12 $(VENV); \
	  uv pip install -p $(BIN)/python -e ".[dev,slides]"; \
	elif $(PY) -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then \
	  test -x $(BIN)/python || $(PY) -m venv $(VENV); \
	  $(BIN)/python -m pip install -q --upgrade pip && $(BIN)/pip install -q -e ".[dev,slides]"; \
	else \
	  echo "✗ Нужен Python 3.11+ (у вас: $$($(PY) --version 2>&1))."; \
	  echo "  Самый простой путь:  curl -LsSf https://astral.sh/uv/install.sh | sh  &&  source ~/.bashrc  &&  make setup"; \
	  exit 1; \
	fi
	@test -f .env || cp .env.example .env
	@echo "✓ Готово. Дальше: make doctor"

doctor:
	$(RADAR) doctor

# + замер скорости модели на этом ноутбуке
doctor-deep:
	$(RADAR) doctor --deep

index:
	$(RADAR) index --export $(EXPORT)

simulate:
	$(RADAR) simulate

# для 8 ГБ RAM или слабого CPU
simulate-fast:
	LLM_MODEL=qwen3:4b $(RADAR) simulate

ui:
	$(RADAR) ui

bot:
	$(RADAR) bot

ask:
	$(RADAR) ask "$(Q)"

test:
	$(BIN)/python -m pytest

eval:
	$(RADAR) eval

# быстрая оценка (первые 80 постов) — для CPU без видеокарты
eval-quick:
	$(RADAR) eval --limit 80 --act-sample 10

screenshots:
	$(BIN)/python -m playwright install chromium
	$(BIN)/python scripts/screenshots.py

slides:
	$(BIN)/python scripts/build_slides.py

presentation: simulate screenshots slides
	@echo "✓ Презентация: docs/StudyRadar_presentation.pptx (метрики — после make eval и make slides)"

diagram:
	@command -v plantuml >/dev/null && plantuml -tpng docs/architecture.puml && mv -f docs/StudyRadar.png docs/architecture.png \
	  || echo "PlantUML не найден: sudo apt install plantuml (или откройте docs/architecture.puml на plantuml.com)"

fixtures:
	$(BIN)/python scripts/make_fixtures.py

reset:
	$(RADAR) reset --yes

clean:
	rm -rf data/runtime/* .pytest_cache
	@touch data/runtime/.gitkeep
