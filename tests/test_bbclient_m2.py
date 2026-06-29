from pathlib import Path

from bbwatch.bbclient import API, BB, BbClient
from bbwatch.transport import FakeTransport, Response

FIX = Path(__file__).parent / "fixtures"


def _resp(name, status=200, ct="application/json"):
    return Response(status, {"Content-Type": ct}, (FIX / name).read_text(), "u")


def test_list_columns_filters_summary_columns():
    url = BB + API + "/courses/_17236_1/gradebook/columns?limit=100"
    t = FakeTransport({("GET", url): _resp("columns_p1.json")})
    cols = BbClient(t).list_columns("_17236_1")
    assert {c.id for c in cols} == {"_c_hw1", "_c_hw4"}  # Weighted Total filtered
    hw1 = next(c for c in cols if c.id == "_c_hw1")
    assert hw1.due_utc == "2026-06-09T15:59:00.000Z"
    assert hw1.content_id == "_638150_1"
    assert hw1.score_possible == 100


def test_get_column_status_graded():
    url = BB + API + "/courses/_17236_1/gradebook/columns/_c_hw1/users/_49765_1"
    t = FakeTransport(
        {("GET", url): Response(200, {"Content-Type": "application/json"},
                                '{"status":"Graded","score":100.0}', "u")}
    )
    s = BbClient(t).get_column_status("_17236_1", "_c_hw1", "_49765_1")
    assert s.status == "Graded" and s.score == 100.0
    assert s.is_done and s.is_graded


def test_get_column_status_404_is_not_submitted():
    url = BB + API + "/courses/_17236_1/gradebook/columns/_c_hw4/users/_49765_1"
    t = FakeTransport({("GET", url): Response(404, {"Content-Type": "application/json"}, "{}", "u")})
    s = BbClient(t).get_column_status("_17236_1", "_c_hw4", "_49765_1")
    assert s.status == "None" and s.score is None
    assert not s.is_done


def test_list_announcements():
    url = BB + API + "/courses/_17236_1/announcements?limit=100"
    t = FakeTransport({("GET", url): _resp("announcements_p1.json")})
    anns = BbClient(t).list_announcements("_17236_1")
    assert len(anns) == 1 and anns[0].title == "Reminder of Assignment 3"
    assert anns[0].created == "2026-06-23T10:35:02.000Z"
