from pathlib import Path

from bbwatch.diff import diff_announcements
from bbwatch.downloader import mirror
from bbwatch.extract import announcement_is_important, is_exam_file
from bbwatch.models import Announcement, Attachment, Column, ColumnStatus, Content, Course
from bbwatch.store import Store
from bbwatch.summary import build_session_summary

NOW = "2026-06-28T00:00:00.000Z"
COURSE = Course("_c1", "MAT3007:Opt", "Opt", "_t1", "Student", "Yes", "Classic")


# ---------- F4 关键词 ----------
def test_is_exam_file():
    assert is_exam_file("Midterm_25_Fall.pdf")
    assert is_exam_file("MAT3007Spring_midterm_sol.pdf")
    assert is_exam_file("往年卷.pdf")
    assert not is_exam_file("chapter 4.pdf")


def test_announcement_important_detection():
    assert announcement_is_important("Midterm Make-up Exam")
    assert announcement_is_important("补课通知：本周日 3 点")
    assert not announcement_is_important("欢迎选课")


def test_diff_marks_important_announcement():
    s = Store(":memory:")
    anns = [Announcement("_a1", "Midterm Exam Announcement", "2026-06-26T00:00:00Z", "期中考试安排")]
    ch = diff_announcements(s.known_entities("_c1", "announcement"), anns,
                            cid="_c1", scan_id=1, suppress=False)
    for c in ch:
        s.apply_change(c, NOW)
    ev = s.claim_pending_events(NOW)[0]
    assert "[重要]" in ev["title"]


# ---------- F1 往年卷归集 ----------
class _DLClient:
    def walk_contents(self, cid):
        return iter([
            (["Past exam papers"], Content("_d1", "Midterm25", "resource/x-bb-document", modified="m")),
            (["Content"], Content("_d2", "Slides 1", "resource/x-bb-document", modified="m")),
        ])

    def list_attachments(self, cid, content_id):
        return {"_d1": [Attachment("_a1", "Midterm_Solution.pdf")],
                "_d2": [Attachment("_a2", "slides1.pdf")]}[content_id]

    def download_attachment(self, cid, content_id, att_id, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"x")
        return 1


def test_exam_collection(tmp_path):
    s = Store(":memory:")
    res = mirror(_DLClient(), s, COURSE, tmp_path, now=NOW)
    assert res.downloaded == 2 and res.exams == 1  # 仅 Midterm 那个进 _exams
    assert (tmp_path / "MAT3007_Opt" / "_exams" / "Midterm_Solution.pdf").exists()


# ---------- F2 本地检索 ----------
def test_search_downloads():
    s = Store(":memory:")
    s.record_download("att:1", "_c1", "/d/MAT3007/slides1.pdf", "m", 1, NOW)
    s.record_download("att:2", "_c1", "/d/MAT3007/hw2.pdf", "m", 1, NOW)
    assert s.search_downloads("slides") == ["/d/MAT3007/slides1.pdf"]
    assert len(s.search_downloads("MAT3007")) == 2
    assert s.search_downloads("zzz") == []


# ---------- F5 待批积压 ----------
def test_grading_backlog_and_summary():
    s = Store(":memory:")
    from bbwatch.diff import diff_columns

    # 一个已交(NeedsGrading)且截止已过很久的作业
    for ch in diff_columns(
        {}, [Column("_h1", "HW1", "2026-05-01T15:59:00.000Z")],
        {"_h1": ColumnStatus("NeedsGrading")}, cid="_c", scan_id=1, suppress=False,
    ):
        s.apply_change(ch, NOW)
    backlog = s.grading_backlog(NOW, days=14)
    assert len(backlog) == 1 and backlog[0]["name"] == "HW1"
    sid = s.start_scan(NOW)
    s.finish_scan(sid, "ok", NOW)
    assert "未出分" in build_session_summary(s, NOW)


