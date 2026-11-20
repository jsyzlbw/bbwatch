import argparse
import plistlib
import subprocess
from pathlib import Path

import pytest

from bbwatch import cli, launchagent


@pytest.fixture(autouse=True)
def platform_dependencies(request, monkeypatch):
    # System-call simulations can run on Windows without a real Unix user ID.
    uid = launchagent.os.getuid() if hasattr(launchagent.os, "getuid") else 1000
    monkeypatch.setattr(
        launchagent, "os", argparse.Namespace(getuid=lambda: uid, replace=launchagent.os.replace)
    )
    if request.node.name.startswith("test_sync"):
        pytest.importorskip("fcntl")


class FakeLaunchctl:
    def __init__(self, files):
        self.files = files
        self.loaded = set()
        self.calls = []
        self.fail_bootstrap = 0
        self.fail_stop = set()

    def __call__(self, *args, check=True):
        self.calls.append(args)
        command = args[0]
        target = args[-1].split("/")[-1]
        code = 0
        if command == "print":
            code = 0 if target in self.loaded else 113
        elif command == "bootout":
            if target in self.fail_stop:
                code = 5
            elif target in self.loaded:
                self.loaded.remove(target)
            else:
                code = 113
        elif command == "bootstrap":
            target = plistlib.loads(Path(args[-1]).read_bytes())["Label"]
            if self.fail_bootstrap:
                self.fail_bootstrap -= 1
                code = 5
            else:
                self.loaded.add(target)
        result = subprocess.CompletedProcess(args, code, stdout="", stderr="simulated error")
        if check and code:
            raise subprocess.CalledProcessError(code, args)
        return result


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setattr(launchagent.sys, "platform", "darwin")
    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path / "data"))
    files = launchagent.agent_files(tmp_path)
    monkeypatch.setattr(launchagent, "agent_files", lambda: files)
    fake = FakeLaunchctl(files)
    monkeypatch.setattr(launchagent, "launchctl", fake)
    args = argparse.Namespace(
        action="install",
        interval=3600,
        courses=["MAT3007"],
        dest=str(tmp_path / "downloads"),
        port=8765,
    )
    return files, fake, args


def jobs(tmp_path, **changes):
    options = {
        "python": "/a path/venv/bin/python",
        "root": tmp_path / "data",
        "interval": 3600,
        "courses": ["MAT3007", "CSC1001"],
        "dest": tmp_path / "course files",
        "port": 8766,
    }
    options.update(changes)
    return launchagent.build_agents(**options)


def test_plists_are_valid_and_do_not_use_shell(tmp_path):
    result = jobs(tmp_path)
    for kind, job in result.items():
        assert plistlib.loads(plistlib.dumps(job)) == job
        assert job["RunAtLoad"] is True
        assert job["ProgramArguments"][0] == "/a path/venv/bin/python"
        assert job["EnvironmentVariables"]["BBWATCH_HOME"] == str(tmp_path / "data")
        assert job["StandardErrorPath"].endswith(f"{kind}.error.log")
    assert result["dashboard"]["KeepAlive"] is True
    assert "--no-browser" in result["dashboard"]["ProgramArguments"]
    assert result["sync"]["StartInterval"] == 3600
    assert "KeepAlive" not in result["sync"]
    assert str(tmp_path / "course files") in result["sync"]["ProgramArguments"]


@pytest.mark.parametrize(
    "changes",
    [{"interval": 0}, {"interval": 59}, {"courses": []}, {"courses": ["1"]}, {"port": 65536}],
)
def test_invalid_configuration(tmp_path, changes):
    with pytest.raises(ValueError):
        jobs(tmp_path, **changes)


def test_install_reinstall_and_uninstall(system):
    files, fake, args = system
    assert launchagent.manage(args) == 0
    assert launchagent.manage(args) == 0
    assert all(p.exists() for p in files.values())
    assert len([c for c in fake.calls if c[0] == "bootstrap"]) == 4
    args.action = "uninstall"
    assert launchagent.manage(args) == 0
    assert all(not p.exists() for p in files.values())
    assert not fake.loaded


