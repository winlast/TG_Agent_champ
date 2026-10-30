"""Модуль 1 (индексация) + Модуль 2 (гибридный поиск и RAG-ответы со ссылками)."""

from datetime import datetime

from study_radar.agent.ask import NO_DATA, Asker
from study_radar.eval import baseline_predict, score
from study_radar.retrieval.hybrid import rrf_merge, tokenize

from conftest import FIXTURES


def _index_fixtures(services):
    for p in sorted(FIXTURES.glob("*.json")):
        services.indexer.index_export(p)


def test_rrf_and_tokenize():
    scores = rrf_merge([["a", "b", "c"], ["c", "a"]], k=60)
    assert max(scores, key=scores.get) == "a"
    assert tokenize("Лабораторную перенесли на среду") == ["лабор", "перен", "среду"]


def test_hybrid_search_with_filters(services):
    _index_fixtures(services)
    hits = services.retriever.search("защита лабораторной по элтеху", k=5)
    assert hits and any("ЛР" in h.text or "лаб" in h.text.lower() for h in hits)
    assert all(h.vector_rank or h.bm25_rank for h in hits)
    elteh_id = -1001000000003
    only_elteh = services.retriever.search("защита", k=5, chat_id=elteh_id)
    assert only_elteh and all(h.meta["chat_id"] == elteh_id for h in only_elteh)
    since = datetime(2026, 10, 28, tzinfo=services.s.tz)
    recent = services.retriever.search("защита", k=5, since=since)
    assert all(h.meta["date"] >= since.isoformat() for h in recent)


async def test_ask_answers_with_sources_and_admits_no_data(services):
    _index_fixtures(services)
    services.db.execute("UPDATE posts SET is_important=1 WHERE text LIKE '%ЛР2%' OR text LIKE '%лабу%'")
    asker = Asker(services, services.llm)
    res = await asker.ask("когда защита ЛР2 по элтеху?")
    assert "[1]" in res.answer and res.sources and res.sources[0].get("link")
    assert "Источники" in res.format()
    empty = await asker.ask("ъъъ щщщ")
    assert empty.answer == NO_DATA and not empty.sources
    assert services.db.asks()


def test_keyword_baseline_fails_where_meaning_matters(settings):
    d = datetime(2026, 10, 27, 8, 0, tzinfo=settings.tz)
    fizra = baseline_predict(settings, "кто на физру сегодня?", d)
    assert fizra["is_important"] and fizra["subject"] == "physics"      # ключевые слова ошибаются
    transfer = baseline_predict(settings, "лабу перенесли на среду", d)
    assert transfer["subject"] is None                                   # предмет не назван — не угадать
    rows = [{"gold": {"is_important": True, "subject": "physics", "category": "deadline", "due": "2026-10-30"},
             "pred": {"is_important": True, "subject": "physics", "category": "deadline", "due": "2026-10-30"}},
            {"gold": {"is_important": False, "subject": None, "category": "noise", "due": None},
             "pred": {"is_important": True, "subject": "physics", "category": "event", "due": None}}]
    m = score(rows)
    assert m["precision"] == 0.5 and m["recall"] == 1.0 and m["date_acc"] == 1.0
