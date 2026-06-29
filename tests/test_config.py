import os
import stat

from bbwatch.config import AppPaths


def test_apppaths_uses_env_root(tmp_path):
    p = AppPaths(root=tmp_path / ".bbwatch")
    assert p.db_path.name == "state.db"
    assert p.session_path.name == "session"
    assert str(p.root) in str(p.db_path)


def test_ensure_dirs_sets_0700(tmp_path):
    p = AppPaths(root=tmp_path / ".bbwatch")
    p.ensure_dirs()
    mode = stat.S_IMODE(os.stat(p.root).st_mode)
    assert mode == 0o700
