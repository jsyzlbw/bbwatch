# 安装指南

> 把 `<OWNER>/bbwatch` 换成你的实际 GitHub 仓库（如 `liangbowen/bbwatch`）。
> 插件市场名是 `bill-plugins`（在 `.claude-plugin/marketplace.json` 里，可改）。

前置：已安装 **Claude Code CLI** 与 **Python ≥ 3.11**（macOS / Linux）。

---

## A. 给同学：在 Claude Code 里让 AI 一键安装（推荐）

在 Claude Code 对话框发给 AI（一句话）：

> 帮我安装这个 Claude Code 插件：`https://github.com/<OWNER>/bbwatch`
> 我的账号是 `125xxxxxx@link.cuhk.edu.cn`，密码是 `xxxxxx`

AI 会执行下面三步（你也可以自己在终端照做）：

```bash
# 1) 添加插件市场并安装(用户级，全局可用)
claude plugin marketplace add <OWNER>/bbwatch
claude plugin install bbwatch@bill-plugins --scope user

# 2) 触发"安装后自举"：建虚拟环境 + 装引擎 + 写入凭据
#    把账号密码放进环境变量(不要作为明文参数)，随 --init-only 一起传入
BBWATCH_USERNAME='125xxxxxx@link.cuhk.edu.cn' \
BBWATCH_PASSWORD='你的密码' \
claude --init-only
```

完成。**新开一个 Claude Code 会话**即可：

- 开场自动报待办（最近 ddl、新变化）；
- 直接对话：“扫一下有没有新作业 / 出分了吗”、“把 MAT3007 的课件下下来”、“我还有哪些 ddl”、“第 3 个做完了”。

> 安全：密码只存你本机 macOS 钥匙串；引擎装在 Claude Code 插件数据目录（`${CLAUDE_PLUGIN_DATA}`，随插件更新保留）。

### 排障
- 没生效：重跑 `claude --init-only`，再新开会话。
- 改了密码：`BBWATCH_USERNAME=... BBWATCH_PASSWORD=... claude --init-only` 重配。
- 自检：在新会话里用 `/bb-scan`，或让 AI 运行插件自带的 `bbwatch doctor`。

---

## B. 给开发者：怎么分发

1. 把本仓库推到 **GitHub**（公开，或让同学有读权限）。
   `.gitignore` 已排除 `.venv / state.db / 下载的课件 / 日志`，**不会泄露你的账号或数据**（仓库里也确无这些）。
2. 把仓库地址发给同学，让他们按上面的 **A** 安装。
3. 想自定义市场名：改 `.claude-plugin/marketplace.json` 的 `name`，并相应改安装命令 `bbwatch@<新名字>`。
4. 插件更新后，提醒同学再跑一次 `claude --init-only` 重建引擎（拉取了新代码/依赖时）。

> 注：插件从 git 安装时会被复制到 `~/.claude/plugins/cache/...`，**`.venv` 不会被复制**——所以靠 Setup 钩子（`scripts/bootstrap.sh`，由 `claude --init-only` 触发）在持久目录重建虚拟环境并安装引擎。这是 Python 插件的官方推荐做法。

---

## C. 仅当命令行工具用（不装插件）

```bash
git clone https://github.com/<OWNER>/bbwatch && cd bbwatch
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/bbwatch setup        # 终端输入账号密码
.venv/bin/bbwatch scan         # 之后: tasks / dashboard / download MAT3007 / find / doctor
```

---

## D. Windows

当前自举脚本与命令路径按 macOS/Linux 写（`.venv/bin/...`）。Windows（`.venv\Scripts\...`）需要一个 `.cmd` 版自举脚本与包装器——尚未提供，欢迎后续补充或用 **WSL**。
