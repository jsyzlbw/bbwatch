# Codex 安装指南

在 Codex 桌面应用或 Codex CLI 中，通过自然语言管理港中深 Blackboard。兼容已有 Claude Code 版本的数据与凭据。这里的“全局”是当前 macOS 用户的所有 Codex 项目，不需要管理员权限。

这是 **Codex 本机插件**。ChatGPT 网页版的远程 MCP 接入需要额外部署 HTTPS 服务；本安装器不会创建云端服务或公开本机端口。

[返回首页](README.md) · [Claude Code 安装](INSTALL.md)

## 开始前

面向 **macOS**。需要 Git、支持 `codex plugin` 的 Codex CLI，以及 Python 3.11 或更新版本。即使平时使用 Codex 桌面应用，安装器也需要调用 Codex CLI 完成插件注册。

```bash
git --version
codex plugin --help
python3 --version
```

下面以 `python3` 为例。如果它低于 3.11，请改用已经安装的新版 Python，例如 `python3.12`；本文对应命令都要使用同一个解释器。

## 1. 下载项目并安装

```bash
git clone https://github.com/jsyzlbw/bbwatch.git
cd bbwatch
python3 scripts/install_codex.py --dry-run
python3 scripts/install_codex.py
```

如果已经下载了本仓库，直接进入仓库目录运行最后两行。`--dry-run` 只检查环境并显示安装位置；确认检查通过后，下一行才执行安装。

安装器会安装依赖与引擎、复制 Codex 插件并注册到当前用户的本地插件市场，所有 Codex 项目都可使用。它会保留其他插件和 Codex 配置；遇到同名但非本安装器管理的目录或命令时会停止，避免覆盖。

**安装过程不需要学校密码，也不会登录、扫描或创建后台监控。**

如果终端找不到 `codex`，可向安装器提供你机器上的实际 Codex CLI 路径。例如，确实安装在下面位置时使用：

```bash
python3 scripts/install_codex.py --codex /opt/homebrew/bin/codex
```

<details>
<summary>查看安装位置和工作原理</summary>

| 位置 | 用途 |
| --- | --- |
| `~/plugins/bbwatch` | Codex 插件源副本 |
| `~/.agents/plugins/marketplace.json` | 当前用户的本地插件市场 |
| `~/.local/share/bbwatch-codex/venv` | 持久 Python 运行环境与已安装引擎 |
| `~/.local/bin/bbwatch` | 全局命令入口 |
| `~/.bbwatch` | 原有任务数据库、会话与用户配置 |

安装后的 MCP 配置使用运行环境的绝对路径，因此不依赖 Codex 的 PATH，也不依赖本仓库所在目录。无需另行运行 `codex mcp add`。如果终端不识别 `bbwatch`，可直接使用 `~/.local/bin/bbwatch`；插件不受影响。

</details>

## 2. 在本机终端配置账号

安装完成后，在终端依次运行：

```bash
~/.local/bin/bbwatch setup
~/.local/bin/bbwatch whoami
~/.local/bin/bbwatch scan
```

| 命令 | 作用 |
| --- | --- |
| `setup` | 按提示输入学校账号和密码；密码不回显，并存入本机钥匙串 |
| `whoami` | 实际登录学校，验证账号是否可用 |
| `scan` | 第一次扫描 Blackboard，建立本地课程和作业清单 |

**无需把密码发进聊天。** 如果已经用过 Claude Code 版，两个版本默认共用钥匙串服务 `bbwatch` 和 `~/.bbwatch` 数据目录，通常可以跳过 `setup`，直接验证登录并扫描。

安装成功不代表学校登录成功；首次扫描之前，本地空清单也不等于学校没有作业。

### 登录失败或提示“认证连续失败已熔断”

连续 **3 次账号或密码认证失败**后，bbwatch 会暂停新的登录尝试 **1 小时**。先核对账号和密码，再在本机终端重新配置：

```bash
~/.local/bin/bbwatch setup
~/.local/bin/bbwatch whoami
```

`setup` 成功保存凭据后，会清零失败计数、解除本地暂停，并清除旧登录会话。**这不代表密码已验证**；接着用 `whoami` 验证新凭据，显示已登录后再扫描。若仍然失败，先检查凭据，不要反复尝试登录或循环运行这两条命令。

如果旧版本重新配置后仍提示熔断，在最初下载的仓库目录中更新并重新安装，再执行上面的两步：

