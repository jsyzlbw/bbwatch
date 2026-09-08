"""Credential replacement recovers local authentication without losing course data."""

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bbwatch import cli, session
from bbwatch.config import AppPaths
from bbwatch.diff import diff_columns
from bbwatch.errors import AuthCircuitOpenError, CredentialError
from bbwatch.models import Column, ColumnStatus
from bbwatch.secrets import Credentials
from bbwatch.store import Store
from bbwatch.transport import FakeTransport

NOW = "2026-09-08T01:00:00.000Z"
DUE = "2026-09-12T15:59:00.000Z"
STALE_SESSION = b'[{"name":"JSESSIONID","value":"old-test-cookie"}]'


@pytest.fixture
def setup_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path / "isolated-home"))
    monkeypatch.setenv("BBWATCH_USERNAME", "test-student@example.invalid")
    monkeypatch.setenv("BBWATCH_PASSWORD", "test-password")
    save = Mock()
    monkeypatch.setattr(cli, "store_credentials", save)
    monkeypatch.setattr(cli, "adfs_login", Mock(side_effect=AssertionError("No live login")))
    monkeypatch.setattr(cli, "CurlCffiTransport", Mock(side_effect=AssertionError("No network")))
    return AppPaths(), save


def task_snapshot(store):
    return {
        "tasks": store.actionable_tasks(),
        "pending": store.submitted_ungraded(),
        "hidden": store.hidden_tasks(),
        "baseline": store.baseline_established("_course", "columns"),
        "last_scan": store.last_scan_time(),
    }


def seed_locked_home(paths):
    paths.ensure_dirs()
    store = Store(paths.db_path)
    try:
        scan_id = store.start_scan(NOW)
        store.establish_baseline("_course", "columns", NOW)
        for change in diff_columns(
            {},
            [Column("_done", "Completed assignment", DUE),
             Column("_pending", "Awaiting feedback", DUE),
             Column("_hidden", "Hidden assignment", DUE)],
            {"_done": ColumnStatus("None"), "_pending": ColumnStatus("NeedsGrading"),
             "_hidden": ColumnStatus("None")},
            cid="_course", scan_id=scan_id, suppress=False,
        ):
            store.apply_change(change, NOW)
        store.mark_manual_done("col:_course:_done", True, NOW)
        store.set_hidden("col:_course:_hidden", True, NOW)
        store.finish_scan(scan_id, "success", NOW)
        for _ in range(3):
            store.record_auth_failure(NOW)
        assert store.auth_circuit_open(NOW)
        snapshot = task_snapshot(store)
    finally:
        store.close()
    paths.session_path.write_bytes(STALE_SESSION)
    return snapshot


def read_auth_state(paths):
    with sqlite3.connect(paths.db_path) as connection:
        return connection.execute(
            "SELECT fail_count, circuit_open_until FROM auth_state WHERE id=1"
        ).fetchone()


def test_setup_replaces_credentials_and_recovers_auth_without_changing_tasks(setup_home, capsys):
    paths, save = setup_home
    before = seed_locked_home(paths)

    assert cli.cmd_setup(SimpleNamespace(stdin=False)) == 0

    save.assert_called_once_with("test-student@example.invalid", "test-password")
    store = Store(paths.db_path)
    try:
        assert store.auth_circuit_open(NOW) is False
        assert task_snapshot(store) == before
    finally:
        store.close()
    assert read_auth_state(paths) == (0, None)
    assert not paths.session_path.exists()
    assert "已存入" in capsys.readouterr().out


def test_first_setup_succeeds_without_existing_database_or_session(setup_home, capsys):
    paths, save = setup_home
    assert not paths.db_path.exists()
    assert not paths.session_path.exists()

    assert cli.cmd_setup(SimpleNamespace(stdin=False)) == 0

    save.assert_called_once_with("test-student@example.invalid", "test-password")
    assert not paths.session_path.exists()
    if paths.db_path.exists():
        store = Store(paths.db_path)
        try:
            assert store.auth_circuit_open(NOW) is False
            assert store.actionable_tasks() == []
        finally:
            store.close()
    assert "已存入" in capsys.readouterr().out


def test_failed_credential_save_preserves_auth_session_and_tasks(setup_home, capsys):
    paths, save = setup_home
    before = seed_locked_home(paths)
    auth_before = read_auth_state(paths)
    save.side_effect = RuntimeError("Test keychain is locked")

    with pytest.raises(RuntimeError, match="keychain is locked"):
        cli.cmd_setup(SimpleNamespace(stdin=False))

    assert read_auth_state(paths) == auth_before
    assert paths.session_path.read_bytes() == STALE_SESSION
    store = Store(paths.db_path)
    try:
        assert task_snapshot(store) == before
    finally:
        store.close()
    assert "已存入" not in capsys.readouterr().out


