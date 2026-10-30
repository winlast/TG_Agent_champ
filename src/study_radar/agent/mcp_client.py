"""MCP-клиент агента: запускает MCP-сервер StudyRadar (stdio) и даёт LLM его инструменты.

Используется langchain-mcp-adapters поверх официального MCP Python SDK.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from study_radar.config import CONFIG_ENV, Settings
from study_radar.mcp_server.server import HUB_ENV

log = logging.getLogger(__name__)

SRC_DIR = str(Path(__file__).resolve().parents[2])


def _content_to_obj(content: Any) -> Any:
    """Результат MCP-инструмента → dict (если это JSON) или текст."""
    if isinstance(content, tuple):   # (content, artifact)
        content = content[0]
    if isinstance(content, list):
        texts = [c.get("text", "") if isinstance(c, dict) else getattr(c, "text", str(c)) for c in content]
        content = "\n".join(texts)
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content


class MCPToolbox:
    """async with MCPToolbox(settings, hub_backend) as tb: await tb.call("list_deadlines", {})"""

    def __init__(self, settings: Settings, hub_backend: str = "fake"):
        env = {**os.environ, CONFIG_ENV: settings.to_env_json(), HUB_ENV: hub_backend,
               "PYTHONPATH": SRC_DIR + os.pathsep + os.environ.get("PYTHONPATH", ""),
               "PYTHONIOENCODING": "utf-8"}
        self.connection = {"transport": "stdio", "command": sys.executable,
                           "args": ["-m", "study_radar.mcp_server.server"], "env": env}
        self.client = MultiServerMCPClient({"study_radar": self.connection})
        self._cm = None
        self.tools: dict[str, Any] = {}

    async def __aenter__(self) -> "MCPToolbox":
        self._cm = self.client.session("study_radar")
        session = await self._cm.__aenter__()
        for t in await load_mcp_tools(session):
            self.tools[t.name] = t
        log.info("MCP: подключён сервер study-radar (stdio), инструментов: %d", len(self.tools))
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._cm is not None:
            await self._cm.__aexit__(*exc)

    def ollama_specs(self) -> list[dict[str, Any]]:
        """Описание инструментов в формате tool calling Ollama (JSON Schema из MCP)."""
        specs = []
        for t in self.tools.values():
            schema = t.args_schema if isinstance(t.args_schema, dict) else t.args_schema.model_json_schema()
            specs.append({"type": "function", "function": {
                "name": t.name, "description": (t.description or "").strip(), "parameters": schema}})
        return specs

    def schema(self, name: str) -> dict[str, Any]:
        t = self.tools[name]
        return t.args_schema if isinstance(t.args_schema, dict) else t.args_schema.model_json_schema()

    async def call(self, name: str, args: dict[str, Any]) -> tuple[bool, Any, int]:
        """Вызвать инструмент через MCP. Возвращает (успех, результат, мс)."""
        t0 = time.perf_counter()
        if name not in self.tools:
            return False, f"Нет такого инструмента: {name}. Доступны: {', '.join(self.tools)}", 0
        try:
            raw = await self.tools[name].ainvoke(args)
            res = _content_to_obj(raw)
            ok = not (isinstance(res, str) and res.lower().startswith("error")) and not (
                isinstance(res, dict) and res.get("ok") is False)
        except Exception as e:  # ошибки валидации аргументов и т. п.
            res, ok = f"Error: {e}", False
        return ok, res, int((time.perf_counter() - t0) * 1000)
