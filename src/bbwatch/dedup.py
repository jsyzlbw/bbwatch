"""dedup 键的唯一来源（附录 C.1 M4）。禁止在别处构造 dedup 键。

entity_key 形如:
  col:{cid}:{colid}     新作业 / 改期
  grade:{cid}:{colid}   出分
  ann:{cid}:{annid}     新公告

dedup_key = "{event_type}|{entity_key}[|{variant}]"
variant 用于"同一实体的合法多次提醒":
  deadline_changed -> variant = 新的 due_utc（改两次期 = 两次提醒）
  其它默认无 variant（一次性）
"""
from __future__ import annotations


def make_dedup_key(event_type: str, entity_key: str, variant: str | None = None) -> str:
    base = f"{event_type}|{entity_key}"
    return f"{base}|{variant}" if variant else base
