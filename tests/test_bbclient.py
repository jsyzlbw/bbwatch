from pathlib import Path

from bbwatch.bbclient import BB, BbClient
from bbwatch.transport import FakeTransport, Response

FIX = Path(__file__).parent / "fixtures"


def _resp(name):
    return Response(200, {"Content-Type": "application/json"}, (FIX / name).read_text(), "u")


def test_get_me():
    t = FakeTransport({("GET", BB + "/learn/api/public/v1/users/me"): _resp("users_me.json")})
    me = BbClient(t).get_me()
    assert me.id == "_10000_1" and me.given_name == "示例同学"


def test_list_courses_follows_pagination():
    base = BB + "/learn/api/public/v1/users/_10000_1/courses?expand=course&limit=100"
    nxt = BB + "/learn/api/public/v1/users/_10000_1/courses?expand=course&limit=1&offset=1"
    t = FakeTransport(
        {("GET", base): _resp("courses_p1.json"), ("GET", nxt): _resp("courses_p2.json")}
    )
    courses = BbClient(t).list_courses("_10000_1")
    assert len(courses) == 2
    assert {c.course_id for c in courses} == {"MAT3007:Optimization_L01", "PED1201:Badminton"}
    active = [c for c in courses if c.is_active]
    assert len(active) == 1 and active[0].course_id == "MAT3007:Optimization_L01"
