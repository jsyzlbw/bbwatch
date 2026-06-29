"""扫描编排：per-course、维度独立、冷启动静默、complete 闸门（附录 C）。

维度（columns / announcements）各自 try/except：任一异常 → 记 failure 并跳过该维度的
diff/基线/通知（不污染、不软删）。某维度本轮完整成功且基线未建 → 只写快照并建立基线
（冷启动静默）；基线已建 → 正常产生事件。基线按 (course, dimension) 独立。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .diff import diff_announcements, diff_columns, diff_contents
from .models import Course


@dataclass
class ScanResult:
    new_events: int = 0
    courses_scanned: int = 0
    failures: list[str] = field(default_factory=list)


def scan(
    client,
    store,
    uid: str,
    *,
    now: str,
    current_terms: set[str] | None = None,
    course_filter: Callable[[Course], bool] | None = None,
) -> ScanResult:
    scan_id = store.start_scan(now)
    res = ScanResult()

    courses = [c for c in client.list_courses(uid) if c.is_active]
    if current_terms is not None:
        courses = [c for c in courses if c.term_id in current_terms]
    if course_filter is not None:
        courses = [c for c in courses if course_filter(c)]
    res.courses_scanned = len(courses)

    for c in courses:
        res.new_events += _scan_columns(client, store, c, uid, scan_id, now, res.failures)
        res.new_events += _scan_announcements(client, store, c, scan_id, now, res.failures)
        res.new_events += _scan_contents(client, store, c, scan_id, now, res.failures)

    store.finish_scan(scan_id, "partial" if res.failures else "ok", now)
    return res


def _scan_contents(client, store, course, scan_id, now, failures) -> int:
    cid = course.id
    dim = "contents"
    try:
        contents = [c for _, c in client.walk_contents(cid)]
    except Exception as e:  # noqa: BLE001
        failures.append(f"{course.course_id}/{dim}: {type(e).__name__}")
        return 0
    suppress = not store.baseline_established(cid, dim)
    known = store.known_entities(cid, "content")
    changes = diff_contents(known, contents, cid=cid, scan_id=scan_id, suppress=suppress)
    n = sum(store.apply_change(ch, now) for ch in changes)
    if suppress:
        store.establish_baseline(cid, dim, now)
    return n


def _scan_columns(client, store, course, uid, scan_id, now, failures) -> int:
    cid = course.id
    dim = "columns"
    try:
        cols = client.list_columns(cid)
        statuses = {col.id: client.get_column_status(cid, col.id, uid) for col in cols}
    except Exception as e:  # noqa: BLE001  维度隔离：失败不污染
        failures.append(f"{course.course_id}/{dim}: {type(e).__name__}")
        return 0
    suppress = not store.baseline_established(cid, dim)
    known = store.known_entities(cid, "column")
    changes = diff_columns(known, cols, statuses, cid=cid, scan_id=scan_id, suppress=suppress)
    n = sum(store.apply_change(ch, now) for ch in changes)
    if suppress:  # 本维度完整成功（未抛错） → 建立基线
        store.establish_baseline(cid, dim, now)
    return n


def _scan_announcements(client, store, course, scan_id, now, failures) -> int:
    cid = course.id
    dim = "announcements"
    try:
        anns = client.list_announcements(cid)
    except Exception as e:  # noqa: BLE001
        failures.append(f"{course.course_id}/{dim}: {type(e).__name__}")
        return 0
    suppress = not store.baseline_established(cid, dim)
    known = store.known_entities(cid, "announcement")
    changes = diff_announcements(known, anns, cid=cid, scan_id=scan_id, suppress=suppress)
    n = sum(store.apply_change(ch, now) for ch in changes)
    if suppress:
        store.establish_baseline(cid, dim, now)
    return n
