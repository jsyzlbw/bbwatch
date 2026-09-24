import os
import stat
from pathlib import Path

from bbwatch.config import AppPaths, default_root
from bbwatch.platform import default_download_dir


def test_apppaths_uses_env_root(tmp_path):
    p = AppPaths(root=tmp_path / ".bbwatch")
    assert p.db_path.name == "state.db"
    assert p.session_path.name == "session"
    assert str(p.root) in str(p.db_path)


def test_ensure_dirs_sets_0700(tmp_path):
    p = AppPaths(root=tmp_path / ".bbwatch")
    p.ensure_dirs()
    if os.name != "nt":
        mode = stat.S_IMODE(os.stat(p.root).st_mode)
        assert mode == 0o700


def test_default_download_dir_is_home_relative():
    destination = default_download_dir()
    assert destination == Path.home() / "Downloads" / "bbwatch"


def test_default_root_expands_user_home(monkeypatch):
    monkeypatch.setenv("BBWATCH_HOME", "~/bbwatch-config")
    assert default_root() == Path.home() / "bbwatch-config"
