# bbwatch 设计文档

> CUHK-SZ Blackboard 任务监控 + 课件下载工具（**Claude Code 插件**）
> 日期：2026-06-28 ｜ 状态：设计已定稿，待评审

## 1. 背景与目标

港中深（CUHK-SZ）的作业主要通过 `bb.cuhk.edu.cn`（Blackboard Learn）布置。痛点：
- 老师在 BB 上布置作业/发公告时**不一定发邮件**，学生容易漏看、忘做。
- 期末复习需要**批量下载课件**，手动一个个下很麻烦。

**目标**：做一个 **Claude Code 插件**。学生每天开始工作时打开 Claude Code，插件即自动扫描 BB、把待办塞到眼前，并维护一个浏览器可看的本地任务清单；需要下课件时跟 Claude 说一声即可。最终便于分发给同学。

### 形态决策（已定）
- **以 Claude Code 插件为唯一形态/入口**，比独立守护进程更轻、天然跨平台、分发简单。
- **不安装任何 OS 级后台代理（不用 launchd/cron）**：扫描只在使用 Claude Code 期间发生（用户已确认接受此取舍）。
- 已知取舍：哪天不开 Claude Code 就不会扫，可能漏当天 ddl。清单页/会话摘要会对"临近 ddl 但最近未扫"做醒目提示来缓解。

### 非目标
- 不做多用户托管服务（不替任何人在服务器上保管学校密码）。
- 不监控学校邮箱收件箱（邮箱本身已推送到手机）。
- v1 优先 macOS；跨平台（Windows/Linux）留到打包分发阶段。

## 2. 可行性结论（已实测验证）

使用本人账号实测，**全部功能可行，且走官方 REST API（无需爬 HTML）**：

- 全校统一身份认证为 **AD FS**（`sts.cuhk.edu.cn`），BB 与邮箱（M365，`@link.cuhk.edu.cn`）共用。
- BB 登录为 **ADFS OAuth2 授权码流**；**账号+密码即可，无 MFA**；脚本可全自动完成。
- 登录后 `/learn/api/public/v1/...` REST API 可直接用网页会话 cookie 调用，返回干净 JSON。
- 课程为 **Classic** 模式，内容是标准文件夹/文件树，附件可下载。

> 传输层坑：anaconda 自带 Python `requests` 直连 ADFS 出现 TLS 握手失败（`UNEXPECTED_EOF`），`curl` 正常。推断为本机代理(Clash 7890)+OpenSSL 组合或 ADFS TLS 指纹。对策见 §5。

## 3. 架构：核心引擎 + 插件外壳

```
engine/  （Python 包：价值与难度都在这里）
  ├─ auth        ADFS OAuth2 登录 → BB 会话；密码存 macOS 钥匙串
  ├─ bbclient    REST API 封装：课程/日历/公告/成绩册/内容树/附件
  ├─ store       SQLite：已知快照 + 任务完成状态 + 上次扫描时间
  ├─ scanner     拉取 → 与上次快照 diff → 产出"新事件"
  ├─ downloader  内容树 → 增量镜像到本地（按 课程/文件夹 结构）
  └─ notifier    可插拔渠道：macOS（v1）/ 邮件 / Telegram（后期）

plugin/  （Claude Code 插件外壳——本工具的入口）
  ├─ .claude-plugin/plugin.json   插件清单
  ├─ hooks/hooks.json             SessionStart → 后台扫描 + 起清单页 + 注入待办摘要
  ├─ commands/                    /bb-setup · /bb-scan · /bb-dashboard · /bb-download
  ├─ mcp/                         scan_now·list_tasks·download_course·mark_done·list_courses·open_dashboard
  └─ skill/                       教 Claude 何时调这些工具（用户问作业/ddl 就查清单）

dashboard  （本地清单服务，绑 127.0.0.1）
  开 Claude Code 时由 SessionStart 起；浏览器查看未完成作业、按 ddl 排序、勾选完成。
  存活期间可按配置间隔自动重扫（即"定时触发"，不依赖任何 OS 常驻代理）。
```

**原则**：引擎是工作机；hooks/commands/mcp/skill/dashboard 都是薄壳。

## 4. 触发模型（对应"定时 / 手动 / 两者结合"）

