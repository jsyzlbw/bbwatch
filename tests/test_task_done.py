import pytest

from bbwatch.cli import format_tasks, run_mark_done
from bbwatch.diff import diff_columns
from bbwatch.models import Column, ColumnStatus
from bbwatch.store import Store

NOW = "2026-06-28T00:00:00.000Z"
DUE = "2026-07-10T15:59:00.000Z"
CID = "_c1"


def _seed(store, colid, name, status="None", score=None):
    store.establish_baseline(CID, "columns", NOW)
    known = store.known_entities(CID, "column")
    ch = diff_columns(
        known, [Column(colid, name, DUE)], {colid: ColumnStatus(status, score)},
        cid=CID, scan_id=1, suppress=False,
    )
    for c in ch:
        store.apply_change(c, NOW)


def test_actionable_includes_undone_excludes_graded():
    s = Store(":memory:")
    _seed(s, "_h1", "HW1", "None")
    _seed(s, "_h2", "HW2", "Graded", 100.0)  # 系统已知完成 → 不在可操作列表
    acts = s.actionable_tasks()
    assert [t["name"] for t in acts] == ["HW1"]
    assert acts[0]["done"] is False


def test_mark_done_then_undone_roundtrip():
    s = Store(":memory:")
    _seed(s, "_h1", "HW1", "None")

    msg = run_mark_done(s, 1, done=True, now=NOW)
    assert "已完成" in msg
    acts = s.actionable_tasks()
    assert acts[0]["done"] is True
    assert s.outstanding_tasks() == []          # 不再计入未完成
    assert "✓" in format_tasks(acts, NOW)        # 仍显示(可被撤销)

    msg2 = run_mark_done(s, 1, done=False, now=NOW)
    assert "未完成" in msg2
    assert s.actionable_tasks()[0]["done"] is False
    assert len(s.outstanding_tasks()) == 1       # 撤销后重新计入


def test_mark_done_out_of_range_raises():
    s = Store(":memory:")
    with pytest.raises(ValueError):
        run_mark_done(s, 1, done=True, now=NOW)  # 列表为空
