"""Exercise the actual stdio protocol without credentials or Blackboard traffic."""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_stdio_initialize_local_tools_and_clean_shutdown(tmp_path):
    async def exercise():
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(tmp_path),
            "BBWATCH_HOME": str(tmp_path / "bbwatch"),
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "bbwatch.mcp_server", cwd=ROOT, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def send(message):
            process.stdin.write((json.dumps({"jsonrpc": "2.0", **message}) + "\n").encode())
            await process.stdin.drain()

        async def request(request_id, method, params):
            await send({"id": request_id, "method": method, "params": params})
            while True:
                line = await process.stdout.readline()
                assert line, "MCP server closed stdout before responding"
                response = json.loads(line)
                if response.get("id") == request_id:
                    assert "error" not in response, response
                    return response["result"]

        try:
            async with asyncio.timeout(15):
                initialized = await request(1, "initialize", {
                    "protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "bbwatch-isolated-test", "version": "1.0"},
                })
                assert initialized["serverInfo"]["name"] == "bbwatch"
                await send({"method": "notifications/initialized"})
                listing = await request(2, "tools/list", {})
                assert {"get_status", "list_tasks", "find_materials"} <= {
                    tool["name"] for tool in listing["tools"]
                }
                status_result = await request(3, "tools/call", {
                    "name": "get_status", "arguments": {},
                })
                assert not status_result.get("isError"), status_result
                status = json.loads(status_result["content"][0]["text"])
                assert status["initialized"] is False
                assert not (tmp_path / "bbwatch").exists()
                tasks = await request(4, "tools/call", {"name": "list_tasks", "arguments": {}})
                assert not tasks.get("isError"), tasks
                assert "没有需要跟踪的作业" in tasks["content"][0]["text"]
                materials = await request(5, "tools/call", {
                    "name": "find_materials", "arguments": {"query": "slides"},
                })
                assert not materials.get("isError"), materials
                assert "未找到" in materials["content"][0]["text"]
                process.stdin.close()
                await asyncio.wait_for(process.wait(), timeout=5)
                assert process.returncode == 0, (await process.stderr.read()).decode()
        finally:
            # Own exactly this child, and reap it even after a protocol assertion fails.
            if process.returncode is None:
                process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=5)

    asyncio.run(exercise())
