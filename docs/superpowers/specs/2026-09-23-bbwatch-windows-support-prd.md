# bbwatch Windows Support PRD

## 1. 文档信息

- **项目名称**：bbwatch

- **开发分支**：`feat/windows-support`

- **目标仓库**：`dominictpl11/bbwatch`

- **上游仓库**：`jsyzlbw/bbwatch`

- **文档类型**：Product Requirements Document

- **主要目标平台**：Windows 10 / Windows 11

- **主要使用环境**：

  - Cursor

  - Claude Code

  - Codex

  - PowerShell

  - Python 3.11+

- **目标学校**：香港中文大学（深圳）CUHK-Shenzhen

- **目标系统**：CUHK-SZ Blackboard

---

# 2. 项目背景

bbwatch 是一个面向 CUHK-SZ Blackboard 的课程管理与课件下载工具。

现有项目已经支持：

- Blackboard 登录

- 课程读取

- 作业与 Deadline 扫描

- 公告与课程更新监控

- 成绩与待批改状态读取

- 课件批量下载

- 本地 SQLite 数据缓存

- 课程资料检索

- 网页 Dashboard

- MCP Server

- Claude Code 插件

- Codex 插件

- 基于自然语言调用 Blackboard 功能

当前项目的核心 Python 引擎具有较好的跨平台基础，但安装、环境初始化、通知、路径及部分系统集成功能主要围绕 macOS / Linux 设计。

Windows 用户目前无法获得完整、稳定、低门槛的原生体验。

本 fork 的第一阶段目标，是在尽量不修改 Blackboard 核心逻辑的前提下，将 bbwatch 完整适配 Windows，并建立后续持续开发的基础。

---

# 3. 产品目标

## 3.1 核心目标

让 Windows 用户可以在本机完整使用 bbwatch，并能够直接通过 Claude Code、Codex、Cursor 等 AI Coding Agent 管理 CUHK-SZ Blackboard。

最终希望用户可以直接通过自然语言完成：

```
帮我看看这周还有什么作业。扫描一下 Blackboard 有没有新东西。CSC4303 最近上传了什么课件？把 CSC4303 的所有课件下载下来。帮我把这学期所有课程的 PPT 下载到本地。最近有没有老师修改 Deadline？哪些作业我已经交了但还没有出分？
```

而不需要用户手动频繁访问 Blackboard 页面。

---

## 3.2 第一阶段目标

第一阶段重点为：

> **Windows Native Support**

需要实现：

1. Windows 原生安装

2. Windows Python 虚拟环境支持

3. Windows 路径兼容

4. Windows Credential Manager 凭据存储

5. Windows MCP 启动

6. Claude Code Windows 支持

7. Codex Windows 支持

8. Windows 系统通知

9. Windows 代理环境兼容

10. Windows 下完整测试 Blackboard 登录、扫描和下载流程

---

# 4. 非目标

第一阶段暂不重点开发以下功能：

- 不重写 Blackboard 登录协议

- 不替换现有 Blackboard 抓取逻辑

- 不重新设计 Dashboard

- 不开发 Android / iOS App

- 不开发云端账号系统

- 不将 Blackboard 数据上传云端

- 不实现多人共享 Blackboard 数据

- 不自动提交 Blackboard 作业

- 不自动修改 Blackboard 成绩或课程内容

- 不建立公共 Blackboard 代理服务

- 不绕过学校 MFA、验证码或安全机制

第一阶段优先原则：

> **先让现有功能在 Windows 上完整稳定运行，再增加新功能。**

---

# 5. 用户画像

## 5.1 核心用户

CUHK-SZ 在校学生，特点包括：

- 使用 Windows 作为主要开发环境

- 使用 Blackboard 获取课程资料

- 使用 Cursor / VS Code

- 使用 Claude Code / Codex 等 AI Coding Agent

- 希望减少手动进入 Blackboard 的次数

- 希望批量下载课程资料

- 希望统一管理 Assignment / Deadline / Announcement

- 具有一定命令行使用能力

---

## 5.2 典型用户环境

```
OS:Windows 11Terminal:PowerShellEditor:CursorAI Agents:Claude CodeCodexPython:Python 3.11+Git:Git for WindowsBlackboard:https://bb.cuhk.edu.cn
```