def test_cli_dispatch(monkeypatch):
    seen = []
    monkeypatch.setattr(launchagent, "manage", lambda args: seen.append(args) or 0)
    assert (
        cli.main(
            [
                "agent",
                "install",
                "--interval",
                "3600",
                "--courses",
                "MAT3007",
                "--dest",
                "/tmp/with spaces",
            ]
        )
        == 0
    )
    assert seen[0].dest == "/tmp/with spaces"


def test_sync_continues_after_scan_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "run_scan_with_status", lambda *a, **kw: ("partial", True))
    course = argparse.Namespace(id="course", course_id="MAT3007", is_active=True)

    class Client:
        def get_me(self):
            return argparse.Namespace(id="me")

        def list_courses(self, uid):
            return [course]

    closed = []
    store = argparse.Namespace(close=lambda: closed.append(True))
    monkeypatch.setattr(cli, "_authed", lambda: (Client(), store, cli.AppPaths()))
    calls = []

    def mirror(*a, **kw):
        calls.append(a)
        return argparse.Namespace(downloaded=1, skipped=0, failed=0, errors=[])

    monkeypatch.setattr("bbwatch.downloader.mirror", mirror)
    assert launchagent.run_sync(argparse.Namespace(courses=["MAT3007"], dest=str(tmp_path))) == 1
    assert len(calls) == 1
    assert closed == [True]
    assert "同步结束，状态：失败/部分完成" in capsys.readouterr().out


def test_sync_continues_after_unknown_course(tmp_path, monkeypatch):
    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "run_scan_with_status", lambda *a, **kw: ("ok", False))
    course = argparse.Namespace(id="course", course_id="MAT3007", is_active=True)
    client = argparse.Namespace(
        get_me=lambda: argparse.Namespace(id="me"), list_courses=lambda uid: [course]
    )
    monkeypatch.setattr(
        cli, "_authed", lambda: (client, argparse.Namespace(close=lambda: None), cli.AppPaths())
    )
    calls = []

    def mirror(*a, **kw):
        calls.append(a)
        return argparse.Namespace(downloaded=1, skipped=0, failed=0, errors=[])

    monkeypatch.setattr("bbwatch.downloader.mirror", mirror)
    args = argparse.Namespace(courses=["MISSING", "MAT3007", "MAT3007"], dest=str(tmp_path))
    assert launchagent.run_sync(args) == 1
    assert len(calls) == 1


def test_sync_skips_when_locked(tmp_path, monkeypatch):
    import fcntl

    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path))
    with (tmp_path / "agent.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert launchagent.run_sync(argparse.Namespace()) == 0


def test_install_rejects_foreign_plist_before_starting(tmp_path, monkeypatch):
    monkeypatch.setattr(launchagent.sys, "platform", "darwin")
    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path / "data"))
    files = launchagent.agent_files(tmp_path)
    monkeypatch.setattr(launchagent, "agent_files", lambda: files)
    file = files["dashboard"]
    file.parent.mkdir(parents=True)
    file.write_bytes(plistlib.dumps({"Label": "some.other.app"}))
    calls = []
    monkeypatch.setattr(launchagent, "launchctl", lambda *a, **kw: calls.append(a))
    with pytest.raises(ValueError, match="拒绝覆盖"):
        launchagent.manage(
            argparse.Namespace(
                action="install", interval=3600, courses=["MAT3007"], dest=str(tmp_path), port=8765
            )
        )
    assert not calls


def test_sync_downloads_incrementally_without_network(tmp_path, monkeypatch):
    """Exercise the real mirror/store path; all downloaded bytes stay in tmp_path."""
    from pathlib import Path

    from bbwatch.models import Attachment, Content, Course
    from bbwatch.store import Store

    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "run_scan_with_status", lambda *a, **kw: ("ok", False))
    course = Course("_c1", "MAT3007", "Math", "_t1", "Student", "Yes", "Classic")
    content = Content("_d1", "Slides", "resource/x-bb-document", modified="2026-11-17")
    calls = []

    class Client:
        def get_me(self):
            return argparse.Namespace(id="me")

        def list_courses(self, uid):
            return [course]

        def walk_contents(self, cid):
            return iter([(["Content"], content)])

        def list_attachments(self, cid, content_id):
            return [Attachment("_a1", "slides.pdf")]

        def download_attachment(self, cid, content_id, att_id, path):
            calls.append(path)
            Path(path).write_bytes(b"tiny offline test fixture")
            return len(b"tiny offline test fixture")

    def authed():
        paths = cli.AppPaths()
        paths.ensure_dirs()
        return Client(), Store(paths.db_path), paths

    monkeypatch.setattr(cli, "_authed", authed)
    destination = tmp_path / "download directory"
    args = argparse.Namespace(courses=["MAT3007"], dest=str(destination))
    assert launchagent.run_sync(args) == 0
    assert launchagent.run_sync(args) == 0
    assert len(calls) == 1
    assert (destination / "MAT3007" / "Content" / "slides.pdf").read_bytes() == (
        b"tiny offline test fixture"
    )


