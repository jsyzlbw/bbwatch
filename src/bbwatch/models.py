from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Me:
    id: str
    user_name: str
    given_name: str | None = None


@dataclass(frozen=True)
class Term:
    id: str
    name: str | None


@dataclass(frozen=True)
class Course:
    id: str  # 内部 id, 如 _17236_1
    course_id: str  # 人类可读, 如 MAT3007:Optimization_L01
    name: str
    term_id: str | None
    role: str  # courseRoleId
    availability: str  # Yes / No / Term
    ultra_status: str

    @property
    def is_active(self) -> bool:
        return self.role == "Student" and self.availability in ("Yes", "Term")


@dataclass(frozen=True)
class Column:
    """成绩册栏目（作业/quiz，可无截止日期）。汇总列在 bbclient 层已过滤。"""

    id: str
    name: str
    due_utc: str | None  # grading.due, UTC ISO8601；未设置截止日期时为 None
    content_id: str | None = None
    score_possible: float | None = None


@dataclass(frozen=True)
class ColumnStatus:
    status: str  # None / NeedsGrading / Graded
    score: float | None = None

    @property
    def is_done(self) -> bool:
        return self.status in ("NeedsGrading", "Graded") or self.score is not None

    @property
    def is_graded(self) -> bool:
        return self.status == "Graded" or self.score is not None


@dataclass(frozen=True)
class Announcement:
    id: str
    title: str
    created: str  # 发布时间 UTC
    body: str = ""


@dataclass(frozen=True)
class Content:
    id: str
    title: str
    handler: str | None  # contentHandler.id: x-bb-folder/-document/-file/-assignment
    has_children: bool = False
    created: str | None = None
    modified: str | None = None


@dataclass(frozen=True)
class Attachment:
    id: str
    file_name: str
    mime_type: str | None = None
