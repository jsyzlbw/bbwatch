# bbwatch 详细设计文档

> CUHK-SZ Blackboard 任务监控 + 课件下载工具（**Claude Code 插件**）
> 日期：2026-06-28 ｜ 状态：详细设计（多代理起草 + 对抗式评审 + 鲁棒性审计）
> 配套：概要设计见 [2026-06-28-bbwatch-design.md](2026-06-28-bbwatch-design.md)；BB 实测接口见其附录 A。

## 目录

- 1. 概述、目标与范围
- 2. 功能清单
- 3. 总体架构与代码结构
- 4. 数据模型与 SQLite Schema
- 5. 鲁棒性: 绝不漏、绝不重复(核心)
- 6. 认证与会话管理
- 7. BB REST 客户端
- 8. 扫描编排与 Diff 算法
- 9. Claude Code 集成
- 10. 本地任务清单(前端)
- 11. 课件增量镜像下载
- 12. 配置、首次设置与通知渠道
- 13. 测试、验证与分阶段开发计划
- 附录 A: 鲁棒性对抗审计
- 附录 B: 完整性复核与待决问题
- 附录 C: 审核定稿决议（实现前以本节为准）


---

## 1. 概述、目标与范围

> 本章为 bbwatch 工程设计文档的开篇,定义"我们在解决什么问题、解决到什么程度、不碰什么、用什么词、按什么红线行事、怎样判定成功"。所有涉及 Blackboard(下称 BB)的断言均对应本人账号(CUHK-SZ,userId `_49765_1`)的只读实测,事实依据见 `/private/tmp/.../scratchpad/bb_findings.md`,既有设计稿见 `/Users/mac/Programming/cuhkszbb/docs/superpowers/specs/2026-06-28-bbwatch-design.md`。

### 1.1 背景与痛点

港中深(CUHK-SZ)的课程作业、公告、课件主要通过 `https://bb.cuhk.edu.cn`(Blackboard Learn,课程均为 **Classic** 模式)下发。真实使用中存在三个反复出现的痛点:

1. **布置不一定推送**。老师在 BB 上新建作业(成绩册栏目)或发公告时,并不一定触发邮件/手机推送;学生若不主动登录,容易漏看、忘做。实测公告 `提醒 Assignment 3 (2026-06-23)`、`补课通知`、`期中座位表`、`Assignment 1 Grade Release` 等关键信息只存在于 BB,正文(`announcements[].body`)里常夹带考试/补课/座位安排。
2. **截止时间分散且需手算时区**。截止时间的权威来源是成绩册栏目的 `grading.due` 字段,格式形如 `2026-06-30T15:59:00.000Z`,是 **UTC**;学生需在 17 门在读课程(本人 19 门 membership 跨 3 学期,其中 17 门 role=Student 且 `availability.available` ∈ {`Yes`, `Term`})之间逐课翻看,并自行 +8 小时换算东八区,极易看错。
3. **期末批量取课件费力**。课件以标准文件夹/文件树组织(实测 `MAT3007` 50 个文件:slides1–10、标注版、tutorial、homework、往年卷,多层目录;`MAT3350` 13 个讲义),手动逐个点击 302 下载链接保存非常繁琐;且新上传/更新(`contents[].modified` 变化)无从一眼察觉。

关键约束来自登录链路:全校统一身份认证为 **AD FS**(`sts.cuhk.edu.cn`),走 OAuth2 授权码流,`client_id=4b71b947-7b0d-4611-b47e-0ec37aabfd5e`,回跳 `https://bb.cuhk.edu.cn/webapps/bb-SSOIntegrationOAuth2-BBLEARN/authValidate/getCode`;ADFS 表单字段为 `UserName/Password/Kmsi`,POST 后 302 回 BB `getCode` 写入会话 cookie;**账号密码即可、无 MFA**,故登录可全自动。此后以会话 cookie 直接调用 `/learn/api/public/v1/...` REST API 返回干净 JSON,**无需爬 HTML**。已知传输层坑:anaconda 自带 Python `requests` 直连 ADFS 出现 TLS 握手失败(`UNEXPECTED_EOF`),`curl` 正常;故引擎必须用带浏览器 TLS 指纹的客户端(`curl_cffi`,回退子进程 `curl`)并尊重本机代理 `127.0.0.1:7890`(Clash)。

这三个痛点都指向同一个能力缺口:**一个本地的、自动的、对接 BB 官方 API 的"变化检测 + 待办归集 + 课件镜像"工具**。

### 1.2 目标(Goals)

bbwatch 以 **Claude Code 插件** 为唯一形态交付,目标如下:

- **G1 四类变化提醒(去重)**。检测并提醒四类事件,每类均按稳定 id 做全量 diff 去重:
  - 新作业 + 截止时间(新的 gradebook column id,带 `grading.due`);
  - 新公告(新的 announcement id);
  - 新课件上传/更新(新的 content id,或 `modified` 时间变化);
  - 出分(per-user 栏目 `status` 变为 `Graded`,或 `score` 由空变非空)。
- **G2 可勾选的本地任务清单**。绑 `127.0.0.1` 的小服务器,浏览器查看,未完成按 ddl 升序排序,展示"未交 / 待批 / 已批"三态(来源:per-user column `status` ∈ {`None`, `NeedsGrading`, `Graded`}),逾期标红、临近高亮,可手动勾选完成并回写 SQLite。
- **G3 课件增量镜像**。递归内容树,按 课程/文件夹 结构镜像到本地,按 content id + `modified` 增量下载,只取变化项;附件下载需跟随 302 重定向到真实文件。
- **G4 三种触发,零 OS 级常驻**。(a) 插件 `SessionStart` 钩子:开 Claude Code 即后台异步扫一次,用 `additionalContext` 把待办摘要注入会话;(b) 手动 `/bb-scan` 或 MCP `scan_now`;(c) 清单服务存活期间的可选周期扫描(每 10 分钟~每天定点可配)。三者共用同一 SQLite,幂等。
- **G5 全自动登录与凭据安全**。ADFS OAuth2 自动登录,会话 cookie 缓存复用、过期自动重登;密码仅存 macOS 钥匙串,不落盘明文、不进日志。
- **G6 可分发**。最终能打包成 Claude Code 插件分发给同学,安装即用。

每个目标都映射到一个引擎模块(G1→`engine.scanner`;G2→`engine.store` + dashboard;G3→`engine.downloader`;G4→插件 hooks/commands/mcp;G5→`engine.auth`;G6→插件打包),便于按目标验收。

### 1.3 非目标(Non-Goals)

明确**不做**以下事项,以约束范围、规避安全与合规风险:

- **NG1 不做多用户托管**。不在任何服务器上替他人保管学校密码或代登录;bbwatch 是"用户在自己机器上、用自己账号、访问自己数据"的本地工具。每个安装实例只服务本机当前用户。
- **NG2 不监控邮箱收件箱**。不读取、不轮询 M365(`@link.cuhk.edu.cn`)邮件;邮箱本身已推送到手机,重复监控既无必要也扩大授权面。bbwatch 的数据面**仅限 BB REST API**。
- **NG3 仅面向 Claude Code**。v1 不追求跨工具可移植(不做独立守护进程、不做通用 CLI 生态适配)。形态固定为 Python 引擎 + Claude Code 插件外壳。
- **NG4 不安装 OS 级后台代理**。用户已明确否决 `launchd`/`cron`;扫描只在使用 Claude Code 期间发生(SessionStart 或清单服务进程存活期)。已知取舍:**哪天不开 Claude Code 就不会扫**,可能漏当天 ddl;靠清单页/会话摘要对"临近 ddl 但最近未扫"做醒目提示来缓解(详见 §1.6 成功标准 S4)。注意此取舍只影响**通知时效**,不影响**完备性**:因 S1 用全量 diff(§1.6),哪怕多天未扫,下次扫描仍把累计的新项一次补齐,绝不丢项。
- **NG5 v1 通知仅 macOS 桌面**。邮件 / Telegram 渠道作为可插拔后续(`engine.notifier` 留渠道插件扩展点),不在 v1 范围。
- **NG6 不抓取隐私敏感数据**。`courses/{cid}/users` 会返回全班同学信息,**不拉取**;镜像与提醒只覆盖"我"的作业、成绩、公告、课件。

### 1.4 术语表(Glossary)

| 术语 | 定义 | 数据来源(实测字段/端点) |
|---|---|---|
| **uid** | 当前用户的 BB 内部 id,形如 `_49765_1`。`me` 别名在 `users/{uid}/courses` 子资源**不可用**,必须用真实 uid。 | `GET /learn/api/public/v1/users/me` → `id` |
| **cid** | 课程内部 id,形如 `_17236_1`(区别于人类可读的 `courseId` 如 `MAT3007:Optimization_L01`)。 | `users/{uid}/courses?expand=course` → `course.id` |
| **term** | 学期。用于识别"当前学期"与过滤在读课程。 | `GET terms?limit=100` → `termId → name`(如 `2550UG`) |
| **在读课程** | role=Student 且 `availability.available` ∈ {`Yes`, `Term`} 的课程。实测 19 门 membership 跨 3 学期,其中 17 门在读。 | `users/{uid}/courses?expand=course&limit=100`(`limit≤100`,需翻页) |
| **gradebook column(成绩册栏目)** | 作业的**权威载体**。带 `grading.due` 的列=有截止的作业/quiz;`Weighted Total`/`Total` 等汇总列**无 due,需过滤**。 | `GET courses/{cid}/gradebook/columns` → `id`,`name`,`grading.due`,`grading.type`,`contentId`,`score.possible` |
| **column id** | 成绩册栏目的稳定 id。**新作业 ⇔ 新 column id**(与本地已知集合 diff)。 | 同上 → `id` |
| **status(完成状态)** | 当前用户对某栏目的提交状态。 | `GET courses/{cid}/gradebook/columns/{colId}/users/{uid}` → `status` ∈ {`None`=未提交, `NeedsGrading`=已交待批, `Graded`=已批改} |
| **score** | 当前用户在某栏目的得分,出分后非空。**出分 ⇔ `status`→`Graded` 或 `score` 由空变非空**。 | 同上 → `score` |
| **ddl(截止时间)** | 来自 `grading.due`,**UTC**,形如 `2026-06-30T15:59:00.000Z`;展示需转东八区 +8。 | `grading.due` / 日历项 `end` |
| **日历项** | 跨课程 ddl 聚合。本实例返回项**全为** `type=GradebookColumn`(带 `end`=截止)。窗口**必须 ≤16 周**(超出报 400),需按窗口翻页覆盖整学期。per-course columns 信息更全(含 `contentId`/status),日历仅作跨课程 ddl 视图。 | `GET calendars/items?since=..&until=..&limit=100` |
| **announcement(公告)** | 课程公告。**新公告 ⇔ 新 announcement id**,按 `created` 排序;`body` 常含考试/补课信息。系统级 `GET announcements` 本例为空。 | `GET courses/{cid}/announcements` → `id`,`title`,`created`,`body` |
| **content(内容项)** | 内容树节点。`contentHandler.id` ∈ {`resource/x-bb-folder`, `x-bb-document`, `x-bb-file`, `x-bb-assignment`}。`modified` 用于"新课件/更新"检测。 | `GET courses/{cid}/contents`(顶层多为 folder);子级 `contents/{id}/children` → `id`,`title`,`created`,`modified`,`position`,`hasChildren` |
| **attachment(附件)** | 内容项挂载的文件。下载链接返回 **302** 跳真实文件,需跟随重定向。 | `contents/{id}/attachments` → `id`,`fileName`,`mimeType`;下载 `attachments/{aid}/download` |
| **paging** | 分页字段**仅在有下一页时出现**(含 `nextPage`);不能假设固定结构,需据此判断是否翻页。 | 各列表端点响应的 `paging.nextPage` |
| **事件(Event)** | 一次扫描中检出的"新变化",四类之一(新作业/新公告/新课件/出分),由 `engine.scanner` 产出供通知与清单使用。 | `engine.scanner.scan() -> list[Event]` |
| **快照(snapshot)** | 本地 SQLite 中"上次扫描已知集合"的持久化,用于全量 diff(非时间窗口增量)。 | `engine.store`(见 §1.6 DDL) |
| **会话(session)** | ADFS 登录后写入的 BB 会话 cookie 集合,缓存复用、过期自动重登。 | `engine.auth.get_session()` |

### 1.5 安全与可接受使用总原则

以下为贯穿全工程的红线,任何模块设计与代码评审都以此为准:

- **AUP-1 自有账号、自有数据**。bbwatch 仅以用户本人凭据访问其本人在 BB 上的数据(我的课程、我的作业、我的成绩、我的公告与课件)。不代理他人、不越权读取同班同学信息(对应 NG6,不调 `courses/{cid}/users`)。
- **AUP-2 凭据只进钥匙串**。学校密码**仅**存 macOS 钥匙串(`keyring`),由本机当前用户持有。**绝不**落盘明文、**绝不**进日志、**绝不**出现在文档示例里(本文所有示例一律不含真实密码)。会话 cookie/token 同样不打印、不写日志。
- **AUP-3 本地优先,最小暴露面**。清单服务**仅绑 `127.0.0.1`**,不监听对外端口;不上传任何数据到第三方;镜像文件存本机。
- **AUP-4 对 BB 温和(好公民)**。复用会话、合理限速、请求间加延时、`fields=` 精简返回负载;不并发轰炸、不做无意义全量重拉(全量 diff 基于本地快照,只在必要时调 API)。
- **AUP-5 失败安全**。登录流/接口因 ADFS 或 BB 改版而失效时,**宁可不通知也不误报/不泄密**:报错信息脱敏(不含密码/cookie/token),并提示用户。MFA 目前关闭;若学校开启,降级为"半自动会话复用"(保留扩展点),绝不弱化凭据保护去强行自动化。
- **AUP-6 可审计、可撤销**。所有写操作(下载落地、DB 写入、通知)可追溯;用户可随时清空本地数据与钥匙串条目以彻底退出。

### 1.6 成功标准(Success Criteria)

最高优先的非功能需求是 **绝不漏(no miss)且 绝不重复(no duplicate)**。下面给出**可度量**定义与判据。

设某课程在 BB 端某时刻的真实集合为 `B`(如所有带 `grading.due` 的 column id 集合、所有 announcement id 集合、所有 content id 集合),本地已知/已通知集合为 `K`,某次扫描检出待通知集合为 `D`。

- **S1 不漏(完备性)。** 对任意一次扫描,要求 `B \ K ⊆ D`——凡 BB 端存在而本地未知的项,**全部**被检出。度量:`miss_count = |(B \ K) \ D| == 0`。
  - 实现保证:用**稳定 id 对本地已知集合做全量 diff**(非时间窗口增量),因此即使多天未扫,下次也补齐累计变化。
  - 边界保证:`fresh_ids` 必须是**翻页到底后的完整集合**才能与 `K` 比对——日历窗口必须 **≤16 周** 且按窗口翻页覆盖整学期;列表分页据 `paging.nextPage` 翻到底。任一环节漏翻即引入伪"新项缺失"(把未翻到的真实项误当作不存在),直接违反 S1。
- **S2 不重复(幂等性)。** 对同一稳定 id,跨**所有触发源**(SessionStart / `/bb-scan` / `scan_now` / 周期循环)一生只通知一次。度量:对任意 id,`notify_count(id) ≤ 1`。
  - 实现保证:id 一旦"已知/已通知"即在 SQLite 打标记(`notified_at` 非空),多触发源共用同一库;通知写入与标记在**同一事务**内完成(发后立即标记,并在重复检测时跳过已标记项),保证并发触发不重发。
- **S3 状态正确(不误报已交)。** 已提交/已批改的作业不再作为"未完成"提醒或排进待办未交区。判据:`status` ∈ {`NeedsGrading`, `Graded`} 或 `score` 非空 ⇒ 不计入"未交"。出分提醒仅在 `status` 由非 `Graded` 变 `Graded`(或 `score` 由空变非空)时触发一次。
- **S4 缺扫可见(缓解 NG4)。** 由于不开 Claude Code 当天不扫,清单页与 SessionStart 摘要必须对"距上次 `last_scan` 已超过阈值且存在临近 ddl"的情形给出醒目提示。度量:存在 ddl 在 `now+24h` 内、且 `now - last_scan > 配置阈值` 时,UI 必须高亮告警(可由集成测试断言)。
- **S5 时区正确。** 所有 ddl 在存储时保留 UTC 原值,展示时统一 +8(东八区)。判据:对样例 `2026-06-30T15:59:00.000Z`,清单页须显示 `2026-07-01 00:59`(本地)。
- **S6 安全零泄漏。** 全量日志与持久化文件中,密码/cookie/token 出现次数为 0(可由扫描脚本对日志目录与 DB 断言)。

为支撑上述度量,`engine.store` 的最小 SQLite DDL 如下(快照即 `B`,标记列承载 `K` 与 S2):

```sql
-- 已知项快照 + 通知幂等标记;一行一个稳定 id
CREATE TABLE IF NOT EXISTS seen_item (
  course_id    TEXT NOT NULL,                 -- cid,如 _17236_1
  kind         TEXT NOT NULL,                 -- 'column' | 'announcement' | 'content'
  item_id      TEXT NOT NULL,                 -- 稳定 id:column id / announcement id / content id
  first_seen   TEXT NOT NULL,                 -- 本地首次检出 (UTC ISO8601)
  modified     TEXT,                          -- content.modified;用于课件更新检测
  notified_at  TEXT,                          -- 非空=已通知(S2 幂等关键)
  PRIMARY KEY (course_id, kind, item_id)
);

-- 作业完成/出分状态(S3);与 seen_item 的 column 项对应
CREATE TABLE IF NOT EXISTS task_state (
  course_id     TEXT NOT NULL,                -- cid
  column_id     TEXT NOT NULL,                -- 成绩册栏目 id
  name          TEXT,                         -- 栏目名(如 Homework 4)
  due_utc       TEXT,                         -- grading.due,UTC 原值(S5)
  status        TEXT,                         -- None | NeedsGrading | Graded(来自 per-user 接口)
  score         REAL,                         -- 出分后非空
  graded_notified INTEGER NOT NULL DEFAULT 0, -- 出分提醒幂等
  manual_done   INTEGER NOT NULL DEFAULT 0,   -- 线下/纸质作业手动勾选完成
  PRIMARY KEY (course_id, column_id)
);

-- 全局扫描元信息(S4 的 last_scan)
CREATE TABLE IF NOT EXISTS scan_meta (
  key   TEXT PRIMARY KEY,                      -- 例如 'last_scan'
  value TEXT NOT NULL                          -- UTC ISO8601
);
```

不漏/不重的核心 diff 逻辑(伪代码,体现 S1+S2 在一处闭合):

```python
def diff_and_notify(course_id: str, kind: str, fresh_ids: set[str], store, notifier) -> list[Event]:
    """fresh_ids = 本次从 BB 拉到的全部稳定 id(必须已翻页到底,见 S1 边界保证)。"""
    known = store.known_ids(course_id, kind)        # K:seen_item 中该 (cid, kind) 的全部 item_id
    new_ids = fresh_ids - known                     # S1:全量 diff,非时间窗口
    events = []
    for item_id in new_ids:
        with store.transaction():                   # S2:标记与通知同事务,幂等
            if store.is_notified(course_id, kind, item_id):
                continue                            # 并发触发兜底:已通知则跳过
            store.upsert_seen(course_id, kind, item_id)
            ev = build_event(course_id, kind, item_id)
            store.mark_notified(course_id, kind, item_id)
            events.append(ev)
    notifier.notify(events)                          # 失败安全:通知失败不回滚标记,避免重发风暴
    return events
```

> 注:`fresh_ids` 仅在调用方确认"已无 `paging.nextPage`、且日历已覆盖全学期窗口"后才传入;否则不完整的 `fresh_ids` 会使真实存在的项落在 `known` 之外又不在本次集合内,延后甚至漏报(违反 S1)。翻页完整性是 diff 正确性的前置条件,必须在 `bbclient` 层强约束,不下放给 `scanner`。

**验收口径**:S1/S2 以对同一只读 BB 状态连续两次扫描(第二次应产出 0 个事件)、以及"先注入历史快照再扫描应补齐全部新项"两类自动化测试断言;S3/S5 以构造的 column 样本(`Graded`/`NeedsGrading`/`None` × 带 due)断言;S4 以伪造 `last_scan` 与临近 ddl 断言 UI 告警;S6 以日志/DB 文本扫描断言零命中。全部满足方视为本工具达成其最高非功能目标。

---

## 2. 功能清单

> 下列功能均以"附录 A 已验证端点 + 实测字段"为依据，已剔除技术上不成立的设想。每条给出：一句话价值 / 依赖的 BB 接口字段 / 可验收标准。
> 通用约定：所有时间字段为 UTC，展示一律转东八区 (+8)；所有"新东西"判定均为 **稳定 id 与本地 SQLite 已知集合做全量 diff**（非时间窗口增量），保证多天未扫也能补齐且幂等去重。

### 2.1 MVP（第一刀闭环必须有）

**F1. 未完成作业清单 + ddl 倒计时（主屏）**
- 价值：一眼看清还欠什么、最紧的是哪个、还剩多少小时；逾期标红、临近高亮，所有提醒最终都落到这张清单。
- 接口字段：`courses/{cid}/gradebook/columns` 取 `id/name/grading.due/score.possible`（**过滤无 `grading.due` 的汇总列**，如 Weighted Total / Total）；`gradebook/columns/{colId}/users/{uid}` 取 `status`（`None` 即未交才进清单）。`grading.due` UTC → +8 算倒计时。
- 验收：跨全部在读课程，列出所有"带 due 且 status=None"的列，按 ddl 升序；逾期项标红、24h 内临近高亮；已交/已批项不出现在未完成区；汇总列不出现。

**F2. 新作业提醒（新 column id 即通知，含 ddl）**
- 价值：老师布置作业不一定发邮件，这是最易漏的环节；天然去重不重复打扰。
- 接口字段：`courses/{cid}/gradebook/columns` 全量拉取，与 SQLite 已知 column id 集合 diff；新 id 且带 `grading.due` 判为新作业，取 `name/grading.due`。macOS 桌面通知（`osascript`/`terminal-notifier`）。
- 验收：出现一个此前未记录的 column id（带 due）时弹一次通知并入库；同一 id 再次扫描不重复通知；多天未扫后单次扫描能补齐期间所有新列。

**F3. 新公告提醒（新 id 即通知，展示标题 + 正文摘要）**
- 价值：公告正文常含补课/期中/座位表/改 ddl 等关键信息且不进邮箱，正文摘要省去再点进 BB。
- 接口字段：`courses/{cid}/announcements` 取 `id/title/created/body`，与已知 id 集合 diff，按 `created` 排序；正文取摘要。
- 验收：新 `announcement id` 触发一次通知，含标题 + 正文摘要；已通知 id 不重复；按 created 时间正确排序展示。

**F4. 出分提醒 + 成绩台账**
- 价值：学生很关心出分（窗口往往只几天，便于核对/申诉）；同一接口顺带做各科 score/possible 成绩台账，无额外抓取成本。
- 接口字段：`gradebook/columns/{colId}/users/{uid}` 的 `status`（`None`/`NeedsGrading`→`Graded`）与 `score`（由空变非空）做 diff 触发；台账聚合各 column 的 `score` 与 `score.possible`。
- 验收：某 column 的 status 变为 Graded 或 score 由空变非空时弹一次通知；重复扫描不再通知；台账正确显示各科 `score/score.possible`。

**F5. 作业状态可视化（未交 / 待批 / 已批）**
- 价值：区分"还没交""交了等批""已出分"，避免对已提交作业误报，减少焦虑与误判。
- 接口字段：`gradebook/columns/{colId}/users/{uid}` 的 `status` 三态 `None`/`NeedsGrading`/`Graded` 直接映射；线下/纸质等无法自动判定者由清单页**手动勾选**，状态存 SQLite，扫描器尊重。
- 验收：每个作业项显示正确三态；手动勾选的线下项状态持久化且不被扫描覆盖；已交项不触发未交提醒。

**F6. SessionStart 待办摘要注入**
- 价值：拖延党往往不主动查 BB；开 Claude Code 即把今日/本周新变化与最紧 ddl 塞到会话最前面，是"绝不漏"的关键触达点，无需用户主动操作。
- 接口字段：复用一次 scan 的 diff 结果（新作业/公告/出分）+ F1 未完成清单按 ddl 排序；经插件 `SessionStart` 钩子的 `additionalContext` 注入（异步非阻塞）。
- 验收：开 Claude Code 后会话开头出现摘要，含本次新事件与最紧若干 ddl；扫描在后台进行不阻塞会话；无新变化时给简洁"无新增"提示。

**F7. "临近 ddl 但最近未扫"醒目提示**
- 价值：纯插件形态不开 Claude Code 当天就不扫，这是最大漏网风险；用上次扫描时间与临近 ddl 交叉高亮缓解。
- 接口字段：store 的 `last_scan` 时间戳 + columns `grading.due`，计算"距 ddl 小时数 vs 距上次扫描小时数"，无新接口。
- 验收：当存在临近 ddl 且 last_scan 距今超过阈值时，清单页与会话摘要均出现醒目提示；扫描刷新后提示消除。

### 2.2 后续（第二刀：自动化 + 下载）

**F8. 新课件上传检测 + 提醒**
- 价值：复习时漏掉新讲义/标注版/往年卷会吃亏；能区分"新上传"与"已有项更新"。
- 接口字段：`courses/{cid}/contents` 递归 `children`，对内容项 `id` 做全量 diff 检出新项；`modified` 字段判断已知项是否更新；类型 `x-bb-folder/-document/-file/-assignment`。macOS 桌面通知。
- 验收：新 content id 触发"新增"通知；已知项 modified 变化触发"更新"通知；同状态重复扫描不重复通知。

**F9. 按课程增量镜像下载课件（保留文件夹结构）**
- 价值：期末批量下课件是明确痛点，手动逐个下很烦；增量镜像让重复下载只补新/改文件。
- 接口字段：递归 `contents`/`children` 建树 → `contents/{id}/attachments` 取 `id/fileName/mimeType` → `attachments/{aid}/download`（**跟随 302** 到真实文件）落盘；以 attachment `id` 为稳定键、`modified` 判更新做增量。
- 验收：首次按"课程/文件夹"层级完整落盘；二次运行仅下载新增/更新文件，已有文件跳过；302 正确跟随得到真实文件而非重定向页。

**F10. 往年卷 / past paper 识别与归集**
- 价值：突击复习最值钱的就是往年卷，但常埋在层层文件夹里；自动抽到统一目录省去翻树。
- 接口字段：复用 F9 全树遍历的 `contents.title` 与 `attachments.fileName`，按关键词/正则（past/exam/midterm/final/卷/真题/年份等）匹配命中项，额外复制/软链到归集目录（如 `exams/`）。
- 验收：命中项出现在归集目录且保留可溯源的原路径信息；未命中项不误归集；增量运行不重复归集。

**F11. 本地课件库检索（课程 / 路径 / 文件名）**
- 价值：复习时"那张图在哪门课哪个 slide"，本地按课程/文件夹/文件名秒定位，比 BB 网页逐课翻快得多。
- 接口字段：纯本地查询，不额外打 BB。镜像时把 `courseId`（人类可读如 `MAT3350:Information_Theory`）、文件夹路径、`title`、`fileName` 存入 SQLite 建索引。
- 验收：关键词能按课程/路径/文件名命中本地已镜像文件并返回其落盘路径；查询不发起任何 BB 请求。

### 2.3 可选（第三刀及打磨）

**F12. 全学期 ddl 周视图 / iCal 导出**
- 价值：把所有课的截止集中成周历或导入系统日历/手机提醒，离开 Claude Code 也能被提醒。
- 接口字段：`calendars/items?since&until`（**窗口 ≤16 周，超出报 400，需按窗翻页覆盖整学期**），返回项均为 `type=GradebookColumn` 带 `end`=截止；或直接复用 per-course columns 的 `grading.due` 生成标准 `.ics`（VEVENT）。
- 验收：覆盖整学期无窗口漏页；导出的 .ics 能被系统日历导入且事件时间为正确本地时间；与 F1 清单的 ddl 集合一致。

**F13. 公告正文关键信息抽取（考试 / 补课 / 座位 / 范围）**
- 价值：期中时间、补课、座位表、改 ddl 常埋在公告正文，单独抽出加进清单/提醒比让学生自己读省心。
- 接口字段：`courses/{cid}/announcements` 的 `body` 文本，交给 Claude 解析抽取日期/地点/事项（无需新接口），挂在 F3 新公告提醒上。
- 验收：对含考试/补课/座位/范围关键词的公告抽出结构化条目（时间/地点/事项）并集中展示；无关公告不产噪声条目。

**F14. 待批积压提醒（NeedsGrading 长期未出分）**
- 价值：提醒哪些已交作业迟迟没批，可催老师，避免学期末扎堆出分来不及核对。
- 接口字段：`gradebook/columns/{colId}/users/{uid}` 的 `status=NeedsGrading`，结合本地记录的进入该状态时间，超过 N 天未变 Graded 即提示（本地计时，无新接口）。
- 验收：某项 NeedsGrading 持续超阈值时给一次提示；变为 Graded 后停止提示且触发 F4 出分通知。

---

**已剔除（技术上不成立 / 不建议纳入）：**
- **成绩历史快照与"分数被改"审计**：技术上可行（每次把 score 带时间戳写库做全量 diff），但本实例未观察到改分场景，且与 F4 的 score-diff 机制重叠；列为 F4 的可选扩展而非独立功能，不单列。
- **实时加权 GPA / 课程总评估算 / 目标分反推**：依赖"汇总列权重结构"。实测仅确认存在 `Weighted Total`/`Total` 汇总列，但**未验证能从 API 稳定取到各 column 的权重占比**；在权重可靠获取前，加权 GPA 与目标分反推不能保证正确，故不纳入 MVP/后续，待权重字段核实后再评估。
- **课程间发挥对比**：仅 `score/possible` 比例聚合可做，但价值低且与 F4 台账重叠，并入 F4 不单列。

---

## 3. 总体架构与代码结构

本章定义 bbwatch 的代码组织方式。核心判断只有一句：**所有价值与所有难度都在 Python 引擎里, 插件外壳是一层薄到几乎没有逻辑的胶水。** 因此本章先给分层模型, 再给组件关系图, 再给逐文件的仓库树, 然后逐模块写清单一职责、依赖方向、关键函数签名与 SQL DDL, 最后单列进程模型(谁常驻、谁一次性)。

贯穿全章有一条优先级裁决, 凡设计取舍冲突一律以它为准: **绝不漏 > 绝不重 > 对 BB 温和 > 性能。** 后文每处涉及"漏/重"的地方都会回指这条。

### 3.1 分层模型: 引擎 / 外壳 / 视图

bbwatch 分三层, 依赖严格单向向下, 上层可以 import 下层, 下层永远不 import 上层:

```
┌──────────────────────────────────────────────────────────────┐
│  L3 外壳层 (Claude Code plugin shell) —— 零业务逻辑            │
│    hooks/ · commands/ · .mcp.json(指向 engine.mcp_server)      │
│    只做: 解析触发 → 调用 L2 一个函数 → 把返回值格式化给 CC     │
├──────────────────────────────────────────────────────────────┤
│  L2 编排层 (engine 顶层用例) —— 事务边界在这里                 │
│    engine.scanner · engine.downloader · engine.dashboard      │
│    engine.cli · engine.mcp_server                              │
│    每个公开入口是"一次完整用例", 自带 try/finally 与限速       │
├──────────────────────────────────────────────────────────────┤
│  L1 能力层 (engine 基础设施) —— 纯粹、可单测、无副作用串联     │
│    engine.auth · engine.bbclient · engine.store               │
│    engine.notifier · engine.models · engine.config            │
└──────────────────────────────────────────────────────────────┘
```

关键约束:
- **L3 不允许出现任何 BB 知识。** 外壳层任何文件里不得出现 `/learn/api/public/v1`、`grading.due`、`x-bb-folder` 这类字符串。一旦出现, 说明逻辑漏到了壳里。外壳唯一被允许知道的是"调哪个 Python 入口、参数是什么"。
- **L1 模块之间也分主从。** `bbclient` 依赖 `auth`(要会话)、`models`(返回类型)、`config`(基址/代理); `store` 只依赖 `models` 和标准库 `sqlite3`; `notifier` 只依赖 `models`。`auth`、`config`、`models` 是叶子, 不依赖其它 engine 模块。这保证了 `store` 与 `notifier` 可以脱离网络单测。
- **唯一的"真理之源"是 `engine.store` 背后的 SQLite 文件。** 所有触发源(SessionStart / `/bb-scan` / MCP / dashboard 周期循环)写的是同一个库, 这是"绝不重复"硬约束在架构层的落点——幂等性靠库里的稳定 id 标记保证, 而不是靠"哪个触发源先跑"。
- **"绝不漏/绝不重"是 L1 `store` 的不变量, 不是 L2 的编排技巧。** 关键推论: L2 的 `scanner` 无论以什么顺序、被谁、并发多少次调用, 都不能破坏这两条——因为新旧判定与已通知判定全部下沉到 `store` 的全量 id diff 与标记位, `scanner` 只是搬运工。这一点在 §3.4 `store` 与 §3.5 进程模型里各落一次。

### 3.2 组件关系图

```
                         Claude Code 进程
   ┌──────────────┬──────────────────┬───────────────────────────┐
   │ SessionStart │   /bb-* commands │   MCP client (stdio)      │
   │   hook       │   (Markdown)     │                           │
   └──────┬───────┴────────┬─────────┴─────────────┬─────────────┘
          │ exec(一次性)   │ exec(一次性)          │ stdio(随会话存活)
          ▼                ▼                       ▼
   bbwatch session-start  bbwatch scan/...   engine.mcp_server (FastMCP)
   (engine.cli)          (engine.cli)        scan_now/list_tasks/...
          │                │                       │
          └────────────────┴───────────┬───────────┘
                                        │  都调用同一组 L2 用例
                                        ▼
        ┌───────────────────────  engine.scanner.scan()  ───────────────────────┐
        │                                                                        │
        ▼                  ▼                    ▼                  ▼              ▼
   engine.auth   →   engine.bbclient   →   engine.store   →   engine.notifier   │
   (会话/钥匙串)      (REST 封装/分页)      (SQLite diff)       (macOS 通知)      │
        │                  │                    ▲                                │
        │                  ▼                    │                                │
        │            BB REST API                │  engine.downloader 也读写它    │
        │     bb.cuhk.edu.cn/learn/api/...      │  (镜像清单表)                  │
        ▼                                       │
   ADFS OAuth2                                  │
   sts.cuhk.edu.cn                              │
                                                │
   ┌────────────────────────────────────────────┴───────────────────────────┐
   │  dashboard 常驻进程 (engine.dashboard, FastAPI @127.0.0.1)              │
   │  读 store 渲染任务清单 / 写回 mark_done / 进程内可选周期调 scanner.scan()│
   └─────────────────────────────────────────────────────────────────────────┘
                         ▲ 浏览器查看, 勾选完成
                         │
                       用户
```

读图要点:
- 横向五个 L1 模块(`auth → bbclient → store → notifier`, 加旁挂的 `downloader`)构成一次扫描的数据流, `scanner` 是把它们串起来的唯一编排者。
- 三个外壳触发源(hook / command / MCP)在图上是三个入口, 但都收敛到 `engine.cli` 或 `engine.mcp_server`, 再收敛到同一批 L2 用例函数。没有任何外壳直接 import `bbclient` 或 `store`——它们够不到 L1, 必须经过 L2。
- `dashboard` 是唯一可能"常驻"的方框(虚线生命周期 = 用户在用 Claude Code 期间), 它和 hook/command 共享同一个 store 文件, 因此 dashboard 里勾的"已完成"下次扫描会被 scanner 尊重(见 §3.4 `my_status.manual_done`)。

### 3.3 仓库目录树(完整, 逐文件)

```
bbwatch/
├── .claude-plugin/
│   └── plugin.json                 # 插件清单: name/version/description, 声明 hooks 与 commands 目录
├── .mcp.json                       # MCP 服务器声明: command="bbwatch", args=["mcp"], 由 CC 以 stdio 拉起
├── hooks/
│   └── hooks.json                  # SessionStart → {type:"command", command:"bbwatch session-start --async"}
├── commands/
│   ├── bb-setup.md                 # /bb-setup    → 引导存账号到钥匙串(调 bbwatch setup)
│   ├── bb-scan.md                  # /bb-scan     → bbwatch scan, 回填本次新事件摘要
│   ├── bb-dashboard.md             # /bb-dashboard→ bbwatch dashboard --open, 打印 127.0.0.1 URL
│   └── bb-download.md              # /bb-download → bbwatch download <course>
├── engine/                         # ← 全部业务逻辑在此 Python 包
│   ├── __init__.py
│   ├── __main__.py                 # `python -m engine` == `bbwatch` 控制台入口, 转发给 cli.main()
│   ├── config.py                   # L1 叶子: 基址/代理/路径/黑白名单/限速 的唯一来源
│   ├── models.py                   # L1 叶子: dataclass —— Course/Column/Announcement/ContentNode/Attachment/MyColumnStatus/Event/Task
│   ├── auth.py                     # L1: ADFS OAuth2 登录 → BB 会话; 钥匙串读写
│   ├── bbclient.py                 # L1: REST 封装(分页/限速/重试/fields 裁剪)
│   ├── store.py                    # L1: SQLite schema + diff/标记/查询(本章 §3.4 给全 DDL)
│   ├── notifier.py                 # L1: 渠道接口 + macOS 实现(osascript/terminal-notifier)
│   ├── scanner.py                  # L2: scan() 编排 —— 拉取→diff→写库→通知
│   ├── downloader.py               # L2: mirror() 增量镜像内容树到本地
│   ├── dashboard.py                # L2: FastAPI app(127.0.0.1) + 进程内可选周期扫描
│   ├── mcp_server.py               # L2: FastMCP, 暴露 scan_now/list_tasks/... 6 个工具
│   └── cli.py                      # L2: argparse 子命令 setup/scan/session-start/dashboard/download/mcp
├── dashboard/
│   ├── templates/
│   │   └── index.html              # 任务清单页(未完成按 ddl 升序, 逾期标红, 可勾选)
│   └── static/
│       ├── app.js                  # fetch /api/tasks, POST /api/tasks/{id}/done
│       └── style.css
├── tests/
│   ├── test_store.py               # 纯 SQLite, 无网络: diff 幂等/状态机/崩溃后重检
│   ├── test_scanner.py             # 用 fake bbclient 注入固定 JSON, 验证 Event 产出 + 通知-打标顺序
│   └── fixtures/                   # 实测 JSON 样本(脱敏): columns/announcements/contents/children/calendar/my_status
├── pyproject.toml                  # 包元数据 + [project.scripts] bbwatch = "engine.cli:main" + 依赖
└── README.md                       # 安装/分发(含改密提示)
```

几个不显然但重要的设计点:

- **`pyproject.toml` 用 `[project.scripts]` 注册一个 `bbwatch` 控制台脚本**, 入口 `engine.cli:main`。整个外壳层(hooks.json / commands/*.md / .mcp.json)对引擎的全部调用都走这一个 `bbwatch <subcommand>` 命令, 不直接 `python path/to/script.py`。这样外壳与引擎之间只有一个稳定契约面(子命令名 + 参数), 重构 engine 内部不会动外壳。
- **`.mcp.json` 与 hooks 调的是同一个二进制**, 只是子命令不同(`bbwatch mcp` vs `bbwatch session-start`)。MCP server 启动时不自己实现工具逻辑, 而是直接 import 并调用 `engine.scanner` / `engine.store` 的函数——MCP 工具体≈一行转发。
- **依赖(在 `pyproject.toml`)**: `curl_cffi`(传输层, 见下条)、`keyring`(钥匙串)、`fastmcp`、`fastapi`+`uvicorn`(dashboard)。**刻意不依赖 `requests`**——实测 anaconda 的 `requests` 直连 `sts.cuhk.edu.cn` 出现 TLS `UNEXPECTED_EOF`(疑为本机 Clash 代理 7890 + OpenSSL 组合或 ADFS TLS 指纹所致), 而 `curl_cffi` 带浏览器 TLS 指纹可正常握手; 若 `curl_cffi` 仍失败, `auth`/`bbclient` 回退到子进程 `curl`(见 §3.4 `auth`)。

### 3.4 各模块单一职责、依赖方向与关键签名

下面按 L1 → L2 → L3 顺序给每个模块的职责契约。涉及 BB 的断言均对齐实测事实(见文末对齐清单)。

#### L1 叶子: `config.py` / `models.py`

`config.py` 是所有"环境常量"的唯一出口, 别处不得硬编码:

```python
BB_BASE   = "https://bb.cuhk.edu.cn"
ADFS_BASE = "https://sts.cuhk.edu.cn"
OAUTH_CLIENT_ID = "4b71b947-7b0d-4611-b47e-0ec37aabfd5e"
OAUTH_REDIRECT  = f"{BB_BASE}/webapps/bb-SSOIntegrationOAuth2-BBLEARN/authValidate/getCode"
API = f"{BB_BASE}/learn/api/public/v1"          # 所有 REST 路径以此为前缀
# 尊重本机环境代理(Clash 127.0.0.1:7890); HTTPS_PROXY 缺省时回落到通用 ALL_PROXY
PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("ALL_PROXY")
DB_PATH       = Path.home() / ".bbwatch" / "bbwatch.db"
MIRROR_ROOT   = Path.home() / ".bbwatch" / "courses"
CALENDAR_WINDOW_DAYS = 110       # < 16 周(112 天)硬上限, 留余量; 整学期靠多窗口翻页拼接
MIN_REQUEST_INTERVAL = 0.4       # 对 BB 温和: 请求间最小间隔(秒)
ACTIVE_AVAILABILITY = {"Yes", "Term"}   # 在读判定: availability.available ∈ 此集合 且 role=Student
KEYRING_SERVICE = "bbwatch"      # keyring.get_password(KEYRING_SERVICE, username)
```

`models.py` 是 L1↔L2↔L3 之间传递的全部 dataclass。它让"BB 的 JSON 形状"只在 `bbclient` 解析处出现一次, 之后全系统传 dataclass:

```python
@dataclass(frozen=True)
class Column:                 # 一个 gradebook column == 一个潜在作业
    id: str                   # 稳定 id, diff 的主键
    course_id: str            # 内部 id 如 "_17236_1"
    name: str
    due: datetime | None      # grading.due, UTC; None ⇒ 汇总列(Weighted Total/Total), 应过滤
    content_id: str | None    # contentId, 关联到内容树作业项
    possible: float | None    # score.possible

@dataclass(frozen=True)
class MyColumnStatus:
    column_id: str
    status: str               # "None"(未提交) | "NeedsGrading"(待批) | "Graded"(已批)
    score: float | None

@dataclass(frozen=True)
class Event:                  # scanner 的产物, 也是 notifier 的输入
    kind: str                 # "new_assignment" | "new_announcement" | "new_content" | "graded"
    course_id: str
    ref_id: str               # column_id / announcement_id / content_id —— 去重锚点(配合 store 标记位)
    title: str
    due: datetime | None      # 仅 new_assignment 有
    detail: str
```

#### L1: `auth.py`

职责: **拿到一个能调 BB REST 的已认证会话, 别的什么都不管。** 封装 ADFS OAuth2 授权码流(账号密码 POST → 302 回 BB `getCode` → 会话 cookie), 缓存复用会话, 过期自动重登。密码只从 macOS 钥匙串读, 绝不落盘、绝不进日志、绝不出现在任何异常信息里。依赖 `config`, 不依赖任何其它 engine 模块。

```python
def get_session() -> CurlSession:
    """返回带有效 BB 会话 cookie 的 curl_cffi session;
       先试缓存会话, 探到未认证(被 302 重定向回 ADFS 登录页 / REST 返回 401)则跑 _adfs_login() 重登。"""

def _adfs_login(session, username: str, password: str) -> None:
    # 1) GET ADFS authorize(client_id/redirect_uri 见 config)
    # 2) 解析返回表单(字段 UserName/Password/Kmsi), POST 凭据
    # 3) 跟随 302 回 bb 的 getCode, 会话即写入 BB cookie
    # 无 MFA, 全自动; 密码仅来自 keyring.get_password(KEYRING_SERVICE, username)
    # curl_cffi 握手仍失败时回退子进程 curl(继承同一 cookie jar)

def store_credentials(username: str, password: str) -> None:   # /bb-setup 调用
    keyring.set_password(KEYRING_SERVICE, username, password)
```

> MFA 当前关闭使全自动成立。若学校开启 MFA 或仅对校外触发, `get_session()` 的会话缓存层就是扩展点: 退化为"半自动一次登录 + 长期复用会话", 上层 `bbclient` / `scanner` 无须改动。

#### L1: `bbclient.py`

职责: **把 BB REST 端点翻译成返回 dataclass 的 Python 方法**, 内含分页、限速、重试、`fields=` 裁剪。这是全系统唯一知道 `/learn/api/public/v1/...` 路径与 JSON 字段名的地方。依赖 `auth`(取 session)、`models`(返回类型)、`config`(基址/限速)。

```python
class BBClient:
    def __init__(self, session): ...
    def whoami(self) -> str:                                 # GET users/me → id (如 "_49765_1")
                                                             #   注意: me 别名仅在 /users/me 自身可用;
                                                             #   下面 list_courses 的 {uid} 子资源必须用真实 id
    def list_courses(self, uid: str) -> list[Course]:        # GET users/{uid}/courses?expand=course&limit=100
                                                             #   翻页; 仅保留 role=Student 且 availability ∈ {Yes,Term}
    def list_columns(self, cid: str) -> list[Column]:        # GET courses/{cid}/gradebook/columns
                                                             #   过滤掉 due is None 的汇总列(Weighted Total/Total)
    def my_status(self, cid, col, uid) -> MyColumnStatus:    # GET .../columns/{col}/users/{uid} → status/score
    def list_announcements(self, cid) -> list[Announcement]: # GET courses/{cid}/announcements → id/title/created/body
    def list_calendar(self, since, until) -> list[Column]:   # GET calendars/items?since&until  (窗口必须 ≤16 周!)
                                                             #   本实例返回项全为 type=GradebookColumn(带 end=ddl)
    def get_contents(self, cid) -> list[ContentNode]:        # GET courses/{cid}/contents, 再对 hasChildren 的项
                                                             #   递归 GET contents/{id}/children; 返回扁平全树
    def list_attachments(self, cid, content_id) -> list[Attachment]   # GET .../contents/{id}/attachments
    def download_attachment(self, cid, content_id, att_id, dest) -> Path  # .../download 返回 302, 跟随到真实文件

    def _get_paged(self, path, params) -> Iterator[dict]:
        # paging 字段仅在有下一页时出现(含 nextPage); 不假设固定结构, 据其存在与否翻页
```

三条由实测事实直接约束的内部规则, 必须落在本模块, 不能外泄:
1. **`whoami` 用 `users/me`(合法), 但子资源不行。** `users/me/courses` 的 `me` 别名实测不可用——`list_courses` 必须传真实 uid。这一坑只在本模块兜住, 调用方拿到的永远是真实 uid。
2. **`list_calendar` 的 `until - since` 必须 ≤16 周**, 否则 BB 返回 400。本模块按 `CALENDAR_WINDOW_DAYS` 自动切窗翻页, 对调用者呈现"整学期一把拉"的假象。窗口拼接必须**首尾相接无空隙**, 否则会漏 ddl——这是"绝不漏"在传输层的一个具体落点, 须由 `test_bbclient` 覆盖边界。
3. **内容树是两级 API。** `courses/{cid}/contents` 只给顶层; 子级要对 `hasChildren=true` 的项逐个 `contents/{id}/children` 递归。`get_contents` 内部把递归做完、返回扁平全树, 否则深层课件会漏检。`_get_paged` 同理不能假设 `paging` 一定存在——只有还有下一页时才返回 `paging.nextPage`, 据此判断终止。

#### L1: `store.py`

职责: **SQLite 之上的快照-diff 与状态持久化。** 这是"绝不漏、绝不重"两条硬约束的物理载体: 所有"新"都靠稳定 id 与已知集合**全量 diff**(非时间窗口增量), 所有"已通知"都打标记。依赖 `models` 与 `sqlite3`, 不碰网络, 可完全离线单测。

完整 DDL:

```sql
-- 已知作业列(gradebook column)快照; 新列出现即"新作业"
CREATE TABLE IF NOT EXISTS columns (
    id          TEXT PRIMARY KEY,        -- column id, 全量 diff 主键
    course_id   TEXT NOT NULL,
    name        TEXT NOT NULL,
    due_utc     TEXT,                    -- grading.due, ISO UTC; NULL=无截止(已在 client 过滤汇总列)
    content_id  TEXT,
    possible    REAL,
    first_seen  TEXT NOT NULL,           -- 本地首次发现时间(UTC)
    notified    INTEGER NOT NULL DEFAULT 0   -- 1=已就"新作业"通知, 幂等去重
);

-- 我对每个 column 的完成/出分状态; 出分(status→Graded 或 score 由空变非空)即触发"出分"事件
CREATE TABLE IF NOT EXISTS my_status (
    column_id   TEXT PRIMARY KEY REFERENCES columns(id),
    status      TEXT NOT NULL,           -- None | NeedsGrading | Graded
    score       REAL,
    graded_notified INTEGER NOT NULL DEFAULT 0,
    manual_done INTEGER NOT NULL DEFAULT 0   -- dashboard 勾选(纸质/线下作业); scanner 必须尊重
);

CREATE TABLE IF NOT EXISTS announcements (
    id          TEXT PRIMARY KEY,        -- announcement id, 全量 diff
    course_id   TEXT NOT NULL,
    title       TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    notified    INTEGER NOT NULL DEFAULT 0
);

-- 内容树节点; modified 变化 ⇒ 新课件/更新上传
CREATE TABLE IF NOT EXISTS content_nodes (
    id            TEXT PRIMARY KEY,
    course_id     TEXT NOT NULL,
    title         TEXT NOT NULL,
    handler       TEXT NOT NULL,         -- resource/x-bb-folder | -document | -file | -assignment
    modified_utc  TEXT,
    first_seen    TEXT NOT NULL,
    notified      INTEGER NOT NULL DEFAULT 0,   -- "首次出现"通知位
    notified_modified_utc TEXT                  -- 上次已就此节点通知时的 modified; 用于"更新"二次通知去重
);

-- 下载镜像清单; 按 attachment id + 修改时间增量, 避免重复下载
CREATE TABLE IF NOT EXISTS mirror_files (
    att_id        TEXT PRIMARY KEY,
    course_id     TEXT NOT NULL,
    content_id    TEXT NOT NULL,
    file_name     TEXT NOT NULL,
    local_path    TEXT NOT NULL,
    content_modified_utc TEXT,           -- 与 content_nodes.modified 比对决定是否重下
    downloaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (        -- last_scan 等单值
    key TEXT PRIMARY KEY, value TEXT
);
CREATE INDEX IF NOT EXISTS idx_columns_course ON columns(course_id);
CREATE INDEX IF NOT EXISTS idx_columns_due    ON columns(due_utc);
```

关键函数签名:

```python
def init_db(path=DB_PATH) -> None                         # 建表; 幂等, 每次进程启动可安全调
def diff_columns(seen: list[Column]) -> list[Column]      # 返回 id 不在表中的(=新作业); 落库, notified=0
def diff_announcements(seen) -> list[Announcement]        # 同上, 全量 id diff
def diff_content(seen) -> list[ContentNode]               # 新 id, 或已知 id 但 modified 晚于 notified_modified_utc
def known_columns(course_id) -> list[Column]              # scanner 据此对每个已知列查 my_status
def upsert_my_status(s: MyColumnStatus) -> bool           # 返回 True 当 status 跨入 Graded / score 由空变非空
def mark_notified(table: str, ref_id: str) -> None        # 打"已通知"标记 → 幂等去重(content 同时写 notified_modified_utc)
def mark_done(column_id: str, done: bool) -> None         # dashboard 写回 manual_done
def open_tasks() -> list[Task]                            # dashboard 查询: 未完成, 按 due_utc 升序
def last_scan() -> datetime | None
def set_last_scan(dt: datetime) -> None
```

**"绝不漏"在此落地**: diff 是与全表的全量 id 比对, 不依赖"上次扫描时间窗口"。`last_scan` 仅供 dashboard 显示"最近未扫"提示, **绝不参与 diff 判定**——这是关键, 否则一旦把 `last_scan` 当增量游标用就会重新引入"窗口外漏检"。即使多天未扫, 累积的新 id 下次全部检出。

**"绝不重复"在此落地**: 同一事件只通知一次, 靠四个标记位(`columns.notified` / `announcements.notified` / `content_nodes.notified` / `my_status.graded_notified`)。多触发源共享一库时, 第二个触发源看到标记已置位即跳过。"课件更新"是唯一的"二次通知"场景, 用 `notified_modified_utc` 与当前 `modified` 比对界定, 避免同一版本反复通知。

**并发安全**: 多进程可能同时写库(dashboard 周期循环 + 一次手动 `/bb-scan`)。`store` 以 SQLite WAL 模式打开, 所有"读旧值→判定→置标记"的复合操作走单条 `UPDATE ... WHERE notified=0` 的条件写, 由数据库保证原子, 不需要应用层锁——见 §3.5 "协调一张表而非协调进程"。

#### L1: `notifier.py`

职责: **把 `Event` 列表推到已启用渠道**, 渠道插件式。v1 只实现 macOS。依赖 `models`。

```python
class Notifier(Protocol):
    def notify(self, events: list[Event]) -> None: ...      # 返回前即视为"已送达本渠道"; 异常上抛由 scanner 决定是否打标

class MacNotifier:           # osascript display notification / terminal-notifier
    def notify(self, events): ...   # 标题=课程+类型, 正文=作业名+ddl(已转 +8); 不含密码/token/cookie
```

#### L2: `scanner.py`(唯一编排者)

职责: 把 L1 串成一次完整扫描事务。这是 hook / command / MCP / dashboard 周期循环全部最终调用的函数。它**不持有任何"新/旧""通知过没"的判断**——这些全在 `store`; `scanner` 只负责拉取与按序调用。

```python
def scan(courses_filter: list[str] | None = None) -> list[Event]:
    session = auth.get_session()
    client  = bbclient.BBClient(session)
    uid     = client.whoami()                      # 真实 uid, 不能用 me 子资源
    events: list[Event] = []
    for course in _active_courses(client, uid):    # term + availability(Yes/Term) + 黑白名单过滤
        for col in store.diff_columns(client.list_columns(course.id)):
            events.append(Event("new_assignment", ...))   # diff_columns 已滤掉无 due 汇总列, 全部有 due
        for ann in store.diff_announcements(client.list_announcements(course.id)):
            events.append(Event("new_announcement", ...))
        for node in store.diff_content(client.get_contents(course.id)):
            events.append(Event("new_content", ...))
        for col in store.known_columns(course.id):
            if store.upsert_my_status(client.my_status(course.id, col.id, uid)):
                events.append(Event("graded", ...))
    notifier.MacNotifier().notify(events)          # 先通知
    for e in events: store.mark_notified(...)      # 通知返回后才打标 → 崩溃也不会漏
    store.set_last_scan(now_utc())
    return events
```

两条编排级的"绝不漏"保证, 必须按此顺序, 不可调换:

1. **先通知成功、再 `mark_notified`。** 若在通知与打标之间崩溃, 标记没打上, 下次扫描重新检出该事件——宁可重试一次也不丢通知。在"绝不漏 > 绝不重"的裁决下偏向不漏, 而 macOS 通知重复一次的代价远小于漏一次。注意这把"绝不重"的责任收窄到了一个极小窗口(通知已发但打标前崩溃), 且仅影响通知去重, 不影响 dashboard 任务列表(后者只读 `columns`/`my_status` 的事实, 与 `notified` 位无关)。

2. **`diff_*` 必须在 `mark_notified` 之前完成全部落库。** `diff_columns` 等在返回"新项"的同时就把新 id 写进表(`notified=0`); 即便后续通知或打标失败, 这些 id 已在库里, 不会因为本次崩溃而被当成"从未见过"再次误判——它们只是 `notified` 仍为 0, 等下次补发通知。这把"已发现"与"已通知"两个状态解耦, 是崩溃安全的核心。

`_active_courses` 的过滤口径与实测一致: 先按 `terms` 表定位当前学期, 再要求 `role=Student` 且 `availability.available ∈ {Yes, Term}`, 最后套用户配置的课程黑/白名单。

#### L2: `downloader.py` / `dashboard.py` / `mcp_server.py` / `cli.py`

- **`downloader.mirror(course_id, dest=MIRROR_ROOT)`**: 递归 `get_contents` → 按文件夹结构在本地建目录 → 对每个 attachment 比 `mirror_files.content_modified_utc`, 仅新增/改动的跟随 302 下载。增量、可重入。下载写入用"临时文件 + 原子 rename", 中断不会留下半截文件被误判为已下载。
- **`dashboard.create_app() -> FastAPI`**: 路由 `GET /`(渲染 `dashboard/templates/index.html`)、`GET /api/tasks`(= `store.open_tasks()`)、`POST /api/tasks/{id}/done`(= `store.mark_done`)。绑 `127.0.0.1`。**唯一可常驻**的组件; 存活期内按配置间隔在后台线程调 `scanner.scan()`(= 可选的"定时触发", 不依赖任何 OS 级代理)。清单页对 `last_scan` 过旧且有临近 ddl 的情形做醒目提示, 缓解"不开 Claude Code 当天不扫"。
- **`mcp_server.main()`**: FastMCP, 暴露 6 个工具 `scan_now / list_tasks / download_course / mark_done / list_courses / open_dashboard`, 每个工具体≈一行转发到对应 L2 函数。
- **`cli.main()`**: argparse 分发 `setup / scan / session-start / dashboard / download / mcp`。`session-start` 子命令是 hook 的目标: 后台非阻塞跑 `scan()`, 确保 dashboard 进程已起(探端口, 已占用则复用), 并把待办摘要打到 stdout 供 hook 经 `additionalContext` 注入会话。

#### L3 外壳: hooks / commands / .mcp.json

外壳层无逻辑, 仅声明"调哪个子命令":

```json
// hooks/hooks.json — SessionStart 异步非阻塞, 不拖慢会话
{ "hooks": { "SessionStart": [
  { "matcher": "*", "hooks": [
    { "type": "command", "command": "bbwatch session-start --async" } ] } ] } }
```

```json
// .mcp.json — CC 以 stdio 拉起 MCP server, 随会话存活
{ "mcpServers": { "bbwatch": { "command": "bbwatch", "args": ["mcp"] } } }
```

`commands/bb-scan.md` 等是 Markdown 命令文件, 正文指示 Claude 运行 `bbwatch scan` 并把返回的事件摘要报告给用户——同样不含任何 BB API 知识。

### 3.5 进程模型: 谁常驻, 谁一次性

bbwatch 刻意**只有一个可能常驻的进程**, 这是"不安装任何 OS 级后台代理(无 launchd/cron)"硬约束的直接体现。进程分四类:

| 进程 | 生命周期 | 由谁拉起 | 跑什么 |
|---|---|---|---|
| `bbwatch session-start` | **一次性**, 跑完即退 | Claude Code `SessionStart` hook | 异步扫一次 + 确保 dashboard 已起 + 输出待办摘要 |
| `bbwatch scan` / `download` / `setup` | **一次性**, 跑完即退 | `/bb-*` slash command | 单次用例, 把结果回报给会话 |
| `bbwatch mcp`(MCP server) | **随会话存活**(stdio 连着 CC 就活, 会话结束即被 CC 回收) | Claude Code 经 `.mcp.json` | 等待 MCP 工具调用, 每次转发到 L2 |
| `bbwatch dashboard`(FastAPI@127.0.0.1) | **常驻**(用户在用 Claude Code 期间; 唯一长跑进程) | `session-start` 或 `/bb-dashboard` 首次启动(端口已占则复用) | 服务清单页 + 进程内可选周期扫描 |

进程模型的几条规则:
- **常驻进程只有 dashboard 一个**, 且它"常驻"的边界就是用户使用 Claude Code 的时段——不开 Claude Code 就没有任何 bbwatch 进程在跑(已确认接受此取舍; 缓解措施是清单页对"临近 ddl 但最近未扫"做醒目提示)。这正是放弃 launchd/cron 的代价与边界。
- **dashboard 单例**: `session-start` 与 `/bb-dashboard` 启动 dashboard 前先探端口, 已占用则不重复起。这样无论开几个 Claude Code 会话, 周期扫描循环只有一份, 不会并发多扫 BB(对 BB 温和)。
- **所有进程无共享内存, 只共享 SQLite 文件**。即便单例失效(罕见竞态下两个 dashboard 抢起, 或周期循环与一次手动 `/bb-scan` 撞上), 正确性也不依赖"只有一个写者": `scanner.scan()` 的拉取是只读的, 写库则全部经 `store` 的条件写(WAL + `UPDATE ... WHERE notified=0` 原子置标), 因此**并发触发也幂等去重, 最坏只是对 BB 多打几次请求, 绝不产生重复通知, 也绝不漏检**。这是把"绝不重复"约束从"协调进程"降维成"协调一张表"的关键, 也是为什么本架构不需要任何分布式锁或消息队列。

---

(本章所有 BB 断言均与实测事实对齐: ADFS OAuth2 `client_id=4b71b947-…`; `whoami` 用 `users/me` 合法、但 `users/{uid}/courses` 子资源必须真实 uid; 在读判定为 `role=Student ∧ availability ∈ {Yes,Term}`; gradebook column 的 `grading.due`/`contentId`/`score.possible` 及汇总列过滤; per-user `status` 三态 `None/NeedsGrading/Graded`; calendar 窗口 ≤16 周且需首尾相接翻页; 内容树两级 `contents` + `contents/{id}/children` 递归、`contentHandler.id` 为 `resource/x-bb-folder|-document|-file|-assignment`; `paging.nextPage` 仅在有下一页时出现; 附件 `download` 返回 302 需跟随; 时间 UTC、展示转 +8; 传输层用 `curl_cffi`、失败回退子进程 `curl`、尊重 Clash 7890 代理。)

---

## 4. 数据模型与 SQLite Schema

### 4.1 设计目标与不变量

本章 schema 服务于全文最高优先级的两条非功能需求——**绝不漏 (no miss)** 与 **绝不重复 (no duplicate)**。整个数据模型围绕一个核心思想：

> **以 BB 返回的稳定 id 为真值锚点，本地表保存"已知集合"的全量快照；每次扫描做"拉取集 vs 已知集"的全量 diff（而非时间窗口增量），diff 结论落 `events` 表并打 `state` 标记；去重靠主键 / 唯一约束在 DB 层强制，而非靠应用逻辑记忆。**

由此衍生贯穿所有表的不变量：

- **I1（稳定身份）**：每个被监控对象都有来自 BB 的稳定字符串 id（`column.id`、`announcement.id`、`content.id`、`attachment.id`、`course.id`、`term.id`）。这些 id 在 BB 内部不变，可直接做主键。注意 `attachment.id` 仅在所属 content 作用域内唯一（见 §4.4.7），故用复合键。
- **I2（全量可重建）**：本地"已知集合"是 BB 当前真实状态的镜像；任何一次扫描即使距上次过了很多天，diff 也会把累计的所有新项一次性检出（findings §6）。**diff 永不以 `last_scan_utc` 做时间裁剪**——`sync_state` 里的时间戳只用于展示与"数据可能陈旧"提示，绝不用来跳过对象。
- **I3（幂等去重）**：一个 (对象, 提醒类型, variant) 组合在 `events` 表中至多一行，由 `UNIQUE(dedup_key)` 保证。多触发源（SessionStart / `/bb-scan` / 周期循环）并发或重复触发，写入同一行只会 `ON CONFLICT DO NOTHING`，不产生第二条通知。通知侧再加一层：notifier 仅取 `state='new'`，推送后原子置 `notified`。
- **I4（凭据隔离）**：DB 中不存任何密码、cookie、token、Authorization 头。凭据只进 macOS 钥匙串（见 §5/§8）。`config` 表只存非敏感配置。
- **I5（时间统一）**：所有时间戳列以 **UTC ISO-8601 字符串**原样存储（BB 返回即 UTC，形如 `2026-06-30T15:59:00.000Z`），展示层转东八区 +8。不在 DB 里做时区转换，避免双重偏移。字符串 ISO-8601（含末尾 `Z`、毫秒、定宽）满足字典序 = 时序，故可直接 `ORDER BY due_utc`。

### 4.2 物理布局与连接约定

- 单文件 SQLite：`~/.bbwatch/bbwatch.db`；目录 `~/.bbwatch/` 权限 `0700`。课件镜像默认根：`~/.bbwatch/mirror/`（见 `downloads` 表）。
- 连接 PRAGMA（每次打开即设置）：

```python
# engine/store/db.py
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".bbwatch" / "bbwatch.db"

def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=5.0)  # autocommit; 显式 BEGIN
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")      # 清单服务读 + 扫描器写 可并发
    conn.execute("PRAGMA foreign_keys=ON;")        # 外键约束生效（SQLite 默认关闭，必须每连接显式开）
    conn.execute("PRAGMA busy_timeout=5000;")      # 多触发源争锁时等待而非立刻报 locked
    conn.execute("PRAGMA synchronous=NORMAL;")     # WAL 下安全且更快
    return conn
```

> **为什么 WAL**：清单页（FastAPI 进程）持续读、扫描器周期写，WAL 允许读写并发，避免 `database is locked`。`busy_timeout` 进一步缓冲多触发源（I3 场景）的写争用。
>
> **写事务边界**：每轮"一门课一个维度"的 diff 在一个显式 `BEGIN IMMEDIATE … COMMIT` 内完成——快照 UPSERT 与对应 `events` 入队必须同一事务原子提交，否则崩溃时可能"快照已更新但事件未入队"，下次 diff 因 id 已知而漏报（破坏 I2）。`BEGIN IMMEDIATE` 立即取写锁，配合 `busy_timeout` 让并发触发源串行化而非互相看到半成品。

所有时间列存 TEXT（UTC ISO 字符串），布尔列用 `INTEGER`（0/1）。带 `user_id` 的表（`column_status`）保证状态语义清晰（一个 column 的状态是"对某个 uid"而言的），并为未来多账户留余地。

### 4.3 schema 版本与初始化

```python
# engine/store/schema.py
SCHEMA_VERSION = 1

def init_db(conn) -> None:
    with conn:                                     # 包裹成单事务
        conn.execute("BEGIN IMMEDIATE")
        conn.executescript(_DDL)                   # 见 §4.4 各表 DDL 拼接（全部 IF NOT EXISTS）
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )

def migrate(conn) -> None:
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    have = int(row["value"]) if row else 0
    for v in range(have + 1, SCHEMA_VERSION + 1):  # 逐版本 apply
        MIGRATIONS[v](conn)
    if have != SCHEMA_VERSION:
        conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
```

`meta` 是一张极小键值表，专门承载 schema 版本（与业务用的 `config` 表分开，避免迁移逻辑依赖业务表结构）：

```sql
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

### 4.4 各表 DDL

下列 DDL 即 `_DDL` 字符串的全部内容，可直接 `executescript`。表按依赖顺序排列（被引用者在前）。

#### 4.4.1 `terms` — 学期表

来源：`GET /learn/api/public/v1/terms?limit=100`（findings §1）。用于识别"当前学期"，进而过滤在读课程。

```sql
CREATE TABLE IF NOT EXISTS terms (
    id          TEXT PRIMARY KEY,          -- BB termId 内部 id, 如 "_122_1"
    external_id TEXT,                       -- 人类可读 termId, 如 "2550UG"
    name        TEXT,
    available   TEXT,                       -- availability.available
    start_utc   TEXT,                       -- availability.duration.start (UTC ISO)
    end_utc     TEXT,                       -- availability.duration.end   (UTC ISO)
    is_current  INTEGER NOT NULL DEFAULT 0, -- 派生: 当前 UTC 落在 [start,end] 内 -> 1
    raw_json    TEXT,                       -- 原始 JSON, 兜底未来字段
    first_seen_utc TEXT NOT NULL,
    last_seen_utc  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_terms_current ON terms(is_current);
```

- **主键** `id`（term 内部 id，I1）。terms 基本静态，不产生用户事件；`is_current` 每次扫描重算，决定 `courses` 过滤范围。
- **稳健性**：`terms.duration` 可能为空（部分学期无明确起止）。此时 `is_current` 退化为"该 term 下有 `availability ∈ {Yes,Term}` 的在读课"判定，避免因 term 缺时间窗而误判全部课程不在读（防漏，I2）。`raw_json` 落地保证 BB 增字段不丢信息。

#### 4.4.2 `courses` — 课程表

来源：`GET users/{uid}/courses?expand=course&limit=100`（需翻页；`me` 别名在此子资源**不可用**，必须真实 uid 如 `_49765_1`；uid 本身由 `GET users/me` 解析，findings §1 / 附录 A）。实测 **19 门 membership、17 门在读**。

```sql
CREATE TABLE IF NOT EXISTS courses (
    id           TEXT PRIMARY KEY,          -- course.id 内部, 如 "_17236_1"
    course_id    TEXT,                       -- 人类可读, 如 "MAT3007:Optimization_L01"
    name         TEXT,
    term_id      TEXT REFERENCES terms(id),
    role         TEXT,                       -- courseRoleId, 如 "Student"
    availability TEXT,                       -- availability.available: Yes/No/Term
    ultra_status TEXT,                       -- "Classic" / "Ultra"
    watched      INTEGER NOT NULL DEFAULT 1, -- 黑/白名单: 1=纳入扫描, 0=排除
    enrolled     INTEGER NOT NULL DEFAULT 1, -- 本轮判定"在读" -> 1
    raw_json     TEXT,
    first_seen_utc TEXT NOT NULL,
    last_seen_utc  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_courses_term ON courses(term_id);
CREATE INDEX IF NOT EXISTS idx_courses_scan ON courses(watched, enrolled);
```

- **主键** `id`（course.id 内部 id，I1）。
- **`enrolled` 判定**：`role='Student' AND availability ∈ {Yes, Term}`（findings §1 的 17 门口径）。每次扫描重算；掉课/结课自动置 0、不再扫，但历史行保留（不破坏旧 events 的外键）。
- **`watched`**：用户经清单页/配置设的黑白名单（可排除体育/已结课）。扫描器只遍历 `watched=1 AND enrolled=1` 的课。
- 课程本身一般不产生"新课程"通知；它是其它表外键的根。课程集合 diff 只用于决定"扫哪些课"。

#### 4.4.3 `columns` — 任务（成绩册栏目）

**最关键的表。** 来源：`GET courses/{cid}/gradebook/columns`（findings §2）。带 `grading.due` 的列 = 有截止的作业/quiz；`Weighted Total`/`Total` 等汇总列无 due，由 `is_assignment` 标记过滤。

```sql
CREATE TABLE IF NOT EXISTS columns (
    id             TEXT PRIMARY KEY,          -- gradebook column id（稳定, 新 id=新任务）
    course_id      TEXT NOT NULL REFERENCES courses(id),
    name           TEXT NOT NULL,             -- 如 "Homework 4"
    due_utc        TEXT,                      -- grading.due (UTC ISO); NULL=无截止
    content_id     TEXT,                      -- grading.contentId, 关联 contents.id（可空）
    score_possible REAL,                      -- score.possible
    grading_type   TEXT,                      -- grading.type, 如 "Manual"/"Attempts"
    is_assignment  INTEGER NOT NULL DEFAULT 0,-- 过滤汇总列: 见 is_real_assignment()
    raw_json       TEXT,
    first_seen_utc TEXT NOT NULL,             -- 本地首次发现 = "新任务"事件的时间锚
    last_seen_utc  TEXT NOT NULL,
    deleted        INTEGER NOT NULL DEFAULT 0 -- 本轮未再出现且曾存在 -> 1（软删）
);
CREATE INDEX IF NOT EXISTS idx_columns_course ON columns(course_id);
CREATE INDEX IF NOT EXISTS idx_columns_due    ON columns(due_utc) WHERE due_utc IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_columns_assign ON columns(is_assignment, deleted);
```

- **主键** `id`（column id，I1）。**新作业检出 = 拉取集中出现一个 `id` 不在本表 → 入 `columns` 并产生 `events(kind='new_assignment')`**（findings §2）。
- **`due_utc` 部分索引**：清单页"未完成按 ddl 升序"是高频查询；只索引非空 due 的列，体积小、命中快。
- **`content_id`**：把任务连到内容树里的题面（`x-bb-assignment`），供下载/打开。可空，因为部分 column 无关联内容。
- **`is_assignment` 过滤规则**（写库时计算；兼容 BB 把 `contentId` 放在 `grading` 下或顶层两种形态）：

```python
def is_real_assignment(col: dict) -> bool:
    name = (col.get("name") or "").strip().lower()
    if name in {"weighted total", "total"}:           # findings §2: 汇总列
        return False
    grading = col.get("grading") or {}
    if grading.get("due"):                             # 有 due 一定是作业
        return True
    # 无 due 但有关联内容且非汇总 -> 可能是线下/纸质作业列, 保守纳入(防漏 I2)
    return bool(grading.get("contentId") or col.get("contentId"))
```

> **为什么"保守纳入"**：宁可多收一个无 due 的非汇总列进清单（用户可手动忽略），也不能漏一个真作业。漏=违反 I2；多=用户一键 `watched`/勾选即可消解。

- **`deleted` 软删**：BB 偶尔删列。全量 diff 时本地有、拉取集无 → 置 `deleted=1`（不物理删，保留 `column_status` 历史与已发事件的去重锚，防止"删后又出现"被当全新重发）。清单页隐藏 `deleted=1`。
- **diff 支撑（I2）**：每轮把该课全部 column 拉全（翻页），逐 id UPSERT 并更新 `last_seen_utc`；`due_utc` 变化（老师改 ddl）触发 `events(kind='due_changed')`。

#### 4.4.4 `column_status` — 我的 per-user 完成状态快照

来源：`GET courses/{cid}/gradebook/columns/{colId}/users/{uid}`（findings §2）。一举支撑"已完成判定"与"出分提醒"。

```sql
CREATE TABLE IF NOT EXISTS column_status (
    column_id   TEXT NOT NULL REFERENCES columns(id),
    user_id     TEXT NOT NULL,               -- BB uid, 如 "_49765_1"
    status      TEXT,                         -- None / NeedsGrading / Graded
    score       REAL,                         -- 出分后非空
    graded      INTEGER NOT NULL DEFAULT 0,   -- status=='Graded' OR score 非空 -> 1
    submitted   INTEGER NOT NULL DEFAULT 0,   -- status ∈ {NeedsGrading,Graded} -> 1
    attempt_id  TEXT,                          -- 若 API 返回最近一次 attempt id
    raw_json    TEXT,
    first_seen_utc TEXT NOT NULL,
    updated_utc    TEXT NOT NULL,             -- 状态最近一次发生变化的时间
    PRIMARY KEY (column_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_status_user ON column_status(user_id, graded, submitted);
```

- **复合主键** `(column_id, user_id)`：一个任务对一个用户唯一一行（I1）。
- **diff 三件事（findings §2，幂等）**——拉到新状态后与旧行比对：
  - `status` `None → NeedsGrading`：刚提交（不通知，仅清单显示"待批"，`submitted` 置 1）。
  - `graded` `0 → 1`（或 `score` `NULL → 非空`）：**出分** → `events(kind='grade_posted')`。
  - 任何变化都更新 `updated_utc`；无变化不写、不发事件（I3）。
- **"已交还提醒"误报规避**：清单页"未完成"过滤 `submitted=0`，已交/已批的任务不再催（findings §2/§6）。

```python
def derive_status_flags(u: dict) -> tuple[int, int]:
    status = u.get("status")            # None / NeedsGrading / Graded
    score  = u.get("score")
    graded = int(status == "Graded" or score is not None)
    submitted = int(status in ("NeedsGrading", "Graded"))
    return graded, submitted
```

> **拉取范围 = 全量，不止"未交"**：`column_status` 必须对**每个未软删的 `is_assignment` column** 拉取，包括本地已标 `graded` 的——否则改分（`score` 变化）或撤回成绩会漏。这正是全量 diff 而非增量的体现（I2）。对 BB 温和：此处是请求量大头，按课串行 + 请求间延时（§8）。

#### 4.4.5 `announcements` — 公告

来源：`GET courses/{cid}/announcements`（findings §3）。新 id = 新公告；正文常含考试/补课信息。

```sql
CREATE TABLE IF NOT EXISTS announcements (
    id           TEXT PRIMARY KEY,          -- 公告 id（稳定, 新 id=新公告）
    course_id    TEXT NOT NULL REFERENCES courses(id),
    title        TEXT,
    body         TEXT,                       -- HTML 正文; 抽取考试/补课关键词的输入
    created_utc  TEXT,                       -- created (发布时间, 排序键)
    modified_utc TEXT,
    raw_json     TEXT,
    first_seen_utc TEXT NOT NULL,
    last_seen_utc  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ann_course  ON announcements(course_id);
CREATE INDEX IF NOT EXISTS idx_ann_created ON announcements(created_utc);
```

- **主键** `id`（公告 id，I1）。**新公告 = 拉取集出现新 id → `events(kind='new_announcement')`**（findings §3/§6）。
- `body` 落地供后续"考试/补课/座位"关键词抽取；`created_utc` 是清单页/摘要排序键。

#### 4.4.6 `contents` — 内容树（含 modified）

来源：`GET courses/{cid}/contents` + 递归 `contents/{id}/children`（findings §4）。`modified` 用于"新课件/更新"检测。

```sql
CREATE TABLE IF NOT EXISTS contents (
    id           TEXT PRIMARY KEY,          -- content id（稳定）
    course_id    TEXT NOT NULL REFERENCES courses(id),
    parent_id    TEXT,                       -- 父内容 id（顶层为 NULL）; 自引用树
    title        TEXT,
    handler      TEXT,                       -- contentHandler.id: x-bb-folder/-document/-file/-assignment
    position     INTEGER,
    has_children INTEGER NOT NULL DEFAULT 0,
    created_utc  TEXT,
    modified_utc TEXT,                       -- 新上传/更新检测的核心列
    raw_json     TEXT,
    first_seen_utc TEXT NOT NULL,
    last_seen_utc  TEXT NOT NULL,
    deleted      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_contents_course   ON contents(course_id);
CREATE INDEX IF NOT EXISTS idx_contents_parent   ON contents(parent_id);
CREATE INDEX IF NOT EXISTS idx_contents_modified ON contents(modified_utc);
```

- **主键** `id`（content id，I1）。`parent_id` 自引用建树（递归 children）。
- **新课件检出（两种信号，findings §4）**：
  - **新项**：拉取集出现新 `id` → `events(kind='new_content')`。
  - **更新项**：已知 `id` 但 `modified_utc` 比库里新 → `events(kind='content_updated')`。
- `handler` 决定下载策略：只有 `x-bb-document/x-bb-file/x-bb-assignment` 可能挂附件，`x-bb-folder` 仅递归。`deleted` 软删同 `columns`。
- **递归覆盖 = 无漏前提**：必须按 `has_children` 递归拉全整棵树（findings §4），任一层翻页/递归缺失都会漏课件。递归深度与节点数记入 `sync_state.items_seen` 做自检。

#### 4.4.7 `attachments` — 附件

来源：`GET contents/{id}/attachments`（findings §4）。下载走 `attachments/{aid}/download`（302 跳真实文件，需跟随）。

```sql
CREATE TABLE IF NOT EXISTS attachments (
    id          TEXT NOT NULL,              -- attachment id（仅在所属 content 内唯一）
    content_id  TEXT NOT NULL REFERENCES contents(id),
    course_id   TEXT NOT NULL REFERENCES courses(id),
    file_name   TEXT,                        -- fileName
    mime_type   TEXT,                        -- mimeType
    raw_json    TEXT,
    first_seen_utc TEXT NOT NULL,
    last_seen_utc  TEXT NOT NULL,
    deleted     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (content_id, id)             -- attachment id 仅在 content 作用域唯一
);
CREATE INDEX IF NOT EXISTS idx_attach_content ON attachments(content_id);
CREATE INDEX IF NOT EXISTS idx_attach_course  ON attachments(course_id);
```

- **复合主键** `(content_id, id)`：BB attachment id 只在所属 content 内保证唯一（I1，谨慎口径）。
- 附件集合的全量 diff 决定 `downloader` 下载哪些新文件；具体本地落盘登记在 `downloads`，二者分离（attachments 描述"BB 上有什么"，downloads 描述"本地镜像了什么"）。

#### 4.4.8 `downloads` — 本地镜像登记

记录"哪些附件已下到本地的哪个路径、何时、多大/校验值"。`downloader` 据此做增量镜像（已下且未变则跳过）。

```sql
CREATE TABLE IF NOT EXISTS downloads (
    content_id    TEXT NOT NULL,
    attachment_id TEXT NOT NULL,
    course_id     TEXT NOT NULL REFERENCES courses(id),
    local_path    TEXT NOT NULL,            -- 绝对路径, 形如 ~/.bbwatch/mirror/<course>/<folder>/<file>
    file_name     TEXT,
    size_bytes    INTEGER,
    sha256        TEXT,                      -- 下载后算, 防半截文件/变更
    src_modified_utc TEXT,                   -- 下载时对应 content.modified_utc 快照
    status        TEXT NOT NULL DEFAULT 'done', -- pending / done / failed
    downloaded_utc TEXT,
    error         TEXT,
    PRIMARY KEY (content_id, attachment_id),
    FOREIGN KEY (content_id, attachment_id) REFERENCES attachments(content_id, id)
);
CREATE INDEX IF NOT EXISTS idx_downloads_course ON downloads(course_id);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);
```

- **复合主键** `(content_id, attachment_id)`：与 `attachments` 一一对应（I3：同一附件不重复登记/重复下载）。
- **增量镜像判据**：对每个 attachment——`downloads` 无此行 → 下载；有但 `src_modified_utc < contents.modified_utc`（课件被替换）→ 重下并更新 `sha256`；否则跳过。`status ∈ {pending,failed}` 行可断点续传。
- **`src_modified_utc` 代理**：attachment API 不带 modified，用所属 content 的 `modified_utc` 作"源是否变化"的代理（findings §4）。仅当 content 行存在 `modified_utc` 时该判据生效；若为 NULL，回退到"已存在即跳过"，避免误判反复重下。

#### 4.4.9 `events` — 检出的新事件 / 通知队列（含 state）

**去重与通知的中枢。** 每条 diff 结论落一行；`UNIQUE(dedup_key)` 保证 (对象, 类型, variant) 至多一行（I3）。`state` 驱动通知生命周期。

```sql
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,              -- new_assignment | due_changed | new_announcement
                                            --   | new_content | content_updated | grade_posted
    course_id   TEXT REFERENCES courses(id),
    object_type TEXT NOT NULL,              -- column | announcement | content | column_status
    object_id   TEXT NOT NULL,              -- 对应表主键（column.id / announcement.id / ...）
    dedup_key   TEXT NOT NULL,              -- 幂等键: f"{kind}:{object_type}:{object_id}:{variant}"
    title       TEXT,                        -- 通知标题（如 "新作业: Homework 4"）
    body        TEXT,                        -- 通知正文（含转 +8 的 ddl 等）
    due_utc     TEXT,                        -- 冗余存一份, 便于摘要按 ddl 排序
    payload_json TEXT,                       -- 结构化细节, 供清单页/摘要渲染
    state       TEXT NOT NULL DEFAULT 'new',-- new -> notified -> ack / suppressed
    detected_utc TEXT NOT NULL,             -- diff 检出时间
    notified_utc TEXT,                       -- 通知实际弹出时间
    UNIQUE (dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_events_state ON events(state);
CREATE INDEX IF NOT EXISTS idx_events_course ON events(course_id);
CREATE INDEX IF NOT EXISTS idx_events_kind  ON events(kind, detected_utc);
```

- **`dedup_key` + `UNIQUE`**：去重的 DB 级强制。`variant` 让"同对象可多次合法提醒"成立：

  | `kind` | variant | 语义 |
  |---|---|---|
  | `new_assignment` / `new_announcement` / `new_content` | `""` | 一次性，永不重发 |
  | `due_changed` | `<new_due_utc>` | 改一次 ddl 提醒一次；**改回旧值也算一次新变更**（variant 不同），符合"老师又改了"的真实语义 |
  | `content_updated` | `<modified_utc>` | 每次真实更新提醒一次 |
  | `grade_posted` | `<score>`，无分时回退 `'graded'` | 出分一次；改分（score 变）再提醒一次 |

  > **variant 必须确定性**：同一输入永远产出同一 `dedup_key`，否则并发两遍会因 key 不同而双写——这是"绝不重复"在应用层的根。`make_event()` 是 `dedup_key` 的**唯一**生成处。

- **写入即去重**（I3，多触发源安全）：

```python
def enqueue_event(conn, ev: "Event") -> bool:
    cur = conn.execute(
        "INSERT INTO events(kind,course_id,object_type,object_id,dedup_key,"
        " title,body,due_utc,payload_json,state,detected_utc) "
        "VALUES(?,?,?,?,?,?,?,?,?,'new',?) "
        "ON CONFLICT(dedup_key) DO NOTHING",
        (ev.kind, ev.course_id, ev.object_type, ev.object_id, ev.dedup_key,
         ev.title, ev.body, ev.due_utc, ev.payload_json, ev.detected_utc),
    )
    return cur.rowcount == 1            # True=新事件; False=已存在(被忽略)
```

- **`state` 生命周期**：`new`（刚检出，未弹）→ `notified`（已弹 macOS 通知，记 `notified_utc`）→ `ack`（用户清单页确认 / 勾完成）或 `suppressed`（黑白名单 / 静默期内，不弹）。notifier 在**单事务**内 `SELECT … WHERE state='new'` 后立即 `UPDATE … SET state='notified'`，再发系统通知；即便发通知的 `osascript` 调用失败，事件已离开 `new` 队列、不会下次重弹（"绝不重复通知"优先于"绝不漏通知一次显示"——漏弹由清单页兜底，重弹无可挽回地骚扰用户）。多扫描并发也只有一个事务能把某行从 `new` 取走（I3）。
- **静默期处理**：落在 `quiet_hours` 内的新事件不置 `notified`，而是保持 `new` 且不弹；静默期结束后下一次 notifier 轮询自然补发。`suppressed` 仅用于黑白名单等"永不弹"，与静默期区分，避免静默期误吞通知（防漏）。

#### 4.4.10 `config` — 非敏感配置

只存非敏感配置（I4：无密码/cookie/token）。键值结构，便于清单页与命令行共写。

```sql
CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,                     -- JSON 编码值
    updated_utc TEXT NOT NULL
);
```

预期键（示例，非敏感）：

| key | 含义 | 示例 value |
|---|---|---|
| `bb_user_id` | 真实 uid（courses 子资源必需，由 `users/me` 解析后缓存，findings §1） | `"_49765_1"` |
| `scan_interval_minutes` | 周期扫描频率（10~1440 可配） | `30` |
| `mirror_root` | 课件镜像根目录 | `"~/.bbwatch/mirror"` |
| `notify_channels` | 启用通知渠道（v1 仅 macOS） | `["macos"]` |
| `proxy` | 尊重的本机代理 | `"http://127.0.0.1:7890"` |
| `quiet_hours` | 静默时段（不弹通知，本地时区） | `["23:00","08:00"]` |

> **凭据不在此表**：BB 密码只在钥匙串（`keyring`，service=`bbwatch`，account=`bb_user_id`）。`config` 只放可明文的偏好。`bb_user_id` 非敏感（仅内部 id，非密码），缓存于此免去每次扫描重调 `users/me`。

#### 4.4.11 `sync_state` — 扫描状态

记录每个扫描维度的进度，支撑"无漏根基"（即使中断，下次知道续扫 / 全量重扫）。

```sql
CREATE TABLE IF NOT EXISTS sync_state (
    scope            TEXT NOT NULL,         -- "global" | course_id（如 "_17236_1"）
    dimension        TEXT NOT NULL,         -- courses | columns | column_status
                                            --   | announcements | contents | calendar
    last_scan_utc    TEXT,                  -- 该维度上次"开始"全量 diff 的时间（仅展示）
    last_success_utc TEXT,                  -- 上次"成功完成"全量 diff（覆盖完整）的时间
    last_error       TEXT,
    cursor_json      TEXT,                  -- 分页/16周窗口续扫游标（中断恢复用）
    items_seen       INTEGER DEFAULT 0,     -- 本轮拉到的对象数（自检"是否覆盖全量"）
    PRIMARY KEY (scope, dimension)
);
```

- **复合主键** `(scope, dimension)`：每门课每维度一行（如 `("_17236_1","contents")`），加全局行 `("global","courses")`。
- **时间戳仅作展示，绝不做增量过滤**（findings §6）：`last_success_utc` 用于清单页"临近 ddl 但最近未扫"的醒目提示（对应设计文档 §1 缓解措施）；diff 永远全量，时间窗口只决定要不要提示"数据可能陈旧"，绝不用来跳过对象（否则漏，违反 I2）。区分 `last_scan_utc`（开始）与 `last_success_utc`（完整成功）：仅后者能解除陈旧提示，半截扫描不算数。
- **`cursor_json` — 覆盖保证**：日历窗口必须 ≤16 周（findings §2，超出 400）。`calendar` 维度用 cursor 记"已覆盖到哪个 `until`"，按 16 周窗口翻页直至覆盖整学期；REST 分页（`paging` 字段**仅在有下一页时出现**，findings §5）同样以 cursor 续。中断后下次从 cursor 续扫，不丢窗口。
- **`calendar` 维度的角色 = no-miss 交叉校验**：findings §2 实测日历项**全为 `type=GradebookColumn`**（带 `end`=due），是"全课程带 due 的成绩册列"的聚合，无独立 event。因此日历**不单独建表**——其结果回填进 `columns`：若日历出现某带 due 项而 per-course `columns` 扫描未覆盖（如某课临时拉取失败），即触发对该课的补扫。这是跨课程的"漏检兜底网"，与 per-course columns 互为冗余（防漏）。
- **`items_seen` 自检**：若某课某维度本轮拉到 0 项而历史非 0，扫描器告警（疑似会话失效/接口变更/翻页断裂），**不写 `last_success_utc`、不据此 diff 软删**（否则会把"拉取失败"误判为"BB 删光了"而错误软删全部，连带漏掉真实存在项）。宁可保留旧快照、下次重扫（I2）。

### 4.5 各表如何共同支撑"全量 diff 幂等"

| 提醒类型 | 真值表 | diff 谓词（全量，非时间窗口） | `events.kind` | `dedup_key` variant |
|---|---|---|---|---|
| 新作业 + ddl | `columns` | 拉取集 `id` ∉ 本地（`is_assignment=1`） | `new_assignment` | `""` |
| ddl 变更 | `columns` | 同 `id` 但 `grading.due` 变化 | `due_changed` | 新 `due_utc` |
| 新公告 | `announcements` | 拉取集 `id` ∉ 本地 | `new_announcement` | `""` |
| 新课件 | `contents` | 拉取集 `id` ∉ 本地 | `new_content` | `""` |
| 课件更新 | `contents` | 同 `id` 但 `modified` 变新 | `content_updated` | `modified_utc` |
| 出分 | `column_status` | `graded` 0→1 或 `score` 变化 | `grade_posted` | `score`/`'graded'` |

**全量 diff 的统一写法**（以 columns 为例，其余表同构；整体处于外层 `BEGIN IMMEDIATE` 事务内）：

```python
# engine/store/diff.py
def diff_columns(conn, course_id: str, fetched: list[dict], now: str,
                 *, complete: bool) -> list["Event"]:
    """complete=本轮该课 columns 是否成功拉全(翻页无断裂)。未拉全则禁止软删, 防误删漏检。"""
    known = {r["id"]: r for r in conn.execute(
        "SELECT * FROM columns WHERE course_id=? AND deleted=0", (course_id,))}
    events, seen = [], set()
    for col in fetched:                       # fetched = 该课全部列(已翻页, I2)
        if not is_real_assignment(col):
            continue
        cid = col["id"]
        due = (col.get("grading") or {}).get("due")
        seen.add(cid)
        row = known.get(cid)
        if row is None:                       # 新作业
            upsert_column(conn, course_id, col, now, first=True)
            events.append(make_event("new_assignment", course_id, "column", cid,
                                     variant="", due_utc=due))
        else:
            if due != row["due_utc"] and due is not None:   # ddl 改了(含改回; 不把有 due 抹成 NULL 当变更)
                events.append(make_event("due_changed", course_id, "column", cid,
                                         variant=due, due_utc=due))
            upsert_column(conn, course_id, col, now, first=False)
    if complete:                              # 仅在确认拉全时才软删本地有、拉取集无的列
        for cid in set(known) - seen:
            conn.execute("UPDATE columns SET deleted=1, last_seen_utc=? WHERE id=?", (now, cid))
    # 入队去重: enqueue_event 内 ON CONFLICT(dedup_key) DO NOTHING
    return [e for e in events if enqueue_event(conn, e)]
```

要点：

- **无漏（I2）**：`known` 是本地全量已知集，`fetched` 是 BB 当前全量（翻页 + 16 周窗口 + 递归覆盖保证），二者求差即累计新项——多天未扫也补齐，不依赖 `last_scan_utc` 裁剪。`complete` 闸门确保"拉取不完整"绝不触发软删（避免把漏拉误当删除）。
- **无重（I3）**：`make_event` 算出确定性 `dedup_key`，`enqueue_event` 靠 `UNIQUE(dedup_key)` 在 DB 层挡重；`notifier` 单事务取 `state='new'` 并即时置 `notified`。即便 SessionStart 与周期循环同秒并发跑同一门课，`BEGIN IMMEDIATE` 串行化写、events 表最终一行、只弹一次。
- **软删不破链**：`deleted=1` 而非物理删，保住 `column_status`/已发 `events` 外键与去重锚（防"删后又现"被误判全新重发）。
- **`raw_json` 兜底**：每张快照表存原始 JSON，BB 加字段不丢数据，迁移时可回填新列，巩固 I2。

### 4.6 store 层函数签名（供后续章节引用）

```python
# engine/store/__init__.py —— scanner / downloader / dashboard 共用入口
from pathlib import Path
import sqlite3

def connect(path: Path = DB_PATH) -> sqlite3.Connection: ...
def init_db(conn) -> None: ...
def migrate(conn) -> None: ...

# 快照 upsert（全量 diff 时调用）
def upsert_term(conn, term: dict, now: str) -> None: ...
def upsert_course(conn, course: dict, now: str, *, watched: int, enrolled: int) -> None: ...
def upsert_column(conn, course_id: str, col: dict, now: str, *, first: bool) -> None: ...
def upsert_column_status(conn, column_id: str, user_id: str, u: dict, now: str) -> bool: ...  # 返回是否变化
def upsert_announcement(conn, course_id: str, ann: dict, now: str) -> None: ...
def upsert_content(conn, course_id: str, c: dict, now: str) -> None: ...
def upsert_attachment(conn, course_id: str, content_id: str, a: dict, now: str) -> None: ...

# diff（返回去重后的新事件）；complete=本轮该维度是否拉全(控制软删与 last_success_utc)
def diff_columns(conn, course_id: str, fetched: list[dict], now: str, *, complete: bool) -> list["Event"]: ...
def diff_column_status(conn, course_id: str, user_id: str, fetched: list[dict], now: str) -> list["Event"]: ...
def diff_announcements(conn, course_id: str, fetched: list[dict], now: str, *, complete: bool) -> list["Event"]: ...
def diff_contents(conn, course_id: str, fetched: list[dict], now: str, *, complete: bool) -> list["Event"]: ...

# 事件 / 通知
def enqueue_event(conn, ev: "Event") -> bool: ...               # ON CONFLICT(dedup_key) DO NOTHING
def claim_pending_events(conn, now: str) -> list[sqlite3.Row]: ...  # 单事务: 取 state='new' 并即置 'notified'
def ack_event(conn, event_id: int, now: str) -> None: ...
def suppress_event(conn, event_id: int, now: str) -> None: ...

# 清单页 / 完成状态
def list_open_tasks(conn) -> list[sqlite3.Row]: ...             # submitted=0 AND deleted=0, ORDER BY due_utc
def mark_done(conn, column_id: str, user_id: str, now: str) -> None: ...  # 线下作业手动勾选

# 下载登记
def need_download(conn, content_id: str, attachment_id: str, src_modified_utc: str | None) -> bool: ...
def record_download(conn, *, content_id, attachment_id, course_id, local_path,
                    size_bytes, sha256, src_modified_utc, status, now, error=None) -> None: ...

# 扫描状态
def get_sync_state(conn, scope: str, dimension: str) -> sqlite3.Row | None: ...
def update_sync_state(conn, scope: str, dimension: str, *, last_scan_utc=None,
                      last_success_utc=None, cursor_json=None, items_seen=None, error=None) -> None: ...
def get_config(conn, key: str, default=None): ...
def set_config(conn, key: str, value, now: str) -> None: ...
```

> `Event` 为引擎内 dataclass（`kind / course_id / object_type / object_id / dedup_key / title / body / due_utc / payload_json / detected_utc`）。`make_event(...)` 工厂据 §4.5 规则生成**确定性** `dedup_key`，是"绝不重复"在应用层的唯一来源，与 DB 的 `UNIQUE(dedup_key)` 构成双保险。`claim_pending_events` 把"取出待通知"与"标记已通知"合并为单事务，是"绝不重复通知"在通知侧的唯一来源。

---

相关文件路径（绝对）：
- DB 文件：`~/.bbwatch/bbwatch.db`；镜像根：`~/.bbwatch/mirror/`
- 实现落点：`/Users/mac/Programming/cuhkszbb/engine/store/`（`db.py` 连接/PRAGMA、`schema.py` DDL+迁移、`diff.py` 全量 diff、`__init__.py` 对外函数）
- 设计依据：`/private/tmp/claude-501/-Users-mac-Programming-cuhkszbb/82db56a5-da23-42e2-a147-6a5d1bfa6443/scratchpad/bb_findings.md`、`/Users/mac/Programming/cuhkszbb/docs/superpowers/specs/2026-06-28-bbwatch-design.md`

---

## 5. 鲁棒性: 绝不漏、绝不重复(核心)

> 本章是 bbwatch 的灵魂。功能再多,只要漏一次 ddl 或对同一件事重复轰炸,工具就失去信任。本章把"绝不漏(no miss)"和"绝不重复(no duplicate)"从口号落成**可实现的状态机、SQL DDL、事务边界与函数签名**,并对每条 BB 相关断言对齐 §2 实测事实。

设计的两条总纲:

- **绝不漏的根基 = 与本地已知集合做全量 diff,而非时间窗口增量。** 每次扫描把 BB 当前可见的稳定 id 全集,与本地 `seen_entity` 表做差集,差集即"新项"。这与"上次扫到现在新增了什么"的增量思路根本不同:后者依赖每次都按时扫、且时间窗口无缝拼接,任何一次缺席都留下永久空洞;前者只要某次扫到,累积的所有新项一次补齐。
- **绝不重复的根基 = 稳定 id 一旦进入"已通知"终态就永久打标,所有触发源共用同一 SQLite + 进程锁,投递幂等。**

### 5.1 实体的幂等键(稳定身份的精确定义)

去重的前提是每类实体有一个**跨扫描稳定、且能唯一标识"这一件需要被通知的事"**的键。下表给出 bbwatch 监控的全部实体及其幂等键,均来自 §2–§4 实测字段:

| 事件类型 | BB 实体 | 端点(已实测) | 稳定 id 字段 | 幂等键 `entity_key` |
|---|---|---|---|---|
| 新作业 | gradebook column(带 `grading.due`) | `GET courses/{cid}/gradebook/columns` | `column.id`(如 `_88123_1`) | `col:{cid}:{column.id}` |
| 新公告 | announcement | `GET courses/{cid}/announcements` | `announcement.id` | `ann:{cid}:{id}` |
| 新课件 | content item | `GET courses/{cid}/contents[/{id}/children]` | `content.id` | `content:{cid}:{id}` |
| 新附件 | attachment | `GET .../contents/{id}/attachments` | `attachment.id` | `att:{cid}:{contentId}:{aid}` |
| 出分 | per-user grade | `GET .../columns/{colId}/users/{uid}` | `column.id`(状态附着其上) | `grade:{cid}:{column.id}` |

**关键约定:**

1. **幂等键带 `cid` 前缀。** BB 的内部 id(如 `_88123_1`)由各对象空间独立分配,跨课程理论上可能重号;加 `cid` 前缀彻底杜绝跨课程串扰,也让人读得懂。`cid` 一律用 `course.id`(内部 id,如 `_17236_1`),不用人类可读的 `courseId`(如 `MAT3007:Optimization_L01`)——后者可被教务改写,不稳定。
2. **"出分"与"新作业"共用 `column.id` 作锚,但事件类型不同 → 幂等键不同(`col:` vs `grade:`)。** 同一个 column,先作为"新作业"通知一次,出分后再作为"出分"通知一次,这是两件事,各自有独立终态,互不抑制。
3. **稳定身份只认 id,不认 name/title。** 老师把 `Homework 4` 改名成 `HW4 (final)` 不应触发"新作业"。name 仅作展示与变更日志,不进幂等键。
4. **只有带 `grading.due` 的 column 才进"新作业"事件流。** §2 实测:`gradebook/columns` 里含 `Weighted Total`/`Total` 等汇总列(无 `due`)。这些列在拉取阶段即过滤,**不写入 `seen_entity`**,不产生任何作业事件——但其 per-user `score` 仍可被成绩跟踪功能单独读取(非本章去重范畴)。
5. **"出分"的去重锚是状态值,不只是 id 存在性。** 见 §5.6:`grade:` 事件在 `status` 由非 `Graded` 翻转为 `Graded`(或 `score` 由空变非空)的那一刻才进入 pending,且只触发一次。

**重命名/删除再加的处理(基于 id 而非内容):**

- **重命名:** id 不变 → diff 判定为已知 → 不重发。把新 `name`/`title` 写回 `seen_entity.payload_json`,清单页展示用新名,并可选记一条 `changelog`(非通知)。
- **删除后再加(老师撤回作业又重新发布):** BB 通常分配**新 id** → diff 判定为新项 → 作为新事件通知。这是合理的(确实是一件新的、需要重新关注的事)。
- **删除:** BB 不再返回该 id。bbwatch **不主动删 `seen_entity` 行**(保留终态记忆),仅在清单页把对应任务标记为 `archived`(见 §5.3 的 `last_seen_scan` 字段:连续 N 次扫描未出现 → 归档,不删行,以防"删了又回来"被当成新项重发)。

### 5.2 扫描–diff–通知状态机(失败可重试不重发)

每个被检出的事件实例在 `event` 表中沿**单向状态机**前进。状态机的全部价值在于:**先持久化为"已知 + 待通知",再投递,再标记已通知**——任一步崩溃,重启后只会重试投递,绝不会丢、也绝不会把已 `notified` 的再发一遍。

```
            detect (diff 命中新 entity_key)
                       │
                       ▼
   ┌──────────────────────────────────────┐
   │  DETECTED                             │  事务 T1 已提交: seen_entity + event 行落盘
   │  (entity 已记入 seen_entity)          │  ← 崩溃在此之后: 重启不会重新 detect(已 seen)
   └──────────────────────────────────────┘
                       │ 同一事务内置为 pending_notify
                       ▼
   ┌──────────────────────────────────────┐
   │  PENDING_NOTIFY                       │  待投递。崩溃/通知失败停在这里
   │  notify_attempts, next_retry_at       │  ← 重启扫到 PENDING_NOTIFY 的行 → 重投(幂等)
   └──────────────────────────────────────┘
            │ 投递成功            │ 投递失败(osascript 非0 / 超时)
            ▼                     ▼
   ┌─────────────────┐   attempts++, 指数退避 next_retry_at
   │  NOTIFIED        │   仍停在 PENDING_NOTIFY,下次扫描或重试循环再投
   │  (终态)          │   超过 max_attempts(默认5) → FAILED_NOTIFY(终态,清单页可见,不再骚扰)
   └─────────────────┘
```

**铁律:** `DETECTED → PENDING_NOTIFY` 的写入与 `seen_entity` 的写入**在同一个 SQLite 事务里**。这保证不存在"已记为 seen 但没排进通知队列"的窗口——否则下次 diff 因为 seen 命中而跳过它,通知就永远丢了(经典的"标记早于投递"漏报)。

### 5.3 store 层 DDL 与函数签名

文件:`engine/store/schema.sql`,数据库路径 `~/.bbwatch/state.db`(目录 `0700`,与凭据隔离,**库中不含任何密码/cookie/token**——凭据只存 macOS 钥匙串,见设计 §8)。

```sql
-- 已知实体集合: 全量 diff 的"本地已知"那一半。每个 entity_key 一行,永久记忆。
CREATE TABLE IF NOT EXISTS seen_entity (
    entity_key      TEXT PRIMARY KEY,        -- 见 §5.1, 如 'col:_17236_1:_88123_1'
    kind            TEXT NOT NULL,           -- 'column'|'announcement'|'content'|'attachment'|'grade'
    course_id       TEXT NOT NULL,           -- BB course.id(内部), 如 '_17236_1'
    bb_id           TEXT NOT NULL,           -- 裸 BB id(不含前缀), 便于回查
    first_seen_scan INTEGER NOT NULL,        -- 首次检出的 scan_run.id
    last_seen_scan  INTEGER NOT NULL,        -- 最近一次仍出现的 scan_run.id(用于归档判定)
    grade_status    TEXT,                    -- 仅 kind='grade': 上次记录的 status(None/NeedsGrading/Graded)
    grade_score     REAL,                    -- 仅 kind='grade': 上次记录的 score(可空)
    payload_json    TEXT NOT NULL,           -- 展示用快照: name/title/due/modified 等(不进幂等键)
    created_at      TEXT NOT NULL            -- 本地写入时刻(UTC ISO8601)
);

-- 通知事件: 状态机的载体。一个 entity_key 在某一"事件类型"上至多产生一个未结事件。
CREATE TABLE IF NOT EXISTS event (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_key      TEXT NOT NULL,
    event_type      TEXT NOT NULL,           -- 'new_assignment'|'new_announcement'|'new_content'|'new_attachment'|'graded'
    state           TEXT NOT NULL,           -- 'PENDING_NOTIFY'|'NOTIFIED'|'FAILED_NOTIFY'
    notify_attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at   TEXT,                    -- 退避到点前不重投(UTC ISO8601)
    detected_at     TEXT NOT NULL,
    notified_at     TEXT,
    dedupe_tag      TEXT NOT NULL UNIQUE,    -- = '{event_type}:{entity_key}'  ← 数据库级唯一,硬防重
    FOREIGN KEY (entity_key) REFERENCES seen_entity(entity_key)
);

-- 任务清单(清单页): 由带 due 的 column 派生, 承载完成状态。与 event 解耦。
CREATE TABLE IF NOT EXISTS task (
    entity_key      TEXT PRIMARY KEY,        -- = 'col:{cid}:{column.id}'
    course_id       TEXT NOT NULL,
    name            TEXT NOT NULL,
    due_utc         TEXT,                    -- grading.due 原值(UTC)
    bb_status       TEXT,                    -- None/NeedsGrading/Graded(BB 权威)
    score           REAL,
    score_possible  REAL,
    manual_done     INTEGER NOT NULL DEFAULT 0,  -- 线下作业手动勾选
    archived        INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL
);

-- 扫描运行台账: 崩溃恢复 + "最近未扫"提示的依据。
CREATE TABLE IF NOT EXISTS scan_run (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger      TEXT NOT NULL,              -- 'session_start'|'manual'|'periodic'
    is_bootstrap INTEGER NOT NULL DEFAULT 0, -- 1 = 冷启动建基线轮, 只写 seen 不发通知(见 §5.6/反例5)
    started_at   TEXT NOT NULL,
    finished_at  TEXT,                       -- 空 = 该次扫描未正常收尾(崩溃),下次启动可诊断
    status       TEXT NOT NULL DEFAULT 'running'  -- 'running'|'ok'|'crashed'|'partial'
);

CREATE INDEX IF NOT EXISTS ix_event_pending ON event(next_retry_at) WHERE state = 'PENDING_NOTIFY';
CREATE INDEX IF NOT EXISTS ix_seen_course   ON seen_entity(course_id);
```

`dedupe_tag UNIQUE` 是**数据库强制**的最后一道防线:即便上层逻辑有竞态,`INSERT ... ON CONFLICT(dedupe_tag) DO NOTHING` 也保证同一 `(event_type, entity_key)` 物理上只能存在一行,杜绝重复通知。

关键函数签名(`engine/store/__init__.py`):

```python
def open_db(path: Path = DEFAULT_DB) -> sqlite3.Connection: ...
    # 连接时执行: PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON;

def begin_scan(conn, trigger: str) -> int:
    """插入 scan_run(status='running'), 返回 scan_id。
       若库内无任何 seen_entity 行 → 自动标 is_bootstrap=1(冷启动)。"""

def diff_and_stage(
    conn: sqlite3.Connection,
    scan_id: int,
    kind: str,
    course_id: str,
    observed: list[ObservedEntity],   # 本次 BB 拉取到的该 kind 全集(已过滤汇总列)
    suppress_notify: bool,            # = 该 scan_run.is_bootstrap; True 时只写 seen 不 stage event
) -> list[StagedEvent]:
    """全量 diff + 在【同一事务】内写 seen_entity 并(非 bootstrap 时)stage PENDING_NOTIFY event。
       返回新 staged 的事件(供 notifier 投递)。已 seen 的只刷新 last_seen_scan/payload。"""

def take_pending(conn, limit: int = 50) -> list[Event]:
    """取出 state=PENDING_NOTIFY 且 (next_retry_at IS NULL OR next_retry_at<=now) 的事件(含崩溃残留)。"""

def mark_notified(conn, event_id: int) -> None: ...
def mark_notify_failed(conn, event_id: int, max_attempts: int = 5) -> None: ...

def upsert_task(conn, entity_key, course_id, name, due_utc,
                bb_status, score, score_possible) -> None: ...
def archive_stale(conn, current_scan_id: int, miss_threshold: int = 3) -> None:
    """连续 miss_threshold 次扫描未出现的 seen_entity 对应 task 置 archived=1(不删行)。
       注意: 仅在【非 partial】扫描上推进 miss 计数, 避免一次抓取失败误判删除。"""

def finish_scan(conn, scan_id: int, status: str) -> None:
    """收尾 scan_run: 写 finished_at + status('ok'|'partial'|'crashed')。"""
```

### 5.4 为什么用"全量 diff"而非时间窗口增量(多天未扫也不漏的论证)

设计硬约束:bbwatch **不装 launchd/cron**(用户已否决,见设计 §1),扫描只发生在用 Claude Code 期间。这意味着扫描节律天然不规律——可能连续几天不开机。在这种节律下,**时间窗口增量必然漏**,**全量 diff 必然不漏**。论证如下:

**时间窗口增量的漏报模型。** 增量法每次扫描问 BB"`created/modified > last_scan` 的有哪些"。它的不漏依赖两个脆弱前提:(a) 每次扫描的窗口 `(last_scan, now]` 与上一次无缝衔接;(b) BB 的 `created` 时间戳准确且不回填。一旦周五扫完后整周末不开机,周一扫描虽然窗口起点仍是周五(看似无缝),但它要靠"窗口拼接正确"这一假设支撑——任何一次 `last_scan` 写入失败、时钟漂移、或老师把内容的 `created` 回填到过去(BB 内容树排序、补发公告均可能),都会让某项落在所有窗口的缝隙里,**永久不可见**。增量的本质缺陷:它信任"时间",而时间是不可靠的连续量。

**全量 diff 的不漏证明。** 全量法每次扫描问 BB"**现在可见的全部** id 是哪些",再减去 `seen_entity` 全集。设第 k 次扫描时 BB 可见集为 $V_k$,本地已知集为 $S_k$。则:

- 本次通知集 $N_k = V_k \setminus S_k$;
- 扫描后 $S_{k+1} = S_k \cup V_k$。

**命题:** 任一进入过 $V$ 的 id,只要它在某次扫描时仍可见,必被通知恰好一次。

**证明:** 设 id $x$ 在第 $j$ 次扫描首次出现($x \in V_j$)。若 $x \notin S_j$,则 $x \in N_j$,被通知,且 $S_{j+1} \supseteq \{x\}$,此后 $x$ 永远在 $S$ 中 → 不再进入任何 $N$ → 不重复。若第 $j$ 次因故未扫(那天没开 Claude Code),$x$ 不消失;到第 $j'>j$ 次开机扫描时 $x$ 仍 $\in V_{j'}$ 且 $\notin S_{j'}$ → 被通知。**结论:漏的唯一条件是"`x` 在它存在的整个生命周期里,bbwatch 一次都没扫过"**——而这正是"从不开 Claude Code"的退化情形,已被设计 §1 列为已接受取舍,且由 §5.7 的 SessionStart 高频补扫 + additionalContext 兜底进一步压缩。∎

**该命题成立有一个隐含前提:$V_k$ 必须真的"全"。** 这是全量 diff 唯一的攻击面——若某次拉取因翻页中断、窗口未覆盖、或单门课请求失败而少返回了本该可见的 id,那项就不在 $V_k$、不入 $N_k$,等同于"这次没扫到它"。两道防线封住此面:

1. **覆盖完整性(§5.5):** 16 周窗口分段 + 分页穷尽,保证单次拉取拿到的就是 BB 此刻可见全集。
2. **失败不污染基线:** 若某门课/某端点本次抓取失败,该 `(kind, course_id)` 维度**整体跳过 diff**(不把"少返回"误当"被删除",也不据此推进 `last_seen_scan` 与 miss 计数),并把 `scan_run.status` 记为 `partial`。下次扫描重做该维度的全量 diff——因为是全量而非增量,补齐零成本,漏不了。

一句话:增量信任时间轴的连续性(开机才连续),全量只信任"id 此刻是否可见"(开机一次 + 拉全即可)。bbwatch 的不规律节律下,只有后者成立。

> 实测支撑(§2 findings):`columns`、`announcements`、`contents`、`calendars/items` 返回的都是**当前完整可见集**(配合 §5.5 的窗口/翻页覆盖),不是"自某时刻起的增量",因此 $V_k$ 可被完整构造。注意 `calendars/items` 本实例返回项**全为 `type=GradebookColumn`**(即"全课程带 due 的成绩册列"的聚合),与 per-course `columns` 指向同一批 column.id;它是**跨课程 ddl 的便捷视图**,而非独立实体来源——两条路径的 id 落入同一 `col:` 命名空间,diff 自然合并去重,不会因"日历又看到一遍"而重发。

### 5.5 16 周窗口与分页:必须完整覆盖否则漏(覆盖算法)

全量 diff 的前提是 $V_k$ 真的"全"。两个 BB 实测限制会让 $V_k$ 残缺,从而漏:

1. **`calendars/items?since&until` 窗口必须 ≤16 周**,超出报 400(§2 实测)。
2. **分页:`paging.nextPage` 仅在有下一页时出现**,不能假设固定结构(§5 findings)。

**对策一律是"穷尽覆盖",不是"取最近一段"。** 注意:per-course `gradebook/columns`、`announcements`、`contents` **不受 16 周窗口限制**(它们直接返回该课全集,只需分页穷尽);窗口限制只作用于跨课程 `calendars/items`。因此即便日历视图某段未覆盖,作业 ddl 仍能经 per-course `columns` 全量拿到——**两条独立路径互为冗余**,共同收敛到同一 `col:` 集合。

**日历窗口覆盖(覆盖整学期,而非未来几天):**

```python
def iter_calendar_window(term_start: date, term_end: date,
                         step_weeks: int = 15):     # 留 1 周安全余量, 不贴 16 周硬上限
    """把 [term_start-1周, term_end+1周] 切成 ≤15 周的连续闭区间, 逐段拉取。
       前后各扩 1 周, 吸收时区与补发的边界项。区间相接处用 [since, until) 半开避免重叠重复。"""
    cur = term_start - timedelta(weeks=1)
    hard_end = term_end + timedelta(weeks=1)
    while cur < hard_end:
        nxt = min(cur + timedelta(weeks=step_weeks), hard_end)
        yield (cur, nxt)          # since=cur(含), until=nxt(不含)
        cur = nxt
```

学期边界由 `GET terms`(返回 `termId→name`)结合 `users/{uid}/courses` 的 `termId` 过滤出在读学期得到;`terms` 不直接给精确起止日期时,**退化为以 today 为中心向前 26 周、向后 30 周**两段覆盖,确保任何在读学期都被框住。**全量 diff 对窗口的轻微重叠免疫**(重叠项 id 重复,diff 自然合并),所以宁可多覆盖、绝不留缝。

**分页穷尽(所有列表端点通用):**

```python
def paginate(client, path: str, params: dict) -> Iterator[dict]:
    while True:
        body = client.get(path, params=params).json()  # 已限速 + 复用会话
        yield from body.get("results", [])
        nxt = body.get("paging", {}).get("nextPage")    # 仅有下页时才出现该字段
        if not nxt:                                      # 缺席即终止(实测:无下页时 paging 不出现)
            break
        path, params = nxt, {}                           # nextPage 是带 offset 的完整相对路径
```

> 课程列表本身也要分页:`users/{uid}/courses?expand=course` 的 `limit≤100`,需翻页(§1 实测)。且**此子资源不接受 `me` 别名**——必须先 `GET users/me` 取真实 `uid`(如 `_49765_1`)再代入。本学期"在读"过滤 = `courseRoleId=Student` 且 `availability.available ∈ {Yes, Term}`(实测 17 门在读),再叠加课程黑/白名单。漏掉一门在读课 = 漏掉它的全部任务,故课程枚举本身也是 $V_k$ 完整性的一环。

**漏报反例:** 若日历只取 `since=now & until=now+2周`,则学期后段的 ddl 此刻不在日历视图 → 但仍能经 per-course `columns` 拿到;真正危险的是**两条路径都只取最近一段**。覆盖整学期窗口后,**全部 ddl 在学期初就一次进入已知集**,后续只是状态推进。

### 5.6 状态变化的去重:避免"已交还提醒"与出分误报

幂等不只针对"新项",也针对"状态翻转"。BB 的 per-user column 状态(§2 实测)`status ∈ {None, NeedsGrading, Graded}` + `score`,要求两类去重:

**(A) 不对已完成的作业重发 ddl 提醒。** "新作业"事件在 column 首次出现时触发一次。此后清单页的 ddl 倒计时不再产生通知;且若 `bb_status ∈ {NeedsGrading, Graded}` 或 `manual_done=1`,清单页**不再红色催办**(任务折叠到已完成)。判定优先级(对齐设计 §7):已出分 > BB 已提交(`NeedsGrading`/`Graded`)> 手动勾选。注意 per-user 状态需对每门在读课的每个 column 单独拉 `columns/{colId}/users/{uid}`,这是扫描中请求量最大的一环——故对 BB 温和(限速、复用会话)在此尤为关键。

**(B) 出分只在真正翻转时通知一次。** `graded` 事件的触发条件是**状态转移**,不是状态值:

```python
def maybe_stage_grade(conn, scan_id, cid, col_id, uid, status, score, suppress_notify):
    key  = f"grade:{cid}:{col_id}"
    prev = get_seen(conn, key)                            # 读上次记录的 grade_status/grade_score
    became_graded  = (status == "Graded" and (prev is None or prev.grade_status != "Graded"))
    score_appeared = (score is not None and (prev is None or prev.grade_score is None))
    if (became_graded or score_appeared) and not suppress_notify:
        stage_event(conn, entity_key=key,
                    event_type="graded", scan_id=scan_id)   # dedupe_tag 保证至多一次
    upsert_seen_grade(conn, key, status, score)           # 总是刷新基线(含 bootstrap 轮)
```

- **首次扫描就已 `Graded`(bbwatch 上线前老师已批改):** 由 `scan_run.is_bootstrap` 控制——bootstrap 轮 `suppress_notify=True`,只写 `seen_entity`/`grade_status` 建立基线、**不发历史出分通知**(否则首扫会把全部历史成绩当"新出分"补报)。这是推荐的冷启动策略,与反例 5 同源。bootstrap 之后才正常通知。
- **`Graded → Graded`(分数被老师改了再改):** `grade_status` 未翻转 → 不重发。若要支持"改分提醒",另立 `regraded` 事件类型(条件 `prev.grade_score != score`),独立幂等键,不与首次出分混淆。
- **`NeedsGrading`(已交待批):** 绝不触发出分,只更新 `task.bb_status` 供清单页显示。

### 5.7 多触发源并发:去抖 + 进程锁 + 同表幂等

三个触发源(SessionStart 钩子 / `/bb-scan`·`scan_now` / 清单服务周期循环,见设计 §4)可能**同时**发起扫描。并发风险:两个扫描同时 diff 同一 column,各自判定为"新",发两次通知。三层防御:

**层一:跨进程文件锁(去抖 + 互斥)。** 扫描入口先抢 `~/.bbwatch/scan.lock`(`fcntl.flock` 非阻塞):

```python
def scan(trigger: str) -> list[Event]:
    with try_flock("~/.bbwatch/scan.lock", timeout=0) as got:
        if not got:                       # 已有扫描在跑
            return []                     # 去抖: 直接返回, 不排队不重扫(那次会覆盖本次需求)
        return _scan_locked(trigger)
```

SessionStart 与周期循环撞车时,后到者拿不到锁直接跳过——因为先到的全量扫描产出的 $V_k$ 已覆盖后到者想要的一切(全量 diff 的性质),**跳过不会漏**。

**层二:SQLite WAL + busy_timeout + 事务。** 即便锁因极端情况失效(如崩溃后锁文件残留被强清),`diff_and_stage` 在 `BEGIN IMMEDIATE` 事务内做"查 seen → 插 seen → stage event",写写互斥;`dedupe_tag UNIQUE` 让第二个事务的 `INSERT ... ON CONFLICT DO NOTHING` 静默失败。**物理上不可能产生两行同 `dedupe_tag`**。

**层三:投递与标记的幂等。** `take_pending` 取行后,投递与 `mark_notified` 同在一个事务,且 `mark_notified` 带 `WHERE state='PENDING_NOTIFY'` 守卫;两个投递者抢同一事件时,先提交者把 state 改成 `NOTIFIED`,后者的 `UPDATE` 影响 0 行 → 不投。

**SessionStart 频繁补扫不会变成"频繁打扰":** 通知只由"事件首次进入 PENDING_NOTIFY"产生,而那由全量 diff 的差集决定。开十次 Claude Code,若无新项,差集为空,**零通知**。这正是"高频扫 + 幂等"能共存的原因。

### 5.8 时区(UTC → +8)

§2 实测:所有时间字段(`grading.due`、`announcement.created`、`content.modified`、calendar `end`)均为 **UTC**,形如 `2026-06-30T15:59:00.000Z`。

- **存储一律存 UTC ISO8601 原值**(`task.due_utc` 等),不在存储层转换——避免双重转换 bug。
- **仅在两处转 +8 展示:** 清单页渲染、macOS 通知文案。统一经 `to_local(dt_utc) -> "2026-06-30 23:59 (+08)"`(`ZoneInfo("Asia/Shanghai")`)。
- **ddl 临近判定用 UTC 比较**(`due_utc` vs `datetime.now(timezone.utc)`),与展示解耦,杜绝"本地午夜算错一天"。
- 上例 `15:59:00Z` = 北京时间 `23:59`,正是常见的"当日 23:59 截止",印证统一 +8 的正确性。

### 5.9 反例场景:会漏/会重的六个陷阱与各个击破

| # | 反例场景 | 朴素实现为何漏/重 | bbwatch 的击破 |
|---|---|---|---|
| **1** | 周五扫完,周末老师布置 `Homework 5`(ddl 周三),用户周二才开 Claude Code | 时间窗口增量若窗口拼接出错/`created` 被回填,`HW5` 落入缝隙永久不可见 → **漏 ddl** | 全量 diff:周二扫描时 `HW5` 的 column.id 仍在 $V_k$、不在 seen → 立即检出并提醒(§5.4 命题) |
| **2** | 老师把 `Assignment 3` 改名为 `A3 (extended)`,顺延 ddl | 以 name/title 作幂等键 → 视作新作业 → **重复通知** | 幂等键只认 `column.id`(§5.1):id 未变 → 不重发;payload 更新新名与新 due,清单页静默刷新 |
| **3** | SessionStart 钩子与清单服务周期循环在同一秒触发,同时 diff 出新公告 | 两扫描各发一次 → **重复通知** | 文件锁去抖(后到者跳过)+ `dedupe_tag UNIQUE` 物理防重(§5.7) |
| **4** | 通知投递时 `osascript` 失败/进程被 kill 在"已发通知前" | "先标 notified 再发"模型:崩溃后该事件已 notified 但用户没收到 → **永久漏这条通知** | "先 seen+PENDING 再投递再 NOTIFIED"(§5.2):崩溃停在 PENDING,重启 `take_pending` 重投;`mark_notify_failed` 退避重试,绝不丢 |
| **5** | bbwatch **首次安装**,17 门在读课累计上百个历史 column/公告/已出分 | 全量 diff 把全部历史项当"新" → **首扫狂轰上百条通知** | 首个 `scan_run` 标 `is_bootstrap`,`suppress_notify=True`:只写 `seen_entity` 建基线,**不 stage event**。此后才正常通知(§5.6)。清单页仍立即展示全部待办,只是不弹通知 |
| **6** | 已交待批的 `HW2`(`NeedsGrading`),每次扫描都看到它有 ddl | 仅按"有 due 且未过期"催办 → **对已交作业反复提醒** | 完成判定读 per-user `status`(§5.6 A):`NeedsGrading/Graded` 即视为已完成,清单页折叠、不催办 |
| **7** | 某门课的 `contents` 请求中途 500/超时,只返回了一半内容项 | 把"少返回的一半"当成"被删除",推进 miss 计数甚至误判新增 → **状态错乱/潜在漏** | 该 `(kind,course)` 维度抓取失败即**整体跳过 diff**、不动 `last_seen_scan`、`scan_run.status='partial'`;下次全量重做,补齐零成本(§5.4) |

### 5.10 借 Claude Code 能力进一步压低漏报

全量 diff 的唯一残余漏报面是"在某项的整个可见生命周期里一次都没扫"。Claude Code 的两个特性把这个面压到接近零,且不违反"不装 OS 后台代理"的硬约束:

- **SessionStart 高频补扫即"事件驱动的伪 cron"。** 学生每天开 Claude Code 工作不止一次,每次开都触发一次全量扫描(异步非阻塞,见设计 §3/§4)。扫描频率随使用频率自然上升;全量 diff + 幂等保证"扫得勤"只会更早发现、绝不会更多打扰(§5.7)。这把"漏一项"的条件从"那项存在期间某一天没扫"收紧为"那项存在期间一次 Claude Code 都没开"。
- **additionalContext 兜底展示:即使通知通道全失效,也不漏看。** SessionStart 钩子除了扫描,还把"未完成且临近 ddl + 本次新增事件 + 距上次成功扫描已过 N 小时"的摘要,经 `additionalContext` 注入会话上下文。于是哪怕 macOS 通知被静音、`osascript` 失败(反例 4 停在 PENDING),**用户一旦在 Claude Code 里开口,Claude 第一句就会报 ddl**。这是独立于通知通道的第二条信息出口,把"通知漏看"降级为"最多晚到下一次开会话"。
- **"最近未扫"自曝。** `scan_run` 台账让清单页与 additionalContext 都能显示"上次成功扫描在 X 小时前"(只认 `status='ok'` 的轮,`partial`/`crashed` 不算);若临近某 ddl 而最近未扫,醒目警示(设计 §7)。这把纯插件形态"不开机就不扫"的固有缺口,转成**用户可见、可主动补扫**的状态,而非沉默的漏。

---

**本章不变量(实现期回归测试须逐条断言):**

1. 任一 `entity_key` 在任一 `event_type` 上,`event` 表至多一行(`dedupe_tag UNIQUE`)。
2. 不存在 `seen_entity` 有行而对应 `PENDING_NOTIFY`/`NOTIFIED`/`FAILED_NOTIFY` event 缺失的"已 seen 未排队通知"窗口(同事务写入;bootstrap 轮除外,该轮设计上只写 seen)。
3. 任一进入过 $V$ 且至少被**完整**扫到一次的 id,被通知恰好一次(全量 diff 命题,§5.4);抓取失败的维度不污染基线。
4. 崩溃在投递前后任意点重启,结果是"重投"而非"丢失",且不产生第二条通知(状态机 + WAL,§5.2/§5.7)。
5. `Graded` 出分通知对每个 column 至多一次;`NeedsGrading` 永不触发出分;bootstrap 轮不产生任何通知(§5.6)。
6. 无 `grading.due` 的汇总列(`Total`/`Weighted Total`)永不进入 `seen_entity` 的 `column`/作业事件流(§5.1)。

---

主要改动(相对初稿):修正幂等键示例 id 与课程 id 来源(强调用内部 `course.id` 而非可改写的 `courseId`);新增 §5.1 第 4 条明确汇总列过滤;把"抓取失败不污染基线/partial 标记"补成贯穿 §5.3/§5.4/§5.7/反例 7/不变量的完整保证(这是初稿最大的漏报缺口——原文只防"时间缝隙",未防"单次拉取残缺");bootstrap 策略从散落提及收敛为 `scan_run.is_bootstrap` + `suppress_notify` 贯通字段、函数签名与代码;校正 calendar 视图与 per-course columns 的关系(同一 `col:` 命名空间、互为冗余、自然去重),澄清 16 周窗口只约束日历而不约束 per-course 端点;补全课程枚举本身的完整性要求(`me` 别名不可用、17 门在读过滤条件);修正不变量 2/5 措辞以兼容 bootstrap 与 FAILED 终态;`mark_notified` 改为带 `WHERE` 守卫的乐观并发;索引改为按 `next_retry_at` 以匹配 `take_pending` 查询。

文件路径:设计文档 `/Users/mac/Programming/cuhkszbb/docs/superpowers/specs/2026-06-28-bbwatch-design.md`,findings `/private/tmp/claude-501/-Users-mac-Programming-cuhkszbb/82db56a5-da23-42e2-a147-6a5d1bfa6443/scratchpad/bb_findings.md`。

---

## 6. 认证与会话管理

本章是整个 bbwatch 引擎的根。所有 REST 调用都依赖一个有效的 BB 会话 cookie；本章定义如何"从钥匙串里的账号密码，自动换出可用会话"，如何检测失效并自愈，如何在传输层避开实测踩到的 TLS 坑，以及在学校将来开启 MFA 时的兜底路径。

本章直接服务于全局硬需求 **绝不漏 / 绝不重复**：扫描器的全量 diff 只有在"每次触发都拿得到可用会话"时才成立。一次静默的会话失效会让该轮扫描拉到空集，被 diff 误判为"无新项"——既漏（新作业未检出）又可能埋下重复隐患（下轮把积压项当首见，若通知幂等键设计不当会重发）。因此本章把会话可用性当作扫描正确性的前置不变量来保证：**宁可抛出明确异常让该轮扫描显式失败，也绝不返回半残会话让 diff 拿到不完整数据。**

对应模块为 `engine/auth/`，文件布局：

```
engine/auth/
  ├─ __init__.py
  ├─ transport.py     # curl_cffi 会话工厂 + 代理 + 重试退避
  ├─ credentials.py   # keyring 读写, 首次 setup 录入
  ├─ adfs.py          # ADFS OAuth2 授权码流的分步实现
  ├─ session.py       # 会话缓存/失效检测/重登编排 (对外主入口)
  ├─ errors.py        # 错误分类
  └─ models.py        # CachedSession dataclass
```

对外唯一主入口约定为 `engine.auth.session.SessionManager.get_session() -> BBSession`，引擎其余部分（`bbclient`、`scanner`、`downloader`）只调它，从不直接接触 ADFS 细节。

---

### 6.1 传输层：curl_cffi + 代理（`transport.py`）

实测事实：anaconda 自带的 Python `requests` 直连 `sts.cuhk.edu.cn` 出现 TLS 握手失败（`UNEXPECTED_EOF`），而 `curl` 正常。推断为 ADFS 对 TLS 指纹（JA3）敏感 + 本机 Clash 代理（`127.0.0.1:7890`）的 OpenSSL 组合。结论：**不用裸 `requests`**，改用 `curl_cffi`（底层 curl-impersonate，可伪装浏览器 TLS/HTTP2 指纹），失败时回退子进程 `curl`。

```python
# engine/auth/transport.py
from curl_cffi import requests as cffi
import os

IMPERSONATE = "chrome124"          # 浏览器指纹档位; 升级 curl_cffi 时复核可用值
DEFAULT_TIMEOUT = (10, 30)         # (connect, read) 秒
BB_BASE  = "https://bb.cuhk.edu.cn"
STS_BASE = "https://sts.cuhk.edu.cn"

def _proxies() -> dict | None:
    """尊重本机环境代理; 未设则探测 Clash 默认端口。"""
    p = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if p is None:
        p = "http://127.0.0.1:7890"   # 实测本机 Clash; 可被配置覆盖/置空
    return {"http": p, "https": p} if p else None

def new_curl_session() -> cffi.Session:
    s = cffi.Session(
        impersonate=IMPERSONATE,
        proxies=_proxies(),
        timeout=DEFAULT_TIMEOUT,
        allow_redirects=False,      # ADFS/OAuth 各跳必须手动跟随并检视
        verify=True,
    )
    s.headers.update({"Accept-Language": "en-US,en;q=0.9"})
    return s
```

关键决策：

- `impersonate="chrome124"`：这是绕过 `UNEXPECTED_EOF` 的核心。一旦此档位失效（curl_cffi 升级、ADFS 升级），`adfs.py` 的首步 `GET authorize` 会立即抛 `TransportError`，触发 6.4 的回退路径。
- `allow_redirects=False`：整个 OAuth 流的每一个 302 都要被显式检视（看 `Location` 指向 ADFS 还是 BB，以判断成败），绝不让库自动一路跟到底。只有"附件下载"那种确定的 302→真实文件场景才在 `bbclient` 局部开启跟随。
- 代理可被配置项 `transport.proxy` 覆盖：`null` 表示显式禁用（直连，适配不挂 Clash 的同学），未配置则探测 Clash 默认端口。注意区分"未设置环境变量"与"显式设为空串禁用代理"——上面 `_proxies` 用 `is None` 判定，使 `HTTPS_PROXY=""` 能表达"强制直连"。
- 子进程 `curl` 回退封装为 `curl_fallback(method, url, headers, data, cookiejar_path)`，共享同一个 Netscape 格式 cookie jar 文件，使两条传输路径对会话透明等价。

---

### 6.2 凭据存储：macOS 钥匙串（`credentials.py`）

硬约束：密码只存钥匙串，绝不落盘明文、绝不进日志、绝不出现在文档示例。用 `keyring`（macOS 后端为 Keychain）。

```python
# engine/auth/credentials.py
import keyring
from dataclasses import dataclass

SERVICE = "bbwatch.adfs"           # 钥匙串 service 名

@dataclass(frozen=True)
class Credential:
    username: str                  # AD 账号
    password: str                  # 仅在内存中存在; 不写日志/repr

    def __repr__(self) -> str:     # 防止误把密码打进日志/异常栈
        return f"Credential(username={self.username!r}, password=***)"

def save_credential(username: str, password: str) -> None:
    keyring.set_password(SERVICE, "username", username)
    keyring.set_password(SERVICE, username, password)

def load_credential() -> Credential | None:
    username = keyring.get_password(SERVICE, "username")
    if not username:
        return None
    password = keyring.get_password(SERVICE, username)
    if not password:
        return None
    return Credential(username=username, password=password)

def delete_credential() -> None:
    username = keyring.get_password(SERVICE, "username")
    if username:
        keyring.delete_password(SERVICE, username)
        keyring.delete_password(SERVICE, "username")
```

设计点：

- 账号形态对齐实测：登录走 ADFS，`UserName` 字段接受 `学号@link.cuhk.edu.cn` 或域账号形式；**`username` 不等于 BB userId**（如 `_49765_1`）。userId 是登录后从 `users/me` 取回并缓存的另一码事（见 6.3 Step 5 / 6.5），二者切勿混用——`users/{uid}/courses` 子资源只认 userId，不认 `me`、更不认登录账号。
- 钥匙串里存两条目：`(SERVICE, "username")` 记住账号本身，`(SERVICE, <username>)` 存密码。这样 `load_credential` 无需任何外部输入即可还原完整凭据，满足全自动 SessionStart 触发。
- `Credential.__repr__` 抹掉密码，杜绝异常栈/调试打印泄露。`errors.py` 的所有异常构造也禁止把 `password`、`Cookie`、`code` 参数纳入消息（见 6.8）。

#### 首次 setup 录入流

对应插件命令 `/bb-setup`（以及 MCP 工具同名能力），走 `engine.auth.credentials.interactive_setup()`：

```
1. 提示输入 AD 账号 + 密码(密码用无回显输入: getpass)。
2. 立刻做一次 SessionManager.login_fresh() 真实验证 (不只是存盘):
     - 成功 -> save_credential() 写钥匙串 -> 把 users/me 的 uid 写入 auth_state(见 6.5)
              -> 清零 consecutive_auth_failures, 复位熔断(见 6.7)。
     - 失败(凭据错) -> 不写钥匙串, 报"账号或密码错误", 让用户重输。
     - 失败(网络/TLS) -> 提示检查代理/网络, 凭据已在内存可重试不必重输。
3. 全程密码不写任何文件、不进 stdout 回显、不进日志。
```

"先验证再存盘"避免把错密码写进钥匙串导致后续每次扫描都失败。`/bb-setup` 成功是熔断（6.7）的唯一人工复位入口。

---

### 6.3 ADFS OAuth2 授权码流的分步实现（`adfs.py`）

这是会话获取的核心。实测客户端参数：

- `client_id = 4b71b947-7b0d-4611-b47e-0ec37aabfd5e`
- `redirect_uri = https://bb.cuhk.edu.cn/webapps/bb-SSOIntegrationOAuth2-BBLEARN/authValidate/getCode`
- ADFS 表单字段：`UserName`、`Password`、`Kmsi`（keep-me-signed-in）。

主函数签名：

```python
# engine/auth/adfs.py
def adfs_login(curl: cffi.Session, cred: Credential) -> str:
    """跑完整 OAuth 授权码流; 成功后会话 cookie 已写入 curl 的 cookie jar,
    返回从 users/me 探针取回的真实 uid (如 '_49765_1')。
    失败抛 CredentialError / TransportError / AntiBotError / MfaRequiredError。"""
```

分五步，每步都对照实测行为：

**Step 1 — `GET authorize`，取 ADFS 登录表单**

```python
authorize_url = (
    f"{STS_BASE}/adfs/oauth2/authorize"
    f"?response_type=code"
    f"&client_id={CLIENT_ID}"
    f"&redirect_uri={quote(REDIRECT_URI, safe='')}"
)
r1 = curl.get(authorize_url)        # 期望 200 + HTML 登录表单
```

- 若此步连接即失败（TLS `UNEXPECTED_EOF`/超时）→ `TransportError`（指纹/代理问题，触发 6.4 回退）。
- 若返回非 200 或非登录页 → `AntiBotError`（可能被风控/改版）。
- 此步可能本身就带若干 302（ADFS 内部跳转）。因 `allow_redirects=False`，需手动有限次跟随（≤5）直到落到含登录表单的 200 页；途中下发的 `MSISContext`/`MSISSamlRequest` 等 cookie 必须被 curl 的 jar 自动持有，后续 POST 才能成功。

**Step 2 — 解析表单**

ADFS 登录页是标准 `<form method="post">`，`action` 通常是相对路径（形如 `/adfs/oauth2/authorize?...&client-request-id=...`），且常含隐藏字段（部分版本有 `__VIEWSTATE` 类隐藏态或 `AuthMethod`）。绝不能硬编码 action，必须从返回 HTML 中解析。

```python
def _parse_adfs_form(html: str, base_url: str) -> tuple[str, dict[str, str]]:
    """返回 (post_action_绝对url, 隐藏字段dict)。用 lxml 解析 <form>,
    抓 action 与所有 <input type=hidden>。找不到含密码框的 form -> AntiBotError。"""
```

实现要点：用 `lxml.html` 而非正则；取第一个含密码输入框的 `<form>`；`urljoin(base_url, action)` 还原绝对 URL；收集所有隐藏 `<input>` 的 `name=value` 一并回传（把它们原样塞回 POST，提高跨 ADFS 小版本鲁棒性）。

**Step 3 — `POST UserName/Password/Kmsi`**

```python
form = hidden_fields | {
    "UserName": cred.username,
    "Password": cred.password,
    "Kmsi": "true",                 # keep-me-signed-in, 延长会话寿命
    "AuthMethod": "FormsAuthentication",
}
r3 = curl.post(post_action, data=form,
               headers={"Content-Type": "application/x-www-form-urlencoded"})
```

判定：

- **成功** → `302`，`Location` 指向 `redirect_uri`（BB `getCode`）并带 `?code=<授权码>`。
- **凭据错** → 一般返回 `200` 且 HTML 内含错误文案（ADFS 的 `#errorText` / "Incorrect user ID or password"）；此时 `Location` 不存在 → `CredentialError`。
- **MFA** → 若 `Location` 或返回页指向 `/adfs/.../additionalauth`、出现 OTP/验证码挑战 → `MfaRequiredError`（当前实测无 MFA；此分支为 6.9 兜底）。

**Step 4 — 跟随 302 到 BB `getCode`，完成 code 交换**

手动跟随 Step 3 的 `Location`（因为 `allow_redirects=False`）。BB 的 `bb-SSOIntegrationOAuth2-BBLEARN/authValidate/getCode` 端点用授权码换 token 并**回种 BB 会话 cookie**（如 `BbRouter`、`JSESSIONID`、`s_session_id` 等；以实际下发为准，不要硬依赖某个固定名），再 302 到 BB 首页。

```python
loc = r3.headers["Location"]                 # .../getCode?code=...&state=...
r4 = curl.get(loc)                            # BB 完成交换, 回种会话 cookie; 可能再 302
# 继续手动跟随有限次 (<=5) 直到落到 bb.cuhk.edu.cn 的 2xx, 防重定向环
```

绝不把 `code` 写日志（等同短期凭据）。

**Step 5 — 验证会话真的可用，并取回 uid**

不靠"落到首页"判成功（可能落到一个要求重登的中转页）。用一次廉价 REST 探针确认，**并把 uid 当作 `adfs_login` 的返回值**——后续 `users/{uid}/courses` 等子资源**不接受 `me` 别名**（实测），扫描全程必须用真实 uid，所以这里就取定：

```python
r5 = curl.get(f"{BB_BASE}/learn/api/public/v1/users/me",
              headers={"Accept": "application/json"})
if r5.status_code == 200 and r5.json().get("id"):
    return r5.json()["id"]                    # 会话就绪, 返回 _49765_1 形态 uid
raise AntiBotError("post-login probe failed", status=r5.status_code)
```

把 uid 的获取焊死在登录成功路径里（而非交给上层另调一次），保证"有会话必有 uid"这一不变量，避免扫描器因 uid 缺失而静默拉空——这正是 6.0 所述"宁可显式失败也不返回半残会话"的体现。

---

### 6.4 传输回退：curl_cffi → 子进程 curl

仅当 `adfs_login` 的 Step 1 抛 `TransportError`（疑似指纹/底层 TLS）时才回退；凭据错/风控/MFA 不回退（回退也救不了）。

```python
def login_with_fallback(cred: Credential) -> tuple[cffi.Session, str]:
    try:
        s = new_curl_session()
        uid = adfs_login(s, cred)
        return s, uid
    except TransportError:
        log.warning("curl_cffi transport failed, falling back to subprocess curl")
        jar = SESSION_DIR / "cookies.txt"           # Netscape cookie jar
        uid = run_adfs_via_subprocess_curl(cred, jar)   # 复用同一 jar, 同样返回 uid
        return curl_session_from_jar(jar), uid          # 用 jar 重建 cffi.Session 继续跑 REST
```

子进程 `curl` 路径用 `-c/-b cookies.txt`（共享 jar）、`-x http://127.0.0.1:7890`（代理；若配置禁用代理则省略）、`-L --max-redirs 5`，但同样**逐跳检视**关键 302 并自行做 Step 5 探针取 uid。两条路径产出物严格等价：一个持有 BB 会话 cookie 的 jar + 一个真实 uid，REST 阶段统一回到 curl_cffi。密码经 `--data-urlencode @-` 从 stdin 传入，绝不出现在命令行 argv（防 `ps` 泄露），也绝不写进任何临时文件。

---

### 6.5 会话缓存与持久化（`models.py` + store）

会话来之不易（一次完整 OAuth 流），要缓存复用，对 BB 温和。

```python
# engine/auth/models.py
@dataclass
class CachedSession:
    cookies: list[dict]        # 序列化的 cookie jar (name/value/domain/path/expires)
    uid: str                   # _49765_1, 登录后从 users/me 取
    created_at: float          # epoch 秒
    last_validated_at: float   # 最近一次成功 REST 调用的时间
```

存储位置：cookie 这类敏感会话态**不进 SQLite 明文库**，单独落到受限权限文件：

```
~/Library/Application Support/bbwatch/session/cookies.json   # chmod 0600, 创建前先 mkdir(mode=0700)
~/Library/Application Support/bbwatch/session/cookies.txt    # curl 回退用 jar, 同样 0600
```

写入用"临时文件 + `os.replace` 原子换名"，并在 `open` 时即以 `0o600` 创建（先 `os.open(..., O_CREAT|O_WRONLY, 0o600)` 再包 `fdopen`），避免出现"先 644 后 chmod"的瞬时可读窗口。读取前校验文件权限与属主，异常则视作不可信、丢弃重登。

SQLite 里只放**非敏感**的会话元数据，供调度/诊断与 6.6 的"过早重登抑制"、6.7 的熔断：

```sql
-- store schema 的认证相关部分
CREATE TABLE IF NOT EXISTS auth_state (
    id               INTEGER PRIMARY KEY CHECK (id = 1),   -- 单行
    uid              TEXT,                 -- 真实 BB userId, 如 '_49765_1'
    session_created  INTEGER,              -- epoch 秒
    last_validated   INTEGER,              -- 最近一次 REST 成功 epoch 秒
    last_login_ok    INTEGER,              -- 最近一次成功完整登录 epoch 秒
    consecutive_auth_failures INTEGER NOT NULL DEFAULT 0,  -- 连续凭据失败计数 (风控退避)
    circuit_open_until INTEGER,            -- 熔断冷却截止 epoch 秒 (NULL=未熔断); 进程重启后仍有效
    last_auth_error  TEXT                  -- 最近错误的分类标签, 非敏感
);
```

`uid` 缓存进 DB，使非首扫无需为拿 uid 再打 `users/me`。`circuit_open_until` 持久化进 DB（而非内存），确保熔断状态跨进程、跨 SessionStart 重启依然生效——否则每开一次 Claude Code 就重置熔断，等于没熔断，会把"狂打错密码"风险放大（见 6.7）。

---

### 6.6 会话失效检测与重登编排（`session.py`）

对外主入口。BB 会话失效有两种信号（实测会话过期表现）：**API 返回 401**，或 **API 被 302 重定向回登录/ADFS**（典型于会话超时，REST 也会被劫到登录页）。`SessionManager` 把"重登"封死在内部，调用方永远拿到可用会话或一个明确异常——**绝不返回一个会让 diff 拉空的失效会话**。

```python
# engine/auth/session.py
class SessionManager:
    def __init__(self, store, lock_path): ...

    def get_session(self, force_relogin: bool = False) -> "BBSession":
        """返回一个已就绪的 BBSession(含活 cookie + uid)。
        优先复用缓存; 缓存缺失/过期/被判失效则自动重登。线程/进程安全(文件锁)。
        若熔断开启则抛 AuthCircuitOpenError; 凭据错抛 CredentialError。"""

    def invalidate(self, reason: str) -> None:
        """标记当前缓存会话失效(由 bbclient 在收到 401/被重定向到登录时回调)。"""
```

**`get_session` 决策逻辑（伪代码）：**

```
with file_lock(lock_path):                     # 跨触发源(SessionStart/手动/周期)互斥, 防并发重登风暴
    if circuit_open():                          # 6.7: 冷却中直接拒绝, 不打 ADFS
        raise AuthCircuitOpenError(until=circuit_open_until)
    if force_relogin:
        return do_login()
    cached = load_cached_session()
    if cached is None:
        return do_login()

    # 软过期: Kmsi 会话寿命有限; 超过 SOFT_TTL 主动验证一次
    if now - cached.last_validated_at < SOFT_TTL:        # SOFT_TTL = 20 min
        return BBSession(cached)                          # 信任, 不打探针 (对 BB 温和)
    if probe_ok(cached):                                  # GET users/me 探针
        touch last_validated_at; return BBSession(cached)
    return do_login()
```

> 关于 `SOFT_TTL` 与"绝不漏"的权衡：软过期窗口内信任缓存、不打探针，是为了对 BB 温和。代价是窗口内会话可能已被服务端单方失效。这不构成"漏"——因为 `bbclient` 的每次真实业务请求都套了下面的失效判定，一旦 401/被重定向就会即时 `invalidate` + 重登 + **重放该请求**，扫描数据仍然完整。`SOFT_TTL` 只决定"何时主动探活"，不决定"是否容忍拉空"；真正的兜底是请求级重放，不是 TTL。

**失效检测如何接入 `bbclient`：** `bbclient` 的每个请求包一层判定——

```
resp = session.request(...)
if resp.status_code == 401 \
   or (resp.is_redirect and _location_is_login(resp.headers.get("Location"))):
       session_manager.invalidate("api_401_or_login_redirect")
       session = session_manager.get_session(force_relogin=True)   # 重登一次
       resp = session.request(...)                                 # 重放该请求
       if still_failing(resp):
           raise SessionRefreshError(...)   # 重登后仍失效 -> 让本轮扫描显式失败
```

`_location_is_login` 判断 `Location` 是否指向 `sts.cuhk.edu.cn/adfs` 或 BB 登录端点。**每个原始请求只允许因失效触发一次重登重放**，避免"重登→又被踢→再重登"的死循环；二次仍失败则抛错，由扫描器记为"本轮失败"而非"本轮无新项"——这是不漏的关键：**失败必须可观测，绝不能被 diff 静默吞成空集。** 扫描器据此跳过快照更新（不把空结果写成新基线），下一轮会重试并补齐。

`do_login()` 内部：`load_credential()`（无则抛 `CredentialError` 提示 `/bb-setup`）→ `login_with_fallback()`（6.4）→ 原子写 cookies + uid → 更新 `auth_state`（`last_login_ok`、`last_validated`、清零 `consecutive_auth_failures`、清 `circuit_open_until`）。

**文件锁的必要性：** 三类触发（SessionStart 钩子、`/bb-scan`、清单服务周期循环）可能并发，且分属不同进程。用 `~/Library/Application Support/bbwatch/session/login.lock` 的 `fcntl.flock` 排他锁串行化登录：先拿到锁者登录后写缓存，后到者进入临界区后**先重新读缓存**，命中即复用，避免对 ADFS 重复打登录请求（既慢又像攻击）。注意"双重检查"：拿锁后必须重新 `load_cached_session()`，不能用拿锁前的判断结果。

---

### 6.7 重试与指数退避

两个层次，分开处理，避免把"凭据错"也无脑重试（凭据错重试只会触发账号锁定风控）。

**(a) 传输级重试** — 仅针对**瞬态**网络/TLS/5xx，在 `transport.py` 包装：

```python
RETRYABLE = (TransportError,)         # 连接超时、TLS 偶发、502/503/504
def with_retry(fn, *, attempts=4, base=1.0, cap=30.0):
    for i in range(attempts):
        try:
            return fn()
        except RETRYABLE as e:
            if i == attempts - 1: raise
            sleep = min(cap, base * 2**i) + random.uniform(0, base)  # 指数退避 + 抖动
            time.sleep(sleep)
```

退避序列约 1s → 2s → 4s（+抖动），抖动防多课程扫描时请求对齐成尖峰。`429`（限速）若出现，优先读 `Retry-After` 头，否则按上式退避；`429` 视作瞬态可重试，但**不**计入下面的凭据熔断计数（它不是凭据问题）。

**(b) 登录级熔断（风控保护）** — `CredentialError` **绝不重试**（立即停，提示用户跑 `/bb-setup` 重录）。但为防"密码在钥匙串里恰好过期/被改"导致每 10 分钟周期扫描狂打错密码触发 AD 账号锁定，引入**持久化熔断**：

```
每次 CredentialError: consecutive_auth_failures += 1 (写 auth_state)。
达到 AUTH_FAIL_CIRCUIT (=3) 次连续凭据失败:
  -> 置 circuit_open_until = now + CIRCUIT_COOLDOWN (=1h), 写库;
  -> 期间 get_session() 直接抛 AuthCircuitOpenError, 不打 ADFS, 通知用户去 /bb-setup;
  -> 复位仅两条路: (1) /bb-setup 真实验证成功; (2) 冷却到期后自动允许再试一次。
```

熔断状态存 DB（6.5 的 `circuit_open_until`），**跨进程/跨 SessionStart 生效**——否则每开一次 Claude Code 就清零计数，熔断形同虚设。这把"凭据失效"从"每次扫描都撞墙"收敛为"撞 3 次就停手等人"，对学校 AD 友好，也避免锁号。

熔断与"绝不漏"的关系：熔断期间扫描会显式失败（抛 `AuthCircuitOpenError`），扫描器同样**不更新快照**，待人工 `/bb-setup` 修复后下一轮全量 diff 自动补齐所有积压项——不漏由全量 diff 兜底，熔断只是延后而非丢弃。

---

### 6.8 错误分类（`errors.py`）

精确分类是"自愈 vs 停手求助"决策的前提。统一异常树，**任何异常的 `str()`/`repr()` 都不含密码、cookie、`code`**：

```python
# engine/auth/errors.py
class AuthError(Exception): ...

class TransportError(AuthError):
    """网络/TLS/代理/超时/5xx 等瞬态层。-> 可重试(6.7a); Step1 触发回退(6.4)。"""

class CredentialError(AuthError):
    """账号或密码错(ADFS 返回错误文案, 无授权码)。-> 不重试; 计入熔断; 提示 /bb-setup。"""

class MfaRequiredError(AuthError):
    """登录被要求第二因子(当前实测无, 为未来兜底)。-> 切交互式会话复用(6.9)。"""

class AntiBotError(AuthError):
    """疑似风控/页面改版: 拿不到登录表单、探针 200 却非预期、出现验证码挑战。
       -> 不盲目重试; 退避 + 通知用户; 可能需更新解析器/指纹档位。"""

class SessionRefreshError(AuthError):
    """会话失效后重登+重放仍失败(6.6)。-> 本轮扫描显式失败, 不更新快照。"""

class AuthCircuitOpenError(AuthError):
    """连续凭据失败触发熔断, 自动登录暂停冷却中(6.7b)。"""
```

| 信号（实测/预期） | 分类 | 处置 |
|---|---|---|
| Step1 连接 `UNEXPECTED_EOF`/超时 | `TransportError` | 退避重试 → 仍败则回退子进程 curl |
| `502/503/504`、读超时 | `TransportError` | 指数退避重试 |
| `429` 限速 | `TransportError`（不计熔断） | 读 `Retry-After`，退避重试 |
| POST 后 200 + ADFS 错误文案、无 `code` | `CredentialError` | 停；计熔断；提示重录凭据 |
| 拿不到含密码框的 `<form>`、探针非 200 | `AntiBotError` | 退避 + 通知；可能要维护解析/指纹 |
| `Location` 指向 `/additionalauth`、OTP 挑战 | `MfaRequiredError` | 转 6.9 交互式复用 |
| REST 返回 401 或被 302 到登录 | （会话失效，非异常） | `invalidate` → 重登一次重放（6.6） |
| 重登+重放后仍 401/被踢 | `SessionRefreshError` | 本轮扫描失败，不更新快照，下轮补齐 |

分类决定行为：`TransportError` 自愈（重试/回退）；`CredentialError`/`AntiBotError`/`AuthCircuitOpenError` 停手并精确通知（macOS 通知 + 清单页 banner）；`SessionRefreshError` 让扫描器把本轮记为失败而非空集。所有路径都不在错误里泄露任何敏感串。

---

### 6.9 未来 MFA 兜底：交互式会话复用扩展点

当前实测**无 MFA**，全自动可行。但学校随时可能开启（或仅校外网络触发）。设计预留扩展点，使开启 MFA 后无需重构核心：把"如何获得一个带 BB 会话 cookie 的 jar + uid"抽象成策略接口。

```python
# engine/auth/session.py
class LoginStrategy(Protocol):
    def login(self, cred: Credential | None) -> CachedSession: ...

class AutoAdfsStrategy:      # 当前默认: 6.3 全自动账号密码流
    ...

class InteractiveReuseStrategy:   # MFA 兜底: 用户在浏览器自己过 MFA, 我们复用其会话
    ...
```

`MfaRequiredError` 被抛出时，`SessionManager` 自动从 `AutoAdfsStrategy` 切到 `InteractiveReuseStrategy`。后者的可选实现（从简到全）：

1. **手动 cookie 导入**（最简，零依赖）：提示用户在浏览器登录 BB 后，把 `bb.cuhk.edu.cn` 的会话 cookie 导出/粘贴（或读浏览器 cookie 文件），写入同一 `cookies.json` jar。后续 REST 与自愈逻辑完全复用——失效检测仍走 6.6，只是"重登"动作变成"再次请用户过一次 MFA"。
2. **半自动浏览器驱动**：借用环境中已有的浏览器自动化（Claude Code 的 chrome/computer-use 能力或本地 Playwright），打开 ADFS 页，自动填 `UserName/Password`，在 MFA 步骤**暂停等用户输二次码**，完成后抓 BB 会话 cookie 回灌 jar。
3. **Kmsi 延寿**：已对 ADFS 传 `Kmsi=true`，会话寿命被拉长，使"需要人过 MFA"的频率降到最低——理想情况一天一次。

无论哪种策略，产出物都统一为 `CachedSession`（活 cookie + uid），`bbclient`/`scanner` 之上的一切代码**零改动**。这是把全自动与半自动隔离在认证层、不污染上层的关键。注意：交互式策略下不能自动重登，因此 6.6 的请求级重放在二次失效时会落到 `SessionRefreshError` + 通知用户"请重新登录"，而非静默——失效仍可观测，不漏不变。

---

### 6.10 本章对外契约小结

| 符号 | 签名 / 取值 | 说明 |
|---|---|---|
| `SessionManager.get_session(force_relogin=False)` | `-> BBSession` | 引擎唯一会话入口；复用或自动重登；熔断/凭据错时抛对应异常 |
| `SessionManager.invalidate(reason)` | `-> None` | bbclient 收到 401/登录重定向时回调 |
| `adfs_login(curl, cred)` | `-> str (uid)` | 跑完 OAuth，回种 cookie，返回真实 uid |
| `credentials.interactive_setup()` | `-> None` | `/bb-setup`：录入→真实验证→存钥匙串→复位熔断 |
| `credentials.load_credential()` | `-> Credential \| None` | 从钥匙串无人值守还原凭据（`username` 是 AD 账号，非 BB uid） |
| `auth.errors.*` | 异常树 | 自愈 vs 停手求助的分类依据 |
| 会话文件 | `~/Library/Application Support/bbwatch/session/cookies.{json,txt}` (0600，目录 0700，原子写) | 敏感会话态，不入 SQLite 明文 |
| `auth_state` 表 | 见 6.5 DDL | 非敏感会话元数据 + 持久化熔断状态 |
| 关键常量 | `SOFT_TTL=20m`、`AUTH_FAIL_CIRCUIT=3`、`CIRCUIT_COOLDOWN=1h`、`IMPERSONATE="chrome124"` | 可配置；在配置层暴露 |

设计准则贯穿全章：**会话获取与失效自愈封死在 `engine.auth` 内**，`client_id`/`redirect_uri`/ADFS 表单字段全部对齐实测；BB userId（`_49765_1`）由登录探针取定并贯穿，绝不与登录账号或 `me` 别名混用；凭据只活在钥匙串与进程内存、绝不落盘明文/进日志，会话态原子写入 0600 文件；传输层用 curl_cffi 浏览器指纹 + 代理绕开实测 TLS 坑并保留子进程 curl 回退；错误分类驱动"重试/回退/停手/转交互"四种确定性处置；持久化熔断防账号锁定。最关键的是：**会话失效绝不被静默吞成空集**——请求级重放兜底、失败显式抛错、扫描器据此不更新快照，使全量 diff 在下一轮无损补齐，从认证层为"绝不漏 / 绝不重复"上了锁。

---

## 7. BB REST 客户端

本章定义 `engine.bbclient` 模块:把附录 A 中全部已实测的 Blackboard Learn REST 端点封装为带类型的方法,处理分页、16 周窗口分片、限速、`fields` 精简、统一错误与重试。本模块**只负责"把结构化数据完整、确定地拿回来"**,不做 diff、不写库、不通知——那些是 `scanner`/`store`/`notifier` 的职责。它依赖 `engine.auth` 提供一个已登录、会自动重登的传输会话。

对"绝不漏 / 绝不重复"硬需求,本模块承担两条**可被上层信赖的契约**(§7.12 汇总,全章为其服务):

1. **全集保证**:凡返回"集合"的方法(`list_*`),要么返回该资源在 BB 侧的**完整全集**(所有页、所有递归层、所有窗口分片都已穷尽),要么**抛异常**——绝不静默返回部分结果。上层据此做全量 diff,部分结果会被误判为"项目消失/新增",直接破坏 no-miss/no-dup。
2. **稳定 id 保真**:每个返回对象都携带 BB 的稳定主键(column/announcement/content/attachment id 原样、不加工),作为上层去重与 diff 的唯一键。

所有断言均对应 `bb_findings.md` 与设计文档附录 A 的实测事实;每个端点下方标注其来源事实。

---

### 7.1 边界与依赖

```
engine/bbclient/
  ├─ __init__.py        # 导出 BBClient 与所有返回模型
  ├─ models.py          # dataclass 返回模型(本章 §7.3)
  ├─ transport.py       # 低层 HTTP:会话注入、限速、重试、分页、错误归一(§7.7–7.10)
  ├─ client.py          # BBClient:高层端点方法(§7.4–7.6)
  └─ errors.py          # 异常层级(§7.9)
```

- **上游依赖** `engine.auth.Session`:提供 `request(method, url, *, params, headers, allow_redirects, stream) -> Response`,内部已是 `curl_cffi`(浏览器 TLS 指纹)、已带会话 cookie、尊重环境代理 `127.0.0.1:7890`,并在收到 401 或被重定向到 ADFS 登录页时**自动重登一次再重放**该请求。**`bbclient` 不碰 TLS、cookie、代理或密码**——这是实测踩坑(anaconda `requests` 直连 ADFS 握手失败 `UNEXPECTED_EOF`、`curl` 正常)后刻意的分层:传输指纹问题归 `auth`,REST 语义归 `bbclient`。
- **基址常量**:`API = "https://bb.cuhk.edu.cn/learn/api/public/v1"`。所有请求带 `Accept: application/json`(由 `auth.request` 默认注入)。
- **会话单一性**:一个 `BBClient` 实例全程复用同一个 `auth.Session`(实测登录较重),不每次重登——对应"复用会话/好公民"要求。
- **不可移植**:仅面向本实例,所有路径硬编码,不抽象多租户。

---

### 7.2 设计约束(对得上实测)

| 约束 | 来源事实 | 实现位置 |
|---|---|---|
| `/users/me` 可取本人 `id`;但 `me` 别名在 **per-user 子资源**(如 `users/me/courses`)不可用,必须真实 uid | 实测:`/users/me` 正常,`users/me/courses` 失效 | `BBClient` 构造时用 `/users/me` 取得并缓存 `self.uid`,所有 per-user 端点强制传真实 uid(§7.4) |
| `paging` 字段仅在有下一页时出现,含 `nextPage` 链接 | 实测分页结构非固定 | `_paginate()` 据 `results` + `paging.nextPage` 判断,不假设固定键;带不前进保护(§7.8) |
| 日历窗口必须 ≤16 周,超出报 400 | 实测超窗 400 | `list_calendar()` 按 <16 周分片翻页,`id` 去重合并(§7.6) |
| 时间均为 UTC(形如 `2026-06-30T15:59:00.000Z`) | 实测 `grading.due`/`created`/`end` 全 UTC | 模型存 aware `datetime`(tzinfo=UTC),展示转 +8 是上层职责(§7.3) |
| 附件下载返回 302 跳真实文件 | 实测需跟随重定向 | `download_attachment()` 跟随重定向并流式落盘、原子替换(§7.5) |
| 汇总列(`Weighted Total`/`Total`)无 `grading.due` | 实测需过滤 | 模型保留全部列,`has_due` 派生属性供上层过滤,**不在客户端丢弃任何列**(§7.3) |
| `fields=` 在**集合端点**可精简返回 | 实测支持点路径 `results.x.y` | 每个 `list_*` 方法传最小 `fields` 集(§7.11) |

> **未实测、刻意不依赖的点**(列明以免后人误信):
> - `fields=` 在**单对象端点**(per-user column status)是否生效未验证 → §7.4 该方法**不传 `fields`**,取完整小对象。
> - 日历项里是否带**课程归属字段**(`calendarId`/`courseId`)未验证 → `CalendarItem.course_id` 标注为"可能为 None",上层**不得**依赖它做按课程归并;跨课程 ddl 的权威来源仍是 per-course `list_columns`(信息更全,含 `contentId`/`status`),日历仅作"是否有遗漏列"的交叉校验(§7.6、§7.12)。

---

### 7.3 返回模型(`models.py`)

全部为 `@dataclass(frozen=True, slots=True)`。时间字段在解析时统一转为 **aware `datetime`(UTC)**;原始 JSON 缺字段时填 `None`。字段名直接对应实测 JSON 路径。

```python
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

# --- 学期 (GET /terms) ---
@dataclass(frozen=True, slots=True)
class Term:
    id: str                 # termId, e.g. "_181_1"
    name: str               # e.g. "2550UG"
    available: bool         # availability.available == "Yes"

# --- 课程成员 (GET /users/{uid}/courses?expand=course) ---
class Availability(str, Enum):
    YES = "Yes"; NO = "No"; TERM = "Term"; DISABLED = "Disabled"

    @classmethod
    def _missing_(cls, value):      # 未知取值不崩,归一为 DISABLED(保守=不当在读)
        return cls.DISABLED

@dataclass(frozen=True, slots=True)
class CourseMembership:
    course_id: str          # course.id 内部 id, e.g. "_17236_1"  <- 后续所有 {cid} 用它
    course_code: str        # course.courseId 人类可读, e.g. "MAT3007:Optimization_L01"
    name: str               # course.name
    term_id: str | None     # course.termId
    availability: Availability   # course.availability.available
    role: str               # courseRoleId, e.g. "Student"
    ultra_status: str       # course.ultraStatus, e.g. "Classic"

    @property
    def is_active_student(self) -> bool:
        # 实测"在读"= role=Student 且 availability ∈ {Yes, Term}(19 门 membership → 17 门在读)
        return self.role == "Student" and self.availability in (
            Availability.YES, Availability.TERM)

# --- 成绩册列 (GET /courses/{cid}/gradebook/columns) ---
@dataclass(frozen=True, slots=True)
class GradeColumn:
    id: str                 # column id  <- 新任务的稳定 diff key
    name: str               # e.g. "Homework 4"
    due: datetime | None    # grading.due (UTC); None=无截止
    content_id: str | None  # contentId 关联作业内容项
    score_possible: float | None  # score.possible
    grading_type: str | None      # grading.type

    @property
    def has_due(self) -> bool:
        # 实测:带 due 的列才是有截止的作业;Weighted Total/Total 无 due。
        # 仅作"上层过滤建议",客户端不据此丢列(留给 scanner 决策)。
        return self.due is not None

# --- 我的某列状态 (GET .../columns/{colId}/users/{uid}) ---
class GradeStatus(str, Enum):
    NONE = "None"                   # 未提交/无
    NEEDS_GRADING = "NeedsGrading"  # 已交待批
    GRADED = "Graded"               # 已批改

    @classmethod
    def _missing_(cls, value):      # 未知状态归一为 NONE(保守=未完成,不漏报)
        return cls.NONE

@dataclass(frozen=True, slots=True)
class GradeEntry:
    column_id: str
    user_id: str
    status: GradeStatus     # status 字段
    score: float | None     # score, 出分后非空
    exists: bool            # True=接口有此条目;False=404(从未交且无占位)

    @property
    def is_submitted(self) -> bool:
        return self.status in (GradeStatus.NEEDS_GRADING, GradeStatus.GRADED) \
               or self.score is not None

    @property
    def is_graded(self) -> bool:
        # 出分提醒的判据:status=Graded 或 score 非空(任一即视为已出分)
        return self.status is GradeStatus.GRADED or self.score is not None

# --- 日历项 (GET /calendars/items) ---
@dataclass(frozen=True, slots=True)
class CalendarItem:
    id: str
    type: str               # 实测本实例全为 "GradebookColumn"
    title: str
    course_id: str | None   # 课程归属字段未实测确认 → 可能为 None;勿据此做权威归并
    end: datetime | None    # end = 截止时间 (UTC)

# --- 公告 (GET /courses/{cid}/announcements) ---
@dataclass(frozen=True, slots=True)
class Announcement:
    id: str                 # 新公告 diff key
    course_id: str          # 由调用上下文注入(端点路径已含 cid)
    title: str
    created: datetime | None  # 发布时间 (UTC),排序用
    body: str               # 正文(可含考试/补课信息)

# --- 内容项 (GET /courses/{cid}/contents[/{id}/children]) ---
@dataclass(frozen=True, slots=True)
class ContentItem:
    id: str                 # content id 稳定 diff key
    course_id: str          # 由调用上下文注入
    parent_id: str | None   # 递归构树用;顶层为 None
    title: str
    handler: str            # contentHandler.id: resource/x-bb-folder|-document|-file|-assignment
    created: datetime | None
    modified: datetime | None  # 新课件/更新检测用(diff 辅助键)
    position: int | None
    has_children: bool      # hasChildren

    @property
    def is_folder(self) -> bool:
        return self.handler == "resource/x-bb-folder"

# --- 附件 (GET .../contents/{id}/attachments) ---
@dataclass(frozen=True, slots=True)
class Attachment:
    id: str                 # attachment id 稳定 diff key
    content_id: str         # 由调用上下文注入
    course_id: str          # 由调用上下文注入
    file_name: str          # fileName
    mime_type: str | None   # mimeType
```

时间解析助手(集中一处,处理 `Z` 后缀与毫秒;解析失败返回 `None` 而非崩溃,避免单条坏时间拖垮整轮扫描):

```python
def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # 实测形如 "2026-06-30T15:59:00.000Z";统一 aware-UTC
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None   # 容错:坏格式不阻断本轮全集返回
```

> 注:`course_id`/`content_id` 等"由上下文注入"的字段,其值来自调用方传入的路径参数(端点 URL 里已含),而非响应体——保证即便响应体不回带归属也不丢失关联。

---

### 7.4 端点方法签名(`client.py`)

`BBClient` 构造时用 `/users/me` 解析当前用户 id 并缓存为 `self.uid`,以满足"`me` 别名在 per-user **子资源**不可用"的实测约束。

```python
class BBClient:
    def __init__(self, session: "engine.auth.Session",
                 *, rate: "RateLimiter | None" = None) -> None:
        self._t = Transport(session, rate=rate or RateLimiter())
        # /users/me 本身可用(实测);仅其下子资源的 me 别名不可用
        self.uid: str = self._t.get_json("/users/me",
                                         params={"fields": "id"})["id"]  # e.g. "_49765_1"

    # --- 学期与课程 ---
    def list_terms(self) -> list[Term]: ...
    def list_courses(self, uid: str | None = None) -> list[CourseMembership]:
        """uid 默认 self.uid;'me' 在此子资源不可用故必须真实 uid。expand=course,翻页穷尽。"""

    # --- 成绩册(任务权威载体) ---
    def list_columns(self, course_id: str) -> list[GradeColumn]:
        """返回该课全部列(含汇总列);是否过滤交上层据 has_due 决定。"""
    def get_column_status(self, course_id: str, column_id: str,
                          uid: str | None = None) -> GradeEntry:
        """uid 默认 self.uid。单对象端点,不传 fields(未实测其支持)。
           404(从未交且无占位)→ GradeEntry(exists=False, status=NONE),不抛错。"""

    # --- 跨课程截止日历(交叉校验,非权威) ---
    def list_calendar(self, since: datetime, until: datetime) -> list[CalendarItem]:
        """窗口 ≤16 周硬约束:内部按 <16 周分片 + 每片翻页,按 id 去重合并。
           覆盖 [since, until) 全程,任一分片失败则整体抛错(不返回半集)。"""

    # --- 公告 ---
    def list_announcements(self, course_id: str) -> list[Announcement]: ...

    # --- 内容树(递归全集) ---
    def list_contents(self, course_id: str,
                      *, recursive: bool = True) -> list[ContentItem]:
        """recursive=True:对 has_children 的项 BFS 拉 children,扁平返回(带 parent_id 可重建树);
           带环/重复 id 保护(§7.5)。任一层失败则整体抛错。"""
    def list_attachments(self, course_id: str,
                         content_id: str) -> list[Attachment]: ...

    # --- 附件下载 ---
    def download_attachment(self, course_id: str, content_id: str,
                            attachment_id: str, dest: "pathlib.Path") -> "pathlib.Path":
        """跟随 302 流式落盘到 dest(.part + 原子 replace);返回最终路径。不读全量进内存。"""
```

各方法到端点的映射,与每条对应的实测事实:

| 方法 | 端点 | 实测依据 |
|---|---|---|
| `list_terms` | `GET /terms?limit=100&fields=results.id,results.name,results.availability.available` | `termId→name`(如 `2550UG`),识别当前学期 |
| `list_courses` | `GET /users/{uid}/courses?expand=course&limit=100&fields=…` | 19 门 membership / 17 门在读;`me` 子资源别名不可用;`limit≤100` 翻页 |
| `list_columns` | `GET /courses/{cid}/gradebook/columns?limit=100&fields=…` | 字段 `id/name/grading.due/contentId/score.possible/grading.type`;汇总列无 `due` |
| `get_column_status` | `GET /courses/{cid}/gradebook/columns/{colId}/users/{uid}` | `status`∈{None,NeedsGrading,Graded},`score`;无该用户条目→404 |
| `list_calendar` | `GET /calendars/items?since&until&limit=100` | 窗口 ≤16 周报 400;返回 `type=GradebookColumn` 带 `end` |
| `list_announcements` | `GET /courses/{cid}/announcements?limit=100&fields=…` | `id/title/created/body`;新 id=新公告 |
| `list_contents` | `GET /courses/{cid}/contents` + `…/contents/{id}/children` | 顶层多为 `x-bb-folder`,`hasChildren` 递归,`modified` 判更新 |
| `list_attachments` | `GET /courses/{cid}/contents/{id}/attachments?fields=…` | `id/fileName/mimeType` |
| `download_attachment` | `GET /courses/{cid}/contents/{id}/attachments/{aid}/download` | 302 跳真实文件,需跟随 |

---

### 7.5 内容树递归与附件下载(伪代码)

**递归内容树** 用 BFS,只对 `has_children=True` 的项展开,避免对叶子做无谓 `children` 请求;每个请求都过限速器。**关键 no-dup/no-miss 加固**:维护 `visited` 集合,绝不二次展开同一 content id,既防 BB 数据成环导致死循环,也防同一项被重复收入全集。

```python
def list_contents(self, course_id, *, recursive=True):
    out: list[ContentItem] = []
    visited: set[str] = set()
    # 顶层(穷尽分页)
    top = self._t.paginate(
        f"/courses/{course_id}/contents",
        params={"limit": 100, "fields": _CONTENT_FIELDS})
    queue = [_content(course_id, parent_id=None, raw=r) for r in top]
    while queue:
        item = queue.pop(0)
        if item.id in visited:        # 防环 + 防重复收录
            continue
        visited.add(item.id)
        out.append(item)
        if recursive and item.has_children:
            kids = self._t.paginate(
                f"/courses/{course_id}/contents/{item.id}/children",
                params={"limit": 100, "fields": _CONTENT_FIELDS})
            queue.extend(_content(course_id, parent_id=item.id, raw=r) for r in kids)
    return out      # 全集或(任一 paginate 抛错时)异常,绝不半集
```

**附件下载** 必须跟随 302 且流式落盘(实测 `MAT3007` 单课 50 个文件,不能整文件读内存):

```python
def download_attachment(self, course_id, content_id, attachment_id, dest):
    path = (f"/courses/{course_id}/contents/{content_id}"
            f"/attachments/{attachment_id}/download")
    self._t.rate.acquire()
    # auth.request 已带 cookie/代理/TLS 指纹;此处显式 allow_redirects=True + stream
    with self._t.session.request("GET", self._t.url(path),
                                 allow_redirects=True, stream=True) as resp:
        self._t.raise_for_status(resp, path)        # 归一错误(§7.9)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(64 * 1024):
                f.write(chunk)
        tmp.replace(dest)        # 原子落盘:要么完整文件,要么不存在,断点不留半截
    return dest
```

> 下载的"是否需要重新下"判定(按 content `modified` 增量镜像)在 `engine.downloader`,非本模块职责。本模块只保证"给定 id 能把字节**完整且原子**地落盘"——半截文件会让 downloader 误判已下载,是 no-miss 的隐形杀手,故用 `.part`+`replace` 杜绝。

---

### 7.6 16 周窗口分片(`list_calendar`)

实测 `calendars/items` 的 `since`/`until` 跨度 >16 周直接 400。要覆盖整学期(约 15–18 周,加首尾缓冲常 >16 周),必须分片;且同一截止项可能落在相邻片边界,需按 `id` 去重。

```python
_MAX_WINDOW = timedelta(weeks=16) - timedelta(days=1)   # 留 1 天余量,稳避边界 400

def list_calendar(self, since, until):
    seen: dict[str, CalendarItem] = {}
    cursor = since
    while cursor < until:
        chunk_end = min(cursor + _MAX_WINDOW, until)
        rows = self._t.paginate(                       # 任一分片抛错 → 整体抛错(不半集)
            "/calendars/items",
            params={"since": _iso(cursor), "until": _iso(chunk_end), "limit": 100})
        for r in rows:
            it = _calendar_item(r)
            seen[it.id] = it           # id 去重:相邻片边界重叠安全,无重复
        cursor = chunk_end             # 前闭后开拼接,无缝隙、不漏
    return list(seen.values())
```

`_iso()` 输出 UTC ISO8601(带 `Z`)。调用方(scanner)传整学期 `[term_start - 1w, term_end + 1w]`;本方法保证无论跨度多大都**连续覆盖、无缝隙、按 id 去重**(同时满足"不漏"与"不重")。

> **定位**:日历是**交叉校验源**,不是任务权威源。权威任务集来自逐课 `list_columns`(含 `contentId`/可配 `status`)。scanner 可用"日历有、但某课 columns 没扫到"来发现潜在遗漏(如某课临时不在白名单却有 ddl),但**不**把日历项直接当任务落库——避免与 columns 同一作业产生双重身份(no-dup)。两者的天然连接键是 column id:实测日历项 `type=GradebookColumn`,其 `id` 即对应成绩册列 id。

---

### 7.7 限速与请求间延时(好公民)

令牌桶 + 每请求后最小间隔,集中在 `transport.py`,**所有**出站请求(含分页每页、递归每层、每次下载、构造期的 `/users/me`)都过 `rate.acquire()`。

```python
@dataclass
class RateLimiter:
    min_interval: float = 0.4      # 单请求间至少 400ms
    burst: int = 4                 # 允许的瞬时突发
    _last: float = field(default=0.0)
    _tokens: float = field(default=4.0)

    def acquire(self) -> None:
        now = time.monotonic()
        # 按经过时间恢复令牌
        self._tokens = min(self.burst,
                           self._tokens + (now - self._last) / self.min_interval)
        if self._tokens < 1.0:
            time.sleep(self.min_interval - (now - self._last))
        self._tokens -= 1.0
        self._last = time.monotonic()
```

- 默认 `min_interval=0.4s`(≈ ≤2.5 req/s 稳态),可由配置下调到更温和值。一次全量扫描(17 门 × {columns, announcements, contents 递归, 各 column status})≈ 数百请求,在该速率下数十秒内完成,对 BB 友好。
- `RateLimiter` 非线程安全;若清单服务的周期扫描与 SessionStart 扫描可能并发,**由 `scanner` 用单一调度串行化扫描**(共享同一 `BBClient`),而非在限速器内加锁——并发扫描既无必要也会放大对 BB 的压力。

---

### 7.8 分页(`paginate`)

实测关键:`paging` 字段**仅在有下一页时出现**,且含 `nextPage`(一个可直接 GET 的链接,已带 offset)。不能假设固定结构,也不能自己拼 offset(以 BB 给的 `nextPage` 为准最稳)。**no-miss 关键**:必须穷尽所有页才返回;任何一页失败都向上抛(由重试层先兜底,仍失败则整次 `list_*` 失败,绝不返回缺页的"伪全集")。

```python
def paginate(self, path, *, params=None) -> list[dict]:
    results: list[dict] = []
    next_url: str | None = None
    guard = 0
    while True:
        guard += 1
        if guard > 10_000:                       # 死循环兜底(nextPage 异常自指等)
            raise BBError(f"pagination runaway: {path}")
        if next_url is None:
            data = self.get_json(path, params=params)
        else:
            data = self.get_json(next_url, params=None)   # nextPage 已含全部 query
        results.extend(data.get("results", []))
        paging = data.get("paging")                       # 缺失=末页
        nxt = paging.get("nextPage") if paging else None
        if not nxt:
            return results                                # 穷尽,全集
        if nxt == next_url:                               # nextPage 不前进 → 防无限循环
            raise BBError(f"pagination not advancing: {path}")
        next_url = nxt
```

- `get_json` 接受相对 API 路径或 `nextPage` 形态的 URL(可能是相对或绝对),内部统一归一到绝对 URL(`url()` 对已带 scheme 的原样使用,否则拼 `API` 基址)。
- `limit` 一律取实测上限 `100`(`users/{uid}/courses`、`calendars/items` 实测 ≤100),减少页数与往返。

---

### 7.9 统一错误(`errors.py`)与归一

```python
class BBError(Exception): ...                 # 基类
class BBAuthError(BBError): ...               # 401/被弹回 ADFS(交回 auth 重登)
class BBNotFound(BBError): ...                # 404
class BBBadWindow(BBError): ...               # calendars 400(窗口>16周;不应发生,发生即逻辑 bug)
class BBRateLimited(BBError): ...             # 429
class BBServerError(BBError): ...             # 5xx
class BBTransportError(BBError): ...          # 连接/TLS/超时(curl_cffi 层)
```

`raise_for_status` 把 HTTP 状态归一为上述类型;**错误对象只携带 method+path+status,绝不携带 cookie/token/密码/响应体**(对应安全约束"不进日志")。

```python
def raise_for_status(self, resp, path):
    s = resp.status_code
    if s < 400:
        return
    if s == 404:
        raise BBNotFound(path)
    if s == 401:
        raise BBAuthError(path)              # 一般已被 auth 自动重登拦截;仍兜底
    if s == 429:
        raise BBRateLimited(path)
    if s == 400 and path.startswith("/calendars/items"):
        raise BBBadWindow(path)             # 分片逻辑保证不触发;触发即 bug,需修分片
    if 500 <= s < 600:
        raise BBServerError(f"{s} {path}")
    raise BBError(f"{s} {path}")
```

`get_column_status` 对 `BBNotFound` 做**语义转换**(从未提交、列下无该用户条目)→ 返回 `GradeEntry(exists=False, status=NONE, score=None)`,而非抛错——这是有意的:把"未交"建模为合法状态而非异常,scanner 才能把它纳入待办,不会因异常吞掉一条任务(no-miss)。**注意边界**:此 404→"未交"的语义化**仅限** `get_column_status` 这一个端点;其余 `list_*` 端点收到 404 一律抛 `BBNotFound`(课程/内容被删属真实异常,不能静默当空集,否则会被 diff 误判为"全部消失"而误删本地已知集)。

---

### 7.10 重试策略

只对**幂等只读 GET** 与下载重试(下载有 `.part`+原子 replace 保证可安全重放)。

```python
_RETRYABLE = (BBTransportError, BBServerError, BBRateLimited)
_MAX_RETRIES = 3

def _with_retry(self, fn):
    delay = 1.0
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return fn()
        except _RETRYABLE as e:
            if attempt == _MAX_RETRIES:
                raise                       # 耗尽 → 上抛 → 该次 list_* 失败(不返回半集)
            # 429 优先用 Retry-After;否则指数退避 + 抖动
            sleep = self._retry_after(e) or (delay + random.uniform(0, 0.3))
            time.sleep(sleep)
            delay *= 2
        # BBAuthError 不在此重试:交回 auth.request 的"重登一次再重放"(§7.1);
        # 若仍 401 则上抛,由 scanner 标记需重新 /bb-setup。
```

- `BBNotFound`/`BBBadWindow`/其它 4xx 业务类**不重试**——重试无意义。
- `BBTransportError`(对应实测 `UNEXPECTED_EOF` 类握手抖动)可重试,`curl_cffi` 偶发抖动重试通常即恢复。
- **与 no-miss 的关系**:重试是为了把"瞬时失败"挡在"返回半集"之前。一旦重试耗尽仍失败,**宁可整次扫描该资源失败并让 scanner 跳过本轮 diff**(保留上次快照不动),也不接受"缺了一页/一层却当全集写库"——后者会让 store 误以为项目消失而错误地把任务标记为不存在。scanner 须把 `list_*` 抛出的异常当作"本资源本轮不可信,沿用旧快照",绝不当空集。

---

### 7.11 `fields` 精简清单

每方法只取下游(scanner/downloader)真正用到的字段,减小负载、降低 BB 负担(实测 `fields=` 在集合端点支持点路径选择 `results.x.y`)。

```python
_TERM_FIELDS    = "results.id,results.name,results.availability.available"
_COURSE_FIELDS  = ("results.courseRoleId,results.availability.available,"
                   "results.course.id,results.course.courseId,results.course.name,"
                   "results.course.termId,results.course.ultraStatus")
_COLUMN_FIELDS  = ("results.id,results.name,results.contentId,"
                   "results.grading.due,results.grading.type,results.score.possible")
_ANN_FIELDS     = "results.id,results.title,results.created,results.body"
_CONTENT_FIELDS = ("results.id,results.title,results.created,results.modified,"
                   "results.position,results.hasChildren,results.contentHandler.id")
_ATTACH_FIELDS  = "results.id,results.fileName,results.mimeType"
# get_column_status:单对象端点,fields 是否生效未实测 → 不传,取完整小对象(status/score)。
# calendars/items:返回项少且需 id/end/type(及可能的 course 归属)→ 不裁剪,取全字段。
```

---

### 7.12 与上层的契约小结

- **稳定 id 全集供给(no-miss 基石)**:`list_columns`/`list_announcements`/`list_contents`/`list_attachments` 各自返回带稳定 `id` 的**完整全集或抛异常**,使 scanner 能与 store 已知集合做**全量 diff**(非时间窗增量)——多天未扫也能一次补齐。任何"半集"都被错误层杜绝:缺页/缺层/缺分片一律抛错,由 scanner 沿用旧快照而非写入残缺集。
- **完成态可判(no-dup 提醒基石)**:`get_column_status` 提供 `status`/`score`/`exists`,使 scanner 区分 未交(`None`/`exists=False`)/待批(`NeedsGrading`)/已批(`Graded`),实现"已交不再提醒""出分只提醒一次"且天然幂等。
- **日历仅作交叉校验,不作权威**:`list_calendar` 与 `list_columns` 的连接键是 column id(日历项 `type=GradebookColumn`);scanner 用日历查漏,但落库任务以 columns 为唯一身份,杜绝同一作业双重计数(no-dup)。`CalendarItem.course_id` 可能为 None,不得用于权威归并。
- **时间一律 UTC aware**:展示转东八区(+8)由 dashboard/notifier 负责,本模块不做本地化,避免双重转换出错。
- **无副作用、可回放测试**:本模块不写 SQLite、不发通知、不决定"是否需重新下载";纯拉取,便于单测——对每个端点录制实测 JSON 做夹具,并专门覆盖**分页边界、16 周分片边界、内容树成环、404 语义化、半集必抛**这几条 no-miss/no-dup 关键路径。

---

相关文件路径(绝对):
- 设计依据(实测事实):`/private/tmp/claude-501/-Users-mac-Programming-cuhkszbb/82db56a5-da23-42e2-a147-6a5d1bfa6443/scratchpad/bb_findings.md`
- 既有总设计(附录 A 端点表):`/Users/mac/Programming/cuhkszbb/docs/superpowers/specs/2026-06-28-bbwatch-design.md`
- 本章对应将实现模块:`/Users/mac/Programming/cuhkszbb/engine/bbclient/`(`models.py` / `transport.py` / `client.py` / `errors.py`)

---

## 8. 扫描编排与 Diff 算法

本章定义 `engine.scanner` 的完整行为：一次 `scan()` 如何把"四类提醒"从 BB 的原始 JSON 推导出来。两条最高优先约束贯穿全章——**绝不漏（按稳定 id 做全量集合 diff，而非时间窗口增量）**与**绝不重复（稳定 id + store 的 `UNIQUE(dedup_key)` 幂等闸门）**。scanner 只负责"拉取 → 规整 → 与快照 diff → 产出事件草稿"；落库去重、基线抑制、通知状态推进、完成态判定全部下沉到 `engine.store` 与第 9 章的 robustness 状态机。scanner 本身**无状态**。

### 8.1 数据流总览

```
scan(trigger)                       # trigger ∈ {session_start, manual, periodic}
  ├─ run_id  = store.begin_scan(trigger)       # 写 scan_run 行，拿单调递增 run_id
  ├─ session = auth.get_session()              # 复用 cookie；过期则 ADFS 重登（第6章）
  ├─ uid     = resolve_uid(session)            # GET users/me 取真实 _49765_1，缓存
  ├─ courses = select_active_courses(session, uid)   # §8.3 在读过滤
  ├─ drafts  = await scan_courses(session, uid, courses, run_id)  # §8.7 课级并发
  ├─ drafts += derive_deadline_soon(uid, run_id)     # §8.5.6 纯本地，不打网
  ├─ persisted = store.commit_events(drafts, run_id) # §8.6 幂等去重+基线抑制
  ├─ store.end_scan(run_id, ok|partial|failed, n_ok, n_failed)
  └─ return persisted                          # 仅"真正首次出现且非基线"的事件
```

**铁律不变量**（这是"绝不重复"的根）：

1. **`commit_events` 之前不发任何通知，也不写任何 `known_*` 快照行。** scanner 产出的全是"事件草稿（`EventDraft`）"。是否首次出现、是否属于基线，全部由 store 在**单事务**内裁决。
2. **快照 upsert 与事件落库在同一事务、同一 `run_id` 内提交**，且"先算 diff、再写快照"。绝不允许某一路在 diff 前就把新快照写进 `known_*`——否则同一 run 内（或并发 run 间）下一次比对会把刚出现的新项当成"已知"，造成**漏报**。draft 把 `store.upsert_*` 散落在 `scan_course` 里、与 diff 交错执行，是一个会同时破坏"不漏"和"不重"的顺序缺陷，本定稿予以纠正（见 §8.4、§8.6）。
3. SessionStart / `/bb-scan` / 周期循环三个触发源并发跑时，靠 `UNIQUE(dedup_key)` + `INSERT OR IGNORE` 保证同一草稿只有一个 run 插入成功，其余静默忽略。

### 8.2 核心数据结构与函数签名

文件：`engine/scanner.py`

```python
from dataclasses import dataclass
from enum import Enum

class EventType(str, Enum):
    NEW_TASK         = "NewTask"          # 新成绩册列（带 due 的真实作业）
    DEADLINE_SOON    = "DeadlineSoon"     # 未完成且 due 进入提醒窗口（纯本地）
    NEW_ANNOUNCEMENT = "NewAnnouncement"  # 新公告 id
    NEW_MATERIAL     = "NewMaterial"      # 新内容项 / modified 变化
    GRADE_POSTED     = "GradePosted"      # status→Graded 或 score 由空变非空

@dataclass(frozen=True)
class EventDraft:
    type: EventType
    course_pk: str          # course.id 内部 id，如 "_17236_1"
    entity_id: str          # 去重锚点（column_id / ann_id / content_id）
    title: str              # 人类可读，已就绪供通知/清单展示
    due_utc: str | None     # ISO8601 UTC，仅 NewTask/DeadlineSoon 携带
    payload: dict           # 附加字段（score、body 摘要、modified、window_h…）
    observed_run: int

# 顶层入口
def scan(trigger: str) -> list[Event]: ...

# 课级并发编排（§8.7）
async def scan_courses(session, uid, courses, run_id) -> list[EventDraft]: ...

# 单课编排：四路只读拉取 → 规整快照（不写库），返回 (course_pk, FreshSnapshot)
def fetch_course(session, uid, course, run_id) -> CourseFetch: ...

# 纯函数 differ：输入 = 新快照 + store 旧快照，输出 = 草稿（不写库）
def diff_columns(course_pk, fresh: list[Column]) -> list[EventDraft]: ...
def diff_grades(course_pk, fresh: dict[str, UserGrade]) -> list[EventDraft]: ...
def diff_announcements(course_pk, fresh: list[Announcement]) -> list[EventDraft]: ...
def diff_contents(course_pk, fresh: list[ContentNode]) -> list[EventDraft]: ...
def derive_deadline_soon(uid, run_id) -> list[EventDraft]: ...
```

`CourseRef` 字段：`course_pk`(=`course.id`，如 `_17236_1`，**稳定主键**)、`course_id`(人类可读 `MAT3007:Optimization_L01`)、`term_id`、`availability`、`display_name`。

`CourseFetch` 是一门课四路拉取的**规整结果**（含每路的 `value | None` 与各自错误标记），交由 store 在事务内统一 diff + upsert。`fetch_course` **只读不写**，`None` 表示该路本次未拉到（见 §8.4 局部失败隔离）。

### 8.3 在读课程过滤

来源：`GET /learn/api/public/v1/users/{uid}/courses?expand=course&limit=100`。两个实测约束必须遵守：**(a)** `me` 别名在 `users/me` 可用（用来取 uid），但在 `users/{uid}/courses` 这个**子资源上不可用**，必须传真实 uid（如 `_49765_1`）；**(b)** `limit≤100`，须按 `paging.nextPage` 翻页。配合 `GET /terms?limit=100` 求当前学期。

```python
def select_active_courses(session, uid) -> list[CourseRef]:
    memberships  = bbclient.list_memberships(session, uid)   # 自动翻页
    current_terms = compute_current_terms(bbclient.list_terms(session))
    cfg = config.load()
    out = []
    for m in memberships:
        c = m.get("course") or {}
        # 关1 角色：只盯学生课，排除助教/旁听噪声（实测 courseRoleId == "Student"）
        if m.get("courseRoleId") != "Student":
            continue
        # 关2 可用性：available ∈ {Yes, Term} 视为在读；No/Disabled 跳过
        avail = (c.get("availability") or {}).get("available")
        if avail not in ("Yes", "Term"):
            continue
        # 关3 学期：termId 属于当前学期集合（已结课自然落选）
        if c.get("termId") not in current_terms:
            continue
        ref = to_course_ref(c)
        # 关4 黑/白名单：白名单非空则只留白名单；否则去黑名单
        if not passes_course_filter(ref, cfg):
            continue
        out.append(ref)
    return out
```

补充规则，对齐实测：
- **availability 语义**：实测 `course.availability.available` ∈ {`Yes`, `No`, `Term`}；`Term`=随学期开放，与 `Yes` 同等视为在读。实测 19 门 membership → **17 门在读**，正是 `courseRoleId == "Student"` 且 `available ∈ {Yes, Term}` 的交集。
- **`courseRoleId` 的稳健性**：实测值为字面量 `"Student"`。考虑到 BB 偶有自定义角色名，配置提供 `extra_student_roles` 兜底列表；过滤**只用于减负**（少打无关课的网），不作为漏检兜底——见下一条。
- **当前学期判定**：`compute_current_terms` 优先用 `term.availability.duration.start/end` 框住 `now`（本实例 `termId` 形如 `2550UG`）；字段缺失则回退"出现频次最高的学期"启发式，并写日志供人工核对。**学期/角色/可用性三关只是优化**：若某门在读课被误判落选，其 column/announcement/content id 一旦在后续任一次扫描被纳入，仍会被全量 diff 补检（无漏根基不依赖过滤正确性）。误判方向是"少扫"而非"误删"——落选课**不会**触发任何快照收敛/删除（见 §8.4）。
- **黑/白名单**：以 `course_pk`（`_17236_1`，稳定）为主键存配置，同时接受 `course_id` 前缀模糊规则（如 `PED` 体育）。白名单优先于黑名单。典型用途=排除体育课、已退选但仍挂当前学期的课。
- `ultraStatus` 实测恒为 `Classic`，不作过滤条件，仅记录以备将来分流 Ultra 解析。

### 8.4 单课拉取编排（fetch_course，只读）

每门课四路只读拉取，**任一路失败不中止其余路**（局部失败隔离）。本阶段**只拉取与规整，绝不写库、绝不 diff**——diff 与 upsert 统一在 store 的事务里做（§8.6），以满足 §8.1 不变量 2。

```python
def fetch_course(session, uid, course, run_id) -> CourseFetch:
    cpk = course.course_pk
    f = CourseFetch(course_pk=cpk)

    # 路1 成绩册列（NewTask 锚，也是 grade / DeadlineSoon 的锚）
    cols = guarded(lambda: bbclient.get_columns(session, cpk), run_id, cpk, "columns")
    if cols is not None:
        f.columns = [c for c in cols if is_real_assignment_column(c)]  # §8.5.1

    # 路2 我的成绩/状态：仅对"带 due 的真实作业列"逐列查（控量）
    if f.columns is not None:
        statuses, any_fail = {}, False
        for col in f.columns:
            ug = guarded(lambda: bbclient.get_grade(session, cpk, col["id"], uid),
                         run_id, cpk, f"grade:{col['id']}")
            if ug is not None:
                statuses[col["id"]] = ug
            else:
                any_fail = True
        # 关键：只有"全部带 due 列都查到了"才允许 grade 路参与 diff；
        # 否则置 None，避免半截状态被当成"该列还没出分"产生漏报/误判
        f.grades = None if any_fail else statuses

    # 路3 公告（NewAnnouncement）
    f.announcements = guarded(lambda: bbclient.list_announcements(session, cpk),
                              run_id, cpk, "ann")
    # 路4 内容树递归（NewMaterial）
    f.contents = guarded(lambda: bbclient.walk_contents(session, cpk),
                         run_id, cpk, "contents")
    return f
```

**`guarded(fn, run_id, cpk, tag)`**：执行 fn，捕获网络/解析异常，写 `scan_error(run_id, course_pk, tag, msg_redacted)`，返回 `None`。

**`None` 路的语义（不漏的关键防线）**：differ 必须严格区分"拉到空列表 `[]`（确实没有）"与"`None`（没拉到）"。

- `None` 路**绝不进入 diff，绝不更新对应 `known_*` 快照，绝不触发任何删除/收敛**。一次网络抖动不能把整门课的已知集合误判为"全没了"。
- 对 grade 路额外加强：**全有或全无**（`any_fail` 任一列失败即整路 `None`）。因为出分 diff 比较的是"上次状态 vs 本次状态"，若只查到一半列，缺失列会被误读为"未变化"而非"未知"，可能漏报某列出分。整路置 `None` 后，该课 grade 在本 run 不参与 diff，下一次扫描全量补齐。

**`walk_contents`**：自顶层 `GET /courses/{cid}/contents?limit=100` 起，对 `hasChildren == true` 的节点递归 `GET /contents/{id}/children`，深度优先、走全局令牌桶限速；扁平化为 `list[ContentNode]`，每节点记 `id`、`title`、`contentHandler.id`、`created`、`modified`、`parent_id`、`path`（祖先 title 链，供下载器与清单展示复用）。**递归任一子节点拉取失败，则整棵树视为不完整、`contents` 置 `None`**（同 grade 路理由：避免子树缺失被当成"课件被删/未变化"）。

### 8.5 五类事件的 Diff 推导

所有 differ 与 store 旧快照按**稳定 id 全量比对**，不依赖"自上次扫描以来"的时间窗口。多日未扫后单次扫描即可补齐全部累积新项。所有 differ 是**纯函数**（输入新快照 + store 读旧快照，输出草稿），不写库。

#### 8.5.1 汇总列过滤（NewTask 的前置）

成绩册列里混有非作业的汇总列，必须先剔除，否则把 `Weighted Total` 当成"新作业"误报：

```python
def is_real_assignment_column(col: dict) -> bool:
    g = col.get("grading") or {}
    # 主判据：只有带 grading.due 的列才是有截止的作业/quiz
    if not g.get("due"):
        return False
    # 兜底1：计算型汇总列 grading.type == "Calculated"（Weighted Total/Total/Average）
    if g.get("type") == "Calculated":
        return False
    # 兜底2：名称黑名单（防个别手建汇总列也带了 due）
    if (col.get("name") or "").strip().lower() in {
        "weighted total", "total", "running total", "average"}:
        return False
    return True
```

主判据是 `grading.due` 必须存在——实测 `Weighted Total`/`Total` 无 `due`，自然落选；`type == "Calculated"` 与名称黑名单是冗余兜底。**适用范围**：`is_real_assignment_column` 只用于 NewTask/DeadlineSoon/逐列查 grade 的范围裁剪。老师为线下/纸质作业手建的"带 due 手工列"会被正常纳入（符合预期，靠清单页手动勾选完成）。

#### 8.5.2 NewTask —— 新成绩册列

去重锚 = column id（实例内稳定）。旧快照表 `known_column`。

```python
def diff_columns(cpk, fresh):                # fresh 已过 §8.5.1 过滤
    known = store.known_column_ids(cpk)      # set[str]
    out = []
    for col in fresh:
        if col["id"] not in known:
            out.append(EventDraft(
                type=EventType.NEW_TASK, course_pk=cpk,
                entity_id=col["id"], title=col["name"],
                due_utc=col["grading"]["due"],
                payload={"possible": (col.get("score") or {}).get("possible"),
                         "content_id": col.get("contentId")}))
    return out
```

- `contentId` 关联内容树里的作业项（题面 PDF / 在线提交），存下供清单页"打开作业"与下载器定位题面。
- **due 被改不另发 NewTask**：NewTask 只认 column id 首次出现。老师把已存在作业的 `due` 改了，不再发"新作业"（避免噪声）；ddl 变更由 DeadlineSoon 的 dedup_key 含 due 来正确再提醒（§8.5.6）。
- **首扫抑制不在此处判断**：diff 照常产出草稿，是否属基线、是否通知由 store 统一裁决（§8.6）。这样保证基线判定单点、可测。

#### 8.5.3 GradePosted —— 出分

去重锚 = column id（一列一次出分一条）。依据：per-user column `status` 实测 ∈ {`None`(未提交), `NeedsGrading`(已交待批), `Graded`(已批改)}，`score` 出分后非空。

```python
def diff_grades(cpk, fresh_status):          # fresh_status: dict[col_id -> UserGrade]
    out = []
    for col_id, ug in fresh_status.items():
        prev = store.prev_grade(cpk, col_id)             # 旧 (status, score) 或 None
        now_graded = (ug.status == "Graded") or (ug.score is not None)
        was_graded = bool(prev) and (
            prev.status == "Graded" or prev.score is not None)
        if now_graded and not was_graded:
            out.append(EventDraft(
                type=EventType.GRADE_POSTED, course_pk=cpk,
                entity_id=col_id, title=ug.column_name, due_utc=None,
                payload={"score": ug.score, "possible": ug.possible,
                         "status": ug.status}))
    return out
```

设计要点：
- **触发=`Graded` 或 `score` 非空的并集**，因为部分列只给分不改状态。
- **首次见到该列状态（`prev is None`）且已 Graded** → 归基线抑制，不补报历史分（§8.6）。
- **撤分/重判的处理**：若 store 已记录某列出分、本次又变回 `None`（教师撤分），diff 不发任何事件（GradePosted 只在"由未出分→出分"上升沿触发，去重锚已存在的事件被 `UNIQUE` 挡住）。后续若再次出分，因 dedup_key 已存在仍不重发——这是有意取舍：**一列只通知一次出分**。分数数值变化（改分）由清单/成绩跟踪页展示，不另发桌面通知，避免来回改分刷屏。
- diff_grades 的最新 `(status, score)` 在 store 事务里 upsert 到 `known_grade`，**双重用途**：既是下次出分 diff 的旧值，也是清单页"未交/待批/已批"三态与 DeadlineSoon 完成度判定的**唯一真相源**。

#### 8.5.4 NewAnnouncement —— 新公告

去重锚 = announcement id。`GET /courses/{cid}/announcements`，字段 `id/title/created/body`。

```python
def diff_announcements(cpk, fresh):
    known = store.known_announcement_ids(cpk)
    out = []
    for a in sorted(fresh, key=lambda x: x.get("created") or ""):
        if a["id"] not in known:
            out.append(EventDraft(
                type=EventType.NEW_ANNOUNCEMENT, course_pk=cpk,
                entity_id=a["id"], title=a["title"], due_utc=None,
                payload={"created": a.get("created"),
                         "body_excerpt": excerpt(a.get("body"), 500)}))
    return out
```

`body_excerpt`（去 HTML、截断）保留正文摘要，因为实测公告正文常含考试/补课/座位等关键信息（如`补课通知`、`期中座位表`、`提醒 Assignment 3`），值得进通知与清单。系统级 `/announcements` 本实例为空，不拉。

#### 8.5.5 NewMaterial —— 新课件 / 更新

去重锚 = content id；**版本指纹** = `modified`（用于"同 id 但被更新"的二次提醒）。

```python
def diff_contents(cpk, fresh):
    known = store.known_contents(cpk)        # dict[id -> modified_utc(str)]
    out = []
    for n in fresh:
        if n.handler == "resource/x-bb-folder":   # 文件夹不作为"课件上传"事件
            continue
        prev_mod = known.get(n.id)
        is_new     = prev_mod is None
        # 时间戳必须解析后比较，不能按字符串字典序比（时区/毫秒位数会骗过 >）
        is_updated = (prev_mod is not None
                      and parse_utc(n.modified) > parse_utc(prev_mod))
        if is_new or is_updated:
            out.append(EventDraft(
                type=EventType.NEW_MATERIAL, course_pk=cpk,
                entity_id=n.id, title=f"{n.path} / {n.title}", due_utc=None,
                payload={"handler": n.handler, "modified": n.modified,
                         "is_update": is_updated, "content_id": n.id}))
    return out
```

要点对齐实测：
- 内容类型来自 `contentHandler.id`：`resource/x-bb-folder`/`x-bb-document`/`x-bb-file`/`x-bb-assignment`。文件夹本身不报；其余三类视为"课件/作业项"。
- **`modified` 既做新建也做更新判定**：去重锚 `entity_id` 恒为 content id，但 dedup_key（§8.6）拼入 `modified`，于是"老师替换新版"因 `modified` 变化成为一条新去重行，二次提醒"（更新）"，与首次上传不重复。**`modified` 必须解析为时间戳再比较**——BB 返回的 ISO8601 毫秒位数与时区写法不固定，字符串字典序会误判（draft 用 `n.modified > prev_modified` 是 bug，本定稿改为 `parse_utc(...)` 比较）。
- 附件下载与镜像由 `engine.downloader` 负责。scanner 在 diff 阶段**不拉 attachments**（避免每个内容项一次额外请求拖慢扫描）；NewMaterial 事件携 `content_id`，下载器据此按需取 `attachments` 并跟随 302 下载。

#### 8.5.6 DeadlineSoon —— 临近未完成（纯本地推导）

DeadlineSoon **不打网**，完全由本地快照推导，因此与"本次是否扫到"解耦——即便某次扫描某门课失败，已知列的 ddl 仍会被提醒。这是缓解"不开 Claude Code 就不扫"缺口的核心。

```python
def derive_deadline_soon(uid, run_id) -> list[EventDraft]:
    cfg = config.load()
    now = datetime.now(timezone.utc)
    out = []
    for row in store.iter_known_columns_with_due():   # 跨所有在读课的带 due 列
        due = parse_utc(row.due_utc)
        if due is None:
            continue
        if store.is_done(row.course_pk, row.column_id):   # 已完成不催
            continue
        delta_h = (due - now).total_seconds() / 3600
        for window_h in sorted(cfg.deadline_windows):     # 如 [72, 24, 6]
            if 0 < delta_h <= window_h:
                out.append(EventDraft(
                    type=EventType.DEADLINE_SOON, course_pk=row.course_pk,
                    entity_id=row.column_id, title=row.column_name,
                    due_utc=row.due_utc,
                    payload={"window_h": window_h,
                             "hours_left": round(delta_h, 1),
                             "due_utc": row.due_utc}))   # 入 dedup_key，见 §8.6
                break          # 命中最紧的一档即止
    return out
```

要点：
- **完成判定唯一真相 = `store.is_done`**，综合 per-user column status（`NeedsGrading`/`Graded`/`score` 非空）与清单页**手动勾选**（线下作业）。已完成不催，杜绝"已交还提醒"。
- **多档窗口分别去重**：`window_h` 是 dedup_key 的一部分，72h/24h/6h 各提醒一次、互不重复，越临近越升级。
- **ddl 被改后重新计窗**：dedup_key 含 `due_utc`（§8.6）。老师改了截止时间，每档窗口对新 due 视为新去重行，会按新时间重新提醒，旧 due 的已发记录不复用——既不漏新 ddl，也不与旧 ddl 重复。
- **逾期处理**：`delta_h <= 0` 不再发 DeadlineSoon 事件，但清单页以"逾期标红"展示。
- **边界与时钟**：窗口判定用 UTC 比较，规避时区/夏令时；恰好等于窗口上界（`delta_h == window_h`）计入该档（闭区间），避免临界值在两次扫描间漏发。
- DeadlineSoon 是**唯一**随时间反复触发的事件类型，其余四类都是一次性 id 上升沿。把它做成纯本地、每次 scan 末尾统一求值，保证任何触发源都能补到点的临近提醒。

### 8.6 事件落库与去重（store 单事务裁决）

scanner 只产出 `EventDraft` 与 `CourseFetch`；**diff、快照 upsert、去重、基线抑制全在 store 的一个事务内完成**。表 DDL（`engine/store.py`，SQLite）：

```sql
-- 已知快照表（全量 diff 的"旧值"来源）
CREATE TABLE IF NOT EXISTS known_column (
  course_pk   TEXT NOT NULL,
  column_id   TEXT NOT NULL,
  name        TEXT,
  due_utc     TEXT,            -- grading.due, ISO8601 UTC, 可空
  possible    REAL,
  content_id  TEXT,
  first_run   INTEGER NOT NULL,
  last_run    INTEGER NOT NULL,
  PRIMARY KEY (course_pk, column_id)
);
CREATE TABLE IF NOT EXISTS known_grade (
  course_pk   TEXT NOT NULL,
  column_id   TEXT NOT NULL,
  status      TEXT,            -- None / NeedsGrading / Graded
  score       REAL,            -- 出分后非空
  done_manual INTEGER NOT NULL DEFAULT 0,   -- 清单页手动勾选完成（线下作业）
  last_run    INTEGER NOT NULL,
  PRIMARY KEY (course_pk, column_id)
);
CREATE TABLE IF NOT EXISTS known_announcement (
  course_pk   TEXT NOT NULL,
  ann_id      TEXT NOT NULL,
  title       TEXT,
  created_utc TEXT,
  first_run   INTEGER NOT NULL,
  PRIMARY KEY (course_pk, ann_id)
);
CREATE TABLE IF NOT EXISTS known_content (
  course_pk    TEXT NOT NULL,
  content_id   TEXT NOT NULL,
  title        TEXT,
  path         TEXT,
  handler      TEXT,            -- contentHandler.id
  modified_utc TEXT,            -- 版本指纹
  first_run    INTEGER NOT NULL,
  last_run     INTEGER NOT NULL,
  PRIMARY KEY (course_pk, content_id)
);
-- 课程基线标记（基线抑制的单一判据，先于任何快照写入而置位）
CREATE TABLE IF NOT EXISTS course_baseline (
  course_pk     TEXT PRIMARY KEY,
  baselined_run INTEGER NOT NULL
);
-- 事件去重表（通知/清单的唯一真相；UNIQUE 即幂等闸门）
CREATE TABLE IF NOT EXISTS event (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  type         TEXT NOT NULL,
  course_pk    TEXT NOT NULL,
  entity_id    TEXT NOT NULL,
  dedup_key    TEXT NOT NULL,
  title        TEXT,
  due_utc      TEXT,
  payload      TEXT,                    -- JSON
  observed_run INTEGER NOT NULL,
  created_at   TEXT NOT NULL,
  suppressed   INTEGER NOT NULL DEFAULT 0,   -- 基线抑制：落库但不通知
  notify_state TEXT NOT NULL DEFAULT 'pending',  -- pending/sent/failed（第9章）
  UNIQUE (dedup_key)
);
CREATE TABLE IF NOT EXISTS scan_run (
  run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  trigger     TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  status      TEXT,                    -- ok/partial/failed
  courses_ok  INTEGER, courses_failed INTEGER
);
CREATE TABLE IF NOT EXISTS scan_error (
  run_id    INTEGER NOT NULL,
  course_pk TEXT,
  tag       TEXT,                    -- columns/grade:.../ann/contents
  message   TEXT,                    -- 已脱敏，绝不含 cookie/token/密码
  at        TEXT NOT NULL
);
```

**dedup_key 构造**（"绝不重复"且"该重复触发的能再触发"）：

| 事件 | dedup_key |
|---|---|
| NewTask | `NewTask:{course_pk}:{column_id}` |
| GradePosted | `GradePosted:{course_pk}:{column_id}` |
| NewAnnouncement | `NewAnnouncement:{course_pk}:{ann_id}` |
| NewMaterial | `NewMaterial:{course_pk}:{content_id}:{modified_utc}` |
| DeadlineSoon | `DeadlineSoon:{course_pk}:{column_id}:{due_utc}:{window_h}` |

> 与 draft 的差异：DeadlineSoon 的 key 增列 `due_utc`，使"ddl 被改"能对每档窗口重新提醒而不与旧 ddl 重复（§8.5.6）。

**store 的单事务提交流程**（`commit_events` 与 upsert 合一，幂等）：

```python
def commit_events(fetches: list[CourseFetch], local_drafts, run_id) -> list[Event]:
    persisted = []
    with db:                                      # 单事务（BEGIN…COMMIT）
        for f in fetches:
            cpk = f.course_pk
            # 1) 先锁定"本课是否已建基线"——必须在写任何快照之前读，
            #    否则后面的 upsert 会让 is_baseline 永远为假，基线抑制失效（draft 的隐患）
            is_baseline = not _has_baseline(cpk)

            # 2) 对每路"非 None"的 fresh：先 diff（读旧快照）→ 收草稿 → 再 upsert 快照
            drafts = []
            if f.columns      is not None: drafts += diff_columns(cpk, f.columns)
            if f.grades       is not None: drafts += diff_grades(cpk, f.grades)
            if f.announcements is not None: drafts += diff_announcements(cpk, f.announcements)
            if f.contents     is not None: drafts += diff_contents(cpk, f.contents)

            for d in drafts:
                _insert_event(d, run_id, suppressed=is_baseline, out=persisted)

            if f.columns       is not None: _upsert_columns(cpk, f.columns, run_id)
            if f.grades        is not None: _upsert_grades(cpk, f.grades, run_id)
            if f.announcements is not None: _upsert_announcements(cpk, f.announcements, run_id)
            if f.contents      is not None: _upsert_contents(cpk, f.contents, run_id)

            # 3) 仅当四路均成功（无 None）才落基线标记——半截基线会漏抑制后续历史项
            if is_baseline and _all_paths_ok(f):
                _set_baseline(cpk, run_id)

        # 4) DeadlineSoon 等纯本地草稿照常走去重；它们落在已建基线的课上，
        #    suppressed 取该课基线态（首扫课的临近 ddl 不轰炸）
        for d in local_drafts:
            _insert_event(d, run_id,
                          suppressed=not _has_baseline(d.course_pk), out=persisted)
    return persisted          # 仅"rowcount==1 且 suppressed==0"的事件进 notifier 队列

def _insert_event(d, run_id, suppressed, out):
    cur = db.execute(
        "INSERT OR IGNORE INTO event(type,course_pk,entity_id,dedup_key,title,"
        "due_utc,payload,observed_run,created_at,suppressed) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (d.type, d.course_pk, d.entity_id, make_dedup_key(d), d.title,
         d.due_utc, json.dumps(d.payload), run_id, utcnow_iso(), int(suppressed)))
    if cur.rowcount == 1 and not suppressed:
        out.append(row_to_event(cur.lastrowid, d))
```

去重与基线的三条保证：
- **`INSERT OR IGNORE` + `UNIQUE(dedup_key)` 是去重闸门**：并发触发源同算出同一草稿，只有一个 `rowcount==1`，其余静默忽略。**绝不重复**由数据库约束兜底，不靠应用层判断。
- **基线抑制正确性（修正 draft 的顺序缺陷）**：`is_baseline` **必须在写任何快照前读**，且整门课的基线判定在该课处理周期内**冻结为一个值**。draft 把 `store.upsert_*` 散在 `scan_course` 里、与 diff 交错，会导致先 upsert 的路把后续 `is_baseline(course_pk)` 拉成假，使同一首扫里后处理的路漏抑制、炸出历史项。定稿改为"读基线 → 全课 diff → 全课 upsert → 视全路成功才落基线"。
- **基线只在四路全成功时落**（`_all_paths_ok`）。若首扫某路 `None`（没拉到），不落基线标记：下次扫描该课仍按基线处理，避免"半截基线"把另一半历史项当成真实新增通知出去。代价是首扫不全则历史项可能多抑制一轮，符合"宁可少通知一次历史项，也不误报"的取向。
- store 还派生清单页视图：未完成 = `known_column.due_utc` 非空且 `not is_done(course_pk, column_id)`，按 due 升序；逾期标红、临近高亮。

### 8.7 串/并行与限速取舍

约束：对 BB **温和**（复用会话、限速、尊重 `127.0.0.1:7890` 代理），同时 SessionStart **非阻塞**、整次扫描尽量快。

**决策：课级有界并发，课内串行；网络拉取（`fetch_course`）并发，store 写入串行单事务。**

- **课内串行**：四路按 columns→grades→announcements→contents 顺序，因 grades 依赖 columns 过滤结果（先过汇总列再逐列查），且课内串行天然限速。
- **课级并发**：17 门课用 `asyncio` + `Semaphore(N)`，**N=3**（保守，可配 `scan.concurrency`）。叠加**全局令牌桶**（默认 `scan.rate_qps=4`，跨所有协程每秒 ≤4 请求）。**令牌桶优先于并发数生效**——并发只为填满 RTT 空隙，不为压站提速。
- **store 写入不并发**：`fetch_course` 可并发（纯网络/CPU），但 §8.6 的 diff+upsert+落库**在主协程里串行、单事务**完成，规避 SQLite 写竞争，并保证基线判定的原子性。
- **重试/退避**：429/5xx 走指数退避（`base=1s, factor=2, max=30s, retries=3`），退避期间让出令牌给其他协程；重试耗尽 → 该路 `guarded` 返回 `None`（不漏防线接管）。
- **传输层**：所有请求经 `curl_cffi`（浏览器 TLS 指纹）会话，失败回退子进程 `curl`；会话级 cookie 复用，**绝不每请求重登**（对齐实测：原生 requests 直连 ADFS `UNEXPECTED_EOF`，curl 正常）。
- **翻页**：`paging` 字段**仅在有下一页时出现**（含 `nextPage`）；翻页循环以"`nextPage` 是否存在"为终止条件，不假设固定结构。日历类查询窗口 ≤16 周，须按窗口分段覆盖整学期——但 §8.5 的 per-course columns 已能拿到全部带 due 的列（且含 `contentId/status`），**日历不在主扫描路径**，仅作可选的跨课交叉校验增强（实测日历项全为 `GradebookColumn` 类型，与 per-course columns 同源）。

并发编排骨架：

```python
async def scan_courses(session, uid, courses, run_id) -> list[EventDraft]:
    sem    = asyncio.Semaphore(config.scan.concurrency)   # 默认 3
    bucket = TokenBucket(qps=config.scan.rate_qps)        # 默认 4，全局共享
    async def one(course):
        async with sem:
            return await asyncio.to_thread(
                fetch_course, session, uid, course, run_id)   # 内部走 bucket
    results = await asyncio.gather(*[one(c) for c in courses],
                                   return_exceptions=True)
    fetches = []
    for c, r in zip(courses, results):
        if isinstance(r, Exception):
            store.record_course_failure(run_id, c.course_pk, r)   # 隔离，不中断全局
        else:
            fetches.append(r)
    return fetches    # 交 store.commit_events 在主协程串行 diff+落库（§8.6）
```

**非阻塞**：SessionStart 钩子 `bbwatch session-start` fork 出扫描进程后立即返回，仅把"**上一次**已落库的待办摘要"经 `additionalContext` 注入会话（读 DB，不等本次网络）；本次扫描完成后由 notifier 弹通知、刷新清单页，不阻塞 Claude Code 启动。`scan.concurrency` 与 `scan.rate_qps` 调小即可在"快"与"温和"间滑动，默认偏温和。

### 8.8 与 robustness 状态机的对齐点

第 9 章状态机与本章的耦合面收敛为三处，避免职责重叠：

1. **基线态（BASELINE）**：课程首次纳入 → 该课四路全成功后写 `course_baseline`，首扫事件 `suppressed=1` 落库建基线。此后该 `course_pk` 的新增事件才允许 `suppressed=0` 并转 `pending`。
2. **通知态（notify_state: pending→sent/failed）**：`commit_events` 产出 `pending`；notifier 成功置 `sent`、失败置 `failed` 并保留重投。状态机保证"发过的不重发、发失败的下次补发"。scanner/store 的 diff 路径不碰 `notify_state`。
3. **完成态（done）**：由 per-user column status 自动推进（None→NeedsGrading→Graded）或清单页手动勾选写 `done_manual`；DeadlineSoon 与清单"未完成"视图都只读 `store.is_done`，状态机是其唯一写入方。

由此，**全量 id diff（不漏）** 与 **`UNIQUE(dedup_key)` + 先读基线后写快照 + notify_state（不重）** 共同满足全局最高优先约束。本章相对 draft 的实质强化集中在四点：**(i)** 拆分"只读拉取"与"事务内 diff+落库"，根除 upsert 与 diff 交错导致的基线失效与同-run 漏报；**(ii)** `None`/半截路一律不参与 diff、不收敛快照，把网络抖动隔离在"不漏"防线外；**(iii)** DeadlineSoon dedup_key 纳入 `due_utc`、NewMaterial 的 `modified` 改为解析后时间戳比较，修正 ddl 改期与版本比较两个误判；**(iv)** 基线标记仅在四路全成功时落，杜绝半截基线漏抑制。scanner 保持无状态纯编排，所有持久判定下沉 store/状态机。

---

涉及的关键文件路径：`engine/scanner.py`（本章主体：`fetch_course` + 五个纯函数 differ + `derive_deadline_soon`）、`engine/store.py`（上列 DDL 与单事务 `commit_events`：diff+upsert+去重+基线）、`engine/bbclient.py`（`get_columns/get_grade/list_announcements/walk_contents` 等带翻页/限速/退避的封装）、`engine/auth.py`（`get_session`）。

---

定稿已完成。相对初稿的实质性修改（按"绝不漏/绝不重复"硬需求排序）：

1. **修复基线抑制的顺序 bug**（最严重）：初稿在 `scan_course` 里把 `store.upsert_*` 与 diff 交错执行，`is_baseline(course_pk)` 在首扫中途就被先 upsert 的路拉假，导致同一首扫里后处理的路漏抑制、炸出历史项。定稿拆为"只读拉取（`fetch_course`）→ store 单事务内先读基线、再全课 diff、再全课 upsert、四路全成功才落基线"。
2. **`None`/半截路一律不参与 diff、不收敛快照**：grade 路改为"全有或全无"，内容树递归任一子节点失败则整树置 `None`，把网络抖动彻底隔离在"不漏"防线外。
3. **DeadlineSoon dedup_key 增列 `due_utc`**：修正初稿中"老师改 ddl 后无法对新截止时间重新提醒"的漏报。
4. **NewMaterial 的 `modified` 比较改为 `parse_utc()` 后时间戳比较**：初稿的 `n.modified > prev_modified` 字符串字典序会因时区/毫秒位数误判更新。
5. 澄清 `me` 别名"在 `users/me` 可用、在 `/courses` 子资源不可用"（初稿表述含糊）；明确 GradePosted/撤分重判的一次性语义；补全时钟/窗口边界、基线半截不落标记等。

无需写出的文件已全部内联在上面的定稿正文中。设计文档原文件位于 `/Users/mac/Programming/cuhkszbb/docs/superpowers/specs/2026-06-28-bbwatch-design.md`（本次仅产出第 8 章定稿文本，未改动磁盘文件）。

---

## 9. Claude Code 集成

本章是 bbwatch「插件外壳」的全部实现细节。前提:所有难度与价值都在 `engine/` Python 包(`engine.auth/bbclient/store/scanner/downloader/notifier`),本章的 hooks、commands、skill、MCP server、dashboard 全是**薄壳**,只负责「在合适时机以合适参数调用引擎,并把结果以 Claude Code 能消费的形态(additionalContext / stdout / MCP 返回)交回」。所有壳层调用引擎都走同一个 CLI 入口 `bbwatch`(见 §9.2),保证幂等去重逻辑只有一份、落在 `engine.store` 的同一个 SQLite。**「绝不漏 / 绝不重复」不是靠某一条触发路径小心翼翼实现的,而是靠下面三条物理不变量**(贯穿全章,§9.7 给出落地):

- **不变量 A(全量 diff,不漏):** 每次扫描都用稳定 id(column / announcement / content / attachment id)与本地已知集合做**全集 diff**,而非时间窗口增量。多天没扫,下次也能把累计新项一次补齐。
- **不变量 B(已知即闭嘴,不重复):** 任一 id 一旦进入「已通知」状态就打 `notified_* = 1`;通知只在 `notified_* = 0` 时发出并随即置 1,**在同一事务内**完成,与触发源/次数/并发无关。
- **不变量 C(全成功才推进水位,不漏):** `meta.last_scan_utc` 只在一次扫描**完整成功**后才更新;任何课程抓取失败 → 本轮不推进水位、不缩短下次 once 窗口、不丢已成功课程的 diff 结果。

### 9.1 插件目录布局与 `${CLAUDE_PLUGIN_ROOT}`

Claude Code 加载插件时,会把插件根目录的绝对路径注入环境变量 `${CLAUDE_PLUGIN_ROOT}`。本插件**所有**对引擎脚本、配置文件、wrapper 的引用一律以它为根,绝不写死用户名或绝对路径(否则分发给同学即坏)。

```
bbwatch/                                  ← 插件根 = ${CLAUDE_PLUGIN_ROOT}
├─ .claude-plugin/
│  └─ plugin.json                         ← 插件清单(§9.2)
├─ hooks/
│  └─ hooks.json                          ← SessionStart 钩子定义(§9.3)
├─ commands/
│  ├─ bb-setup.md                         ← /bb-setup(§9.4)
│  ├─ bb-scan.md                          ← /bb-scan
│  ├─ bb-dashboard.md                     ← /bb-dashboard
│  └─ bb-download.md                      ← /bb-download
├─ skills/
│  └─ bb-watch/
│     └─ SKILL.md                         ← 教 Claude 何时调 MCP 工具(§9.5)
├─ mcp/
│  └─ server.py                           ← FastMCP server(§9.6),被 .mcp.json 拉起
├─ .mcp.json                              ← MCP server 声明(§9.6)
├─ bin/
│  ├─ bbwatch                             ← CLI wrapper(§9.2.2),hooks/commands 只调它
│  └─ bbwatch-mcp                         ← MCP wrapper(§9.6),同构,exec server.py
└─ engine/                                ← Python 引擎包(本章不展开,见 §3/§5)
   └─ ...
```

设计约束:hooks.json、commands/*.md、.mcp.json 里**任何**对脚本的引用都写成 `${CLAUDE_PLUGIN_ROOT}/bin/bbwatch ...` 或 `${CLAUDE_PLUGIN_ROOT}/bin/bbwatch-mcp`。Claude Code 在执行 hook 命令、启动 MCP server 前会对这些字符串做变量展开(`.mcp.json` 的 `command`/`args`/`env` 值同样展开)。

### 9.2 插件清单 `plugin.json` 与统一 CLI 入口

#### 9.2.1 `.claude-plugin/plugin.json`

```json
{
  "name": "bbwatch",
  "version": "0.1.0",
  "description": "CUHK-SZ Blackboard 作业/公告/课件/出分监控与课件下载。开 Claude Code 即扫描,注入今日待办,维护本地任务清单。",
  "author": { "name": "bbwatch" },
  "homepage": "https://github.com/<owner>/bbwatch",
  "license": "MIT",
  "keywords": ["blackboard", "cuhk-sz", "homework", "deadline", "courseware"],
  "commands": "./commands",
  "hooks": "./hooks/hooks.json",
  "mcpServers": "./.mcp.json",
  "skills": "./skills"
}
```

字段语义:

| 字段 | 取值 | 说明 |
|---|---|---|
| `name` | `bbwatch` | 插件唯一标识;命令前缀、MCP 工具命名空间均由它派生。必须是 kebab-case。 |
| `version` | semver | 市场更新比对依据。 |
| `description` | 一句话 | 在 `/plugin` 列表与市场里展示。 |
| `commands` | `./commands` | 斜杠命令目录(相对插件根)。目录内每个 `.md` = 一个命令。省略时默认即 `commands/`,这里显式写出以求自文档化。 |
| `hooks` | `./hooks/hooks.json` | 钩子清单路径。 |
| `mcpServers` | `./.mcp.json` | MCP server 声明文件路径。也可内联对象;这里指向独立文件便于单测。 |
| `skills` | `./skills` | skill 目录;每个子目录含一个 `SKILL.md`。 |

注:`commands`/`hooks`/`skills` 都有默认约定目录,清单里其实可省略;本插件全部显式声明,使「外壳由哪些部分构成」对评审者一目了然。`author.email` 刻意留空——清单会随插件分发,不把个人邮箱写进可公开仓库。

#### 9.2.2 统一 CLI wrapper `bin/bbwatch`

壳层不直接 `python -m engine...`,而是统一调 `${CLAUDE_PLUGIN_ROOT}/bin/bbwatch`。理由:(1) 解决 anaconda Python 与系统 Python 混用导致 `curl_cffi`/`keyring` 找不到的问题——wrapper 内部锁定解释器,**绝不落到 anaconda**(实测 anaconda 的 requests 直连 ADFS 会 TLS 握手失败);(2) 给所有触发源(hook/command/mcp)一个稳定签名;(3) 把「找解释器」「设 PYTHONPATH」「尊重代理」收口到一处。

```bash
#!/usr/bin/env bash
# ${CLAUDE_PLUGIN_ROOT}/bin/bbwatch — 引擎统一入口
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # = 插件根
# 解释器解析优先级:用户显式配置 > 插件自带 venv > python3。绝不假设 anaconda。
PY="${BBWATCH_PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
# 尊重本机代理(实测 Clash 127.0.0.1:7890);用户已设则不覆盖。
export HTTPS_PROXY="${HTTPS_PROXY:-${https_proxy:-http://127.0.0.1:7890}}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost}"   # 清单页/本机回环不走代理
exec "$PY" -m engine.cli "$@"
```

`engine.cli` 暴露的子命令(壳层只用到这几个,签名稳定):

| 子命令 | 用途 | 关键参数 | stdout |
|---|---|---|---|
| `bbwatch setup` | 录入凭据→钥匙串、自检、写配置 | `--user <学号>` | 人类可读进度 |
| `bbwatch session-start` | SessionStart 专用:detach 后台扫 + 起清单页 + 打印注入摘要 | `--once-window <min>`(默认 60) | **仅** additionalContext 摘要文本(诊断走 stderr) |
| `bbwatch scan` | 扫描一次(全量 diff) | `--source <hook\|manual\|mcp\|periodic> [--force] [--json]` | 事件列表 |
| `bbwatch tasks` | 列任务 | `--filter <...> [--course <id>] [--json]` | 任务清单 |
| `bbwatch download` | 增量镜像课件 | `--course <id\|keyword> --dest <path> [--incremental]` | 下载摘要 |
| `bbwatch done` | 标记完成 | `--task <taskKey>` | 确认 |
| `bbwatch dashboard` | 确保清单页起、打印 URL | `[--open]` | URL |
| `bbwatch courses` | 列在读课程 | `[--json]` | 课程列表 |

所有写操作经由 `engine.store` 落同一 SQLite(路径 `~/.local/state/bbwatch/bbwatch.db`,见 §9.7 DDL),并以 **WAL 模式 + `busy_timeout`** 串行化三条触发路径的并发写,天然跨触发源幂等。

### 9.3 SessionStart 钩子

#### 9.3.1 目标与硬约束

打开 Claude Code 即:① detach 一个后台扫描;② 确保本地清单页已起;③ 把「未来 7 天 ddl / 自上次以来的新变化」摘要通过 `additionalContext` 注入会话,使 Claude 一开口就能报 ddl。

硬约束:
- **非阻塞:** 扫描可能耗时数秒到十几秒(17 门在读课、翻页、16 周日历窗口分段、ADFS 可能重登,均超过 hook 的 8s 上限)。真正抓取必须 detach 出去,hook 进程秒级返回。
- **once 语义:** 同机短时间内开多个窗口 / 频繁重启,不应每次全量抓取、更不应重复弹通知。用「最近 N 分钟内已成功扫过(`fresh`)则跳过抓取,只复述当前 DB 摘要」实现;**fresh 的判据是 `meta.last_scan_utc`(只在全成功后更新,不变量 C),因此一次失败的扫描不会冒充 fresh 把下次真扫吃掉**。
- **永不污染:** hook 的 stderr / 非零退出码不能打断会话启动(`session-start` 子命令在任何分支都 `exit 0`)。凭据缺失给温和引导(去 `/bb-setup`)而非报错刷屏。

#### 9.3.2 `hooks/hooks.json`

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/bin/bbwatch session-start --once-window 60",
            "timeout": 8
          }
        ]
      }
    ]
  }
}
```

- `matcher` 限定 `startup`(全新会话)与 `resume`(恢复会话);不挂 `clear`,避免清屏即重扫。
- `timeout: 8`(秒)是 hook 进程本身的硬上限。**真正的网络扫描不在这 8 秒里**——见下「非阻塞」实现。这 8 秒只够 wrapper:判断 once 窗口、读出 DB 当前摘要、把后台扫描 detach 出去、打印 additionalContext。
- SessionStart hook 的 stdout 被 Claude Code 当作 `additionalContext` 注入会话上下文。所以 `session-start` **只往 stdout 写那段摘要**,所有诊断走 stderr。

#### 9.3.3 `bbwatch session-start` 内部逻辑(伪代码)

```python
# engine/cli.py :: cmd_session_start(once_window_min: int)
def cmd_session_start(once_window_min=60) -> None:
    try:
        cfg = load_config()
        if not credentials_present():          # 钥匙串里没密码
            print(ONBOARD_HINT)                # "尚未配置 bbwatch,运行 /bb-setup 录入学号密码"
            return                             # exit 0,绝不打断会话

        ensure_dashboard_running()             # 幂等:已在跑则跳过;否则后台拉起 127.0.0.1 清单服务

        last = store.last_scan_at()            # = meta.last_scan_utc,只反映"全成功"扫描;UTC or None
        fresh = last and (now_utc() - last) < timedelta(minutes=once_window_min)

        if not fresh:
            # —— 非阻塞核心:抓取 detach 成独立会话,本进程不等它 ——
            spawn_detached([BBWATCH, "scan", "--source", "hook", "--quiet"])
            # detached 进程:确保会话→全量 diff→写 DB→对"真新事件"弹 macOS 通知→
            #               全成功才更新 last_scan→刷新清单页/注入摘要缓存。

        # 立刻(不等扫描)用"当前已知 DB 状态"产出注入摘要:
        summary = store.build_injection_summary(
            horizon_days=7,                          # 未来 7 天 ddl
            stale_warn_hours=cfg.stale_warn_hours,   # 距上次成功扫太久则提示(缓解"不开就不扫")
        )
        print(render_additional_context(summary, last_scan=last, scanning=not fresh))
    except Exception:                          # 任何异常都不得污染会话
        log_to_stderr_only()                   # 不进 stdout、不带凭据
    # 无论如何 exit 0
```

要点:
- **once 语义**由 `fresh` 决定是否真发起抓取;无论 fresh 与否,**总是**立即打印基于当前 DB 的摘要——多开窗口不会延迟、不会重复抓取/重复通知。
- **detach** 用 `subprocess.Popen(..., start_new_session=True, stdin/out/err=DEVNULL, close_fds=True)`(或 `os.posix_spawn`),父进程立即返回,hook 在 1s 内结束,不吃满 8s。
- **去重通知** 全部由 detached `scan` 负责(§9.3 不变量 B):只对「id 不在已知集合」的项 `notify` 并在**同一事务**里置 `notified_* = 1`,故同一新作业只通知一次,与触发源无关。
- **并发自保:** detached `scan` 先取一把进程级文件锁(`flock` on `~/.local/state/bbwatch/scan.lock`,非阻塞)。已有扫描在跑则本次直接退出——避免两个窗口同时开导致双抓取/双通知竞态(SQLite 事务是最后防线,文件锁是第一道)。

#### 9.3.4 注入摘要的形态(stdout 样例)

`render_additional_context` 产出的纯文本(给模型看,不做终端美化):

```
[bbwatch] 已知 BB 状态(上次成功扫描 2026-06-28 14:02 CST,正在后台重新扫描)
未来 7 天截止(东八区):
  • 06-30 23:59  MAT3007:Optimization  Homework 4     ← 未提交
  • 07-02 23:59  MAT3350:Information Theory  Problem Set 6  ← 未提交
待批/已交(无需再做):MAT3007 HW2(待批)
近期新公告 2 条:MAT3007「补课通知」、MAT3350「期中座位表」
本地任务清单:http://127.0.0.1:8765/  (可勾选完成)
提示:若用户问作业/ddl/成绩,先调 bbwatch MCP 工具 list_tasks 取实时数据再答,勿凭记忆。
```

时间一律由 UTC(`grading.due` 形如 `2026-06-30T15:59:00.000Z`)转东八区(+8)展示。「未提交 / 待批 / 已批」状态来自 per-user column `status`(`None` / `NeedsGrading` / `Graded`),保证不把已交的再列为待办。当 `now − last_scan > stale_warn_hours` 时,摘要头部追加一行醒目提示(例如「上次成功扫描已是 3 天前,可能漏了新 ddl,建议 /bb-scan」),缓解「不开 Claude Code 就不扫」的固有缺口。

### 9.4 斜杠命令

命令文件即 Markdown,正文是给 Claude 的指令(prompt);frontmatter 的 `allowed-tools` 限定它能跑哪些 Bash,以 `!` 开头的行在命令展开时执行并把 stdout 注入上下文。本插件命令统一让 Claude 调 `${CLAUDE_PLUGIN_ROOT}/bin/bbwatch ...` 并解读输出。

#### 9.4.1 `/bb-setup` — `commands/bb-setup.md`

唯一会触碰密码的入口。密码**不**作为命令参数(会进 shell 历史/日志),而是交互式读入并直接写钥匙串。

```markdown
---
description: 录入 CUHK-SZ 学号与密码到 macOS 钥匙串,并配置当前学期与课程过滤
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/bbwatch setup:*)
argument-hint: "[学号]"
---
运行交互式配置向导,把 BB 凭据安全写入 macOS 钥匙串(绝不落盘明文、绝不进日志)。

执行:
!`${CLAUDE_PLUGIN_ROOT}/bin/bbwatch setup --user "$1"`

向导会:
1. 在终端**隐式**读入密码(getpass,不回显、不入参、不入历史),经 `keyring` 写入 service="bbwatch"。
2. 立即做一次 ADFS OAuth2 登录自检(client_id=4b71b947-…,无 MFA),失败则提示重输,不保存坏凭据。
3. 用 `GET users/me` 取真实 uid(形如 `_49765_1`)并缓存——后续 `users/{uid}/courses` 必须用真实 uid(`me` 别名在该子资源不可用)。
4. 用 `GET terms` 选当前学期、列出在读课程(实测 17 门),询问黑/白名单。
完成后告知:现在可以 /bb-scan,或直接重开会话让 SessionStart 自动扫。
```

`bbwatch setup` 内部:`getpass.getpass()` 读密码 → `keyring.set_password("bbwatch", user, pw)` → 触发一次 `engine.auth.get_session()` 自检 → `GET users/me` 取真实 uid → 写 `~/.config/bbwatch/config.toml`(仅 uid、termId、名单、频率;**绝无密码**)。注:`me` 别名仅在 `users/me` 顶层资源可用,在 `users/{uid}/courses` 子资源上**不可用**,故必须把真实 uid 缓存下来。

#### 9.4.2 `/bb-scan` — `commands/bb-scan.md`

```markdown
---
description: 立即扫描 BB,报告新作业/新公告/新课件/新出分,并刷新本地任务清单
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/bbwatch scan:*)
---
立刻手动扫描一次 Blackboard。

执行:
!`${CLAUDE_PLUGIN_ROOT}/bin/bbwatch scan --source manual --force --json`

把返回的 JSON 事件按四类(new_assignment / new_announcement / new_content / new_grade)归并,
用东八区时间播报;带 ddl 的新作业按截止时间升序。这是与本地 SQLite 已知集合的**全量 diff**,
即使多天没扫也会把累计的新项一次补齐;已通知过的不会重复出现。手动扫带 --force,无视 once 窗口。
```

#### 9.4.3 `/bb-dashboard` — `commands/bb-dashboard.md`

```markdown
---
description: 打开本地任务清单网页(127.0.0.1),未完成按 ddl 排序、可勾选完成
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/bbwatch dashboard:*)
---
确保本地清单服务在跑,并在浏览器打开。

执行:
!`${CLAUDE_PLUGIN_ROOT}/bin/bbwatch dashboard --open`

把打印出的 URL(默认 http://127.0.0.1:8765/)告诉用户。该页仅绑 127.0.0.1。
```

#### 9.4.4 `/bb-download` — `commands/bb-download.md`

```markdown
---
description: 增量镜像某门课(或全部在读课)的课件到本地,保留文件夹结构
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/bbwatch download:*, ${CLAUDE_PLUGIN_ROOT}/bin/bbwatch courses:*)
argument-hint: "[课程关键字] [目标目录]"
---
下载课件。用户给的课程关键字(如 MAT3007)先用 `bbwatch courses --json` 解析成 course.id(形如 _17236_1)。

执行(示例):
!`${CLAUDE_PLUGIN_ROOT}/bin/bbwatch download --course "$1" --dest "${2:-$HOME/bb-courseware}" --incremental`

增量规则:按 content id + modified 时间戳跳过未变文件;附件下载走 302 跟随到真实文件。
完成后报告:新增/更新/跳过 各几个文件,落地路径。
```

### 9.5 Skill:教 Claude 何时调 MCP 工具

skill 不含可执行逻辑,只是一段「触发条件 + 工具选择」的指南,让 Claude 在自然对话里(用户没敲斜杠命令时)也知道该调哪个 MCP 工具,而不是凭记忆瞎答或去爬网页。

`skills/bb-watch/SKILL.md`:

```markdown
---
name: bb-watch
description: >
  当用户询问 Blackboard / BB / 港中深课程的作业、deadline、ddl、还有什么没交、
  成绩出了没、有没有新公告、要下载课件/slides/讲义时使用。优先调用 bbwatch 的
  MCP 工具拿实时数据,不要凭记忆回答,也不要自己去爬网页。
---

# bb-watch:何时以及如何使用 bbwatch 工具

bbwatch 把 CUHK-SZ Blackboard 的状态镜像在本地 SQLite,并通过 MCP 暴露只读/操作工具。
数据权威来源是 BB 官方 REST API(成绩册栏目=作业、per-user status=完成态、公告、内容树)。

### 触发与工具映射
- 「我还有什么作业 / 最近的 ddl / 这周要交什么」
  → `list_tasks`(filter="open" 或 "due_7d"),按截止时间升序播报,时间转东八区。
  若注入摘要显示数据偏旧,先 `scan_now` 再 `list_tasks`。
- 「成绩出了吗 / 我 HW2 多少分」
  → `list_tasks`(filter="graded"),或在结果里找对应 column 的 score。
- 「刷新一下 / 现在扫一遍 / 看看有没有新东西」
  → `scan_now(force=true)`,然后总结返回的四类事件(新作业/新公告/新课件/新出分)。
- 「把 MAT3007 的课件下下来 / 下载这门课的 slides」
  → 先 `list_courses` 把课程名解析成 course id,再 `download_course`(incremental=true)。
- 「我这个作业做完了 / 标记 X 已完成」(尤指线下/纸质作业,BB 无法自动判定)
  → `mark_done`(task=对应 task_key)。
- 「看清单 / 打开任务页」→ `open_dashboard`。

### 注意
- 所有截止时间从 BB 取的是 UTC,展示务必 +8 转东八区。
- 「未提交/待批/已批」对应 status None/NeedsGrading/Graded;已交的别再当待办催。
- 工具都是幂等的:重复 scan 不会重复通知;mark_done 重复调用结果一致。
- 不要在对话里要用户的 BB 密码;配置凭据只能走 /bb-setup。
```

### 9.6 MCP Server(工具清单)

#### 9.6.1 声明 `.mcp.json`

```json
{
  "mcpServers": {
    "bbwatch": {
      "command": "${CLAUDE_PLUGIN_ROOT}/bin/bbwatch-mcp",
      "args": [],
      "env": { "BBWATCH_ROOT": "${CLAUDE_PLUGIN_ROOT}" }
    }
  }
}
```

`bin/bbwatch-mcp` 与 `bin/bbwatch` 同构(同样的解释器解析 / PYTHONPATH / 代理 / NO_PROXY 处理),最后 `exec "$PY" "$ROOT/mcp/server.py"`。server 用 FastMCP(Python MCP SDK),**启动时不登录、不抓取**——所有工具都是「调用进程内 `engine.*` 函数」的薄封装,与 CLI 共用同一 `engine.store`(同一 SQLite,WAL + busy_timeout),因此 hook/command/MCP 三条触发路径完全幂等共享去重标记。

#### 9.6.2 工具总表

| 工具 | 入参 | 出参 | 语义 | 幂等性 |
|---|---|---|---|---|
| `scan_now` | `force: bool=false` | `{events: Event[], scanned_at, courses_scanned, courses_failed}` | 触发一次全量 diff 扫描(全部在读课:gradebook columns / announcements / contents / per-user status)。`force=false` 且距上次**成功**扫描 < once 窗口则直接返回上次结果不再抓取。 | 幂等:重复调用只在有真·新 id 时才产 event 并通知一次;已 `notified` 的不再出现。 |
| `list_tasks` | `filter: enum`, `course: str?` | `Task[]` | 从本地 DB 读任务,不触网。 | 只读。 |
| `list_courses` | `only_active: bool=true` | `Course[]` | 列课程(本学期在读 = role=Student 且 availability ∈ {Yes, Term})。 | 只读。 |
| `download_course` | `course: str`, `dest: str?`, `incremental: bool=true` | `{downloaded, updated, skipped, dest_root, files: []}` | 递归内容树,按 content id + modified 增量镜像;附件走 302 跟随。 | 幂等:`incremental=true` 下未变文件 skip;重复调用不重复下载。 |
| `mark_done` | `task: str` | `{task, status:"done"}` | 把无法自动判定的任务(线下/纸质)在 DB 标完成。 | 幂等:重复标记结果一致;扫描器尊重该手动状态不覆盖。 |
| `open_dashboard` | `open_browser: bool=true` | `{url}` | 确保清单服务在跑,返回 URL;`open_browser` 时 `open` 系统浏览器。 | 幂等:已在跑则复用,不重复起进程。 |

#### 9.6.3 各工具签名、出参字段与实现要点

**`scan_now`**
```python
@mcp.tool()
def scan_now(force: bool = False) -> dict:
    """扫描 BB 一次,返回自上次以来的新事件(四类)。"""
    result = engine.scanner.scan(source="mcp", force=force)   # -> ScanResult
    return {
        "scanned_at": iso8601_utc(store.last_scan_at()),
        "courses_scanned": result.ok_count,
        "courses_failed":  result.failed_labels,   # 非空 = 本轮未推进 last_scan(见不变量 C)
        "events": [e.as_dict() for e in result.events],
    }
```
`Event` schema(`scanner.scan` 产物,也是 `/bb-scan` 的 `--json` 输出):
```json
{
  "type": "new_assignment | new_announcement | new_content | new_grade",
  "course_id": "_17236_1",
  "course_label": "MAT3007:Optimization_L01",
  "ref_id": "_columnId_ / _announcementId_ / _contentId_",
  "title": "Homework 4",
  "due_utc": "2026-06-30T15:59:00.000Z",   // 仅 new_assignment 有
  "due_local": "2026-06-30 23:59 CST",
  "score": null,                            // 仅 new_grade:出分值
  "notified": true
}
```
四类事件来源:`new_assignment` = 新出现的 gradebook column id(带 `grading.due`,**过滤掉 Weighted Total / Total 等无 due 的汇总列**);`new_announcement` = 新 announcement id;`new_content` = 新内容项 id 或 `modified` 变化;`new_grade` = per-user column status 变 `Graded` 或 score 由空变非空。全部基于稳定 id 与 DB 已知集合做**全量 diff**(非时间窗口,不变量 A),多天没扫也不漏。

**冗余安全网(双源校验,进一步保「不漏」):** 带 ddl 的作业有两条独立发现路径——per-course `gradebook/columns` 与跨课程 `calendars/items`。日历窗口必须 **≤16 周**(超出报 400),`scanner` 按 16 周分段翻页覆盖整学期,并把日历返回的 `GradebookColumn` 项与逐课 columns 做并集 diff。任一路径单独遗漏(如某课 columns 接口偶发异常),另一路径仍能兜底检出该 column id。

**部分失败处理(不变量 C 的落地):** `scan` 对每门课独立 try/except,单课失败只把该课记入 `failed_labels` 并保留其上次快照(不删不改),其余课的成功 diff 照常写库与通知。**只有 `failed_labels` 为空(全成功)才更新 `meta.last_scan_utc`**;否则下次 SessionStart 因 `fresh=false` 会再扫一遍补齐失败课——失败永不被 once 窗口吞掉。

**`list_tasks`**
```python
@mcp.tool()
def list_tasks(filter: str = "open", course: str | None = None) -> list[dict]:
    """从本地 DB 列任务。filter ∈ {open,due_7d,overdue,graded,needs_grading,all}。"""
```
`filter`:`open`(未完成 = status `None` 且未手动 done,按 due 升序)、`due_7d`(未来 7 天截止的未完成)、`overdue`(已过 due 且未完成)、`graded`(已出分)、`needs_grading`(已交待批)、`all`。`Task` 出参:
```json
{
  "task_key": "_17236_1:_2891_1",      // course.id + column id,稳定主键
  "course_label": "MAT3007:Optimization_L01",
  "title": "Homework 4",
  "due_utc": "2026-06-30T15:59:00.000Z",
  "due_local": "2026-06-30 23:59 CST",
  "bb_status": "None | NeedsGrading | Graded",
  "manual_done": false,
  "score": null,
  "score_possible": 100,
  "is_overdue": false
}
```

**`list_courses`**
```python
@mcp.tool()
def list_courses(only_active: bool = True) -> list[dict]:
    """列课程。only_active=True 只返回本学期在读。"""
```
出参 `{course_id:"_17236_1", course_label:"MAT3007:Optimization_L01", term_id:"2550UG", term_name, availability:"Yes|Term", role:"Student"}`。`course_id` 即 REST 里的 `course.id`,供 `download_course` 使用。在读判据 = role=Student 且 availability ∈ {Yes, Term}(实测 19 门 membership 跨 3 学期,过滤后 17 门在读)。

**`download_course`**
```python
@mcp.tool()
def download_course(course: str, dest: str | None = None,
                    incremental: bool = True) -> dict:
    """增量镜像课件。course 可传 course.id 或可读名(内部用 list_courses 解析)。"""
```
内部走 `engine.downloader.mirror(course_id, dest_root, incremental)`:递归 `contents` / `children`,对每个附件按 `content_id + modified` 比对 DB `content` 表的 `downloaded_mod`,新增或变了才 `attachments/{aid}/download`(302 跟随)。`dest` 缺省 `~/bb-courseware/<course_label>/<原文件夹结构>`。返回各计数与落地路径列表。

**`mark_done`**
```python
@mcp.tool()
def mark_done(task: str) -> dict:
    """把任务(task_key)在 DB 标为手动完成。用于 BB 无法自动判定的线下作业。"""
```
仅写 `task.manual_done=1, manual_done_at=now`。扫描器在 diff 时**尊重** `manual_done`,不会因 BB 仍是 `None` 而把它重新当待办或再次通知。

**`open_dashboard`**
```python
@mcp.tool()
def open_dashboard(open_browser: bool = True) -> dict:
    """确保 127.0.0.1 清单服务在跑并返回 URL。"""
```
调 `ensure_dashboard_running()`(检测 pidfile + 端口可连,已跑则复用),返回 `{"url": "http://127.0.0.1:8765/"}`。

### 9.7 共享状态:SQLite DDL(壳层去重的物理基础)

hook / command / MCP 三条路径必须读写**同一**库,才能保证「绝不漏、绝不重复」。库路径 `~/.local/state/bbwatch/bbwatch.db`(由 `engine.store` 创建,启用 `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000`,串行化并发写)。与本章相关的核心表:

```sql
-- 任务 = 带 due 的成绩册栏目;主键用稳定的 course.id + column id
CREATE TABLE IF NOT EXISTS task (
  task_key        TEXT PRIMARY KEY,           -- '_17236_1:_2891_1'
  course_id       TEXT NOT NULL,              -- BB course.id '_17236_1'
  course_label    TEXT NOT NULL,              -- 'MAT3007:Optimization_L01'
  column_id       TEXT NOT NULL,              -- gradebook column id
  content_id      TEXT,                       -- 关联作业内容项(可空)
  title           TEXT NOT NULL,
  due_utc         TEXT,                       -- ISO8601 UTC,来自 grading.due
  score_possible  REAL,
  bb_status       TEXT,                       -- None | NeedsGrading | Graded
  score           REAL,                       -- 出分后非空
  manual_done     INTEGER NOT NULL DEFAULT 0, -- mark_done / 清单页勾选写
  manual_done_at  TEXT,
  first_seen_at   TEXT NOT NULL,
  notified_new    INTEGER NOT NULL DEFAULT 0, -- 新作业是否已通知(去重)
  notified_grade  INTEGER NOT NULL DEFAULT 0  -- 出分是否已通知(去重)
);

-- 公告:id 已知即不再通知
CREATE TABLE IF NOT EXISTS announcement (
  ann_id        TEXT PRIMARY KEY,             -- announcement id
  course_id     TEXT NOT NULL,
  title         TEXT NOT NULL,
  created_utc   TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  notified      INTEGER NOT NULL DEFAULT 0
);

-- 内容项(课件):id + modified 决定是否"新/更新",并驱动增量下载
CREATE TABLE IF NOT EXISTS content (
  content_id     TEXT PRIMARY KEY,
  course_id      TEXT NOT NULL,
  parent_id      TEXT,
  title          TEXT NOT NULL,
  handler        TEXT,                         -- x-bb-folder/-document/-file/-assignment
  modified_utc   TEXT,
  first_seen_at  TEXT NOT NULL,
  notified       INTEGER NOT NULL DEFAULT 0,
  local_path     TEXT,                         -- 已镜像落地路径(增量下载用)
  downloaded_mod TEXT                          -- 上次下载时的 modified(变了才重下)
);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,                      -- 'last_scan_utc'(仅全成功扫描后写)等
  value TEXT
);
```

去重不变量的物理落地:

- **同一事务内 notify+置位(不变量 B):** 「发现新事件 → `notify` → `UPDATE ... SET notified_*=1`」三步在**一个 SQLite 事务**里;事务提交前若进程崩溃,该事件仍是 `notified_*=0`,下次扫描会重新发现并通知——宁可一次通知**可能**因极端崩溃重发(代价:偶尔多一条桌面通知),也绝不静默丢失通知。这是「绝不漏 ≻ 绝不重复」在原子性边界上的明确取舍。
- **diff 只对差集产事件:** `scan` = 「拉取 → 与上表全量 diff → 仅对 `notified_*=0` 的差集产事件并通知」,与触发频率/次数/并发无关。
- **水位单调推进(不变量 C):** `meta.last_scan_utc` 只在本轮全部在读课成功后才 `UPSERT`;部分失败时保持旧值,使 SessionStart 的 `fresh` 判定不会被一次失败扫描误导。

这三条正是 §9.3 once 语义、§9.6 部分失败兜底、多触发源幂等的共同物理保证。

### 9.8 dashboard 进程的生命周期

清单服务由 `ensure_dashboard_running()` 统一管理(SessionStart、`/bb-dashboard`、`open_dashboard` 都调它):
- 绑 `127.0.0.1:8765`(仅本机)。读 `~/.local/state/bbwatch/dashboard.pid`;若进程存活且端口可连则复用,否则后台拉起。起进程这一步同样取文件锁,避免多窗口并发各起一个。
- 页面只读 SQLite 渲染:未完成按 `due_utc` 升序、逾期标红、临近高亮、已完成折叠;勾选完成 → 写 `task.manual_done`(与 `mark_done` 等价,经同一 `engine.store`)。
- 对「距 `meta.last_scan_utc` 过久」给醒目提示(缓解「不开 Claude Code 就不扫」的固有缺口)。
- 存活期间按 `config.toml` 的 `scan_interval`(每 10 分钟~每天定点可配)跑可选周期扫描,`--source periodic`,复用同一 `engine.scanner.scan`,同样取 `scan.lock`、同样幂等。这就是设计中「定时触发」——不依赖任何 OS 级常驻代理(无 launchd/cron)。

### 9.9 安装与市场分发

#### 9.9.1 一次性引擎依赖

插件壳是纯文件,但引擎需 Python 依赖(`curl_cffi`、`keyring`、`mcp`/FastMCP、`fastapi`/`flask`)。安装时在插件根建独立 venv,避免污染 / 被 anaconda 干扰:

```bash
python3 -m venv "${CLAUDE_PLUGIN_ROOT}/.venv"
"${CLAUDE_PLUGIN_ROOT}/.venv/bin/pip" install -r "${CLAUDE_PLUGIN_ROOT}/engine/requirements.txt"
```

`bin/bbwatch` 与 `bin/bbwatch-mcp` 默认即用 `${CLAUDE_PLUGIN_ROOT}/.venv/bin/python`(§9.2.2)。该步骤写进 README,并由 `/bb-setup` 首次运行时检测、缺失则提示执行(`session-start` 检测到 venv/依赖缺失时,只在注入摘要里温和提示,绝不报错刷屏)。

#### 9.9.2 市场清单 `marketplace.json`

发布到一个 Git 仓库作为「插件市场」,根目录放:

```json
{
  "name": "bbwatch-marketplace",
  "owner": { "name": "bbwatch", "url": "https://github.com/<owner>/bbwatch" },
  "plugins": [
    {
      "name": "bbwatch",
      "source": "./",
      "description": "CUHK-SZ Blackboard 作业/公告/课件/出分监控 + 课件下载",
      "version": "0.1.0"
    }
  ]
}
```

#### 9.9.3 同学安装步骤(写进 README)

```
# 1. 在 Claude Code 里添加市场并安装
/plugin marketplace add <owner>/bbwatch
/plugin install bbwatch@bbwatch-marketplace

# 2. 安装引擎依赖(一次性)
python3 -m venv "<plugin_root>/.venv" && \
  "<plugin_root>/.venv/bin/pip" install -r "<plugin_root>/engine/requirements.txt"

# 3. 录入凭据(密码仅进 macOS 钥匙串,绝不落盘/入日志/入文档)
/bb-setup <你的学号>

# 4. 重开会话:SessionStart 自动扫描并注入今日待办;或随时 /bb-scan
```

分发约束:文档示例中**绝不**出现任何真实密码;凭据只经 `/bb-setup` 的隐式输入进钥匙串。v1 仅 macOS(桌面通知 `osascript` / `terminal-notifier`、钥匙串);Windows/Linux 的钥匙串后端与通知渠道留到第三刀,壳层与 MCP 工具签名保持不变,仅替换 `engine.notifier` 与凭据后端即可跨平台。

---

## 10. 本地任务清单(前端)

> 本章详述 bbwatch 的本地清单服务:进程模型、端口策略、路由契约、数据来源、UI 规格与安全边界。所有涉及 BB 的字段名/语义均对齐实测事实(`status ∈ {None, NeedsGrading, Graded}`、`grading.due` 为 UTC、`column id`/`announcement id`/`content id` 为去重锚点),时间一律以 UTC 存储、展示转东八区(+08:00)。本服务**只读/写 store,绝不直连 BB、绝不复制扫描逻辑**——它是引擎的可视前端与可选心跳,不是第二个数据权威。

### 10.1 定位与边界

本地清单服务承担三件事:

1. **看**:浏览器里看未完成作业(按 ddl 升序)、状态徽章(未交/待批/已批/已出分)、逾期/临近高亮。
2. **改**:对无法自动判定的线下任务,手动勾选"已完成",回写 DB,且 `scanner` 必须尊重该标记(§10.7)。
3. **跳**:页面内可触发一次扫描(`/bb-scan` 的等价物),并在"距上次扫描已 X 小时 + 临近 ddl"时给醒目提示,缓解"不开 Claude Code 就不扫"的结构性缺口(对齐设计 §1 取舍)。

非目标:不暴露公网、不做鉴权体系(仅本机单用户)、不渲染密码/cookie/ADFS token、不重复实现扫描逻辑(扫描永远委托 `engine.scanner.scan()`)。

技术栈:**FastAPI + uvicorn**(ASGI),前端为单文件无构建步骤的静态页(原生 JS + SSE),随插件分发。选 FastAPI 而非 Flask 的理由:原生 async(SSE/后台扫描不阻塞事件循环)、Pydantic 响应模型(契约即代码)、与引擎同为 Python 便于直接 import。

### 10.2 文件布局

```
dashboard/
  ├─ __init__.py
  ├─ server.py          # FastAPI app、生命周期、路由注册
  ├─ routes.py          # 路由处理函数(瘦,逻辑下沉到 service)
  ├─ service.py         # store 行 → 视图模型(DTO)的纯函数;时区/派生字段在此层
  ├─ models.py          # Pydantic 响应模型(TaskDTO/ScanStateDTO/...)
  ├─ events.py          # SSE 广播器(进程内 asyncio 订阅/发布)
  └─ runner.py          # 进程管理:单例、端口选择、状态落盘、可选周期扫描循环
  # 静态资源随包内嵌(importlib.resources),避免按 cwd 找文件:
  └─ static/
       ├─ index.html
       ├─ app.js
       └─ app.css
```

运行期产物(都在 `~/.bbwatch/` 下,与 store 同目录):

| 文件 | 用途 |
|---|---|
| `~/.bbwatch/bbwatch.db` | SQLite(store,本服务连这一个;WAL 模式见 §10.7) |
| `~/.bbwatch/dashboard.json` | 服务运行态:`{pid, port, token, instance_id, started_at}`,供 MCP/命令发现已起的服务(权限 `0o600`) |
| `~/.bbwatch/dashboard.lock` | 文件锁(`flock`),保证全机仅一个清单服务实例 |
| `~/.bbwatch/dashboard.log` | 仅请求行+状态码,关闭 query string,绝不含 token/cookie |

### 10.3 进程模型与生命周期

#### 10.3.1 谁来起、何时起

启动触发点全部经由 `runner.ensure_running()` 这一个幂等入口(对齐设计 §4 触发模型):

- **SessionStart 钩子**:`bbwatch session-start` 在后台调 `ensure_running()` 后立即返回(非阻塞,避免拖慢会话)。
- **命令** `/bb-dashboard`:前台调 `ensure_running()` 然后 `open` 浏览器到带 token 的 URL。
- **MCP** `open_dashboard`:同上,返回 URL 给 Claude。

`ensure_running()` 语义="确保有且仅有一个清单服务在跑,返回其 URL+token",反复调用安全。

#### 10.3.2 单例保证(no duplicate 进程)

不能每次开 Claude Code 都起一个新服务(会端口冲突、SSE 各看各的)。用**文件锁 + 健康探测**双保险:

```python
# runner.py
from pathlib import Path
import json, os, secrets, socket, subprocess, sys
import fcntl
import httpx

STATE_DIR  = Path.home() / ".bbwatch"
STATE_FILE = STATE_DIR / "dashboard.json"
LOCK_FILE  = STATE_DIR / "dashboard.lock"
PORT_RANGE = range(53111, 53121)   # 固定 10 个候选,URL 可预测、便于认回

def ensure_running() -> dict:
    """返回 {url, port, token}。幂等:已在跑则复用,否则拉起。"""
    STATE_DIR.mkdir(mode=0o700, exist_ok=True)
    state = _read_state()
    if state and _is_healthy(state):           # 1) 已有健康实例 → 直接复用
        return _as_result(state)

    lockf = open(LOCK_FILE, "w")               # 2) 抢锁,锁内再查一次(防竞态双起)
    fcntl.flock(lockf, fcntl.LOCK_EX)
    try:
        state = _read_state()
        if state and _is_healthy(state):
            return _as_result(state)
        port  = _pick_port()                   # 3) 选端口
        token = secrets.token_urlsafe(24)      # 4) 生成本机回环 token(§10.8)
        iid   = secrets.token_hex(8)           # 5) instance_id,健康探测据此防假阳性
        proc  = _spawn_uvicorn(port, token, iid)
        _wait_healthy(port, iid, timeout=8)    # 6) 轮询 /healthz 直到就绪;失败则清理并报错
        _write_state({"pid": proc.pid, "port": port, "token": token,
                      "instance_id": iid, "started_at": _now_iso()})
        return _as_result(_read_state())
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN); lockf.close()
```

`_is_healthy(state)` 不只看 PID 存活,而是真的 `GET http://127.0.0.1:{port}/healthz` 并校验返回里的 `instance_id` 与 `state["instance_id"]` **完全相等**——防止两类假阳性:"PID 被系统回收后复用给别的进程"、"端口被别的程序占了"。探测失败则视为无实例,继续拉起。注意:`instance_id` 与 `token` 是两个独立值,健康探测用 `instance_id`(可公开,仅用于身份核对),`token` 仅用于授权(§10.8),不在 `/healthz` 暴露。

#### 10.3.3 端口选择与冲突处理

- 用**固定候选区间** `53111–53120`(私有动态端口段),URL 可预测、便于书签、便于把已起服务认回来。
- 选端口算法:遍历区间,对每个端口试 `bind('127.0.0.1', p)`(不设 `SO_REUSEADDR`,确保"能 bind"等价于"当前真空闲");第一个能 bind 的即用;全占满则报错并提示用户。

```python
def _pick_port() -> int:
    for p in PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p)); return p
            except OSError:
                continue
    raise RuntimeError(f"no free port in {PORT_RANGE.start}-{PORT_RANGE.stop-1}")
```

> `bind` 探测与子进程真正 `bind` 之间有 TOCTOU 窗口,但整个选端口在 `flock` 临界区内、且 `_wait_healthy` 会确认实际就绪;若子进程因端口被抢而退出,`_wait_healthy` 超时 → `ensure_running` 清理后由调用方重试(或在锁内对下一个端口重试),实践上安全。

#### 10.3.4 子进程与常驻

服务用 `uvicorn` 在 **detached 子进程**里跑(不随 SessionStart 钩子进程退出而被杀),以便在 Claude Code 会话之间常驻、支撑可选周期扫描:

```python
def _spawn_uvicorn(port: int, token: str, instance_id: str) -> subprocess.Popen:
    env = {**os.environ,
           "BBWATCH_DASH_TOKEN": token,           # str,不是 int
           "BBWATCH_DASH_PORT": str(port),
           "BBWATCH_DASH_INSTANCE": instance_id}
    log = open(STATE_DIR / "dashboard.log", "a")   # 仅请求行,绝不含 token/cookie
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "dashboard.server:app",
         "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning", "--no-access-log"],  # 关访问日志,杜绝 query 里的 token 落盘
        env=env, stdout=log, stderr=log,
        start_new_session=True,                        # 脱离会话进程组,常驻
    )
```

生命周期总结:

| 阶段 | 行为 |
|---|---|
| 首次 SessionStart | 抢锁 → 选端口 → spawn → 健康后写 `dashboard.json` |
| 后续 SessionStart | `_is_healthy` 命中 → 0 成本复用 |
| 进程崩溃后再开 | 健康探测失败 → 重新拉起,端口尽量复用同一个 |
| 主动停止 | `/bb-dashboard --stop` 或 MCP → `runner.stop()` 读 `dashboard.json` 的 pid `SIGTERM`,清状态文件 |
| 机器重启 | 无 OS 级常驻(对齐"不用 launchd/cron"硬约束);残留状态文件被下次 `_is_healthy` 判失败后覆盖 |

FastAPI 用 lifespan 管理可选周期扫描任务(§10.6.4):

```python
# server.py
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    cfg = load_config()
    if cfg.periodic_scan_enabled:
        task = asyncio.create_task(_periodic_scan_loop(cfg.scan_interval_seconds))
    yield
    if task:
        task.cancel()

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
```

### 10.4 数据来源:store 视图

清单服务只读 store 暴露的**任务视图**。store 已把"成绩册列 + 我的列状态 + 手动完成标记"归一成可查询的 `task` 表。本前端契约所需的形态(完整 DDL 属 `engine.store` 章节):

```sql
-- 任务 = 带 grading.due 的成绩册列(已过滤掉 Weighted Total/Total 等无 due 汇总列)
CREATE TABLE IF NOT EXISTS task (
    column_id      TEXT NOT NULL,            -- BB gradebook column id,如 '_12345_1'(去重锚点)
    course_id      TEXT NOT NULL,            -- BB course.id,如 '_17236_1'
    user_id        TEXT NOT NULL,            -- BB userId,如 '_49765_1'
    course_label   TEXT NOT NULL,            -- 人类可读 courseId,如 'MAT3007:Optimization_L01'
    name           TEXT NOT NULL,            -- 列名,如 'Homework 4'
    content_id     TEXT,                     -- grading 关联的内容项 id(可空)
    due_utc        TEXT,                     -- grading.due,ISO8601 UTC,如 '2026-06-30T15:59:00.000Z'
    score_possible REAL,                     -- score.possible
    -- 我的完成态(来自 columns/{colId}/users/{uid})
    bb_status      TEXT NOT NULL DEFAULT 'None',  -- 'None' | 'NeedsGrading' | 'Graded'
    score          REAL,                     -- 出分后非空
    -- 本地手动完成(线下任务)
    manual_done    INTEGER NOT NULL DEFAULT 0,    -- 0/1
    manual_done_at TEXT,                     -- 勾选时间 UTC
    -- 审计 / 去重锚点
    first_seen_utc TEXT NOT NULL,            -- 列首次被扫到(新作业提醒锚点,仅 INSERT 写)
    last_scan_utc  TEXT NOT NULL,            -- 该行最近一次被扫描刷新的时间
    PRIMARY KEY (column_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_task_due ON task(due_utc);

-- 全局扫描元信息(单行)
CREATE TABLE IF NOT EXISTS scan_meta (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    last_scan_utc TEXT,                      -- 最近一次成功扫描完成时间
    last_scan_ok  INTEGER NOT NULL DEFAULT 1,
    last_error    TEXT                       -- 仅异常类名,脱敏
);
```

> 字段语义对账实测:`bb_status` 三态与 `score` 取自 `GET .../gradebook/columns/{colId}/users/{uid}`(`None`=未提交、`NeedsGrading`=已交待批、`Graded`=已批改;`score` 出分后非空)。`due_utc` 取自 `grading.due` 且为 UTC。`task` 以 `(column_id, user_id)` 为主键,天然对齐"用稳定 id 做全量 diff、幂等去重"的鲁棒性约束。

**派生完成态**(单一真相,前后端共用同一判定):

```
is_done(task) = task.manual_done == 1
             OR task.bb_status in ('NeedsGrading', 'Graded')
             OR task.score is not None
```

即:已交(待批)、已批改、已出分、或手动勾选,四者任一为"完成"。这与设计 §7 优先级一致,避免"已交还提醒"的误报。

`service.py` 把行映射为 DTO,所有时间在此层转 +08:00 并算派生字段:

```python
# models.py
from pydantic import BaseModel
from typing import Literal, Optional

class TaskDTO(BaseModel):
    column_id: str
    course_label: str
    name: str
    due_local: Optional[str]        # 'YYYY-MM-DD HH:MM' (+08:00),可空(无 due 仍入清单,排末尾)
    due_utc: Optional[str]
    badge: Literal["not_submitted", "needs_grading", "graded"]  # 未交/待批/已批
    is_done: bool
    manual_done: bool               # 单独给出:供 UI 判断勾选框该不该置灰(§10.6.2)
    score: Optional[float]
    score_possible: Optional[float]
    overdue: bool                   # due < now 且未完成
    due_soon: bool                  # 0 <= due - now <= 48h 且未完成
    hours_to_due: Optional[float]

class ScanStateDTO(BaseModel):
    last_scan_utc: Optional[str]
    last_scan_local: Optional[str]
    hours_since_scan: Optional[float]
    last_scan_ok: bool
    stale_warning: bool             # hours_since_scan 超阈值
    urgent_unscanned: bool          # 有 due_soon 任务 且 最近未扫(§10.6.1 醒目提示核心)
```

徽章映射(徽章反映 BB 真实状态,与"是否完成"解耦——手动勾选的线下任务在 BB 里仍是 `None`):

```python
def badge_of(row) -> str:
    if row.bb_status == "Graded" or row.score is not None:
        return "graded"            # 已批/已出分
    if row.bb_status == "NeedsGrading":
        return "needs_grading"     # 待批
    return "not_submitted"         # 未交(含手动勾选完成的线下任务)
```

### 10.5 路由(API 契约)

所有路由挂 `/api` 前缀;HTML/静态资源在根。除 `/healthz` 外,所有路由要求回环 token(§10.8)。响应统一 `application/json`,时间字段同时给 `*_utc` 与 `*_local`(+08:00)。

| 方法 | 路径 | 用途 | 关键返回/入参 |
|---|---|---|---|
| GET | `/healthz` | 存活探测(无需 token) | `{instance_id, started_at}` |
| GET | `/api/tasks` | 任务列表 | `query: status, course, include_done`;返回 `{tasks: TaskDTO[], scan: ScanStateDTO}` |
| GET | `/api/scan-state` | 仅扫描元信息(轻量轮询/降级用) | `ScanStateDTO` |
| POST | `/api/tasks/{column_id}/done` | 勾选/取消完成(线下任务) | body `{done: bool}`;回写 `manual_done`,返回更新后 `TaskDTO` |
| POST | `/api/scan` | 触发一次扫描(= `/bb-scan`) | 异步启动 `scanner.scan()`,立即返回 `{status}` |
| GET | `/api/events` | SSE 刷新流 | 事件:`tasks_changed` / `scan_started` / `scan_finished` |
| GET | `/` | 清单页 HTML | 把 token 注入页面(同源 fetch/SSE 携带) |

#### 10.5.1 GET /api/tasks

排序与过滤:

- **默认仅未完成**(`include_done=false`),按 `due_utc` 升序;`due_utc` 为空(无 ddl)排末尾。
- `include_done=true` 时,已完成项追加在后、前端折叠分组。
- `status` 取值 `not_submitted|needs_grading|graded` 可选过滤;`course` 按 `course_label` 前缀过滤(支持课程黑/白名单视图)。

```python
# routes.py
@router.get("/api/tasks", response_model=TasksResponse)
def get_tasks(status: str | None = None, course: str | None = None,
              include_done: bool = False, _=Depends(require_token)):
    rows  = store.query_tasks(include_done=include_done,
                              status=status, course_prefix=course)
    tasks = [service.to_dto(r) for r in rows]
    tasks.sort(key=service.sort_key)        # 未完成优先 → due 升序 → 无 due 末尾
    return TasksResponse(tasks=tasks, scan=service.scan_state())
```

排序键(用 UTC 字符串可直接字典序比较,因为 ISO8601 同为 `...Z`):

```python
def sort_key(t: TaskDTO):
    return (t.is_done, t.due_utc is None, t.due_utc or "9999")
```

#### 10.5.2 POST /api/tasks/{column_id}/done

幂等写。`column_id` 是 BB 稳定列 id,直接作主键定位;勾选只动 `manual_done`/`manual_done_at`,**绝不触碰 `bb_status`/`score`**(那是 BB 真相,扫描器维护)。写完广播 SSE `tasks_changed`。

```python
@router.post("/api/tasks/{column_id}/done", response_model=TaskDTO)
def set_done(column_id: str, body: DoneBody, _=Depends(require_token)):
    row = store.set_manual_done(column_id, user_id=current_uid(),
                                done=body.done, at=utcnow_iso())
    if row is None:
        raise HTTPException(404, "unknown task")
    events.publish("tasks_changed", {"column_id": column_id})
    return service.to_dto(row)
```

store 侧:

```python
# engine/store.py
def set_manual_done(self, column_id, user_id, done: bool, at: str):
    cur = self.conn.execute(
        "UPDATE task SET manual_done=?, manual_done_at=? "
        "WHERE column_id=? AND user_id=?",
        (1 if done else 0, at if done else None, column_id, user_id))
    self.conn.commit()
    if cur.rowcount == 0:
        return None                          # 未知任务 → 路由层 404
    return self.get_task(column_id, user_id)
```

> `current_uid()` 不取自请求(防越权指定他人 uid),而由服务启动时从 store 读定的本机单用户 uid(如 `_49765_1`)。本服务单用户,uid 固定。

#### 10.5.3 POST /api/scan

页面内"立即扫描"按钮。委托引擎,不重复逻辑;后台跑,SSE 通知进度:

```python
scan_lock = asyncio.Lock()

@router.post("/api/scan")
async def trigger_scan(_=Depends(require_token)):
    if scan_lock.locked():
        return {"status": "already_running"}
    asyncio.create_task(_run_scan())         # 不阻塞响应
    return {"status": "running"}

async def _run_scan():
    async with scan_lock:                    # 进程内互斥,防并发双扫
        events.publish("scan_started", {})
        try:
            n = await asyncio.to_thread(scanner.scan)   # 引擎同步,丢线程池
            events.publish("scan_finished", {"ok": True, "events": n})
        except Exception as e:
            events.publish("scan_finished", {"ok": False, "error": type(e).__name__})
        events.publish("tasks_changed", {})  # 无论成败都刷新(失败时拉到的是旧数据,正确)
```

> `scanner.scan()` 对 BB 温和(限速、复用会话),并与 SessionStart/周期扫描共用同一 SQLite,幂等去重由 store 保证(对齐设计 §6/§8)。`scan_lock` 只防"同一进程内"并发;跨进程并发(如另一个 Claude Code 会话的钩子同时扫)由 store 的写事务 + 幂等 upsert + WAL 兜底(§10.7)。

#### 10.5.4 GET /api/events(SSE)

选 SSE 而非 WebSocket:单向推送够用、实现简单、断线浏览器自动重连。选 SSE 而非纯轮询:即时、省请求。但**保留轮询作为降级**——前端若 SSE 连接失败,退回每 30s 轮询 `/api/scan-state`,变化时再拉 `/api/tasks`。

```python
@router.get("/api/events")
async def events_stream(request: Request, _=Depends(require_token)):
    async def gen():
        q = events.subscribe()
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: {evt.name}\ndata: {json.dumps(evt.data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"      # 心跳,穿透代理空闲超时
        finally:
            events.unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream")
```

`events.py` 是进程内极简发布订阅(每个 SSE 连接一个 `asyncio.Queue`),无外部依赖。

### 10.6 UI 规格

单页,三段式:**顶部状态条 → 未完成任务列表 → 已完成(折叠)**。原生 JS,启动拉 `/api/tasks`,之后由 SSE 驱动刷新。

#### 10.6.1 顶部状态条(缓解"不开就不扫")

这是对结构性缺口的核心缓解,必须醒目。基于 `ScanStateDTO`:

- 始终显示 **"距上次扫描已 X 小时"**(`hours_since_scan`,来源 `scan_meta.last_scan_utc`)。
- `stale_warning`(默认阈值 `hours_since_scan > 6`,可配):状态条变琥珀色,文案"数据可能过时,建议刷新"。
- `urgent_unscanned`(**最高优先级红色横幅**):存在 `due_soon` 任务(48h 内到期、未完成)**且** `hours_since_scan > 2`。文案如:"有 2 项作业 48 小时内截止,但已 7 小时未扫描——点此立即刷新"。点击即 `POST /api/scan`。
- 一个"立即扫描"按钮;扫描中转 spinner(收 SSE `scan_started`/`scan_finished` 切换)。
- 若 `last_scan_ok=false`,显示上次扫描失败(脱敏的 `last_error` 类名,绝不含密码/cookie/URL)。

> 阈值设计要点(no miss):`urgent_unscanned` 的判定在**后端** `service.scan_state()` 算定并随每次 `/api/tasks`、`/api/scan-state`、SSE 后的刷新返回,前端不自行推断,避免前端时钟漂移导致漏报。前端仅在页面可见时按 60s 定时拉 `/api/scan-state` 重算横幅,确保"用户开着页面但很久没动作"也能持续看到升级中的紧急提示。

```js
// app.js —— 状态条渲染要点
function renderScanBar(scan) {
  bar.classList.toggle('stale',  scan.stale_warning);
  bar.classList.toggle('urgent', scan.urgent_unscanned);
  scanInfo.textContent = scan.hours_since_scan == null
      ? '尚未扫描过'
      : `距上次扫描已 ${scan.hours_since_scan.toFixed(1)} 小时`;
  urgentBanner.hidden = !scan.urgent_unscanned;
}
```

#### 10.6.2 任务列表

每行:课程标签 · 任务名 · ddl(本地时间)· 倒计时 · 状态徽章 · 完成勾选框。

- **排序**:未完成按 ddl 升序;无 ddl 末尾;已完成折叠到底部分组。
- **逾期标红**(`overdue`):`due_local` 与整行红色;倒计时"已逾期 Xh"。
- **临近高亮**(`due_soon`,48h 内):整行琥珀底/左边框;倒计时"还剩 Xh"。
- **状态徽章**三态,与"完成"解耦:
  - `not_submitted` → 灰底"未交"
  - `needs_grading` → 蓝底"待批"
  - `graded` → 绿底"已批";若有 `score`,后缀 `score/possible`(如"已批 92/100")
- **完成勾选框**:勾上立即 `POST .../done {done:true}`,乐观更新,失败回滚;勾选后移入折叠区。**置灰规则用 `manual_done` 而非 `is_done`**:仅当任务"完成"来自 BB(`bb_status ∈ {NeedsGrading, Graded}` 或 `score` 非空)而**非**手动勾选时,勾选框默认勾上且禁用(其状态由 BB 决定,手动无意义);纯手动勾选的线下任务勾选框保持可用,以便用户取消勾选。判据:`disabled = is_done && !manual_done`。

> 徽章与勾选并存的语义:勾选框=该任务"是否还要我操心"(`is_done`);徽章=BB 客观提交/批改状态。一道线下作业可"已勾选完成"但徽章仍"未交"(BB 无此提交记录),这是预期行为,不是 bug。

#### 10.6.3 SSE 驱动刷新

```js
const es = new EventSource(`/api/events?t=${encodeURIComponent(TOKEN)}`);
es.addEventListener('tasks_changed', () => refreshTasks());
es.addEventListener('scan_started',  () => setScanning(true));
es.addEventListener('scan_finished', () => { setScanning(false); refreshTasks(); });
es.onerror = () => startPollingFallback();   // SSE 挂了退回 30s 轮询
```

#### 10.6.4 可选周期扫描

服务存活期间(=用户在用 Claude Code)的"定时触发",由 lifespan 起的循环驱动,不依赖任何 OS 常驻代理(对齐硬约束):

```python
async def _periodic_scan_loop(interval_s: int):
    while True:
        await asyncio.sleep(interval_s)      # 配置:600s ~ 86400s
        if not scan_lock.locked():
            await _run_scan()                # 复用同一扫描入口,SSE 自动通知前端
```

频率每 10 分钟到每天定点可配(`config.scan_interval_seconds`);默认关闭或保守值,对 BB 温和。

### 10.7 勾选回写与扫描器协同(no miss / no duplicate)

核心不变量:**手动勾选只写 `manual_done`/`manual_done_at`;扫描只写 BB 来源字段(`bb_status`/`score`/`due_utc`/`name`/`course_label`/`content_id`/`score_possible`/`last_scan_utc`);两组字段不相交,永不互相覆盖。`first_seen_utc` 仅在 INSERT 时写,任何路径都不改。**

扫描器对 `task` 行用 upsert,显式只更新 BB 来源字段:

```sql
INSERT INTO task (column_id, user_id, course_id, course_label, name,
                  content_id, due_utc, score_possible, bb_status, score,
                  first_seen_utc, last_scan_utc)
VALUES (:column_id, :user_id, :course_id, :course_label, :name,
        :content_id, :due_utc, :score_possible, :bb_status, :score,
        :now, :now)
ON CONFLICT(column_id, user_id) DO UPDATE SET
    course_label   = excluded.course_label,
    name           = excluded.name,
    content_id     = excluded.content_id,
    due_utc        = excluded.due_utc,
    score_possible = excluded.score_possible,
    bb_status      = excluded.bb_status,
    score          = excluded.score,
    last_scan_utc  = excluded.last_scan_utc;
    -- 不触碰 manual_done / manual_done_at / first_seen_utc / course_id
```

协同规则:

1. **扫描尊重手动勾选**:重扫不会清掉用户勾的"已完成"(`manual_done` 不在 update 列)。
2. **BB 反转优先且无矛盾**:若用户曾手动勾完成,后来 BB 出现真实提交(`bb_status → NeedsGrading/Graded`),`is_done` 仍为真;反之若 BB 显示已交,UI 勾选框置灰(§10.6.2),用户无从误取消。
3. **新作业不漏**:`first_seen_utc` 仅 INSERT 时写,是"新作业提醒"的锚点;`ON CONFLICT` 路径不改它,保证已通知过的不会重复触发(去重)。新作业/出分等**通知事件由 scanner 产出并自带去重标记**(在 store 记"已通知"),清单页不产出通知,只通过 SSE 刷新视图,因此前后端不会重复通知。
4. **出分检测**:`bb_status: None/NeedsGrading → Graded` 或 `score: null → 非空` 的迁移,由 scanner 在 upsert **前**比对旧值产出"出分"事件;清单页只消费结果,经 SSE `tasks_changed` 把徽章刷成"已批"。

**并发与一致性(跨进程 no duplicate / no miss)**:

- store 以 **WAL 模式**打开(`PRAGMA journal_mode=WAL`),并设 `busy_timeout`(如 5000ms),让"清单页周期扫描写"与"另一会话 SessionStart 钩子写"并发时不致 `database is locked` 而丢更新。
- 所有"读旧值 → 比对 → upsert → 记已通知"在 scanner 内是**单事务**,保证出分/新作业判定与去重标记原子落库;即使两个进程同时扫同一门课,第二个看到的是已更新+已标记的状态,不会重复产出事件。
- 清单页对 store 的写仅 `set_manual_done` 一处,与 scanner 字段不交、且各自短事务,天然不冲突。

**时区**:DB 全存 UTC;仅 `service.to_dto` 与前端展示转 +08:00。倒计时/逾期/`due_soon`/`urgent_unscanned` 全部用 UTC `now` 与 `due_utc` 直接比较,避免本地化误差。

### 10.8 安全(仅本机)

定位:单用户、单机、回环。安全目标是"防本机其它进程/页面顺手读到",非抗定向攻击。

- **仅绑回环**:`--host 127.0.0.1`,绝不 `0.0.0.0`。不暴露任何公网/局域网入口。
- **回环 token**:即便绑回环,本机其它用户/进程仍可能访问端口。生成进程级随机 `token`(`secrets.token_urlsafe(24)`),写入 `dashboard.json`(`0o600`),HTML 由服务注入页面、同源 fetch/SSE 携带。`require_token` 校验所有 `/api/*`(`/healthz` 除外):

```python
def require_token(request: Request):
    expected = os.environ["BBWATCH_DASH_TOKEN"]
    got = request.query_params.get("t") or request.headers.get("X-BB-Token")
    if not got or not secrets.compare_digest(got, expected):
        raise HTTPException(401)
```

- **CSRF/同源**:状态变更路由(`/done`、`/scan`)要求自定义头 `X-BB-Token`(简单请求无法携带自定义头 → 强制预检 → 拦截跨站表单 CSRF);不开放 CORS(不设 `Access-Control-Allow-Origin`,跨源预检直接失败)。`/healthz` 不暴露 token,仅返回 `instance_id`/`started_at`,且这两者均为非敏感值。
- **token 不进 URL 日志**:`--no-access-log` 关闭 uvicorn 访问日志,`dashboard.log` 只承载 warning 级请求行;SSE/fetch 虽把 token 放 query(`?t=`),但因访问日志关闭,token 不落 `dashboard.log`;状态变更走 `X-BB-Token` 头而非 query。
- **绝不渲染机密**:页面、API、日志一律不含 BB 密码、cookie、ADFS token。`last_error` 只暴露异常类名,不带堆栈细节(堆栈里可能夹带 URL/凭据)。
- **目录权限**:`~/.bbwatch/` 为 `0o700`,`dashboard.json`/`bbwatch.db` 为 `0o600`,降低同机他人读 token/数据的面。
- **无远程能力**:服务不持有"代发请求"接口;唯一对外动作是触发本地 `scanner.scan()`(用用户钥匙串里的凭据访问其本人 BB 数据),符合"用户用自己的账号访问自己的数据"的合规定位。

---

相关实现文件(全部绝对路径,落地时创建):
- 服务实现:`/Users/mac/Programming/cuhkszbb/dashboard/server.py`、`routes.py`、`service.py`、`models.py`、`events.py`、`runner.py`
- 前端静态:`/Users/mac/Programming/cuhkszbb/dashboard/static/{index.html,app.js,app.css}`
- 运行态产物:`~/.bbwatch/{bbwatch.db,dashboard.json,dashboard.lock,dashboard.log}`
- store DDL 归属:`/Users/mac/Programming/cuhkszbb/engine/store.py`(本章 §10.4 的 `task`/`scan_meta` 表)

---

## 11. 课件增量镜像下载

> 本章定义 `engine.downloader` 模块（对应 §3 架构与 §9 第二刀）。职责：把每门在读课程的内容树（含附件文件）增量镜像到本地，按"课程代码 / 文件夹层级 / 原文件名"组织；只下载新增或变更的附件；保证完整性与断点续传；与 `engine.scanner` 的"新课件"事件联动，做到"检测到新课件即可自动下载"。
>
> 全部 BB 接口断言均对应附录 A 实测端点：内容树走 `contents`（顶层）/ `contents/{id}/children`（子级）递归，附件走 `contents/{id}/attachments`，下载 `attachments/{aid}/download` 返回 **302** 跳真实文件需跟随；所有时间字段为 UTC，展示层转 +8。

### 11.1 设计目标与不变式

下载子系统服从全局两条硬需求（§1/§8 的"绝不漏 / 绝不重复"），并将其细化为可验证不变式：

| 编号 | 不变式 | 实现支点 |
|---|---|---|
| D1 **不漏** | 任一时刻 BB 上存在的附件，最终都会落到本地镜像——即便中间多次扫描失败、多天未扫。 | 每次 mirror 对内容树做**全量遍历 + 与 `downloads` 表全量 diff**（非时间窗口增量，与 §6 全量 diff 鲁棒性一致）；失败项留库待下次重试，不依赖单次成功。 |
| D2 **不重复下载** | 已下载且未变更的附件，绝不重复拉字节。 | 稳定主键 `(course_pk, content_id, attachment_id)`；变更判据 = 新 `attachment_id` ∨ 父内容项 `modified` 变新 ∨ 下载响应元数据（`ETag`/`Content-Length`）变化 ∨ 本地文件缺失/大小不符。 |
| D3 **完整性** | 落盘的每个文件要么完整正确，要么不存在（绝不留半截文件冒充已下载）。 | 下载到 `*.part` → 校验大小 → 原子 `os.replace` 到正式名；`status=complete` 只在 replace 成功后写库。 |
| D4 **可断点** | 中断后重跑只补未完成部分。 | 保留 `*.part` + 记录 `bytes_done`，用 `Range` 续传；未完成行 `status=partial/failed` 下次优先重试。 |
| D5 **对 BB 温和** | 并发受限、请求间节流、复用同一会话。 | 课程内串行 + 课程间全局信号量（默认并发 3）+ 全局令牌桶节流（默认 400ms）+ 复用 `engine.auth.get_session()`。 |
| D6 **凭据/敏感 URL 安全** | 下载链路不落盘明文凭据、不进日志；302 目标 URL（常带签名 token）视为敏感。 | 沿用会话 cookie；日志只记 id/相对路径/大小/错误类别，**绝不记 cookie、原始 URL、302 目标 URL**。 |
| D7 **不漏通知 / 不重通知** | 一个新附件最多触发一条"新课件"提醒，且不会因下载逻辑漏报或重报。 | 通知归属 `engine.scanner` 的内容事件 id（沿用全局"id 已通知即打标记"机制，§7/§8）；下载只是该事件的**副作用**，自身从不直接发通知（详见 11.8）。 |

> **职责边界（消除与 scanner 的重叠）**：内容树的"发现新课件并去重通知"由 `engine.scanner` 负责；`downloader` 只负责"把附件搬到本地且不重复搬"。两者各持一张表——scanner 持内容快照（§6），downloader 持 `downloads` 表——通过同一份内容树抓取结果联动（11.8），不各自重复请求 BB。

### 11.2 本地路径布局

镜像根目录默认 `~/Library/Application Support/bbwatch/mirror/`（配置 `mirror.root` 覆盖）。布局：

```
<mirror.root>/
  <course_slug>/                      # 课程级目录，如 MAT3007_Optimization
    <folder>/<subfolder>/...          # 镜像 BB 内容树文件夹层级
      <fileName>                      # 原始 fileName（来自 attachments.fileName）
      <fileName (2).pdf>              # 重名消歧后缀（插在扩展名前）
  .trash/<course_slug>/...            # --prune 时被撤下附件的归宿（默认不启用）
```

`*.part` 临时文件与最终文件**同目录同分区**存放（保证 `os.replace` 原子），命名 `<最终名>.part`；不集中到单独的 partials 目录，避免跨分区 rename 退化为非原子拷贝。

**`course_slug` 推导**：取 `course.courseId`（人类可读，实测形如 `MAT3007:Optimization`），经 `sanitize_component()` 把 `:` 等非法字符替换为 `_`，得 `MAT3007_Optimization`。同时把 `course.id`（内部稳定 id，形如 `_17236_1`）写入 `mirror_dirs`/`downloads` 做关联——**目录名给人看，去重靠内部 id**；课程改名只更新 slug，不影响任何附件的去重身份。

**文件夹层级**：递归内容树时，每个 `resource/x-bb-folder` 节点的 `title` 经 sanitize 后作为一级路径段；`x-bb-document`/`x-bb-file`/`x-bb-assignment` 等非文件夹节点**默认不单独建目录**，其附件直接落在父文件夹目录下（贴近 BB 网页观感）。配置 `mirror.group_by_item=true` 时，为挂多个附件的非文件夹项额外建一层 `sanitize(title)/` 子目录。

**`sanitize_component()`**（对每段路径独立施加，面向跨平台分发）：

```python
_ILLEGAL  = r'[/\\:*?"<>|\x00-\x1f]'              # 跨平台非法字符并集
_RESERVED = {"CON","PRN","AUX","NUL", *(f"COM{i}" for i in range(1,10)),
             *(f"LPT{i}" for i in range(1,10))}    # Windows 设备名

def sanitize_component(name: str, *, maxlen: int = 150) -> str:
    s = unicodedata.normalize("NFC", name).strip()
    s = re.sub(_ILLEGAL, "_", s)
    s = s.rstrip(" .")                             # Windows 禁止结尾空格/点
    if s.upper().split(".")[0] in _RESERVED:
        s = "_" + s
    if not s:
        s = "untitled"
    if len(s) > maxlen:                            # 截断保留扩展名
        stem, dot, ext = s.rpartition(".")
        s = (stem[: maxlen - len(ext) - 1] + "." + ext) if (dot and len(ext) <= 16) \
            else s[:maxlen]
    return s
```

> **大小写一致性**：macOS 默认大小写不敏感、Linux 敏感。重名检测（`resolve_collision`）在**大小写折叠**后比较，确保同一份镜像复制到任一平台都不会出现"两文件名仅大小写不同"导致的覆盖。

**重名消歧 `resolve_collision()`**：同一目标目录内，若某 `fileName` 经 sanitize（折叠大小写比较）后与**另一个** `(content_id, attachment_id)` 已占用的本地名冲突，追加 ` (2)`、` (3)`…（插在扩展名前）。结果持久化到 `downloads.local_relpath`，**一经分配即冻结**——附件的稳定身份恒为 `(content_id, attachment_id)`，与本地名解耦，故重跑/改名都不会触发重复下载（D2）。

### 11.3 数据库 schema（`engine.store`，SQLite）

下载子系统新增两张表，与既有快照表共库。所有时间存 ISO-8601 UTC 文本。

```sql
-- 镜像目录登记：course.id ↔ 本地 course_slug，便于改名追踪与清理
CREATE TABLE IF NOT EXISTS mirror_dirs (
    course_pk     TEXT PRIMARY KEY,      -- course.id，内部稳定 id，如 _17236_1
    course_id     TEXT NOT NULL,         -- courseId，人类可读，如 MAT3007:Optimization
    course_slug   TEXT NOT NULL,         -- sanitize 后目录名
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- 每个附件一行；稳定主键 = (course_pk, content_id, attachment_id)
CREATE TABLE IF NOT EXISTS downloads (
    course_pk        TEXT NOT NULL,      -- course.id
    content_id       TEXT NOT NULL,      -- 内容项 id（attachments 的父）
    attachment_id    TEXT NOT NULL,      -- attachments[].id
    file_name        TEXT NOT NULL,      -- attachments[].fileName（原始）
    mime_type        TEXT,               -- attachments[].mimeType
    content_modified TEXT,               -- 父内容项 contents.modified（变更检测）
    local_relpath    TEXT NOT NULL,      -- 相对 mirror.root，含 course_slug/.../name
    etag             TEXT,               -- 下载响应 ETag（若服务端给）
    remote_size      INTEGER,            -- 期望大小（Content-Length，可能 NULL）
    bytes_done       INTEGER NOT NULL DEFAULT 0,  -- 断点续传已落字节
    sha256           TEXT,               -- 完成后整文件哈希（完整性 + 跨项秒传）
    status           TEXT NOT NULL DEFAULT 'pending',  -- pending|partial|complete|failed|stale
    attempts         INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,               -- 仅错误类别/消息，绝不含 URL/cookie
    first_seen       TEXT NOT NULL,      -- 首次在内容树发现的时刻（UTC）
    downloaded_at    TEXT,               -- status→complete 的时刻
    PRIMARY KEY (course_pk, content_id, attachment_id)
);

CREATE INDEX IF NOT EXISTS idx_downloads_course ON downloads(course_pk);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);
-- 同一课程目录树内本地路径唯一（兜底防撞）；不同课程目录天然不冲突
CREATE UNIQUE INDEX IF NOT EXISTS idx_downloads_relpath ON downloads(course_pk, local_relpath);
```

`status` 状态机：

```
pending ──下载开始──▶ partial ──校验通过 + os.replace──▶ complete
   ▲                    │                                    │
   │                    └──可重试失败──▶ failed ──下次重试──▶ partial
   └── 父项 modified 变新 / ETag|Content-Length 变 / 本地缺失或大小不符 ──▶ stale ──▶ 重下
```

> `idx_downloads_relpath` 唯一约束是 `resolve_collision` 之外的最后一道保险：两个附件若意外抢同一本地路径，写库即报错而非静默覆盖（保 D2/D3）。

### 11.4 变更/增量判定（D1+D2 的核心）

对每门课，`mirror()` 先**全量**抓内容树，得当前 `live` 附件集合 `{(content_id, attachment_id) → meta}`，再与 `downloads` 表做三路 diff：

1. **新增**（`live` 有、表无）→ 插 `status=pending`，入下载队列。
2. **两边都有**→ 满足任一条件置 `status=stale` 重下，否则维持 `complete` 且**不触网拉字节**：
   - 父内容项 `modified` 比表中 `content_modified` 更新（附录 A：`modified` 用于"新课件/更新"判定）；
   - 下载时回填的 `ETag` 与表中不同；
   - 下载时回填的 `Content-Length` 与 `remote_size` 不同；
   - 本地文件缺失，或本地大小 ≠ `remote_size`（用户删了文件 / 上次只下一半）。
3. **消失**（表有、`live` 无）→ **默认保留本地文件**（期末复习不丢东西），仅把行标记便于审计；只有显式 `--prune` 才移入 `.trash/<course_slug>/`。

> **实测对齐**：附件列表（`attachments`）实测只稳定返回 `id`/`fileName`/`mimeType`，**不保证给大小/ETag**。故 `remote_size`/`etag` 一律靠下载时的响应头回填，**绝不臆造不存在的元数据字段**；首轮变更判定只能依赖 `attachment_id` 与父项 `modified`，这正是 D1 选择"全量 diff 而非依赖元数据增量"的原因。

> **不漏的闭环保证**：diff 基于本地稳定集合而非时间窗口，因此即使连续多天没开 Claude Code、或中间扫描部分失败，下一次 mirror 仍会把所有"表里非 complete"和"表外新增"的附件全部纳入队列——无单点遗漏。

### 11.5 单附件下载：跟随 302、续传、校验、原子落盘

实测 `GET .../attachments/{aid}/download` 返回 **302** 跳真实文件（URL 常带签名 token）。下载器**必须**跟随重定向，并把 302 目标 URL 视为敏感（不入日志、不入库，D6）。

```python
def fetch_attachment(sess, course_pk, content_id, att, dst_abs, row) -> DownloadResult:
    """下载单个附件到 dst_abs（原子）。sess 为复用的 BB 会话。"""
    part = dst_abs + ".part"
    headers = {"Accept": "*/*"}
    resume_from = 0
    if os.path.exists(part) and row.status in ("partial", "failed"):
        resume_from = os.path.getsize(part)
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"      # 断点续传

    url = (f"{BASE}/learn/api/public/v1/courses/{course_pk}"
           f"/contents/{content_id}/attachments/{att.id}/download")

    with sess.get(url, headers=headers, stream=True, allow_redirects=True,
                  timeout=(10, 120)) as r:                  # 跟随 302 到真实文件
        # 会话失效：BB 对未认证请求会 302 回 ADFS 登录页（HTML，非附件流）
        if _is_login_redirect(r):
            raise SessionExpired()                          # 由上层暂停队列→重登→重试
        if r.status_code == 416:                            # Range 越界 → part 脏/已满，重下
            os.remove(part)
            row.status = "pending"
            return fetch_attachment(sess, course_pk, content_id, att, dst_abs, row)
        if r.status_code not in (200, 206):
            raise TransientError(f"HTTP {r.status_code}")    # 不带 URL

        etag = r.headers.get("ETag")
        clen = r.headers.get("Content-Length")
        is_resumed = (r.status_code == 206 and resume_from > 0)
        total = (int(clen) + resume_from) if (clen and is_resumed) \
                else (int(clen) if clen else None)

        mode = "ab" if is_resumed else "wb"                 # 服务端忽略 Range 返 200 → 从头覆盖写
        if mode == "wb":
            resume_from = 0
        h = _resume_hasher(part, resume_from)               # sha256，续传时先吃已存在字节
        written = resume_from
        with open(part, mode) as f:
            for chunk in r.iter_content(64 * 1024):
                f.write(chunk); h.update(chunk); written += len(chunk)
                row.bytes_done = written                    # 节流 flush 到 DB（非每块写）

    if total is not None and written != total:              # 大小硬校验（D3）
        raise IntegrityError("size mismatch")               # 保留 .part 供续传
    os.replace(part, dst_abs)                               # 原子 rename（同分区）
    return DownloadResult(sha256=h.hexdigest(), size=written, etag=etag, remote_size=total)
```

落盘后由调用方写库：`status=complete, sha256, remote_size, etag, bytes_done=size, downloaded_at=now()`。

**完整性策略**：
- 有 `Content-Length` → 字节数硬校验（D3）。
- 无声明大小（BB/CDN 偶发不给）→ 退化为"流式无异常读完即视为完成"，仍计算并入库 `sha256`，作为后续重复探测依据。
- **跨项秒传**（可选优化，默认关）：若新附件下载后算得的 `sha256` 已存在于 `downloads`（同一文件挂多处），可用 `copyfile`/硬链接替代再次拉取——但仅作"下载后去重"，**不做下载前预测**（无法在不下载的前提下可靠拿到 BB 附件哈希）。

### 11.6 并发、限速与重试（D5）

- **课程内串行、课程间受限并发**：全局 `asyncio.Semaphore(mirror.concurrency=3)`；同一课程的附件队列在单 worker 内顺序处理，避免对同一目录树并发写。
- **节流**：每次下载请求前 `await throttle(mirror.min_interval_ms=400)`（全局共享令牌桶，跨课程也节流，对 BB 整体温和）。
- **会话复用与失效恢复**：所有请求复用 `engine.auth.get_session()`；检测到会话失效（401，或 302 跳 ADFS 登录页，见 `_is_login_redirect`）时，**暂停整个下载队列 → 调 auth 重登 → 恢复**，登录逻辑集中在一处，不散落于下载循环。
- **重试**：`TransientError`（网络/5xx/连接重置/读超时）指数退避重试 `mirror.max_attempts=4` 次（约 0.5s、2s、8s，加抖动），`attempts` 入库；超限置 `status=failed`，**不阻塞**其余文件，留待下次 mirror（D1 兜底）。`IntegrityError`（大小不符）同样可重试但保留 `.part` 以续传。
- **超时**：连接 10s / 读 120s（兼顾大 PDF/视频）；读超时按可重试处理，`.part` 保留续传。

### 11.7 `mirror()` 主流程伪代码

```python
def mirror(course, *, dest_root=None, prune=False,
           only_new=False, progress=None) -> MirrorReport:
    """
    增量镜像单门课程全部课件附件到本地。
    course: 含 course.id(course_pk)、courseId、title
    only_new: True 时仅下载本次 diff 的新增/变更项（供"自动下载"联动，见 11.8）
    返回 MirrorReport(new, updated, skipped, failed, kept, bytes)
    """
    sess = auth.get_session()                        # 复用会话，必要时自动重登
    root = dest_root or config.mirror.root
    slug = sanitize_component(course.courseId)        # courseId 含 ':' → sanitize 处理
    store.upsert_mirror_dir(course.id, course.courseId, slug)

    # 1) 全量递归内容树 → 收集 (rel_dir, content_item, attachment)
    live = []                                         # list[LiveAttachment]
    def walk(parent, folder_path, *, is_root):
        items = (bbclient.get_contents(course.id) if is_root            # 顶层 contents
                 else bbclient.get_children(course.id, parent))         # 子级 children
        for it in items:                              # it: id/title/modified/hasChildren/contentHandler
            seg = sanitize_component(it.title)
            handler = it.contentHandler.id
            if handler == "resource/x-bb-folder":
                if it.hasChildren:
                    walk(it.id, os.path.join(folder_path, seg), is_root=False)
            else:                                     # document / file / assignment → 取附件
                atts = bbclient.list_attachments(course.id, it.id)      # id/fileName/mimeType
                sub = os.path.join(folder_path, seg) if config.mirror.group_by_item else folder_path
                for att in atts:
                    live.append(LiveAttachment(content_id=it.id, modified=it.modified,
                                               att=att, rel_dir=sub))
            throttle()                                # 列树也节流（D5）
    walk(None, "", is_root=True)

    # 2) 三路 diff（11.4）→ 决定每个 live 项的动作；本地名一经分配即冻结
    known = store.load_downloads(course.id)           # {(content_id, att_id): row}
    plan  = []
    for la in live:
        key = (la.content_id, la.att.id)
        row = known.get(key)
        relpath = (row.local_relpath if row else
                   os.path.join(slug, la.rel_dir,
                                resolve_collision(course.id, la.rel_dir, la.att)))
        if row is None:
            plan.append(Action.NEW(la, relpath))
        elif _changed(row, la):                       # modified 变新 / 本地缺失或大小不符（etag,size 待下载回填）
            plan.append(Action.UPDATE(la, relpath, row))
        elif only_new:
            continue                                  # 已完成且未变，自动模式跳过
        else:
            plan.append(Action.SKIP(row))             # 计入 skipped
    # prune：known 有而 live 无 → 标记；仅 prune=True 时移 .trash/（否则计 kept）

    # 3) 执行（受限并发 + 限速 + 原子落盘 + 续传）
    report = MirrorReport()
    for action in plan:                               # 课程内顺序，跨课程由上层并发
        if action.kind == "SKIP":
            report.skipped += 1; continue
        dst_abs = os.path.join(root, action.relpath)
        os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
        row = store.upsert_download_row(
            course.id, action,
            status=("pending" if action.kind == "NEW" else "stale"))
        try:
            res = fetch_attachment(sess, course.id, action.content_id,
                                   action.att, dst_abs, row)            # 11.5
            store.mark_complete(row, res)             # status=complete + sha256/size/etag
            report.bump(action.kind, res.size)
        except SessionExpired:
            auth.relogin(); sess = auth.get_session() # 集中重登后重试本项
            continue_with_retry(action)
        except (TransientError, IntegrityError) as e:
            store.mark_failed(row, error_class(e))    # 入库错误类别，不入 URL/cookie
            report.failed += 1
        if progress: progress(report)
    return report
```

`mirror_all()` 包一层：对在读课程列表（§1/§6 过滤：term + availability + 黑/白名单）逐课调 `mirror()`，外层信号量做课程间并发（默认 3），返回汇总 `MirrorReport`。**幂等**：`mirror()` 全程基于 `downloads` 表与稳定主键，重复调用（多触发源）不会重复下载已 `complete` 且未变更的附件。

### 11.8 与监控/通知联动（"新课件自动下载"）

`engine.scanner.scan()` 已对每门课遍历内容树并 diff 出"新课件"内容事件（新 `content_id`/新 attachment、或 `modified` 变新）。为不重复请求 BB，下载联动**复用同一份内容树抓取结果**：

- scanner diff 出 `NewContent`/`UpdatedContent` 事件后，按配置 `download.auto`（`off` | `new_only` | `full`）决策：
  - `off`：只发"新课件上传"通知，不下载。
  - `new_only`（推荐默认）：对涉及的新增/变更附件以 `mirror(course, only_new=True)` **仅拉这些项**，下完在通知文案里附本地相对路径（如"已下载到 `MAT3007_Optimization/.../slides11.pdf`"）。
  - `full`：触发整课 `mirror(course)`。
- **下载与通知解耦（D7）**：附件"已下载"本身**不单独发通知**；通知只绑 scanner 的内容事件 id，沿用全局"id 已通知即打标记"机制——
  - **不漏通知**：即便 `download.auto=off` 或下载失败，"新课件"提醒仍照发（提醒不依赖下载成功）。
  - **不重通知**：同一内容事件无论被几个触发源处理、下载重试几次，提醒至多一条。
- **幂等去重（D2）**：自动下载与手动 `/bb-download` 共用 `downloads` 表与稳定主键；SessionStart / `/bb-scan` / 周期循环 / 手动 任一来源都不会重复下载同一附件。
- **非阻塞**：SessionStart 路径下自动下载在后台任务运行（与扫描同为 async，不拖慢会话）；大文件下载失败不影响待办摘要注入与"新课件"提醒。

### 11.9 暴露的命令 / MCP 接口

| 入口 | 行为 | 映射 |
|---|---|---|
| `/bb-download [课程代码\|all] [--full\|--new] [--prune]` | 手动镜像指定课程或全部在读课程 | → `mirror()` / `mirror_all()` |
| MCP `download_course(course, mode="new"\|"full")` | 对话里"帮我下 MAT3007 的新课件" | → `mirror(course, only_new=(mode=="new"))` |
| 配置 `download.auto = off\|new_only\|full` | scanner 检测到新课件后的自动下载策略（11.8） | 默认 `new_only` |
| 配置 `mirror.root / concurrency / min_interval_ms / max_attempts / group_by_item / prune` | 路径与并发/限速/重试/布局调参 | 见 11.2 / 11.6 |

> `download_course` 的 `course` 入参接受课程代码（如 `MAT3007`）或内部 `course.id`；代码经 `courseId` 前缀匹配解析到唯一在读课程，歧义时报错要求显式 id。

### 11.10 边界与失败处理小结

- **会话过期**：下载中 401 或 302→ADFS 登录页（`_is_login_redirect`）→ 暂停队列、`auth` 重登、续跑当前 `.part`（D4），登录集中在一处（11.6）。
- **磁盘满 / 无权限**：写 `.part` 抛 `OSError` → `status=failed`，报告聚合提示，不污染已完成文件（D3）。
- **附件被撤下**：内容树不再出现 → 默认保留本地（计 `kept`）；`--prune` 才移入 `.trash/<course_slug>/`。
- **文件名为空/全非法字符**：`sanitize_component` 兜底 `untitled`，`resolve_collision` 加序号，唯一索引 `idx_downloads_relpath` 防撞。
- **同名不同附件**：以 `(content_id, attachment_id)` 区分，本地名经 `resolve_collision` 加 ` (n)` 且冻结不漂移（D2）。
- **服务端忽略 Range 返 200**：自动退化为从头覆盖写（`mode="wb"`），不致拼接出错文件（D3）。
- **大文件/视频读超时**：按可重试处理，`.part` + `Range` 续传，不从头下（D4）。

---

## 12. 配置、首次设置与通知渠道

本章定义 bbwatch 的全部可配置面、配置物理存储、`/bb-setup` 首次引导流程，以及通知渠道（notifier）的插件式抽象。三条设计目标贯穿全章：**机密绝不入配置文件**（只进 macOS 钥匙串）、**配置是声明式纯数据**（引擎读它、不被它注入行为以外的逻辑）、**通知去重由 store 负责、渠道是无状态纯发射器**。本章对「绝不漏 / 绝不重复任务与通知」这一硬需求的承诺集中在 §12.6–§12.7 与 §12.11 的「首扫静默基线」。

---

### 12.1 配置分层模型

bbwatch 的「状态」分三层，物理隔离、职责互斥，不混存：

| 层 | 内容 | 物理位置 | 谁写 | 用户可手改 |
|---|---|---|---|---|
| **机密层** | BB 账号密码；（后续渠道的 SMTP 码 / bot token） | macOS 钥匙串（`keyring`） | `/bb-setup`、各渠道 | 否（经 CLI） |
| **配置层** | 扫描频率、黑白名单、下载目录、启用渠道、端口、开关 | `~/.config/bbwatch/config.toml` | `/bb-setup`、用户手改 | 是 |
| **状态层** | 已知 id 快照、任务完成状态、已通知账本、last_scan、uid/term/活动端口缓存 | `~/.local/share/bbwatch/bbwatch.db`（SQLite） | `engine.store` | 否（程序拥有） |

机密层只存**密码本身**；会话 cookie 是易失派生物，存内存 / DB（短时缓存），过期即按 §12.11[2] 重登，不进钥匙串、不进配置（与设计 §5「会话 cookie 缓存复用、过期自动重登」一致）。

**为什么 uid / term 进状态层而非配置层**：BB userId（实测形如 `_49765_1`）由登录后 `GET /learn/api/public/v1/users/me` 取回，是派生数据而非用户偏好；且 `users/{uid}/courses` 子资源**不接受 `me` 别名、必须用真实 uid**（findings §1），故权威值缓存在 DB `kv` 表（§12.6），随每次登录刷新。配置层只保留一份**只读镜像**供人排查，引擎运行时一律以 DB `kv` 为准、不读配置镜像，避免两处漂移。

**路径解析**（遵循 XDG；macOS 上 `~/.config`、`~/.local/share` 同样适用）：

```python
# engine/paths.py
import os
from pathlib import Path

APP = "bbwatch"

def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / APP

def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP

def config_path() -> Path:        return config_dir() / "config.toml"
def db_path() -> Path:            return data_dir() / "bbwatch.db"
def download_root_default() -> Path:  return Path.home() / "bbwatch" / "courses"
```

创建目录时强制 `0700`（目录）/ `0600`（`config.toml`）。配置不含机密，但黑白名单、下载路径仍不应被同机他用户读到。

---

### 12.2 配置项清单与 schema

配置用 **TOML**（标准库 `tomllib` 自 Python 3.11 可读；写回用 `tomli-w`）。完整 schema 与默认值：

```toml
# ~/.config/bbwatch/config.toml
# bbwatch 配置。机密不在此文件 —— BB 密码存 macOS 钥匙串。
# 所有 BB 原始时间为 UTC；展示统一转东八区(+08:00)。

schema_version = 1

[account]
# 登录账号（学号）。密码不在这里，存钥匙串 service="bbwatch", account=<username>。
username = "<your-student-id>"
keyring_service = "bbwatch"       # 钥匙串 service 名，一般无需改
# 以下两行为引擎登录后写回的【只读镜像】，权威值在 DB kv 表；用户勿手改。
cached_user_id = ""               # users/me 的 id，如 _49765_1
cached_term_id = ""               # 当前学期 termId，如 2550UG；空则扫描时自动判定

[scan]
# 触发模型见设计 §4。本节只配【周期扫描】；SessionStart 与手动扫描不受频率限制。
session_start = true              # 开 Claude Code 时后台扫一次（SessionStart 钩子）
periodic = false                 # 清单服务存活期间是否周期重扫
mode = "interval"               # "interval"(每 N 分钟) | "daily"(每天定点)
interval_minutes = 30            # mode=interval 时生效；硬下限 10（见 §12.5）
daily_at = ["09:00", "21:00"]    # mode=daily 时生效；本地时间 HH:MM，可多点
quiet_hours = ["23:00", "07:30"] # 此区间内【仍扫描】但不弹通知，攒到安静期外补发（见 §12.7，不丢）

[courses]
# 黑/白名单，二选一语义：
#   include 非空 -> 仅扫这些（白名单优先，exclude 被忽略）
#   include 为空 -> 扫所有【在读】课程，再去掉 exclude（黑名单）
# 标识可用 courseId(人类可读，如 "MAT3007:Optimization_L01") 或内部 course.id(如 "_17236_1")。
include = []
exclude = ["PED"]               # 例：子串命中所有体育课（见 §12.4）
# 【在读】判定沿用实测口径：courseRoleId=Student 且 availability.available ∈ {Yes, Term}
auto_only_available = true

[download]
root = "~/bbwatch/courses"       # 课件镜像根；按 课程/文件夹 结构落盘
include_ext = []                 # 例 ["pdf","pptx","docx","zip"]；空=全部（跳过大视频等用）
on_new_content = "notify"       # "notify"(只提醒) | "download"(自动增量下载) | "both"
skip_larger_than_mb = 0          # 0=不限；>0 跳过超此大小的附件并在清单页标注

[notify]
channels = ["macos"]             # 启用渠道（有序发射）。v1 仅 "macos"。
on_new_assignment = true         # 新成绩册列(带 grading.due)
on_new_announcement = true
on_new_content = true            # 新/更新内容项（content.modified 变化）
on_grade_posted = true           # status 跨入 Graded 或 score 由空变非空
coalesce_threshold = 6           # 单次扫描事件数 >= 此值则合并为一条摘要通知

[notify.macos]
backend = "auto"                # "auto" | "terminal-notifier" | "osascript"
sound = "Glass"                 # 提示音名；空=静音
open_dashboard_on_click = true   # 点击通知打开清单页（仅 terminal-notifier 支持）

[dashboard]
host = "127.0.0.1"              # 安全硬约束：强制环回，非环回值拒绝加载（见 §12.3/§12.8）
port = 8770                      # 占用则按 §12.5 顺延探测
autostart_on_session = true      # SessionStart 时拉起清单服务
open_browser = false             # 起服务后是否自动开浏览器（默认否，避免打扰）
```

> `[account].username` 与 `cached_*` 在仓库内文档/示例里**一律用占位符**（`<your-student-id>` / 空串），不写入任何真实学号或密码。

**字段级约束**（加载时校验，见 §12.3）：

| 字段 | 取值 | 校验 |
|---|---|---|
| `scan.interval_minutes` | int | `>= 10`：低于则夹到 10 并 warn（对 BB 温和是硬约束，不可绕过） |
| `scan.mode` | enum | `interval` / `daily` |
| `scan.daily_at` / `quiet_hours` | `"HH:MM"` | 正则 `^([01]\d|2[0-3]):[0-5]\d$`；`quiet_hours` 恰好两元素 |
| `courses.include` / `exclude` | list[str] | 元素为非空字符串 |
| `download.on_new_content` | enum | `notify` / `download` / `both` |
| `download.root` | path | `~` 展开；父目录可创建 |
| `notify.channels` | list[enum] | 每项须在已注册渠道集合内（v1 仅 `macos`），且非空 |
| `dashboard.port` | int | `1024–65535` |
| `dashboard.host` | const | 强制环回（`127.0.0.1`/`localhost`/`::1`），否则拒绝加载 |

---

### 12.3 配置加载、校验与迁移

引擎从不裸用 `tomllib.load` 的 dict；统一经 `load_config()` 产出冻结数据类：补默认、夹紧或报错、并把 `schema_version` 迁移到当前。

```python
# engine/config.py
from dataclasses import dataclass, field
import tomllib, tomli_w
from pathlib import Path
from .paths import config_path

CURRENT_SCHEMA = 1
REGISTERED_CHANNELS = {"macos"}          # §12.9 注册表的镜像；v1 仅此
MIN_INTERVAL_MINUTES = 10
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}

class ConfigError(Exception): ...

@dataclass(frozen=True)
class ScanCfg:
    session_start: bool = True
    periodic: bool = False
    mode: str = "interval"
    interval_minutes: int = 30
    daily_at: tuple[str, ...] = ("09:00",)
    quiet_hours: tuple[str, str] | None = None

@dataclass(frozen=True)
class CoursesCfg:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    auto_only_available: bool = True

@dataclass(frozen=True)
class NotifyCfg:
    channels: tuple[str, ...] = ("macos",)
    on_new_assignment: bool = True
    on_new_announcement: bool = True
    on_new_content: bool = True
    on_grade_posted: bool = True
    coalesce_threshold: int = 6
    macos: dict = field(default_factory=lambda: {
        "backend": "auto", "sound": "Glass", "open_dashboard_on_click": True})

    def enabled_for(self, kind: str) -> bool:
        return {
            "new_assignment":   self.on_new_assignment,
            "new_announcement": self.on_new_announcement,
            "new_content":      self.on_new_content,
            "grade_posted":     self.on_grade_posted,
        }.get(kind, True)

@dataclass(frozen=True)
class Config:
    schema_version: int
    account_username: str | None
    keyring_service: str
    cached_user_id: str | None
    cached_term_id: str | None
    scan: ScanCfg
    courses: CoursesCfg
    download_root: Path
    download_include_ext: tuple[str, ...]
    download_on_new_content: str
    download_skip_larger_than_mb: int
    notify: NotifyCfg
    dashboard_host: str
    dashboard_port: int
    dashboard_autostart: bool
    dashboard_open_browser: bool

def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    raw = {}
    if path.exists():
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    raw = _migrate(raw)              # schema 升级
    return _coerce_and_validate(raw) # 补默认、夹紧、报错、组装

def _migrate(raw: dict) -> dict:
    v = raw.get("schema_version", 0)
    if v < 1:                       # v0->v1 例：旧单数 notify_channel -> channels 列表
        n = raw.setdefault("notify", {})
        if "notify_channel" in raw:
            n.setdefault("channels", [raw.pop("notify_channel")])
        raw["schema_version"] = 1
    if raw.get("schema_version", 0) > CURRENT_SCHEMA:
        raise ConfigError(
            f"config schema_version={raw['schema_version']} 高于本程序支持的 {CURRENT_SCHEMA}；"
            "请升级 bbwatch（拒绝降级写回，以免损坏更高版本配置）")
    return raw

def _coerce_and_validate(raw: dict) -> Config:
    errs: list[str] = []
    scan = raw.get("scan", {})
    iv = int(scan.get("interval_minutes", 30))
    if iv < MIN_INTERVAL_MINUTES:    # 不报错，夹紧 + warn（不让用户绕过限速）
        log.warning("interval_minutes=%d 低于下限，已夹到 %d", iv, MIN_INTERVAL_MINUTES)
        iv = MIN_INTERVAL_MINUTES

    host = raw.get("dashboard", {}).get("host", "127.0.0.1")
    if host not in _LOOPBACK:
        errs.append(f"dashboard.host 必须为环回地址，得到 {host!r}")

    chans = tuple(raw.get("notify", {}).get("channels", ["macos"]))
    if not chans:
        errs.append("notify.channels 不能为空")
    bad = [c for c in chans if c not in REGISTERED_CHANNELS]
    if bad:
        errs.append(f"未知通知渠道 {bad}（已注册: {sorted(REGISTERED_CHANNELS)}）")

    on_nc = raw.get("download", {}).get("on_new_content", "notify")
    if on_nc not in ("notify", "download", "both"):
        errs.append(f"download.on_new_content 非法: {on_nc!r}")
    # ... daily_at/quiet_hours 正则、port 区间等其余校验略 ...

    if errs:
        raise ConfigError("配置校验失败:\n  - " + "\n  - ".join(errs))
    # 组装 Config（逐字段搬运略）
    ...
```

**写回经唯一原子出口**（`/bb-setup` 与清单页「设置」面板共用），并发不撕裂：

```python
def save_config(cfg: Config, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".toml.tmp")
    with open(tmp, "wb") as f:
        tomli_w.dump(_config_to_toml_dict(cfg), f)
    tmp.chmod(0o600)
    tmp.replace(path)               # 原子替换
```

**首次无配置**：文件不存在时 `load_config()` 返回全默认 `Config`（`account_username=None`）。所有命令/MCP 前置检查据 `account_username is None` 判「未 setup」，提示先跑 `/bb-setup`，不静默跑空扫描。

---

### 12.4 黑白名单匹配规则

课程过滤在 `scanner` 取到课程列表后、拉取明细前生效，决定遍历哪些课。匹配同时认两种实测标识：人类可读 `courseId`（如 `MAT3007:Optimization_L01`）与内部 `course.id`（如 `_17236_1`）。

```python
# engine/filters.py
import re
_INNER = re.compile(r"^_\d+_\d+$")        # 内部 id 形如 _17236_1

def course_selected(course: dict, cfg) -> bool:
    cid_human = course.get("courseId", "")
    cid_inner = course.get("course", {}).get("id", "")

    def match(pat: str) -> bool:
        if _INNER.match(pat):              # 内部 id：精确匹配
            return pat == cid_inner
        return pat.lower() in cid_human.lower()   # 人类可读：子串(大小写不敏感)

    if cfg.courses.include:                # 白名单优先
        return any(match(p) for p in cfg.courses.include)
    if any(match(p) for p in cfg.courses.exclude):  # 否则黑名单
        return False
    if cfg.courses.auto_only_available:    # 实测口径：role=Student 且 availability ∈ {Yes, Term}
        avail = course.get("availability", {}).get("available")
        role = course.get("courseRoleId")
        return role == "Student" and avail in ("Yes", "Term")
    return True
```

`exclude = ["PED"]` 子串命中所有体育课（如 `PED1001:...`）；`include = ["MAT"]` 命中所有数学课。内部 id 用收紧的正则 `^_\d+_\d+$` 判定，避免课程名里恰含下划线时被误当内部 id。

> **与「绝不漏」的契约**：黑白名单只改「扫哪些课」，绝不改「某课内如何 diff」。被排除的课不扫、不通知；**重新纳入后下次扫描会把其累计的全部新项一次补齐**——因为 diff 是基于稳定 id 的全量比对、而非时间窗口增量（findings §6）。

---

### 12.5 端口、频率与限速的运行期解析

- **端口探测**：`dashboard.port` 被占用时，从配置值起向上探测最多 20 个端口（`8770→8771→…`），命中即用，并把**实际端口**写入 DB `kv['dashboard_port_active']`，供 SessionStart 注入摘要时给出正确 URL。**不回写 `config.toml`**（占用是临时环境，不污染用户意图）。
- **频率夹紧**：`interval_minutes` 已在加载时夹到 `>= 10`。`daily_at` 多点按本地时间排序、去重。周期循环用**单调时钟**计算下次触发；错过的点（休眠唤醒）只补**一次**、不堆积。
- **请求限速与配置无关**：写死在 `bbclient`（请求间最小延时 + 复用会话 + 分页节流），**不暴露为配置项**，避免用户调激进——属设计 §8「好公民」硬约束。日历查询的 **≤16 周窗口**翻页同样由 `bbclient` 内部保证覆盖整学期（findings §2），不交由用户配置，杜绝因窗口配错而漏 ddl。

---

### 12.6 状态层中与配置 / 通知相关的 SQLite DDL

完整快照 / 任务表见设计第 6、7 章。这里给出**本章直接依赖**的两张表：键值缓存 `kv` 与通知去重账本 `notified`。

```sql
-- engine/store/schema.sql （本章相关片段；与全局 schema 同库 bbwatch.db）
PRAGMA journal_mode = WAL;          -- 多触发源并发读写更稳

-- 派生状态与运行期缓存（非用户配置）
CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
-- 典型 key: 'user_id'(_49765_1) / 'term_id'(2550UG) / 'dashboard_port_active'(8771)

-- 通知去重账本：每个【值得通知的事实状态】对每个渠道只发一次
CREATE TABLE IF NOT EXISTS notified (
    event_key    TEXT PRIMARY KEY,   -- 事件指纹，稳定且幂等（见下表）
    kind         TEXT NOT NULL,      -- new_assignment|new_announcement|new_content|grade_posted
    course_id    TEXT,               -- 内部 course.id，便于按课聚合
    ref_id       TEXT,               -- column id / announcement id / content id
    channel_mask TEXT NOT NULL DEFAULT '',  -- 已成功送达的渠道集合，逗号分隔，如 'macos'
    deferred     INTEGER NOT NULL DEFAULT 0, -- 1=quiet_hours 内待补发（见 §12.7）
    title        TEXT,               -- 标题快照（排查用，不含敏感数据）
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_notified_course   ON notified(course_id);
CREATE INDEX IF NOT EXISTS idx_notified_deferred ON notified(deferred);
```

**`event_key` 生成规则**（保证「绝不重复」，与设计 §6.1 全量 diff 一致）：

| 事件 | event_key | 「再次通知」的唯一条件 |
|---|---|---|
| 新作业 | `asgn:{course_id}:{column_id}` | 永不重复（新列才有新 key） |
| 新公告 | `ann:{course_id}:{announcement_id}` | 永不重复（新 id 才有新 key） |
| 新课件 | `cont:{course_id}:{content_id}:{modified_iso}` | content `modified` 变化 → 新 key → 作为「更新」再提醒一次 |
| 出分 | `grade:{course_id}:{column_id}` | 仅当 status 跨入 `Graded` 或 score 由空变非空时才**生成**此 key |

关键点：

- 出分 key **不含** score 值——避免教师改分时 key 变化导致二次轰炸；它只在「首次出分」这一状态跃迁时被生成一次。「未交→待批」（`None→NeedsGrading`）**不生成** key、不通知（学生自己交的，无需提醒）。
- 课件 key **含** `modified`，故同一课件被改动重传能再提醒一次，而「已通知且未变」永不重复。`modified` 取 BB 原始 ISO 串，规整为稳定格式（去毫秒、统一 `Z`）后入 key，避免同值不同格式产生伪新 key。
- `channel_mask` 支持「某渠道当时发失败 / 当时未启用 → 下次只补发该渠道，不重发已成功渠道」。

---

### 12.7 通知去重与渠道的职责边界

**铁律：渠道是无状态发射器，去重在 store。** 编排算法：

```python
# engine/notifier/dispatch.py （要点）
def notify(events: list[Event], cfg: Config, store) -> None:
    # 1) 渠道级开关过滤（notify.on_*）。被关掉的类别根本不进入去重账本，
    #    日后开启时仍会被全量 diff 重新检出 —— 关开关不会造成「永久漏」。
    events = [e for e in events if cfg.notify.enabled_for(e.kind)]
    if not events:
        return

    # 2) store 去重：在 BEGIN IMMEDIATE 事务内，剔除 (event_key 已存在
    #    且目标渠道已在 channel_mask) 的事件；返回每事件【尚未送达的渠道集合】。
    with store.tx_immediate():
        pending = store.filter_unnotified(events, channels=cfg.notify.channels)
        if not pending:
            return
        # 3) 安静时段：仅【缓存进 DB 并标 deferred=1】，本次不发射。
        #    deferred 落库 -> 进程退出也不丢；安静期外的任一次扫描会先 flush。
        if in_quiet_hours(cfg.scan.quiet_hours):
            store.defer(pending)
            return

    # 4) 先 flush 历史 deferred，再处理本次 pending（合并到同一发射批）
    pending = store.take_deferred(channels=cfg.notify.channels) + pending

    # 5) 合并：超阈值则把多事件并成一条摘要；但【每个底层 event 仍逐一 mark】，
    #    摘要只是展示形态，不改变去重粒度（避免合并导致个别事件漏标）。
    batches = summarize(pending) if len(pending) >= cfg.notify.coalesce_threshold else pending

    # 6) 逐渠道发射；仅【送达成功】才回写 channel_mask（失败保留，下次自然重试该渠道）。
    for ch in build_channels(cfg):                 # §12.9
        for item in iter_events(batches):          # 摘要也遍历其覆盖的每个 event
            if ch.name in store.delivered_channels(item.event_key):
                continue                            # 该渠道已送达，跳过
            try:
                ch.send(to_notification(item, cfg))
                store.mark_notified(item, channel=ch.name)   # 幂等 upsert，并 deferred=0
            except NotifierError as e:
                log.warning("渠道 %s 发送失败，下次扫描重试: %s", ch.name, e)  # 不写 mask
```

**幂等由四处叠加保证**：

1. **稳定 `event_key`**（不随时间窗口或非状态字段变化）。
2. **`notified` 主键**对 `event_key` 去重。
3. **`mark_notified` 仅在送达成功后回写** `channel_mask` —— 失败的渠道下次重试，成功的不重发。
4. **`BEGIN IMMEDIATE` 事务**串行化 `filter_unnotified` / `defer` / `mark_notified`：多触发源（SessionStart / 手动 / 周期）共用同一 `bbwatch.db`，并发时不会两个扫描同时发同一条。

**与「绝不漏」的契约**：quiet_hours 内的通知**落 DB（`deferred=1`）而非内存**，进程在安静期被关闭也不丢；安静期外的下一次扫描会先 `take_deferred` 再发。渠道发送失败不写 mask、不计入已通知，因此「发失败=未通知」，下次必重试。

---

### 12.8 通知渠道抽象接口（notifier 插件式）

渠道实现一个最小协议；新增邮件 / Telegram 只需新增类并注册，编排层与 store 不改。

```python
# engine/notifier/base.py
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

@dataclass(frozen=True)
class Notification:
    title: str                 # 已转东八区、人话化，如 "MAT3007 新作业: Homework 5"
    body: str                  # 如 "截止 2026-07-05 23:59 (+08:00) · 满分 100"
    url: str | None = None     # 点击跳转，通常是清单页 http://127.0.0.1:<port>/#course=<id>
    kind: str = ""             # 事件类型，渠道可据此选图标/声音/分组
    urgency: str = "normal"    # "low"|"normal"|"high"（high 例如 24h 内 ddl）

class NotifierError(Exception): ...

@runtime_checkable
class Channel(Protocol):
    name: str                              # 注册名，对应 notify.channels 中的字符串
    def available(self) -> bool: ...       # 本机/本配置下是否可用（探测二进制/凭据）
    def send(self, n: Notification) -> None: ...   # 失败抛 NotifierError；成功静默返回
```

约定：`send` **同步、快**（macOS 通知是即时的）；耗时渠道（SMTP）自设超时并在超时抛 `NotifierError`，绝不拖慢扫描。`send` 与所有渠道实现**绝不打印 / 记录密码、token、cookie**（设计 §8）；`Notification` 字段也只放展示用文本，不携带凭据。

---

### 12.9 v1 macOS 渠道实现

v1 仅 `macos` 渠道，优先 `terminal-notifier`（支持点击跳 URL、自定义分组），不可用则回退 `osascript`（系统自带，但**无法点击跳转**）。

```python
# engine/notifier/macos.py
import shutil, subprocess, sys
from .base import Notification, NotifierError

class MacOSChannel:
    name = "macos"
    def __init__(self, cfg_macos: dict):
        self._backend = cfg_macos.get("backend", "auto")
        self._sound = cfg_macos.get("sound", "Glass")
        self._open_on_click = cfg_macos.get("open_dashboard_on_click", True)
        self._tn = shutil.which("terminal-notifier")

    def available(self) -> bool:
        return sys.platform == "darwin"     # osascript 必在；terminal-notifier 仅增强

    def _use_tn(self) -> bool:
        if self._backend == "osascript":          return False
        if self._backend == "terminal-notifier":  return bool(self._tn)
        return bool(self._tn)                      # auto

    def send(self, n: Notification) -> None:
        try:
            if self._use_tn():
                args = ["terminal-notifier", "-title", n.title, "-message", n.body,
                        "-group", f"bbwatch.{n.kind or 'misc'}"]   # 同类通知合并/替换
                if self._sound:                       args += ["-sound", self._sound]
                if n.url and self._open_on_click:     args += ["-open", n.url]
                subprocess.run(args, check=True, timeout=10, capture_output=True)
            else:
                script = (f'display notification {_q(n.body)} with title {_q(n.title)}'
                          + (f' sound name {_q(self._sound)}' if self._sound else ''))
                subprocess.run(["osascript", "-e", script],
                               check=True, timeout=10, capture_output=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            raise NotifierError(str(e)) from e

def _q(s: str) -> str:
    # AppleScript 字符串转义：反斜杠在前，再双引号
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
```

`available()` 仅判平台；`terminal-notifier` 缺失不致渠道不可用，只是退化到 osascript（不可点击）。`-group` 让 BB 的同类通知在通知中心合并，避免堆叠刷屏（与 `coalesce_threshold` 互补：前者是系统侧合并，后者是发射前合并）。

**渠道注册表**（配置字符串 → 实例），与 `REGISTERED_CHANNELS` 保持同步：

```python
# engine/notifier/registry.py
def build_channels(cfg) -> list:
    out = []
    for name in cfg.notify.channels:
        if name == "macos":
            ch = MacOSChannel(cfg.notify.macos)
        # elif name == "email":    ch = EmailChannel(...)      # §12.10
        # elif name == "telegram": ch = TelegramChannel(...)
        else:
            raise ConfigError(f"未知渠道 {name!r}")    # 加载期已挡，此处兜底
        if ch.available():
            out.append(ch)
        else:
            log.warning("渠道 %s 在本机不可用，已跳过（其事件保留 deferred 待下次重试）", name)
    return out
```

> **不可用渠道不丢事件**：若某启用渠道 `available()` 为假而被跳过，该渠道**不会被写入任何事件的 `channel_mask`**，因此一旦它恢复可用，下次扫描 `filter_unnotified` 仍判其「未送达」并补发——与「绝不漏」一致。

**时区转换**：BB 所有时间为 UTC（实测 `grading.due` 形如 `2026-06-30T15:59:00.000Z`）。`to_notification` **统一在编排层 `+08:00` 转换**后再填 `body`，渠道不做时区运算。例：`Homework 5` due `2026-07-05T15:59:00.000Z` → body `"截止 2026-07-05 23:59 (+08:00)"`。

---

### 12.10 后续渠道扩展点（邮件 / Telegram）

新增渠道是**纯加法**，不触动 store / 编排 / 配置加载主干，仅四处：

1. 新建 `engine/notifier/email.py`（或 `telegram.py`），实现 `Channel` 协议。
2. `registry.build_channels` 增一个 `elif`。
3. `config.REGISTERED_CHANNELS` 加入新名（与上一步同步）。
4. config schema 增一节。**机密仍走钥匙串，不入文件**：

```toml
[notify.email]
to = "you@example.com"
smtp_host = "smtp.example.com"
smtp_port = 587
username = "<smtp-username>"
# 无 password 字段。SMTP 应用码存钥匙串 service="bbwatch.email", account=<username>，
# 由 EmailChannel 经 keyring.get_password("bbwatch.email", username) 读取。

[notify.telegram]
chat_id = "<chat-id>"
# bot token 存钥匙串 service="bbwatch.telegram"；配置文件不出现 token。
```

去重对新渠道天然生效：`notified.channel_mask` 已是**渠道集合**，`filter_unnotified(channels=...)` 为新渠道独立判定「是否已送达」。因此加邮件后——历史事件**不会**被重发（macOS 已在各自 mask 内），而此后的新事件会**同时**发 macOS 与邮件，各自独立标记。

---

### 12.11 `/bb-setup` 首次设置流程

`/bb-setup` 是 Claude Code 命令（薄壳），调用引擎 `engine.setup.run_setup()`。交互在 Claude Code 会话内进行（命令产出提示、引擎做事）。机密只经 stdin / 钥匙串，**绝不写入配置文件、不进会话日志、不回显**。

**流程（状态机）**：

```
[1] 录账号
    - 提示输入 username（学号）。
    - 提示输入 password（不回显）；引擎读后立即
      keyring.set_password("bbwatch", username, pw)，内存变量随即清零；
      绝不写 config、绝不 log、绝不回显。

[2] 验证登录（最关键的早失败点）
    - engine.auth.login(username)：从钥匙串取密码，跑 ADFS OAuth2 授权码流
      （GET sts.cuhk.edu.cn/adfs/oauth2/authorize?client_id=4b71b947-...&redirect_uri=
       …/bb-SSOIntegrationOAuth2-BBLEARN/authValidate/getCode → POST 表单
       UserName/Password/Kmsi → 302 回 BB getCode 写会话 cookie）。
      传输层用 curl_cffi(浏览器 TLS 指纹)，尊重环境代理(本机 127.0.0.1:7890)；失败回退子进程 curl。
    - 成功：GET /learn/api/public/v1/users/me 取 id(如 _49765_1) -> 写 DB kv['user_id']
      与 config.cached_user_id(只读镜像)。
    - 失败分支：
        * 凭据错  -> 删除刚写入的钥匙串条目，回 [1] 重录（不留错密码）。
        * TLS/网络 -> 提示检查代理(7890)/网络，可重试，【不删凭据】。

[3] 选学期 + 选课程
    - GET /learn/api/public/v1/terms?limit=100 取学期；默认选当前 term(如 2550UG) 写 cached_term_id。
    - GET /learn/api/public/v1/users/{uid}/courses?expand=course&limit=100（按 paging.nextPage 翻页；
      用真实 uid，不能用 me）。按实测口径筛【在读】(Student 且 availability∈{Yes,Term}) 列给用户勾选：
        ☑ MAT3007:Optimization_L01
        ☑ MAT3350:Information Theory_L01
        ☐ PED…（默认不勾，体育）
    - 映射为 config.courses.include 或 exclude（默认：include 空 + exclude 选中要排除项）。

[4] 设频率与渠道
    - 周期扫描：是否开 periodic；mode=interval(默认 30min，下限 10) 或 daily(默认 09:00)。
    - SessionStart 自动扫：默认开。
    - 渠道：v1 仅 macos；探测 terminal-notifier 是否存在并告知（无则 osascript 回退，不可点击跳转）。
    - 下载根目录：默认 ~/bbwatch/courses，可改。

[5] 落盘 + 基线自检
    - save_config(cfg)（原子写，0600）。
    - scanner.scan(baseline=True)：初始化快照并【静默建基线】（见下）。
    - 起清单页(127.0.0.1:port)，输出实际 URL；提示就绪。
```

**首扫静默基线**（避免首次设置即被几十条历史项轰炸——直接关系「绝不重复」）：`run_setup` 末尾调 `scanner.scan(baseline=True)`。`baseline=True` 时，diff 出的所有「新」事件**不进 notifier**，而是在**与写快照同一个事务内**把每个 `event_key` 以 `mark_notified(item, channel="__baseline__")` 全部记入 `notified`（`channel_mask='__baseline__'`，非真实渠道名）。要点：

- **同事务**保证「快照已建立」与「基线已标记」原子完成；若中途崩溃则整体回滚，重跑 setup 不会出现「快照有、基线无 → 把存量当新项全发一遍」。
- `__baseline__` 不在任何真实渠道集合内，故首扫后**真实渠道仍判这些事件「未送达」吗？** 否——`filter_unnotified` 的语义是「event_key 已存在即视为已知」：基线项的 `event_key` 已落 `notified`，**此后真实扫描只对 `event_key` 全新（= 基线之后新增）的项发通知**。这与 findings §6 的「全量 diff、稳定 id」完全兼容：基线之后任何新 id 仍被检出。
- 但出分（`grade:`）是**状态跃迁**类：基线时把当前已 `Graded` 的列记入 `notified`，故已出分的历史成绩不重报；基线后**新**出分（新生成 `grade:` key）仍会通知。

**重跑 `/bb-setup`**：幂等，进入「修改模式」，逐节展示当前值供改。改密走 [1]+[2]（重新验证）。**不重置 `notified`**（不重炸历史）。子动作：
- `/bb-setup --reset-credentials`：仅换密码（走 [1][2]）。
- `/bb-setup --reselect-courses`：仅改名单。新纳入的课在下次扫描经全量 diff 把其累计新项一次补齐（不重置基线、不漏）。

---

### 12.12 配置与设计其余部分的契约小结

- **触发模型（设计 §4）**：`scan.session_start` / `scan.periodic` / `mode` / `interval_minutes` / `daily_at` 决定三类周期触发的开关与频率；手动 `/bb-scan`、MCP `scan_now` 无视频率、随时可跑。
- **绝不漏（设计 §6.1 / findings §6）**：配置不影响「用稳定 id 全量 diff」的根基。黑白名单只改「扫哪些课」；被排除后重新纳入、关掉的通知类别重新开启、临时不可用的渠道恢复——下次扫描都会把累计项补齐，因为 diff 是全量、非时间窗口。日历 ≤16 周窗口翻页由 `bbclient` 内部保证、不交用户配置，杜绝配错漏 ddl。
- **绝不重复（设计 §8）**：去重账本 `notified` 与渠道解耦；稳定 `event_key` + 主键 + 仅成功回写 `channel_mask` + `BEGIN IMMEDIATE` 串行化。改配置（加渠道、改频率、改名单）不会重发已通知事件；首扫静默基线避免初次轰炸。
- **安全（设计 §8）**：密码与各渠道凭据只在钥匙串（service 前缀 `bbwatch`）；`config.toml` 与 `bbwatch.db` 均不含明文机密，日志 / 通知 / 文档示例均不出现真实账号或密码；`dashboard.host` 强制环回，配置层无法把清单页暴露到公网。

---

## 13. 测试、验证与分阶段开发计划

本章给出 bbwatch 的可执行测试策略与里程碑计划。最高优先级的非功能需求是**绝不漏（no miss）**与**绝不重复（no duplicate）**;因此本章把这两条提升为一等公民——既有专门的不变量(invariant)断言,也有对应的崩溃/翻页/窗口/状态机边界用例。所有涉及 BB 的断言均锚定 `bb_findings.md` 的实测事实:稳定 id 全量 diff、`grading.due` 为 UTC(形如 `2026-06-30T15:59:00.000Z`)、日历窗口 ≤16 周、`status ∈ {None, NeedsGrading, Graded}`、日历项**全为 `type=GradebookColumn`**、`paging` 字段仅在有下页时出现(含 `nextPage`)、`me` 别名在 `users/{uid}/courses` 子资源不可用须用真实 uid `_49765_1`。

### 13.1 测试金字塔与目录布局

```
tests/
  unit/                     # 纯函数, 无网络无 DB(或内存 DB)
    test_diff.py            #   稳定 id 全量 diff
    test_status_machine.py  #   None→NeedsGrading→Graded 状态机
    test_pathmap.py         #   内容树 → 本地镜像路径映射
    test_time.py            #   UTC→+08:00, due 解析(跨日)
    test_paging.py          #   nextPage 翻页 / 16 周窗口切分
    test_course_filter.py   #   在读过滤(term + availability + 黑白名单)
  contract/                 # 录制的 BB JSON fixtures 离线回放
    fixtures/               #   见 13.3, 脱敏后的真实响应
    test_bbclient_parse.py  #   字段解析契约
    test_scanner_replay.py  #   端到端 diff(喂 fixtures, 不触网)
  e2e/                      # 起本地清单服务, HTTP 点查
    test_dashboard_api.py
    conftest.py             # tmp_path SQLite, FakeBBClient, freezegun 时钟
pytest.ini
```

技术约束:测试**绝不触达真实 BB**,也**绝不读钥匙串**。网络层通过依赖注入替换为 `FakeBBClient`(返回 fixtures),`engine.auth.get_session` 在 e2e 中被 monkeypatch 为返回哑会话。时间用 `freezegun` 冻结,使 `due` 相对断言可复现。

### 13.2 被测核心契约(签名锚点)

测试针对以下引擎签名编写。这些签名是断言的"被测面",与 §5 的模块职责一致。

```python
# engine/store.py
def diff_ids(known: set[str], fetched: set[str]) -> DiffResult: ...
#   DiffResult(added: set[str], removed: set[str], unchanged: set[str])

def upsert_snapshot(conn, course_id: str, kind: str,
                    items: list[Item]) -> list[Event]:
    """事务内: 计算 added/changed, 写快照, 仅对真正新增/变更返回 Event。
       幂等: 同一 (kind, item_id, content_hash) 重复调用返回 []。"""

# engine/scanner.py
def scan(conn, client: BBClient, now: datetime) -> list[Event]: ...

# engine/status.py
def reduce_status(prev: GradeState | None,
                  cur: GradeState) -> StatusTransition | None:
    """仅当 (status, score) 有'值得通知'的跃迁时返回 Transition, 否则 None。"""

# engine/courses.py
def select_active(memberships: list[Membership],
                  terms: dict[str, Term],
                  allow: set[str], deny: set[str]) -> list[Course]:
    """role=Student 且 availability ∈ {Yes, Term} → 在读; 再过黑/白名单。"""

# engine/downloader.py
def local_path(course: Course, folder_chain: list[str],
               file_name: str) -> Path: ...
def needs_download(local: Path, remote_modified: datetime,
                   known_modified: datetime | None) -> bool: ...
```

`Event` 落库与去重的唯一键见 13.5 的 DDL(`event(dedup_key)` 唯一约束)。

### 13.3 契约测试:用录制的 BB JSON fixtures 离线回放

**录制方式**:一次性用真实会话抓取各端点响应,经脱敏脚本(替换学号 `125090374`/姓名 `梁博文`/uid `_49765_1`、剥离 cookie/token、保留结构与字段名)落为 fixtures。每个文件对应一个实测端点:

```
tests/contract/fixtures/
  users_me.json                  # users/me → id=_49765_1 (uid 来源)
  courses_expand.json            # users/{uid}/courses?expand=course  (含 paging.nextPage)
  courses_expand_page2.json      # 第二页(验证翻页拼接)
  terms.json                     # terms?limit=100  (识别当前学期)
  columns_mat3007.json           # gradebook/columns  (含 Homework 1/2/4 + Weighted Total)
  column_user_hw1_graded.json    # columns/{col}/users/{uid}  status=Graded score=100
  column_user_hw2_needsgrading.json
  column_user_hw4_none.json      # status=None due=2026-06-30T15:59:00.000Z
  calendar_window1.json          # calendars/items 一个 ≤16 周窗口, 全 type=GradebookColumn
  calendar_window2.json          # 相邻窗口(验证跨窗口不漏不重)
  announcements_mat3007.json     # 含 "提醒 Assignment 3" created=2026-06-23
  contents_top.json              # 顶层全 x-bb-folder, hasChildren=true
  contents_children_slides.json  # 子级 x-bb-document/x-bb-file
  attachments_doc.json           # fileName/mimeType/id
  paging_nopage.json             # 无 paging 字段的响应(末页边界)
```

契约断言(对齐实测字段名,任何字段更名/改型即测试红):

- `test_parse_columns`:从 `columns_mat3007.json` 解析出 `id/name/grading.due/contentId/score.possible/grading.type`;**断言 `Weighted Total` / `Total`(无 `grading.due`)被过滤**,只剩带 due 的作业列。
- `test_parse_due_is_utc`:`grading.due == "2026-06-30T15:59:00.000Z"` 解析为带 tzinfo 的 UTC `datetime`,转 +08:00 后日期为 `2026-07-01`(跨日,验证不能用裸日期截断)。
- `test_parse_grade_status`:三个 `column_user_*.json` 分别解析为 `Graded/score=100`、`NeedsGrading/score=None`、`None/score=None`。
- `test_calendar_items_all_gradebookcolumn`:`calendar_window*.json` 全部项 `type == "GradebookColumn"` 且带 `end`(=ddl);解析器**不假设**存在 course event/office hour 项(本实例无)。
- `test_paging_only_when_next`:`paging_nopage.json` 无 `paging` 键时,翻页器**不**构造下一页请求(对齐"`paging` 字段仅在有下一页时出现")。
- `test_uid_required_not_me`:断言 bbclient 拉 courses 时 URL 用真实 uid `_49765_1`(由 `users/me` 先取得)而非 `me`(实测 `me` 在该子资源不可用)。
- `test_select_active_filters_memberships`:喂入 19 门 membership 跨 3 学期,断言 `select_active` 恰返回 **17 门在读**(role=Student 且 availability ∈ {Yes, Term}),已结课/已退课不进扫描面。

回放型 scanner 测试 `test_scanner_replay.py`:把上述 fixtures 装进 `FakeBBClient`,跑 `scan()`,断言产出的 `Event` 集合与黄金期望一致——这是离线、确定性的"端到端 diff",不触网。

### 13.4 单元测试:diff / 状态机 / 路径映射

**(A) 稳定 id 全量 diff** — 对齐"不漏的基础 = 全量 diff 而非时间窗增量"。

```python
def test_diff_added_only_returns_new():
    r = diff_ids(known={"_1_1","_2_1"}, fetched={"_1_1","_2_1","_3_1"})
    assert r.added == {"_3_1"} and r.unchanged == {"_1_1","_2_1"}

def test_diff_catches_up_after_multi_day_gap():
    # 多天没扫, 一次性出现 3 个新 column → 全部检出(无时间窗丢失)
    r = diff_ids(known={"_1_1"}, fetched={"_1_1","_4_1","_5_1","_6_1"})
    assert r.added == {"_4_1","_5_1","_6_1"}

def test_diff_removed_not_emitted_as_new():
    # 教师删列: removed 不应产生"新作业"事件
    r = diff_ids(known={"_1_1","_2_1"}, fetched={"_1_1"})
    assert r.added == set() and r.removed == {"_2_1"}
```

**(B) 状态机** — `reduce_status` 只在"该变时"返回跃迁,实现"出分提醒只发一次、已交不重复提醒"。

合法跃迁表(其余皆返回 `None`):

| prev | cur | 通知? | 事件类型 |
|---|---|---|---|
| `None`(无记录,首见已交) | `NeedsGrading` | 否(仅更新清单状态) | — |
| `None`/`NeedsGrading` | `Graded`(score 由空变非空) | **是** | `GRADE_RELEASED` |
| `Graded` | `Graded`(同分) | 否 | — |
| `Graded` | `Graded`(改分,score 变化) | **是** | `GRADE_CHANGED` |
| `Graded` | `NeedsGrading`/`None`(撤分回退) | 否(仅更新清单状态) | — |
| 任意 | 同值 | 否 | — |

```python
def test_grade_release_notifies_once():
    t = reduce_status(GradeState("NeedsGrading", None),
                      GradeState("Graded", 100))
    assert t.event_type == "GRADE_RELEASED"

def test_graded_idempotent_no_renotify():
    assert reduce_status(GradeState("Graded",100),
                         GradeState("Graded",100)) is None

def test_submitted_does_not_trigger_grade_event():
    assert reduce_status(GradeState("None",None),
                         GradeState("NeedsGrading",None)) is None

def test_regrade_emits_change():
    t = reduce_status(GradeState("Graded",80),GradeState("Graded",100))
    assert t.event_type == "GRADE_CHANGED"

def test_grade_retraction_is_silent():
    # 教师撤回成绩 Graded→NeedsGrading: 不发"出分", 也不误判为新提交
    assert reduce_status(GradeState("Graded",100),
                         GradeState("NeedsGrading",None)) is None
```

**(C) 路径映射** — 内容树 → 本地镜像;稳定路径 + 按 `modified` 增量。

```python
def test_local_path_mirrors_folder_chain():
    p = local_path(Course("MAT3007:Optimization_L01"),
                   ["Lecture Slides","Annotated"], "slides_01.pdf")
    assert p == Path("MAT3007_Optimization/Lecture_Slides/Annotated/slides_01.pdf")

def test_needs_download_when_remote_newer():
    assert needs_download(Path("x.pdf"),
                          remote_modified=dt("2026-06-28T10:00:00Z"),
                          known_modified=dt("2026-06-20T10:00:00Z")) is True

def test_no_redownload_when_unchanged():
    assert needs_download(Path("x.pdf"),
                          remote_modified=dt("2026-06-20T10:00:00Z"),
                          known_modified=dt("2026-06-20T10:00:00Z")) is False

def test_path_sanitizes_unsafe_chars():
    # 文件名含 "/" ":" → 不得越出镜像根; 防路径穿越
    p = local_path(Course("X:Y"), ["a/b","..",], "q:1.pdf")
    assert ".." not in p.parts and not p.is_absolute()
```

### 13.5 存储层 DDL(去重与不丢的物理基础)

去重不是靠应用层判断,而是靠**数据库唯一约束**兜底:即便扫描逻辑被并发/重试触发两次,`INSERT ... ON CONFLICT(dedup_key) DO NOTHING` 也保证每个事件只通知一次。

```sql
-- 已知项快照: 全量 diff 的"已知集合"
CREATE TABLE IF NOT EXISTS known_item (
  course_id    TEXT NOT NULL,
  kind         TEXT NOT NULL,            -- 'column' | 'announcement' | 'content' | 'attachment'
  item_id      TEXT NOT NULL,            -- 稳定 BB id, 如 _17236_1
  content_hash TEXT,                     -- 内容"变更"检测(如 content.modified 派生)
  first_seen   TEXT NOT NULL,            -- UTC ISO8601
  PRIMARY KEY (course_id, kind, item_id)
);

-- 作业完成/出分状态: per-user column status
CREATE TABLE IF NOT EXISTS grade_state (
  course_id TEXT NOT NULL,
  column_id TEXT NOT NULL,
  status    TEXT NOT NULL,               -- None | NeedsGrading | Graded
  score     REAL,                        -- 出分后非空
  due_utc   TEXT,                        -- grading.due (UTC)
  updated   TEXT NOT NULL,
  PRIMARY KEY (course_id, column_id)
);

-- 任务清单(含手动勾选, 线下作业靠它)
CREATE TABLE IF NOT EXISTS task (
  course_id      TEXT NOT NULL,
  column_id      TEXT NOT NULL,
  title          TEXT NOT NULL,
  due_utc        TEXT,
  auto_done      INTEGER NOT NULL DEFAULT 0,  -- 由 status 推断(NeedsGrading/Graded → 1)
  manual_done    INTEGER,                     -- 用户勾选; 非空则覆盖 auto
  PRIMARY KEY (course_id, column_id)
);

-- 事件/通知去重: dedup_key 唯一 = "绝不重复"的物理保证
CREATE TABLE IF NOT EXISTS event (
  dedup_key   TEXT PRIMARY KEY,          -- 见下方构造规则
  event_type  TEXT NOT NULL,             -- NEW_ASSIGNMENT|NEW_ANNOUNCEMENT|NEW_CONTENT|GRADE_RELEASED|GRADE_CHANGED
  course_id   TEXT NOT NULL,
  ref_id      TEXT NOT NULL,
  payload     TEXT NOT NULL,             -- JSON, 用于通知展示
  created     TEXT NOT NULL,
  notified    INTEGER NOT NULL DEFAULT 0 -- 通知发送成功后置 1
);

-- 扫描水位线(诊断 + 临近未扫提示, 非增量依据)
CREATE TABLE IF NOT EXISTS scan_log (
  scan_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  started    TEXT NOT NULL,
  finished   TEXT,                        -- NULL = 崩溃在扫描中
  ok         INTEGER
);
```

`dedup_key` 构造规则(决定"同一件事=同一 key"):
- 新作业:`f"NEW_ASSIGNMENT:{course_id}:{column_id}"`
- 新公告:`f"NEW_ANNOUNCEMENT:{course_id}:{announcement_id}"`
- 新课件:`f"NEW_CONTENT:{course_id}:{content_id}:{modified_iso}"`(`modified` 入 key → 更新课件可再次提醒,但同一版本不重复)
- 出分:`f"GRADE_RELEASED:{course_id}:{column_id}"`(列级唯一 → 一门作业只报一次出分,即便后续改分)
- 改分:`f"GRADE_CHANGED:{course_id}:{column_id}:{score}"`(score 入 key → 每次新分值各报一次,同分回扫不重)

> 注:`dedup_key` 一律由稳定 BB id 构成,**不含时间窗口**;这是"多天未扫也能补齐、补齐时不重复"在 schema 层的固化。`event` 与 `known_item` 在**同一事务**内写入(见 13.6 N6),避免"快照已更新但事件未落库"的漏报窗口。

### 13.6 "绝不漏 / 绝不重复"的专项验证用例

这是本章的核心。每条用例直接映射一个失效模式。

**N1 注入新 column 应恰好一次事件**
```python
def test_new_column_emits_exactly_one_event(conn, fake_client):
    fake_client.set_columns("MAT3007", [hw1, hw2])      # 第一次扫: 建立基线
    scan(conn, fake_client, now=T0)
    fake_client.set_columns("MAT3007", [hw1, hw2, hw4]) # 注入 hw4
    events = scan(conn, fake_client, now=T1)
    assert len([e for e in events if e.ref_id == hw4.id and
                e.event_type == "NEW_ASSIGNMENT"]) == 1
    assert count_rows(conn, "event", "dedup_key=?",
                      f"NEW_ASSIGNMENT:MAT3007:{hw4.id}") == 1
```

**N2 重复扫描不应重复通知(幂等)**
```python
def test_rescan_no_duplicate(conn, fake_client):
    fake_client.set_columns("MAT3007",[hw1,hw2,hw4])
    e1 = scan(conn, fake_client, T1)         # hw4 首报
    e2 = scan(conn, fake_client, T2)         # 数据未变
    e3 = scan(conn, fake_client, T3)
    assert any(x.ref_id==hw4.id for x in e1)
    assert e2 == [] and e3 == []             # 后续零事件
    assert count_rows(conn,"event","event_type=?","NEW_ASSIGNMENT")==1
```

**N3 多触发源共用一库,仍幂等** — 模拟 SessionStart + 手动 `/bb-scan` + 周期循环近乎并发。该用例同时锚定 schema 选型:`event` 写入须用 `INSERT ... ON CONFLICT DO NOTHING`,且单次 `scan` 事务以 `BEGIN IMMEDIATE` 取写锁,使并发扫描串行化而非交错。
```python
def test_concurrent_triggers_dedup(conn_factory, fake_client):
    # 三个连接(各模拟一个触发源)同帧调 scan; dedup_key PRIMARY KEY + 事务兜底
    with ThreadPool(3) as p:
        p.map(lambda _: scan(conn_factory(), fake_client, T1), range(3))
    assert count_rows(any_conn,"event","event_type=?","NEW_ASSIGNMENT")==1
```

**N4 跨 16 周窗口不漏** — 日历窗口必须 ≤16 周(超出报 400);一个学期需多窗口拼接。
```python
def test_calendar_window_split_covers_term_no_gap_no_overlap():
    wins = split_windows(term_start, term_end, max_weeks=16)
    # 相邻窗口端到端衔接, 无缝隙(漏)、无重叠重复计同一 due
    for a,b in pairwise(wins): assert a.until == b.since
    assert wins[0].since==term_start and wins[-1].until>=term_end
    assert all((w.until-w.since).days <= 16*7 for w in wins)

def test_item_on_window_boundary_counted_once(conn, fake_client):
    # 一个 due 恰落在窗口边界, 出现在两个窗口响应里 → 只 1 个事件(id 去重)
    fake_client.set_calendar([col_at_boundary], windows=[w1,w2])
    events = scan(conn, fake_client, T1)
    assert len([e for e in events if e.ref_id==col_at_boundary.id])==1
```

> 实测约束:日历项与 per-course `gradebook/columns` 是同一批 `GradebookColumn` 的两个视图。扫描以 **per-course columns 为权威**(含 `contentId/status`),日历仅用于跨课程 ddl 总览;两路对同一 column id 去重收敛到同一 `known_item`,不得各记一次。下面 N4b 锁死这一点。

```python
def test_calendar_and_columns_same_id_not_double_counted(conn, fake_client):
    # 同一 column 既出现在 calendars/items 又出现在 courses/{cid}/gradebook/columns
    fake_client.set_columns("MAT3007", [hw4])
    fake_client.set_calendar([hw4_as_calendar_item])   # 同 id 的日历视图
    events = scan(conn, fake_client, T1)
    assert len([e for e in events if e.ref_id==hw4.id])==1
```

**N5 跨页项不漏** — `users/{uid}/courses` 与 columns 翻页;`paging` 仅在有下页时出现。
```python
def test_paging_concatenates_all_pages(fake_client):
    fake_client.set_pages("courses",[courses_expand, courses_expand_page2])
    got = list(client.list_memberships("_49765_1"))
    assert len(got) == 19                  # 实测 19 门 membership, 不能停在第一页
def test_paging_stops_when_no_nextpage(fake_client):
    fake_client.set_pages("courses",[paging_nopage])  # 无 paging 字段
    assert len(list(client.list_memberships("_49765_1"))) == 1  # 不无限翻页
```

**N6 崩溃后重启不丢不重** — 扫描中途崩溃(写了快照但通知未发 / 通知发了但快照未写)。
```python
def test_crash_after_snapshot_before_notify_recovers(conn, fake_client):
    # 模拟: 事务提交(known_item+event 同事务落库), notifier 抛异常前进程死
    fake_client.set_columns("MAT3007",[hw1,hw4])
    with pytest.raises(NotifierCrash):
        scan(conn, fake_client, T1, notifier=crashing_notifier)
    # 重启: event 已落库且 notified=0 → 重扫不再产生新 event, 但补发未送通知
    events = scan(conn, fake_client, T2, notifier=ok_notifier)
    assert events == []                                  # 不重复产事件
    assert count_rows(conn,"event",
                      "notified=1 AND event_type=?","NEW_ASSIGNMENT")==1

def test_crash_mid_scan_unfinished_log_then_resume(conn, fake_client):
    # scan_log.finished=NULL 表示上次崩溃; 全量 diff 天然补齐, 无需断点续传
    insert_unfinished_scan(conn)
    fake_client.set_columns("MAT3007",[hw1,hw4])
    scan(conn, fake_client, T2)
    assert count_rows(conn,"event","ref_id=?",hw4.id)==1
```
要点:**已知快照与事件在同一事务内提交,通知是事务外的独立第二步,通知成功才置 `notified=1`**。重启策略是"补发所有 `notified=0`"而非"重新 diff":既不漏(崩溃前已 diff 出的事件留在库里)也不重(diff 不再重复产、通知按 `notified` 标记补发)。全量 diff 使"断点续传"不必要——这是 §6 鲁棒性事实的直接落地。**反向坑也要测**:若实现把"先发通知再落库"写反,`test_crash_after_snapshot_before_notify_recovers` 将因重启后重复产事件而变红。

**N7 出分提醒生命周期** — 集成层串起 13.4(B)。
```python
def test_grade_release_then_rescan_silent(conn, fake_client):
    fake_client.set_grade("MAT3007", hw1.id, "NeedsGrading", None)
    scan(conn, fake_client, T0)                       # 无出分事件
    fake_client.set_grade("MAT3007", hw1.id, "Graded", 100)
    e1 = scan(conn, fake_client, T1)                  # GRADE_RELEASED ×1
    e2 = scan(conn, fake_client, T2)                  # 静默
    assert any(x.event_type=="GRADE_RELEASED" for x in e1) and e2==[]
```

**N8 已交不误报、手动覆盖自动** — 清单 `auto_done` 由 status 推断(`NeedsGrading/Graded` → 已交);`manual_done` 非空则覆盖。
```python
def test_submitted_marks_task_done_not_overdue():
    assert task_done(GradeState("NeedsGrading",None)) is True
def test_manual_done_overrides_auto():
    # 线下纸质作业 status 恒 None(auto_done=0), 用户勾选后清单不再标红
    assert effective_done(auto=False, manual=True) is True
def test_manual_unchecked_overrides_auto_done():
    # 反向: 自动判已交但用户主动取消勾选, 以手动为准
    assert effective_done(auto=True, manual=False) is False
```

### 13.7 端到端测试:本地清单服务点查

起真实清单服务(`127.0.0.1`,随机端口,后端为 tmp SQLite),用 `httpx` 打 HTTP:

```python
def test_dashboard_lists_open_tasks_sorted_by_due(live_server, seeded_db):
    r = httpx.get(f"{live_server}/api/tasks?status=open")
    due = [t["due_utc"] for t in r.json()]
    assert due == sorted(due)                  # ddl 升序
    # 展示字段为 +08:00, 校验 hw4 due 2026-06-30T15:59Z → 显示 2026-07-01 00:59 (+08)

def test_check_done_persists_and_survives_rescan(live_server, fake_client):
    httpx.post(f"{live_server}/api/tasks/MAT3007/{hw4.id}/done")
    scan_via_api(live_server)                  # 触发一次扫描
    t = httpx.get(f"{live_server}/api/tasks/MAT3007/{hw4.id}").json()
    assert t["manual_done"] is True            # 扫描尊重手动状态, 不被覆盖

def test_overdue_recent_unscanned_banner(live_server, freeze_time):
    # 临近 ddl 但 last_scan 久远 → 返回醒目提示标记(缓解"不开就不扫")
    assert httpx.get(f"{live_server}/api/health").json()["stale_warning"] is True

def test_bind_localhost_only(live_server):
    assert live_server.startswith("http://127.0.0.1")  # 不绑 0.0.0.0
```

另有插件壳层的冒烟测试(不触网):`SessionStart` 钩子脚本以 `--dry-run` 跑通,断言其为 **async 非阻塞**(进程立即返回、扫描在后台)、`additionalContext` 输出含待办摘要,且**不含任何凭据字符串**——grep `Password`、cookie、token、学号 `125090374`、姓名 `梁博文` 必须全为空,对齐"不进日志/不落盘明文"。

### 13.8 测试数据时钟与可复现性

- 所有相对时间断言用 `freezegun.freeze_time("2026-06-28T00:00:00Z")`。
- `due` 一律按 UTC 解析再转 +08:00 展示;**禁止裸日期比较**(N4 与 13.4 已覆盖跨日)。
- fixtures 只读;每个测试用 `tmp_path` 全新 SQLite,跑完即弃,保证用例间零状态泄漏。
- CI 入口:`pytest -q`(全部离线、确定性、无网络无钥匙串)。对 `diff/status/paging+窗口/pathmap/courses(在读过滤)` 五个核心模块要求 **100% 行覆盖**,并对 13.4(B) 跃迁表的每个格子有显式用例(分支全覆盖)。

### 13.9 分阶段开发计划(里程碑 M0..M6)

里程碑与 spec §9 的"三刀"对齐:**M1–M2 = 第一刀**(最小可用闭环)、**M3–M4 = 第二刀**(自动化 + 下载)、**M5–M6 = 第三刀**(打磨分发)。每个里程碑给交付物、验收标准(可观测、可回归)与依赖。

| 里程碑 | 三刀 | 交付物 | 验收标准 | 依赖 |
|---|---|---|---|---|
| **M0 骨架与传输** | 前置 | repo 结构、`pyproject`、`engine.auth` + `bbclient` 传输层(`curl_cffi`,回退子进程 `curl`,尊重 `127.0.0.1:7890` 代理)、契约 fixtures 录制+脱敏脚本 | 用真实账号一次性跑通 ADFS OAuth2 登录拿到会话 cookie,`GET users/me` 返回 `_49765_1`,翻页拉到 19 门 membership;`requests` TLS 失败已被 `curl_cffi` 规避;凭据只进钥匙串、脱敏脚本输出 grep 凭据为空 | — |
| **M1 引擎核心(第一刀 a)** | 一 | `store`(13.5 DDL)、`diff_ids`、`reduce_status`、`select_active`、`scan()`;契约 + 单元 + 不变量测试 13.3/13.4/13.6 全绿 | N1–N8 全部通过;`select_active` 从 19 门筛出 17 门在读;`pytest -q` 离线确定性绿;五核心模块 100% 行覆盖 | M0 |
| **M2 插件最小闭环(第一刀 b)** | 一 | `plugin.json`、`/bb-setup`(账号→钥匙串)、`/bb-scan`(手动)、本地清单页(任务/ddl/勾选)、macOS 桌面通知、MCP `scan_now`/`list_tasks`;检测**新作业+ddl**与**新公告** | e2e 13.7 全绿;`/bb-scan` 后清单页按 ddl 升序、`hw4` 显示 +08 时间(2026-07-01);新作业/新公告各弹一次 macOS 通知且重扫不重复;勾选完成持久化且扫描不覆盖 | M1 |
| **M3 SessionStart 自动化(第二刀 a)** | 二 | `hooks.json` 的 `SessionStart`(async 非阻塞,`additionalContext` 注入待办摘要)、清单服务内可选周期扫描(10min~每日定点可配)、`stale_warning` 临近未扫提示 | 开 Claude Code 即后台扫描不阻塞会话(钩子立即返回);周期扫描在服务存活期触发且与手动共库幂等(N3);注入摘要 grep 凭据为空 | M2 |
| **M4 课件下载(第二刀 b)** | 二 | `downloader`(内容树递归、`local_path`/`needs_download` 增量镜像、附件 302 跟随)、MCP `download_course`、`/bb-download`、检测**新课件上传**与**出分** | 镜像目录结构匹配 BB 文件夹链(13.4C 测试);未变文件不重下;`modified` 变更触发重下与 `NEW_CONTENT` 提醒;出分按 N7 仅报一次;路径穿越用例为绿 | M2(下载相对独立,可与 M2 对调) |
| **M5 渠道与配置(第三刀 a)** | 三 | 可插拔 `notifier`(邮件 / Telegram)、课程黑/白名单、频率配置 UI | 同一事件经多渠道发送仍按 `dedup_key` 去重(不重复跨渠道);黑名单课程不产事件(`select_active` deny 生效);配置改动即时生效 | M3,M4 |
| **M6 打包分发(第三刀 b)** | 三 | 插件市场打包、同学安装文档(含 Windows/Linux 注意项)、端到端冒烟 | 干净机器按文档安装 → `/bb-setup` → `/bb-scan` 走通;全程 grep 凭据为空;跨平台路径/通知降级有文档 | M5 |

依赖顺序为严格链:**M0 → M1 → M2 →(M3 ∥ M4)→ M5 → M6**。M1 是质量地基:**N1–N8 不全绿不进 M2**——"绝不漏/绝不重复"必须在引擎层确立后才允许接通通知,否则会把误报推到用户面前。M4 与 M3 无强耦合,若优先要下载可先做 M4(与 spec "下载可与第一刀对调"一致)。

### 13.10 退出标准(Definition of Done,全局)

一个变更可合入,当且仅当:
1. `pytest -q` 全绿且五核心模块(diff/状态机/翻页+窗口/路径/在读过滤)行覆盖率 100%(离线、无网络、无钥匙串);
2. 触及 diff/状态机/翻页/窗口/路径/在读过滤的改动**必须**带对应的 N1–N8 风格不变量回归用例,且崩溃路径(N6)在改动通知/落库顺序时强制更新;
3. 任何新增 BB 解析路径都有一份脱敏 fixture 与契约断言锚定实测字段名(字段更名即红);
4. 凭据 grep(`Password`、cookie、token、学号 `125090374`、姓名 `梁博文`)在源码、日志样本、`additionalContext` 输出、脱敏后的 fixtures 中均为空;
5. 涉及时间的代码经 `freezegun` 冻结并验证 UTC→+08:00 跨日正确(裸日期比较即视为缺陷)。

---

## 附录 A: 鲁棒性对抗审计

> 本附录对前述四章(§4 数据模型 / §5 鲁棒性 / §6 认证 / §7 BB 客户端 / §8 扫描编排)做一次跨章对抗审计,穷举可导致**漏报**或**重复**的场景。每条给出:触发条件 → 当前设计是否已防住 → 未防住的必须修复项。结论与"上线前检查清单"在末尾。

---

### A.1 分页 / 16 周窗口边界

#### A.1.1 分页中途失败被当成"全集"
- **触发**:某课 `gradebook/columns` 第 2 页 5xx/超时,第 1 页已返回。
- **已防住**:§7.8 `paginate` 任一页失败上抛;§7.12 "全集或抛异常"契约;§8.4 `None` 路不参与 diff、不收敛快照;§7.10 重试耗尽则整次 `list_*` 失败。**充分。**

#### A.1.2 `nextPage` 不前进 / 自指 → 死循环或重复收录
- **触发**:BB 返回的 `nextPage` 指向同一 offset,或形成环。
- **已防住**:§7.8 `guard>10000` runaway 守卫 + `nxt == next_url` 不前进检测。**充分。**

#### A.1.3 翻页期间底层集合变动 → 跨页重复/漏项(offset 漂移)
- **触发**:基于 offset 的分页,翻页过程中老师新增/删除一个 column,导致某项跨页边界被跳过或重复返回。
- **未防住**:§7.8 信任 BB 的 `nextPage`(已带 offset),但 BB REST 的 offset 分页对"翻页期间插入/删除"无快照隔离。重复项靠 diff 的 id 去重天然吸收(不重);**但被挤出窗口的漏项不会被本轮检出**。
- **必须修复项**:**(M1)** 这是"单轮拉取残缺"的隐性来源,且不抛错(无失败信号)。缓解=单课单维度的分页应尽量快(已有限速反而拉长窗口,矛盾);更稳的是**接受"本轮可能漏一项"但靠下一轮全量 diff 补齐**——前提是 §A.6.1 的"失败/残缺不可推进软删"必须覆盖到"残缺但未抛错"的情形。**结论:M1 不会造成永久漏(下轮补),但必须保证残缺轮绝不触发软删/归档,否则被挤出项会被误判删除→永久漏。** 见 A.6.1 修复项。

#### A.1.4 16 周窗口分片边界项重复
- **触发**:同一 ddl 落在相邻分片重叠区。
- **已防住**:§7.6 按 `id` 去重(`seen[it.id]=it`);§5.5 "全量 diff 对窗口重叠免疫"。**充分。**

#### A.1.5 学期边界估算错误 → 日历窗口未覆盖学期尾部
- **触发**:`terms.duration` 缺失,§5.5 退化为"today 前 26 周/后 30 周",若学期长度异常或跨年导致尾部 ddl 未进任一窗口。
- **已防住(部分)**:日历仅作交叉校验(§7.6/§8.5),权威 ddl 来自 per-course `columns`(不受 16 周限制)。**即使日历漏覆盖,ddl 仍由 columns 全量拿到。充分,但有前提**——见 A.1.6。

#### A.1.6 日历交叉校验本身的窗口缺口被静默忽略
- **触发**:日历用于"发现某课 columns 漏扫"。若日历窗口未覆盖学期尾,这个"查漏网"在尾部失效,而 columns 路恰好也漏了某课(如 A.8 课程上线未及时纳入)。
- **未防住**:两条冗余路径在"学期尾 + 新课"这一角落同时失效。
- **必须修复项**:**(M2)** 日历分片必须覆盖 `[term_start-1w, term_end+1w]` 全程并对**全部在读课**做"日历有 col_id 但本地 columns 无"的对账;当 `terms.duration` 缺失时,退化窗口须以**实际已知 column 的最大 due**为下界动态外扩(而非固定 30 周),保证已知最远 ddl 之后仍有覆盖。

---

### A.2 崩溃与并发触发

#### A.2.1 "标记已 seen 但未排队通知"窗口
- **触发**:写 `seen_entity` 后、stage event 前崩溃。
- **已防住**:§5.2 铁律(同事务)、§8.1 不变量 2、§5 不变量 2。**充分。**

#### A.2.2 通知投递前后崩溃 → 漏弹或重弹
- **触发**:`osascript` 调用前/后进程被 kill。
- **已防住**:§5.2 状态机(先 PENDING 再投递再 NOTIFIED)、§4.4.9 `claim_pending_events` 单事务"取+置 notified"、§5.7 层三 `WHERE state='new'` 守卫。**充分。**

#### A.2.3 §4 与 §5 两套 schema / 通知语义冲突 ⚠️
- **触发**:§5 定义 `event.state ∈ {PENDING_NOTIFY, NOTIFIED, FAILED_NOTIFY}` + 失败**指数退避重投**(`next_retry_at`, `max_attempts`);§4.4.9 定义 `notify_state ∈ {pending,sent,failed}` 且 §4.4.9 正文写"即便 osascript 失败,事件已离开 new 队列、不会下次重弹"(**失败即终态、不重投**,理由"重弹无可挽回地骚扰")。两章对"通知失败"的处理**直接矛盾**:§5 要重试(防漏弹),§4 不重试(防重弹)。
- **未防住**:这是跨章语义冲突,不是实现细节。按 §4 则 `osascript` 偶发失败=**永久漏这一条桌面通知**(仅靠清单页/additionalContext 兜底);按 §5 则退避重投,但若"投递成功但进程在 mark 前崩溃"则**重弹一次**。
- **必须修复项**:**(M3, 最高优先)** 统一为单一状态机。建议:**投递与 `mark sent` 同事务**(§4 的 `claim` 模型)消除"成功后崩溃重弹";**失败(osascript 非 0/超时)走 §5 的有限退避重投**(`failed` 非终态,`attempts<max` 时下轮重取),`attempts>=max` 才入 `FAILED_NOTIFY` 终态。即"成功路径不重弹(§4 的原子性)+ 失败路径有限重投(§5 的退避)"。否则 no-miss/no-duplicate 在通知层二选一被破坏。

#### A.2.4 多触发源并发首扫同一新课 → 基线判定竞态
- **触发**:SessionStart 与周期循环同秒首次扫到某新课,各自读 `is_baseline=True`,都按基线抑制——或一方已 upsert 快照使另一方读到 `is_baseline=False` 而把历史项当新增轰炸。
- **已防住**:§5.7 文件锁去抖(后到者跳过整轮);§8.6 `BEGIN IMMEDIATE` 串行化 + "先读基线再写快照";§8.1 不变量 2。**充分**,前提是文件锁与 store 事务两道都在。注意 §5.7 锁路径 `~/.bbwatch/scan.lock` 与 §6.6 登录锁 `login.lock` 是两把锁,职责不重叠,无死锁(获取顺序固定:scan 锁包含登录调用,登录锁在内层)。**需在实现期断言获取顺序固定以免未来加锁引入死锁。**

#### A.2.5 `dedupe_tag` 与 `dedup_key` 命名/构造跨章不一致
- **触发**:§5.3 用 `dedupe_tag = '{event_type}:{entity_key}'`;§8.6 用 `dedup_key`,且 NewMaterial 含 `modified`、DeadlineSoon 含 `due_utc+window_h`。§5 的 `dedupe_tag` 不含 `modified`/`window`,若按 §5 构造则"课件更新二次提醒""多档 ddl 提醒"会被 UNIQUE 误挡 → **漏报这些合法重复提醒**。
- **必须修复项**:**(M4)** 全局统一 dedup 键构造为 §8.6 的版本(含 variant: NewMaterial→modified、DeadlineSoon→due_utc+window_h、GradePosted→score/'graded'),§5.3 的 `dedupe_tag` 定义作废或对齐。键的生成必须**单一函数** `make_dedup_key()`(§8.6 已声明唯一来源),实现期禁止第二处构造。

---

### A.3 status 状态机漏洞

#### A.3.1 出分历史在首扫被当"新出分"轰炸
- **触发**:bbwatch 上线前已有大量 Graded。
- **已防住**:§5.6 `is_bootstrap`/`suppress_notify`、§8.6 基线抑制。**充分。**

#### A.3.2 已交待批反复催办
- **触发**:`NeedsGrading` 每轮仍见 due。
- **已防住**:§5.6(A)、§8.5.6 `store.is_done`、反例 6。**充分。**

#### A.3.3 改分(Graded→Graded, score 变)既不报也不漏看
- **触发**:老师改分。
- **已防住(按设计取舍)**:§5.6 与 §8.5.3 一致取舍——一列只通知一次出分,改分不发桌面通知,仅清单/成绩页展示。**一致,无矛盾。** 但 §8.5.3 提到可选 `regraded` 事件,§5.6 提到可选 `regraded` 事件类型——若启用须各自独立 dedup,当前默认关闭,无风险。

#### A.3.4 撤分(Graded→None)后再出分 → 永久漏第二次出分 ⚠️
- **触发**:老师误批后撤回(`Graded→None`),择日重新批改(`None→Graded`)。
- **未防住**:§8.5.3 明确"GradePosted dedup_key 已存在仍不重发——一列只通知一次出分"。撤分时 §5.6 也不回退 `grade_status` 基线吗?**§5.6 的 `upsert_seen_grade` 总是刷新基线**(把 `grade_status` 写回 `None`),于是再出分时 `prev.grade_status != 'Graded'` → `became_graded=True` → **会 stage**;但 §8.6 的 `event.UNIQUE(dedup_key='GradePosted:{cid}:{col}')` 会**挡住**第二次(键不含 variant)。**两章再次冲突**:§5.6 的状态机想发,§8.6 的 UNIQUE 不让发。
- **必须修复项**:**(M5)** 明确撤分语义。二选一并贯穿两章:(a) 接受"一列一生只通知一次出分",则 §5.6 不应在 became_graded 上重 stage(否则徒增被 IGNORE 的写),且文档须写明"撤分后重批不再提醒"——属已知取舍;(b) 若要支持重批提醒,GradePosted 的 dedup_key 须含 `score` 或一个"出分世代"序号。**鉴于实测出分是低频且撤分更罕见,推荐 (a) 并显式文档化**,但必须消除 §5.6"会 stage"与 §8.6"被挡"的隐性矛盾(白写一行被静默吞,易在测试中误判为 bug)。

#### A.3.5 `status=None` 但 `score` 非空 / 未知状态值
- **触发**:BB 返回 score 但 status 仍 None(部分只给分列);或返回枚举外的新状态字符串。
- **已防住**:§8.5.3 `now_graded = Graded OR score 非空`(并集);§7.3 `GradeStatus._missing_` 归一为 `NONE`(保守=未完成,不漏催办,但 score 非空仍触发 graded)。**充分。**

#### A.3.6 per-user status 半数列拉取失败 → 漏某列出分
- **触发**:逐列查 `columns/{colId}/users/{uid}` 部分失败。
- **已防住**:§8.4 grade 路"全有或全无"(`any_fail` 任一失败整路置 `None`)。**充分。**

#### A.3.7 404=未交 的语义化越界
- **触发**:`get_column_status` 404 当"未交";若其他列表端点 404 被同样处理则把"课程删除"当空集。
- **已防住**:§7.9 明确 404→未交**仅限** `get_column_status`,其余 `list_*` 的 404 必抛。**充分。**

---

### A.4 重命名 / 删除再添加

#### A.4.1 重命名误判为新项
- **触发**:column/content 改名。
- **已防住**:§5.1(3) 幂等键只认 id;§5.9 反例 2。**充分。**

#### A.4.2 删除后重建分配新 id → 作为新项通知
- **触发**:老师撤回作业又重发。
- **已防住(按设计取舍)**:§5.1 视为合理新事件。**一致。**

#### A.4.3 软删后又出现被当全新重发
- **触发**:column 本轮消失(置 deleted=1),下轮又出现。
- **已防住**:§4.4.3/§5.1 软删不物理删行,保留去重锚;再现时 id 仍在 `seen`/`known` → 不重发"新任务"。**充分。** 但注意:§8.5.2 `diff_columns` 读的是 `known_column`(全表),还是仅 `deleted=0`?§8.5(diff_contents)示例 `store.known_contents(cpk)` 未带 `deleted` 过滤——**若 differ 只读 `deleted=0` 的已知集,则软删项再现会被当新项**。
- **必须修复项**:**(M6)** 所有 differ 的"已知集"查询必须包含软删行(`known_*` 全表,不加 `deleted=0`),软删仅影响清单页展示与归档,绝不影响 diff 去重。实现期须断言 `known_column_ids/known_contents` 等返回**含 deleted** 的全集。

#### A.4.4 归档阈值导致永久遗忘 → 再现重发
- **触发**:§5.1 连续 N 次未出现→归档;§5.3 `archive_stale(miss_threshold=3)`。若归档=物理删行则再现重发;若 BB 长期隐藏后又恢复(>3 轮)。
- **已防住**:§5.1/§5.3 明确"归档不删行"。**充分**,只要归档严格只置 `archived=1`。**(连带 M6:归档行也必须留在 differ 的已知集内。)**

#### A.4.5 残缺轮误触发软删/归档 ⚠️
- **触发**:某课 columns 本轮抓取残缺(A.1.3 offset 漂移,**未抛错**)或整路失败,本地有该 id 而拉取集无 → 误置 deleted/推进 miss 计数。
- **已防住(部分)**:§8.5 `diff_columns(complete=...)` 闸门——`complete=False` 禁止软删;§5.4/§5.7/反例 7 "失败维度跳过 diff";§4.4.11 `items_seen` 自检失败不软删。
- **必须修复项**:**(M7)** `complete` 的判定必须覆盖**"未抛错但可能残缺"**(A.1.3):即 `complete=True` 仅当"该维度分页正常终止(见到末页 paging 缺失)且无任何重试降级"。仅靠"没抛异常"不足以证明 complete。`miss` 计数推进必须 `AND scan_run.status='ok' AND dimension.complete`。否则 offset 漂移→误软删→永久漏。

---

### A.5 时区

#### A.5.1 字符串字典序比较 modified → 误判更新有无
- **触发**:BB `modified` 毫秒位数/时区写法不定。
- **已防住**:§8.5.5 `parse_utc(n.modified) > parse_utc(prev_mod)`(已修正初稿 bug)。**充分。**

#### A.5.2 本地午夜算错一天 / DeadlineSoon 边界
- **触发**:用本地时间算 ddl 窗口。
- **已防住**:§5.8 存 UTC、仅展示转 +8;§8.5.6 窗口判定用 UTC、闭区间 `<= window_h`、`delta_h<=0` 不发但清单标红。**充分。**

#### A.5.3 `_parse_dt` 解析失败返回 None → 该项 due 丢失
- **触发**:坏时间格式。
- **已防住(部分)**:§7.3 解析失败返回 None 不崩。**但** due=None 的真实作业列在 §8.5.6 `derive_deadline_soon` 中 `if due is None: continue` → **不催办**,且 §8.5.2 NewTask 仍会发(基于 id)。即"坏 due 的作业"会作为 NewTask 提醒一次,但**永不进 ddl 倒计时**,可能漏 ddl。
- **必须修复项**:**(M8)** `_parse_dt` 对"原始字符串非空但解析失败"必须记一条 `scan_error`(可观测),且清单页对"有作业但 due 不可解析"显著标注(而非静默无 ddl)。低概率,但属静默漏 ddl。

---

### A.6 会话失效中途 / 拉取残缺

#### A.6.1 会话半途失效 → 后半段拉空被 diff 当"全删"
- **触发**:扫描进行中会话过期,后续请求 401/被重定向到登录页。
- **已防住**:§6.6 请求级 `invalidate`+重登一次+**重放该请求**;二次失败抛 `SessionRefreshError`→§6.0/§6.6 扫描器记本轮失败、**不更新快照**;§7.12 失败维度沿用旧快照。**充分**,前提是"重放"作用于**原始业务请求**而非仅探针,且 §A.4.5 的 `complete` 闸门生效。

#### A.6.2 SOFT_TTL 窗口内会话已被服务端单方失效
- **触发**:20 分钟信任窗口内 BB 主动失效会话。
- **已防住**:§6.6 论证——TTL 只决定何时主动探活,真正兜底是请求级重放;窗口内首个业务请求遇 401 即重登重放。**充分。**

#### A.6.3 熔断期间无会话 → 静默漏扫
- **触发**:连续 3 次凭据失败→熔断 1h。
- **已防住**:§6.7 熔断期 `get_session` 抛 `AuthCircuitOpenError`→扫描显式失败、不更新快照;修复后下轮全量 diff 补齐;通知用户去 `/bb-setup`。**漏被延后而非丢弃,充分。**

#### A.6.4 `additionalContext` 注入的是上一轮数据 → 误导但不漏
- **触发**:§6.x/§5.10 SessionStart 非阻塞,注入"上一次已落库摘要"。
- **已防住**:这是有意设计(不阻塞启动),本轮结果稍后由 notifier 补。属"信息略延迟",非漏。**可接受。**

---

### A.7 首次运行冷启动

#### A.7.1 首扫轰炸上百历史项
- **已防住**:§5.6/§8.6 bootstrap 抑制。**充分。**

#### A.7.2 基线半截 → 另一半历史项当新增轰炸 ⚠️
- **触发**:首扫某课某路 `None`(没拉到),§8.6 "仅四路全成功才落 `course_baseline`"。但 §8.6 `_insert_event(suppressed=is_baseline)` 中 `is_baseline = not _has_baseline(cpk)`——首扫未落基线时 `is_baseline=True`,**已抑制本轮所有草稿**;问题在**第二轮**:若首轮未落基线标记,第二轮 `_has_baseline` 仍 False → `is_baseline=True` → 继续抑制。看似安全,但**何时退出抑制**?只有"四路全成功的那一轮"才落基线并从**下一轮**起正常通知。若某课长期有一路持续失败,则**永远不落基线 → 永远抑制 → 永久漏所有通知**。
- **未防住**:持续性单路失败导致永久抑制。
- **必须修复项**:**(M9)** 基线落定不能要求"同一轮四路全成功"无限等待。改为**按维度独立基线**:每个 `(course, dimension)` 各自有 baseline 标记,某维度成功即落该维度基线,该维度此后正常通知;不因其他维度持续失败而连累。否则一门课只要 contents 树永远有一个坏子节点(§8.4 整树置 None),其 columns/announcements/grades 的通知就被永久抑制。

#### A.7.3 首扫被中断(崩溃)→ 基线只建了一半课
- **触发**:首扫扫到第 8 门课崩溃。
- **已防住**:§8.6 逐课事务;已处理课各自落基线,未处理课下轮按"首扫"继续抑制。**充分**(配合 M9 的按维度基线)。

#### A.7.4 DeadlineSoon 在首扫课上轰炸临近 ddl
- **已防住**:§8.6 local_drafts 的 `suppressed = not _has_baseline(course_pk)`;反例 5"清单展示但不弹"。**充分**(注意 M9 后改为按维度:DeadlineSoon 应挂 columns 维度基线)。

---

### A.8 课程上线 / 下线

#### A.8.1 课程过滤误判 → 漏扫一门在读课
- **触发**:§8.3 `courseRoleId != "Student"`(自定义角色名)、`availability` 枚举外值、当前学期误判 → 在读课被过滤掉。
- **已防住(部分)**:§7.3/§8.3 `Availability._missing_→DISABLED`(保守=不在读,**倾向少扫**);§8.3"三关只是优化,落选课不会触发收敛/删除";`extra_student_roles` 兜底。**关键保证:误判方向是"少扫"而非"误删",落选课不软删 → 不会把已知项当删除 → 不永久漏(下次纳入即全量补)。**
- **残余风险**:若一门在读课**从未**通过过滤(自定义角色一直没配),其全部任务**永不进入** $V_k$ → 永久漏。这不是 diff 缺陷,是"课程枚举完整性"缺陷。
- **必须修复项**:**(M10)** 课程纳入应**宽进**:对 `role` 不在白名单但 `availability ∈ {Yes,Term}` 且 `termId ∈ current_terms` 的课,至少记一条"疑似未纳入课程"告警到清单页/日志(而非静默丢弃),让用户可一键纳入。配合 §A.1.6 的日历对账(日历有 due 项指向某 cid,但该 cid 不在扫描集)作为第二张网。

#### A.8.2 课程下线/退选 → 历史 events 外键悬空
- **触发**:`enrolled→0`,不再扫;但旧 `events`/`known_*` 仍引用该 course_id。
- **已防住**:§4.4.2 课程历史行保留(不删),外键不破。**充分。**

#### A.8.3 当前学期判定漂移(`compute_current_terms` 启发式)
- **触发**:`term.duration` 缺失,退化为"出现频次最高的学期";跨学期切换期(两学期重叠)误判。
- **已防住(部分)**:§7.3/§8.3 退化启发式 + 写日志。
- **必须修复项**:**(M11)** 学期切换期应允许"当前学期集合"含**多个**(上学期未结课 + 新学期),宁可多扫一学期也不漏;`compute_current_terms` 须返回集合而非单值,且以 `now ∈ [start,end]` 为主、频次仅兜底。否则切换期可能整门新学期课漏扫。

---

### A.9 下载 / 镜像(课件)

#### A.9.1 半截文件被当已下载
- **触发**:下载中断留下部分文件。
- **已防住**:§7.5/§4.4.8 `.part`+`os.replace` 原子落盘;`downloads.status ∈ {pending,failed}` 可续。**充分。**

#### A.9.2 课件被替换(modified 变)但附件 id 不变 → 不重下
- **触发**:老师替换同名附件,attachment id 不变,但 content.modified 变新。
- **已防住**:§4.4.8 `need_download` 比较 `src_modified_utc < contents.modified_utc` 重下;§8.5.5 NewMaterial 以 modified 入 dedup → 二次提醒。**充分**,前提 content.modified 确实随附件替换而更新(实测 modified 可判更新,合理假设;若 BB 替换附件不更新父 content.modified,则漏)。
- **必须修复项**:**(M12, 低优先)** 对 attachment 维度也做全量 id diff(§4.4.7 `attachments` 表已设计),新 attachment id 即新文件,不依赖 content.modified。当前 §8.5.5 注释"scanner 不拉 attachments,由 downloader 按需取"——则**新增附件(content.modified 不变时)的检测**依赖 downloader 而非 scanner diff,职责边界须写清,避免两边都以为对方负责而漏。

---

### A.10 跨章一致性总览(矛盾即风险)

| 冲突点 | §5 / §6 说法 | §4 / §8 说法 | 风险 | 修复 |
|---|---|---|---|---|
| 通知失败处理 | 退避重投(防漏弹) | 失败即终态不重弹(防重弹) | 漏弹 **或** 重弹 | **M3** |
| dedup 键构造 | `dedupe_tag={type}:{key}` 无 variant | `dedup_key` 含 modified/window/score | 漏合法二次提醒 | **M4** |
| 撤分后重批 | §5.6 会重 stage | §8.6 UNIQUE 挡住 | 白写被吞/语义不明 | **M5** |
| differ 已知集 | 含软删(留锚) | §8.5 示例未显含 deleted | 软删再现重发 | **M6** |
| 基线粒度 | 课级 bootstrap | 课级"四路全成功才落" | 单路久败→永久抑制 | **M9** |

---

### A.11 上线前"不漏不重"检查清单

**A. 去重单点(no-duplicate)**
- [ ] **M4**:全局仅一个 `make_dedup_key()`,variant 规则统一(NewMaterial→modified、DeadlineSoon→due_utc+window_h、GradePosted→score/'graded');§5.3 `dedupe_tag` 定义已对齐或删除。grep 确认无第二处键构造。
- [ ] `event` 表 `UNIQUE(dedup_key)` 存在;所有写入走 `INSERT OR IGNORE`/`ON CONFLICT DO NOTHING`。
- [ ] **M3**:通知投递与 `mark sent` 同事务(成功不重弹);失败有限退避重投、`attempts>=max` 入终态(失败不永久漏弹)。单一状态机,§4/§5 措辞统一。
- [ ] **M5**:撤分后重批语义已显式定稿(默认"一列一次出分"并文档化),§5.6 与 §8.6 无隐性矛盾。
- [ ] 多触发源:`scan.lock` 文件锁去抖 + `BEGIN IMMEDIATE` 串行化;拿锁后双重检查重读缓存/基线。

**B. 全集完整性(no-miss 前置)**
- [ ] 每个 `list_*` "返回全集或抛异常",任一页/层/分片失败上抛;`paginate` 有 runaway + 不前进守卫。
- [ ] **M7**:`complete=True` 仅当"分页正常见到末页且无重试降级";`miss`/软删/归档推进须 `status='ok' AND complete`。"未抛错"不等于 complete。
- [ ] **M6**:所有 differ 的"已知集"查询**含软删与归档行**;软删/归档只影响展示,绝不影响 diff 去重。
- [ ] grade 路 / contents 树"全有或全无"(任一子失败整路/整树置 None)。
- [ ] **M9**:基线按 `(course, dimension)` 独立落定;某维度成功即解除该维度抑制,不被其他维度持续失败连累。

**C. 覆盖与枚举完整(no-miss 来源)**
- [ ] **M10**:课程枚举宽进;`role` 未匹配但在读的课记"疑似未纳入"告警,不静默丢弃。
- [ ] **M11**:`compute_current_terms` 返回**集合**,学期切换期含上/新两学期;以 `now∈[start,end]` 为主。
- [ ] **M2**:日历分片覆盖 `[term_start-1w, term_end+1w]` 全程;`duration` 缺失时窗口下界随"已知最大 due"动态外扩;对全部在读课做"日历有 col_id 而本地无"对账。
- [ ] per-course `columns` 不受 16 周限制,确认逐课全量翻页;日历仅交叉校验。

**D. 状态机与时间**
- [ ] bootstrap/`suppress_notify` 首扫只写快照不发通知;非 bootstrap 才通知。
- [ ] 完成判定唯一真相 `store.is_done`(status∈{NeedsGrading,Graded} 或 score 非空 或手动勾选);已交不催。
- [ ] 出分触发 = `Graded OR score 非空`;未知 status 归一为 NONE(保守未完成)。
- [ ] 所有时间存 UTC;ddl 窗口判定用 UTC、闭区间;仅展示转 +8。
- [ ] **M8**:`_parse_dt` 对"非空但解析失败"的 due 记 `scan_error` 且清单页显著标注,不静默丢 ddl;**M5/M12** 的 modified 比较用 `parse_utc` 而非字符串。

**E. 会话与凭据**
- [ ] 请求级失效检测(401/登录重定向)→ 重登一次 + 重放**原始业务请求**;二次失败抛 `SessionRefreshError`→本轮失败、不更新快照。
- [ ] 熔断状态持久化进 `auth_state`(跨进程);熔断期扫描显式失败不静默;`/bb-setup` 可复位。
- [ ] 会话失败/熔断/`SessionRefreshError` 的任一轮**绝不更新快照、绝不推进 miss/软删**(空集不得写成新基线)。

**F. 回归测试断言(§5/§8 不变量逐条)**
- [ ] 任一 `(event_type, entity_key)` 至多一行(UNIQUE)。
- [ ] 无"已 seen 未排队通知"窗口(同事务,bootstrap 除外)。
- [ ] 完整扫到一次的 id 通知恰一次;残缺/失败维度不污染基线。
- [ ] 崩溃在投递前后重启=重投而非丢失/重弹(M3 落地后)。
- [ ] 汇总列(`Total`/`Weighted Total`,无 due)永不入作业事件流。
- [ ] **新增**:offset 漂移/软删再现/单路久败/学期切换 四个对抗用例须各有专门测试(对应 M1/M6/M9/M11)。

---

**审计结论**:四章在"全量 diff 不漏 + 稳定 id/UNIQUE 不重"的主干上是稳固的,绝大多数经典陷阱(时间窗增量、首扫轰炸、改名、崩溃重弹、半截文件、会话失效拉空)已被显式防住。**真正的剩余风险集中在两类**:(1) **跨章语义冲突**——通知失败处理(M3)、dedup 键构造(M4)、撤分重批(M5)、基线粒度(M9)在 §4/§8 与 §5/§6 之间存在直接矛盾,必须收敛为单一定义,否则 no-miss/no-duplicate 在通知层或基线层被破坏;(2) **"残缺但未抛错"的隐性不完整**——offset 漂移(M1/M7)、课程枚举遗漏(M10)、学期切换(M11)、日历窗口尾部(M2)会让 $V_k$ 静默残缺,只要 `complete` 闸门与"残缺不软删"覆盖到位即可保证"下轮补齐、永不永久漏"。**M3、M4、M9 为上线阻断项**(直接破坏核心不变量),其余为高/中优先修复项。

---

## 附录 B: 完整性复核与待决问题

> 作为完整性评审者，对照设计文档(§1–13 + 附录 A/功能清单/鲁棒性审计)与实测事实，逐项指出缺口、未定义边界、未回答的设计问题，并给出建议。按"会真伤到 no-miss/no-duplicate"程度排序。

### B.1 鲁棒性核心漏洞(最高优先, 直接威胁"绝不漏")

- **会话过期 / 重登发生在扫描中途 → 半完成扫描的原子性未定义。** 当前 §8 diff 算法假设一次扫描能完整拉完 17 门课。若第 9 门课时 cookie 过期或网络中断,前 8 门已写库、后 9 门未拉。下次扫描若把"本次未拉到"误判为"已知集合不变",不会漏;但若错误地推进了 `last_scan` 或快照游标,可能漏。**建议:明确"扫描事务边界"——快照更新必须 per-course 原子提交,且只在该课程成功拉全(所有分页 + 子资源)后才更新该课的快照版本;任何一门课失败不污染其它课,也不推进全局水位。增加 `scan_runs` 表记录每次扫描每门课的成功/失败,失败课程下次强制重扫。**
- **diff 的"删除"语义未定义。** 老师撤回一个 column、删一条公告、删一个课件后再重新发布同名项(新 id),会发生什么?当前只定义了"新 id = 新事件"。若一个作业 column 被删,本地任务清单是否保留为"幽灵任务"永远未完成?**建议:明确三态——新增(notify)、消失(标记 stale/归档,不再催)、内容变更(modified 变化是否重新 notify)。尤其 ddl 被老师改期(grading.due 变化)必须能检测并重新提醒,这是高频真实场景,文档完全没提。**
- **ddl 变更 / 公告正文编辑 / column 改名 的"更新"检测缺失。** 实测事实里 column 有 `grading.due`、公告有 `body`、内容项有 `modified`,但 diff 只比对"id 是否新出现"。**老师把 HW4 从 6/30 改到 7/3,或在公告里追加考场信息,是学生最怕漏的——必须把关键字段纳入快照做字段级 diff,而非只比 id 集合。** 这是设计最大的实质性遗漏。
- **16 周日历窗口翻页 + 整学期覆盖的边界未写死。** 实测强调窗口 ≤16 周、分页 `nextPage` 仅在有下页时出现。文档 §8 未规定 since/until 的起止锚点(学期开始?今天往前回看多久?)。**若只从今天往后看,会漏掉"今天之前发布但 ddl 在未来、或刚补发"的项;若窗口拼接有缝隙(off-by-one 边界),会系统性漏。建议:per-course gradebook columns 作为权威源(信息更全, 含 status/contentId),日历仅作交叉校验,二者取并集,降低单一源漏检风险。**

### B.2 认证与传输的未决边界

- **MFA 一旦被学校开启的降级路径只字未提具体机制。** §10 说"保留扩展点"但无设计。**建议:明确半自动模式——检测到 ADFS 返回 MFA challenge 时,如何提示用户(桌面通知/清单页弹窗手输验证码)、会话获取后如何尽量延长复用以减少 MFA 频率。** 这关系到工具会不会某天突然全员失效。
- **ADFS 密码错误 / 账号锁定的处理缺失。** 自动重登逻辑若拿着过期/被改的密码反复 POST,可能触发**学校 AD 账号锁定**——这是比"漏一次扫描"严重得多的后果。**必须定义:连续认证失败 N 次后停止自动重试、清单页/通知明确告知"密码可能已变,请重新 /bb-setup",绝不无限重试。**
- **curl_cffi 回退到子进程 curl 的触发条件、以及代理(7890)不可用时的行为未定义。** Clash 没开时直连是否能成?需要明确探测与回退顺序。
- **cookie/会话缓存落盘位置与权限。** §8 说密码进钥匙串,但**会话 cookie 缓存存哪、什么权限**没说。cookie 等价于登录态,若明文存 `~/.bbwatch/` 且 644,等于绕过了钥匙串。**建议:cookie 也进钥匙串或加密存储,文件权限 600。**

### B.3 并发、触发源冲突与幂等(威胁"绝不重复")

- **三触发源(SessionStart / 手动 / 周期循环)并发跑同一 SQLite 的串行化未定义。** SessionStart 后台扫描尚未结束,用户又手动 `/bb-scan`,两个进程同时 diff 同一张表 → 可能双重通知,直接违反 no-duplicate。**建议:全局扫描锁(文件锁/SQLite BEGIN IMMEDIATE/PID 锁),后到的扫描请求合并或跳过;明确 SQLite WAL 模式 + busy_timeout。**
- **"已通知"标记与"通知实际送达"的原子性。** 先发 macOS 通知再写"已通知"标记,中间崩溃 → 下次重发(重复);先写标记再发通知,发送失败 → 永久不补发(漏)。**建议:定义通知 outbox 状态机(pending→sent),崩溃恢复时按状态补发,且通知去重以事件 id 为准而非时间。**
- **dashboard 手动勾选完成 与 扫描器自动判定状态 的写回冲突。** 学生手动勾了"已完成"(纸质作业),下次扫描 column status 仍是 None,会不会覆盖掉手动状态又弹"未完成"?§7 说"扫描器尊重手动",但**未定义优先级合并规则与字段隔离(手动 override 列 vs 自动 status 列分开存)。**

### B.4 性能上限与对 BB 温和(17 门课的实际耗时)

- **全文档没有一处量化扫描耗时或请求数预算。** 粗算:17 门课 × (gradebook columns 1 + 每个 column 的 per-user status N + announcements 1 + 内容树递归多层) + 日历多窗口翻页。**若每门课 10–15 个 column 都要单独 GET per-user status,单次全量扫描轻松上百次请求**;叠加"请求间延时"(§8 说温和),单次扫描可能数十秒到几分钟。**SessionStart 号称 async 非阻塞,但若清单页周期扫描每 10 分钟跑一次几分钟的全量,既不温和也耗电。建议:明确单次扫描的请求数量级、目标时延(如 <60s)、per-course 并发度上限、两次扫描间最小冷却,以及 per-user status 只对"带 due 且本地未终态"的 column 拉取(剪枝)。**
- **缺少条件请求 / ETag / fields 裁剪策略。** 实测提到 `fields=` 可精简返回但未纳入设计。**建议:列表类请求一律用 fields 裁剪;内容树用 modified 时间剪枝,未变的子树跳过递归。**
- **内容树递归深度 / 环 / 超大课程的保护。** MAT3007 已 50 文件多层,没有最大深度、最大节点数、超时熔断。

### B.5 运维 / 升级 / 卸载(完全缺章)

- **卸载流程未定义。** 删插件后:钥匙串里的密码 + cookie、SQLite 库、下载的课件镜像、周期扫描进程,如何清理?**需要 `/bb-uninstall` 或卸载说明,至少要能撤销钥匙串凭据,否则学校密码残留在每台装过的机器上。**
- **数据库 schema 迁移 / 版本升级。** SQLite schema 改版后老用户库如何迁移?无 `schema_version` 与迁移脚本约定 → 升级即炸库或静默漏检。
- **周期扫描进程的生命周期。** 绑 127.0.0.1 的清单服务"存活期间"周期扫描——Claude Code 关了进程怎么收?端口被占(多开 Claude Code / 上次没退干净)怎么办?僵尸进程?**需定义:端口选择/冲突处理、PID 文件、优雅关闭、随会话结束的清理钩子(SessionEnd?)。**
- **多机 / 同一账号多设备。** 同学在宿舍和实验室两台 Mac 都装了,各自独立 SQLite,通知会在两台都弹(可接受),但"已勾选完成"不同步(可接受但需说明)。至少文档要承认这是已知行为。

### B.6 错误用户体验(降级与可读性)

- **首次设置失败的引导缺失。** 密码错、ADFS 改版、代理挡住——`/bb-setup` 失败时给什么人话提示?目前无设计。
- **"最近未扫 + 临近 ddl"提示已提到(§7),但阈值、展示位置(通知?清单页 banner?SessionStart 注入文案?)未定义。** 这是缓解"不开就不扫"的唯一手段,值得写细。
- **SessionStart 注入摘要的体量控制。** additionalContext 注入若把一堆待办塞进每次会话,既占 token 又烦人。**需定义:只注入 Top-N 临近 ddl + 新变化计数,可配置开关。**
- **通知风暴防护。** 第一次扫描(冷启动)会把 17 门课所有历史 column/公告/课件当"新"全部检出——**冷启动必须静默建立基线(只入库不通知),否则首次用就是几十条通知轰炸。文档完全没提冷启动语义,这是必现的糟糕首体验。**

### B.7 可观测性 / 日志脱敏

- **日志策略只有一句"不打印密码/token/cookie",无落地机制。** **建议:统一日志封装 + 出站前正则脱敏(cookie/Authorization/code/UserName/Password 一律打码);明确日志级别、日志文件位置与轮转、URL 里 query 参数(含 OAuth code)的脱敏。** OAuth 授权码 `code=` 会出现在 redirect URL,极易被顺手记进日志。
- **扫描结果的可审计性缺失。** 没有"上次扫了哪些课、各拉到多少项、新增几条、失败几门"的可查记录。**建议:scan_runs / events 表 + 清单页一个"扫描历史/健康"视图,让 no-miss 可被用户自检。**
- **健康自检 / 烟雾测试入口。** `/bb-doctor`:检查钥匙串凭据在不在、会话能否建立、代理通不通、DB 可写、端口可用——分发给同学后排障的刚需。

### B.8 测试与验证的空白(§13 强调但需补具体)

- **缺少录制的 API 夹具(fixtures)用于离线回归。** 真账号、有 MFA 风险、数据会变,**无法对 diff 算法做可重复单测**。建议把实测 JSON 脱敏后存为 fixtures,对 scanner/diff 做纯函数级测试(新增/删除/改期/出分/冷启动/分页边界/16 周拼接 各造一个用例)。
- **没有"对 BB 只读"的强保证测试。** 工具理论上只 GET,但需断言:绝不发非 GET 请求(下载的 302 跟随除外),防止哪天误调到提交/修改类端点。

### B.9 隐私 / 合规边界(实测已埋点但设计未表态)

- **`courses/{cid}/users` 含全班同学 PII,实测已标"隐私敏感未拉取",但设计文档未明文禁止。** **建议:写成硬约束——永不调用成员/花名册端点,即便将来想做"联系老师"功能也要单独评审。** 分发给同学时这是合规底线。
- **下载课件的版权 / 分发边界。** 镜像的课件可能含老师受版权材料,工具应提示"仅供个人学习,勿二次分发"。

### B.10 功能完整性的小缺口

- **公告正文里的考试/补课信息抽取**在 findings(§7)被列为高价值,但设计正文未承诺,只字面存 body。建议明确 v1 是否做(哪怕只是把 body 原样展示在清单页 + 关键词高亮"期中/补课/座位/exam")。
- **iCal 导出 / 周视图** findings 提及,设计未收录——可明确列为非目标或第三刀,避免范围漂移。
- **时区处理只说"UTC+8 展示"**,但 ddl 跨夏令时、用户在国外、`15:59:00Z`(=23:59 CST 这种"看似随意"的 UTC)的取整展示都需明确;**倒计时与"逾期标红"的判定基准时钟(本机 vs UTC)要钉死,否则边界处误报逾期。**

---

**评审结论:** 设计在"用稳定 id 全量 diff + per-user status 判完成"这一核心机制上是扎实且被实测支撑的。但有四个**必现且伤及 no-miss/no-duplicate 的硬缺口**未被覆盖,建议在动工前补进规格:(1) **ddl/字段级"更新"检测**(只比 id 会漏改期);(2) **冷启动静默建基线**(否则首用通知轰炸);(3) **三触发源并发的扫描锁 + 通知 outbox 幂等**;(4) **per-course 原子快照事务 + scan_runs 失败重扫**。其次补齐**卸载/凭据清理、schema 迁移、认证失败防账号锁定、性能预算与剪枝、日志脱敏落地**这五个运维级章节。

相关文件(绝对路径):设计文档 `/Users/mac/Programming/cuhkszbb/docs/superpowers/specs/2026-06-28-bbwatch-design.md`;实测依据 `/private/tmp/claude-501/-Users-mac-Programming-cuhkszbb/82db56a5-da23-42e2-a147-6a5d1bfa6443/scratchpad/bb_findings.md`。

---

## 附录 C: 审核定稿决议（实现前以本节为准）

> 主控对附录 A（鲁棒性审计）/ B（完整性复核）逐项裁决。**凡正文 §1–13 与本节冲突，以本节为准**；实现期每个 PR 须逐条对照本节验收。目的：把"绝不漏、绝不重复"从原则落成单一、无矛盾的实现契约。

### C.1 上线阻断项（必须先改，否则破坏核心不变量）

- **M3 通知失败处理 → 统一为单一状态机**：采用"成功路径原子不重弹 + 失败路径有限退避重投"。`claim_pending_events` 在**单事务内**"取出待发并置 NOTIFIED"，投递与置位同事务（成功后崩溃不重弹）；`osascript` 非 0/超时则该事件回落 `FAILED_NOTIFY(attempts+1, next_retry_at)`，`attempts<max(=5)` 下轮重取，`>=max` 入终态并在清单页标"通知失败"。§4.4.9"失败即终态、不重投"措辞**作废**。
- **M4 dedup 键唯一构造**：全局只保留一个 `make_dedup_key(event_type, entity_key, variant)`。variant 规则：NewMaterial→`modified`；DeadlineSoon→`due_utc|window_h`；DeadlineChanged→`new_due_utc`；GradePosted→`'graded'`（默认一列一次）。§5.3 `dedupe_tag` 旧定义**删除**。CI 加 grep 断言"无第二处键构造"。
- **M9 基线按维度独立**：baseline 粒度改为 `(course_pk, dimension)`。某维度（columns / announcements / contents / grades）成功拉全即落该维度基线并解除其抑制；**不因其它维度持续失败而连累**。`course_baseline` 表加 `dimension` 列。（杜绝"一棵坏内容树永久压制该课所有通知"。）
- **B.3 并发扫描锁**：三触发源（SessionStart / 手动 / 周期）共用 `~/.bbwatch/scan.lock` 文件锁 + SQLite `WAL` + `BEGIN IMMEDIATE` + `busy_timeout=5000`；后到者**跳过本轮**（去抖）。拿锁后重读基线/快照（双检）。

### C.2 必现正确性 / 体验缺口（动工前补进规格）

- **字段级"更新"检测（B.1，最关键）**：diff 不止比 id；快照保存关键字段并做字段级比较：
  - **ddl 改期**（column `grading.due` 变化）→ 触发 **`DeadlineChanged`** 提醒并刷新清单（**列为 MVP 必做**——改期最高频、最怕漏）；
  - 课件 `modified` 变化 → `MaterialUpdated`（§8.5.5 已有，保留）；
  - 公告 `title/body` 编辑（按内容 hash）→ 可选 `AnnouncementEdited`（默认关）。
- **冷启动静默建基线（B.6 / A.7）**：首次运行或新课首扫**只写快照、不发任何通知**，以各维度基线落定为准。验收：装好后首扫零通知。
- **per-course 原子快照 + scan_runs（B.1）**：每门课"全部分页 + 子资源成功"才提交该课快照；任一失败不污染该课快照、不推进水位。`scan_runs(course, dimension, status, complete, items, ts)` 记录；失败课下轮强制重扫。**`complete=True` 仅当分页见到末页且无重试降级（M7）**——"未抛错"不等于 complete。
- **differ 已知集含软删/归档（M6）**：所有 `known_*` 查询返回**含 `deleted/archived` 的全集**；软删/归档只影响展示，绝不影响 diff 去重（防"软删项再现被当新项重发"）。
- **撤分语义（M5）**：默认"一列一生只通知一次出分"，撤分后重批**不再**桌面提醒（清单/成绩页仍更新）；§5.6 不在 `became_graded` 上重 stage，消除与 §8.6 `UNIQUE` 的隐性矛盾。

### C.3 认证与凭据安全（B.2）

- **防学校账号锁定**：连续**凭据类**失败阈值（=3）即熔断；熔断状态**持久化** `auth_state.circuit_open_until`（跨进程、跨 SessionStart 生效）；熔断期扫描显式失败并提示 `/bb-setup` 重设密码；**绝不无限重试**。429/网络类不计入锁定计数（A.2 的 M 项一致）。
- **cookie / 会话态安全**：会话 cookie ≈ 登录态——存 `~/.bbwatch/session`，**0600 + 原子写（先写 .tmp 再 `os.replace`，创建即受限）**，不进日志；建议加密。（密码进钥匙串，cookie 至少 0600。）
- **MFA 兜底**：检测到 ADFS MFA challenge → 转半自动（清单页/通知提示手输验证码，会话尽量延长复用）。v1 实测无 MFA，仅预留接口与检测分支。
- **代理回退顺序（写死、可观测）**：环境代理(7890) curl_cffi → 失败探测直连 → 再失败回退子进程 curl。

### C.4 运维 / 可观测 / 合规（B.5 / B.7 / B.9）

- **卸载 `/bb-uninstall`**（分发底线）：删钥匙串凭据 + 会话文件 + SQLite + （询问后）下载镜像，并停清单服务。撤装后**学校密码不残留**。
- **schema 迁移**：建 `meta(schema_version)`，启动按版本跑迁移；禁止隐式炸库/静默漏检。
- **清单服务生命周期**：PID 文件 + 端口选择/冲突处理（占用则探测下一个并记录）+ 优雅关闭；可选 `SessionEnd` 钩子清理。
- **日志脱敏落地**：统一日志封装，出站前正则打码 `cookie/Authorization/code=/UserName/Password/学号/姓名`；日志 0600 + 轮转。**OAuth `code=` 在 redirect URL，必须脱敏**。
- **健康自检 `/bb-doctor`**：查钥匙串凭据、会话可建、代理可达、DB 可写、端口可用。
- **只读硬约束**：除附件下载的 302 跟随外**只发 GET**，CI 断言无非 GET；**永不调用 `courses/{cid}/users` 等花名册端点**（含全班 PII，合规底线）。
- **版权提示**：镜像课件标注"仅供个人学习，勿二次分发"。

### C.5 性能预算（B.4）

- 单次全量扫描目标 **< 60s**；per-user status **只对"带 due 且本地未终态"的 column** 拉取（剪枝）；列表用 `fields=` 裁剪；内容树按 `modified` 剪枝未变子树；per-course 并发上限（如 4）+ 请求间小延时；两次扫描最小冷却（5 分钟，手动可强制）；周期扫描默认间隔 ≥ 30 分钟（省电/温和）。

### C.6 确认保留（审计认定稳固，无需改）

全量 diff（非时间窗增量）、稳定 id 去重、per-user status 判完成、首扫抑制、改名只认 id、崩溃同事务、半截文件 `.part`+`os.replace` 原子落盘、会话失效请求级重放、404→未交仅限 `get_column_status`、汇总列（无 due）永不入作业流——保留。

### C.7 实现顺序的硬门（Definition of Done 摘要）

动工后合并任何"扫描/通知"相关代码前，下列断言必须在 CI 绿灯：① 同一只读状态连扫两次第二次产 0 事件；② 注入历史快照再扫恰好补齐且各一次；③ 冷启动零通知；④ 残缺/失败维度不推进 complete、不软删、不污染快照；⑤ 仅 GET（除 302 跟随）；⑥ 凭据/cookie/`code=` 在源码与日志样本 grep 零命中。
