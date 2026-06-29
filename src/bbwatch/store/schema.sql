CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- 全量 diff 的"本地已知"那一半。每个 entity_key 一行，永久记忆（含软删行）。
CREATE TABLE IF NOT EXISTS seen_entity (
    entity_key      TEXT PRIMARY KEY,         -- col:{cid}:{colid} / ann:{cid}:{annid}
    kind            TEXT NOT NULL,            -- 'column' | 'announcement'
    course_id       TEXT NOT NULL,
    bb_id           TEXT NOT NULL,
    due_utc         TEXT,                     -- column: grading.due（用于改期检测）
    grade_status    TEXT,                     -- column: None/NeedsGrading/Graded
    grade_score     REAL,                     -- column: 出分后非空
    payload_json    TEXT NOT NULL,            -- 展示用快照(name/title/created/...)，不进幂等键
    archived        INTEGER NOT NULL DEFAULT 0,
    first_seen_scan INTEGER,
    last_seen_scan  INTEGER,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_course_kind ON seen_entity(course_id, kind);

-- 通知事件状态机载体。dedup_key UNIQUE 保证去重。
CREATE TABLE IF NOT EXISTS event (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key       TEXT NOT NULL UNIQUE,
    entity_key      TEXT NOT NULL,
    event_type      TEXT NOT NULL,            -- new_assignment/deadline_changed/graded/new_announcement
    state           TEXT NOT NULL,            -- PENDING_NOTIFY/NOTIFIED/FAILED_NOTIFY
    title           TEXT NOT NULL,
    detail          TEXT,
    notify_attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at   TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_state ON event(state);

-- 基线按 (课程, 维度) 独立（附录 C M9）。建立前该维度只写快照不通知。
CREATE TABLE IF NOT EXISTS course_baseline (
    course_id      TEXT NOT NULL,
    dimension      TEXT NOT NULL,             -- 'columns' | 'announcements'
    established_at TEXT NOT NULL,
    PRIMARY KEY (course_id, dimension)
);

CREATE TABLE IF NOT EXISTS scan_run (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running'
);

-- 手动勾选完成（线下/纸质作业），扫描器尊重。
CREATE TABLE IF NOT EXISTS task_override (
    entity_key  TEXT PRIMARY KEY,
    manual_done INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT
);

-- 课件镜像下载登记（增量判定）。att_key = att:{cid}:{contentid}:{attid}
CREATE TABLE IF NOT EXISTS download (
    att_key          TEXT PRIMARY KEY,
    course_id        TEXT NOT NULL,
    local_path       TEXT NOT NULL,
    src_modified_utc TEXT,
    size             INTEGER,
    status           TEXT NOT NULL DEFAULT 'done',
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_download_path ON download(local_path);

-- 认证熔断状态(单行 id=1)：连续凭据失败达阈值即熔断，防学校账号锁定(附录 C.3)。
CREATE TABLE IF NOT EXISTS auth_state (
    id                 INTEGER PRIMARY KEY CHECK (id = 1),
    fail_count         INTEGER NOT NULL DEFAULT 0,
    circuit_open_until TEXT
);
