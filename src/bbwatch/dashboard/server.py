"""本地任务清单网页：仅绑 127.0.0.1。GET / 页面；/api/tasks 数据；/api/done 勾选回写。
核心逻辑在 DashboardState(可单测)，HTTP 处理是薄壳。"""
from __future__ import annotations

import json
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer

INDEX_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>bbwatch 任务清单</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font:15px/1.5 -apple-system,system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#1c1c1e}
 h1{font-size:20px} .banner{background:#fff7e6;border:1px solid #ffd591;padding:8px 12px;border-radius:8px;margin:12px 0;white-space:pre-wrap}
 ul{list-style:none;padding:0} li{display:flex;align-items:center;gap:10px;padding:10px;border-bottom:1px solid #eee}
 li.done{opacity:.5} .due{font-variant-numeric:tabular-nums;color:#666;min-width:96px}
 .overdue{color:#d4380d;font-weight:600} .urgent{color:#fa8c16;font-weight:600}
 .name{flex:1} .course{color:#888;font-size:13px} button{margin-left:auto}
 input[type=checkbox]{width:18px;height:18px}
</style></head><body>
<h1>📚 bbwatch 任务清单</h1>
<div id="banner" class="banner"></div>
<button onclick="scan()">立即扫描</button>
<ul id="list"></ul>
<script>
async function load(){
  const r=await fetch('/api/tasks'); const d=await r.json();
  document.getElementById('banner').textContent=d.summary||'';
  const ul=document.getElementById('list'); ul.innerHTML='';
  const now=Date.now();
  for(const t of d.tasks){
    const li=document.createElement('li'); if(t.done) li.className='done';
    const cb=document.createElement('input'); cb.type='checkbox'; cb.checked=t.done;
    cb.onchange=()=>toggle(t.entity_key,cb.checked);
    const due=new Date(t.due_utc.replace('Z','+00:00'));
    const local=new Date(due.getTime()+8*3600*1000);
    const dh=(due-now)/3600000;
    const ds=document.createElement('span'); ds.className='due';
    ds.textContent=local.toISOString().slice(5,16).replace('T',' ');
    if(!t.done){ if(dh<0)ds.className='due overdue'; else if(dh<=24)ds.className='due urgent'; }
    const nm=document.createElement('span'); nm.className='name';
    nm.innerHTML='<b>'+escapeHtml(t.name||'')+'</b> <span class="course">'+escapeHtml(t.course_id||'')+'</span>';
    li.append(cb,ds,nm); ul.append(li);
  }
}
async function toggle(k,done){ await fetch('/api/done',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entity_key:k,done})}); load(); }
async function scan(){ document.getElementById('banner').textContent='扫描中…'; await fetch('/api/scan',{method:'POST'}); setTimeout(load,1500); }
function escapeHtml(s){return s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
load();
</script></body></html>"""


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
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

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