---

# 6. 用户痛点

目前 CUHK-SZ Blackboard 存在以下典型使用痛点：

### 6.1 课件下载效率低

用户需要：

```
打开 Blackboard→ 进入课程→ 找到 Course Content→ 进入对应文件夹→ 手动点击 PPT / PDF→ 逐个下载
```

当一门课存在大量 Lecture Slides 时效率较低。

---

### 6.2 多课程信息分散

用户可能同时有 5–8 门课程。

作业、公告、Deadline、课件分散在不同课程页面内。

---

### 6.3 Blackboard 更新不明显

老师可能：

- 新增作业

- 修改 Deadline

- 上传新课件

- 发布公告

- 更新成绩

但用户不一定及时注意到。

---

### 6.4 AI 无法直接读取 Blackboard

Claude Code / Codex 本身无法直接理解用户 Blackboard 中的数据。

需要通过 MCP / CLI 提供结构化接口。

---

### 6.5 Windows 缺乏完整支持

当前项目中存在诸如：

```
.venv/bin/python~/.local/bin/bootstrap.shosascriptUnix symlink
```

等 Unix / macOS 假设。

这使 Windows 用户无法获得完整原生体验。

---

# 7. 产品设计原则

## 7.1 Local First

所有课程数据优先保存在本机。

包括：

- Blackboard Session

- 作业缓存

- 课程列表

- 下载记录

- 配置

- 日志

默认不得上传第三方服务器。

---

## 7.2 Password Never Enters AI Context

用户密码不得：

- 写入 Prompt

- 出现在 Claude Code 对话中

- 出现在 Codex 对话中

- 写入 Git 仓库

- 写入日志

密码必须通过本机安全凭据系统保存。

Windows 默认使用：

```
Windows Credential Manager
```

---

# 8. 功能需求

# 8.1 Windows 安装器

## 需求

提供 Windows 原生安装方式。

建议优先实现：

```
scripts/install_windows.py
```

或将现有：

```
scripts/install_codex.py
```

重构为真正跨平台安装器。

---

## Windows 安装流程

理想流程：

```
git clone https://github.com/dominictpl11/bbwatch.gitcd bbwatchpy scripts/install_windows.py
```

安装器自动完成：

```
检测 Python↓检测 Python >= 3.11↓创建 bbwatch runtime↓创建 venv↓安装 dependencies↓安装 bbwatch↓配置 CLI↓配置 MCP↓检测 Codex / Claude Code↓输出下一步操作
```

---

## 验收标准

Windows 用户无需：

- 修改源码

- 手动改 `.mcp.json`

- 手动寻找 Python executable

- 使用 WSL

即可安装 bbwatch。

---

# 8.2 跨平台 Virtual Environment

目前 Unix：

```
.venv/bin/python.venv/bin/pip.venv/bin/bbwatch
```

Windows 应为：

```
.venv\Scripts\python.exe.venv\Scripts\pip.exe.venv\Scripts\bbwatch.exe
```

需要统一封装获取逻辑。

建议增加：

```
def venv_python(venv: Path) -> Path:    if os.name == "nt":        return venv / "Scripts" / "python.exe"    return venv / "bin" / "python"
```

类似处理：

```
pipbbwatchbbwatch-mcp
```

---

# 8.3 Windows CLI

Windows 用户应可以直接运行：

```
bbwatch setupbbwatch whoamibbwatch scanbbwatch tasksbbwatch coursesbbwatch download CSC4303bbwatch dashboard
```

不应要求长期使用：

```
python -m bbwatch...
```

---

# 8.4 Windows Credential Manager

继续使用 Python：

```
keyring
```

Windows 后端应自动使用：

```
Windows Credential Manager
```

需要验证：

```
store_credentials()load_credentials()clear_credentials()
```

在 Windows 正常运行。

---

## 凭据要求

密码：

- 不存储为明文

- 不保存到 `.env`

- 不保存到 config.toml

- 不保存到日志

- 不输出到 stdout

---

# 8.5 Claude Code Windows 支持

现有初始化依赖：

```
bootstrap.sh
```

