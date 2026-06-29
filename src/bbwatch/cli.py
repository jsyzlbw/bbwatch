from __future__ import annotations

import argparse
import getpass
import sys
from datetime import timedelta

from .auth import login as adfs_login
from .bbclient import BbClient
from .config import AppPaths
from .notifier import MacNotifier, deliver_pending
from .scanner import scan
from .secrets import Credentials, load_credentials, store_credentials
from .store import Store, now_utc, parse_utc
from .transport import CurlCffiTransport, Transport


def run_whoami(transport: Transport, creds: Credentials, login_fn=adfs_login) -> str:
    login_fn(transport, creds)
    client = BbClient(transport)
    me = client.get_me()
    courses = client.list_courses(me.id)
    active = [c for c in courses if c.is_active]
    name = me.given_name or me.user_name
    return (
        f"已登录：{name}（uid={me.id}）\n"
        f"课程：共 {len(courses)} 门，在读 {len(active)} 门"
    )


def run_scan(client, store, notifier, *, now: str) -> str:
    """登录后的扫描装配（client 已就绪，便于测试注入）。"""
    me = client.get_me()
    result = scan(client, store, me.id, now=now)
    sent = deliver_pending(store, notifier, now)
    outstanding = store.outstanding_tasks()
    lines = [
        f"扫描完成：{result.courses_scanned} 门在读课，新事件 {result.new_events}，已推送 {sent}。"
    ]
    if result.failures:
        lines.append(f"⚠ 部分维度失败 {len(result.failures)} 处：{'; '.join(result.failures[:5])}")
    lines.append(f"未完成作业：{len(outstanding)} 项（bbwatch tasks 查看）")
    return "\n".join(lines)


def format_tasks(tasks: list[dict], now: str) -> str:
    """带编号 + ○/✓ 的可操作作业清单。编号即 done/undone 的引用。"""
    if not tasks:
        return "没有需要跟踪的作业 🎉"
    now_dt = parse_utc(now)
    lines = []
    for i, t in enumerate(tasks, 1):
        mark = "✓" if t.get("done") else "○"
        due = parse_utc(t["due_utc"])
        local = due + timedelta(hours=8)
        if t.get("done"):
            tag = ""
        else:
            delta_h = (due - now_dt).total_seconds() / 3600
            tag = "[逾期] " if delta_h < 0 else ("[紧急] " if delta_h <= 24 else "")
        lines.append(
            f"[{i}] {mark} {tag}{local.strftime('%m-%d %H:%M')}  {t['name']}  ({t['course_id']})"
        )
    return "\n".join(lines)


def run_mark_done(store, n: int, done: bool, now: str) -> str:
    """把 `bbwatch tasks` 列表中第 n 项(1 起)标记为 完成/未完成。"""
    tasks = store.actionable_tasks()
    if n < 1 or n > len(tasks):
        raise ValueError(f"任务编号 {n} 超出范围（当前共 {len(tasks)} 项），先运行 bbwatch tasks 查看")
    t = tasks[n - 1]
    store.mark_manual_done(t["entity_key"], done, now)
    state = "已完成 ✓" if done else "未完成 ○"
    return f"已将 [{n}] {t['name']}（{t['course_id']}）标记为{state}"


def cmd_setup(_args) -> int:
    username = input("学校账号(形如 学号@link.cuhk.edu.cn): ").strip()
    password = getpass.getpass("密码（输入不回显）: ")
    store_credentials(username, password)
    print("已存入 macOS 钥匙串。可运行 bbwatch whoami 验证。")
    return 0


def cmd_whoami(_args) -> int:
    creds = load_credentials()
    print(run_whoami(CurlCffiTransport(), creds))
    return 0


def cmd_scan(_args) -> int:
    paths = AppPaths()
    paths.ensure_dirs()
    creds = load_credentials()
    transport = CurlCffiTransport()
    adfs_login(transport, creds)
    client = BbClient(transport)
    store = Store(paths.db_path)
    print(run_scan(client, store, MacNotifier(), now=now_utc()))
    return 0


def cmd_tasks(_args) -> int:
    paths = AppPaths()
    paths.ensure_dirs()
    store = Store(paths.db_path)
    print(format_tasks(store.actionable_tasks(), now_utc()))
    return 0


def cmd_done(args) -> int:
    paths = AppPaths()
    paths.ensure_dirs()
    store = Store(paths.db_path)
    print(run_mark_done(store, args.n, done=True, now=now_utc()))
    return 0


def cmd_undone(args) -> int:
    paths = AppPaths()
    paths.ensure_dirs()
    store = Store(paths.db_path)
    print(run_mark_done(store, args.n, done=False, now=now_utc()))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bbwatch")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="录入并保存学校账号密码到钥匙串").set_defaults(fn=cmd_setup)
    sub.add_parser("whoami", help="登录并打印身份与课程数").set_defaults(fn=cmd_whoami)
    sub.add_parser("scan", help="扫描 BB，检测新作业/改期/公告/出分并通知").set_defaults(fn=cmd_scan)
    sub.add_parser("tasks", help="列出可跟踪作业(编号 + ○/✓)").set_defaults(fn=cmd_tasks)
    p_done = sub.add_parser("done", help="把 tasks 列表第 N 项标记为已完成")
    p_done.add_argument("n", type=int, help="bbwatch tasks 中的编号")
    p_done.set_defaults(fn=cmd_done)
    p_undone = sub.add_parser("undone", help="把 tasks 列表第 N 项改回未完成")
    p_undone.add_argument("n", type=int, help="bbwatch tasks 中的编号")
    p_undone.set_defaults(fn=cmd_undone)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as e:  # noqa: BLE001
        print(f"错误：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
