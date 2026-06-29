from __future__ import annotations

import argparse
import getpass
import sys
from datetime import timedelta
from pathlib import Path

from .auth import login as adfs_login
from .bbclient import BbClient
from .config import DEFAULT_CONFIG_TOML, AppPaths, load_config, make_course_filter
from .downloader import mirror
from .notifier import MacNotifier, deliver_pending
from .scanner import scan
from .secrets import Credentials, load_credentials, store_credentials
from .session import ensure_session, save_session
from .store import Store, now_utc, parse_utc
from .transport import CurlCffiTransport, Transport

DEFAULT_DEST = Path.home() / "Downloads" / "bbwatch"


def _identity_summary(client: BbClient) -> str:
    me = client.get_me()
    courses = client.list_courses(me.id)
    active = [c for c in courses if c.is_active]
    name = me.given_name or me.user_name
    return f"已登录：{name}（uid={me.id}）\n课程：共 {len(courses)} 门，在读 {len(active)} 门"


def _authed():
    """会话缓存版登录：复用 cookie，失效才重登。返回 (client, store, paths)。"""
    paths = AppPaths()
    paths.ensure_dirs()
    store = Store(paths.db_path)
    creds = load_credentials()
    transport = CurlCffiTransport()

    def relogin():
        adfs_login(transport, creds)
        save_session(transport, paths.session_path)

    def verify(t):
        try:
            return BbClient(t).get_me() is not None
        except Exception:  # noqa: BLE001
            return False

    ensure_session(transport, store, creds, paths.session_path, now=now_utc(), verify=verify)
    return BbClient(transport, relogin=relogin), store, paths


def run_whoami(transport: Transport, creds: Credentials, login_fn=adfs_login) -> str:
    login_fn(transport, creds)
    return _identity_summary(BbClient(transport))


def run_scan(client, store, notifier, *, now: str, course_filter=None, archive_weeks: int = 0) -> str:
    """登录后的扫描装配（client 已就绪，便于测试注入）。"""
    me = client.get_me()
    result = scan(client, store, me.id, now=now, course_filter=course_filter)
    archived = store.archive_overdue(now, archive_weeks) if archive_weeks else 0
    sent = deliver_pending(store, notifier, now)
    outstanding = store.outstanding_tasks()
    lines = [
        f"扫描完成：{result.courses_scanned} 门在读课，新事件 {result.new_events}，已推送 {sent}。"
    ]
    if result.failures:
        lines.append(f"⚠ 部分维度失败 {len(result.failures)} 处：{'; '.join(result.failures[:5])}")
    if archived:
        lines.append(f"已归档 {archived} 个逾期旧作业")
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


def format_courses(courses: list) -> str:
    if not courses:
        return "没有在读课程"
    return "\n".join(f"[{i}] {c.course_id}" for i, c in enumerate(courses, 1))


def pick_course(courses: list, ref: str):
    """按编号(1 起)或课程代码子串选课。"""
    if ref.isdigit():
        i = int(ref)
        if 1 <= i <= len(courses):
            return courses[i - 1]
        raise ValueError(f"课程编号 {i} 超出范围（共 {len(courses)} 门）")
    matches = [c for c in courses if ref.lower() in (c.course_id or "").lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"未找到匹配课程: {ref}（用 bbwatch courses 查看）")
    raise ValueError("匹配到多门：" + ", ".join(m.course_id for m in matches) + "，请用编号")


def run_download(client, store, course, dest, *, now: str) -> str:
    res = mirror(client, store, course, dest, now=now)
    lines = [
        f"下载完成：{course.course_id} → {dest}",
        f"  新下载 {res.downloaded}，跳过(已最新) {res.skipped}，失败 {res.failed}",
    ]
    if res.errors:
        lines.append("  错误：" + "; ".join(res.errors[:5]))
    return "\n".join(lines)


def resolve_setup_credentials(env: dict, stdin_text: str | None = None):
    """非交互获取凭据：优先环境变量 BBWATCH_USERNAME/PASSWORD，其次 stdin 两行。
    都没有则返回 (None, None)，由调用方回退到交互式。"""
    u = env.get("BBWATCH_USERNAME")
    p = env.get("BBWATCH_PASSWORD")
    if u and p:
        return u, p
    if stdin_text:
        lines = [ln.strip() for ln in stdin_text.splitlines() if ln.strip()]
        if len(lines) >= 2:
            return lines[0], lines[1]
    return None, None


def cmd_setup(args) -> int:
    import os

    stdin_text = sys.stdin.read() if getattr(args, "stdin", False) else None
    username, password = resolve_setup_credentials(os.environ, stdin_text)
    if not (username and password):
        username = input("学校账号(形如 学号@link.cuhk.edu.cn): ").strip()
        password = getpass.getpass("密码（输入不回显）: ")
    store_credentials(username, password)
    print("已存入 macOS 钥匙串。可运行 bbwatch whoami 验证。")
    return 0


def cmd_whoami(_args) -> int:
    client, _store, _paths = _authed()
    print(_identity_summary(client))
    return 0


def cmd_scan(_args) -> int:
    client, store, paths = _authed()
    cfg = load_config(paths.config_path)
    print(run_scan(
        client, store, MacNotifier(), now=now_utc(),
        course_filter=make_course_filter(cfg), archive_weeks=cfg.archive_overdue_weeks,
    ))
    return 0