```bash
git pull --ff-only
python3 scripts/install_codex.py
```

更新时沿用初次安装的 Python 版本及 `--codex` 路径；详见[更新、检查与移除](#更新检查与移除)。

### 网络连接与代理自动切换

bbwatch 会在每次请求前重新检查代理设置，无需重启看板或手动切换命令：

- 本机代理端口可用时使用代理；关闭代理后自动直连，再次开启后恢复使用。
- 优先使用终端中匹配该请求的代理环境变量；未配置时读取 macOS 手动代理设置，并遵守对应的绕过代理规则。
- 自动直连仅适用于不可连接的本机回环代理。远程代理或已连接后的网络错误会明确报错，登录请求不会自动重放。

此行为只影响 bbwatch，不修改系统、终端或其他应用的代理配置。支持 HTTP、HTTPS 和 SOCKS 代理；系统 PAC 自动代理脚本暂不解析。

`ConnectionError` 表示连接失败，页面会指出连接的服务和错误类别；处理后点击“重新扫描”。网络连接失败本身不会被计为密码错误，也不会清除原有登录缓存。

## 3. 新建 Codex 任务，开始对话

**新建一个 Codex 任务**加载新插件。可以直接说：

- “用 bbwatch 看一下初始化状态。”
- “扫描 Blackboard，告诉我最近有什么更新。”
- “我还有什么作业和 ddl？”
- “哪些作业交了还没出分？”
- “把 MAT3007 的课件下载下来。”
- “查找我已经下载的 slides。”
- “把第 1 个作业标记为完成。”

**Codex 版默认按需运行。** 查询作业读取本地缓存；要求扫描或最新状态时才实时刷新。开启 Codex 不会自动扫描。需要定时监控时明确提出周期和通知方式后再配置。

Claude Code 版原有的 SessionStart 自动刷新仍然保留；安装 Codex 版不会停用它。两个版本的刷新方式不同。

## 网页看板

```bash
~/.local/bin/bbwatch dashboard
```

在浏览器打开命令显示的本机地址，默认是 `http://127.0.0.1:8765/`。命令保持前台运行，关闭时按 Ctrl+C。安装过程不会预先启动看板。

点击“扫描更新”后，页面会显示真实进度，完成后自动更新；扫描失败或部分完成时会显示原因。课程较多时可能需要几分钟，无需重复点击。页面可见时每分钟重新读取本地清单，不会主动扫描学校网站。

## 更新、检查与移除

在最初下载的 `bbwatch` 仓库目录中更新并重新安装；完成后新建 Codex 任务。默认保留任务、配置和账号凭据。

```bash
git pull --ff-only
python3 scripts/install_codex.py
codex plugin list --json
~/.local/bin/bbwatch doctor
```

安装器会刷新引擎和插件缓存版本，然后重新注册；即使包版本号未变，也会安装仓库中的新代码。如果第一次安装使用了 `python3.12` 或 `--codex` 路径，更新时沿用相同参数。

如果看板已经打开，更新安装后还需在原终端按 Ctrl+C，再运行 `bbwatch dashboard` 并刷新页面；已经运行的看板不会自动加载新代码。

本地市场不存在时使用 `personal`；已经存在时沿用其合法名称和显示名称。只移除 Codex 插件可运行：

```bash
codex plugin remove bbwatch@personal
```

如果安装器显示了另一个市场名，请替换 `personal`。该操作不清理共享的 Blackboard 数据。`bbwatch uninstall` 是原项目的账号/会话清除命令，会影响共用凭据的客户端，不是仅移除 Codex 插件的命令。

## 开发与验证

Codex 包位于 `plugins/bbwatch/`；原有 `.claude-plugin/`、根目录 `.mcp.json`、命令与钩子仍供 Claude Code 使用。不要把 Claude 的 SessionStart 自动扫描钩子复制到 Codex 包。

引擎使用 FastMCP 1.x，依赖限定为 `mcp>=1.9,<2`；MCP 2.x 的旧导入入口不兼容。八个工具分别为 `get_status`、`list_tasks`、`list_pending`、`mark_task_done`、`scan_now`、`list_courses`、`download_course` 和 `find_materials`。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' -q
```

协议测试使用隔离数据目录及禁用的钥匙串后端，验证连接、工具发现、本地调用和正常退出，不访问真实账号。安装测试通过临时路径和模拟命令验证重复安装、空格路径、冲突保护与失败恢复。
