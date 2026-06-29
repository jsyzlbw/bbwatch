from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 3
_SCHEMA = Path(__file__).parent / "schema.sql"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _add_seconds(iso: str, secs: int) -> str:
    return (parse_utc(iso) + timedelta(seconds=secs)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@dataclass
class Change:
    """diff 的产物：一个实体的快照 + 0..n 个待发事件。整体单事务落库。"""

    seen: dict
    events: list[dict] = field(default_factory=list)


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        # isolation_level=None → 自动提交模式，便于显式 BEGIN IMMEDIATE 控制原子性
        self._conn = sqlite3.connect(str(path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        # 所有建表为 CREATE IF NOT EXISTS，对新库与旧库均幂等（加表即迁移）。
        self._conn.executescript(_SCHEMA.read_text())
        row = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        cur = int(row["value"]) if row else 0
        if cur == 0:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),)
            )
        elif cur < SCHEMA_VERSION:
            self._migrate(cur)
            self._conn.execute(
                "UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),)
            )

    def _migrate(self, from_version: int) -> None:
        # 目前各版本变更均为新增表（executescript 已用 IF NOT EXISTS 处理）。
        # 未来非新增式变更（ALTER/数据迁移）在此按 from_version 顺序补。
        return

    def schema_version(self) -> int:
        row = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row["value"]) if row else 0

    def close(self) -> None:
        self._conn.close()

    # ---------------- scan runs ----------------
    def start_scan(self, now: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO scan_run(started_at, status) VALUES(?, 'running')", (now,)
        )
        return cur.lastrowid

    def finish_scan(self, scan_id: int, status: str, now: str) -> None:
        self._conn.execute(
            "UPDATE scan_run SET finished_at=?, status=? WHERE id=?", (now, status, scan_id)
        )

    # ---------------- 认证熔断(附录 C.3) ----------------
    def _auth_row(self) -> sqlite3.Row:
        row = self._conn.execute("SELECT * FROM auth_state WHERE id=1").fetchone()
        if row is None:
            self._conn.execute("INSERT INTO auth_state(id, fail_count) VALUES(1, 0)")
            row = self._conn.execute("SELECT * FROM auth_state WHERE id=1").fetchone()
        return row

    def auth_circuit_open(self, now: str) -> bool:
        row = self._auth_row()
        until = row["circuit_open_until"]
        return until is not None and parse_utc(now) < parse_utc(until)

    def record_auth_failure(self, now: str, threshold: int = 3, open_seconds: int = 3600) -> bool:
        """记一次凭据失败；达阈值则开启熔断。返回是否已熔断。"""
        self._auth_row()
        self._conn.execute("UPDATE auth_state SET fail_count = fail_count + 1 WHERE id=1")
        row = self._auth_row()
        if row["fail_count"] >= threshold:
            self._conn.execute(
                "UPDATE auth_state SET circuit_open_until=? WHERE id=1",
                (_add_seconds(now, open_seconds),),
            )
            return True
        return False

    def reset_auth_failures(self) -> None:
        self._auth_row()
        self._conn.execute(
            "UPDATE auth_state SET fail_count=0, circuit_open_until=NULL WHERE id=1"
        )

    def last_scan_time(self) -> str | None:
        row = self._conn.execute(
            "SELECT finished_at FROM scan_run WHERE finished_at IS NOT NULL "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["finished_at"] if row else None

    # ---------------- baseline (per course/dimension) ----------------
    def baseline_established(self, course_id: str, dimension: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM course_baseline WHERE course_id=? AND dimension=?",
                (course_id, dimension),
            ).fetchone()
            is not None
        )

    def establish_baseline(self, course_id: str, dimension: str, now: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO course_baseline(course_id, dimension, established_at) "
            "VALUES(?, ?, ?)",
            (course_id, dimension, now),
        )

    # ---------------- known set (含 archived，附录 C M6) ----------------
    def known_entities(self, course_id: str, kind: str) -> dict[str, sqlite3.Row]:
        rows = self._conn.execute(
            "SELECT * FROM seen_entity WHERE course_id=? AND kind=?", (course_id, kind)
        ).fetchall()
        return {r["entity_key"]: r for r in rows}

    def mark_archived(self, entity_key: str) -> None:
        self._conn.execute("UPDATE seen_entity SET archived=1 WHERE entity_key=?", (entity_key,))

    # ---------------- core: 单事务写 seen + 事件 ----------------
    def apply_change(self, change: Change, now: str) -> int:
        """单事务: upsert seen_entity + INSERT OR IGNORE 每个事件。返回新插入事件数。"""
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._upsert_seen(change.seen, now)
            inserted = 0
            for ev in change.events:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO event"
                    "(dedup_key, entity_key, event_type, state, title, detail, created_at) "
                    "VALUES(?, ?, ?, 'PENDING_NOTIFY', ?, ?, ?)",
                    (ev["dedup_key"], ev["entity_key"], ev["event_type"], ev["title"],
                     ev.get("detail"), now),
                )
                if cur.rowcount > 0:
                    inserted += 1
            conn.execute("COMMIT")
            return inserted
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def _upsert_seen(self, seen: dict, now: str) -> None:
        params = {
            "entity_key": seen["entity_key"],
            "kind": seen["kind"],
            "course_id": seen["course_id"],
            "bb_id": seen["bb_id"],
            "due_utc": seen.get("due_utc"),
            "grade_status": seen.get("grade_status"),
            "grade_score": seen.get("grade_score"),
            "payload_json": json.dumps(seen["payload"], ensure_ascii=False),
            "scan_id": seen.get("scan_id"),
            "now": now,
        }
        self._conn.execute(
            """INSERT INTO seen_entity
                 (entity_key, kind, course_id, bb_id, due_utc, grade_status, grade_score,
                  payload_json, archived, first_seen_scan, last_seen_scan, created_at)
               VALUES
                 (:entity_key, :kind, :course_id, :bb_id, :due_utc, :grade_status, :grade_score,
                  :payload_json, 0, :scan_id, :scan_id, :now)
               ON CONFLICT(entity_key) DO UPDATE SET
                  due_utc=excluded.due_utc,
                  grade_status=excluded.grade_status,
                  grade_score=excluded.grade_score,
                  payload_json=excluded.payload_json,
                  archived=0,
                  last_seen_scan=excluded.last_seen_scan""",
            params,
        )

    # ---------------- 通知 outbox ----------------
    def claim_pending_events(self, now: str, limit: int = 50) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM event WHERE state='PENDING_NOTIFY' "
            "AND (next_retry_at IS NULL OR next_retry_at<=?) ORDER BY id LIMIT ?",
            (now, limit),
        ).fetchall()

    def mark_notified(self, event_id: int, now: str) -> None:
        self._conn.execute("UPDATE event SET state='NOTIFIED' WHERE id=?", (event_id,))

    def mark_failed(self, event_id: int, now: str, backoff_s: int = 300, max_attempts: int = 5) -> None:
        row = self._conn.execute(
            "SELECT notify_attempts FROM event WHERE id=?", (event_id,)
        ).fetchone()
        attempts = (row["notify_attempts"] if row else 0) + 1
        if attempts >= max_attempts:
            self._conn.execute(
                "UPDATE event SET state='FAILED_NOTIFY', notify_attempts=? WHERE id=?",
                (attempts, event_id),
            )
        else:
            self._conn.execute(
                "UPDATE event SET notify_attempts=?, next_retry_at=? WHERE id=?",
                (attempts, _add_seconds(now, backoff_s * attempts), event_id),
            )

    # ---------------- 任务清单 ----------------
    def actionable_tasks(self) -> list[dict]:
        """可手动跟踪的作业：带 due、未归档、且**未被系统判定为已完成(出分/已交)**的列。
        其完成状态由手动勾选(task_override.manual_done)控制，故可来回切换。
        每项含 done(bool, =手动已完成)。按截止升序。"""
        rows = self._conn.execute(
            "SELECT * FROM seen_entity WHERE kind='column' AND archived=0 "
            "AND due_utc IS NOT NULL ORDER BY due_utc ASC"
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            auto_done = (r["grade_status"] in ("NeedsGrading", "Graded")) or (
                r["grade_score"] is not None
            )
            if auto_done:
                continue  # 系统已知完成(出分/已交)，无需手动管理
            ov = self._conn.execute(
                "SELECT manual_done FROM task_override WHERE entity_key=?", (r["entity_key"],)
            ).fetchone()
            payload = json.loads(r["payload_json"])
            out.append(
                {
                    "entity_key": r["entity_key"],
                    "course_id": r["course_id"],
                    "name": payload.get("name"),
                    "due_utc": r["due_utc"],
                    "done": bool(ov and ov["manual_done"]),
                }
            )
        return out

    def outstanding_tasks(self) -> list[dict]:
        """未完成作业(供 scan 摘要)：actionable 中未手动完成的。"""
        return [t for t in self.actionable_tasks() if not t["done"]]

    def archive_overdue(self, now: str, weeks: int) -> int:
        """归档逾期超过 weeks 周且未完成的作业(减少清单杂乱)。归档行仍留在 diff 已知集，
        不影响去重(M6)，仅从 actionable/outstanding 隐藏。返回归档数。"""
        if weeks <= 0:
            return 0
        cutoff = _add_seconds(now, -weeks * 7 * 86400)
        rows = self._conn.execute(
            "SELECT entity_key, grade_status, grade_score FROM seen_entity "
            "WHERE kind='column' AND archived=0 AND due_utc IS NOT NULL AND due_utc < ?",
            (cutoff,),
        ).fetchall()
        n = 0
        for r in rows:
            auto_done = (r["grade_status"] in ("NeedsGrading", "Graded")) or (
                r["grade_score"] is not None
            )
            ov = self._conn.execute(
                "SELECT manual_done FROM task_override WHERE entity_key=?", (r["entity_key"],)
            ).fetchone()
            if auto_done or (ov and ov["manual_done"]):
                continue  # 已完成的不必归档(不算杂乱)
            self._conn.execute(
                "UPDATE seen_entity SET archived=1 WHERE entity_key=?", (r["entity_key"],)
            )
            n += 1
        return n

    def mark_manual_done(self, entity_key: str, done: bool, now: str) -> None:
        self._conn.execute(
            "INSERT INTO task_override(entity_key, manual_done, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(entity_key) DO UPDATE SET manual_done=excluded.manual_done, "
            "updated_at=excluded.updated_at",
            (entity_key, 1 if done else 0, now),
        )

    # ---------------- 下载登记（增量） ----------------
    def get_download(self, att_key: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM download WHERE att_key=?", (att_key,)
        ).fetchone()

    def path_owner(self, local_path: str) -> str | None:
        row = self._conn.execute(
            "SELECT att_key FROM download WHERE local_path=?", (local_path,)
        ).fetchone()
        return row["att_key"] if row else None

    def need_download(self, att_key: str, src_modified: str | None, size: int | None) -> bool:
        row = self.get_download(att_key)
        if row is None or row["status"] != "done":
            return True
        if src_modified and row["src_modified_utc"] != src_modified:
            return True
        if size is not None and row["size"] is not None and row["size"] != size:
            return True
        return False

    def search_downloads(self, keyword: str) -> list[str]:
        like = f"%{keyword}%"
        rows = self._conn.execute(
            "SELECT local_path FROM download WHERE status='done' "
            "AND (local_path LIKE ? OR course_id LIKE ?) ORDER BY local_path",
            (like, like),
        ).fetchall()
        return [r["local_path"] for r in rows]

    def grading_backlog(self, now: str, days: int = 14) -> list[dict]:
        """已交但久未出分(NeedsGrading 且截止已过 days 天)的作业，可催老师。"""
        cutoff = _add_seconds(now, -days * 86400)
        rows = self._conn.execute(
            "SELECT entity_key, payload_json, due_utc FROM seen_entity "
            "WHERE kind='column' AND archived=0 AND grade_status='NeedsGrading' "
            "AND due_utc IS NOT NULL AND due_utc < ?",
            (cutoff,),
        ).fetchall()
        return [
            {"entity_key": r["entity_key"], "name": json.loads(r["payload_json"]).get("name"),
             "due_utc": r["due_utc"]}
            for r in rows
        ]

    def record_download(
        self, att_key, course_id, local_path, src_modified, size, now, status="done"
    ) -> None:
        self._conn.execute(
            "INSERT INTO download(att_key, course_id, local_path, src_modified_utc, size, "
            "status, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(att_key) DO UPDATE SET local_path=excluded.local_path, "
            "src_modified_utc=excluded.src_modified_utc, size=excluded.size, "
            "status=excluded.status, updated_at=excluded.updated_at",
            (att_key, course_id, local_path, src_modified, size, status, now),
        )
