# bbwatch M3（补全：下载 / 插件外壳+MCP / SessionStart / 网页 / 会话缓存 / 运维 / 可选）计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。TDD、每任务一提交。深规格见 [详细设计](../specs/2026-06-28-bbwatch-detailed-design.md) §9/§10/§11/§6 + 附录 C。
> **本阶段不做：** 邮件 / Telegram 通知渠道（留下一阶段）。

**Goal:** 把 bbwatch 从"纯 CLI 引擎"补全为一个真正的 **Claude Code 插件**：能批量增量下课件、开 Claude Code 自动扫并把待办塞进对话、浏览器看可勾选清单、会话复用更省事，并具备运维与可选增强。

承自 M1/M2：auth/transport/bbclient/store/diff/scanner/notifier/cli 已就绪，51 测试绿。

---

## 阶段 A —— 课件增量镜像下载（硬需求 #2，优先）

**A1 传输层支持二进制下载**
- `transport.py`：`Transport` 协议加 `download_to(url, path) -> int`（跟随 302，流式写盘，返回字节数）；`CurlCffiTransport` 用 `stream`/`content` 实现；`FakeTransport` 按路由写入预置 bytes。
- DoD：FakeTransport.download_to 写出预置内容；测试断言文件存在且内容正确。

**A2 模型 + bbclient 内容/附件**
- `models.py`：`Content(id,title,handler,has_children,created,modified)`、`Attachment(id,file_name,mime_type)`。
- `bbclient.py`：`list_contents(cid)`(顶层)、`list_content_children(cid,content_id)`、`list_attachments(cid,content_id)`、`download_attachment(cid,content_id,att_id,path)`。分页。
- `walk_contents(cid) -> Iterator[(path_parts, Content)]`：递归整棵树（深度/环守卫）。
- DoD：fixtures 造两层树 + 附件；测试断言递归遍历顺序与附件解析。

**A3 downloads 注册表 + 增量判定**
- `store/schema.sql`：表 `download(att_key TEXT PK, course_id, local_path, src_modified_utc, size, status, updated_at)`（schema_version→2 + 迁移）。
- `store.py`：`need_download(att_key, src_modified, size)->bool`、`record_download(...)`。
- DoD：首次需下、相同 modified+size 不重下、modified 变化重下。

**A4 downloader**
- `downloader.py`：`mirror(client, store, course, dest, *, now) -> MirrorResult(downloaded,skipped,failed)`。
  - 遍历 `walk_contents`，对每个 attachment：本地路径 = `dest/课程可读名/文件夹层级/原文件名`（非法字符替换、重名加序号）；`need_download` 为真则 `download_attachment` 到 `.part` 再 `os.replace` 原子落盘 + `record_download`；否则 skip。
- DoD（FakeTransport+内存 store）：首次全下、二次全 skip、改 modified 后只下该文件、非法文件名净化、原子 .part。

**A5 CLI**
- `cli.py`：`bbwatch courses`（列在读课，编号）、`bbwatch download <编号|课程代码> [--dest 目录]`（默认 `~/Downloads/bbwatch/`）。
- DoD：`run_download(client, store, course, dest, now)` 可注入测试；CLI 冒烟。

**A6 新课件上传检测（监控）**
- scanner 增加 `contents` 维度：对 `walk_contents` 的 content `id` 做全量 diff（新 id→`new_material`；已知项 `modified` 变→`material_updated`），entity_key=`content:{cid}:{contentid}`，dedup variant=modified。冷启动静默 + 基线维度 `contents`。
- DoD：首扫静默；新内容→1 new_material；modified 变→material_updated；重复→0。

---

## 阶段 B —— Claude Code 插件外壳 + MCP（让它成为"插件"）

**B1 MCP server**
- `mcp_server.py`：用 stdlib JSON-RPC over stdio 实现最小 MCP（或 mcp SDK 若可用）。工具：`scan_now`、`list_tasks`、`mark_task_done(n,done)`、`list_courses`、`download_course(course,dest)`。每工具内部复用 cli 的 run_* 装配。
- DoD：对 server 的工具分发函数做单测（注入 fake engine），断言入参→出参 JSON。

**B2 插件清单与命令**
- `.claude-plugin/plugin.json`（name/description/version/author/mcpServers 指向 mcp_server）。
- `commands/`：`bb-setup.md / bb-scan.md / bb-tasks.md / bb-download.md`（用 `${CLAUDE_PLUGIN_ROOT}` 调引擎 CLI）。
- `.claude-plugin/marketplace.json`（分发）。
- DoD：JSON 合法（json.load 测试）；命令 md 含 frontmatter。

