from unittest.mock import Mock

import pytest

from bbwatch import cli
from bbwatch.errors import CredentialError


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
