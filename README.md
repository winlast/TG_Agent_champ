# 📡 StudyRadar — ИИ-агент для учебных Telegram-чатов

**StudyRadar** читает учебные чаты, **по смыслу** (локальная LLM, а не ключевые слова) решает, что важно, раскладывает важное по темам-предметам чата-хаба, ведёт **закреплённую доску дедлайнов** (сортировка по сроку) и **сам обновляет** её при переносах, отменах и редактированиях. Отвечает на вопросы по переписке со ссылками на сообщения (`/ask когда сдавать лабу по элтеху?`).

Команда **SuperPuper** · МГТУ им. Н. Э. Баумана, ИУ5-31Б · кейс-чемпионат по созданию ИИ-агентов («Архитектура АСОИУ»).

| «физика — говно» | «лабу перенесли на среду» (ответом на сообщение о ЛР2) |
|---|---|
| ключевые слова: «физика» → важно ❌ | ключевые слова: предмет не найден ❌ |
| **StudyRadar: шум** ✅ | **StudyRadar: перенос защиты ЛР2 по электротехнике → ср 04.11, дедлайн обновлён, а не продублирован** ✅ |

Всё работает **локально**: Ollama (`qwen3:8b` или `qwen3:4b` + `bge-m3`), без облачных LLM.

> **Честность.** В репозитории есть заглушки `FakeLLM` / `HashEmbedder` / `FakeHub`: это детерминированные правила **только для автотестов**. Их результаты не являются работой модели. Скриншоты и метрики для презентации получаются **только с настоящей LLM** (`make simulate`, `make eval` на ноутбуке). Скрипты скриншотов и слайдов отказываются использовать данные FakeLLM.

---

## 🚀 Как запустить демо за 10 минут

Что понадобится: ноутбук с 16 ГБ RAM (или 8 ГБ — тогда модель `qwen3:4b`), ~8 ГБ свободного места, интернет на время установки.

### Вариант 1 — Windows + WSL2 (Ubuntu), рекомендуется

```bash
# 0) один раз, в PowerShell от администратора:  wsl --install -d Ubuntu   (перезагрузка)
# дальше всё — в терминале Ubuntu (WSL)
sudo apt update && sudo apt install -y python3-venv python3-pip make git curl

# 1) Ollama и модели (~5.5 ГБ)
curl -fsSL https://ollama.com/install.sh | sh
ollama serve > /tmp/ollama.log 2>&1 &      # если Ollama не запустилась сама как сервис
ollama pull qwen3:8b                        # для 8 ГБ RAM: ollama pull qwen3:4b
ollama pull bge-m3

# 2) проект
git clone https://github.com/winlast/TG_Agent_champ.git && cd TG_Agent_champ
make setup                                  # venv + зависимости (3–5 минут)
# если make setup пишет «Нужен Python 3.11+» (Ubuntu 22.04):
#   curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc && make setup
make doctor                                 # чек-лист: всё зелёное → можно запускать

# 3) демо без Telegram
make simulate                               # 36 сообщений из 5 чатов через весь агент
make ui                                     # открыть http://localhost:8501 в браузере Windows
```

Для 8 ГБ RAM впишите в `.env` одну строку `LLM_MODEL=qwen3:4b` (и `ollama pull qwen3:4b`).

### Вариант 2 — нативный Windows (без WSL)