**B3 SessionStart 钩子**
- `hooks/hooks.json`：SessionStart → `scripts/session_start.py`（`async`/后台），后台触发一次 scan，并向 stdout 输出 `additionalContext`：Top-N 临近 ddl + 本次新事件计数 + "最近未扫"提示。
- `scripts/session_start.py`：调引擎，超时保护，**绝不阻塞会话**，输出脱敏。
- DoD：`build_session_summary(store, now) -> str` 纯函数单测（Top-N、未扫提示、无内容时简洁）。

**B4 bb-assistant skill**
- `skills/bb-assistant/SKILL.md`：描述"用户问作业/ddl/下课件时调用 bbwatch MCP 工具"。
- DoD：frontmatter 含 name/description。

---

## 阶段 C —— 会话缓存 + 失效重放 + 认证熔断（附录 C.3）

**C1 会话持久化**：`auth.py`/`transport.py`：导出/导入 cookie 到 `~/.bbwatch/session`（0600 原子写）；启动复用。
**C2 请求级失效重放**：bbclient 检测 401/被重定向登录页 → `auth.login` 一次 + 重放原请求；二次失败抛 `SessionRefreshError`。
**C3 熔断**：`auth_state(circuit_open_until,fail_count)` 表持久化；连续凭据失败=3→熔断 1h；熔断期 `get_session` 抛 `AuthCircuitOpenError`；429/网络不计数。
- DoD：FakeTransport 脚本化 401→重放成功；连续失败→熔断；熔断期拒绝。

---

## 阶段 D —— 本地任务清单网页（127.0.0.1）

- `dashboard/server.py`：stdlib `http.server`，仅绑 127.0.0.1；路由 `GET /`(HTML 清单)、`GET /api/tasks`(actionable_tasks JSON)、`POST /api/done`(entity_key,done)、`POST /api/scan`(触发)。
- `dashboard/index.html`：清单 + 勾选框(调 /api/done) + "距上次扫描 Xh / 临近未扫"横幅 + 逾期/紧急高亮。
- `cli.py`：`bbwatch dashboard [--port]`（起服务 + 打开浏览器）。
- DoD：对路由处理函数单测（注入 store）：GET tasks JSON、POST done 改库、端口冲突回退。

---

## 阶段 E —— 运维 / 配置 / 归档

- **E1 配置文件** `~/.bbwatch/config.toml`：scan 频率、课程黑/白名单、下载目录、清单端口、SessionStart 自动扫开关。`config.py` 读+默认；scanner/cli 应用（current_terms 自动 + 白/黑名单）。
- **E2 schema 迁移框架**：`store` 按 `meta.schema_version` 顺序跑迁移（已用于 A3 的 v2、C3 的 v3）。
- **E3 `bbwatch doctor`**：查钥匙串/会话/代理/DB/端口/osascript。
- **E4 `bbwatch uninstall`**：清钥匙串凭据 + 会话 + （询问）DB/下载；停服务。
- **E5 旧逾期归档**：scanner 收尾把"逾期超 N 周(默认 4)且未完成"的 column 置 `archived=1`（仍留 diff 锚，不影响去重）；清单不再显示。
- DoD：各自单测（配置解析、迁移幂等、归档阈值、doctor 各检查项布尔）。

---

## 阶段 F —— 可选增强

- **F1 往年卷归集**：下载时按关键词(past/exam/midterm/final/真题/年份)把命中文件软链/复制到 `dest/_exams/`。
- **F2 本地课件检索** `bbwatch find <kw>`：查 `download` 表 local_path/文件名/课程，返回路径，不联网。
- **F3 iCal 导出** `bbwatch ical [--out a.ics]`：未完成 column 的 due → VEVENT。
- **F4 公告考试信息抽取**：清单/摘要里对含 期中/补课/座位/exam 关键词的公告高亮（轻量正则，不引入 LLM 依赖）。
- **F5 待批积压提醒**：`NeedsGrading` 超 N 天未变 Graded → 清单提示（本地计时）。
- DoD：各自纯函数单测。

---

## 全程不变量（沿用附录 C / M2）
全量 diff、稳定 id、同事务、冷启动静默、complete 闸门、dedup 单一来源、UTC 存+8 展示、凭据/cookie/code 脱敏、只读(除附件下载 302 跟随)。每阶段并入后跑全量 `pytest -q && ruff check`。

## Self-Review
- 覆盖：A=下载+新课件；B=插件形态+MCP+SessionStart；C=会话鲁棒；D=网页；E=运维/配置/归档；F=可选。邮件/TG 显式排除。
- 顺序：A→B 让"插件+下载"先可用；C/D 增强；E/F 收尾。各阶段独立可交付、可单测。