def test_uninstall_failed_stop_keeps_plist(system, capsys):
    files, fake, args = system
    launchagent.manage(args)
    original = files["dashboard"].read_bytes()
    fake.fail_stop.add(f"{launchagent.LABEL}.dashboard")
    args.action = "uninstall"
    assert launchagent.manage(args) == 1
    assert files["dashboard"].read_bytes() == original
    assert f"{launchagent.LABEL}.dashboard" in fake.loaded
    assert not files["sync"].exists()
    assert "dashboard" in capsys.readouterr().err


def test_uninstall_absent_jobs_removes_configuration(system):
    files, fake, args = system
    launchagent.manage(args)
    fake.loaded.clear()
    args.action = "uninstall"
    assert launchagent.manage(args) == 0
    assert all(not p.exists() for p in files.values())


def test_uninstall_unknown_query_failure_preserves_config(system):
    files, fake, args = system
    launchagent.manage(args)
    fake.fail_stop.add(f"{launchagent.LABEL}.dashboard")

    def fail_query(*a, **kw):
        if a[0] == "print":
            return subprocess.CompletedProcess(a, 5, stdout="", stderr="permission denied")
        return fake(*a, **kw)

    from unittest.mock import patch

    args.action = "uninstall"
    with patch.object(launchagent, "launchctl", fail_query):
        assert launchagent.manage(args) == 1
    assert files["dashboard"].exists()


def fail_sync_bootstrap(fake, monkeypatch):
    failed = False

    def call(*args, **kwargs):
        nonlocal failed
        if args[0] == "bootstrap" and args[-1].endswith("sync.plist") and not failed:
            failed = True
            raise subprocess.CalledProcessError(5, args)
        return fake(*args, **kwargs)

    monkeypatch.setattr(launchagent, "launchctl", call)


def test_first_install_failure_removes_started_jobs_and_new_files(system, monkeypatch):
    files, fake, args = system
    fail_sync_bootstrap(fake, monkeypatch)
    with pytest.raises(RuntimeError, match="已恢复"):
        launchagent.manage(args)
    assert not fake.loaded
    assert all(not p.exists() for p in files.values())


@pytest.mark.parametrize(
    "initially_loaded", [set(), {"dashboard"}, {"sync"}, {"dashboard", "sync"}]
)
def test_update_failure_restores_original_files_and_loading(system, monkeypatch, initially_loaded):
    files, fake, args = system
    launchagent.manage(args)
    original = {kind: p.read_bytes() for kind, p in files.items()}
    fake.loaded = {f"{launchagent.LABEL}.{kind}" for kind in initially_loaded}
    expected = fake.loaded.copy()
    args.interval = 7200
    args.port = 8766
    fail_sync_bootstrap(fake, monkeypatch)
    with pytest.raises(RuntimeError, match="已恢复"):
        launchagent.manage(args)
    assert fake.loaded == expected
    assert {kind: p.read_bytes() for kind, p in files.items()} == original


def test_update_stop_failure_does_not_touch_original(system):
    files, fake, args = system
    launchagent.manage(args)
    original = {kind: p.read_bytes() for kind, p in files.items()}
    fake.fail_stop.add(f"{launchagent.LABEL}.dashboard")
    with pytest.raises(RuntimeError, match="未能停止"):
        launchagent.manage(args)
    assert {kind: p.read_bytes() for kind, p in files.items()} == original
    assert fake.loaded == {f"{launchagent.LABEL}.{kind}" for kind in files}


