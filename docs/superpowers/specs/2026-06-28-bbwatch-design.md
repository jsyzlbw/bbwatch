# bbwatch 设计文档

> CUHK-SZ Blackboard 任务监控 + 课件下载工具
> 日期：2026-06-28 ｜ 状态：设计已确认，待评审

## 1. 背景与目标

港中深（CUHK-SZ）的作业主要通过 `bb.cuhk.edu.cn`（Blackboard Learn）布置。问题：
- 老师在 BB 上布置作业/发公告时**不一定发邮件**，学生容易漏看、忘做。
- 期末复习需要**批量下载课件**，手动一个个下很麻烦。

**目标**：做一个跑在学生自己 Mac 上的本地工具，自动监控 BB、发现新任务时主动提醒，并能一键把课件镜像到本地。最终打包成 **Claude Code 插件**便于分发给同学。

### 非目标（本期不做）
- 不做多用户托管服务（不替任何人在服务器上保管学校密码）。
- 不监控学校邮箱收件箱（邮箱本身已会推送到手机；本工具的"邮件"仅作为**输出**提醒渠道，且为后期功能）。
- v1 不保证 Windows/Linux；以 macOS 优先，跨平台留到打包阶段。

## 2. 可行性结论（已实测验证）

使用本人账号实测，**全部功能可行，且走官方 REST API（无需爬 HTML）**：

- 全校统一身份认证为 **AD FS**（`sts.cuhk.edu.cn`），BB 与邮箱（M365，`@link.cuhk.edu.cn`）共用。
- BB 登录为 **ADFS OAuth2 授权码流**；**账号+密码即可，无 MFA**；脚本可全自动完成。
- 登录后，BB 的 `/learn/api/public/v1/...` REST API 可直接用网页会话 cookie 调用，返回干净 JSON。
- 课程为 **Classic** 模式，内容是标准文件夹/文件树，附件可下载。

> 注：实测中 anaconda 自带的 Python `requests` 直连 ADFS 出现 TLS 握手失败（`UNEXPECTED_EOF`），而 `curl` 正常。推断为本机代理(Clash)+OpenSSL 组合或 ADFS TLS 指纹问题。**对策见 §5 传输层**。
> 注：本机存在本地代理 `http://127.0.0.1:7890`，工具需尊重系统/环境代理。

## 3. 形态：核心引擎 + 三个薄壳，打包为插件

```
┌──────────────────── bbwatch 引擎 (Python 包) ────────────────────┐
│ core/                                                            │
│  ├─ auth       ADFS OAuth2 登录 → BB 会话；密码存 macOS 钥匙串      │
│  ├─ bbclient   REST API 封装：课程/日历/公告/成绩册/内容树/附件     │
│  ├─ store      SQLite：已知快照 + 任务完成状态                     │
│  ├─ scanner    拉取 → 与上次快照 diff → 产出"新事件"               │
│  └─ notifier   可插拔渠道：macOS（v1）/ 邮件 / Telegram（后期）    │
│ downloader     内容树 → 增量镜像到本地（按 课程/文件夹 结构）        │
└──────────────────────────────────────────────────────────────────┘
      ▲                       ▲                        ▲
 launchd 定时调用        本地 127.0.0.1 服务         被 Claude 调用
 ┌────┴────┐          ┌───────┴────────┐       ┌──────┴───────┐
 │ 定时扫描器 │          │ 任务清单网页      │       │ MCP server    │
 └─────────┘          └────────────────┘       └──────────────┘
        ↑ 全部打包进 Claude Code 插件（plugin.json + 命令 + MCP）↑
```

**原则**：价值与难度集中在引擎；定时器、网页、MCP 都是薄壳。插件只是分发外壳。

## 4. 模块职责

