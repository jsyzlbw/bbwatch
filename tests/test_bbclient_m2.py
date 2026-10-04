import json
from pathlib import Path

import pytest

from bbwatch.bbclient import API, BB, BbClient
from bbwatch.cli import format_pending
from bbwatch.scanner import scan
from bbwatch.store import Store
from bbwatch.transport import FakeTransport, Response

FIX = Path(__file__).parent / "fixtures"


def _resp(name, status=200, ct="application/json"):
    return Response(status, {"Content-Type": ct}, (FIX / name).read_text(encoding="utf-8"), "u")


def test_list_columns_filters_summary_columns():
    url = BB + API + "/courses/_17236_1/gradebook/columns?limit=100"
    t = FakeTransport({("GET", url): _resp("columns_p1.json")})
    cols = BbClient(t).list_columns("_17236_1")
    assert {c.id for c in cols} == {"_c_hw1", "_c_hw4"}  # Weighted Total filtered
    hw1 = next(c for c in cols if c.id == "_c_hw1")
    assert hw1.due_utc == "2026-06-09T15:59:00.000Z"
    assert hw1.content_id == "_638150_1"
    assert hw1.score_possible == 100


@pytest.mark.parametrize("grading", [{}, {"due": None}, None])
def test_scan_keeps_undated_submission_in_pending(grading):
    cid, uid = "_course", "_user"
    prefix = f"{BB}{API}"
    due = "2026-06-30T15:59:00.000Z"

    def response(data):
        return Response(200, {"Content-Type": "application/json"}, json.dumps(data), "u")

    transport = FakeTransport({
        ("GET", f"{prefix}/users/{uid}/courses?expand=course&limit=100"): response({
            "results": [{"courseRoleId": "Student", "course": {
                "id": cid, "courseId": "DEMO", "availability": {"available": "Yes"},
            }}],
        }),
        ("GET", f"{prefix}/courses/{cid}/gradebook/columns?limit=100"): response({
            "results": [
                {"id": "_total", "name": "Total"},
                {"id": "_weighted", "name": "Weighted Total", "grading": {},
                 "contentId": None},
                {"id": "_lab", "name": "Undated lab", "grading": grading,
                 "contentId": "_content", "score": {"possible": 100}},
                {"id": "_dated", "name": "Dated manual column", "grading": {"due": due}},
            ],
        }),
        ("GET", f"{prefix}/courses/{cid}/gradebook/columns/_lab/users/{uid}"): response({
            "status": "NeedsGrading",
        }),
        ("GET", f"{prefix}/courses/{cid}/gradebook/columns/_dated/users/{uid}"): response({
            "status": "None",
        }),
        ("GET", f"{prefix}/courses/{cid}/announcements?limit=100"): response({"results": []}),
    })
    store = Store(":memory:")
    try:
        result = scan(BbClient(transport), store, uid, now=due, include_contents=False)
        assert result.failures == []
        assert result.new_events == 0  # Cold start remains silent.
        assert set(store.known_entities(cid, "column")) == {
            f"col:{cid}:_lab", f"col:{cid}:_dated",
        }
        pending = store.submitted_ungraded()
        assert [task["name"] for task in pending] == ["Undated lab"]
        assert pending[0]["due_utc"] is None
        assert pending[0]["waited_days"] is None
        assert pending[0]["content_id"] == "_content"
        assert "Undated lab" in format_pending(pending)
        assert [task["name"] for task in store.actionable_tasks()] == ["Dated manual column"]
    finally:
        store.close()


def test_get_column_status_graded():
    url = BB + API + "/courses/_17236_1/gradebook/columns/_c_hw1/users/_10000_1"
    t = FakeTransport(
        {("GET", url): Response(200, {"Content-Type": "application/json"},
                                '{"status":"Graded","score":100.0}', "u")}
    )
    s = BbClient(t).get_column_status("_17236_1", "_c_hw1", "_10000_1")
    assert s.status == "Graded" and s.score == 100.0
    assert s.is_done and s.is_graded


def test_get_column_status_404_is_not_submitted():
    url = BB + API + "/courses/_17236_1/gradebook/columns/_c_hw4/users/_10000_1"
    t = FakeTransport({("GET", url): Response(404, {"Content-Type": "application/json"}, "{}", "u")})
    s = BbClient(t).get_column_status("_17236_1", "_c_hw4", "_10000_1")
    assert s.status == "None" and s.score is None
    assert not s.is_done


def test_list_announcements():
    url = BB + API + "/courses/_17236_1/announcements?limit=100"
    t = FakeTransport({("GET", url): _resp("announcements_p1.json")})
    anns = BbClient(t).list_announcements("_17236_1")
    assert len(anns) == 1 and anns[0].title == "Reminder of Assignment 3"
    assert anns[0].created == "2026-06-23T10:35:02.000Z"
