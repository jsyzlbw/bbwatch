#!/usr/bin/env python3
"""SessionStart 钩子：开 Claude Code 时即时打印待办摘要(注入会话)，并在后台静默刷新扫描。
绝不阻塞会话、绝不抛错。"""
import os
import subprocess
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if os.path.isdir(_SRC):
    sys.path.insert(0, _SRC)  # 未安装时回退到源码

try:
    from bbwatch.config import AppPaths
    from bbwatch.store import Store, now_utc
    from bbwatch.summary import build_session_summary

    paths = AppPaths()
    if paths.db_path.exists():
        store = Store(paths.db_path)
        print(build_session_summary(store, now_utc()))
        store.close()
    else:
        print("📚 bbwatch：尚未初始化。运行 bbwatch setup 录入账号，再 bbwatch scan 开始监控。")

    # 后台静默刷新(fire-and-forget)，下次开会话即最新；失败无声
    subprocess.Popen(
        [sys.executable, "-m", "bbwatch.cli", "scan"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
except Exception as e:  # noqa: BLE001
    print(f"bbwatch 摘要暂不可用：{type(e).__name__}")
