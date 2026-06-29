from bbwatch.models import Announcement, Column, ColumnStatus, Content, Course
from bbwatch.scanner import scan
from bbwatch.store import Store

NOW = "2026-06-28T00:00:00.000Z"
UID = "_10000_1"
DUE1 = "2026-06-30T15:59:00.000Z"


def _course(cid, active=True):
    return Course(
        id=cid, course_id=f"C{cid}", name="X", term_id="_t1",
        role="Student" if active else "Instructor",
        availability="Yes" if active else "No", ultra_status="Classic",
    )


class FakeClient:
    def __init__(self, courses, cols, statuses, anns, fail=None, contents=None):
        self.courses = courses
        self.cols = cols  # {cid: [Column]}
        self.statuses = statuses  # {cid: {colid: ColumnStatus}}
        self.anns = anns  # {cid: [Announcement]}
        self.fail = fail or set()  # {(dim, cid)}
        self.contents = contents or {}  # {cid: [(ancestors, Content)]}

    def list_courses(self, uid):
        return self.courses

    def walk_contents(self, cid):
        if ("contents", cid) in self.fail:
            raise RuntimeError("boom")
        return iter(self.contents.get(cid, []))

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


def test_contents_dimension_detects_new_material():
    s = Store(":memory:")
    doc = Content("_d1", "Slides 1", "resource/x-bb-document", modified="2026-06-01T00:00:00.000Z")
    cli = FakeClient(
        courses=[_course("_c1")],
        cols={"_c1": []},
        statuses={"_c1": {}},
        anns={"_c1": []},
        contents={"_c1": [([], doc)]},
    )
    r1 = scan(cli, s, UID, now=NOW)
    assert r1.new_events == 0  # 冷启动静默
    assert s.baseline_established("_c1", "contents")
    # 新增一个课件
    doc2 = Content("_d2", "Slides 2", "resource/x-bb-document", modified="2026-06-02T00:00:00.000Z")
    cli.contents["_c1"].append(([], doc2))
    r2 = scan(cli, s, UID, now=NOW)
    assert r2.new_events == 1
    assert s.claim_pending_events(NOW)[0]["event_type"] == "new_material"


def test_parallel_fetch_no_miss_no_dup():
    s = Store(":memory:")
    cli = FakeClient(
        courses=[_course("_c1"), _course("_c2")],
        cols={"_c1": [Column("_h1", "HW1", DUE1)], "_c2": [Column("_q1", "Q1", DUE1)]},
        statuses={"_c1": {"_h1": ColumnStatus("None")}, "_c2": {"_q1": ColumnStatus("None")}},
        anns={"_c1": [], "_c2": []},
    )
    # 并行首扫：冷启动 0 通知、两课各建基线
    r1 = scan(cli, s, UID, now=NOW, fetch_workers=4, client_factory=lambda: cli)
    assert r1.new_events == 0 and r1.courses_scanned == 2
    assert s.baseline_established("_c1", "columns") and s.baseline_established("_c2", "columns")
    # 各加一新列 → 并行抓取后串行 diff 恰好 2 个事件(无漏无重)
    cli.cols["_c1"].append(Column("_h2", "HW2", DUE1))
    cli.statuses["_c1"]["_h2"] = ColumnStatus("None")
    cli.cols["_c2"].append(Column("_q2", "Q2", DUE1))
    cli.statuses["_c2"]["_q2"] = ColumnStatus("None")
    r2 = scan(cli, s, UID, now=NOW, fetch_workers=4, client_factory=lambda: cli)
    assert r2.new_events == 2
    # 再扫不重复
    r3 = scan(cli, s, UID, now=NOW, fetch_workers=4, client_factory=lambda: cli)
    assert r3.new_events == 0


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