def cmd_config(_args) -> int:
    paths = AppPaths()
    paths.ensure_dirs()
    if not paths.config_path.exists():
        paths.config_path.write_text(DEFAULT_CONFIG_TOML)
        print(f"已生成默认配置：{paths.config_path}")
    cfg = load_config(paths.config_path)
    print(f"配置文件：{paths.config_path}")
    print(f"  include(白名单)={cfg.include}  exclude(黑名单)={cfg.exclude}")
    print(f"  归档逾期周数={cfg.archive_overdue_weeks}  下载目录={cfg.download_dest}  端口={cfg.dashboard_port}")
    return 0


def cmd_find(args) -> int:
    paths = AppPaths()
    paths.ensure_dirs()
    store = Store(paths.db_path)
    hits = store.search_downloads(args.kw)
    if not hits:
        print(f"未找到匹配 '{args.kw}' 的已下载文件（先 bbwatch download 下载课件）")
    else:
        print("\n".join(hits))
    return 0


def cmd_doctor(_args) -> int:
    from .ops import run_doctor

    paths = AppPaths()
    paths.ensure_dirs()
    print(run_doctor(paths))
    return 0


def cmd_uninstall(args) -> int:
    from .ops import run_uninstall

    paths = AppPaths()
    if not args.yes:
        extra = "、本地数据库" if args.purge_db else ""
        ans = input(f"将清除钥匙串凭据与会话缓存{extra}。确定? [y/N] ").strip().lower()
        if ans != "y":
            print("已取消")
            return 0
    print(run_uninstall(paths, purge_db=args.purge_db))
    return 0


def cmd_courses(_args) -> int:
    client, _store, _paths = _authed()
    me = client.get_me()
    active = [c for c in client.list_courses(me.id) if c.is_active]
    print(format_courses(active))
    return 0


def cmd_download(args) -> int:
    client, store, paths = _authed()
    cfg = load_config(paths.config_path)
    me = client.get_me()
    active = [c for c in client.list_courses(me.id) if c.is_active]
    course = pick_course(active, args.ref)
    dest = Path(args.dest) if args.dest else Path(cfg.download_dest).expanduser()
    print(run_download(client, store, course, dest, now=now_utc()))
    return 0


def cmd_dashboard(args) -> int:
    import webbrowser

    from .dashboard import DashboardState, serve

    paths = AppPaths()
    paths.ensure_dirs()
    state = DashboardState(store_factory=lambda: Store(paths.db_path), now_fn=now_utc)
    httpd, port = serve(state, port=args.port or load_config(paths.config_path).dashboard_port)
    url = f"http://127.0.0.1:{port}/"
    print(f"任务清单：{url}（Ctrl-C 退出）")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
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
    p_setup = sub.add_parser("setup", help="录入并保存学校账号密码到钥匙串")
    p_setup.add_argument("--stdin", action="store_true", help="从 stdin 读两行(账号/密码)，非交互")
    p_setup.set_defaults(fn=cmd_setup)
    sub.add_parser("whoami", help="登录并打印身份与课程数").set_defaults(fn=cmd_whoami)
    sub.add_parser("scan", help="扫描 BB，检测新作业/改期/公告/出分并通知").set_defaults(fn=cmd_scan)
    sub.add_parser("tasks", help="列出可跟踪作业(编号 + ○/✓)").set_defaults(fn=cmd_tasks)
    p_done = sub.add_parser("done", help="把 tasks 列表第 N 项标记为已完成")
    p_done.add_argument("n", type=int, help="bbwatch tasks 中的编号")
    p_done.set_defaults(fn=cmd_done)
    p_undone = sub.add_parser("undone", help="把 tasks 列表第 N 项改回未完成")
    p_undone.add_argument("n", type=int, help="bbwatch tasks 中的编号")
    p_undone.set_defaults(fn=cmd_undone)
    p_dash = sub.add_parser("dashboard", help="启动本地任务清单网页(浏览器查看/勾选)")
    p_dash.add_argument("--port", type=int, help="端口(默认 8765)")
    p_dash.set_defaults(fn=cmd_dashboard)
    sub.add_parser("courses", help="列出在读课程(编号)").set_defaults(fn=cmd_courses)
    p_dl = sub.add_parser("download", help="增量镜像某课程的全部课件")
    p_dl.add_argument("ref", help="课程编号(见 bbwatch courses)或课程代码子串")
    p_dl.add_argument("--dest", help=f"下载目录(默认 {DEFAULT_DEST})")
    p_dl.set_defaults(fn=cmd_download)
    p_find = sub.add_parser("find", help="在已下载课件中按关键词检索(本地, 不联网)")
    p_find.add_argument("kw", help="文件名/路径/课程 关键词")
    p_find.set_defaults(fn=cmd_find)
    sub.add_parser("config", help="查看/生成配置文件").set_defaults(fn=cmd_config)
    sub.add_parser("doctor", help="健康自检(凭据/会话/数据库/端口)").set_defaults(fn=cmd_doctor)
    p_un = sub.add_parser("uninstall", help="清除凭据/会话(可选数据库)")
    p_un.add_argument("--purge-db", action="store_true", help="同时删除本地数据库")
    p_un.add_argument("--yes", action="store_true", help="跳过确认")
    p_un.set_defaults(fn=cmd_uninstall)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as e:  # noqa: BLE001
        print(f"错误：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
