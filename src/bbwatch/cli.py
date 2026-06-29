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
    if not tasks:
        return "没有未完成的作业 🎉"
    now_dt = parse_utc(now)
    lines = []
    for t in tasks:
        due = parse_utc(t["due_utc"])
        local = due + timedelta(hours=8)
        delta_h = (due - now_dt).total_seconds() / 3600
        tag = "[逾期] " if delta_h < 0 else ("[紧急] " if delta_h <= 24 else "")
        lines.append(f"{tag}{local.strftime('%m-%d %H:%M')}  {t['name']}  ({t['course_id']})")
    return "\n".join(lines)


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
    print(format_tasks(store.outstanding_tasks(), now_utc()))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bbwatch")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="录入并保存学校账号密码到钥匙串").set_defaults(fn=cmd_setup)
    sub.add_parser("whoami", help="登录并打印身份与课程数").set_defaults(fn=cmd_whoami)
    sub.add_parser("scan", help="扫描 BB，检测新作业/改期/公告/出分并通知").set_defaults(fn=cmd_scan)
    sub.add_parser("tasks", help="列出未完成作业(按截止排序)").set_defaults(fn=cmd_tasks)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as e:  # noqa: BLE001
        print(f"错误：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
