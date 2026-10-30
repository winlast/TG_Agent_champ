"""Streamlit-дашборд StudyRadar (make ui → http://localhost:8501).

Вкладки: Лента · Хаб · Дедлайны · Трассировка агента · Спросить · Метрики.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from study_radar.agent.dates import fmt_due
from study_radar.config import ROOT, load_settings
from study_radar.models import CATEGORY_EMOJI, CATEGORY_RU, Category

st.set_page_config(page_title="StudyRadar", page_icon="📡", layout="wide",
                   initial_sidebar_state="collapsed" if st.query_params.get("shot") else "expanded")

CSS = """
<style>
.block-container {padding-top: 1.4rem; max-width: 1400px;}
.sr-sub {color:#5b6472; margin-top:-0.6rem; margin-bottom:0.8rem; font-size:0.95rem;}
.sr-badge {display:inline-block; padding:2px 9px; border-radius:999px; font-size:0.78rem; font-weight:600;
           margin-right:6px; white-space:nowrap;}
.sr-card {border:1px solid #e3e7ee; border-left:5px solid #c9d1dc; border-radius:10px; padding:10px 14px;
          margin-bottom:8px; background:#fff;}
.sr-card.important {border-left-color:#2f9e5b;} .sr-card.review {border-left-color:#e6a700;}
.sr-card.noise {border-left-color:#c9d1dc; opacity:0.78;} .sr-card.dropped {border-left-color:#eceff3; opacity:0.6;}
.sr-meta {color:#6b7480; font-size:0.82rem;} .sr-text {margin:4px 0 6px 0; white-space:pre-wrap;}
.sr-reason {color:#3d4652; font-size:0.86rem;}
.tg-wrap {background:#dfe8d6; background-image:linear-gradient(160deg,#dbe6cf 0%,#cfe0d8 100%);
          border-radius:12px; padding:14px 14px 6px 14px; min-height:420px;}
.tg-msg {background:#fff; border-radius:12px; padding:8px 12px; margin:0 0 10px 0; max-width:88%;
         box-shadow:0 1px 1px rgba(0,0,0,.08); font-size:0.93rem; line-height:1.38;}
.tg-msg.bot {background:#eef5ff; margin-left:6%;} .tg-msg.board {background:#fffdf3; max-width:100%; border:1px solid #f0e2a8;}
.tg-fwd {color:#2a7ab0; font-size:0.8rem; font-weight:600; margin-bottom:2px;}
.tg-author {color:#7a4fb5; font-size:0.8rem; font-weight:600;}
.tg-time {color:#8a949e; font-size:0.72rem; text-align:right; margin-top:2px;}
.tg-reply {border-left:3px solid #4a9fe0; padding-left:7px; color:#4a5a6a; font-size:0.8rem; margin-bottom:4px;}
.tg-topic {padding:7px 10px; border-radius:8px; margin-bottom:3px; font-size:0.93rem;}
.tg-pin {color:#b08900; font-size:0.78rem; font-weight:600;}
.sr-step {font-weight:700; font-size:1.02rem; margin:14px 0 4px 0;}
.sr-call {border:1px solid #e3e7ee; border-radius:8px; padding:8px 12px; margin-bottom:6px; background:#fbfcfe;}
.sr-call.fallback {background:#fff6f6; border-color:#f3c7c7;} .sr-call.verify {background:#f6fbf6;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

CAT_COLORS = {
    "deadline": ("#e8f3ff", "#1765ad"), "assignment": ("#eef0ff", "#3b3fb5"), "deadline_update": ("#fff4e5", "#b05a00"),
    "cancellation": ("#ffecec", "#b42318"), "event": ("#eaf7ef", "#1e7a45"), "announcement": ("#f3ecff", "#6a3fb5"),
    "materials": ("#eef6f6", "#226b6b"), "noise": ("#f1f3f5", "#68717d"),
}


def run_backend() -> str:
    from study_radar.storage.db import Database

    base = load_settings()
    return Database(base.db_path, base.tz).get_meta("run_backend") or ""


@st.cache_resource
def services(backend: str):
    """Сервисы под тот бэкенд, которым сделан показываемый прогон (эмбеддинги в индексе должны совпадать)."""
    from study_radar.services import Services

    cfg = load_settings()
    if backend == "fake":
        cfg.llm.backend, cfg.embeddings.backend = "fake", "hash"
    elif backend.startswith("ollama:"):
        cfg.llm.model = backend.split(":", 1)[1]
    return Services(cfg)


sv = services(run_backend())
s = sv.s
db = sv.db


def badge(text: str, bg: str = "#eef1f5", fg: str = "#3d4652") -> str:
    return f'<span class="sr-badge" style="background:{bg};color:{fg}">{html.escape(text)}</span>'


def cat_badge(cat: str) -> str:
    c = Category(cat)
    bg, fg = CAT_COLORS[cat]
    return badge(f"{CATEGORY_EMOJI[c]} {CATEGORY_RU[c]}", bg, fg)


def subj_name(key: str | None) -> str:
    return f"{s.subjects[key].emoji} {s.subjects[key].name}" if key in s.subjects else "📢 Общее"


def tg_html(text: str) -> str:
    return (text or "").replace("\n", "<br>")


def short_dt(iso: str | None) -> str:
    if not iso:
        return ""
    dt = datetime.fromisoformat(iso)
    return f"{fmt_due(dt).split(',')[0]} {dt:%H:%M}"


def jload(v):
    if not v:
        return None
    try:
        return json.loads(v)
    except (TypeError, json.JSONDecodeError):
        return v


# ---------------------------------------------------------------------------------------------- шапка
backend = db.get_meta("run_backend") or f"ollama:{s.llm.model}"
st.markdown("## 📡 StudyRadar — ИИ-агент для учебных Telegram-чатов")
if backend == "fake":
    st.warning("Данные получены в режиме **FakeLLM** (правила для проверки кода, НЕ модель). "
               "Для презентации запустите `make simulate` с настоящей моделью.", icon="⚠️")
llm_label = backend.split(":", 1)[1] if backend.startswith("ollama:") else "FakeLLM (тест)"
st.markdown(f'<div class="sr-sub">Локально: LLM <b>{html.escape(llm_label)}</b> через Ollama · эмбеддинги '
            f'<b>{html.escape(db.get_meta("run_embedder") or s.embeddings.model)}</b> · Chroma + BM25 · LangGraph · MCP (FastMCP, stdio) · '
            f'часы агента: <b>{short_dt(db.now().isoformat())}</b></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------------------------- сайдбар
with st.sidebar:
    st.markdown("### Сводка")
    decisions = db.decisions(100000)
    routes = pd.Series([d["route"] for d in decisions]).value_counts().to_dict() if decisions else {}
    acts = [d for d in decisions if d["route"] in {"act", "review"}]
    c1, c2 = st.columns(2)
    c1.metric("Постов", len(decisions))
    c2.metric("Важных", routes.get("act", 0))
    c1.metric("Шум (LLM)", routes.get("skip", 0))
    c2.metric("Префильтр", routes.get("dropped", 0))
    c1.metric("Дедлайнов", len(db.list_deadlines()))
    c2.metric("Фолбэков", f"{sum(1 for d in acts if d['fallback'])}/{len(acts)}")
    st.divider()
    st.markdown("### Демо")
    status = db.get_meta("run_status")
    if status == "running":
        total = int(db.get_meta("run_total") or 0)
        st.info(f"Идёт прогон сценария… обработано постов: {len(decisions)}")
        st.button("🔄 Обновить")
    else:
        scen = st.selectbox("Сценарий", sorted(p.name for p in (ROOT / "data/scenarios").glob("*.yaml")))
        if st.button("▶ Проиграть сценарий", type="primary", use_container_width=True,
                     help="Запускает make simulate в фоне (настоящая модель через Ollama)"):
            log = open(ROOT / "data/runtime/simulate.log", "w", encoding="utf-8")
            subprocess.Popen([sys.executable, "-m", "study_radar.cli", "simulate", "--scenario",
                              str(ROOT / "data/scenarios" / scen), "--delay", "0.5"], stdout=log, stderr=log, cwd=ROOT)
            db.set_meta("run_status", "running")
            st.rerun()
    st.caption("Лог прогона: data/runtime/simulate.log")


@st.fragment(run_every=3)
def auto_refresh():
    if db.get_meta("run_status") == "running":
        st.caption("⏳ авто-обновление…")
        if len(db.decisions(100000)) != len(decisions):
            st.rerun(scope="app")


with st.sidebar:
    auto_refresh()

tab_feed, tab_hub, tab_dl, tab_trace, tab_ask, tab_metrics = st.tabs(
    ["📥 Лента", "💬 Хаб", "📅 Дедлайны", "🔍 Трассировка агента", "❓ Спросить", "📊 Метрики"])

# ---------------------------------------------------------------------------------------------- Лента
with tab_feed:
    posts = {p["post_id"]: p for p in db.query("SELECT * FROM posts")}
    last = {}
    for d in sorted(decisions, key=lambda x: x["id"]):
        last[d["post_id"]] = d
    chats = sorted({p["chat_title"] for p in posts.values()})
    f1, f2, f3 = st.columns([3, 2, 2])
    sel_chats = f1.multiselect("Чаты", chats, default=chats, label_visibility="collapsed")
    only = f2.radio("Показать", ["все", "важные", "шум и отсеянные"], horizontal=True, label_visibility="collapsed")
    order = f3.radio("Порядок", ["по времени", "новые сверху"], horizontal=True, label_visibility="collapsed")
    items = sorted(last.values(), key=lambda d: posts.get(d["post_id"], {}).get("date", ""),
                   reverse=order == "новые сверху")
    if not items:
        st.info("Лента пуста. Запустите демо: кнопка «▶ Проиграть сценарий» слева или `make simulate`.")
    for d in items:
        p = posts.get(d["post_id"])
        if not p or p["chat_title"] not in sel_chats:
            continue
        route = d["route"]
        if only == "важные" and route not in {"act", "review"}:
            continue
        if only == "шум и отсеянные" and route in {"act", "review"}:
            continue
        cls = jload(d["classification"]) or {}
        pre = jload(d["prefilter"]) or {}
        css = {"act": "important", "review": "review", "skip": "noise", "dropped": "dropped"}[route]
        where = html.escape(p["chat_title"]) + (f" › {html.escape(p['topic'])}" if p["topic"] else "")
        edit = " · ✏️ отредактировано" if d["trigger"] == "edit" else ""
        if route == "dropped":
            right = badge("⏭ префильтр", "#f4f5f7", "#7a8290") + f'<span class="sr-reason">{html.escape(pre.get("reason", ""))}</span>'
        else:
            right = cat_badge(cls.get("category", "noise"))
            if cls.get("is_important"):
                right += badge(subj_name(cls.get("subject")), "#f4f6fa", "#2c3440")
                right += badge(f"уверенность {cls.get('confidence', 0):.2f}", "#f4f6fa", "#2c3440")
                if cls.get("due_at"):
                    right += badge("⏰ " + fmt_due(datetime.fromisoformat(cls["due_at"])), "#fff8e6", "#8a5a00")
            if route == "review":
                right += badge("🤔 на проверку", "#fff3cd", "#7a5a00")
            if d["fallback"]:
                right += badge("⟲ фолбэк", "#ffecec", "#b42318")
            right += f'<div class="sr-reason">💡 {html.escape(cls.get("reason", ""))}</div>'
        st.markdown(
            f'<div class="sr-card {css}"><div class="sr-meta">{where} · <b>{html.escape(p["author"] or "")}</b> · '
            f'{short_dt(p["date"])}{edit}</div><div class="sr-text">{html.escape(p["text"] or "")}</div>{right}</div>',
            unsafe_allow_html=True)

# ---------------------------------------------------------------------------------------------- Хаб
with tab_hub:
    topics = db.hub_topics()
    if not topics:
        st.info("Хаб ещё не создан — запустите демо.")
    else:
        msgs = db.hub_messages()
        counts = pd.Series([m["topic_key"] for m in msgs]).value_counts().to_dict() if msgs else {}
        left, right = st.columns([1, 3])
        with left:
            st.markdown("**Хаб ИУ5-31Б** · темы")
            labels = {k: f"{t['title']}  ·  {counts.get(k, 0)}" for k, t in topics.items()}
            cur = st.radio("Темы", list(topics), format_func=lambda k: labels[k], label_visibility="collapsed",
                           index=list(topics).index("deadlines") if "deadlines" in topics else 0)
            st.caption("Режим FakeHub: так агент раскладывает сообщения в супергруппе с темами. "
                       "В режиме бота то же самое происходит в настоящем Telegram.")
        with right:
            topic_msgs = [m for m in msgs if m["topic_key"] == cur]
            by_id = {m["id"]: m for m in msgs}
            body = []
            pinned = [m for m in topic_msgs if m["pinned"]]
            for m in pinned:
                body.append(f'<div class="tg-msg board"><div class="tg-pin">📌 Закреплённое сообщение · редактируется '
                            f'агентом</div>{tg_html(m["text"])}<div class="tg-time">изменено {short_dt(m["edited_at"] or m["created_at"])}</div></div>')
            for m in topic_msgs:
                if m["pinned"]:
                    continue
                reply = ""
                if m["reply_to"] and m["reply_to"] in by_id:
                    rt = re.sub(r"<[^>]+>", "", by_id[m["reply_to"]]["text"] or "")[:90]
                    reply = f'<div class="tg-reply">{html.escape(rt)}</div>'
                edited = " · изменено" if m["edited_at"] else ""
                if m["kind"] == "forward":
                    head = (f'<div class="tg-fwd">↪ Переслано из «{html.escape(m["source_chat_title"] or "")}» · '
                            f'{html.escape(m["source_author"] or "")}</div>')
                    body.append(f'<div class="tg-msg">{head}{reply}{tg_html(m["text"])}'
                                f'<div class="tg-time">{short_dt(m["source_date"])}</div></div>')
                else:
                    body.append(f'<div class="tg-msg bot"><div class="tg-author">🤖 StudyRadar</div>{reply}'
                                f'{tg_html(m["text"])}<div class="tg-time">{short_dt(m["created_at"])}{edited}</div></div>')
            if not body:
                body.append('<div class="tg-msg bot">Пока пусто</div>')
            st.markdown(f'<div class="tg-wrap"><div style="font-weight:700;margin-bottom:8px">{html.escape(topics[cur]["title"])}'
                        f'</div>{"".join(body)}</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------------------------- Дедлайны
with tab_dl:
    all_d = db.all_deadlines()
    if not all_d:
        st.info("Дедлайнов пока нет.")
    else:
        now = db.now()
        rows = []
        for d in all_d:
            status = {"active": "активен", "cancelled": "отменён", "done": "выполнен"}[d.status]
            if d.status == "active" and d.due_at and d.due_at < now and (d.due_at.hour, d.due_at.minute) != (0, 0):
                status = "прошёл"
            rows.append({"Срок": fmt_due(d.due_at), "Что": ("📌 " if d.kind == "event" else "") + d.title,
                         "Предмет": subj_name(d.subject), "Место": d.location or "", "Статус": status,
                         "Было": fmt_due(d.prev_due_at) if d.prev_due_at else (d.prev_location or ""),
                         "Источников": len(d.sources), "id": d.id})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                     column_config={"id": st.column_config.NumberColumn("id", width="small")})
        st.markdown("#### История изменений")
        st.caption("Каждое изменение — по какому сообщению и что было → что стало. Дубли из других чатов добавляются "
                   "как источники.")
        for d in all_d:
            hist = db.history(d.id)
            if len(hist) <= 1 and len(d.sources) <= 1:
                continue
            with st.expander(f"{subj_name(d.subject)} · {d.title} — {fmt_due(d.due_at)} ({len(hist)} событий)",
                             expanded=any(h["action"] == "updated" for h in hist)):
                for h in hist:
                    ch = jload(h["changes"]) or {}
                    parts = []
                    for k, (a, b) in ch.items():
                        a, b = a or "—", b or "—"
                        if k == "due_at":
                            a = fmt_due(datetime.fromisoformat(a)) if a != "—" else a
                            b = fmt_due(datetime.fromisoformat(b)) if b != "—" else b
                        parts.append(f"**{ {'due_at': 'срок', 'location': 'место', 'title': 'название', 'status': 'статус'}.get(k, k)}**: {a} → {b}")
                    act = {"created": "🆕 создан", "updated": "✏️ изменён", "source_added": "🔗 новый источник",
                           "closed": "❌ закрыт"}.get(h["action"], h["action"])
                    link = f" · [сообщение ↗]({h['source_link']})" if h["source_link"] else ""
                    st.markdown(f"- `{short_dt(h['ts'])}` {act} {'; '.join(parts)}{link}  \n  "
                                f"<span class='sr-meta'>{html.escape(h['note'] or '')}</span>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------------------------- Трассировка
with tab_trace:
    acts_all = [d for d in decisions if d["route"] in {"act", "review", "skip"}]
    if not acts_all:
        st.info("Нет решений агента — запустите демо.")
    else:
        posts = {p["post_id"]: p for p in db.query("SELECT post_id, text, chat_title FROM posts")}

        def label(d):
            c = jload(d["classification"]) or {}
            p = posts.get(d["post_id"], {})
            cat = CATEGORY_EMOJI[Category(c.get("category", "noise"))]
            fb = " ⟲" if d["fallback"] else ""
            edit = " ✏️" if d["trigger"] == "edit" else ""
            return f"#{d['id']} {cat}{edit}{fb} {p.get('chat_title', '')}: {(p.get('text') or '')[:60]}"

        ordered = sorted(acts_all, key=lambda d: d["id"])
        interesting = next((d for d in ordered if (jload(d["classification"]) or {}).get("category") == "deadline_update"
                            and d["trigger"] != "edit"), ordered[-1])
        dsel = st.selectbox("Решение агента", ordered, index=ordered.index(interesting), format_func=label)
        ctx = jload(dsel["context"]) or {}
        cls = jload(dsel["classification"]) or {}
        calls = jload(dsel["tool_calls"]) or []
        timings = jload(dsel["timings"]) or {}
        post = ctx.get("post", {})

        st.markdown("`ingest` → `prefilter` → `build_context (RAG)` → `classify` → `act (LLM + MCP)` → `verify` → `log`  "
                    f"· всего **{(dsel['latency_ms'] or 0) / 1000:.1f} с** · LLM: {html.escape(dsel['model'] or '')}")
        c1, c2 = st.columns([1, 1])
        with c1:
            st.markdown('<div class="sr-step">1. Сообщение</div>', unsafe_allow_html=True)
            st.markdown(f"**{post.get('chat_title', '')}**{' › ' + post['topic'] if post.get('topic') else ''} · "
                        f"{post.get('author', '')} · {post.get('date_human', '')}")
            st.code(post.get("text", ""), language=None, wrap_lines=True)
            if post.get("prev_text"):
                st.caption("Было до редактирования:")
                st.code(post["prev_text"], language=None, wrap_lines=True)
            pre = jload(dsel["prefilter"]) or {}
            st.markdown(f'<div class="sr-step">2. Префильтр</div>✅ пропущено: {html.escape(pre.get("reason", ""))}',
                        unsafe_allow_html=True)
            st.markdown('<div class="sr-step">3. RAG-контекст</div>', unsafe_allow_html=True)
            if ctx.get("reply"):
                st.markdown(f"**Ответ на:** {ctx['reply']['author']}: «{ctx['reply']['text']}»")
            if ctx.get("previous"):
                st.markdown("**Предыдущие сообщения:** " + " · ".join(f"{r['author']}: «{r['text'][:60]}»"
                                                                     for r in ctx["previous"]))
            sim = [x for x in ctx.get("similar") or [] if "error" not in x]
            if sim:
                st.markdown("**Похожие важные сообщения** (гибрид: Chroma + BM25 → RRF)")
                st.dataframe(pd.DataFrame([{"#": x["n"], "чат": x["chat_title"], "текст": x["text"][:80],
                                            "вектор": x["vector_rank"], "BM25": x["bm25_rank"], "RRF": x["score"]}
                                           for x in sim]), hide_index=True, use_container_width=True)
            st.markdown("**Дедлайны-кандидаты на обновление**")
            if ctx.get("candidates"):
                st.dataframe(pd.DataFrame([{"id": x["id"], "предмет": x["subject_name"], "что": x["title"],
                                            "срок": x["due"], "найден": x["origin"]} for x in ctx["candidates"]]),
                             hide_index=True, use_container_width=True)
            else:
                st.caption("нет")
            if ctx.get("subject_guess"):
                st.caption(f"Подсказка по алиасам: {ctx['subject_guess']}")
            with st.expander("Обогащённый промпт классификатора (user-часть)"):
                st.code(dsel["prompt"] or "", language="markdown", wrap_lines=True)
        with c2:
            st.markdown('<div class="sr-step">4. JSON-классификация (LLM, format=JSON Schema)</div>',
                        unsafe_allow_html=True)
            st.json(cls, expanded=True)
            notes = jload(dsel["validation"]) or []
            if notes:
                st.markdown("**Валидация кодом:**\n" + "\n".join(f"- {n}" for n in notes))
            st.markdown('<div class="sr-step">5. MCP-вызовы (stdio → FastMCP-сервер)</div>', unsafe_allow_html=True)
            if dsel["fallback"]:
                st.error(f"⟲ Фолбэк: {dsel['fallback_reason']}")
            if not calls:
                st.caption("Нет вызовов (шум — действия не нужны)")
            for i, c in enumerate(calls, 1):
                who = {"llm": "🧠 LLM", "fallback": "⟲ фолбэк", "verify": "✔ verify",
                       "executor": "⚙ исполнитель"}.get(c["by"], c["by"])
                ok = "✅" if c["ok"] else "❌"
                res = c["result"]
                res_s = json.dumps(res, ensure_ascii=False) if not isinstance(res, str) else res
                st.markdown(
                    f'<div class="sr-call {c["by"]}"><b>{i}. {ok} {html.escape(c["tool"])}</b> · {who} · '
                    f'попытка {c.get("attempt", 1)} · {c.get("ms", 0)} мс<br><code>{html.escape(json.dumps(c["args"], ensure_ascii=False))[:600]}</code>'
                    f'<br><span class="sr-meta">→ {html.escape(res_s[:400])}</span></div>', unsafe_allow_html=True)
            ver = jload(dsel["verify"]) or []
            if ver:
                st.markdown('<div class="sr-step">6. Verify</div>' + "<br>".join(
                    f"{'✅' if v['ok'] else '❌'} {html.escape(v['check'])}" for v in ver), unsafe_allow_html=True)
            if timings:
                st.markdown('<div class="sr-step">Время по узлам графа</div>', unsafe_allow_html=True)
                st.dataframe(pd.DataFrame([{"узел": k, "мс": v} for k, v in timings.items()]), hide_index=True)
            with st.expander("Промпт узла act"):
                st.code(dsel["act_prompt"] or "", language="markdown", wrap_lines=True)

# ---------------------------------------------------------------------------------------------- Спросить
with tab_ask:
    st.markdown("Модуль 2 — **RAG**: вопрос → гибридный поиск по сообщениям и дедлайнам → обогащённый промпт → "
                "LLM → ответ **со ссылками** на исходные сообщения.")
    examples = ["Когда сдавать лабу по элтеху?", "Где и во сколько кейс-чемпионат?", "Что задали по физике?",
                "Когда экзамен по химии?"]
    cols = st.columns(len(examples))
    preset = None
    for col, ex in zip(cols, examples):
        if col.button(ex, use_container_width=True):
            preset = ex
    q = st.chat_input("Ваш вопрос по переписке…") or preset
    if q:
        from study_radar.agent.ask import Asker

        with st.chat_message("user"):
            st.write(q)
        with st.chat_message("assistant", avatar="📡"):
            try:
                with st.spinner("Ищу в переписке и спрашиваю модель…"):
                    res = asyncio.run(Asker(sv, sv.llm).ask(q))
                st.markdown(res.answer)
                for src in res.sources:
                    link = f" — [открыть ↗]({src['link']})" if src.get("link") else ""
                    st.markdown(f"<span class='sr-meta'>[{src['n']}] {html.escape(src['label'])}</span>{link}",
                                unsafe_allow_html=True)
                st.caption(f"{sv.llm.label} · {res.ms / 1000:.1f} с")
            except Exception as e:
                st.error(f"Не удалось получить ответ: {e}. Проверьте, что Ollama запущена (`make doctor`).")
    hist = db.asks(10)
    if hist:
        with st.expander("Предыдущие вопросы"):
            for a in hist:
                st.markdown(f"**{html.escape(a['question'])}**  \n{a['answer']}  \n"
                            f"<span class='sr-meta'>{a['model']} · {(a['latency_ms'] or 0) / 1000:.1f} с</span>",
                            unsafe_allow_html=True)

# ---------------------------------------------------------------------------------------------- Метрики
with tab_metrics:
    rep = ROOT / "docs/eval_report.json"
    if not rep.exists():
        st.info("Метрик пока нет. Запустите `make eval` на ноутбуке с Ollama — отчёт появится здесь и в "
                "docs/eval_report.md. (Метрики считаются только на настоящей модели.)")
    else:
        data = json.loads(rep.read_text(encoding="utf-8"))
        st.markdown(f"**Модель:** {data['model']} · **железо:** {data['hardware']} · **дата:** {data['date']} · "
                    f"постов в датасете: {data['n_posts']}")
        m, b = data["metrics"], data["baseline"]
        rows = [
            ("Precision (важность)", m["precision"], b["precision"]), ("Recall (важность)", m["recall"], b["recall"]),
            ("F1 (важность)", m["f1"], b["f1"]), ("Точность предмета", m["subject_acc"], b["subject_acc"]),
            ("Точность категории", m["category_acc"], b["category_acc"]),
            ("Точность даты (день)", m["date_acc"], b.get("date_acc")),
        ]
        df = pd.DataFrame([{"Метрика": r[0], "StudyRadar (LLM)": r[1], "Бейзлайн: ключевые слова": r[2]} for r in rows])
        st.dataframe(df, hide_index=True, use_container_width=True, column_config={
            "StudyRadar (LLM)": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1),
            "Бейзлайн: ключевые слова": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1)})
        c1, c2, c3 = st.columns(3)
        c1.metric("MCP-действия без фолбэка", f"{m['mcp_no_fallback']:.0%}")
        c2.metric("Корректных вызовов LLM", f"{m['mcp_call_ok']:.0%}")
        c3.metric("Средняя задержка на пост", f"{m['latency_avg_s']:.1f} с")
        if data.get("errors"):
            with st.expander("Ошибки модели (для слайда «Трудности»)"):
                st.dataframe(pd.DataFrame(data["errors"]), hide_index=True, use_container_width=True)
