"""SQLite-хранилище: сообщения, посты, решения агента, дедлайны и их история, хаб.

Одна БД используется двумя процессами (агент и MCP-сервер), поэтому WAL + busy_timeout.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from study_radar.models import ChatMessage, Deadline, Post

SCHEMA = Path(__file__).with_name("migrations.sql")


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


def text_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


class Database:
    def __init__(self, path: Path | str, tz: ZoneInfo | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.tz = tz or ZoneInfo("Europe/Moscow")
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self.conn.executescript(SCHEMA.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ base
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        self.conn.close()

    def reset(self) -> None:
        """Полная очистка рабочих данных (для повторного прогона демо)."""
        with self._lock:
            for t in ("messages", "posts", "decisions", "deadlines", "deadline_sources", "deadline_history",
                      "hub_messages", "board_pages", "asks", "chats", "source_topics"):
                self.conn.execute(f"DELETE FROM {t}")
            self.conn.execute("DELETE FROM meta WHERE key NOT IN ('hub_chat_id')")
            self.conn.execute("DELETE FROM sqlite_sequence")
            # топики реального хаба сохраняем, у фейкового — сбрасываем
            self.conn.execute("DELETE FROM hub_topics WHERE thread_id IS NULL OR thread_id < 0")

    # ------------------------------------------------------------------ meta / clock
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else default

    def set_meta(self, key: str, value: str | None) -> None:
        self.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, value))

    def now(self) -> datetime:
        """«Текущее» время агента. В режиме симуляции — время сценария."""
        sim = self.get_meta("clock_now")
        if sim:
            return datetime.fromisoformat(sim)
        return datetime.now(self.tz).replace(microsecond=0)

    def set_clock(self, dt: datetime | None) -> None:
        self.set_meta("clock_now", iso(dt) if dt else None)
        if dt is None:
            self.execute("DELETE FROM meta WHERE key='clock_now'")

    # ------------------------------------------------------------------ chats & messages
    def upsert_chat(self, chat_id: int, title: str, kind: str = "", is_forum: bool = False) -> None:
        self.execute(
            "INSERT INTO chats(chat_id, title, kind, is_forum) VALUES(?,?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, kind=excluded.kind, "
            "is_forum=MAX(chats.is_forum, excluded.is_forum)",
            (chat_id, title, kind, int(is_forum)),
        )

    def set_source_topic(self, chat_id: int, thread_id: int, name: str) -> None:
        self.execute("INSERT OR REPLACE INTO source_topics(chat_id, thread_id, name) VALUES(?,?,?)",
                     (chat_id, thread_id, name))

    def get_source_topic(self, chat_id: int, thread_id: int | None) -> str | None:
        if thread_id is None:
            return None
        row = self.one("SELECT name FROM source_topics WHERE chat_id=? AND thread_id=?", (chat_id, thread_id))
        return row["name"] if row else None

    def upsert_message(self, m: ChatMessage) -> bool:
        """Сохраняет сообщение. Возвращает True, если текст изменился (редактирование)."""
        old = self.get_message(m.chat_id, m.message_id)
        changed = old is not None and (old["text"] or "") != (m.text or "")
        prev_text = old["text"] if changed else (old["prev_text"] if old else None)
        self.execute(
            """INSERT INTO messages(chat_id, message_id, chat_title, topic, thread_id, author, author_id, date, edited,
                   text, prev_text, reply_to, file_name, is_service, service_action, link, post_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(chat_id, message_id) DO UPDATE SET
                   chat_title=excluded.chat_title, topic=COALESCE(excluded.topic, messages.topic),
                   thread_id=COALESCE(excluded.thread_id, messages.thread_id), author=excluded.author,
                   edited=excluded.edited, text=excluded.text, prev_text=excluded.prev_text,
                   reply_to=excluded.reply_to, file_name=excluded.file_name, link=excluded.link""",
            (m.chat_id, m.message_id, m.chat_title, m.topic, m.thread_id, m.author, m.author_id, iso(m.date),
             iso(m.edited), m.text, prev_text, m.reply_to, m.file_name, int(m.is_service), m.service_action,
             m.link, old["post_id"] if old else None),
        )
        return changed

    def get_message(self, chat_id: int, message_id: int) -> dict[str, Any] | None:
        return self.one("SELECT * FROM messages WHERE chat_id=? AND message_id=?", (chat_id, message_id))

    def get_messages(self, chat_id: int, message_ids: Iterable[int]) -> list[dict[str, Any]]:
        ids = list(message_ids)
        if not ids:
            return []
        q = ",".join("?" * len(ids))
        return self.query(f"SELECT * FROM messages WHERE chat_id=? AND message_id IN ({q}) ORDER BY message_id",
                          (chat_id, *ids))

    def messages_before(self, chat_id: int, topic: str | None, before: datetime, after: datetime,
                        limit: int, exclude_ids: Iterable[int] = ()) -> list[dict[str, Any]]:
        excl = list(exclude_ids)
        sql = ("SELECT * FROM messages WHERE chat_id=? AND is_service=0 AND date<? AND date>=? "
               "AND COALESCE(topic,'')=COALESCE(?,'')")
        params: list[Any] = [chat_id, iso(before), iso(after), topic]
        if excl:
            sql += f" AND message_id NOT IN ({','.join('?' * len(excl))})"
            params += excl
        sql += " ORDER BY date DESC LIMIT ?"
        params.append(limit)
        return list(reversed(self.query(sql, params)))

    def neighbors(self, chat_id: int, message_id: int, n: int = 3) -> list[dict[str, Any]]:
        msg = self.get_message(chat_id, message_id)
        if not msg:
            return []
        before = self.query("SELECT * FROM messages WHERE chat_id=? AND is_service=0 AND COALESCE(topic,'')=COALESCE(?,'') "
                            "AND date<=? AND message_id<>? ORDER BY date DESC LIMIT ?",
                            (chat_id, msg["topic"], msg["date"], message_id, n))
        after = self.query("SELECT * FROM messages WHERE chat_id=? AND is_service=0 AND COALESCE(topic,'')=COALESCE(?,'') "
                           "AND date>=? AND message_id<>? ORDER BY date ASC LIMIT ?",
                           (chat_id, msg["topic"], msg["date"], message_id, n))
        return list(reversed(before)) + after

    # ------------------------------------------------------------------ posts
    def upsert_post(self, p: Post) -> bool:
        """Сохраняет пост. Возвращает True, если пост новый или его текст изменился."""
        old = self.one("SELECT text FROM posts WHERE post_id=?", (p.post_id,))
        changed = old is None or old["text"] != p.text
        self.execute(
            """INSERT INTO posts(post_id, chat_id, chat_title, topic, thread_id, author, author_id, date, message_ids,
                   text, reply_to, link, edited, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(post_id) DO UPDATE SET text=excluded.text, message_ids=excluded.message_ids,
                   edited=MAX(posts.edited, excluded.edited), topic=excluded.topic, chat_title=excluded.chat_title,
                   updated_at=excluded.updated_at""",
            (p.post_id, p.chat_id, p.chat_title, p.topic, p.thread_id, p.author, p.author_id, iso(p.date),
             json.dumps(p.message_ids), p.text, p.reply_to, p.link, int(p.edited), iso(self.now())),
        )
        q = ",".join("?" * len(p.message_ids))
        self.execute(f"UPDATE messages SET post_id=? WHERE chat_id=? AND message_id IN ({q})",
                     (p.post_id, p.chat_id, *p.message_ids))
        return changed

    @staticmethod
    def row_to_post(r: dict[str, Any]) -> Post:
        return Post(
            post_id=r["post_id"], chat_id=r["chat_id"], chat_title=r["chat_title"] or "", topic=r["topic"],
            thread_id=r["thread_id"], author=r["author"] or "", author_id=r["author_id"],
            date=datetime.fromisoformat(r["date"]), message_ids=json.loads(r["message_ids"]), text=r["text"] or "",
            reply_to=r["reply_to"], link=r["link"], edited=bool(r["edited"]),
        )

    def get_post(self, post_id: str) -> Post | None:
        r = self.one("SELECT * FROM posts WHERE post_id=?", (post_id,))
        return self.row_to_post(r) if r else None

    def get_post_row(self, post_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM posts WHERE post_id=?", (post_id,))

    def post_id_for_message(self, chat_id: int, message_id: int) -> str | None:
        r = self.one("SELECT post_id FROM messages WHERE chat_id=? AND message_id=?", (chat_id, message_id))
        return r["post_id"] if r else None

    def set_post_status(self, post_id: str, status: str, classification: dict | None = None) -> None:
        c = classification or {}
        self.execute(
            "UPDATE posts SET status=?, is_important=?, subject=?, category=?, confidence=?, classification=?, "
            "updated_at=? WHERE post_id=?",
            (status, int(bool(c.get("is_important"))) if c else None, c.get("subject"), c.get("category"),
             c.get("confidence"), json.dumps(c, ensure_ascii=False, default=str) if c else None,
             iso(self.now()), post_id),
        )

    def set_post_indexed(self, post_id: str, h: str) -> None:
        self.execute("UPDATE posts SET indexed_hash=? WHERE post_id=?", (h, post_id))

    def list_posts(self, subject: str | None = None, chat_id: int | None = None, since: datetime | None = None,
                   until: datetime | None = None, only_important: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM posts WHERE 1=1"
        params: list[Any] = []
        if subject:
            sql += " AND subject=?"
            params.append(subject)
        if chat_id is not None:
            sql += " AND chat_id=?"
            params.append(chat_id)
        if since:
            sql += " AND date>=?"
            params.append(iso(since))
        if until:
            sql += " AND date<=?"
            params.append(iso(until))
        if only_important:
            sql += " AND is_important=1"
        return self.query(sql + " ORDER BY date", params)

    # ------------------------------------------------------------------ decisions
    def add_decision(self, d: dict[str, Any]) -> int:
        cols = ["post_id", "created_at", "trigger", "backend", "model", "prefilter", "context", "prompt", "raw_output",
                "classification", "validation", "route", "act_prompt", "tool_calls", "fallback", "fallback_reason",
                "verify", "latency_ms", "timings", "error"]
        vals = []
        for c in cols:
            v = d.get(c)
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False, default=str)
            elif isinstance(v, bool):
                v = int(v)
            vals.append(v)
        cur = self.execute(f"INSERT INTO decisions({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
        return int(cur.lastrowid)

    def decisions(self, limit: int = 500) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))

    def decision(self, decision_id: int) -> dict[str, Any] | None:
        return self.one("SELECT * FROM decisions WHERE id=?", (decision_id,))

    # ------------------------------------------------------------------ deadlines
    def _row_to_deadline(self, r: dict[str, Any], with_sources: bool = True) -> Deadline:
        d = Deadline(
            id=r["id"], subject=r["subject"], title=r["title"], kind=r["kind"] or "deadline",
            due_at=parse_dt(r["due_at"]), due_text=r["due_text"], location=r["location"], status=r["status"],
            prev_due_at=parse_dt(r["prev_due_at"]), prev_location=r["prev_location"], close_reason=r["close_reason"],
            created_at=parse_dt(r["created_at"]), updated_at=parse_dt(r["updated_at"]),
        )
        if with_sources:
            d.sources = self.query("SELECT * FROM deadline_sources WHERE deadline_id=? ORDER BY added_at", (d.id,))
        return d

    def create_deadline(self, subject: str | None, title: str, kind: str, due_at: datetime | None,
                        due_text: str | None, location: str | None) -> int:
        now = iso(self.now())
        cur = self.execute(
            "INSERT INTO deadlines(subject, title, kind, due_at, due_text, location, status, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,'active',?,?)",
            (subject, title, kind, iso(due_at), due_text, location, now, now),
        )
        return int(cur.lastrowid)

    def get_deadline(self, deadline_id: int) -> Deadline | None:
        r = self.one("SELECT * FROM deadlines WHERE id=?", (deadline_id,))
        return self._row_to_deadline(r) if r else None

    def update_deadline_fields(self, deadline_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = self.now()
        sets = ", ".join(f"{k}=?" for k in fields)
        vals = [iso(v) if isinstance(v, datetime) else v for v in fields.values()]
        self.execute(f"UPDATE deadlines SET {sets} WHERE id=?", (*vals, deadline_id))

    def list_deadlines(self, subject: str | None = None, statuses: tuple[str, ...] = ("active",),
                       since: datetime | None = None) -> list[Deadline]:
        sql = f"SELECT * FROM deadlines WHERE status IN ({','.join('?' * len(statuses))})"
        params: list[Any] = list(statuses)
        if subject:
            sql += " AND subject=?"
            params.append(subject)
        if since:
            sql += " AND (due_at IS NULL OR due_at>=?)"
            params.append(iso(since))
        sql += " ORDER BY due_at IS NULL, due_at, id"
        return [self._row_to_deadline(r) for r in self.query(sql, params)]

    def all_deadlines(self) -> list[Deadline]:
        return [self._row_to_deadline(r) for r in self.query("SELECT * FROM deadlines ORDER BY due_at IS NULL, due_at, id")]

    def add_deadline_source(self, deadline_id: int, chat_id: int, message_id: int, link: str | None) -> bool:
        cur = self.execute("INSERT OR IGNORE INTO deadline_sources(deadline_id, chat_id, message_id, link, added_at) "
                           "VALUES(?,?,?,?,?)", (deadline_id, chat_id, message_id, link, iso(self.now())))
        return cur.rowcount > 0

    def deadlines_for_messages(self, chat_id: int, message_ids: Iterable[int]) -> list[int]:
        ids = list(message_ids)
        if not ids:
            return []
        rows = self.query(f"SELECT DISTINCT deadline_id FROM deadline_sources WHERE chat_id=? "
                          f"AND message_id IN ({','.join('?' * len(ids))})", (chat_id, *ids))
        return [r["deadline_id"] for r in rows]

    def add_history(self, deadline_id: int, action: str, changes: dict | None = None, source_chat_id: int | None = None,
                    source_message_id: int | None = None, source_link: str | None = None,
                    note: str | None = None) -> None:
        self.execute(
            "INSERT INTO deadline_history(deadline_id, ts, action, changes, source_chat_id, source_message_id, "
            "source_link, note) VALUES(?,?,?,?,?,?,?,?)",
            (deadline_id, iso(self.now()), action, json.dumps(changes or {}, ensure_ascii=False, default=str),
             source_chat_id, source_message_id, source_link, note),
        )

    def history(self, deadline_id: int | None = None) -> list[dict[str, Any]]:
        if deadline_id is None:
            return self.query("SELECT * FROM deadline_history ORDER BY id")
        return self.query("SELECT * FROM deadline_history WHERE deadline_id=? ORDER BY id", (deadline_id,))

    def deadlines_to_index(self) -> list[Deadline]:
        rows = self.query("SELECT * FROM deadlines WHERE indexed_at IS NULL OR indexed_at < updated_at")
        return [self._row_to_deadline(r, with_sources=False) for r in rows]

    def mark_deadline_indexed(self, deadline_id: int) -> None:
        self.execute("UPDATE deadlines SET indexed_at=updated_at WHERE id=?", (deadline_id,))

    # ------------------------------------------------------------------ hub
    def hub_topics(self) -> dict[str, dict[str, Any]]:
        return {r["key"]: r for r in self.query("SELECT * FROM hub_topics ORDER BY position, key")}

    def set_hub_topic(self, key: str, title: str, thread_id: int | None, position: int) -> None:
        self.execute("INSERT INTO hub_topics(key, title, thread_id, position) VALUES(?,?,?,?) "
                     "ON CONFLICT(key) DO UPDATE SET title=excluded.title, thread_id=excluded.thread_id, "
                     "position=excluded.position", (key, title, thread_id, position))

    def add_hub_message(self, **f: Any) -> int:
        f.setdefault("created_at", iso(self.now()))
        cols = list(f)
        vals = [iso(v) if isinstance(v, datetime) else v for v in f.values()]
        cur = self.execute(f"INSERT INTO hub_messages({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
        return int(cur.lastrowid)

    def update_hub_message(self, hub_id: int, **f: Any) -> None:
        f.setdefault("edited_at", iso(self.now()))
        sets = ", ".join(f"{k}=?" for k in f)
        self.execute(f"UPDATE hub_messages SET {sets} WHERE id=?", (*f.values(), hub_id))

    def hub_message(self, hub_id: int) -> dict[str, Any] | None:
        return self.one("SELECT * FROM hub_messages WHERE id=?", (hub_id,))

    def hub_messages(self, topic_key: str | None = None) -> list[dict[str, Any]]:
        if topic_key is None:
            return self.query("SELECT * FROM hub_messages ORDER BY id")
        return self.query("SELECT * FROM hub_messages WHERE topic_key=? ORDER BY id", (topic_key,))

    def hub_forwards_of(self, chat_id: int, message_id: int, topic_key: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM hub_messages WHERE kind='forward' AND source_chat_id=? AND source_message_id=?"
        params: list[Any] = [chat_id, message_id]
        if topic_key:
            sql += " AND topic_key=?"
            params.append(topic_key)
        return self.query(sql + " ORDER BY id", params)

    def hub_notes_for_sources(self, chat_id: int, message_ids: Iterable[int]) -> list[dict[str, Any]]:
        ids = list(message_ids)
        if not ids:
            return []
        return self.query(f"SELECT * FROM hub_messages WHERE kind='note' AND source_chat_id=? "
                          f"AND source_message_id IN ({','.join('?' * len(ids))}) ORDER BY id", (chat_id, *ids))

    def hub_notes_for_deadline(self, deadline_id: int) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM hub_messages WHERE kind='note' AND deadline_id=? ORDER BY id", (deadline_id,))

    def board_pages(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM board_pages ORDER BY page")

    def set_board_page(self, page: int, hub_message_id: int, text: str) -> None:
        self.execute("INSERT INTO board_pages(page, hub_message_id, text, updated_at) VALUES(?,?,?,?) "
                     "ON CONFLICT(page) DO UPDATE SET hub_message_id=excluded.hub_message_id, text=excluded.text, "
                     "updated_at=excluded.updated_at", (page, hub_message_id, text, iso(self.now())))

    # ------------------------------------------------------------------ asks
    def add_ask(self, **f: Any) -> int:
        f.setdefault("created_at", iso(self.now()))
        cols = list(f)
        vals = [json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v for v in f.values()]
        cur = self.execute(f"INSERT INTO asks({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
        return int(cur.lastrowid)

    def asks(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM asks ORDER BY id DESC LIMIT ?", (limit,))
