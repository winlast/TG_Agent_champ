"""StudyRadar — локальный ИИ-агент для сортировки учебных сообщений и контроля дедлайнов.

Архитектура «вариант А» (кейс-чемпионат, дисциплина «Архитектура АСОИУ»):
  * Модуль 1 — индексация: ``study_radar.index``
  * Модуль 2 — RAG-пайплайн: ``study_radar.retrieval`` + ``study_radar.agent.ask``
  * Модуль 3 — MCP-инструменты: ``study_radar.mcp_server`` + узел ``act`` в ``study_radar.agent.graph``
"""

__version__ = "1.0.0"