Windows 应增加：

```
bootstrap.ps1
```

或重构成：

```
bootstrap.py
```

推荐最终方案：

```
bootstrap.py
```

由 Python 统一处理：

```
WindowsmacOSLinux
```

避免维护：

```
bootstrap.shbootstrap.ps1
```

两套逻辑。

---

# 8.6 Codex Windows 支持

Codex 安装器目前存在 Unix 路径假设。

需要修复：

```
~/.local/share~/.local/binvenv/binsymlink
```

Windows 应使用更合理的数据目录，例如：

```
%LOCALAPPDATA%\bbwatch
```

或：

```
%USERPROFILE%\.bbwatch
```

建议：

### 用户数据

```
%USERPROFILE%\.bbwatch
```

### Runtime

```
%LOCALAPPDATA%\bbwatch\runtime
```

### Plugin

遵循 Codex 实际 Windows 插件目录。

---

# 8.7 MCP 支持

需要确保 Windows MCP Server 可以启动：

```
python -m bbwatch.mcp_server
```

Claude Code / Codex 的 MCP 配置应引用正确 Windows Python：

```
C:\Users\<user>\...\python.exe
```

避免写死：

```
.venv/bin/python
```

---

# 8.8 Cursor 支持

Cursor 本身不需要独立 Blackboard Client。

主要支持方式：

```
Cursor ├─ Claude Code ├─ Codex └─ MCP
```

项目需要确保：

- Cursor Terminal 中 CLI 正常运行

- Claude Code 可以调用 bbwatch

- Codex 可以调用 bbwatch

后续可以考虑增加：

```
.cursor/
```

相关配置，但第一阶段不是必须。

---

# 8.9 Windows Notification

现有：

```
MacNotifier
```

使用：

```
osascript
```

Windows 需要新增：

```
WindowsNotifier
```

用于：

```
新作业Deadline 修改新公告新成绩新课件
```

建议优先采用：

- Windows Toast Notification

同时保留：

```
MacNotifierLinuxNotifier
```

最终统一：

```
get_default_notifier()
```

---

# 8.10 Proxy Support

用户可能使用：

- Clash Verge

- FlClash

- v2rayN

- 系统代理

- HTTP Proxy

- SOCKS Proxy

需要支持：

```
HTTP_PROXYHTTPS_PROXYALL_PROXYNO_PROXY
```

同时考虑 Windows 系统代理。

代理规则：

```
优先环境变量↓系统代理↓直连
```

如果检测到：

```
127.0.0.1:<port>
```

但端口不可用，则自动直连。

---

# 8.11 Blackboard 登录

保持现有逻辑。

流程：

```
load credentials↓load saved session↓verify session↓session valid    → reuse↓session invalid↓ADFS login↓save session
```

Windows 适配期间原则上不主动改登录协议。

---

# 8.12 Course Scan

支持：

```
课程列表作业公告成绩课程内容附件Deadline
```

扫描后写入本地数据库。

---

# 8.13 Incremental Scan

再次扫描时只产生变化事件。

例如：

```
NEW_ASSIGNMENTDEADLINE_CHANGEDNEW_ANNOUNCEMENTGRADE_CHANGEDNEW_MATERIAL
```

避免每次重新通知所有旧信息。

---

# 8.14 Course Material Download

必须支持：

```
bbwatch download CSC4303
```

并保持 Blackboard 文件夹结构。

例如：

```
Downloads/└── bbwatch/    └── CSC4303/        ├── Lecture Slides/        │   ├── Week01.pdf        │   ├── Week02.pdf        │   └── Week03.pdf        ├── Tutorials/        └── Assignments/
```

---

# 8.15 Incremental Download

已经下载的文件默认不重新下载。

需要根据：

- URL

- Blackboard Content ID

- 文件大小

- 文件名

- Modified 信息

尽可能判断文件是否发生变化。

---

# 8.16 Download All Courses

后续增加：

```
bbwatch download --all
```

用于一键同步本学期课程资料。

---

# 8.17 Download Filtering

后续支持：

```
bbwatch download CSC4303 --type pdf
```

或：

```
只下载 PPT只下载 PDF只下载 lecture slides忽略 assignment submission
```

