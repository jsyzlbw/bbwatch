from unittest.mock import Mock

import pytest

from bbwatch import cli, session
from bbwatch.config import AppPaths
from bbwatch.errors import CredentialError, TransportError
from bbwatch.secrets import Credentials
from bbwatch.transport import FakeTransport, Response

ME_URL = "https://bb.cuhk.edu.cn/learn/api/public/v1/users/me"
COOKIE = [{"name": "JSESSIONID", "value": "test-session", "domain": "bb.cuhk.edu.cn"}]


def test_missing_credentials_do_not_open_store(tmp_path, monkeypatch):
    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path / "data"))
    store_type = Mock()
    monkeypatch.setattr(cli, "Store", store_type)
    monkeypatch.setattr(
        cli, "load_credentials", Mock(side_effect=CredentialError("未找到凭据"))
    )
    with pytest.raises(CredentialError):
        cli._authed()
    store_type.assert_not_called()


def test_auth_failure_closes_open_store(tmp_path, monkeypatch):
    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path / "data"))
    store = Mock()
    monkeypatch.setattr(cli, "Store", Mock(return_value=store))
    monkeypatch.setattr(cli, "load_credentials", Mock())
    monkeypatch.setattr(cli, "CurlCffiTransport", Mock())
    monkeypatch.setattr(cli, "ensure_session", Mock(side_effect=RuntimeError("offline")))
    with pytest.raises(RuntimeError, match="offline"):
        cli._authed()
    store.close.assert_called_once_with()


@pytest.fixture
def cached_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path / "data"))
    paths = AppPaths()
    paths.ensure_dirs()
    transport = FakeTransport()
    transport.import_cookies(COOKIE)
    session.save_session(transport, paths.session_path)
    monkeypatch.setattr(cli, "load_credentials", lambda: Credentials("test-user", "test-password"))
    monkeypatch.setattr(cli, "CurlCffiTransport", lambda: transport)
    login = Mock()
    monkeypatch.setattr(session, "adfs_login", login)
    return transport, paths, login


def test_authed_connection_failure_does_not_attempt_adfs_login(cached_auth, monkeypatch):
    transport, paths, login = cached_auth
    cached = paths.session_path.read_bytes()
    request = Mock(side_effect=TransportError("connection unavailable"))
    monkeypatch.setattr(transport, "request", request)

    with pytest.raises(TransportError, match="connection unavailable"):
        cli._authed()

    assert request.call_count == 1
    login.assert_not_called()
    assert paths.session_path.read_bytes() == cached
    assert transport.export_cookies() == COOKIE


@pytest.mark.parametrize("status,content_type,body", [
    (503, "text/html", "Service unavailable"),
    (403, "text/html", "Access denied"),
    (200, "text/html", "Unexpected maintenance page"),
    (200, "application/json", '{"error":"unavailable"}'),
    (200, "application/json", "null"),
])
def test_authed_unusable_identity_response_does_not_relogin(cached_auth, status, content_type, body):
    transport, _, login = cached_auth
    transport.routes[("GET", ME_URL)] = Response(status, {"Content-Type": content_type}, body, ME_URL)

    with pytest.raises(TransportError):
        cli._authed()

    assert transport.calls == [("GET", ME_URL)]
    login.assert_not_called()


def test_authed_explicit_401_refreshes_cached_session(cached_auth):
    transport, _, login = cached_auth
    transport.routes[("GET", ME_URL)] = Response(401, {}, "", ME_URL)

    _, store, _ = cli._authed()
    try:
        assert transport.calls == [("GET", ME_URL)]
        login.assert_called_once()
    finally:
        store.close()


def test_authed_valid_identity_keeps_cached_session(cached_auth):
    transport, _, login = cached_auth
    transport.routes[("GET", ME_URL)] = Response(
        200, {"Content-Type": "application/json"}, '{"id":"_100_1","userName":"test"}', ME_URL,
    )

    _, store, _ = cli._authed()
    try:
        assert transport.calls == [("GET", ME_URL)]
        login.assert_not_called()
    finally:
        store.close()