def test_submitted_ungraded_and_format():
    from bbwatch.cli import format_pending
    from bbwatch.diff import diff_columns

    s = Store(":memory:")
    for ch in diff_columns(
        {}, [Column("_h1", "HW2", "2026-05-01T15:59:00.000Z")],
        {"_h1": ColumnStatus("NeedsGrading")}, cid="_c", scan_id=1, suppress=False,
        course_code="MAT3007:Optimization_L01",
    ):
        s.apply_change(ch, NOW)
    p = s.submitted_ungraded()
    assert len(p) == 1 and p[0]["name"] == "HW2"
    assert p[0]["course"] == "MAT3007:Optimization_L01"
    assert p[0]["waited_days"] is not None and p[0]["waited_days"] >= 0
    out = format_pending(p)
    assert "HW2" in out and "待批改" in out
    assert "没有已提交" in format_pending([])


def test_submitted_ungraded_excludes_scored_and_zero_point():
    """BB 的 status 不可靠：NeedsGrading 但有分=已批改;0 分列=占位非作业。两者都不该进待批改。"""
    from bbwatch.diff import diff_columns

    s = Store(":memory:")
    DUE = "2026-05-01T15:59:00.000Z"
    cols = [
        Column("_h1", "真待批改", DUE, score_possible=100),       # NeedsGrading + 无分 → 真待批改
        Column("_h2", "已批改但状态怪", DUE, score_possible=100),  # NeedsGrading + 有分 → 实为已批改
        Column("_h3", "Course Schedule Dates", DUE, score_possible=0),  # 0 分占位列 → 非作业
    ]
    statuses = {
        "_h1": ColumnStatus("NeedsGrading", None),
        "_h2": ColumnStatus("NeedsGrading", 100.0),
        "_h3": ColumnStatus("NeedsGrading", None),
    }
    for ch in diff_columns({}, cols, statuses, cid="_c", scan_id=1, suppress=False):
        s.apply_change(ch, NOW)
    assert [p["name"] for p in s.submitted_ungraded()] == ["真待批改"]


def test_actionable_excludes_zero_point_placeholder():
    """0 分占位列(如 Course Schedule Dates)不是可跟踪作业,不进待完成。"""
    from bbwatch.diff import diff_columns

    s = Store(":memory:")
    DUE = "2026-07-10T15:59:00.000Z"
    cols = [
        Column("_t1", "Real HW", DUE, score_possible=100),
        Column("_t2", "Schedule Dates", DUE, score_possible=0),
    ]
    statuses = {"_t1": ColumnStatus("None", None), "_t2": ColumnStatus("None", None)}
    for ch in diff_columns({}, cols, statuses, cid="_c", scan_id=1, suppress=False):
        s.apply_change(ch, NOW)
    assert [t["name"] for t in s.actionable_tasks()] == ["Real HW"]


def test_tasks_expose_content_id_for_jump():
    """跳转到具体作业页需要 content_id(uploadAssignment 深链)。"""
    from bbwatch.diff import diff_columns

    s = Store(":memory:")
    cols = [
        Column("_t1", "待完成HW", "2026-07-10T15:59:00.000Z", content_id="_618329_1", score_possible=100),
        Column("_p1", "待批改HW", "2026-05-01T15:59:00.000Z", content_id="_638685_1", score_possible=100),
    ]
    statuses = {"_t1": ColumnStatus("None"), "_p1": ColumnStatus("NeedsGrading", None)}
    for ch in diff_columns({}, cols, statuses, cid="_c", scan_id=1, suppress=False):
        s.apply_change(ch, NOW)
    assert s.actionable_tasks()[0]["content_id"] == "_618329_1"
    assert s.submitted_ungraded()[0]["content_id"] == "_638685_1"


def test_grading_backlog_excludes_scored():
    """已出分的不算待批积压(不该催老师)。"""
    from bbwatch.diff import diff_columns

    s = Store(":memory:")
    DUE = "2026-05-01T15:59:00.000Z"
    cols = [Column("_h1", "有分", DUE, score_possible=100),
            Column("_h2", "无分", DUE, score_possible=100)]
    statuses = {"_h1": ColumnStatus("NeedsGrading", 90.0),
                "_h2": ColumnStatus("NeedsGrading", None)}
    for ch in diff_columns({}, cols, statuses, cid="_c", scan_id=1, suppress=False):
        s.apply_change(ch, NOW)
    assert [b["name"] for b in s.grading_backlog(NOW, days=14)] == ["无分"]
