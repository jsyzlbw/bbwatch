from __future__ import annotations

import os
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


def default_root() -> Path:
    return Path(os.environ.get("BBWATCH_HOME", str(Path.home() / ".bbwatch")))


@dataclass
class AppPaths:
    root: Path = field(default_factory=default_root)

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    @property
    def db_path(self) -> Path:
        return self.root / "state.db"

    @property
    def session_path(self) -> Path:
        return self.root / "session"

    @property
    def config_path(self) -> Path:
        return self.root / "config.toml"

    @property
    def log_path(self) -> Path:
        return self.root / "bbwatch.log"

    def ensure_dirs(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)


@dataclass
class Config:
    include: list[str]  # 课程代码白名单子串(空=全部在读)
    exclude: list[str]  # 课程代码黑名单子串(如 PED 体育)
    archive_overdue_weeks: int  # 逾期超过多少周的未完成作业自动归档
    download_dest: str
    dashboard_port: int


def load_config(path) -> Config:
    data: dict = {}
    p = Path(path)
    if p.exists():
        with open(p, "rb") as f:
            data = tomllib.load(f)
    scan = data.get("scan", {})
    dl = data.get("download", {})
    dash = data.get("dashboard", {})
    return Config(
        include=list(scan.get("include", [])),
        exclude=list(scan.get("exclude", [])),
        archive_overdue_weeks=int(scan.get("archive_overdue_weeks", 4)),
        download_dest=dl.get("dest", "~/Downloads/bbwatch"),
        dashboard_port=int(dash.get("port", 8765)),
    )


def make_course_filter(config: Config) -> Callable:
    inc = [s.lower() for s in config.include]
    exc = [s.lower() for s in config.exclude]

    def _filter(course) -> bool:
        cid = (course.course_id or "").lower()
        if inc and not any(s in cid for s in inc):
            return False
        if any(s in cid for s in exc):
            return False
        return True

    return _filter


DEFAULT_CONFIG_TOML = """# bbwatch 配置

[scan]
# 只扫这些课程代码子串(白名单)，留空 = 全部在读
include = []
# 排除这些课程代码子串(黑名单)，如体育课
exclude = ["PED"]
# 逾期超过多少周的未完成作业自动归档隐藏
archive_overdue_weeks = 4

[download]
# 课件下载目录
dest = "~/Downloads/bbwatch"

[dashboard]
port = 8765
"""
