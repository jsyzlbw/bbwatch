# Claude Code 安装指南

把 bbwatch 装进 Claude Code 后，可以直接对话查作业、扫描更新、下载课件。

**使用 Codex 桌面应用或 Codex CLI？** 请看 [Codex 安装指南](CODEX.md)。本文的安装命令和会话自动刷新只适用于 Claude Code。

[返回首页](README.md) · [Codex 安装](CODEX.md)

## 开始前

- 已安装 Claude Code CLI，并且支持 `claude plugin` 和 `claude --init-only`。
- `python3 --version` 显示 **Python 3.11 或更新版本**；安装脚本使用的命令是 `python3`。
- 推荐在 **macOS** 使用，账号密码通过 `keyring` 存入本机钥匙串。Linux 还需要可用的系统密钥环；Windows 原生安装脚本尚未提供。

## 方式一：让 Claude 帮你安装

把下面这段话发给 Claude Code：

> 帮我安装 Claude Code 插件 https://github.com/jsyzlbw/bbwatch，范围设为当前用户。完成插件安装和初始化后，请根据实际安装输出，给我完整的本机终端命令，用于录入账号、验证登录和首次扫描。账号密码由我在终端输入。

Claude 负责安装程序。安装完成后，你在**本机终端**运行它提供的 `setup` 命令，按提示输入学校账号和密码；密码输入时不会显示。**无需把密码发进聊天。**

接着运行它提供的 `whoami` 验证登录，再运行 `scan` 获取第一份作业清单。最后新开一个 Claude Code 会话，即可说“我还有哪些作业？”或“把 MAT3007 的课件下载下来”。

## 方式二：自己在终端安装

### 1. 添加插件并安装

```bash
claude plugin marketplace add jsyzlbw/bbwatch
claude plugin install bbwatch@bill-plugins --scope user
```

`bill-plugins` 是这个仓库的 Claude 插件市场名称。`--scope user` 表示为当前用户安装，在不同项目中都能使用。

### 2. 初始化运行环境

```bash
claude --init-only
```

这一步会创建 Python 环境、安装 bbwatch 引擎。安装脚本成功后会输出：

```text
bbwatch: 引擎已安装到 <本机实际路径>/.venv
```

**记下这行输出中的完整路径。** Claude 提供的 `CLAUDE_PLUGIN_DATA` 决定安装位置，不同机器可能不同；普通终端中也不一定有这个变量。安装器不会把 `bbwatch` 加入终端的全局命令路径。

### 3. 在本机终端配置账号

先运行下面这一行，把上一步输出中从 `/` 开始、到 `.venv` 结束的完整路径粘贴进去，然后按回车。只粘贴路径，不要附带引号或前面的提示文字。

```bash
printf '请粘贴安装输出中的完整 .venv 路径：'; IFS= read -r BBWATCH_VENV
```

然后依次运行：

```bash
"$BBWATCH_VENV/bin/bbwatch" setup
"$BBWATCH_VENV/bin/bbwatch" whoami
"$BBWATCH_VENV/bin/bbwatch" scan
```

| 命令 | 你会看到什么 |
| --- | --- |
| `setup` | 提示输入学校账号和密码，并将其存入系统密钥环 |
| `whoami` | 实际登录学校，验证账号是否可用 |
| `scan` | 扫描 Blackboard，建立本地课程和作业清单 |

如果安装输出里没有路径，可以让 Claude 读取安装日志并给出完整命令。不要直接使用猜测的插件缓存路径。

### 4. 新开 Claude Code 会话

试着说：

- “我还有哪些作业和 ddl？”
- “扫一下 Blackboard，有没有新作业或出分？”
- “把 MAT3007 的课件下载下来。”
- “把第 3 个作业标记为完成。”

Claude Code 版保留了 **SessionStart 自动刷新**：新会话先展示本地待办摘要；从未扫描过，或距上次扫描超过 2 小时时，会再发起一次后台扫描。摘要可能仍是刷新前的缓存。**Codex 版默认按需运行，打开 Codex 不会自动扫描。**

## 网页看板

在刚才配置了 `BBWATCH_VENV` 的同一个终端运行：

```bash
"$BBWATCH_VENV/bin/bbwatch" dashboard
```

打开命令显示的本机地址，默认是 `http://127.0.0.1:8765/`。看板在终端前台运行，关闭时按 **Ctrl+C**。换了终端窗口，需要重新填入安装路径。

## 常见问题

| 遇到的问题 | 怎么处理 |
| --- | --- |
| 提示找不到 `bbwatch` | 使用安装目录中的完整命令，或按上面的步骤设置 `BBWATCH_VENV` |
| 初始化失败或环境缺失 | 先确认 `python3` 为 3.11+，再运行 `claude --init-only` |
| 学校密码变了 | 在终端重新运行同一路径下的 `bbwatch setup`，再用 `whoami` 验证 |
| 插件装好了，但清单是空的 | 先运行一次 `scan`；没有本地记录不代表学校没有作业 |
| 更新后仍在用旧功能 | 更新插件后重新运行 `claude --init-only`，再新开会话 |
| Linux 无法保存凭据 | 检查系统密钥环是否可用，再重试 `setup` |
| 需要检查本地状态 | 运行同一路径下的 `bbwatch doctor` |

## 只用命令行，不安装插件

```bash
git clone https://github.com/jsyzlbw/bbwatch.git
cd bbwatch
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/bbwatch setup
.venv/bin/bbwatch whoami
.venv/bin/bbwatch scan
```

之后使用 `.venv/bin/bbwatch tasks` 查作业，或用 `.venv/bin/bbwatch download MAT3007` 下载课件。命令行方式不会安装 Claude 的会话自动刷新钩子。

## 给维护者

Claude 的插件清单位于 `.claude-plugin/`，市场名定义在 `.claude-plugin/marketplace.json`。如修改市场名，也要同步修改安装命令里的 `bbwatch@bill-plugins`。

插件缓存和引擎运行环境分开：`Setup` 钩子调用 `scripts/bootstrap.sh`，在 `${CLAUDE_PLUGIN_DATA}/.venv` 安装引擎；根目录 `.mcp.json` 与 `SessionStart` 钩子都使用这个环境。引擎或依赖更新后，需要重新运行 `claude --init-only`。

当前脚本使用 macOS/Linux 的 `bin/` 路径。Windows 尚无 `.cmd` 初始化脚本；可考虑 WSL，但仍需处理 Linux 密钥环配置。
