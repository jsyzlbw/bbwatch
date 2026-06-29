# bbwatch

> 港中深（CUHK-SZ）Blackboard 作业监控 + 课件批量下载 —— 一个 Claude Code 插件 / 命令行工具。

老师在 `bb.cuhk.edu.cn` 上布置作业、发公告**不一定发邮件**，容易漏做；期末想批量下课件又很麻烦。
bbwatch 自动扫描你的 BB，发现新作业 / 改期 / 公告 / 出分 / 新课件就提醒你，维护一份可勾选的任务清单，并能一键把课件增量镜像到本地。

- 🔒 **本地优先**：密码只存 macOS 钥匙串，数据只在你自己机器上；除附件下载外只发只读请求，**从不调用花名册等含他人隐私的接口**。
- 🎯 **绝不漏、绝不重复**：用稳定 id 与本地状态做全量 diff + 事件状态机（详见设计文档）。
- 🤖 既能当**命令行工具**用，也能装成 **Claude Code 插件**（开 Claude Code 自动报 ddl，对话即可下课件 / 查作业）。

## 功能

| 能力 | 说明 |
|---|---|
| 新作业 + 截止提醒 | 老师新建作业(成绩册栏目)即检测，含 ddl |
| 作业**改期**提醒 | 老师改 `due` 时间也能发现 |
| 新公告提醒 | 含考试 / 补课 / 座位 / 改期等关键词的公告标 **[重要]** |
| 出分提醒 | 作业出分时通知 |
| 新课件上传提醒 | 老师传新 slides / 讲义即检测 |
| 任务清单 | 命令行 `tasks` 或浏览器网页，按 ddl 排序、逾期/紧急高亮 |
| 手动勾选完成 / 撤销 | 纸质 / 线下作业自己标记，扫描不会覆盖 |
| 课件增量镜像下载 | 按 课程/文件夹 结构下载，重跑只下新增/更新；往年卷自动归集到 `_exams/` |
| 本地课件检索 | `find <关键词>`，不联网 |
| macOS 桌面通知 | 新事件弹通知（失败退避重投、去重） |
| 会话缓存 + 熔断 | 复用登录会话省时；连续失败熔断防账号锁 |

## 安装

环境：macOS、Python ≥ 3.11。

```bash
git clone <repo> bbwatch && cd bbwatch
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/bbwatch setup        # 终端输入 学号@link.cuhk.edu.cn 与密码(存入钥匙串)
.venv/bin/bbwatch doctor       # 自检
```

## 使用（命令行）

```bash
bbwatch scan              # 扫描 BB，检测新作业/改期/公告/出分/新课件并桌面通知
bbwatch tasks             # 列出可跟踪作业(编号 + ○/✓)
bbwatch done 2            # 把第 2 项标记完成 ；undone 2 撤销
bbwatch dashboard         # 浏览器打开任务清单(可勾选)
bbwatch courses           # 列出在读课程
bbwatch download MAT3007  # 增量镜像某课全部课件(默认到 ~/Downloads/bbwatch)
bbwatch find slides       # 在已下载课件里检索
bbwatch config            # 查看/生成配置
bbwatch uninstall         # 清除凭据/会话(可选 --purge-db)
```

## 使用（Claude Code 插件）

```
/plugin marketplace add /Users/mac/Programming/cuhkszbb
/plugin install bbwatch@bill-plugins
```

装好后：

- **开 Claude Code 即自动**：SessionStart 钩子注入待办摘要（最近 ddl、新变化、临近未扫提示），并在后台静默刷新扫描。
- **对话驱动（MCP 工具）**：直接说"扫一下有没有新作业"、"把 MAT3350 的课件下下来"、"我还有哪些 ddl"、"第 3 个做完了"。
- **斜杠命令**：`/bb-scan` `/bb-tasks` `/bb-download` `/bb-setup`。

## 配置

首次 `bbwatch config` 生成 `~/.bbwatch/config.toml`：

```toml
[scan]
include = []            # 课程代码白名单子串(空=全部在读)
exclude = ["PED"]       # 黑名单(如体育)
archive_overdue_weeks = 4   # 逾期超 N 周的未完成作业自动归档隐藏

[download]
dest = "~/Downloads/bbwatch"

[dashboard]
port = 8765
```

## 工作原理（简）

```
engine/ (Python 引擎)                         plugin (Claude Code 外壳)
  auth      ADFS OAuth2 登录(无 MFA)            hooks/SessionStart  开工自动扫+摘要注入
  bbclient  /learn/api/public REST + 分页        commands/           斜杠命令
  store     SQLite 全量 diff + 事件状态机          .mcp.json           MCP 服务器(对话式)
  scanner   编排 + 冷启动静默 + 维度隔离           skills/bb-assistant
  downloader 增量镜像
  notifier  macOS 桌面通知
```

全校统一 AD FS 认证，登录后用会话 cookie 调官方 REST API。"绝不漏"靠**与本地已知集合全量 diff**（非时间窗增量，多天没扫也能补齐），"绝不重复"靠**稳定 id + 事件同事务落库 + UNIQUE 去重**。详见 [`docs/superpowers/specs/`](docs/superpowers/specs/) 的设计文档与对抗式审计。

## 开发

```bash
.venv/bin/pytest -q          # 98 测试
.venv/bin/ruff check src tests
```

设计与计划文档在 `docs/superpowers/`（概要设计、极其详细设计 + 鲁棒性审计、各里程碑实现计划）。

## 状态 / 路线图

- ✅ 监控（作业/改期/公告/出分/新课件）、任务清单（CLI+网页）、手动完成、增量下载、Claude Code 插件（MCP+SessionStart）、会话缓存、运维、往年卷归集/本地检索/积压提醒
- ⏳ 待做：**邮件 / Telegram 通知渠道**（已留可插拔扩展点）

## 安全与隐私

- 密码仅存 macOS 钥匙串；会话 cookie 0600 权限存本机；日志对密码/cookie/OAuth code/学号脱敏。
- 仅访问你自己的账号数据；除课件下载外只发 GET。
- 仅供个人学习使用；下载的课件请勿二次分发。
