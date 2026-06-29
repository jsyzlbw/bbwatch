from __future__ import annotations

import os
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
    def log_path(self) -> Path:
        return self.root / "bbwatch.log"

    def ensure_dirs(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
