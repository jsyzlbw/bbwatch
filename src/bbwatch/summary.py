"""开工待办摘要：供 SessionStart 钩子注入会话(additionalContext)，也用于清单页横幅。
纯函数，便于测试。"""
from __future__ import annotations

from datetime import timedelta

from .store import parse_utc


def build_session_summary(store, now: str, *, top_n: int = 5, stale_hours: int = 12) -> str:
    tasks = store.outstanding_tasks()  # 未完成，按 due 升序
    now_dt = parse_utc(now)
    lines: list[str] = []

    if not tasks:
        lines.append("📚 bbwatch：当前没有未完成作业 ✓")
    else:
        lines.append(f"📚 bbwatch：你有 {len(tasks)} 项未完成作业，最近截止：")
        for t in tasks[:top_n]:
            due = parse_utc(t["due_utc"])
            local = due + timedelta(hours=8)
            dh = (due - now_dt).total_seconds() / 3600
            if dh < 0:
                tag = "已逾期"
            elif dh <= 48:
                tag = f"剩{int(dh)}h"
            else:
                tag = "充裕"
            lines.append(
                f"  • {t['name']}（{t['course_id']}）{local.strftime('%m-%d %H:%M')} [{tag}]"
            )

    last = store.last_scan_time()
    if last is None:
        lines.append("（尚未扫描过，运行 bbwatch scan 开始监控）")
    else:
        gap_h = (now_dt - parse_utc(last)).total_seconds() / 3600
        soon = [
            t for t in tasks
            if 0 <= (parse_utc(t["due_utc"]) - now_dt).total_seconds() / 3600 <= 72
        ]
        if gap_h > stale_hours and soon:
            lines.append(
                f"⚠ 距上次扫描已约 {int(gap_h)} 小时，且有临近截止——建议 bbwatch scan 刷新。"
            )
    return "\n".join(lines)
