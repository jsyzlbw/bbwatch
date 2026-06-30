"""bbwatch MCP 服务器（官方 FastMCP 实现，保证协议合规）。
把能力暴露给 Claude Code 对话式调用：用户说一句话 → Claude 调对应工具。
工具逻辑复用 cli 中已测过的 run_* 函数；store 走 AppPaths(尊重 BBWATCH_HOME)。
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("bbwatch")


def _store():
    from .config import AppPaths
    from .store import Store

    p = AppPaths()
    p.ensure_dirs()
    return Store(p.db_path)


def _now() -> str:
    from .store import now_utc

    return now_utc()


def _authed():
    from .cli import _authed as authed

    return authed()


@mcp.tool()
def list_tasks() -> str:
    """列出未完成/可跟踪的作业(带编号与完成状态 ○/✓，按截止排序)。
    用户问"我有什么作业/ddl/待办"时调用。"""
    from .cli import format_tasks

    return format_tasks(_store().actionable_tasks(), _now())


@mcp.tool()
def list_pending() -> str:
    """列出已提交但未出分(待批改)的作业。用户问"哪些作业交了还没出分 / 待批改 / 等出分"时调用。"""
    from .cli import format_pending

    return format_pending(_store().submitted_ungraded())


@mcp.tool()
def mark_task_done(n: int, done: bool) -> str:
    """把 list_tasks 中第 n 项标记为完成(done=true)或未完成(done=false)。"""
    from .cli import run_mark_done

    return run_mark_done(_store(), n, done, _now())


@mcp.tool()
def scan_now() -> str:
    """立即扫描 BB，检测新作业/改期/公告/出分/新课件并推送通知，返回摘要。
    用户说"扫一下/有没有新东西/出分了吗"时调用。"""
    from .cli import run_scan
    from .config import load_config, make_course_filter
    from .notifier import MacNotifier

    client, store, paths = _authed()
    cfg = load_config(paths.config_path)
    return run_scan(
        client, store, MacNotifier(), now=_now(),
        course_filter=make_course_filter(cfg), archive_weeks=cfg.archive_overdue_weeks,
    )


@mcp.tool()
def list_courses() -> str:
    """列出本学期在读课程(带编号)。"""
    from .cli import format_courses

    client, _store_unused, _paths = _authed()
    me = client.get_me()
    return format_courses([c for c in client.list_courses(me.id) if c.is_active])


@mcp.tool()
def download_course(ref: str, dest: str = "") -> str:
    """增量镜像某课程的全部课件到本地。
    ref=课程编号(见 list_courses)或课程代码子串(如 MAT3007)；dest 可选下载目录。"""
    from pathlib import Path

    from .cli import pick_course, run_download
    from .config import load_config

    client, store, paths = _authed()
    me = client.get_me()
    active = [c for c in client.list_courses(me.id) if c.is_active]
    course = pick_course(active, ref)
    cfg = load_config(paths.config_path)
    d = Path(dest) if dest else Path(cfg.download_dest).expanduser()
    return run_download(client, store, course, d, now=_now())


def main() -> None:
    mcp.run()  # 默认 stdio 传输


if __name__ == "__main__":
    main()
