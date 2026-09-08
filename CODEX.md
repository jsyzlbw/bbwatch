# bbwatch：Codex 本机全局版

在 Codex 桌面应用或 Codex CLI 中，通过自然语言管理港中深 Blackboard。兼容已有 Claude Code 版本的数据与凭据。这里的“全局”是当前 macOS 用户的所有 Codex 项目，不需要管理员权限。

这是 **Codex 本机插件**。ChatGPT 网页版的远程 MCP 接入需要额外部署 HTTPS 服务；本安装器不会创建云端服务或公开本机端口。

## 安装

需要支持 `codex plugin` 的 Codex CLI，以及 Python 3.11 或更新版本。macOS 自带的 Python 可能较旧，先检查版本；本机验证使用 Python 3.12。

在本仓库目录运行：

```bash
python3.12 scripts/install_codex.py --dry-run
python3.12 scripts/install_codex.py
```

如果找不到 Codex，可显式传入路径：

```bash
python3.12 scripts/install_codex.py --codex /opt/homebrew/bin/codex
```

安装器会安装依赖与引擎、复制 Codex 插件并调用 `codex plugin add`。它保留其他插件和 Codex 配置；遇到同名但非本安装器管理的目录或命令会停止，不覆盖。全程不读取学校密码、不登录学校、不扫描、不添加自动化或后台守护进程。

| 位置 | 用途 |
| --- | --- |
| `~/plugins/bbwatch` | Codex 插件源副本 |
| `~/.agents/plugins/marketplace.json` | 当前用户的本地插件市场 |
| `~/.local/share/bbwatch-codex/venv` | 持久 Python 运行环境与已安装引擎 |
| `~/.local/bin/bbwatch` | 全局命令入口 |
| `~/.bbwatch` | 原有任务数据库、会话与用户配置 |

安装后的 MCP 配置使用运行环境的绝对路径，因此不依赖 Codex 的 PATH，也不依赖本仓库所在目录。无需另行运行 `codex mcp add`。如果终端不识别 `bbwatch`，可直接使用 `~/.local/bin/bbwatch`；插件不受影响。

## 首次使用

**新建一个 Codex 任务**加载新插件。可以直接说：

- “用 bbwatch 看一下初始化状态。”
- “扫描 Blackboard，告诉我最近有什么更新。”
- “我还有什么作业和 ddl？”
- “哪些作业交了还没出分？”
- “把 MAT3007 的课件下载下来。”
- “查找我已经下载的 slides。”
- “把第 1 个作业标记为完成。”

安装不代表学校账号已验证。如果还没配置账号，请在本机终端输入：

```bash
~/.local/bin/bbwatch setup
~/.local/bin/bbwatch whoami
~/.local/bin/bbwatch scan
```

`setup` 在终端提示输入账号和密码，密码不回显并存入本机钥匙串。无需把密码发进聊天。已有 Claude Code 版使用同一个钥匙串服务 `bbwatch`，通常可以直接复用；`whoami` 才会实际验证学校登录。扫描之前，本地空清单不等于学校没有作业。

默认按需运行。查询作业读取本地缓存；要求扫描或最新状态时才实时刷新。开启 Codex 不会自动扫描。需要定时监控时明确提出周期和通知方式后再配置。

## 网页看板

```bash
~/.local/bin/bbwatch dashboard
```

在浏览器打开命令显示的本机地址，默认是 `http://127.0.0.1:8765/`。命令保持前台运行，关闭时按 Ctrl+C。安装过程不会预先启动看板。

## 更新、检查与移除

更新本仓库后再次运行安装器即可。安装器会刷新同版本引擎和插件缓存版本，然后重新注册；更新后新建 Codex 任务。默认保留任务、配置和账号凭据。

```bash
python3.12 scripts/install_codex.py
codex plugin list --json
~/.local/bin/bbwatch doctor
```

本地市场不存在时使用 `personal`；已经存在时沿用其合法名称和显示名称。只移除 Codex 插件可运行：

```bash
codex plugin remove bbwatch@personal
```

如果安装器显示了另一个市场名，请替换 `personal`。该操作不清理共享的 Blackboard 数据。`bbwatch uninstall` 是原项目的账号/会话清除命令，会影响共用凭据的客户端，不是仅移除 Codex 插件的命令。

## 开发与验证

Codex 包位于 `plugins/bbwatch/`；原有 `.claude-plugin/`、根目录 `.mcp.json`、命令与钩子仍供 Claude Code 使用。不要把 Claude 的 SessionStart 自动扫描钩子复制到 Codex 包。

引擎使用 FastMCP 1.x，依赖限定为 `mcp>=1.9,<2`；MCP 2.x 的旧导入入口不兼容。八个工具分别为 `get_status`、`list_tasks`、`list_pending`、`mark_task_done`、`scan_now`、`list_courses`、`download_course` 和 `find_materials`。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' -q
```

协议测试使用隔离数据目录及禁用的钥匙串后端，验证连接、工具发现、本地调用和正常退出，不访问真实账号。安装测试通过临时路径和模拟命令验证重复安装、空格路径、冲突保护与失败恢复。
