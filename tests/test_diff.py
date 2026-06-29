"""M2 核心：无漏/无重 不变量（附录 C）。"""
import pytest

from bbwatch.diff import diff_announcements, diff_columns
from bbwatch.models import Announcement, Column, ColumnStatus
from bbwatch.store import Change, Store

NOW = "2026-06-28T00:00:00.000Z"
DUE1 = "2026-06-30T15:59:00.000Z"
DUE2 = "2026-07-03T15:59:00.000Z"
DUE3 = "2026-07-10T15:59:00.000Z"
CID = "_c1"


def _apply(store, changes):
    return sum(store.apply_change(c, NOW) for c in changes)


def _cols(store, cols, statuses, scan_id, suppress):
    known = store.known_entities(CID, "column")
    return diff_columns(known, cols, statuses, cid=CID, scan_id=scan_id, suppress=suppress)


def test_D1_new_then_idempotent():
    s = Store(":memory:")
    cols = [Column("_h1", "HW1", DUE1)]
    st = {"_h1": ColumnStatus("None")}
    assert _apply(s, _cols(s, cols, st, 1, suppress=False)) == 1
    assert len(s.claim_pending_events(NOW)) == 1
    # 再扫同状态 → 0 新事件（known 命中 + dedup 双保险）
    assert _apply(s, _cols(s, cols, st, 2, suppress=False)) == 0


def test_D2_cold_start_silent_then_baseline():
    s = Store(":memory:")
    cols = [Column("_h1", "HW1", DUE1)]
    st = {"_h1": ColumnStatus("None")}
    # 无基线 → suppress → 0 通知，但写 seen
    assert _apply(s, _cols(s, cols, st, 1, suppress=True)) == 0
    assert len(s.claim_pending_events(NOW)) == 0
    assert "col:_c1:_h1" in s.known_entities(CID, "column")
    s.establish_baseline(CID, "columns", NOW)
    # 基线后新列 → 通知
    cols2 = [Column("_h1", "HW1", DUE1), Column("_h2", "HW2", DUE1)]
    st2 = {"_h1": ColumnStatus("None"), "_h2": ColumnStatus("None")}
    assert _apply(s, _cols(s, cols2, st2, 2, suppress=False)) == 1


def test_D3_deadline_change():
    s = Store(":memory:")
    st = {"_h1": ColumnStatus("None")}
    _apply(s, _cols(s, [Column("_h1", "HW1", DUE1)], st, 1, suppress=True))  # seed, no event
    # 改期 → 1
    assert _apply(s, _cols(s, [Column("_h1", "HW1", DUE2)], st, 2, suppress=False)) == 1
    ev = s.claim_pending_events(NOW)[0]
    assert ev["event_type"] == "deadline_changed"
    # 同 due 再扫 → 0
    assert _apply(s, _cols(s, [Column("_h1", "HW1", DUE2)], st, 3, suppress=False)) == 0
    # 再改新 due → 1（variant 不同）
    assert _apply(s, _cols(s, [Column("_h1", "HW1", DUE3)], st, 4, suppress=False)) == 1


def test_D4_graded_once():
    s = Store(":memory:")
    _apply(s, _cols(s, [Column("_h1", "HW1", DUE1)], {"_h1": ColumnStatus("None")}, 1, suppress=True))
    graded = {"_h1": ColumnStatus("Graded", 100.0)}
    assert _apply(s, _cols(s, [Column("_h1", "HW1", DUE1)], graded, 2, suppress=False)) == 1
    assert s.claim_pending_events(NOW)[0]["event_type"] == "graded"
    # 重复 → 0
    assert _apply(s, _cols(s, [Column("_h1", "HW1", DUE1)], graded, 3, suppress=False)) == 0


def test_D5_archived_reappear_not_new():
    s = Store(":memory:")
    st = {"_h1": ColumnStatus("None")}
    _apply(s, _cols(s, [Column("_h1", "HW1", DUE1)], st, 1, suppress=True))
    s.mark_archived("col:_c1:_h1")
    # 再次出现 → known(含 archived) 命中 → 不报新作业
    assert _apply(s, _cols(s, [Column("_h1", "HW1", DUE1)], st, 2, suppress=False)) == 0


def test_D6_rename_not_new_and_payload_updated():
    s = Store(":memory:")
    st = {"_h1": ColumnStatus("None")}
    _apply(s, _cols(s, [Column("_h1", "HW1", DUE1)], st, 1, suppress=True))
    assert _apply(s, _cols(s, [Column("_h1", "HW1 (final)", DUE1)], st, 2, suppress=False)) == 0
    import json
    row = s.known_entities(CID, "column")["col:_c1:_h1"]
    assert json.loads(row["payload_json"])["name"] == "HW1 (final)"


def test_D7_atomic_seen_and_event_rollback():
    s = Store(":memory:")
    bad = Change(
        seen={"entity_key": "col:_c1:_x", "kind": "column", "course_id": CID, "bb_id": "_x",
              "due_utc": DUE1, "grade_status": "None", "grade_score": None,
              "payload": {"name": "X"}, "scan_id": 1},
        events=[{"dedup_key": "k", "entity_key": "col:_c1:_x", "event_type": "new_assignment"}],
        # 缺 title → 插入时 KeyError → 整事务回滚
    )
    with pytest.raises(KeyError):
        s.apply_change(bad, NOW)
    assert "col:_c1:_x" not in s.known_entities(CID, "column")  # seen 也回滚


def test_announcements_new_and_idempotent():
    s = Store(":memory:")
    anns = [Announcement("_a1", "Reminder", "2026-06-23T10:35:02.000Z", "body")]
    known = s.known_entities(CID, "announcement")
    ch = diff_announcements(known, anns, cid=CID, scan_id=1, suppress=False)
    assert _apply(s, ch) == 1
    known2 = s.known_entities(CID, "announcement")
    ch2 = diff_announcements(known2, anns, cid=CID, scan_id=2, suppress=False)
    assert _apply(s, ch2) == 0
