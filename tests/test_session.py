import os
import stat

import pytest

from bbwatch import session as sess
from bbwatch.bbclient import BbClient
from bbwatch.errors import AuthCircuitOpenError, CredentialError, SessionRefreshError
from bbwatch.secrets import Credentials
from bbwatch.store import Store
from bbwatch.transport import FakeTransport, Response

NOW = "2026-06-28T00:00:00.000Z"
COOKIE = [{"name": "JSESSIONID", "value": "x", "domain": "bb.cuhk.edu.cn", "path": "/"}]


def test_save_load_session_roundtrip_and_perms(tmp_path):
    t = FakeTransport()
    t.import_cookies(COOKIE)
    p = tmp_path / "session"
    sess.save_session(t, p)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600  # 0600
    t2 = FakeTransport()
    assert sess.load_session(t2, p) is True
    assert t2.export_cookies() == COOKIE


def test_load_session_missing(tmp_path):
    assert sess.load_session(FakeTransport(), tmp_path / "nope") is False


def test_circuit_breaker_trips_and_resets():
    s = Store(":memory:")
    assert s.auth_circuit_open(NOW) is False
    assert s.record_auth_failure(NOW) is False  # 1
    assert s.record_auth_failure(NOW) is False  # 2
    assert s.record_auth_failure(NOW) is True   # 3 → 熔断
    assert s.auth_circuit_open(NOW) is True
    s.reset_auth_failures()
    assert s.auth_circuit_open(NOW) is False


def test_ensure_session_reuses_valid_cache(tmp_path, monkeypatch):
    n = {"login": 0}
    monkeypatch.setattr(sess, "adfs_login", lambda t, c: n.__setitem__("login", n["login"] + 1))
    t = FakeTransport()
    t.import_cookies(COOKIE)
    p = tmp_path / "session"
    sess.save_session(t, p)
    sess.ensure_session(
        FakeTransport(), Store(":memory:"), Credentials("u", "p"), p, now=NOW, verify=lambda tr: True
    )
    assert n["login"] == 0  # 缓存有效 → 不登录


def test_ensure_session_logs_in_when_invalid(tmp_path, monkeypatch):
    n = {"login": 0}
    monkeypatch.setattr(sess, "adfs_login", lambda t, c: n.__setitem__("login", n["login"] + 1))
    p = tmp_path / "session"
    sess.ensure_session(
        FakeTransport(), Store(":memory:"), Credentials("u", "p"), p, now=NOW, verify=lambda tr: False
    )
    assert n["login"] == 1
    assert p.exists()  # 登录后写缓存


def test_ensure_session_circuit_open_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, "adfs_login", lambda t, c: None)
    s = Store(":memory:")
    for _ in range(3):
        s.record_auth_failure(NOW)
    with pytest.raises(AuthCircuitOpenError):
        sess.ensure_session(
            FakeTransport(), s, Credentials("u", "p"), tmp_path / "s", now=NOW, verify=lambda tr: False
        )


def test_ensure_session_bad_creds_trips_after_threshold(tmp_path, monkeypatch):
    def boom(t, c):
        raise CredentialError("bad")

    monkeypatch.setattr(sess, "adfs_login", boom)
    s = Store(":memory:")
    for _ in range(2):
        with pytest.raises(CredentialError):
            sess.ensure_session(
                FakeTransport(), s, Credentials("u", "p"), tmp_path / "s", now=NOW,
                verify=lambda tr: False,
            )
    with pytest.raises(AuthCircuitOpenError):
        sess.ensure_session(
            FakeTransport(), s, Credentials("u", "p"), tmp_path / "s", now=NOW,
            verify=lambda tr: False,
        )


class FlakyTransport:
    """首个请求 401，relogin 后 200。"""

    def __init__(self):
        self.logged_in = False

    def request(self, method, url, *, data=None, headers=None, allow_redirects=True):
        if not self.logged_in:
            return Response(401, {}, "", url)
        return Response(200, {"Content-Type": "application/json"},
                        '{"id":"_49765_1","userName":"x"}', url)


def test_bbclient_replays_on_401():
    t = FlakyTransport()
    client = BbClient(t, relogin=lambda: setattr(t, "logged_in", True))
    assert client.get_me().id == "_49765_1"


def test_bbclient_session_refresh_error_when_still_401():
    class Always401:
        def request(self, *a, **k):
            return Response(401, {}, "", "u")

    with pytest.raises(SessionRefreshError):
        BbClient(Always401(), relogin=lambda: None).get_me()
