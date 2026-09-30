from pathlib import Path

from bbwatch import platform


def test_windows_venv_and_mcp_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "is_windows", lambda: True)
    venv = tmp_path / "runtime" / "venv"
    assert platform.venv_bin_dir(venv) == venv / "Scripts"
    assert platform.venv_python(venv) == venv / "Scripts" / "python.exe"
    assert platform.venv_executable(venv, "bbwatch") == venv / "Scripts" / "bbwatch.exe"
    assert platform.mcp_server_command(venv) == [
        str(venv / "Scripts" / "python.exe"),
        "-m",
        "bbwatch.mcp_server",
    ]


def test_windows_runtime_and_download_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "is_windows", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local AppData"))
    assert platform.runtime_dir() == tmp_path / "Local AppData" / "bbwatch" / "runtime"
    assert platform.default_download_dir() == Path.home() / "Downloads" / "bbwatch"


def test_user_path_entry_is_case_insensitive_and_deduplicated(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "is_windows", lambda: True)
    scripts = (tmp_path / "Scripts").absolute()
    current = f"{scripts};{str(scripts).lower()}"
    assert platform.add_path_entry(current, scripts).split(";") == [str(scripts)]
    assert platform.add_path_entry(str(scripts), scripts) == str(scripts)


def test_posix_venv_and_runtime_paths_can_be_selected(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "is_windows", lambda: False)
    venv = tmp_path / "venv"
    assert platform.venv_python(venv) == venv / "bin" / "python"
    assert platform.runtime_dir(tmp_path) == tmp_path / ".local" / "share" / "bbwatch-codex"
