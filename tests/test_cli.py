from pathlib import Path

from bbwatch import cli
from bbwatch.bbclient import BB
from bbwatch.cli import run_whoami
from bbwatch.secrets import Credentials
from bbwatch.transport import FakeTransport, Response

FIX = Path(__file__).parent / "fixtures"


def test_windows_console_uses_utf8(monkeypatch):
    class Stream:
        def __init__(self):
            self.calls = []

        def reconfigure(self, **kwargs):
            self.calls.append(kwargs)

    stdout = Stream()
    stderr = Stream()
    monkeypatch.setattr(cli, "is_windows", lambda: True)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    cli._configure_console()

    assert stdout.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert stderr.calls == [{"encoding": "utf-8", "errors": "replace"}]


def _r(name):
    return Response(200, {"Content-Type": "application/json"}, (FIX / name).read_text(encoding="utf-8"), "u")


def test_run_whoami_composes_summary():
    base = BB + "/learn/api/public/v1/users/_10000_1/courses?expand=course&limit=100"
    nxt = BB + "/learn/api/public/v1/users/_10000_1/courses?expand=course&limit=1&offset=1"
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
    assert "示例同学" in summary
    assert "_10000_1" in summary
    assert "在读 1" in summary
