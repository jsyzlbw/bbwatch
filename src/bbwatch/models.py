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
    """成绩册栏目（带截止日期的作业/quiz）。汇总列在 bbclient 层已过滤。"""

    id: str
    name: str
    due_utc: str  # grading.due, UTC ISO8601, 如 2026-06-30T15:59:00.000Z
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