---

# 8.18 File Search

保持：

```
bbwatch find slides
```

并支持关键词查询：

```
lecturemidtermfinaltutorialassignmentweek 5
```

---

# 8.19 Dashboard

现有 Dashboard 第一阶段保持。

启动：

```
bbwatch dashboard
```

访问：

```
http://127.0.0.1:8765
```

必须确保 Windows 可以正常：

- 启动

- 扫描

- 查看任务

- 标记完成

- 查看状态

---

# 9. AI Agent 需求

# 9.1 MCP Tools

至少保留现有：

```
get_statuslist_taskslist_pendingmark_task_donescan_nowlist_coursesdownload_coursefind_materials
```

---

# 9.2 AI Tool Safety

工具分两类：

### Read Operations

例如：

```
list_courseslist_tasksfind_materials
```

可以直接执行。

### Local Write Operations

例如：

```
download_coursemark_task_done
```

只影响：

```
用户本地文件 / 本地数据库
```

---

# 9.3 Blackboard Write Operations

第一阶段不允许：

```
自动提交作业删除内容修改课程信息发送 Blackboard 邮件修改 Blackboard 数据
```

---

# 10. 推荐架构

```
                    User                     │                     ▼         ┌─────────────────────┐         │ Claude / Codex      │         │ Cursor              │         └──────────┬──────────┘                    │                    ▼              MCP Server                    │                    ▼              bbwatch Core     ┌──────────────┼──────────────┐     │              │              │     ▼              ▼              ▼ Blackboard      SQLite         Downloader Client          Store     │     ▼ CUHK-SZ Blackboard
```

---

# 11. 模块职责

```
src/bbwatch/
```

### `auth.py`

Blackboard / ADFS 登录。

### `bbclient.py`

Blackboard HTTP Client。

### `scanner.py`

课程数据扫描。

### `downloader.py`

课件下载。

### `session.py`

Session Cookie 缓存。

### `secrets.py`

密码安全存储。

### `proxy.py`

代理配置。

### `notifier.py`

系统通知。

### `config.py`

本地配置。

### `mcp_server.py`

AI Agent MCP 接口。

### `cli.py`

命令行入口。

---

# 12. Windows 重构建议

建议新增：

```
src/bbwatch/platform.py
```

集中处理 OS 差异。

例如：

```
def is_windows()def is_macos()def is_linux()def get_venv_python()def get_venv_bin_dir()def get_runtime_dir()def get_default_download_dir()
```

避免代码中到处出现：

```
if os.name == "nt":
```

---

# 13. 推荐开发阶段

# Phase 1 — Windows CLI MVP

目标：

```
Windows 上 bbwatch CLI 可以正常运行
```

任务：

- 修复 venv 路径

- 修复 executable 路径

- 测试 keyring

- 测试 config

- 测试 session

- 测试登录

- 测试 scan

- 测试 download

验收：

```
bbwatch setupbbwatch whoamibbwatch scanbbwatch coursesbbwatch download CSC4303
```

全部成功。

---

# Phase 2 — Claude Code Windows

目标：

Claude Code 可以直接调用 bbwatch。

任务：

- 替换 / 补充 `bootstrap.sh`

- Windows MCP 配置

- Windows 初始化

- 插件安装测试

验收：

用户可以直接说：

```
看看 Blackboard 有没有新作业。
```

Claude Code 能调用 bbwatch。

---

# Phase 3 — Codex Windows

目标：

Codex 可以直接调用 bbwatch。

任务：

- 改造 `install_codex.py`

- Windows runtime path

- Windows CLI entry

- MCP registration

- plugin registration

---

# Phase 4 — Windows Notification

实现：

```
Windows Toast
```

用于提醒：

```
新作业新 Deadline新课件成绩更新
```

---

# Phase 5 — Download Improvements

实现：

```
download --all增量同步文件过滤更稳定的目录结构下载失败重试
```

---

# Phase 6 — Blackboard Assistant

逐步增加：

```
“我这周最急的任务是什么？”“CSC4303 新增了哪些文件？”“哪些课程最近更新最多？”“帮我整理本周 Blackboard 更新。”
```

---

# 14. 测试要求