1. Установите **Ollama for Windows**: https://ollama.com/download → запустится сама (значок в трее). В PowerShell: `ollama pull qwen3:8b` и `ollama pull bge-m3`.
2. Установите **Python 3.12** с https://www.python.org/downloads/ (галочка «Add python.exe to PATH»).
3. Скачайте проект: зелёная кнопка **Code → Download ZIP** на GitHub, распакуйте (или `git clone`).
4. В PowerShell в папке проекта:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\win.ps1 setup
powershell -ExecutionPolicy Bypass -File scripts\win.ps1 doctor
powershell -ExecutionPolicy Bypass -File scripts\win.ps1 simulate
powershell -ExecutionPolicy Bypass -File scripts\win.ps1 ui
```

### macOS / Linux

То же, что в варианте 1 (Ollama для macOS — https://ollama.com/download), затем `make setup && make doctor && make simulate && make ui`.

### Сколько это работает

Скорость зависит от железа; точное значение покажет `make doctor-deep` (замеряет токены/с на вашем ноутбуке).
Ориентировочно: с видеокартой NVIDIA или на Apple Silicon — **5–20 с** на важное сообщение, на CPU без видеокарты — **0,5–2,5 мин** для `qwen3:8b` и примерно вдвое быстрее для `qwen3:4b`. Шум, отсеянный префильтром, обрабатывается мгновенно.
Демо-сценарий — 36 сообщений, из них около 20 доходят до LLM. **На CPU запускайте `make simulate` заранее** (10–40 минут); прогресс виден в терминале и в интерфейсе.

Ускорение на CPU (необязательно): перед `ollama serve` задайте `export OLLAMA_NUM_PARALLEL=2`, чтобы Ollama держала в кэше оба системных промпта (классификации и действий), или используйте `make simulate-fast` (`qwen3:4b`).

---

## 🖥 Интерфейс (make ui → http://localhost:8501)

| Вкладка | Что показывает |
|---|---|
| 📥 Лента | все входящие сообщения и решение агента: категория, предмет, уверенность, причина; цвет — важное / шум / на проверку |
| 💬 Хаб | «как в Telegram»: темы предметов, пересланные сообщения с пометками агента, закреплённая доска дедлайнов |
| 📅 Дедлайны | таблица по сроку + история изменений (было → стало, по какому сообщению) |
| 🔍 Трассировка агента | RAG-контекст → промпт → JSON-классификация → **последовательность MCP-вызовов** с аргументами и результатами → verify |
| ❓ Спросить | RAG-ответы со ссылками на исходные сообщения |
| 📊 Метрики | результаты `make eval` и сравнение с поиском по ключевым словам |

Кнопка «▶ Проиграть сценарий» в боковой панели запускает `simulate` прямо из интерфейса и обновляет экраны по ходу.

---

## 🎤 Презентация для защиты

Готовый файл: **`docs/StudyRadar_presentation.pptx`** (содержимое слайдов дублируется в `docs/presentation.md`).

1. Впишите отчества и проверьте роли в **`docs/team.yaml`**.
2. Соберите всё одной командой (нужна запущенная Ollama):

```bash
make presentation      # simulate (настоящая модель) → автоматические скриншоты → слайды
make eval              # метрики качества → docs/eval_report.md (на CPU долго: см. ниже)
make slides            # пересобрать слайды с метриками
```

`make eval` прогоняет 195 размеченных постов: с видеокартой ~10–20 мин, на CPU — 1–3 часа (можно `make eval-quick`: 80 постов). Для скриншотов из настоящего Telegram см. `docs/demo_script.md`.

---

## 🤖 Режим Telegram-бота

Бот читает чаты-источники (новые и **отредактированные** сообщения) и ведёт **чат-хаб** — супергруппу с темами.

1. **Создать бота.** Telegram → @BotFather → `/newbot` → имя → получите токен вида `123456:ABC...`. Впишите его в `.env`: `TELEGRAM_BOT_TOKEN=...`
2. **Выключить privacy mode** (иначе бот не видит обычные сообщения в группах): @BotFather → `/setprivacy` → выберите бота → **Disable**. Если бот уже был в группах — удалите его оттуда и добавьте заново.
3. **Создать хаб.** Telegram → «Новая группа» (например «ИУ5-31Б Хаб») → в настройках группы включите **Темы (Topics)**. Добавьте бота и сделайте его **администратором** с правами «Управление темами» и «Закрепление сообщений».
4. **Добавить бота в учебные чаты-источники** (обычным участником; если privacy mode не выключали — админом).
5. Запустить: `make bot`. В группе-хабе написать **`/sethub`** — бот сам создаст темы (📅 Дедлайны, предметы, 🤔 На проверку, 🧾 Журнал агента) и закрепит доску.
6. Проверка: `make doctor` покажет, видит ли бот хаб, есть ли права и выключен ли privacy mode.

Команды бота: `/deadlines` — доска дедлайнов, `/ask вопрос` — ответ по переписке со ссылками, `/chatid` — id чата, `/sethub` — сделать чат хабом.

**Как узнать chat id:** напишите в чате `/chatid` — бот ответит. Для `.env`: `HUB_CHAT_ID=-100…` (необязательно, если сделали `/sethub`), `SOURCE_CHAT_IDS=-100…,-100…` (пусто = все чаты, где есть бот).

**История до добавления бота.** Бот не видит старые сообщения, поэтому их загружают из экспорта: Telegram Desktop → чат → ⋮ → **Экспорт истории чата** → снять галочки с фото/видео → формат **JSON** → `result.json`. Положите файл в `data/private/` (эта папка не попадает в git) и выполните:

```bash
make index EXPORT=data/private/result.json
```

---

## 🏛 Архитектура (вариант А)

![Архитектура](docs/architecture.png)

| Модуль | Где в коде | Что делает |
|---|---|---|
| **1. Индексация** | `src/study_radar/index/`, `src/study_radar/ingest/` | парсер Telegram-экспорта и Bot API, склейка «очередей», эмбеддинги `bge-m3` → Chroma (`messages`, `subjects`, `deadlines`) |
| **2. RAG-пайплайн** | `src/study_radar/retrieval/hybrid.py`, `src/study_radar/agent/ask.py` | гибридный поиск Chroma + BM25 (RRF) → обогащённый промпт `prompts/ask.md` → LLM → ответ со ссылками |
| **3. Инструменты через MCP** | `src/study_radar/mcp_server/server.py`, `src/study_radar/agent/graph.py` (узел `act`) | FastMCP-сервер (stdio, 9 инструментов); LLM **сама** вызывает инструменты (tool calling), фолбэк — через те же MCP-инструменты |

Граф LangGraph: `ingest → prefilter → build_context (RAG) → classify → act (LLM + MCP) → verify → log`.

Инструменты MCP: `forward_to_topic`, `create_deadline`, `update_deadline`, `close_deadline`, `list_deadlines`, `search_messages`, `get_message_context`, `refresh_deadlines_board`, `flag_for_review`. Сервер можно запустить отдельно (например, для MCP Inspector): `python -m study_radar.mcp_server.server`.

Подробнее: `docs/decisions.md` (принятые решения), `docs/devlog.md` (ход разработки и трудности), `CLAUDE.md` (ТЗ).

---

## 🧰 Все команды

| make | Windows (`scripts\win.ps1 …`) | Что делает |
|---|---|---|
| `make setup` | `setup` | venv + зависимости, создаёт `.env` |
| `make doctor` | `doctor` | проверка Ollama, моделей, токена, хаба (с подсказками) |
| `make simulate` | `simulate` | демо-сценарий `data/scenarios/demo.yaml` настоящей моделью |
| `make simulate-fast` | `simulate-fast` | то же на `qwen3:4b` |
| `make ui` | `ui` | интерфейс Streamlit |
| `make bot` | `bot` | режим Telegram-бота |
| `make index EXPORT=…` | `index путь` | загрузить историю из JSON-экспорта |
| `make ask Q='…'` | `ask "…"` | вопрос по переписке из терминала |
| `make test` | `test` | автотесты (FakeLLM, без Ollama и Telegram) |
| `make eval` / `make eval-quick` | `eval` / `eval-quick` | оценка качества + бейзлайн → `docs/eval_report.md` |
| `make presentation` | `presentation` | simulate + скриншоты + слайды |
| `make slides` | `slides` | пересобрать `docs/StudyRadar_presentation.pptx` |

Модель меняется **одной строкой** в `.env`: `LLM_MODEL=qwen3:4b` (или `qwen3:8b`). Остальные настройки — `config/settings.yaml`, справочник предметов и доверенных авторов — `config/subjects.yaml`.

---

## 📁 Структура

```
config/            settings.yaml, subjects.yaml (предметы, алиасы, «физра ≠ физика», доверенные авторы)
prompts/           classify.md (правила + 10 few-shot), act.md, ask.md
src/study_radar/
  ingest/          telegram_export.py, normalize.py, bursts.py, bot_listener.py
  index/           embedder.py, vector_store.py, indexer.py            ← Модуль 1
  retrieval/       hybrid.py                                           ← Модуль 2 (поиск)
  agent/           prefilter, context, classify, dates, dedup, graph (LangGraph), ask (RAG), mcp_client
  mcp_server/      server.py (FastMCP), tools.py                       ← Модуль 3
  telegram/        hub.py (BotHub, FakeHub), board.py, notes.py
  llm/             client.py (OllamaLLM, FakeLLM)
  storage/         db.py, migrations.sql (SQLite)
  ui/app.py        Streamlit;   cli.py — Typer;   doctor.py;   eval.py;   simulate.py
