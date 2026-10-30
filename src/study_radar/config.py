"""Загрузка настроек: config/settings.yaml + config/subjects.yaml + .env."""

from __future__ import annotations

import json
import logging
import os
from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
CONFIG_ENV = "STUDY_RADAR_CONFIG_JSON"  # так настройки передаются в MCP-сервер (дочерний процесс)


class Subject(BaseModel):
    key: str
    name: str
    emoji: str = "📚"
    aliases: list[str] = Field(default_factory=list)
    not_aliases: list[str] = Field(default_factory=list)
    teachers: list[str] = Field(default_factory=list)
    works: list[str] = Field(default_factory=list)

    @property
    def title(self) -> str:
        return f"{self.emoji} {self.name}"

    def describe(self) -> str:
        parts = [f"{self.key}: {self.name}", "алиасы: " + ", ".join(self.aliases)]
        if self.not_aliases:
            parts.append("НЕ путать с: " + ", ".join(self.not_aliases))
        if self.teachers:
            parts.append("преподаватели: " + ", ".join(self.teachers))
        return "; ".join(parts)


class PathsCfg(BaseModel):
    db: str = "data/runtime/study_radar.sqlite"
    chroma: str = "data/runtime/chroma"


class LLMCfg(BaseModel):
    backend: str = "ollama"
    host: str = "http://localhost:11434"
    model: str = "qwen3:8b"
    temperature: float = 0.0
    think: bool = False
    num_ctx: int = 8192
    timeout_s: float = 900
    keep_alive: str = "30m"


class EmbeddingsCfg(BaseModel):
    backend: str = "ollama"
    model: str = "bge-m3"


class BurstsCfg(BaseModel):
    gap_minutes: float = 5
    live_debounce_seconds: float = 45


class PrefilterCfg(BaseModel):
    threshold: float = 0.42
    min_words: int = 3


class ContextCfg(BaseModel):
    prev_messages: int = 5
    prev_window_minutes: int = 30
    similar_k: int = 3
    deadlines_k: int = 5


class ClassifyCfg(BaseModel):
    review_confidence: float = 0.5
    max_days_past: int = 30
    max_days_future: int = 240


class AgentCfg(BaseModel):
    act_mode: str = "llm"
    max_attempts: int = 2
    max_steps: int = 4


class DedupCfg(BaseModel):
    title_similarity: float = 0.82
    date_tolerance_hours: float = 36


class RetrievalCfg(BaseModel):
    k: int = 6
    rrf_k: int = 60


class HubCfg(BaseModel):
    journal: bool = True
    past_mode: str = "collapse"


class TelegramCfg(BaseModel):
    token: str = ""
    hub_chat_id: int | None = None
    source_chat_ids: list[int] = Field(default_factory=list)


class Settings(BaseModel):
    timezone: str = "Europe/Moscow"
    log_level: str = "INFO"
    paths: PathsCfg = PathsCfg()
    llm: LLMCfg = LLMCfg()
    embeddings: EmbeddingsCfg = EmbeddingsCfg()
    bursts: BurstsCfg = BurstsCfg()
    prefilter: PrefilterCfg = PrefilterCfg()
    context: ContextCfg = ContextCfg()
    classify: ClassifyCfg = ClassifyCfg()
    agent: AgentCfg = AgentCfg()
    dedup: DedupCfg = DedupCfg()
    retrieval: RetrievalCfg = RetrievalCfg()
    hub: HubCfg = HubCfg()
    telegram: TelegramCfg = TelegramCfg()
    subjects: dict[str, Subject] = Field(default_factory=dict)
    trusted_authors: list[str] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    @cached_property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else ROOT / p

    @property
    def db_path(self) -> Path:
        return self.path(self.paths.db)

    @property
    def chroma_path(self) -> Path:
        return self.path(self.paths.chroma)

    def is_trusted(self, author: str | None) -> bool:
        a = (author or "").lower()
        return any(t.lower() in a for t in self.trusted_authors if t)

    def to_env_json(self) -> str:
        return self.model_dump_json()


def _parse_ids(raw: str | None) -> list[int]:
    out: list[int] = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                logging.getLogger(__name__).warning("Не число в SOURCE_CHAT_IDS: %r", part)
    return out


def load_settings(
    settings_path: Path | None = None,
    subjects_path: Path | None = None,
    env_file: Path | None = None,
    use_env: bool = True,
) -> Settings:
    """Собирает настройки. В дочернем процессе MCP берёт готовый JSON из переменной окружения."""
    if use_env and os.environ.get(CONFIG_ENV):
        return Settings.model_validate(json.loads(os.environ[CONFIG_ENV]))

    if use_env:
        from dotenv import load_dotenv

        load_dotenv(env_file or ROOT / ".env", override=False)

    data = yaml.safe_load((settings_path or ROOT / "config/settings.yaml").read_text(encoding="utf-8")) or {}
    subj = yaml.safe_load((subjects_path or ROOT / "config/subjects.yaml").read_text(encoding="utf-8")) or {}
    data["subjects"] = {k: {"key": k, **v} for k, v in (subj.get("subjects") or {}).items()}
    data["trusted_authors"] = list(subj.get("trusted_authors") or []) + list(data.get("trusted_authors") or [])

    s = Settings.model_validate(data)
    if use_env:
        env = os.environ
        if env.get("LLM_MODEL"):
            s.llm.model = env["LLM_MODEL"].strip()
        if env.get("LLM_BACKEND"):
            s.llm.backend = env["LLM_BACKEND"].strip()
        if env.get("EMBED_MODEL"):
            s.embeddings.model = env["EMBED_MODEL"].strip()
        if env.get("EMBED_BACKEND"):
            s.embeddings.backend = env["EMBED_BACKEND"].strip()
        if env.get("OLLAMA_HOST"):
            s.llm.host = env["OLLAMA_HOST"].strip()
        if env.get("TIMEZONE"):
            s.timezone = env["TIMEZONE"].strip()
        if env.get("STUDY_RADAR_DB"):
            s.paths.db = env["STUDY_RADAR_DB"]
        if env.get("STUDY_RADAR_CHROMA"):
            s.paths.chroma = env["STUDY_RADAR_CHROMA"]
        s.telegram.token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
        hub = env.get("HUB_CHAT_ID", "").strip()
        s.telegram.hub_chat_id = int(hub) if hub.lstrip("-").isdigit() else None
        s.telegram.source_chat_ids = _parse_ids(env.get("SOURCE_CHAT_IDS"))
    return s


def setup_logging(level: str = "INFO", stream=None) -> None:
    handlers: list[logging.Handler]
    if stream is None:
        try:
            from rich.logging import RichHandler

            handlers = [RichHandler(rich_tracebacks=True, show_path=False, markup=False)]
            fmt = "%(message)s"
        except ImportError:  # pragma: no cover
            handlers = [logging.StreamHandler()]
            fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    else:
        handlers = [logging.StreamHandler(stream)]
        fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=level.upper(), format=fmt, handlers=handlers, force=True)
    for noisy in ("httpx", "chromadb", "aiogram.event", "mcp", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
