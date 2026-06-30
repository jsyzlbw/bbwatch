"""本地任务清单网页：仅绑 127.0.0.1。GET / 页面；/api/tasks 数据；/api/done 勾选回写；
/api/scan 触发后台扫描。核心逻辑在 DashboardState(可单测)，HTTP 处理是薄壳。
页面在 index.html(同目录)，由前端设计单独维护。"""
from __future__ import annotations

import json
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

INDEX_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")


class DashboardState:
    def __init__(self, store_factory, now_fn, scan_trigger=None):
        self._sf = store_factory
        self._now = now_fn
        self._scan = scan_trigger

    def tasks_payload(self) -> dict:
        from ..summary import build_session_summary

        store = self._sf()
        try:
            return {
                "tasks": store.actionable_tasks(),
                "pending": store.submitted_ungraded(),  # 已提交待批改
                "hidden": store.hidden_tasks(),          # 已删除(回收站)
                "last_scan": store.last_scan_time(),
                "summary": build_session_summary(store, self._now()),
            }
        finally:
            store.close()

    def set_done(self, entity_key: str, done: bool) -> dict:
        store = self._sf()
        try:
            store.mark_manual_done(entity_key, done, self._now())
            return {"ok": True}
        finally:
            store.close()

    def set_hidden(self, entity_key: str, hidden: bool) -> dict:
        store = self._sf()
        try:
            store.set_hidden(entity_key, hidden, self._now())
            return {"ok": True}
        finally:
            store.close()

    def trigger_scan(self) -> dict:
        if self._scan:
            self._scan()
        return {"ok": True}


def make_handler(state: DashboardState):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body: bytes, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(),
                       "application/json; charset=utf-8")

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/index"):
                self._send(200, INDEX_HTML.encode(), "text/html; charset=utf-8")
            elif self.path == "/api/tasks":
                self._json(state.tasks_payload())
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {}
            if self.path == "/api/done":
                self._json(state.set_done(body.get("entity_key"), bool(body.get("done"))))
            elif self.path == "/api/hide":
                self._json(state.set_hidden(body.get("entity_key"), bool(body.get("hidden"))))
            elif self.path == "/api/scan":
                self._json(state.trigger_scan())
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *a):  # 静默
            return

    return Handler


def find_port(preferred: int, host: str = "127.0.0.1") -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    return 0  # 交给系统分配


def serve(state: DashboardState, host: str = "127.0.0.1", port: int = 8765):
    actual = find_port(port, host) if port else 0
    httpd = HTTPServer((host, actual), make_handler(state))
    return httpd, httpd.server_address[1]
