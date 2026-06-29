"""纯函数 diff：把"已知集合 + 本轮拉取"算成 Change（快照 + 待发事件）。

不变量（附录 C）：
- 全量 diff：与 known（含 archived）比对，稳定 id 即身份；改名只更新 payload 不报新。
- suppress=True（冷启动/基线未建）：只产快照、不产事件。
- 改期：column due_utc 变化 → deadline_changed（dedup variant = 新 due）。
- 出分：grade 由非 Graded 翻为 Graded（或 score 由空变非空）→ graded。
"""
from __future__ import annotations

import json

from .dedup import make_dedup_key
from .models import Announcement, Column, ColumnStatus, Content
from .store import Change

_FOLDER = "resource/x-bb-folder"


def col_entity_key(cid: str, colid: str) -> str:
    return f"col:{cid}:{colid}"


def ann_entity_key(cid: str, aid: str) -> str:
    return f"ann:{cid}:{aid}"


def _ev(event_type: str, entity_key: str, title: str, detail: str, variant: str | None = None) -> dict:
    return {
        "dedup_key": make_dedup_key(event_type, entity_key, variant),
        "entity_key": entity_key,
        "event_type": event_type,
        "title": title,
        "detail": detail,
    }


def diff_columns(
    known: dict,
    columns: list[Column],
    statuses: dict[str, ColumnStatus],
    *,
    cid: str,
    scan_id: int,
    suppress: bool,
) -> list[Change]:
    changes: list[Change] = []
    for col in columns:
        ek = col_entity_key(cid, col.id)
        st = statuses.get(col.id)
        grade_status = st.status if st else "None"
        grade_score = st.score if st else None
        seen = {
            "entity_key": ek,
            "kind": "column",
            "course_id": cid,
            "bb_id": col.id,
            "due_utc": col.due_utc,
            "grade_status": grade_status,
            "grade_score": grade_score,
            "payload": {
                "name": col.name,
                "content_id": col.content_id,
                "score_possible": col.score_possible,
            },
            "scan_id": scan_id,
        }
        events: list[dict] = []
        prev = known.get(ek)
        if prev is None:
            if not suppress:
                events.append(_ev("new_assignment", ek, f"新作业: {col.name}", f"截止(UTC) {col.due_utc}"))
        elif not suppress:
            if prev["due_utc"] != col.due_utc:
                events.append(
                    _ev("deadline_changed", ek, f"作业改期: {col.name}",
                        f"新截止(UTC) {col.due_utc}", variant=col.due_utc)
                )
            prev_graded = (prev["grade_status"] == "Graded") or (prev["grade_score"] is not None)
            now_graded = (grade_status == "Graded") or (grade_score is not None)
            if now_graded and not prev_graded:
                detail = f"分数 {grade_score}" if grade_score is not None else "已批改"
                events.append(_ev("graded", ek, f"已出分: {col.name}", detail))
        changes.append(Change(seen=seen, events=events))
    return changes


def diff_announcements(
    known: dict,
    announcements: list[Announcement],
    *,
    cid: str,
    scan_id: int,
    suppress: bool,
) -> list[Change]:
    changes: list[Change] = []
    for a in announcements:
        ek = ann_entity_key(cid, a.id)
        seen = {
            "entity_key": ek,
            "kind": "announcement",
            "course_id": cid,
            "bb_id": a.id,
            "due_utc": None,
            "grade_status": None,
            "grade_score": None,
            "payload": {"title": a.title, "created": a.created, "body": (a.body or "")[:500]},
            "scan_id": scan_id,
        }
        events: list[dict] = []
        if a.id and ek not in known and not suppress:
            events.append(_ev("new_announcement", ek, f"新公告: {a.title}", (a.body or "")[:140]))
        changes.append(Change(seen=seen, events=events))
    return changes


def diff_contents(
    known: dict,
    contents: list[Content],
    *,
    cid: str,
    scan_id: int,
    suppress: bool,
) -> list[Change]:
    """新课件检测：非文件夹内容项 新 id→new_material；已知项 modified 变→material_updated。
    文件夹也记入 seen(避免重复发现)，但不产生通知。"""
    changes: list[Change] = []
    for c in contents:
        ek = f"content:{cid}:{c.id}"
        seen = {
            "entity_key": ek,
            "kind": "content",
            "course_id": cid,
            "bb_id": c.id,
            "due_utc": None,
            "grade_status": None,
            "grade_score": None,
            "payload": {"title": c.title, "handler": c.handler, "modified": c.modified},
            "scan_id": scan_id,
        }
        events: list[dict] = []
        is_material = c.handler != _FOLDER
        prev = known.get(ek)
        if not suppress and is_material:
            if prev is None:
                events.append(_ev("new_material", ek, f"新课件: {c.title}", c.handler or ""))
            else:
                prev_mod = json.loads(prev["payload_json"]).get("modified")
                if c.modified and prev_mod != c.modified:
                    events.append(
                        _ev("material_updated", ek, f"课件更新: {c.title}",
                            c.modified or "", variant=c.modified)
                    )
        changes.append(Change(seen=seen, events=events))
    return changes
