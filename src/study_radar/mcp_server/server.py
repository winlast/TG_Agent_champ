"""Модуль 3 — MCP-сервер StudyRadar (FastMCP, транспорт stdio).

Агент (LangGraph, узел act) запускает этот сервер дочерним процессом и подключается к нему
через langchain-mcp-adapters. LLM сама выбирает инструменты и аргументы (tool calling).

Запуск вручную (например, для MCP Inspector):  python -m study_radar.mcp_server.server
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from study_radar.config import load_settings, setup_logging
from study_radar.mcp_server.tools import RadarTools
from study_radar.services import Services

HUB_ENV = "STUDY_RADAR_HUB"   # fake | bot

mcp = FastMCP(
    "study-radar",
    instructions=(
        "Инструменты агента StudyRadar: пересылка учебных сообщений в топики чата-хаба, "
        "ведение дедлайнов (создание, перенос, отмена) и доски дедлайнов, поиск по переписке."
    ),
)

_tools: RadarTools | None = None


def tools() -> RadarTools:
    global _tools
    if _tools is None:
        settings = load_settings()
        _tools = RadarTools(Services(settings, hub_backend=os.environ.get(HUB_ENV, "fake")))
    return _tools


@mcp.tool()
async def forward_to_topic(subject: str | None, source_chat_id: int, message_ids: list[int], note: str) -> dict[str, Any]:
    """Переслать важное сообщение (или всю очередь сообщений) в топик предмета чата-хаба и добавить
    короткую пометку агента.

    Args:
        subject: ключ предмета из справочника (например "physics", "electro") или null для топика «Общее».
        source_chat_id: id исходного чата.
        message_ids: id всех сообщений поста (очереди), которые нужно переслать.
        note: пометка агента, например «🗓 Дедлайн · Физика · ДЗ №3 — до чт 05.11, 23:59 · уверенность 0.92».
    """
    return await tools().forward_to_topic(subject, source_chat_id, message_ids, note)


@mcp.tool()
async def create_deadline(subject: str | None, title: str, due_at: str | None, due_text: str | None,
                          location: str | None, source_refs: list[str], kind: str = "deadline") -> dict[str, Any]:
    """Создать дедлайн или событие. Если такой уже есть (тот же предмет, похожее название, близкая дата),
    дубль не создаётся — к существующему добавляется источник. Возвращает id.

    Args:
        subject: ключ предмета или null.
        title: коротко: «ДЗ №3», «Защита ЛР2», «Кейс-чемпионат».
        due_at: срок в ISO 8601 с часовым поясом, например "2026-11-05T23:59:00+03:00", или null.
        due_text: срок как написан в сообщении: «до пятницы 23:59».
        location: аудитория или ссылка, или null.
        source_refs: ссылки на исходные сообщения в формате "chat_id:message_id".
        kind: "deadline" (срок сдачи), "event" (защита, РК, консультация, чемпионат) или "assignment".
    """
    return await tools().create_deadline(subject, title, due_at, due_text, location, source_refs, kind)


@mcp.tool()
async def update_deadline(deadline_id: int, due_at: str | None = None, title: str | None = None,
                          location: str | None = None, status: str | None = None, source_ref: str | None = None,
                          change_note: str | None = None) -> dict[str, Any]:
    """Изменить известный дедлайн: перенос срока, уточнение места/названия. История изменений сохраняется,
    пометка в хабе редактируется (старое значение зачёркивается), в топик уходит «⚠️ Перенос: …».

    Args:
        deadline_id: id дедлайна из списка кандидатов / list_deadlines.
        due_at: новый срок ISO 8601 или null, если срок не менялся.
        title: новое название или null.
        location: новое место или null.
        status: "active" | "cancelled" | "done" или null.
        source_ref: сообщение-источник изменения, "chat_id:message_id".
        change_note: коротко, что изменилось и почему.
    """
    return await tools().update_deadline(deadline_id, due_at, title, location, status, source_ref, change_note)


@mcp.tool()
async def close_deadline(deadline_id: int, reason: str, status: str = "cancelled") -> dict[str, Any]:
    """Закрыть дедлайн: отмена (status="cancelled") или выполнено (status="done").

    Args:
        deadline_id: id дедлайна.
        reason: причина, например «пары по ТВиМС не будет».
        status: "cancelled" или "done".
    """
    return await tools().close_deadline(deadline_id, reason, status)


@mcp.tool()
async def list_deadlines(subject: str | None = None, include_past: bool = False) -> dict[str, Any]:
    """Список активных дедлайнов (отсортирован по сроку), опционально по предмету.

    Args:
        subject: ключ предмета или null — все.
        include_past: включать ли прошедшие.
    """
    return await tools().list_deadlines(subject, include_past)


@mcp.tool()
async def search_messages(query: str, subject: str | None = None, limit: int = 5) -> dict[str, Any]:
    """Гибридный поиск (векторный + BM25) по проиндексированным сообщениям учебных чатов (RAG).

    Args:
        query: что искать.
        subject: ключ предмета для фильтра или null.
        limit: сколько результатов вернуть (1–10).
    """
    return await tools().search_messages(query, subject, limit)


@mcp.tool()
async def get_message_context(chat_id: int, message_id: int) -> dict[str, Any]:
    """Сообщение + сообщение, на которое оно отвечает, + соседние сообщения.

    Args:
        chat_id: id чата.
        message_id: id сообщения.
    """
    return await tools().get_message_context(chat_id, message_id)


@mcp.tool()
async def refresh_deadlines_board() -> dict[str, Any]:
    """Перерисовать закреплённую доску в топике «📅 Дедлайны» (сортировка по сроку). Вызывать после
    любого создания / изменения / отмены дедлайна."""
    return await tools().refresh_deadlines_board()


@mcp.tool()
async def flag_for_review(chat_id: int, message_ids: list[int], reason: str) -> dict[str, Any]:
    """Отправить сомнительное сообщение в топик «🤔 На проверку» (низкая уверенность, неизвестный предмет).

    Args:
        chat_id: id исходного чата.
        message_ids: id сообщений.
        reason: почему агент сомневается.
    """
    return await tools().flag_for_review(chat_id, message_ids, reason)


def main() -> None:
    # stdout занят протоколом MCP (JSON-RPC) — логи только в stderr
    setup_logging(os.environ.get("STUDY_RADAR_LOG_LEVEL", "WARNING"), stream=sys.stderr)
    logging.getLogger(__name__).info("MCP-сервер StudyRadar запущен (stdio)")
    mcp.run("stdio")


if __name__ == "__main__":
    main()
