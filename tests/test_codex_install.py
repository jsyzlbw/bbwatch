"""Installer checks use temporary homes and a fake, foreground command runner."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install_codex.py"


@pytest.fixture
def installer():
    assert INSTALLER.is_file(), "The Codex installer has not been implemented"
    spec = importlib.util.spec_from_file_location("bbwatch_codex_installer", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def paths(tmp_path):
    repo = tmp_path / "source with spaces"
    bundle = repo / "plugins" / "bbwatch"
    (bundle / ".codex-plugin").mkdir(parents=True)
    (bundle / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "bbwatch",
                "version": "0.1.0",
                "description": "Blackboard tools",
            }
        )
    )
    (bundle / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {"bbwatch": {"command": "bbwatch-mcp", "args": []}},
            }
        )
    )
    (bundle / "README.md").write_text("source bundle")
    (repo / "pyproject.toml").write_text('[project]\nname = "bbwatch"\n')
    home = tmp_path / "home with spaces"
    codex = tmp_path / "tools with spaces" / "codex"
    codex.parent.mkdir()
    codex.write_text("#!/bin/sh\nexit 0\n")
    codex.chmod(0o755)
    return repo, home, codex


def make_plan(installer, paths):
    repo, home, codex = paths
    return installer.plan_install(repo, home=home, codex=str(codex), python=sys.executable)


def write_marketplace(home, payload):
    path = home / ".agents" / "plugins" / "marketplace.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def fake_runner(plan, commands, *, fail_install=False):
    def run(args, **kwargs):
        assert isinstance(args, list)
        assert kwargs["timeout"] > 0
        assert "shell" not in kwargs
        commands.append(args)
        if "-c" in args:
            return subprocess.CompletedProcess(args, 0, stdout="3.12.0\n")
        if args[1:3] == ["-m", "venv"]:
            (plan.venv / "bin").mkdir(parents=True)
            (plan.venv / "bin" / "python").write_text("managed python")
            (plan.venv / "bin" / "bbwatch").write_text("managed CLI")
        if args[1:3] == ["plugin", "add"] and fail_install:
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0)

    return run


def test_dry_run_shows_paths_without_mutation_or_processes(installer, paths, capsys):
    plan = make_plan(installer, paths)
    installer.install(plan, dry_run=True, run=lambda *a, **k: pytest.fail("process started"))
    text = capsys.readouterr().out
    assert str(plan.plugin) in text and str(plan.venv) in text
    assert "bbwatch@personal" in text
    assert not paths[1].exists()


def test_install_uses_absolute_runtime_and_preserves_paths_with_spaces(installer, paths):
    plan = make_plan(installer, paths)
    commands = []
    installer.install(plan, run=fake_runner(plan, commands))
    mcp = json.loads((plan.plugin / ".mcp.json").read_text())["mcpServers"]["bbwatch"]
    assert mcp["command"] == str(plan.venv / "bin" / "python")
    assert mcp["args"] == ["-m", "bbwatch.mcp_server"]
    assert plan.cli.is_symlink()
    assert plan.cli.resolve() == plan.venv / "bin" / "bbwatch"
    pip_commands = [c for c in commands if c[1:3] == ["-m", "pip"]]
    assert len(pip_commands) == 2
    assert all(str(plan.repo) == command[-1] for command in pip_commands)
    assert "--timeout" in pip_commands[0] and "30" in pip_commands[0]
    assert "--retries" in pip_commands[0] and "2" in pip_commands[0]
    assert "--force-reinstall" in pip_commands[1] and "--no-deps" in pip_commands[1]
    assert all("-e" not in c for c in pip_commands)
    assert commands[-1] == [str(paths[2]), "plugin", "add", "bbwatch@personal"]
    assert json.loads((plan.plugin / ".codex-plugin" / "plugin.json").read_text())[
        "version"
    ].startswith("0.1.0+codex.")


def test_marketplace_and_user_data_survive_install_and_reinstall(installer, paths):
    repo, home, _ = paths
    other = {"name": "other", "source": {"source": "local", "path": "./plugins/other"}}
    path = write_marketplace(
        home,
        {
            "name": "my-personal",
            "interface": {"displayName": "Keep my name"},
            "custom": {"keep": True},
            "plugins": [other],
        },
    )
    data = home / ".bbwatch" / "private-user-data"
    data.parent.mkdir()
    data.write_text("preserve me")
    first = make_plan(installer, paths)
    commands = []
    installer.install(first, run=fake_runner(first, commands))
    first_version = json.loads((first.plugin / ".codex-plugin/plugin.json").read_text())["version"]
    (repo / "plugins/bbwatch/README.md").write_text("updated bundle")
    second = make_plan(installer, paths)
    installer.install(second, run=fake_runner(second, commands))
    market = json.loads(path.read_text())
    assert market["name"] == "my-personal"
    assert market["interface"] == {"displayName": "Keep my name"}
    assert market["custom"] == {"keep": True}
    assert market["plugins"][0] == other
    assert len(market["plugins"]) == 2
    assert market["plugins"][1]["source"]["path"] == "./plugins/bbwatch"
    assert market["plugins"][1]["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
    assert data.read_text() == "preserve me"
    assert (second.plugin / "README.md").read_text() == "updated bundle"
    second_version = json.loads((second.plugin / ".codex-plugin/plugin.json").read_text())[
        "version"
    ]
    assert first_version != second_version
    assert len([c for c in commands if c[1:3] == ["-m", "venv"]]) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "bad;name", "plugins": []},
        {"name": "personal", "plugins": "bad"},
        {"name": "personal", "plugins": [{"name": "bad/name"}]},
        {
            "name": "personal",
            "plugins": [
                {"name": "bbwatch", "source": {"source": "local", "path": "./unrelated"}},
            ],
        },
        {
            "name": "personal",
            "plugins": [
                {"name": "bbwatch", "source": {"source": "git", "url": "https://example.com"}},
            ],
        },
    ],
)
def test_invalid_or_unrelated_marketplace_is_rejected_before_mutation(installer, paths, payload):
    path = write_marketplace(paths[1], payload)
    original = path.read_bytes()
    with pytest.raises(installer.InstallError):
        make_plan(installer, paths)
    assert path.read_bytes() == original
    assert not (paths[1] / ".local").exists()


def test_unowned_plugin_directory_is_preserved(installer, paths):
    target = paths[1] / "plugins" / "bbwatch"
    target.mkdir(parents=True)
    precious = target / "user-file"
    precious.write_text("do not remove")
    with pytest.raises(installer.InstallError, match="[Oo]wn|[Uu]nrelated"):
        make_plan(installer, paths)
    assert precious.read_text() == "do not remove"


def test_unowned_runtime_directory_is_preserved(installer, paths):
    runtime = paths[1] / ".local/share/bbwatch-codex"
    runtime.mkdir(parents=True)
    with pytest.raises(installer.InstallError, match="[Oo]wn|[Uu]nrelated"):
        make_plan(installer, paths)
    assert list(runtime.iterdir()) == []


def test_unrelated_cli_collision_is_preserved(installer, paths):
    cli = paths[1] / ".local/bin/bbwatch"
    cli.parent.mkdir(parents=True)
    cli.write_text("another program")
    with pytest.raises(installer.InstallError, match="[Cc]ollision|[Uu]nrelated"):
        make_plan(installer, paths)
    assert cli.read_text() == "another program"


def test_symlink_target_is_rejected_without_touching_destination(installer, paths, tmp_path):
    real = tmp_path / "unrelated plugin"
    real.mkdir()
    target = paths[1] / "plugins/bbwatch"
    target.parent.mkdir(parents=True)
    target.symlink_to(real, target_is_directory=True)
    with pytest.raises(installer.InstallError):
        make_plan(installer, paths)
    assert target.is_symlink() and real.is_dir()


def test_failed_codex_registration_restores_existing_bundle_and_marketplace(installer, paths):
    plan = make_plan(installer, paths)
    commands = []
    installer.install(plan, run=fake_runner(plan, commands))
    original_market = plan.marketplace.read_bytes()
    original_manifest = (plan.plugin / ".codex-plugin/plugin.json").read_bytes()
    (plan.source / "README.md").write_text("must roll back")
    again = make_plan(installer, paths)
    with pytest.raises(installer.InstallError):
        installer.install(again, run=fake_runner(again, commands, fail_install=True))
    assert plan.marketplace.read_bytes() == original_market
    assert (plan.plugin / ".codex-plugin/plugin.json").read_bytes() == original_manifest
    assert (plan.plugin / "README.md").read_text() == "source bundle"
    assert not list(plan.plugin.parent.glob(".bbwatch-stage-*"))


def test_plan_rechecks_collisions_before_starting_processes(installer, paths):
    plan = make_plan(installer, paths)
    plan.cli.parent.mkdir(parents=True)
    plan.cli.write_text("created after planning")
    with pytest.raises(installer.InstallError):
        installer.install(plan, run=lambda *a, **k: pytest.fail("process started"))
    assert plan.cli.read_text() == "created after planning"


def test_malformed_source_mcp_fails_preflight_cleanly(installer, paths):
    (paths[0] / "plugins/bbwatch/.mcp.json").write_text('{"mcpServers": []}')
    with pytest.raises(installer.InstallError, match="MCP"):
        make_plan(installer, paths)
    assert not paths[1].exists()


def test_failed_rollback_preserves_backup_for_recovery(installer, paths, monkeypatch):
    plan = make_plan(installer, paths)
    commands = []
    installer.install(plan, run=fake_runner(plan, commands))
    original = (plan.plugin / ".codex-plugin/plugin.json").read_bytes()
    again = make_plan(installer, paths)
    rename = Path.rename

    def fail_restore(path, target):
        if path.name == "previous":
            raise OSError("simulated recovery filesystem failure")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_restore)
    with pytest.raises((installer.InstallError, OSError)):
        installer.install(again, run=fake_runner(again, commands, fail_install=True))
    backups = list(plan.plugin.parent.glob(".bbwatch-stage-*/previous"))
    assert len(backups) == 1
    assert (backups[0] / ".codex-plugin/plugin.json").read_bytes() == original
