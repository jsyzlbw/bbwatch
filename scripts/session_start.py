#!/usr/bin/env python3
"""SessionStart 钩子：开 Claude Code 时即时打印待办摘要(注入会话)。
仅在距上次扫描较久(>2h)时才后台静默刷新一次，避免每次开会话都触发完整扫描(慢且费)。
绝不阻塞会话、绝不抛错。"""
import os
import subprocess
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if os.path.isdir(_SRC):
    sys.path.insert(0, _SRC)  # 未安装时回退到源码

_SCAN_THROTTLE_S = 2 * 3600  # 后台刷新最小间隔

try:
    from bbwatch.config import AppPaths
    from bbwatch.store import Store, now_utc, parse_utc

    paths = AppPaths()
    last_scan = None
    if paths.db_path.exists():
        from bbwatch.summary import build_session_summary

        store = Store(paths.db_path)
        print(build_session_summary(store, now_utc()))
        last_scan = store.last_scan_time()
        store.close()
    else:
        print("📚 bbwatch：尚未初始化。运行 bbwatch setup 录入账号，再 bbwatch scan 开始监控。")

    # 节流：仅当从未扫过、或距上次扫描超过阈值时，才后台刷新(fire-and-forget)
    should_scan = True
    if last_scan:
        from datetime import datetime, timezone

        gap = (datetime.now(timezone.utc) - parse_utc(last_scan)).total_seconds()
        should_scan = gap > _SCAN_THROTTLE_S
    if should_scan:
        subprocess.Popen(
            [sys.executable, "-m", "bbwatch.cli", "scan"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
except Exception as e:  # noqa: BLE001
    print(f"bbwatch 摘要暂不可用：{type(e).__name__}")
