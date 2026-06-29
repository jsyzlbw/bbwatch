from bbwatch.models import Announcement, Column, ColumnStatus, Course
from bbwatch.scanner import scan
from bbwatch.store import Store

NOW = "2026-06-28T00:00:00.000Z"
UID = "_49765_1"
DUE1 = "2026-06-30T15:59:00.000Z"


def _course(cid, active=True):
    return Course(
        id=cid, course_id=f"C{cid}", name="X", term_id="_t1",
        role="Student" if active else "Instructor",
        availability="Yes" if active else "No", ultra_status="Classic",
    )


class FakeClient:
    def __init__(self, courses, cols, statuses, anns, fail=None):
        self.courses = courses
        self.cols = cols  # {cid: [Column]}
        self.statuses = statuses  # {cid: {colid: ColumnStatus}}
        self.anns = anns  # {cid: [Announcement]}
        self.fail = fail or set()  # {(dim, cid)}

    def list_courses(self, uid):
        return self.courses

    def list_columns(self, cid):
        if ("columns", cid) in self.fail:
            raise RuntimeError("boom")
        return self.cols.get(cid, [])

    def get_column_status(self, cid, colid, uid):
        return self.statuses.get(cid, {}).get(colid, ColumnStatus("None"))

    def list_announcements(self, cid):
        if ("announcements", cid) in self.fail:
            raise RuntimeError("boom")
        return self.anns.get(cid, [])


def test_first_scan_silent_then_detects_new():
    s = Store(":memory:")
    cli = FakeClient(
        courses=[_course("_c1")],
        cols={"_c1": [Column("_h1", "HW1", DUE1)]},
        statuses={"_c1": {"_h1": ColumnStatus("None")}},
        anns={"_c1": [Announcement("_a1", "Ann1", "2026-06-20T00:00:00Z")]},
    )
    r1 = scan(cli, s, UID, now=NOW)
    assert r1.new_events == 0  # 冷启动静默
    assert r1.courses_scanned == 1
    assert s.baseline_established("_c1", "columns")
    assert s.baseline_established("_c1", "announcements")

    # 第二扫：新增一列 + 新公告
    cli.cols["_c1"].append(Column("_h2", "HW2", DUE1))
    cli.statuses["_c1"]["_h2"] = ColumnStatus("None")
    cli.anns["_c1"].append(Announcement("_a2", "Ann2", "2026-06-25T00:00:00Z"))
    r2 = scan(cli, s, UID, now=NOW)
    assert r2.new_events == 2
    types = {e["event_type"] for e in [dict(row) for row in s.claim_pending_events(NOW)]}
    assert types == {"new_assignment", "new_announcement"}


def test_dimension_failure_isolated():
    s = Store(":memory:")
    cli = FakeClient(
        courses=[_course("_c1"), _course("_c2")],
        cols={"_c1": [Column("_h1", "HW1", DUE1)], "_c2": [Column("_q1", "Q1", DUE1)]},
        statuses={"_c1": {"_h1": ColumnStatus("None")}, "_c2": {"_q1": ColumnStatus("None")}},
        anns={"_c1": [], "_c2": []},
        fail={("columns", "_c1")},  # 只有 _c1 的 columns 维度失败
    )
    r = scan(cli, s, UID, now=NOW)
    assert any("C_c1/columns" in f for f in r.failures)
    # 失败维度不建基线、不污染
    assert not s.baseline_established("_c1", "columns")
    assert "col:_c1:_h1" not in s.known_entities("_c1", "column")
    # 同课其它维度、其它课不受影响（基线独立）
    assert s.baseline_established("_c1", "announcements")
    assert s.baseline_established("_c2", "columns")
    assert "col:_c2:_q1" in s.known_entities("_c2", "column")


def test_inactive_course_skipped():
    s = Store(":memory:")
    cli = FakeClient(
        courses=[_course("_c1", active=False)],
        cols={"_c1": [Column("_h1", "HW1", DUE1)]},
        statuses={"_c1": {"_h1": ColumnStatus("None")}},
        anns={"_c1": []},
    )
    r = scan(cli, s, UID, now=NOW)
    assert r.courses_scanned == 0
    assert not s.baseline_established("_c1", "columns")