| 模块 | 职责 | 关键接口/产物 |
|---|---|---|
| `core.auth` | 走 ADFS OAuth2 拿 BB 会话；缓存 cookie，过期自动重登；密码从钥匙串读 | `get_session() -> Session` |
| `core.bbclient` | 封装所有 REST 调用（分页、限速、错误重试） | `list_courses() / list_calendar() / get_contents() / list_announcements() / get_columns() / get_grade() / download_attachment()` |
| `core.store` | SQLite 持久化：每门课的列/公告/内容/成绩快照；任务及其完成状态 | `snapshot_*` / `diff_*` / `mark_done()` |
| `core.scanner` | 编排：确保会话 → 遍历在读课程 → 拉取 → diff → 写库 → 触发通知 | `scan() -> list[Event]` |
| `core.notifier` | 把事件按已启用渠道推送（v1：macOS `terminal-notifier`/`osascript`） | `notify(events)`；渠道为插件式 |
| `downloader` | 递归内容树，按 文件 id+修改时间 增量镜像到 `课程/文件夹/文件` | `mirror(course, dest)` |
| 定时扫描器 | launchd agent，按可配置频率调用 `scanner.scan()` | `~/Library/LaunchAgents/*.plist` |
| 任务清单网页 | 绑 `127.0.0.1` 的轻量本地服务，展示未完成作业、按 ddl 排序、可勾选完成 | FastAPI/Flask + 单页 |
| MCP server | 把引擎能力暴露给 Claude Code：查任务、立即扫描、下课件、标记完成 | `list_tasks / scan_now / download_course / mark_done` |

## 5. 关键技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 语言 | **Python** | 探索全程已用其验证；生态完善 |
| 传输层 | **`curl_cffi`**（带浏览器 TLS 指纹的 requests 风格库），失败回退子进程 `curl` | 实测原生 requests 直连 ADFS TLS 失败、curl 正常；`curl_cffi` 同时规避潜在指纹拦截，并尊重环境代理 |
| 认证 | ADFS OAuth2 授权码流，POST `UserName/Password/Kmsi`，会话 cookie 缓存复用 | 已实测整链路跑通，无 MFA |
| 取数 | 官方 `/learn/api/public/v1/...` + 网页会话 cookie | 干净 JSON，稳定，免爬 HTML |
| 密码存储 | **macOS 钥匙串**（`keyring`）；不落盘明文、不进日志 | 安全；每用户存本机 |
| 状态/diff | **SQLite** 快照比对 | 只提醒"真正新增" |
| 调度 | **launchd**，频率**可配置**（每 10min ~ 每天定点），默认每 2 小时；请求间加小延时 | 当好公民，避免打疼 BB |
| 清单页 | **本地小服务器**（127.0.0.1） | 可勾选完成、数据实时、状态回写 DB |
| 下载 | 内容树 → 附件 `/download`（302 跟随）→ 镜像；id+修改时间增量 | 实测可拿全树与下载链 |

## 6. 一次扫描的数据流

```
launchd 触发
 → core.auth 确保会话（必要时 ADFS 重登）
 → bbclient.list_courses() 取本学期在读课程（按 term + availability 过滤）
 → 对每门课并/串行拉取：calendar ddl / gradebook columns / announcements / contents / grades
 → store.diff_*：与上次快照比对 → 新增列(=新作业)、新公告、新内容(=新课件)、新成绩
 → 写入任务库 + 更新快照
 → notifier.notify(events)（v1：弹 macOS 通知）
```

## 7. "作业是否完成"的判定（清单页核心）

三层判定，优先级从高到低：
1. **已出分** → 已完成/已批改。
2. **BB 在线作业**（内容类型 `resource/x-bb-assignment`）→ 查提交记录，已提交则完成。
3. **无法自动判定**（纸质/线下提交）→ 清单页**手动勾选**，状态存 DB，扫描器尊重该状态。

清单页展示规则：未完成按 **ddl 升序**；逾期标红、临近高亮；已完成折叠。

## 8. 安全与"好公民"

- 凭据仅存钥匙串；任何日志/报错不打印密码、token、cookie。
- 清单页服务仅绑 `127.0.0.1`，外部不可访问。
- 扫描温和：合理间隔 + 请求间延时 + 复用会话，避免被学校 IT 视为异常。
- 定位为"用户用自己的账号访问自己的数据"，合理合规；分发给同学时遵循同样规则。

## 9. 分期计划