def test_repeating_successful_setup_is_safe_for_existing_tasks(setup_home):
    paths, save = setup_home
    before = seed_locked_home(paths)

    for _ in range(2):
        assert cli.cmd_setup(SimpleNamespace(stdin=False)) == 0

    assert save.call_count == 2
    assert read_auth_state(paths) == (0, None)
    assert not paths.session_path.exists()
    store = Store(paths.db_path)
    try:
        assert task_snapshot(store) == before
    finally:
        store.close()


def test_setup_forces_one_login_with_replacement_credentials_even_if_old_cookie_was_valid(
    setup_home, monkeypatch,
):
    paths, save = setup_home
    seed_locked_home(paths)
    assert cli.cmd_setup(SimpleNamespace(stdin=False)) == 0
    credentials = Credentials(*save.call_args.args)
    new_cookie = [{"name": "JSESSIONID", "value": "new-test-cookie"}]
    login = Mock(side_effect=lambda transport, creds: transport.import_cookies(new_cookie))
    monkeypatch.setattr(session, "adfs_login", login)
    verify = Mock(return_value=True)
    store = Store(paths.db_path)
    first, second = FakeTransport(), FakeTransport()
    try:
        session.ensure_session(first, store, credentials, paths.session_path, now=NOW, verify=verify)
        session.ensure_session(second, store, credentials, paths.session_path, now=NOW, verify=verify)
    finally:
        store.close()

    login.assert_called_once_with(first, credentials)
    verify.assert_called_once_with(second)
    assert second.export_cookies() == new_cookie
    assert read_auth_state(paths) == (0, None)


def test_incorrect_replacement_credentials_restart_failure_count_and_trip_at_three(
    setup_home, monkeypatch,
):
    paths, save = setup_home
    seed_locked_home(paths)
    assert cli.cmd_setup(SimpleNamespace(stdin=False)) == 0
    credentials = Credentials(*save.call_args.args)
    login = Mock(side_effect=CredentialError("Wrong replacement credentials"))
    monkeypatch.setattr(session, "adfs_login", login)
    store = Store(paths.db_path)
    try:
        for expected_count in (1, 2):
            with pytest.raises(CredentialError, match="Wrong replacement credentials"):
                session.ensure_session(
                    FakeTransport(), store, credentials, paths.session_path,
                    now=NOW, verify=lambda _: True,
                )
            assert read_auth_state(paths) == (expected_count, None)
            assert store.auth_circuit_open(NOW) is False
        for _ in range(2):
            with pytest.raises(AuthCircuitOpenError):
                session.ensure_session(
                    FakeTransport(), store, credentials, paths.session_path,
                    now=NOW, verify=lambda _: True,
                )
        assert store.auth_circuit_open(NOW) is True
    finally:
        store.close()

    assert login.call_count == 3
    assert read_auth_state(paths)[0] == 3
    assert not paths.session_path.exists()


@pytest.mark.parametrize("failure", ["reset", "session"])
def test_setup_closes_open_store_when_local_auth_recovery_fails(
    setup_home, monkeypatch, capsys, failure,
):
    paths, save = setup_home
    seed_locked_home(paths)
    auth_before = read_auth_state(paths)
    opened = []

    def tracked_store(path):
        store = Store(path)
        opened.append(store)
        if failure == "reset":
            monkeypatch.setattr(
                store, "reset_auth_failures", Mock(side_effect=OSError("Test recovery failure"))
            )
        return store

    monkeypatch.setattr(cli, "Store", tracked_store)
    if failure == "session":
        real_unlink = Path.unlink

        def fail_session_unlink(path, *args, **kwargs):
            if path == paths.session_path:
                raise OSError("Test recovery failure")
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_session_unlink)

    try:
        with pytest.raises(RuntimeError, match="凭据已保存.*重置失败") as error:
            cli.cmd_setup(SimpleNamespace(stdin=False))
        assert isinstance(error.value.__cause__, OSError)
        assert "Test recovery failure" in str(error.value)
        save.assert_called_once_with("test-student@example.invalid", "test-password")
        if failure == "reset":
            assert opened, "The reset failure must be exercised"
        for store in opened:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                store._conn.execute("SELECT 1")
        assert "已存入" not in capsys.readouterr().out
        if failure == "session":
            assert read_auth_state(paths) == auth_before
            assert paths.session_path.read_bytes() == STALE_SESSION
    finally:
        for store in opened:
            store.close()
