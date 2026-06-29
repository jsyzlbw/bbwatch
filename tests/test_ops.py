import keyring
import pytest
from keyring.backend import KeyringBackend

from bbwatch.config import AppPaths, Config, load_config, make_course_filter
from bbwatch.diff import diff_columns
from bbwatch.models import Column, ColumnStatus, Course
from bbwatch.ops import run_doctor, run_uninstall
from bbwatch.store import Store

NOW = "2026-06-28T00:00:00.000Z"


class MemKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self._d = {}

    def get_password(self, s, u):
        return self._d.get((s, u))

    def set_password(self, s, u, p):
        self._d[(s, u)] = p

    def delete_password(self, s, u):
        self._d.pop((s, u), None)


def _course(code):
    return Course(f"_{code}", code, code, "_t", "Student", "Yes", "Classic")


# ---------- config ----------
def test_load_config_defaults_when_missing(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.archive_overdue_weeks == 4 and cfg.dashboard_port == 8765 and cfg.include == []


def test_load_config_parses(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[scan]\ninclude=["MAT"]\nexclude=["PED"]\narchive_overdue_weeks=2\n'
        '[download]\ndest="/tmp/x"\n[dashboard]\nport=9000\n'
    )
    cfg = load_config(p)
    assert cfg.include == ["MAT"] and cfg.exclude == ["PED"]
    assert cfg.archive_overdue_weeks == 2 and cfg.download_dest == "/tmp/x" and cfg.dashboard_port == 9000


def test_course_filter_include_exclude():
    cfg = Config(include=["MAT"], exclude=["PED"], archive_overdue_weeks=4, download_dest="x", dashboard_port=1)
    f = make_course_filter(cfg)
    assert f(_course("MAT3007:Opt")) is True
    assert f(_course("CSC3001")) is False  # 不在白名单
    cfg2 = Config(include=[], exclude=["PED"], archive_overdue_weeks=4, download_dest="x", dashboard_port=1)
    f2 = make_course_filter(cfg2)
    assert f2(_course("PED1201:Badminton")) is False
    assert f2(_course("MAT3007")) is True


# ---------- archive ----------
def test_archive_overdue_hides_old_undone():
    s = Store(":memory:")
    cols = [Column("_old", "Old", "2026-01-30T15:59:00.000Z"),
            Column("_new", "New", "2026-06-30T15:59:00.000Z")]
    sts = {"_old": ColumnStatus("None"), "_new": ColumnStatus("None")}
    for ch in diff_columns({}, cols, sts, cid="_c", scan_id=1, suppress=False):
        s.apply_change(ch, NOW)
    assert len(s.actionable_tasks()) == 2
    assert s.archive_overdue(NOW, weeks=4) == 1  # Old 逾期 >4 周
    assert [t["name"] for t in s.actionable_tasks()] == ["New"]


# ---------- doctor ----------
def test_doctor_reports_structure(tmp_path):
    keyring.set_keyring(MemKeyring())  # 干净钥匙串 → 凭据未配置
    out = run_doctor(AppPaths(root=tmp_path / ".bbwatch"))
    assert "钥匙串凭据" in out and "数据库可用" in out and "清单端口" in out


# ---------- uninstall ----------
def test_uninstall_clears_creds_and_session(tmp_path):
    keyring.set_keyring(MemKeyring())
    from bbwatch.secrets import load_credentials, store_credentials

    store_credentials("u@link.cuhk.edu.cn", "pw")
    paths = AppPaths(root=tmp_path / ".bbwatch")
    paths.ensure_dirs()
    paths.session_path.write_text("[]")
    out = run_uninstall(paths, purge_db=False)
    assert "凭据" in out
    assert not paths.session_path.exists()
    with pytest.raises(Exception):
        load_credentials()
