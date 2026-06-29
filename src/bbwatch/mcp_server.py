"""最小 MCP 服务器（stdio, 换行分隔 JSON-RPC 2.0）。把 bbwatch 能力暴露给 Claude Code：
list_tasks / mark_task_done / scan_now / list_courses / download_course。

工厂可注入便于离线测试；默认工厂在真实使用时按需登录/建库。
"""
from __future__ import annotations

import json
import sys

from . import __version__

_TOOLS = [
    {
        "name": "list_tasks",
        "description": "列出可跟踪的作业(带编号与完成状态○/✓)。先看这个再用 mark_task_done。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mark_task_done",
        "description": "把 list_tasks 中第 n 项标记为完成(done=true)或未完成(done=false)。",
        "inputSchema": {
            "type": "object",
            "properties": {"n": {"type": "integer"}, "done": {"type": "boolean"}},
            "required": ["n", "done"],
        },
    },
    {
        "name": "scan_now",
        "description": "立即扫描 BB，检测新作业/改期/公告/出分/新课件并推送，返回摘要。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_courses",
        "description": "列出本学期在读课程(带编号)。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "download_course",
        "description": "增量镜像某课程的全部课件到本地。ref=课程编号或代码子串；dest 可选下载目录。",
        "inputSchema": {
            "type": "object",
            "properties": {"ref": {"type": "string"}, "dest": {"type": "string"}},
            "required": ["ref"],
        },
    },
]


class BbwatchServer:
    def __init__(self, store_factory=None, login_client=None, notifier_factory=None, now_fn=None):
        self._store_factory = store_factory or _default_store
        self._login_client = login_client or _default_client
        self._notifier_factory = notifier_factory or _default_notifier
        self._now = now_fn or _default_now

    def tool_specs(self):
        return _TOOLS

    def call_tool(self, name: str, args: dict) -> str:
        # 局部导入避免循环依赖
        from .cli import (
            format_courses,
            format_tasks,
            pick_course,
            run_download,
            run_mark_done,
            run_scan,
        )

        store = self._store_factory()
        if name == "list_tasks":
            return format_tasks(store.actionable_tasks(), self._now())
        if name == "mark_task_done":
            return run_mark_done(store, int(args["n"]), bool(args["done"]), self._now())
        if name == "scan_now":
            client = self._login_client()
            return run_scan(client, store, self._notifier_factory(), now=self._now())
        if name == "list_courses":
            client = self._login_client()
            me = client.get_me()
            return format_courses([c for c in client.list_courses(me.id) if c.is_active])
        if name == "download_course":
            from .cli import DEFAULT_DEST

            client = self._login_client()
            me = client.get_me()
            active = [c for c in client.list_courses(me.id) if c.is_active]
            course = pick_course(active, args["ref"])
            dest = args.get("dest") or str(DEFAULT_DEST)
            return run_download(client, store, course, dest, now=self._now())
        raise ValueError(f"未知工具: {name}")

    def dispatch(self, msg: dict) -> dict | None:
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if method == "initialize":
            return _ok(mid, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "bbwatch", "version": __version__},
            })
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return _ok(mid, {"tools": self.tool_specs()})
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                text = self.call_tool(name, args)
                return _ok(mid, {"content": [{"type": "text", "text": text}]})
            except Exception as e:  # noqa: BLE001  工具错误回传为内容而非协议错误
                return _ok(mid, {
                    "content": [{"type": "text", "text": f"错误：{e}"}],
                    "isError": True,
                })
        if mid is not None:
            return _err(mid, -32601, f"method not found: {method}")
        return None


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _default_store():
    from .config import AppPaths
    from .store import Store

    paths = AppPaths()
    paths.ensure_dirs()
    return Store(paths.db_path)


def _default_client():
    from .cli import _authed  # 复用会话缓存登录

    return _authed()[0]


def _default_notifier():
    from .notifier import MacNotifier

    return MacNotifier()


def _default_now():
    from .store import now_utc

    return now_utc()


def main() -> int:
    server = BbwatchServer()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        resp = server.dispatch(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