data/fixtures/     5 синтетических чатов в формате Telegram-экспорта (214 сообщений, имена вымышлены)
data/scenarios/    demo.yaml — сценарий демо
data/eval/         labeled.jsonl — 195 размеченных постов
data/private/      ваши реальные экспорты (в .gitignore!)
docs/              architecture.puml/.png, presentation.md/.pptx, demo_script.md, decisions.md, devlog.md, team.yaml
scripts/           make_fixtures.py, screenshots.py, build_slides.py, win.ps1
tests/             pytest: парсер, даты, склейка, MCP (stdio), переносы, дубли, редактирование, фолбэк, RAG, бот
```

**Приватность:** реальные переписки, `.env`, базы и индексы в git не попадают (`.gitignore`).

---

## ❓ Если что-то не работает

| Симптом | Решение |
|---|---|
| `Ollama недоступна` | WSL: `ollama serve &`; Windows: запустить приложение Ollama. Проверка: `curl http://localhost:11434/api/tags` |
| `Не скачаны модели` | `ollama pull qwen3:8b` и `ollama pull bge-m3` (или `qwen3:4b`) |
| Очень медленно | `LLM_MODEL=qwen3:4b` в `.env`; закрыть браузер с кучей вкладок; `OLLAMA_NUM_PARALLEL=2` |
| Нехватка памяти | `qwen3:4b`; в `config/settings.yaml` уменьшить `num_ctx` до 4096 |
| Бот молчит в группах | выключить privacy mode (см. выше), удалить и снова добавить бота; `make doctor` |
| `make screenshots` в WSL ругается на библиотеки | `sudo .venv/bin/python -m playwright install-deps chromium` (или сделайте скриншоты вручную: Win+Shift+S) |
| Начать демо заново | `make simulate` сам очищает данные; полная очистка — `make reset` |