| 触发 | 机制 | 说明 |
|---|---|---|
| **开工触发** | 插件 `SessionStart` 钩子 | 打开 Claude Code 即后台扫一次 + 注入待办摘要（Claude 开口就报 ddl）+ 确保清单页已起 |
| **手动触发** | `/bb-scan` 或对话 `scan_now`(MCP) | 随时刷新 |
| **定时触发**（可选） | 清单服务进程内的周期扫描循环 | 仅在清单服务存活（=你在用 Claude Code）期间运行；频率每 10min~每天定点可配 |

可在配置里任意叠加（"两者结合"）。**课程黑/白名单**：可排除不想盯的课（体育、已结课）。

## 5. 模块职责与关键技术决策

| 模块/决策 | 选择 | 理由/接口 |
|---|---|---|
| 语言 | **Python** | 探索全程已用其验证；生态完善；MCP 有官方 SDK |
| `engine.auth` | ADFS OAuth2 流，账号密码 POST，会话 cookie 缓存复用、过期自动重登 | `get_session()`；已实测跑通，无 MFA |
| 传输层 | **`curl_cffi`**（浏览器 TLS 指纹的 requests），失败回退子进程 `curl` | 实测原生 requests TLS 失败、curl 正常；并尊重环境代理 |
| `engine.bbclient` | 封装 REST（分页、限速、重试），返回结构化对象 | `list_courses/list_calendar/get_contents/list_announcements/get_columns/get_grade/download_attachment` |
| `engine.store` | **SQLite** 快照比对 + 任务完成状态 + 上次扫描时间 | `snapshot_*/diff_*/mark_done/last_scan` |
| `engine.scanner` | 编排：确保会话 → 遍历在读课程 → 拉取 → diff → 写库 → 触发通知 | `scan() -> list[Event]` |
| `engine.downloader` | 递归内容树，按 文件 id+修改时间 增量镜像 | `mirror(course, dest)` |
| `engine.notifier` | 事件按已启用渠道推送（v1：macOS `osascript`/`terminal-notifier`） | 渠道插件式 |
| 密码存储 | **macOS 钥匙串**（`keyring`）；不落盘明文、不进日志 | 安全；每用户存本机 |
| 插件 hooks | `SessionStart` 运行 `bbwatch session-start`（后台扫描+起清单页+输出 additionalContext 摘要） | 非阻塞，避免拖慢会话 |
| 插件 mcp | Python MCP server（FastMCP），暴露引擎能力 | `scan_now/list_tasks/download_course/mark_done/list_courses/open_dashboard` |
| 清单页 | **本地小服务器**（FastAPI/Flask，127.0.0.1） | 可勾选完成、数据实时、状态回写 DB；内置可选周期扫描 |

## 6. 一次扫描的数据流

```
触发（SessionStart / /bb-scan / scan_now / 周期循环）
 → engine.auth 确保会话（必要时 ADFS 重登）
 → bbclient.list_courses() 取本学期在读课程（按 term + availability + 黑/白名单过滤）
 → 每门课拉取：calendar ddl / gradebook columns / announcements / contents / grades
 → store.diff_*：比对上次快照 → 新作业(新列)/新公告/新课件(新内容)/新成绩
 → 写任务库 + 更新快照 + 记录 last_scan
 → notifier.notify(events)（v1 弹 macOS 通知）；清单页随即刷新
```

## 7. "作业是否完成"的判定（清单页核心）

优先级从高到低：
1. **已出分** → 已完成/已批改。
2. **BB 在线作业**（`resource/x-bb-assignment`）→ 查提交记录，已提交则完成。
3. **无法自动判定**（纸质/线下）→ 清单页**手动勾选**，状态存 DB，扫描器尊重。

展示：未完成按 **ddl 升序**；逾期标红、临近高亮；已完成折叠。对"临近 ddl 但最近未扫"给醒目提示（缓解纯插件不开就不扫的缺口）。

## 8. 安全与"好公民"

- 凭据仅存钥匙串；任何日志/报错不打印密码、token、cookie。
- 清单页仅绑 `127.0.0.1`。
- 扫描温和：合理间隔 + 请求间延时 + 复用会话。
- 定位为"用户用自己的账号访问自己的数据"，合理合规；分发给同学时遵循同样规则。

## 9. 分期计划（按插件形态重排）