def test_sync_authentication_rejection_only_attempted_once(tmp_path, monkeypatch, capsys):
    from bbwatch.errors import CredentialError

    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path))
    attempts = []

    def authed():
        attempts.append(True)
        raise CredentialError("rejected")

    monkeypatch.setattr(cli, "_authed", authed)
    assert launchagent.run_sync(argparse.Namespace(courses=["MAT3007"], dest=str(tmp_path))) == 1
    assert len(attempts) == 1
    assert "同步结束，状态：失败/部分完成" in capsys.readouterr().out


def test_sync_scan_authentication_failure_skips_downloads(tmp_path, monkeypatch, capsys):
    from bbwatch.errors import CredentialError

    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path))
    attempts = []
    closed = []

    def authed():
        attempts.append(True)
        return object(), argparse.Namespace(close=lambda: closed.append(True)), cli.AppPaths()

    def scan(*a, **kw):
        raise CredentialError("rejected")

    monkeypatch.setattr(cli, "_authed", authed)
    monkeypatch.setattr(cli, "run_scan_with_status", scan)
    assert launchagent.run_sync(argparse.Namespace(courses=["MAT3007"], dest=str(tmp_path))) == 1
    assert attempts == [True]
    assert closed == [True]
    assert "同步结束，状态：失败/部分完成" in capsys.readouterr().out


def test_sync_real_partial_scan_cannot_be_hidden_by_successful_download(
    tmp_path, monkeypatch, capsys
):
    from bbwatch.scanner import ScanResult
    from bbwatch.store import Store

    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path))
    course = argparse.Namespace(id="course", course_id="MAT3007", is_active=True)
    client = argparse.Namespace(
        get_me=lambda: argparse.Namespace(id="me"), list_courses=lambda uid: [course]
    )
    paths = cli.AppPaths()
    store = Store(paths.db_path)
    monkeypatch.setattr(cli, "_authed", lambda: (client, store, paths))
    monkeypatch.setattr(
        cli,
        "scan",
        lambda *a, **kw: ScanResult(
            courses_scanned=1, failures=["MAT3007/announcements: TransportError"]
        ),
    )
    downloads = []

    def mirror(*a, **kw):
        downloads.append(True)
        return argparse.Namespace(downloaded=1, skipped=0, failed=0, errors=[])

    monkeypatch.setattr("bbwatch.downloader.mirror", mirror)
    assert launchagent.run_sync(argparse.Namespace(courses=["MAT3007"], dest=str(tmp_path))) == 1
    assert downloads == [True]
    output = capsys.readouterr().out
    assert "部分维度失败" in output
    assert "同步结束，状态：失败/部分完成" in output


def test_client_does_not_retry_rejected_reauthentication(tmp_path, monkeypatch):
    from bbwatch.errors import CredentialError

    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "load_credentials", lambda: object())
    monkeypatch.setattr(cli, "CurlCffiTransport", lambda: object())
    monkeypatch.setattr(cli, "ensure_session", lambda *a, **kw: None)
    attempts = []

    def reject(*a):
        attempts.append(True)
        raise CredentialError("rejected")

    monkeypatch.setattr(cli, "adfs_login", reject)
    client, store, _ = cli._authed()
    try:
        for _ in range(2):
            with pytest.raises(CredentialError):
                client._relogin()
    finally:
        store.close()
    assert attempts == [True]


def test_rollback_stop_failure_reports_job_and_keeps_configuration(system, monkeypatch):
    files, fake, args = system
    failed = False

    def call(*a, **kw):
        nonlocal failed
        if a[0] == "bootstrap" and a[-1].endswith("sync.plist") and not failed:
            failed = True
            fake.fail_stop.add(f"{launchagent.LABEL}.dashboard")
            raise subprocess.CalledProcessError(5, a)
        return fake(*a, **kw)

    monkeypatch.setattr(launchagent, "launchctl", call)
    with pytest.raises(RuntimeError, match="回滚未完整完成.*dashboard"):
        launchagent.manage(args)
    assert files["dashboard"].exists()
    assert f"{launchagent.LABEL}.dashboard" in fake.loaded
    assert not files["sync"].exists()
