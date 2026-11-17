import argparse
import plistlib

import pytest

from bbwatch import cli, launchagent


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


def test_install_reinstall_and_uninstall(tmp_path, monkeypatch):
    monkeypatch.setattr(launchagent.sys, "platform", "darwin")
    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path / "data"))
    files = launchagent.agent_files(tmp_path)
    monkeypatch.setattr(launchagent, "agent_files", lambda: files)
    calls = []
    monkeypatch.setattr(launchagent, "launchctl", lambda *a, **kw: calls.append(a))
    args = argparse.Namespace(
        action="install",
        interval=3600,
        courses=["MAT3007"],
        dest=str(tmp_path / "downloads"),
        port=8765,
    )
    assert launchagent.manage(args) == 0
    assert launchagent.manage(args) == 0
    assert all(p.exists() for p in files.values())
    assert len([c for c in calls if c[0] == "bootstrap"]) == 4
    args.action = "uninstall"
    assert launchagent.manage(args) == 0
    assert all(not p.exists() for p in files.values())
    assert (tmp_path / "data" / "launchagent-logs").exists()


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


def test_sync_continues_after_scan_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "main", lambda args: 1)
    course = argparse.Namespace(id="course", course_id="MAT3007", is_active=True)

    class Client:
        def get_me(self):
            return argparse.Namespace(id="me")

        def list_courses(self, uid):
            return [course]

    closed = []
    store = argparse.Namespace(close=lambda: closed.append(True))
    monkeypatch.setattr(cli, "_authed", lambda: (Client(), store, None))
    calls = []

    def mirror(*a, **kw):
        calls.append(a)
        return argparse.Namespace(downloaded=1, skipped=0, failed=0, errors=[])

    monkeypatch.setattr("bbwatch.downloader.mirror", mirror)
    assert launchagent.run_sync(argparse.Namespace(courses=["MAT3007"], dest=str(tmp_path))) == 1
    assert len(calls) == 1
    assert closed == [True]


def test_sync_continues_after_unknown_course(tmp_path, monkeypatch):
    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "main", lambda args: 0)
    course = argparse.Namespace(id="course", course_id="MAT3007", is_active=True)
    client = argparse.Namespace(
        get_me=lambda: argparse.Namespace(id="me"), list_courses=lambda uid: [course]
    )
    monkeypatch.setattr(
        cli, "_authed", lambda: (client, argparse.Namespace(close=lambda: None), None)
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
