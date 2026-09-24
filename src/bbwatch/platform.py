"""Small platform-specific helpers shared by the CLI and installers."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


def is_windows() -> bool:
    return os.name == "nt"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def venv_bin_dir(venv: Path | str) -> Path:
    return Path(venv) / ("Scripts" if is_windows() else "bin")


def venv_executable(venv: Path | str, name: str) -> Path:
    suffix = ".exe" if is_windows() else ""
    return venv_bin_dir(venv) / f"{name}{suffix}"


def venv_python(venv: Path | str) -> Path:
    return venv_executable(venv, "python")


def venv_pip(venv: Path | str) -> Path:
    return venv_executable(venv, "pip")


def cli_executable(venv: Path | str) -> Path:
    return venv_executable(venv, "bbwatch")


def user_data_dir() -> Path:
    return Path.home() / ".bbwatch"


def runtime_dir(home: Path | str | None = None) -> Path:
    if is_windows():
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data).expanduser() if local_app_data else Path.home() / "AppData" / "Local"
        return base / "bbwatch" / "runtime"
    base = Path(home).expanduser() if home is not None else Path.home()
    return base / ".local" / "share" / "bbwatch-codex"


def default_download_dir() -> Path:
    return Path.home() / "Downloads" / "bbwatch"


def mcp_server_command(venv: Path | str) -> list[str]:
    return [str(venv_python(venv)), "-m", "bbwatch.mcp_server"]


def add_path_entry(current: str, directory: Path | str) -> str:
    """Return PATH with one absolute, case-aware entry added at most once."""
    separator = ";" if is_windows() else os.pathsep
    entry = str(Path(directory).expanduser().absolute())
    def path_key(item: str) -> str:
        value = os.path.normpath(os.path.expandvars(item))
        return value.casefold() if is_windows() else value

    normalized = path_key(entry)
    entries = []
    seen = set()
    for item in current.split(separator):
        if not item:
            continue
        key = path_key(item)
        if key not in seen:
            entries.append(item)
            seen.add(key)
    if normalized not in seen:
        entries.append(entry)
    return separator.join(entries)


def ensure_user_path(directory: Path | str) -> bool:
    """Add a directory to the Windows user's PATH without using ``setx``."""
    if not is_windows():
        return False

    import winreg

    key_path = r"Environment"
    access = winreg.KEY_READ | winreg.KEY_SET_VALUE
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, access)
    except FileNotFoundError:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
    with key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", getattr(winreg, "REG_EXPAND_SZ", winreg.REG_SZ)
        updated = add_path_entry(str(current), directory)
        if updated == current:
            return False
        winreg.SetValueEx(key, "Path", 0, kind, updated)

    _broadcast_environment_change()
    return True


def _broadcast_environment_change() -> None:
    """Notify already-running GUI processes that the user's environment changed."""
    try:
        ctypes.windll.user32.SendMessageTimeoutW(  # type: ignore[attr-defined]
            0xFFFF,
            0x001A,
            0,
            "Environment",
            0x0002,
            1000,
            None,
        )
    except (AttributeError, OSError):
        pass
