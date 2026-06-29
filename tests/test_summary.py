from bbwatch.diff import diff_columns
from bbwatch.models import Column, ColumnStatus
from bbwatch.store import Store
from bbwatch.summary import build_session_summary

NOW = "2026-06-28T00:00:00.000Z"


def _seed(s, colid, due):
    s.establish_baseline("_c", "columns", NOW)
    for ch in diff_columns(
        s.known_entities("_c", "column"), [Column(colid, colid, due)],
        {colid: ColumnStatus("None")}, cid="_c", scan_id=1, suppress=False,
    ):
        s.apply_change(ch, NOW)


def test_summary_empty():
    assert "没有未完成作业" in build_session_summary(Store(":memory:"), NOW)


def test_summary_lists_tasks_and_stale_warning():
    s = Store(":memory:")
    _seed(s, "_h1", "2026-06-29T15:59:00.000Z")  # 约 40h 后，属临近
    sid = s.start_scan("2026-06-25T00:00:00.000Z")
    s.finish_scan(sid, "ok", "2026-06-25T00:00:00.000Z")  # 3 天前扫过 → stale
    out = build_session_summary(s, NOW, stale_hours=12)
    assert "1 项未完成" in out
    assert "_h1" in out
    assert "距上次扫描" in out  # stale + 临近 → 提醒刷新
