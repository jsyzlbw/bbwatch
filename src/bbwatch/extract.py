"""轻量关键词识别(纯函数，无 LLM 依赖)：往年卷文件、重要公告。"""
from __future__ import annotations

import re

_FILE_EXAM = re.compile(
    r"(past[\s_-]?paper|exam|midterm|final|真题|往年|历年|试卷|卷子|期中|期末)", re.IGNORECASE
)
_ANN_IMPORTANT = re.compile(
    r"(exam|midterm|final|quiz|make[\s_-]?up|补课|reschedul|改期|延期|座位|seat\s?map|"
    r"考试|期中|期末|deadline|due)",
    re.IGNORECASE,
)


def is_exam_file(name: str) -> bool:
    return bool(name and _FILE_EXAM.search(name))


def announcement_is_important(text: str) -> bool:
    return bool(text and _ANN_IMPORTANT.search(text))
