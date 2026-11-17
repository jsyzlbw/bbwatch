"""User-owned macOS launchd jobs; no AI client is required."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .config import AppPaths

LABEL = "cn.edu.cuhk.bbwatch"


def agent_files(home: Path | None = None) -> dict[str, Path]:
    folder = (home or Path.home()) / "Library" / "LaunchAgents"
    return {kind: folder / f"{LABEL}.{kind}.plist" for kind in ("dashboard", "sync")}


def build_agents(
    *, python: str, root: Path, interval: int, courses: list[str], dest: Path, port: int
) -> dict[str, dict]:
    if interval < 60:
        raise ValueError("周期至少为 60 秒")
    if not courses or any(not c.strip() or c.isdigit() for c in courses):
        raise ValueError("请指定稳定的课程代码，不能使用课程列表编号")
    if not 1 <= port <= 65535:
        raise ValueError("端口必须在 1–65535 之间")
    base = [python, "-u", "-m", "bbwatch.cli"]
    logs = root / "launchagent-logs"
    result = {}
    for kind, args in {
        "dashboard": ["dashboard", "--no-browser", "--port", str(port)],
        "sync": ["agent", "run", "--dest", str(dest), "--courses", *courses],
    }.items():
        job = {
            "Label": f"{LABEL}.{kind}",
            "ProgramArguments": base + args,
            "RunAtLoad": True,
            "WorkingDirectory": str(root),
            "EnvironmentVariables": {"BBWATCH_HOME": str(root), "PYTHONIOENCODING": "utf-8"},
            "StandardOutPath": str(logs / f"{kind}.log"),
            "StandardErrorPath": str(logs / f"{kind}.error.log"),
            "ProcessType": "Background",
        }
        if kind == "dashboard":
            job["KeepAlive"] = True
            job["ThrottleInterval"] = 30
        else:
            job["StartInterval"] = interval
        result[kind] = job
    return result


def launchctl(*args: str, check: bool = True):
    return subprocess.run(["/bin/launchctl", *args], check=check)


def manage(args) -> int:
    if sys.platform != "darwin":
        raise ValueError("LaunchAgent 仅支持 macOS")
    paths = AppPaths()
    root = paths.root.resolve()
    files = agent_files()
    domain = f"gui/{os.getuid()}"
    if args.action == "status":
        for kind, file in files.items():
            print(f"{kind}: {file}（{'已安装' if file.exists() else '未安装'}）", flush=True)
            launchctl("print", f"{domain}/{LABEL}.{kind}", check=False)
        return 0
    if args.action == "uninstall":
        for kind, file in files.items():
            launchctl("bootout", f"{domain}/{LABEL}.{kind}", check=False)
            file.unlink(missing_ok=True)
        print("已移除自动运行任务；保留数据、课件及日志")
        return 0
    if args.action == "run":
        return run_sync(args)
    jobs = build_agents(
        python=sys.executable,
        root=root,
        interval=args.interval,
        courses=args.courses,
        dest=Path(args.dest).expanduser().resolve(),
        port=args.port,
    )
    paths.ensure_dirs()
    logs = root / "launchagent-logs"
    logs.mkdir(mode=0o700, exist_ok=True)
    for kind, file in files.items():
        file.parent.mkdir(parents=True, exist_ok=True)
        # Refuse to replace another application's plist.
        if file.exists() and plistlib.loads(file.read_bytes()).get("Label") != jobs[kind]["Label"]:
            raise ValueError(f"拒绝覆盖其他任务：{file}")
    for kind, file in files.items():
        launchctl("bootout", f"{domain}/{LABEL}.{kind}", check=False)
        file.write_bytes(plistlib.dumps(jobs[kind]))
        file.chmod(0o600)
        launchctl("enable", f"{domain}/{LABEL}.{kind}")
        launchctl("bootstrap", domain, str(file))
    print(f"已安装并启动；登录后自动运行。看板：http://127.0.0.1:{args.port}/")
    print(f"周期：{args.interval} 秒；日志：{logs}")
    return 0


def run_sync(args) -> int:
    import fcntl

    from . import cli
    from .downloader import mirror
    from .store import now_utc

    paths = AppPaths()
    paths.ensure_dirs()
    # launchd serializes this job; also protect against a manual agent run.
    with (paths.root / "agent.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("已有同步任务运行，跳过")
            return 0
        print(f"[{now_utc()}] 开始扫描及下载", flush=True)
        scan_result = cli.main(["scan"])
        client, store, _ = cli._authed()
        failed = bool(scan_result)
        try:
            courses = [c for c in client.list_courses(client.get_me().id) if c.is_active]
            seen = set()
            for ref in args.courses:
                try:
                    course = cli.pick_course(courses, ref)
                    if course.id in seen:
                        continue
                    seen.add(course.id)
                    result = mirror(
                        client, store, course, Path(args.dest).expanduser(), now=now_utc()
                    )
                    print(
                        f"{course.course_id}: 下载 {result.downloaded}，跳过 {result.skipped}，"
                        f"失败 {result.failed}",
                        flush=True,
                    )
                    failed |= bool(result.failed)
                    for error in result.errors:
                        print(error, flush=True)
                except Exception as exc:  # noqa: BLE001
                    failed = True
                    print(f"{ref}: {exc}", flush=True)
        finally:
            store.close()
        print(f"[{now_utc()}] 同步结束，状态：{'失败/部分完成' if failed else '成功'}", flush=True)
        return int(failed)


def add_parser(sub):
    parser = sub.add_parser("agent", help="macOS 登录自动启动与定时扫描下载")
    actions = parser.add_subparsers(dest="action", required=True)
    install = actions.add_parser("install", help="安装/更新并立即启动用户 LaunchAgents")
    install.add_argument("--interval", type=int, required=True, help="周期秒数，至少 60")
    install.add_argument("--port", type=int, default=8765)
    run = actions.add_parser("run", help="执行一次扫描和指定课程下载")
    for command in (install, run):
        command.add_argument("--courses", nargs="+", required=True, help="课程代码（不使用编号）")
        command.add_argument("--dest", required=True, help="固定下载目录")
    actions.add_parser("status", help="查看安装与运行状态")
    actions.add_parser("uninstall", help="停止并移除自动运行任务")
    parser.set_defaults(fn=manage)
