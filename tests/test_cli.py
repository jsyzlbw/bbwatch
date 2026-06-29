from pathlib import Path

from bbwatch.bbclient import BB
from bbwatch.cli import run_whoami
from bbwatch.secrets import Credentials
from bbwatch.transport import FakeTransport, Response

FIX = Path(__file__).parent / "fixtures"


def _r(name):
    return Response(200, {"Content-Type": "application/json"}, (FIX / name).read_text(), "u")


def test_run_whoami_composes_summary():
    base = BB + "/learn/api/public/v1/users/_49765_1/courses?expand=course&limit=100"
    nxt = BB + "/learn/api/public/v1/users/_49765_1/courses?expand=course&limit=1&offset=1"
    t = FakeTransport(
        {
            ("GET", BB + "/learn/api/public/v1/users/me"): _r("users_me.json"),
            ("GET", base): _r("courses_p1.json"),
            ("GET", nxt): _r("courses_p2.json"),
        }
    )
    summary = run_whoami(
        transport=t,
        creds=Credentials("u@link.cuhk.edu.cn", "pw"),
        login_fn=lambda tr, c: None,
    )
    assert "梁博文" in summary
    assert "_49765_1" in summary
    assert "在读 1" in summary