- **MVP（第一刀）**：`core.auth` + `bbclient` + `store` + `scanner` + 定时扫描 + **新作业/ddl & 新公告**两类提醒 + **任务清单网页** + **macOS 桌面通知** + **一键增量镜像下载**。
- **第二刀**：**邮件 + Telegram** 渠道；**新课件上传 & 成绩出分**两类提醒；**MCP server**（对话式操作）。
- **第三刀**：打包为正式 **Claude Code 插件** + 同学安装文档（含 Windows 支持）。

## 10. 已知约束 / 风险

- Mac 需开机/唤醒时 launchd 才会跑（可选配置唤醒执行）。
- ADFS/BB 改版会影响登录流与接口（已做错误处理与回退，但需偶尔维护）。
- MFA 目前关闭；若学校后续开启（或仅校外触发），需改为"半自动"会话复用（保留扩展点）。
- 密码已在本次对话明文出现，建议用户**改密**；正式使用时只从钥匙串读取。

## 附录 A：已验证的 BB REST 端点

基址 `https://bb.cuhk.edu.cn`，均需登录后的会话 cookie，`Accept: application/json`。

| 用途 | 方法/路径 | 备注 |
|---|---|---|
| 登录入口 | `GET https://sts.cuhk.edu.cn/adfs/oauth2/authorize?response_type=code&client_id=4b71b947-7b0d-4611-b47e-0ec37aabfd5e&redirect_uri=https://bb.cuhk.edu.cn/webapps/bb-SSOIntegrationOAuth2-BBLEARN/authValidate/getCode` | 返回 ADFS 表单(`UserName/Password/Kmsi`)；POST 后 302 回 BB `getCode` 写会话 |
| 当前用户 | `GET /learn/api/public/v1/users/me` | 取 `id`（形如 `_49765_1`） |
| 学期表 | `GET /learn/api/public/v1/terms?limit=100` | `termId → name`（如 `2550UG`） |
| 我的课程 | `GET /learn/api/public/v1/users/{uid}/courses?expand=course&limit=100` | `limit≤100` 需分页；含 `courseRoleId / availability / termId / ultraStatus` |
| 日历(全课程 ddl) | `GET /learn/api/public/v1/calendars/items?since=...&until=...&limit=50` | 窗口 **≤16 周**；返回 `GradebookColumn` 类带 `end`(=ddl) |
| 内容树(顶层) | `GET /learn/api/public/v1/courses/{cid}/contents?limit=50` | `contentHandler.id`：`x-bb-folder/-document/-file/-assignment`；`hasChildren` |
| 内容树(子级) | `GET /learn/api/public/v1/courses/{cid}/contents/{id}/children` | 递归 |
| 附件列表 | `GET /learn/api/public/v1/courses/{cid}/contents/{id}/attachments` | `fileName / mimeType / id` |
| 附件下载 | `GET /learn/api/public/v1/courses/{cid}/contents/{id}/attachments/{aid}/download` | **302** 跳真实文件，需 `-L` 跟随 |
| 公告 | `GET /learn/api/public/v1/courses/{cid}/announcements?limit=5` | `title / created` |
| 成绩册列 | `GET /learn/api/public/v1/courses/{cid}/gradebook/columns?limit=15` | `name / grading.due / id`；新列=新作业信号 |
| 我的成绩 | `GET /learn/api/public/v1/courses/{cid}/gradebook/columns/{colId}/users/{uid}` | `score / status`，出分即可 diff |

## 附录 B：实测数据样本（证明可行）

- 抓到 19 门课跨 3 学期；本学期在读如 `MAT3007:Optimization`、`MAT3350:Information Theory`。
- 日历返回带 ddl 的作业项（如 `Homework 4` 截止 `2026-06-30`）。
- `MAT3007` 内容树含 50 个文件（slides 1–10、标注版、tutorial、homework、往年卷），文件名与文件夹结构完整可得。
- 公告含 `提醒 Assignment 3 (2026-06-23)`、`补课通知` 等，带时间戳。
- 成绩册列含 `Homework 1/2/4`，带各自 ddl。
