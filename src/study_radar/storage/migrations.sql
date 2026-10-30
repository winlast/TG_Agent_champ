-- Схема SQLite StudyRadar: сообщения, посты, решения агента, дедлайны, хаб.
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS chats (
    chat_id  INTEGER PRIMARY KEY,
    title    TEXT,
    kind     TEXT,
    is_forum INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS source_topics (
    chat_id   INTEGER,
    thread_id INTEGER,
    name      TEXT,
    PRIMARY KEY (chat_id, thread_id)
);

CREATE TABLE IF NOT EXISTS messages (
    chat_id        INTEGER NOT NULL,
    message_id     INTEGER NOT NULL,
    chat_title     TEXT,
    topic          TEXT,
    thread_id      INTEGER,
    author         TEXT,
    author_id      TEXT,
    date           TEXT,
    edited         TEXT,
    text           TEXT,
    prev_text      TEXT,
    reply_to       INTEGER,
    file_name      TEXT,
    is_service     INTEGER DEFAULT 0,
    service_action TEXT,
    link           TEXT,
    post_id        TEXT,
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(chat_id, date);

CREATE TABLE IF NOT EXISTS posts (
    post_id        TEXT PRIMARY KEY,
    chat_id        INTEGER,
    chat_title     TEXT,
    topic          TEXT,
    thread_id      INTEGER,
    author         TEXT,
    author_id      TEXT,
    date           TEXT,
    message_ids    TEXT,
    text           TEXT,
    reply_to       INTEGER,
    link           TEXT,
    edited         INTEGER DEFAULT 0,
    status         TEXT DEFAULT 'new',   -- new | dropped | noise | important | review
    is_important   INTEGER,
    subject        TEXT,
    category       TEXT,
    confidence     REAL,
    classification TEXT,
    indexed_hash   TEXT,
    updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_date ON posts(date);

CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id         TEXT,
    created_at      TEXT,
    trigger         TEXT,     -- new | edit | history | eval
    backend         TEXT,
    model           TEXT,
    prefilter       TEXT,
    context         TEXT,
    prompt          TEXT,
    raw_output      TEXT,
    classification  TEXT,
    validation      TEXT,
    route           TEXT,     -- dropped | skip | act | review
    act_prompt      TEXT,
    tool_calls      TEXT,
    fallback        INTEGER DEFAULT 0,
    fallback_reason TEXT,
    verify          TEXT,
    latency_ms      INTEGER,
    timings         TEXT,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_post ON decisions(post_id);

CREATE TABLE IF NOT EXISTS deadlines (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    subject       TEXT,
    title         TEXT,
    kind          TEXT,
    due_at        TEXT,
    due_text      TEXT,
    location      TEXT,
    status        TEXT DEFAULT 'active',
    prev_due_at   TEXT,
    prev_location TEXT,
    close_reason  TEXT,
    created_at    TEXT,
    updated_at    TEXT,
    indexed_at    TEXT
);

CREATE TABLE IF NOT EXISTS deadline_sources (
    deadline_id INTEGER,
    chat_id     INTEGER,
    message_id  INTEGER,
    link        TEXT,
    added_at    TEXT,
    PRIMARY KEY (deadline_id, chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS deadline_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    deadline_id       INTEGER,
    ts                TEXT,
    action            TEXT,     -- created | updated | source_added | closed
    changes           TEXT,     -- JSON {field: [old, new]}
    source_chat_id    INTEGER,
    source_message_id INTEGER,
    source_link       TEXT,
    note              TEXT
);

CREATE TABLE IF NOT EXISTS hub_topics (
    key       TEXT PRIMARY KEY,   -- ключ предмета или служебный: deadlines | review | journal | general
    title     TEXT,
    thread_id INTEGER,
    position  INTEGER
);

CREATE TABLE IF NOT EXISTS hub_messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_key         TEXT,
    kind              TEXT,     -- forward | note | update | board | review | journal | answer
    text              TEXT,     -- HTML (Telegram parse_mode=HTML)
    source_chat_id    INTEGER,
    source_message_id INTEGER,
    source_chat_title TEXT,
    source_author     TEXT,
    source_date       TEXT,
    source_link       TEXT,
    source_text_hash  TEXT,
    reply_to          INTEGER,  -- id в hub_messages
    deadline_id       INTEGER,
    pinned            INTEGER DEFAULT 0,
    tg_message_id     INTEGER,
    created_at        TEXT,
    edited_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_hub_topic ON hub_messages(topic_key, id);

CREATE TABLE IF NOT EXISTS board_pages (
    page           INTEGER PRIMARY KEY,
    hub_message_id INTEGER,
    text           TEXT,
    updated_at     TEXT
);

CREATE TABLE IF NOT EXISTS asks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    question   TEXT,
    answer     TEXT,
    sources    TEXT,
    prompt     TEXT,
    backend    TEXT,
    model      TEXT,
    latency_ms INTEGER
);
