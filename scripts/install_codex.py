#!/usr/bin/env python3
"""Install bbwatch for the current user without starting a server or scan.

Run with Python 3.11+: python3 scripts/install_codex.py [--dry-run]
The managed runtime is deliberately outside Codex's disposable plugin cache.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT / "src"))

from bbwatch.platform import (
    cli_executable,
    ensure_user_path,
    is_windows,
    mcp_server_command,
    runtime_dir,
    venv_bin_dir,
    venv_python,
)

OWNER_FILE = ".bbwatch-codex-install.json"
OWNER = {"installer": "bbwatch-codex", "schema": 1}
PLUGIN_NAME = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*")
MARKET_NAME = re.compile(r"[A-Za-z0-9_-]+")
SEMVER = re.compile(
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


class InstallError(RuntimeError):
    """A preflight or installation failure that is safe to show to the user."""


@dataclass(frozen=True)
class RuntimePlan:
    repo: Path
    home: Path
    runtime: Path
    venv: Path
    cli: Path
    python: str


@dataclass(frozen=True)
class InstallPlan(RuntimePlan):
    source: Path
    plugin: Path
    marketplace: Path
    marketplace_name: str
    codex: str


def read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InstallError(f"Cannot read valid JSON from {path}: {error}") from error
    if not isinstance(value, dict):
        raise InstallError(f"Expected a JSON object in {path}")
    return value


def executable(value: str | None, default: str) -> str:
    candidate = value or shutil.which(default)
    if not candidate:
        raise InstallError(f"Cannot find {default}; supply --{default} with its executable path.")
    if not Path(candidate).expanduser().is_absolute():
        candidate = shutil.which(candidate)
    if not candidate or not Path(candidate).is_file() or (
        not is_windows() and not os.access(candidate, os.X_OK)
    ):
        raise InstallError(f"Executable is unavailable: {candidate or value}")
    # Keep a virtualenv's symlink path: resolving it would bypass that environment.
    return str(Path(candidate).expanduser().absolute())


def registration(plan: InstallPlan) -> dict:
    """Validate an existing catalog, then return a copy with only our entry changed."""
    if plan.marketplace.is_symlink():
        raise InstallError(f"Refusing symlink marketplace: {plan.marketplace}")
    if plan.marketplace.exists():
        payload = read_object(plan.marketplace)
    else:
        payload = {"name": "personal", "interface": {"displayName": "Personal"}, "plugins": []}
    name = payload.get("name")
    if not isinstance(name, str) or not MARKET_NAME.fullmatch(name):
        raise InstallError(
            "Marketplace name must contain only letters, digits, underscores or hyphens."
        )
    if "interface" in payload and not isinstance(payload["interface"], dict):
        raise InstallError("Marketplace interface must be an object.")
    entries = payload.get("plugins")
    if not isinstance(entries, list):
        raise InstallError("Marketplace plugins must be a list.")
    result = copy.deepcopy(payload)
    matched = None
    seen = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise InstallError("Each marketplace plugin must be an object.")
        entry_name = entry.get("name")
        if not isinstance(entry_name, str) or not PLUGIN_NAME.fullmatch(entry_name):
            raise InstallError("Marketplace contains an invalid plugin identifier.")
        if entry_name in seen:
            raise InstallError(f"Duplicate marketplace plugin: {entry_name}")
        seen.add(entry_name)
        if entry_name != "bbwatch":
            continue
        source = entry.get("source")
        if not isinstance(source, dict) or source.get("source") != "local":
            raise InstallError(
                "Existing bbwatch marketplace entry is unrelated to this local install."
            )
        raw_path = source.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise InstallError("Existing bbwatch entry has no local source path.")
        if (plan.home / raw_path).resolve() != plan.plugin.resolve():
            raise InstallError("Existing bbwatch marketplace entry points at an unrelated source.")
        matched = index
    entry = copy.deepcopy(entries[matched]) if matched is not None else {}
    entry.update(
        {
            "name": "bbwatch",
            "source": {"source": "local", "path": "./plugins/bbwatch"},
        }
    )
    policy = entry.setdefault("policy", {})
    if not isinstance(policy, dict):
        raise InstallError("The bbwatch marketplace policy must be an object.")
    policy.setdefault("installation", "AVAILABLE")
    policy.setdefault("authentication", "ON_INSTALL")
    if policy["installation"] != "AVAILABLE":
        raise InstallError("The existing bbwatch installation policy does not permit this install.")
    if policy["authentication"] not in {"ON_INSTALL", "ON_USE"}:
        raise InstallError("The existing bbwatch authentication policy is invalid.")
    entry.setdefault("category", "Productivity")
    if matched is None:
        result["plugins"].append(entry)
    else:
        result["plugins"][matched] = entry
    return result


def check_owned(path: Path) -> None:
    if path.is_symlink():
        raise InstallError(f"Refusing unrelated symlink at managed location: {path}")
    if path.exists():
        marker = path / OWNER_FILE
        if not path.is_dir() or not marker.is_file() or read_object(marker) != OWNER:
            raise InstallError(f"Unrelated or unowned directory exists; preserving it: {path}")


def preflight_runtime(plan: RuntimePlan) -> None:
    check_owned(plan.runtime)
    if plan.venv.is_symlink():
        raise InstallError(f"Refusing unrelated virtualenv symlink: {plan.venv}")
    if not is_windows():
        if plan.cli.is_symlink():
            if plan.cli.resolve() != cli_executable(plan.venv).resolve():
                raise InstallError(f"Unrelated CLI symlink collision: {plan.cli}")
        elif plan.cli.exists():
            raise InstallError(f"Unrelated CLI collision: {plan.cli}")


def preflight(plan: InstallPlan) -> dict:
    manifest = read_object(plan.source / ".codex-plugin" / "plugin.json")
    if manifest.get("name") != "bbwatch":
        raise InstallError("Source bundle must be named bbwatch.")
    version = manifest.get("version")
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise InstallError("Source bundle version must be semantic versioning (for example 0.1.0).")
    mcp = read_object(plan.source / ".mcp.json")
    servers = mcp.get("mcpServers")
    if not isinstance(servers, dict) or not isinstance(servers.get("bbwatch"), dict):
        raise InstallError("Source bundle must contain the bbwatch MCP server definition.")
    if not (plan.repo / "pyproject.toml").is_file():
        raise InstallError(f"Missing Python project: {plan.repo}")
    check_owned(plan.plugin)
    preflight_runtime(plan)
    return registration(plan)


def plan_runtime(
    repo: Path,
    *,
    home: Path | None = None,
    python: str | None = None,
) -> RuntimePlan:
    repo = repo.expanduser().resolve()
    home = (home or Path.home()).expanduser().resolve()
    runtime = runtime_dir(None if is_windows() else home)
    venv = runtime / "venv"
    return RuntimePlan(
        repo=repo,
        home=home,
        runtime=runtime,
        venv=venv,
        cli=cli_executable(venv) if is_windows() else home / ".local" / "bin" / "bbwatch",
        python=executable(python or sys.executable, "python"),
    )


def plan_install(
    repo: Path,
    *,
    home: Path | None = None,
    codex: str | None = None,
    python: str | None = None,
) -> InstallPlan:
    runtime_plan = plan_runtime(repo, home=home, python=python)
    plan = InstallPlan(
        **runtime_plan.__dict__,
        source=runtime_plan.repo / "plugins" / "bbwatch",
        plugin=runtime_plan.home / "plugins" / "bbwatch",
        marketplace=runtime_plan.home / ".agents" / "plugins" / "marketplace.json",
        marketplace_name="personal",
        codex=executable(codex, "codex"),
    )
    market = preflight(plan)
    return InstallPlan(**{**plan.__dict__, "marketplace_name": market["name"]})


def atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_json(path: Path, payload: dict) -> None:
    atomic_bytes(path, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode())


def _install_runtime(
    plan: RuntimePlan,
    *,
    run: Callable,
    add_path: Callable[[Path], bool] | None,
) -> str:
    def command(args: list[str], timeout: int, **kwargs):
        return run(args, check=True, timeout=timeout, **kwargs)

    try:
        command(
            [
                plan.python,
                "-c",
                (
                    "import sys; sys.exit('Python 3.11 or newer is required') "
                    "if sys.version_info < (3, 11) else None"
                ),
            ],
            30,
        )
        plan.runtime.mkdir(parents=True, exist_ok=True)
        atomic_json(plan.runtime / OWNER_FILE, OWNER)
        python = venv_python(plan.venv)
        if not python.is_file():
            command([plan.python, "-m", "venv", str(plan.venv)], 180)
        # First satisfy dependencies. Then refresh our package even if its public
        # version has not changed, without force-reinstalling those dependencies.
        pip = [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--timeout",
            "30",
            "--retries",
            "2",
        ]
        command([*pip, str(plan.repo)], 600)
        command([*pip, "--force-reinstall", "--no-deps", "--no-cache-dir", str(plan.repo)], 300)
        if is_windows():
            (ensure_user_path if add_path is None else add_path)(venv_bin_dir(plan.venv))
    except (OSError, subprocess.SubprocessError) as error:
        raise InstallError(
            "Runtime installation failed. The managed runtime is retained for retry; "
            f"plugin registration and user data were not changed. {error}"
        ) from error
    return str(python)


def install_cli_only(
    plan: RuntimePlan,
    *,
    dry_run: bool = False,
    run: Callable = subprocess.run,
    add_path: Callable[[Path], bool] | None = None,
) -> None:
    preflight_runtime(plan)
    print(f"Runtime: {plan.venv}\nCLI: {plan.cli}")
    print(f"MCP: {' '.join(mcp_server_command(plan.venv))}")
    if dry_run:
        print("Dry run: no files changed and no processes started.")
        return
    _install_runtime(plan, run=run, add_path=add_path)
    if not is_windows():
        target = cli_executable(plan.venv)
        if plan.cli.is_symlink() and plan.cli.resolve() != target.resolve():
            raise InstallError(f"Unrelated CLI symlink collision: {plan.cli}")
        if plan.cli.exists() and not plan.cli.is_symlink():
            raise InstallError(f"Unrelated CLI collision: {plan.cli}")
        if not plan.cli.exists() and not plan.cli.is_symlink():
            plan.cli.parent.mkdir(parents=True, exist_ok=True)
            plan.cli.symlink_to(target)
    if not plan.cli.is_file():
        raise InstallError(f"The installed CLI executable is missing: {plan.cli}")
    print("Installed bbwatch CLI. Open a new PowerShell to use the command.")


def install(
    plan: InstallPlan,
    *,
    dry_run: bool = False,
    run: Callable = subprocess.run,
    add_path: Callable[[Path], bool] | None = None,
) -> None:
    market = preflight(plan)
    if market["name"] != plan.marketplace_name:
        raise InstallError("Marketplace changed since planning; rerun the installer.")
    selector = f"bbwatch@{plan.marketplace_name}"
    print(f"Plugin: {plan.plugin}\nRuntime: {plan.venv}\nCLI: {plan.cli}")
    print(f"Marketplace: {plan.marketplace}\nCodex plugin: {selector}")
    if dry_run:
        print("Dry run: no files changed and no processes started.")
        return

    def command(args: list[str], timeout: int, **kwargs):
        return run(args, check=True, timeout=timeout, **kwargs)

    _install_runtime(plan, run=run, add_path=add_path)

    # Detect collisions or catalog edits that occurred while pip was running.
    market = preflight(plan)
    if market["name"] != plan.marketplace_name:
        raise InstallError("Marketplace changed during runtime installation; rerun the installer.")
    previous_market = plan.marketplace.read_bytes() if plan.marketplace.exists() else None
    previous_cli = plan.cli.is_symlink()
    plan.plugin.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".bbwatch-stage-", dir=plan.plugin.parent))
    candidate = stage / "new"
    backup = stage / "previous"
    installed = False
    market_written = False
    cli_created = False
    cleanup_stage = True
    try:
        shutil.copytree(plan.source, candidate)
        atomic_json(candidate / OWNER_FILE, OWNER)
        manifest_path = candidate / ".codex-plugin" / "plugin.json"
        manifest = read_object(manifest_path)
        cachebuster = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        manifest["version"] = f"{manifest['version'].split('+', 1)[0]}+codex.{cachebuster}"
        atomic_json(manifest_path, manifest)
        mcp_path = candidate / ".mcp.json"
        mcp = read_object(mcp_path)
        command_line = mcp_server_command(plan.venv)
        mcp["mcpServers"]["bbwatch"].update(
            {
                "command": command_line[0],
                "args": command_line[1:],
            }
        )
        atomic_json(mcp_path, mcp)
        if plan.plugin.exists():
            plan.plugin.rename(backup)
        candidate.rename(plan.plugin)
        installed = True
        atomic_json(plan.marketplace, market)
        market_written = True
        if not is_windows() and not previous_cli:
            plan.cli.parent.mkdir(parents=True, exist_ok=True)
            plan.cli.symlink_to(cli_executable(plan.venv))
            cli_created = True
        command([plan.codex, "plugin", "add", selector], 180)
    except (OSError, subprocess.SubprocessError, InstallError) as error:
        cleanup_stage = False
        try:
            if cli_created:
                plan.cli.unlink()
            if market_written:
                if previous_market is None:
                    plan.marketplace.unlink()
                else:
                    atomic_bytes(plan.marketplace, previous_market)
            if installed:
                shutil.rmtree(plan.plugin)
            if backup.exists():
                backup.rename(plan.plugin)
        except OSError as recovery_error:
            raise InstallError(
                f"Installation and rollback failed; recovery files were preserved at {stage}. "
                f"{recovery_error}"
            ) from error
        cleanup_stage = True
        raise InstallError(
            "Plugin installation failed; prior bundle and marketplace were restored. "
            "The managed Python runtime may already be updated; rerun to retry. "
            f"{error}"
        ) from error
    finally:
        if cleanup_stage:
            shutil.rmtree(stage)
    print("Installed. Start a new Codex task or CLI session to load bbwatch.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", help="Python 3.11+ executable for the managed environment")
    parser.add_argument("--codex", help="Codex executable (defaults to the command on PATH)")
    parser.add_argument(
        "--cli-only",
        action="store_true",
        help="Install the bbwatch CLI and MCP runtime without requiring Codex",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show planned paths without changes")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):  # noqa: UP036 - this script also runs before package installation
        parser.error("Run this installer using Python 3.11 or newer.")
    try:
        repo = Path(__file__).resolve().parents[1]
        if args.cli_only:
            install_cli_only(
                plan_runtime(repo, python=args.python),
                dry_run=args.dry_run,
            )
        else:
            plan = plan_install(repo, codex=args.codex, python=args.python)
            install(plan, dry_run=args.dry_run)
    except (InstallError, OSError) as error:
        print(f"Installation stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
