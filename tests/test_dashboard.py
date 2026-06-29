import json
import threading
import urllib.request

from bbwatch.dashboard.server import INDEX_HTML, DashboardState, serve
from bbwatch.diff import diff_columns
from bbwatch.models import Column, ColumnStatus
from bbwatch.store import Store

NOW = "2026-06-28T00:00:00.000Z"


def _seed_db(db):
    s = Store(db)
    s.establish_baseline("_c", "columns", NOW)
    for ch in diff_columns(
        {}, [Column("_h1", "HW1", "2026-07-10T15:59:00.000Z")],
        {"_h1": ColumnStatus("None")}, cid="_c", scan_id=1, suppress=False,
    ):
        s.apply_change(ch, NOW)
    s.close()


def test_index_html_has_api_hooks():
    assert "/api/tasks" in INDEX_HTML and "/api/done" in INDEX_HTML and "bbwatch" in INDEX_HTML


def test_dashboard_http_get_and_toggle(tmp_path):
    db = tmp_path / "state.db"
    _seed_db(db)
    state = DashboardState(store_factory=lambda: Store(db), now_fn=lambda: NOW)
    httpd, port = serve(state, port=0)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        base = f"http://127.0.0.1:{port}"
        d = json.loads(urllib.request.urlopen(base + "/api/tasks", timeout=5).read())
        assert len(d["tasks"]) == 1 and d["tasks"][0]["done"] is False
        ek = d["tasks"][0]["entity_key"]

        req = urllib.request.Request(
            base + "/api/done",
            data=json.dumps({"entity_key": ek, "done": True}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)

        d2 = json.loads(urllib.request.urlopen(base + "/api/tasks", timeout=5).read())
        assert d2["tasks"][0]["done"] is True  # 勾选已回写

        html = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        assert "bbwatch" in html
    finally:
        httpd.shutdown()
