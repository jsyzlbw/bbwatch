from bbwatch.notifier import deliver_pending
from bbwatch.store import Change, Store

NOW = "2026-06-28T00:00:00.000Z"


class FakeNotifier:
    def __init__(self, fail_times=0):
        self.sent = []
        self.fail_times = fail_times
        self.calls = 0

    def send(self, title, message):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("notify boom")
        self.sent.append((title, message))


def _stage(store, dedup, etype="new_assignment", title="新作业: HW1", detail="截止"):
    ch = Change(
        seen={"entity_key": "col:_c:_x", "kind": "column", "course_id": "_c", "bb_id": "_x",
              "due_utc": None, "grade_status": None, "grade_score": None,
              "payload": {"name": "x"}, "scan_id": 1},
        events=[{"dedup_key": dedup, "entity_key": "col:_c:_x", "event_type": etype,
                 "title": title, "detail": detail}],
    )
    store.apply_change(ch, NOW)


def test_success_marks_notified_and_not_resent():
    s = Store(":memory:")
    _stage(s, "k1")
    n = FakeNotifier()
    assert deliver_pending(s, n, NOW) == 1
    assert len(n.sent) == 1
    assert deliver_pending(s, n, NOW) == 0  # 已 NOTIFIED，不再 claim


def test_fail_then_retry_succeeds():
    s = Store(":memory:")
    _stage(s, "k1")
    n = FakeNotifier(fail_times=1)
    assert deliver_pending(s, n, "2027-01-01T00:00:00.000Z") == 0  # 第一次失败 → 退避
    assert deliver_pending(s, n, "2027-01-01T01:00:00.000Z") == 1  # 退避到点，重投成功
    assert len(n.sent) == 1


def test_terminal_after_max_attempts():
    s = Store(":memory:")
    _stage(s, "k1", etype="graded", title="出分")
    n = FakeNotifier(fail_times=99)
    times = [
        "2027-01-01T00:00:00.000Z",
        "2027-01-01T02:00:00.000Z",
        "2027-01-01T04:00:00.000Z",
        "2027-01-01T06:00:00.000Z",
        "2027-01-01T08:00:00.000Z",
    ]
    for t in times:
        deliver_pending(s, n, t)
    assert deliver_pending(s, n, "2027-01-01T10:00:00.000Z") == 0  # 终态不再 claim
    row = s._conn.execute("SELECT state, notify_attempts FROM event").fetchone()
    assert row["state"] == "FAILED_NOTIFY"
    assert row["notify_attempts"] >= 5
