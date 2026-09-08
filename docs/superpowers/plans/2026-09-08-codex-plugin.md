# Codex Plugin Implementation Plan

> **For agentic workers:** Use subagent-driven-development for bounded independent tasks, and consolidate/review before local installation.

**Goal:** Ship and globally install a working local Codex version of bbwatch.

**Architecture:** A standalone Codex plugin bundle reuses the existing Python stdio MCP engine. A user-level installer manages the persistent runtime, copied plugin, personal marketplace registration, and global command without starting monitors.

**Tech Stack:** Python 3.11+, FastMCP 1.x, pytest, Codex plugin CLI.

## Work items

- [x] Add `plugins/bbwatch/.codex-plugin/plugin.json`, `.mcp.json`, and a Codex-specific Chinese `skills/bb-assistant/SKILL.md`; validate with the plugin-creator validator.
- [x] Pin `mcp>=1.9,<2` after reproducing the import failure with MCP 2.x. Add `bbwatch-mcp` console entry point.
- [x] In `src/bbwatch/mcp_server.py`, add `get_status` and `find_materials` and close stores on success/error. Write failing tests in `tests/test_mcp.py`, then verify them after implementation.
- [x] Add `tests/test_mcp_stdio.py`: use a temporary `BBWATCH_HOME`, start a stdio client, initialize, list tools, call local tools, close and wait. Enforce a finite timeout and never access real credentials.
- [x] Replace the real detached scan in `tests/test_plugin.py` with a mocked `Popen` while preserving the hook output assertion.
- [x] Add `scripts/install_codex.py` and `tests/test_codex_install.py`. Before global writes validate existing plugin ownership, marketplace structure and CLI collisions. Test preservation, spaces, dry-run and reinstall using temporary paths/fake command runners.
- [x] Document installation, account setup, upgrades, user prompts, removal and ChatGPT/Codex distinction in `CODEX.md`; add README/INSTALL entry points.
- [x] Run full pytest, focused lint, manifest/skill validation, and an independent review. Fix concrete findings before installation.
- [x] Execute the installer globally with Python 3.12, inspect installed/enabled state, and handshake against the cached installed MCP command from an unrelated directory.
- [x] Deliver source and installation instructions, report account verification limits, and verify all task-scoped agents/processes exited.
