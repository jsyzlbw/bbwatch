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
