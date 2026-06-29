import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_plugin_json_valid():
    d = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert d["name"] == "bbwatch" and d["description"] and d["version"]


def test_mcp_json_points_to_server():
    d = json.loads((ROOT / ".mcp.json").read_text())
    srv = d["mcpServers"]["bbwatch"]
    assert srv["args"] == ["-m", "bbwatch.mcp_server"]
    assert "CLAUDE_PLUGIN_DATA" in srv["command"]  # 引擎在持久数据目录的 venv


def test_marketplace_json_lists_bbwatch():
    d = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert any(p["name"] == "bbwatch" for p in d["plugins"])


def test_hooks_json_has_sessionstart_and_setup():
    d = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    assert "SessionStart" in d["hooks"]
    assert "Setup" in d["hooks"]  # 安装后自举


def test_bootstrap_and_install_docs_exist():
    assert (ROOT / "scripts" / "bootstrap.sh").exists()
    assert (ROOT / "INSTALL.md").exists()


def test_commands_have_frontmatter():
    for name in ["bb-scan", "bb-tasks", "bb-download", "bb-setup"]:
        t = (ROOT / "commands" / f"{name}.md").read_text()
        assert t.startswith("---") and "description:" in t


def test_skill_frontmatter():
    t = (ROOT / "skills" / "bb-assistant" / "SKILL.md").read_text()
    assert t.startswith("---") and "name: bb-assistant" in t


def test_session_start_runs_without_db(tmp_path):
    env = dict(os.environ, BBWATCH_HOME=str(tmp_path / ".bbwatch"))
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "session_start.py")],
        capture_output=True, text=True, env=env, timeout=20,
    )
    assert r.returncode == 0
    assert "bbwatch" in r.stdout
