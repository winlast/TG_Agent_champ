"""LangGraph-агент StudyRadar.

    ingest → prefilter → build_context (RAG) → classify → act (LLM + MCP-инструменты) → verify → log

* ingest  — пост в SQLite и в векторную БД (Модуль 1);
* build_context — reply, соседи, гибридный поиск похожих сообщений и дедлайнов-кандидатов (RAG);
* classify — строгий JSON от LLM + валидация кодом;
* act     — LLM САМА вызывает инструменты MCP-сервера (Модуль 3, tool calling). Если за
            max_attempts попыток вызовы некорректны — детерминированный исполнитель доделывает
            недостающее через те же MCP-инструменты (фолбэк пишется в журнал);
* verify  — проверка эффектов (переслано, дедлайн в БД, доска обновлена);
* log     — журнал решений (SQLite), метаданные в индексе, «🧾 Журнал агента» в хабе.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import time
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from study_radar.agent.classify import Classifier, ClassifyResult, load_prompt
from study_radar.agent.context import build_context
from study_radar.agent.dates import fmt_due, same_day
from study_radar.agent.mcp_client import MCPToolbox
from study_radar.agent.prefilter import Prefilter
from study_radar.config import Settings
from study_radar.index.indexer import post_document
from study_radar.models import CATEGORY_EMOJI, CATEGORY_RU, Category, Classification, Post, ToolCallRecord
from study_radar.services import Services
from study_radar.telegram.notes import subject_name, suggested_note

log = logging.getLogger(__name__)

DEADLINE_TOOLS = {"create_deadline", "update_deadline", "close_deadline"}


class AgentState(TypedDict, total=False):
    post: Post
    trigger: str
    embedding: list[float]
    prefilter: dict[str, Any]
    context: dict[str, Any]
    result: ClassifyResult
    route: str
    plan: list[dict[str, Any]]
    act_prompt: str
    tool_calls: list[dict[str, Any]]
    fallback: bool
    fallback_reason: str | None
    verify: list[dict[str, Any]]
    timings: dict[str, int]
    decision_id: int
    started: float
    error: str | None


# ---------------------------------------------------------------------- план действий
def build_plan(c: Classification, post: Post, route: str, note: str) -> list[dict[str, Any]]:
    """Ожидаемые вызовы инструментов по классификации (и для проверки LLM, и для фолбэка)."""
    ref = f"{post.chat_id}:{post.message_ids[0]}"
    if route == "review":
        return [{"tool": "flag_for_review", "args": {"chat_id": post.chat_id, "message_ids": post.message_ids,
                                                     "reason": f"{c.reason} (уверенность {c.confidence:.2f})"}}]
    due = c.due_at.isoformat() if c.due_at else None
    plan: list[dict[str, Any]] = [{"tool": "forward_to_topic", "args": {
        "subject": c.subject, "source_chat_id": post.chat_id, "message_ids": post.message_ids, "note": note}}]
    cat = c.category
    if cat in {Category.DEADLINE, Category.EVENT, Category.ASSIGNMENT}:
        if c.related_deadline_id:
            plan.append({"tool": "update_deadline", "args": {
                "deadline_id": c.related_deadline_id, "due_at": due, "location": c.location, "source_ref": ref,
                "change_note": "то же самое в другом сообщении"}})
        else:
            kind = "event" if cat == Category.EVENT else ("assignment" if cat == Category.ASSIGNMENT and not due
                                                          else "deadline")
            plan.append({"tool": "create_deadline", "args": {
                "subject": c.subject, "title": c.title, "due_at": due, "due_text": c.due_text,
                "location": c.location, "source_refs": post.source_refs, "kind": kind}})
    elif cat == Category.DEADLINE_UPDATE and c.related_deadline_id:
        plan.append({"tool": "update_deadline", "args": {
            "deadline_id": c.related_deadline_id, "due_at": due, "location": c.location, "source_ref": ref,
            "change_note": c.summary}})
    elif cat == Category.CANCELLATION and c.related_deadline_id:
        plan.append({"tool": "close_deadline", "args": {"deadline_id": c.related_deadline_id, "reason": c.summary}})
    if any(p["tool"] in DEADLINE_TOOLS for p in plan):
        plan.append({"tool": "refresh_deadlines_board", "args": {}})
    return plan


def _norm_subject(settings: Settings, v: Any) -> str | None:
    if v is None or str(v).strip().lower() in {"", "none", "null", "general", "общее"}:
        return None
    v = str(v).strip()
    if v in settings.subjects:
        return v
    for k, s in settings.subjects.items():
        if v.lower() in {s.name.lower(), *(a.lower() for a in s.aliases)}:
            return k
    return v


def _result_id(res: Any) -> int | None:
    if isinstance(res, dict):
        return res.get("duplicate_of") or res.get("id")
    return None


def _call_matches(settings: Settings, item: dict[str, Any], rec: ToolCallRecord) -> bool:
    if not rec.ok:
        return False
    p, a = item["args"], rec.args
    tool = item["tool"]
    if tool == "update_deadline" and rec.tool == "create_deadline":   # LLM создала, а сервер нашёл дубль
        return _result_id(rec.result) == p["deadline_id"]
    if rec.tool != tool:
        return False
    if tool == "forward_to_topic":
        try:
            ids = {int(x) for x in a.get("message_ids") or []}
        except (TypeError, ValueError):
            return False
        return _norm_subject(settings, a.get("subject")) == p["subject"] and ids >= set(p["message_ids"])
    if tool in {"update_deadline", "close_deadline"}:
        try:
            return int(a.get("deadline_id", -1)) == p["deadline_id"]
        except (TypeError, ValueError):
            return False
    return True


def missing_actions(settings: Settings, plan: list[dict[str, Any]], records: list[ToolCallRecord]) -> list[dict]:
    """Какие обязательные действия плана LLM не выполнила корректно."""
    missing = []
    for item in plan:
        if item["tool"] == "refresh_deadlines_board":
            continue   # доску гарантирует verify
        if not any(_call_matches(settings, item, r) for r in records):
            missing.append(item)
    return missing


def wrong_dates(plan: list[dict[str, Any]], records: list[ToolCallRecord]) -> list[tuple[int, str]]:
    """create_deadline выполнен, но с датой не того дня, что после валидации → (id, правильная дата)."""
    want = next((p["args"].get("due_at") for p in plan if p["tool"] == "create_deadline"), None)
    out = []
    if not want:
        return out
    for r in records:
        if r.tool == "create_deadline" and r.ok and _result_id(r.result):
            got = r.args.get("due_at")
            try:
                ok = got and same_day(datetime.fromisoformat(str(got)), datetime.fromisoformat(want))
            except ValueError:
                ok = False
            if not ok:
                out.append((_result_id(r.result), want))
    return out


def render_act_user(post: Post, c: Classification, ctx: dict[str, Any], route: str, note: str) -> str:
    data = c.model_dump(mode="json")
    lines = [
        "# Пост", f"source_chat_id: {post.chat_id}", f"message_ids: {json.dumps(post.message_ids)}",
        f"source_refs: {json.dumps(post.source_refs)}",
        f"Чат «{post.chat_title}»{', топик «' + post.topic + '»' if post.topic else ''} · {post.author} · "
        f"{fmt_due(post.date)}", "Текст: «" + post.text[:800] + "»", "",
        "# Решение классификатора", "```json", json.dumps(data, ensure_ascii=False, indent=1), "```", "",
        "# Известные дедлайны-кандидаты",
    ]
    cands = ctx.get("candidates") or []
    lines += [f"- id={x['id']} · {x['subject']} · {x['title']} · {x['kind']} · срок {x['due']}" for x in cands] or ["- нет"]
    lines += ["", "# Пометка (передай в note без изменений)", note, ""]
    if route == "review":
        lines.append("# Маршрут: НА ПРОВЕРКУ. Уверенность низкая — вызови только flag_for_review(chat_id, "
                     "message_ids, reason) с кратким объяснением сомнения.")
    else:
        lines.append(f"Категория: {c.category.value}. Вызови нужные инструменты по политике.")
    return "\n".join(lines)


# ---------------------------------------------------------------------- runtime
class AgentRuntime:
    """Держит открытую MCP-сессию и скомпилированный граф. Используется как async context manager."""

    def __init__(self, settings: Settings, hub_backend: str = "fake", services: Services | None = None):
        self.s = settings
        self.sv = services or Services(settings, hub_backend)
        self.hub_backend = hub_backend
        self.llm = self.sv.llm
        self.prefilter = Prefilter(settings, self.sv.embedder)
        self.classifier = Classifier(settings, self.llm)
        self.toolbox = MCPToolbox(settings, hub_backend)
        self.act_system = load_prompt("act.md")
        self.graph = self._build_graph()
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "AgentRuntime":
        await self.toolbox.__aenter__()
        await self.sv.hub.ensure_topics()
        await asyncio.to_thread(self.sv.indexer.index_subjects)
        await self.toolbox.call("refresh_deadlines_board", {})
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.toolbox.__aexit__(*exc)

    async def process(self, post: Post, trigger: str = "new") -> AgentState:
        async with self._lock:   # одна модель — по одному посту за раз
            return await self.graph.ainvoke({"post": post, "trigger": trigger, "timings": {},
                                             "started": time.perf_counter(), "tool_calls": []})

    # ------------------------------------------------------------------ graph
    def _build_graph(self):
        g = StateGraph(AgentState)
        g.add_node("ingest", self.n_ingest)
        g.add_node("prefilter", self.n_prefilter)
        g.add_node("build_context", self.n_build_context)
        g.add_node("classify", self.n_classify)
        g.add_node("act", self.n_act)
        g.add_node("verify", self.n_verify)
        g.add_node("log", self.n_log)
        g.add_edge(START, "ingest")
        g.add_edge("ingest", "prefilter")
        g.add_conditional_edges("prefilter", lambda st: "build_context" if st["prefilter"]["passed"] else "log",
                                {"build_context": "build_context", "log": "log"})
        g.add_edge("build_context", "classify")
        g.add_conditional_edges("classify", lambda st: "act" if st["route"] in {"act", "review"} else "log",
                                {"act": "act", "log": "log"})
        g.add_edge("act", "verify")
        g.add_edge("verify", "log")
        g.add_edge("log", END)
        return g.compile()

    @staticmethod
    def _t(state: AgentState, name: str, t0: float) -> dict[str, int]:
        return {**state.get("timings", {}), name: int((time.perf_counter() - t0) * 1000)}

    async def n_ingest(self, state: AgentState) -> dict[str, Any]:
        """Модуль 1: пост → SQLite + эмбеддинг (bge-m3) → Chroma."""
        t0 = time.perf_counter()
        post = state["post"]
        emb = await asyncio.to_thread(self.sv.embedder.embed_one, post_document(post))
        await asyncio.to_thread(self.sv.indexer.index_posts, [post], [emb])
        return {"embedding": emb, "timings": self._t(state, "ingest", t0)}

    async def n_prefilter(self, state: AgentState) -> dict[str, Any]:
        t0 = time.perf_counter()
        post = state["post"]
        force = False
        if state.get("trigger") == "edit":
            row = self.sv.db.get_post_row(post.post_id) or {}
            force = row.get("status") in {"important", "review"} or bool(
                self.sv.db.deadlines_for_messages(post.chat_id, post.message_ids))
        res = self.prefilter.check(post, state.get("embedding"), force=force)
        out: dict[str, Any] = {"prefilter": res.model_dump(), "timings": self._t(state, "prefilter", t0)}
        if not res.passed:
            out["route"] = "dropped"
        return out

    async def n_build_context(self, state: AgentState) -> dict[str, Any]:
        t0 = time.perf_counter()
        ctx = await asyncio.to_thread(build_context, self.sv, state["post"], state.get("embedding"))
        return {"context": ctx, "timings": self._t(state, "build_context", t0)}

    async def n_classify(self, state: AgentState) -> dict[str, Any]:
        t0 = time.perf_counter()
        res = await self.classifier.classify(state["context"])
        return {"result": res, "route": res.route, "timings": self._t(state, "classify", t0)}

    async def n_act(self, state: AgentState) -> dict[str, Any]:
        """Модуль 3: LLM сама вызывает инструменты MCP-сервера; фолбэк — детерминированный исполнитель."""
        t0 = time.perf_counter()
        post, res, route = state["post"], state["result"], state["route"]
        c = res.classification
        note = suggested_note(self.s, c.category, c.subject, c.title, c.due_at, c.location, c.confidence)
        plan = build_plan(c, post, route, note)
        user = render_act_user(post, c, state["context"], route, note)
        records: list[ToolCallRecord] = []
        fallback, reason = False, None

        if self.s.agent.act_mode == "deterministic":
            missing = plan
            reason = "act_mode=deterministic (LLM не вызывает инструменты)"
        else:
            messages: list[dict[str, Any]] = [{"role": "system", "content": self.act_system},
                                              {"role": "user", "content": user}]
            specs = [t for t in self.toolbox.ollama_specs() if t["function"]["name"] in self.s.agent.act_tools]
            missing = plan
            for attempt in range(1, self.s.agent.max_attempts + 1):
                for _step in range(self.s.agent.max_steps):
                    try:
                        reply = await self.llm.chat(messages, specs, hint={"plan": plan})
                    except Exception as e:  # сбой модели/сети — сразу к фолбэку
                        log.warning("LLM tool calling упал: %s", e)
                        reason = f"ошибка LLM: {e}"
                        break
                    messages.append({"role": "assistant", "content": reply.content or "",
                                     "tool_calls": [{"function": {"name": tc.name, "arguments": tc.args}}
                                                    for tc in reply.tool_calls]})
                    if not reply.tool_calls:
                        break
                    for tc in reply.tool_calls:
                        ok, out, ms = await self.toolbox.call(tc.name, tc.args)
                        records.append(ToolCallRecord(tool=tc.name, args=tc.args, result=out, ok=ok, by="llm",
                                                      attempt=attempt, ms=ms))
                        messages.append({"role": "tool", "tool_name": tc.name,
                                         "content": json.dumps(out, ensure_ascii=False, default=str)[:2000]})
                missing = missing_actions(self.s, plan, records)
                if not missing:
                    break
                if attempt < self.s.agent.max_attempts:
                    hint = "; ".join(f"{m['tool']}({json.dumps(m['args'], ensure_ascii=False)[:300]})" for m in missing)
                    messages.append({"role": "user", "content": "Не все действия выполнены корректно. "
                                     f"Не хватает: {hint}. Вызови недостающие инструменты с правильными аргументами."})
            if missing:
                bad = [r for r in records if not r.ok]
                reason = reason or (f"после {self.s.agent.max_attempts} попыток не выполнено: "
                                    + ", ".join(m["tool"] for m in missing)
                                    + (f"; ошибочных вызовов: {len(bad)}" if bad else ""))
        executor = "executor" if self.s.agent.act_mode == "deterministic" else "fallback"
        for item in missing:
            ok, out, ms = await self.toolbox.call(item["tool"], item["args"])
            records.append(ToolCallRecord(tool=item["tool"], args=item["args"], result=out, ok=ok, by=executor, ms=ms))
            fallback = fallback or executor == "fallback"
        for dl_id, due in wrong_dates(plan, records):   # LLM ошиблась в дате — исправляем через MCP
            ok, out, ms = await self.toolbox.call("update_deadline", {
                "deadline_id": dl_id, "due_at": due, "change_note": "исправление даты после валидации"})
            records.append(ToolCallRecord(tool="update_deadline", args={"deadline_id": dl_id, "due_at": due},
                                          result=out, ok=ok, by="fallback", ms=ms))
            fallback = True
            reason = (reason + "; " if reason else "") + "LLM передала неверную дату — исправлено"
        if fallback:
            log.info("Фолбэк для %s: %s", post.post_id, reason)
        return {"plan": plan, "act_prompt": user, "tool_calls": [r.model_dump(mode="json") for r in records],
                "fallback": fallback, "fallback_reason": reason,
                "timings": self._t(state, "act", t0)}

    async def n_verify(self, state: AgentState) -> dict[str, Any]:
        t0 = time.perf_counter()
        db, post = self.sv.db, state["post"]
        plan_tools = {p["tool"] for p in state.get("plan") or []}
        calls = list(state.get("tool_calls") or [])
        checks: list[dict[str, Any]] = []
        if "forward_to_topic" in plan_tools:
            ok = any(db.hub_forwards_of(post.chat_id, m) for m in post.message_ids)
            checks.append({"check": "сообщение переслано в хаб", "ok": ok})
        if "flag_for_review" in plan_tools:
            ok = any(db.hub_forwards_of(post.chat_id, m, "review") for m in post.message_ids)
            checks.append({"check": "отправлено «На проверку»", "ok": ok})
        if plan_tools & {"create_deadline", "update_deadline"}:
            ok = bool(db.deadlines_for_messages(post.chat_id, post.message_ids))
            checks.append({"check": "дедлайн в БД со ссылкой на источник", "ok": ok})
        if "close_deadline" in plan_tools:
            dl = next(p["args"]["deadline_id"] for p in state["plan"] if p["tool"] == "close_deadline")
            d = db.get_deadline(dl)
            checks.append({"check": "дедлайн закрыт", "ok": bool(d and d.status != "active")})
        changed = [i for i, x in enumerate(calls) if x["ok"] and x["tool"] in DEADLINE_TOOLS]
        if changed:
            refreshed = any(x["ok"] and x["tool"] == "refresh_deadlines_board" for x in calls[changed[-1] + 1:])
            if not refreshed:
                ok, out, ms = await self.toolbox.call("refresh_deadlines_board", {})
                calls.append(ToolCallRecord(tool="refresh_deadlines_board", args={}, result=out, ok=ok,
                                            by="verify", ms=ms).model_dump(mode="json"))
                checks.append({"check": "доска обновлена (verify вызвал refresh_deadlines_board)", "ok": ok})
            else:
                checks.append({"check": "доска дедлайнов обновлена", "ok": True})
        return {"verify": checks, "tool_calls": calls, "timings": self._t(state, "verify", t0)}

    async def n_log(self, state: AgentState) -> dict[str, Any]:
        t0 = time.perf_counter()
        db, post = self.sv.db, state["post"]
        route = state.get("route", "dropped")
        res: ClassifyResult | None = state.get("result")
        c = res.classification if res else None
        status = {"dropped": "dropped", "skip": "noise", "act": "important", "review": "review"}[route]
        cdict = c.model_dump(mode="json") if c else None
        db.set_post_status(post.post_id, status, cdict)
        await asyncio.to_thread(self.sv.indexer.mark_post, post.post_id, c)
        await asyncio.to_thread(self.sv.indexer.sync_deadlines)
        timings = self._t(state, "log", t0)
        total = int((time.perf_counter() - state["started"]) * 1000)
        ctx = state.get("context")
        decision_id = db.add_decision({
            "post_id": post.post_id, "created_at": db.now().isoformat(), "trigger": state.get("trigger"),
            "backend": self.llm.name, "model": self.llm.model, "prefilter": state.get("prefilter"),
            "context": ctx, "prompt": (res.user if res else None), "raw_output": (res.raw if res else None),
            "classification": cdict, "validation": (res.notes if res else None), "route": route,
            "act_prompt": state.get("act_prompt"), "tool_calls": state.get("tool_calls"),
            "fallback": state.get("fallback", False), "fallback_reason": state.get("fallback_reason"),
            "verify": state.get("verify"), "latency_ms": total, "timings": timings,
        })
        if self.s.hub.journal and route != "dropped":
            try:
                await self.sv.hub.send("journal", self._journal_text(state, c), kind="journal")
            except Exception as e:  # журнал не должен ронять агента
                log.warning("Не удалось записать в журнал хаба: %s", e)
        return {"decision_id": decision_id, "timings": timings}

    def _journal_text(self, state: AgentState, c: Classification | None) -> str:
        post = state["post"]
        snippet = html.escape(post.text[:80].replace("\n", " ")) + ("…" if len(post.text) > 80 else "")
        head = f"🧾 «{snippet}» ({html.escape(post.chat_title)} · {html.escape(post.author)})"
        if c is None:
            return head + " → пропущено префильтром"
        verdict = f"{CATEGORY_EMOJI[c.category]} {CATEGORY_RU[c.category]}"
        if c.is_important:
            verdict += f" · {html.escape(subject_name(self.s, c.subject))}"
        s = f"{head} → {verdict}, уверенность {c.confidence:.2f}\nПочему: {html.escape(c.reason)}"
        calls = state.get("tool_calls") or []
        if calls:
            s += "\nMCP: " + ", ".join(f"{x['tool']}{'' if x['ok'] else '✗'}" + ("⟲" if x["by"] == "fallback" else "")
                                       for x in calls)
        if state.get("fallback"):
            s += f"\n⟲ фолбэк: {html.escape(state.get('fallback_reason') or '')}"
        return s
