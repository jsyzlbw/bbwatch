from __future__ import annotations

import logging
import re
from pathlib import Path

_PATTERNS = [
    re.compile(r"(?i)(password=)[^&\s]+"),
    re.compile(r"(?i)(code=)[^&\s]+"),
    re.compile(r"(?i)(username=)[^&\s]+"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"),
    re.compile(r"(?i)(cookie:\s*)[^\n]+"),
    re.compile(r"(JSESSIONID=)[^;\s]+"),
    re.compile(r"(s_session_id=)[^;\s]+"),
    re.compile(r"\b1\d{8}\b"),  # 学号
]


def redact(text: str) -> str:
    out = text
    for p in _PATTERNS:
        if p.groups:
            out = p.sub(lambda m: m.group(1) + "***", out)
        else:
            out = p.sub("***", out)
    return out


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def setup_logging(log_path: Path, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("bbwatch")
    logger.setLevel(level)
    if not logger.handlers:
        log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        fh.addFilter(_RedactFilter())
        logger.addHandler(fh)
    return logger