## Unit Tests

要求至少覆盖：

```
platform pathsconfigcredentialssessionproxydownloaderscannerMCP
```

---

## Windows CI

建议增加 GitHub Actions：

```
windows-latest
```

同时保留：

```
macos-latestubuntu-latest
```

测试 Python：

```
3.113.12
```

---

# 15. 安全要求

必须确保以下内容永远不会进入 Git：

```
Blackboard usernameBlackboard passwordCookieSessionCourse filesStudent IDGradesPrivate course content
```

建议 `.gitignore` 包括：

```
.bbwatch/.env*.cookiesession*Downloads/
```

---

# 16. Logging

日志不得包含：

```
passwordfull cookieauthorization headersession token
```

允许记录：

```
request target hostHTTP statuscourse idcontent iddownload patherror category
```

---

# 17. 错误处理

错误必须尽量给用户明确原因。

例如：

### 网络问题

```
无法连接 Blackboard，请检查网络或代理。
```

### 密码错误

```
Blackboard 登录失败，请重新运行 bbwatch setup。
```

### Session 失效

自动重新登录。

### Python 版本过低

```
Python 3.11 or newer is required.
```

### 下载失败

应指出：

```
课程文件URL / Content ID失败原因
```

---

# 18. UX 目标

安装后用户不应该需要理解：

```
MCPvirtualenvcookieBlackboard endpointSQLiteADFS
```

理想体验：

```
安装一次↓输入账号↓开始使用
```

之后主要交互：

```
自然语言
```

---

# 19. 成功指标

Windows 版本完成后，应达到：

### 安装

全新 Windows 环境在合理步骤内完成安装。

### 登录

无需浏览器 Cookie 手动复制。

### 使用

用户可以通过自然语言：

```
查课程查作业查 Deadline扫描更新下载课件搜索课件
```

### 稳定性

正常使用不需要：

```
修改源码手动改 MCP JSON手动调整 Python 路径使用 WSL
```

---

# 20. MVP Definition of Done

Windows MVP 完成必须同时满足：

- Windows 10 / 11 可运行

- Python 3.11+ 支持

- Windows venv 正常创建

- `bbwatch setup` 正常

- Credential Manager 正常

- `bbwatch whoami` 正常

- Blackboard 登录正常

- Session 缓存正常

- `bbwatch scan` 正常

- `bbwatch tasks` 正常

- `bbwatch courses` 正常

- `bbwatch download <course>` 正常

- 下载目录 Windows 兼容

- Dashboard 正常

- MCP Server 正常

- Claude Code 可以调用

- Codex 可以调用

- 不需要 WSL

- 不泄露密码 / Cookie

- 原有 macOS 功能不被破坏

- 自动化测试通过

---

# 21. 后续长期方向

完成 Windows 支持以后，可以进一步发展为：

## Blackboard Sync

类似：

```
Google Drive / OneDrive Sync
```

自动把 Blackboard 课程资料同步到本机。

---

## Semester Archive

按照：

```
2026-Fall/├── CSC4107/├── CSC4303/├── CSC3150/└── ECO3121/
```

自动归档。

---

## AI Course Assistant

让 Agent 可以结合本地课件：

```
Blackboard+Downloaded Slides+Assignments+Course Notes
```

回答：

```
这周 CSC4303 教了什么？这个 Assignment 对应哪几份课件？帮我复习明天考试涉及的 slides。
```

---

## Calendar Integration

未来可将 Blackboard Deadline 同步到：

```
Google Calendar
```

实现：

```
Blackboard Assignment        ↓      bbwatch        ↓Google Calendar Event
```

---

# 22. 最终愿景

bbwatch 不仅是一个 Blackboard Downloader。

长期目标可以定义为：

> **面向 CUHK-SZ 学生的本地 AI Academic Assistant。**

Blackboard 成为数据源，而不是用户每天需要手动打开和翻找的网页。

最终用户只需要告诉 AI：

```
今天学校有什么事情需要我处理？
```

系统即可综合：

```
Blackboard课程作业Deadline公告成绩课件本地学习进度
```

给出结构化结果。

第一步，就是让这一整套能力在 Windows 上稳定、原生地运行。
