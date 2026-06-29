"""扫描编排：两段式——并行抓取(网络) + 串行 diff/写库(保证无漏/无重)。

抓取阶段每个线程用独立 client(clone)，互不共享 curl 会话；store 只在串行阶段触碰。
维度(columns/announcements/contents)独立 try/except：失败记 failure 并跳过该维度的
diff/基线/通知(不污染)；某维度完整成功且基线未建 → 只写快照并建立基线(冷启动静默)。
"""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .diff import diff_announcements, diff_columns, diff_contents
from .models import Course


@dataclass
class ScanResult:
    new_events: int = 0
    courses_scanned: int = 0
    failures: list[str] = field(default_factory=list)


@dataclass
class _Bundle:
    """一门课抓取到的原始数据(供串行处理)。某维度 *_err 非空表示该维度抓取失败。"""

    course: Course
    columns: list | None = None
    statuses: dict | None = None
    anns: list | None = None
    contents: list | None = None
    col_err: str | None = None
    ann_err: str | None = None
    con_err: str | None = None


def _fetch_course(client, course: Course, uid: str, include_contents: bool) -> _Bundle:
    b = _Bundle(course=course)
    try:
        cols = client.list_columns(course.id)
        b.columns = cols
        b.statuses = {c.id: client.get_column_status(course.id, c.id, uid) for c in cols}
    except Exception as e:  # noqa: BLE001
        b.col_err = f"{course.course_id}/columns: {type(e).__name__}"
    try:
        b.anns = client.list_announcements(course.id)
    except Exception as e:  # noqa: BLE001
        b.ann_err = f"{course.course_id}/announcements: {type(e).__name__}"
    if include_contents:
        try:
            b.contents = [c for _, c in client.walk_contents(course.id)]
        except Exception as e:  # noqa: BLE001
            b.con_err = f"{course.course_id}/contents: {type(e).__name__}"
    return b


def scan(
    client,
    store,
    uid: str,
    *,
    now: str,
    current_terms: set[str] | None = None,
    course_filter: Callable[[Course], bool] | None = None,
    include_contents: bool = True,
    fetch_workers: int = 1,
    client_factory: Callable[[], object] | None = None,
) -> ScanResult:
    scan_id = store.start_scan(now)
    res = ScanResult()

    courses = [c for c in client.list_courses(uid) if c.is_active]
    if current_terms is not None:
        courses = [c for c in courses if c.term_id in current_terms]
    if course_filter is not None:
        courses = [c for c in courses if course_filter(c)]
    res.courses_scanned = len(courses)

    # ---- 阶段一：抓取(可并行；每线程独立 client) ----
    def _fetch(course: Course) -> _Bundle:
        c = client_factory() if (client_factory and fetch_workers > 1) else client
        return _fetch_course(c, course, uid, include_contents)

    if fetch_workers > 1 and client_factory and len(courses) > 1:
        with ThreadPoolExecutor(max_workers=fetch_workers) as ex:
            bundles = list(ex.map(_fetch, courses))
    else:
        bundles = [_fetch(c) for c in courses]

    # ---- 阶段二：串行 diff + 写库(保证不变量) ----
    for b in bundles:
        res.new_events += _process_columns(store, b, scan_id, now, res.failures)
        res.new_events += _process_announcements(store, b, scan_id, now, res.failures)
        if include_contents:
            res.new_events += _process_contents(store, b, scan_id, now, res.failures)

    store.finish_scan(scan_id, "partial" if res.failures else "ok", now)
    return res


def _process_columns(store, b: _Bundle, scan_id, now, failures) -> int:
    if b.col_err is not None:
        failures.append(b.col_err)
        return 0
    cid = b.course.id
    suppress = not store.baseline_established(cid, "columns")
    known = store.known_entities(cid, "column")
    changes = diff_columns(
        known, b.columns, b.statuses, cid=cid, scan_id=scan_id, suppress=suppress,
        course_code=b.course.name or b.course.course_id,
    )
    n = sum(store.apply_change(ch, now) for ch in changes)
    if suppress:
        store.establish_baseline(cid, "columns", now)
    return n


def _process_announcements(store, b: _Bundle, scan_id, now, failures) -> int:
    if b.ann_err is not None:
        failures.append(b.ann_err)
        return 0
    cid = b.course.id
    suppress = not store.baseline_established(cid, "announcements")
    known = store.known_entities(cid, "announcement")
    changes = diff_announcements(known, b.anns, cid=cid, scan_id=scan_id, suppress=suppress)
    n = sum(store.apply_change(ch, now) for ch in changes)
    if suppress:
        store.establish_baseline(cid, "announcements", now)
    return n


def _process_contents(store, b: _Bundle, scan_id, now, failures) -> int:
    if b.con_err is not None:
        failures.append(b.con_err)
        return 0
    if b.contents is None:
        return 0
    cid = b.course.id
    suppress = not store.baseline_established(cid, "contents")
    known = store.known_entities(cid, "content")
    changes = diff_contents(known, b.contents, cid=cid, scan_id=scan_id, suppress=suppress)
    n = sum(store.apply_change(ch, now) for ch in changes)
    if suppress:
        store.establish_baseline(cid, "contents", now)
    return n
