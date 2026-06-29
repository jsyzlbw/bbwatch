from bbwatch.cli import format_tasks, run_scan
from bbwatch.models import Announcement, Column, ColumnStatus, Course, Me
from bbwatch.store import Store

NOW = "2026-06-28T00:00:00.000Z"
DUE_SOON = "2026-06-28T20:00:00.000Z"  # 当天，紧急
DUE_LATER = "2026-07-10T15:59:00.000Z"
DUE_PAST = "2026-06-20T00:00:00.000Z"  # 逾期


class FakeClient:
    def __init__(self):
        self.courses = [
            Course("_c1", "MAT3007", "Opt", "_t1", "Student", "Yes", "Classic")
        ]
        self.cols = {"_c1": [Column("_h1", "HW1", DUE_LATER)]}
        self.statuses = {"_c1": {"_h1": ColumnStatus("None")}}
        self.anns = {"_c1": [Announcement("_a1", "Ann1", "2026-06-20T00:00:00Z")]}

    def get_me(self):
        return Me(id="_49765_1", user_name="125090374", given_name="梁博文")

    def list_courses(self, uid):
        return self.courses

    def list_columns(self, cid):
        return self.cols.get(cid, [])

    def get_column_status(self, cid, colid, uid):
        return self.statuses[cid][colid]

    def list_announcements(self, cid):
        return self.anns.get(cid, [])

    def walk_contents(self, cid):
        return iter([])


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, title, message):
        self.sent.append((title, message))


def test_run_scan_first_silent_then_detects():
    s = Store(":memory:")
    cli = FakeClient()
    n = FakeNotifier()
    first = run_scan(cli, s, n, now=NOW)
    assert "新事件 0" in first
    assert len(n.sent) == 0  # 冷启动静默
    # 第二扫：加一列
    cli.cols["_c1"].append(Column("_h2", "HW2", DUE_LATER))
    cli.statuses["_c1"]["_h2"] = ColumnStatus("None")
    second = run_scan(cli, s, n, now=NOW)
    assert "新事件 1" in second
    assert len(n.sent) == 1
    assert "未完成作业：2 项" in second


def test_format_tasks_index_marks_and_markers():
    tasks = [
        {"due_utc": DUE_PAST, "name": "Old", "course_id": "MAT", "done": False},
        {"due_utc": DUE_SOON, "name": "Soon", "course_id": "CSC", "done": False},
        {"due_utc": DUE_LATER, "name": "Later", "course_id": "STA", "done": True},
    ]
    lines = format_tasks(tasks, NOW).splitlines()
    assert lines[0].startswith("[1] ○") and "[逾期]" in lines[0] and "Old" in lines[0]
    assert lines[1].startswith("[2] ○") and "[紧急]" in lines[1] and "Soon" in lines[1]
    assert lines[2].startswith("[3] ✓") and "Later" in lines[2]
    assert "逾期" not in lines[2] and "紧急" not in lines[2]  # 已完成项不显示紧急/逾期


def test_format_tasks_empty():
    assert "没有需要跟踪" in format_tasks([], NOW)
