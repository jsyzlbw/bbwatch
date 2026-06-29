import asyncio

from bbwatch import mcp_server
from bbwatch.config import AppPaths
from bbwatch.diff import diff_columns
from bbwatch.models import Column, ColumnStatus
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


def test_fastmcp_registers_five_tools():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    assert {"list_tasks", "mark_task_done", "scan_now", "list_courses", "download_course"} <= names


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
    assert mcp_server._store().actionable_tasks()[0]["done"] is True
