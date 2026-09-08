# bbwatch Codex 本机插件

用户目标：把现有 Claude Code 项目适配为当前 Codex 桌面应用可用的插件，并在当前 macOS 用户下全局安装。ChatGPT 网页的远程 HTTPS MCP 接入是另一种部署方式，不在本次本机安装范围内。

## 结构

- `plugins/bbwatch/` 是独立 Codex 分发包，包含 manifest、MCP 配置和中文技能。避免 Codex 自动发现原有 Claude 钩子。
- 现有 Python 引擎继续服务两种客户端。保留原六个工具，新增本地状态和下载路径检索，关闭各调用持有的 SQLite 连接。
- 引擎非 editable 安装在 `~/.local/share/bbwatch-codex/venv`，插件复制到 `~/plugins/bbwatch`；安装后的 MCP 配置使用解释器绝对路径，避免 GUI PATH 差异及缓存路径变化。
- `scripts/install_codex.py` 是可重复执行的安装入口；注册隐式 personal marketplace，调用官方 Codex 插件安装命令，保留其他插件与设置。
- `~/.local/bin/bbwatch` 提供全局命令入口。凭据继续使用原有 keyring 服务，任务与配置继续使用 `~/.bbwatch`，不会迁移或打印密码。

## 运行边界

安装、MCP 启动及状态查询不登录、不扫描、不创建监控。用户明确请求扫描或下载时才访问 Blackboard。使用前台、有界的测试进程，关闭协议客户端后收回子进程。首次无数据库的状态不能被解释为没有作业。

## 验收

原有测试回归；安装保留其他插件、处理空格路径和重装；MCP initialize、tools/list、tools/call 和退出均经真实 stdio 验证。全局插件 installed/enabled 状态通过 CLI 验证。未运行真实学校登录时明确区分安装成功和账号验证。