- **第一刀（最小可用闭环）**：`engine`(auth/bbclient/store/scanner) + 插件骨架(plugin.json) + `/bb-setup`(账号→钥匙串) + `/bb-scan`(手动) + **本地清单页**(任务/ddl/勾选) + **新作业·ddl & 新公告**检测 + **macOS 桌面通知** + MCP `scan_now`/`list_tasks`。
  → 手动扫一下就能看到任务、收到提醒。
- **第二刀（自动化 + 下载）**：`SessionStart` 钩子（开工自动扫 + 待办摘要注入）+ 清单服务内可选周期扫描 + `downloader` 增量镜像 + MCP `download_course`/`/bb-download` + **新课件上传 & 成绩出分**检测。
  → 完整体验：开 Claude Code 即报 ddl，对话下课件。
- **第三刀（打磨分发）**：**邮件 + Telegram** 渠道 + 课程黑/白名单与频率配置 UI + 正式插件市场打包 + 同学安装文档（含 Windows/Linux）。

> 下载相对独立、即时有用，如想先要可与第一刀对调。

## 10. 已知约束 / 风险

- 不开 Claude Code 当天不扫（已确认接受；靠清单页临近提示缓解）。
- ADFS/BB 改版会影响登录流与接口（已做错误处理与回退，偶尔需维护）。
- MFA 目前关闭；若学校开启或仅校外触发，需改为"半自动"会话复用（保留扩展点）。
- 密码已在本次对话明文出现，建议用户**改密**；正式使用只从钥匙串读取。

## 附录 A：已验证的 BB REST 端点

基址 `https://bb.cuhk.edu.cn`，均需登录后会话 cookie，`Accept: application/json`。

| 用途 | 方法/路径 | 备注 |
|---|---|---|
| 登录入口 | `GET https://sts.cuhk.edu.cn/adfs/oauth2/authorize?response_type=code&client_id=4b71b947-7b0d-4611-b47e-0ec37aabfd5e&redirect_uri=https://bb.cuhk.edu.cn/webapps/bb-SSOIntegrationOAuth2-BBLEARN/authValidate/getCode` | 返回 ADFS 表单(`UserName/Password/Kmsi`)；POST 后 302 回 BB `getCode` 写会话 |
| 当前用户 | `GET /learn/api/public/v1/users/me` | 取 `id`（形如 `_49765_1`） |
| 学期表 | `GET /learn/api/public/v1/terms?limit=100` | `termId → name`（如 `2550UG`） |
| 我的课程 | `GET /learn/api/public/v1/users/{uid}/courses?expand=course&limit=100` | `limit≤100` 需分页；含 `courseRoleId/availability/termId/ultraStatus` |
| 日历(全课程 ddl) | `GET /learn/api/public/v1/calendars/items?since=...&until=...&limit=50` | 窗口 **≤16 周**；返回 `GradebookColumn` 类带 `end`(=ddl) |
| 内容树(顶层) | `GET /learn/api/public/v1/courses/{cid}/contents?limit=50` | `contentHandler.id`：`x-bb-folder/-document/-file/-assignment`；`hasChildren` |
| 内容树(子级) | `GET /learn/api/public/v1/courses/{cid}/contents/{id}/children` | 递归 |
| 附件列表 | `GET /learn/api/public/v1/courses/{cid}/contents/{id}/attachments` | `fileName/mimeType/id` |
| 附件下载 | `GET /learn/api/public/v1/courses/{cid}/contents/{id}/attachments/{aid}/download` | **302** 跳真实文件，需跟随 |
| 公告 | `GET /learn/api/public/v1/courses/{cid}/announcements?limit=5` | `title/created` |
| 成绩册列 | `GET /learn/api/public/v1/courses/{cid}/gradebook/columns?limit=15` | `name/grading.due/id`；新列=新作业信号 |
| 我的成绩 | `GET /learn/api/public/v1/courses/{cid}/gradebook/columns/{colId}/users/{uid}` | `score/status`，出分即可 diff |

## 附录 B：实测数据样本（证明可行）

- 19 门课跨 3 学期；本学期在读如 `MAT3007:Optimization`、`MAT3350:Information Theory`。
- 日历返回带 ddl 的作业项（如 `Homework 4` 截止 `2026-06-30`）。
- `MAT3007` 内容树含 50 个文件（slides 1–10、标注版、tutorial、homework、往年卷），文件名与文件夹结构完整。
- 公告含 `提醒 Assignment 3 (2026-06-23)`、`补课通知` 等带时间戳。
- 成绩册列含 `Homework 1/2/4`，带各自 ddl。
