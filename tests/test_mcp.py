from bbwatch.diff import diff_columns
from bbwatch.mcp_server import BbwatchServer
from bbwatch.models import Column, ColumnStatus
from bbwatch.store import Store

NOW = "2026-06-28T00:00:00.000Z"


def _store_with_task():
    s = Store(":memory:")
    s.establish_baseline("_c", "columns", NOW)
    for ch in diff_columns(
        {}, [Column("_h1", "HW1", "2026-07-10T15:59:00.000Z")],
        {"_h1": ColumnStatus("None")}, cid="_c", scan_id=1, suppress=False,
    ):
        s.apply_change(ch, NOW)
    return s


def _server(store):
    return BbwatchServer(
        store_factory=lambda: store, login_client=lambda: None,
        notifier_factory=lambda: None, now_fn=lambda: NOW,
    )


def test_initialize_and_tools_list():
    srv = _server(Store(":memory:"))
    r = srv.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r["result"]["serverInfo"]["name"] == "bbwatch"
    r2 = srv.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in r2["result"]["tools"]}
    assert {"list_tasks", "mark_task_done", "scan_now", "list_courses", "download_course"} <= names


def test_tools_call_list_tasks():
    srv = _server(_store_with_task())
    r = srv.dispatch({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "list_tasks", "arguments": {}}})
    text = r["result"]["content"][0]["text"]
    assert "HW1" in text and "[1]" in text


def test_tools_call_mark_done():
    store = _store_with_task()
    srv = _server(store)
    srv.dispatch({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                  "params": {"name": "mark_task_done", "arguments": {"n": 1, "done": True}}})
    assert store.actionable_tasks()[0]["done"] is True


def test_notifications_initialized_no_response():
    srv = _server(Store(":memory:"))
    assert srv.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_tool_returns_error_content():
    srv = _server(Store(":memory:"))
    r = srv.dispatch({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                      "params": {"name": "nope", "arguments": {}}})
    assert r["result"]["isError"] is True
