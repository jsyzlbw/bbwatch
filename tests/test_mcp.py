import asyncio
import sqlite3

import pytest

from bbwatch import mcp_server
from bbwatch.config import AppPaths
from bbwatch.diff import diff_columns
from bbwatch.models import Column, ColumnStatus, Course, Me
from bbwatch.store import Store

NOW = "2026-06-28T00:00:00.000Z"


def _seed(tmp_path, monkeypatch, status="None"):
    monkeypatch.setenv("BBWATCH_HOME", str(tmp_path / ".bbwatch"))
    p = AppPaths()
    p.ensure_dirs()
    s = Store(p.db_path)
    s.establish_baseline("_c", "columns", NOW)
    for ch in diff_columns(
        {}, [Column("_h1", "HW1", "2026-07-10T15:59:00.000Z")],
        {"_h1": ColumnStatus(status)}, cid="_c", scan_id=1, suppress=False, course_code="MAT3007",
    ):
        s.apply_change(ch, NOW)
    s.close()


def test_fastmcp_registers_all_tools():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    assert {"list_tasks", "list_pending", "mark_task_done", "scan_now",
            "list_courses", "download_course", "get_status", "find_materials"} <= names


def test_tools_have_input_schema():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    by = {t.name: t for t in tools}
    # mark_task_done 的参数应被 FastMCP 从类型注解推断出来
    schema = by["mark_task_done"].inputSchema
    assert "n" in schema["properties"] and "done" in schema["properties"]


def test_list_tasks_tool_reads_store(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    out = mcp_server.list_tasks()
    assert "HW1" in out and "MAT3007" in out


def test_mark_task_done_tool(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    out = mcp_server.mark_task_done(1, True)
    assert "已完成" in out
    store = mcp_server._store()
    try:
        assert store.actionable_tasks()[0]["done"] is True
    finally:
        store.close()


def _forbid_auth():
    raise AssertionError("A local tool must not authenticate")


def test_status_without_database_does_not_initialize_or_authenticate(tmp_path, monkeypatch):
    root = tmp_path / "uninitialized"
    monkeypatch.setenv("BBWATCH_HOME", str(root))
    monkeypatch.setattr(mcp_server, "_authed", _forbid_auth)
    status = mcp_server.get_status()
    assert status == {
        "initialized": False,
        "last_scan_utc": None,
        "data_dir": str(root),
        "database_path": str(root / "state.db"),
        "config_path": str(root / "config.toml"),
        "config_exists": False,
    }
    assert not root.exists()


def test_status_reports_last_scan_without_authentication(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    paths = AppPaths()
    store = Store(paths.db_path)
    scan_id = store.start_scan(NOW)
    store.finish_scan(scan_id, "success", NOW)
    store.close()
    paths.config_path.write_text("# local configuration\n")
    monkeypatch.setattr(mcp_server, "_authed", _forbid_auth)
    status = mcp_server.get_status()
    assert status["initialized"] is True
    assert status["last_scan_utc"] == NOW
    assert status["config_exists"] is True


def test_find_materials_searches_only_completed_local_downloads(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    store = Store(AppPaths().db_path)
    store.record_download("att:1", "MAT3007", "/downloads/slides.pdf", "m", 1, NOW)
    store.record_download(
        "att:2", "MAT3007", "/downloads/slides-failed.pdf", "m", 1, NOW, status="failed"
    )
    store.close()
    monkeypatch.setattr(mcp_server, "_authed", _forbid_auth)
    assert mcp_server.find_materials("MAT3007") == "/downloads/slides.pdf"
    assert "未找到" in mcp_server.find_materials("nonexistent")


@pytest.mark.parametrize("name,args", [
    ("list_tasks", ()), ("list_pending", ()), ("mark_task_done", (1, True)),
    ("find_materials", ("slides",)), ("get_status", ()),
])
@pytest.mark.parametrize("fail", [False, True])
def test_local_tools_close_store_even_on_failure(tmp_path, monkeypatch, name, args, fail):
    _seed(tmp_path, monkeypatch)
    store = Store(AppPaths().db_path)
    monkeypatch.setattr(mcp_server, "_store", lambda: store)
    if fail:
        method = {
            "list_tasks": "actionable_tasks", "list_pending": "submitted_ungraded",
            "mark_task_done": "actionable_tasks", "find_materials": "search_downloads",
            "get_status": "last_scan_time",
        }[name]

        def fail_read(*_args):
            raise RuntimeError("database read failed")

        monkeypatch.setattr(store, method, fail_read)
        with pytest.raises(RuntimeError, match="database read failed"):
            getattr(mcp_server, name)(*args)
    else:
        getattr(mcp_server, name)(*args)
    try:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            store.schema_version()
    finally:
        store.close()


class _OfflineClient:
    def __init__(self, *, fail=False, with_course=False):
        self.fail = fail
        self.with_course = with_course

    def get_me(self):
        if self.fail:
            raise RuntimeError("request failed")
        return Me("_u", "test-user")

    def list_courses(self, _uid):
        if self.with_course:
            return [Course("_c", "MAT3007", "Math", None, "Student", "Yes", "Classic")]
        return []

    def walk_contents(self, _cid):
        return iter([])


@pytest.mark.parametrize("name,args", [
    ("scan_now", ()), ("list_courses", ()), ("download_course", ("MAT3007",)),
])
@pytest.mark.parametrize("fail", [False, True])
def test_remote_tools_close_store_even_on_failure(tmp_path, monkeypatch, name, args, fail):
    paths = AppPaths(tmp_path)
    paths.config_path.write_text(f'[download]\ndest = "{tmp_path / "downloads"}"\n')
    store = Store(paths.db_path)
    client = _OfflineClient(fail=fail, with_course=name == "download_course")
    monkeypatch.setattr(mcp_server, "_authed", lambda: (client, store, paths))
    if fail:
        with pytest.raises(RuntimeError, match="request failed"):
            getattr(mcp_server, name)(*args)
    else:
        getattr(mcp_server, name)(*args)
    try:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            store.schema_version()
    finally:
        store.close()


def test_read_only_tools_have_annotations():
    tools = {tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())}
    for name in ["list_tasks", "list_pending", "get_status", "find_materials"]:
        assert tools[name].annotations.readOnlyHint is True
        assert tools[name].annotations.openWorldHint is False
    assert tools["list_courses"].annotations.readOnlyHint is True
    assert tools["list_courses"].annotations.openWorldHint is True
