# bbwatch M2（监控核心：store + diff + scanner + notifier）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans / subagent-driven-development。TDD、每任务一提交。

**Goal:** 交付可用的 `bbwatch scan`（检测 新作业 / 改期 / 新公告 / 出分，写 SQLite，macOS 桌面通知）与 `bbwatch tasks`（列未完成作业），并以"全量 diff + 状态机 + 同事务"保证**绝不漏、绝不重复**。

**Architecture:** 纯逻辑（store/diff/状态机）与 IO（bbclient/notifier）分离，全部可注入、可 fixture 离线单测。详尽规格见 [详细设计 §4/§5/§8 与附录 C](../specs/2026-06-28-bbwatch-detailed-design.md)。

**承自 M1：** `Transport`/`BbClient`/`auth.login`/`Course` 已就绪。M2 不做：会话缓存与熔断、新课件树检测、下载、dashboard、插件外壳（→ M3）。`bbwatch scan` 暂每次全新登录（功能正确、足够温和）。

**不可违背的不变量（附录 C，实现期 CI 断言）：**
1. **dedup 键唯一来源** `make_dedup_key(event_type, entity_key, variant)`，禁止第二处构造。
2. **seen_entity 写入与 event 写入同一事务**（不存在"已 seen 未排队通知"窗口）。
3. **全量 diff**：与本地已知集合（**含 archived**）比对，不用时间窗增量。
4. **冷启动静默**：某 `(course, dimension)` 基线未建立则只写快照不发通知；该维度**本轮完整拉取成功**才建立基线。
5. **基线按维度** `(course_id, dimension)` 独立，互不连累。
6. **complete 闸门**：维度拉取残缺/失败则不建基线、不发通知、不污染（不软删）。
7. **改期检测**：column `grading.due` 变化 → `deadline_changed`（dedup variant=new_due）。
8. 时间存 UTC，展示转 +8。完成判定：`status ∈ {NeedsGrading, Graded}` 或 `score` 非空 或手动勾选。

---

## 文件结构

```
src/bbwatch/
  models.py        +Column, ColumnStatus, Announcement
  bbclient.py      +list_columns / get_column_status / list_announcements
  dedup.py         make_dedup_key 唯一来源
  store/
    __init__.py    Store 门面
    schema.sql     DDL: meta, seen_entity, event, course_baseline, scan_run, task_override
    store.py       连接(WAL/busy_timeout)、迁移、CRUD、diff、事件状态机
  scanner.py       编排 per-course 拉取→diff→事件；冷启动/基线/complete 闸门；课程过滤
  notifier.py      macOS osascript + claim/retry outbox
  cli.py           +scan / +tasks
tests/
  fixtures/        columns_*.json, column_status_*.json, announcements_*.json
  test_dedup.py test_store.py test_diff.py test_scanner.py test_notifier.py test_cli_scan.py
```

---

## Task 1: 模型与 BB 客户端扩展（columns / status / announcements）

**Files:** Modify `src/bbwatch/models.py`, `src/bbwatch/bbclient.py`；Create fixtures + `tests/test_bbclient_m2.py`

**DoD（测试断言）：**
- `Column(id,name,due_utc,content_id,score_possible)`；`ColumnStatus(status,score)`；`Announcement(id,title,created,body)`。
- `list_columns(cid)` 解析 `gradebook/columns`，**只返回带 `grading.due` 的列**（过滤 Total/Weighted Total），分页。
- `get_column_status(cid,colid,uid)`：返回 `ColumnStatus`；**HTTP 404 → ColumnStatus("None", None)**（仅此端点，见附录 A.3.7）；其余非 200 抛 `TransportError`。
- `list_announcements(cid)` 解析 `announcements`，分页。

**关键实现（bbclient 新增）：**
```python
def get_column_status(self, cid, colid, uid) -> ColumnStatus:
    url = f"{BB}{API}/courses/{cid}/gradebook/columns/{colid}/users/{uid}"
    r = self._t.request("GET", url, headers={"Accept": "application/json"})
    if r.status == 404:
        return ColumnStatus(status="None", score=None)
    if r.status != 200:
        raise TransportError(f"status {colid} -> {r.status}")
    j = r.json() or {}
    return ColumnStatus(status=j.get("status") or "None", score=j.get("score"))
```
`list_columns` 过滤：`due = (col.get("grading") or {}).get("due"); if not due: continue`。

TDD：先写 `test_bbclient_m2.py`（fixtures 含一个汇总列无 due + 两个带 due；status 404 用例），跑红→实现→跑绿→提交。

---

## Task 2: dedup 键唯一来源

**Files:** Create `src/bbwatch/dedup.py`, `tests/test_dedup.py`

**实现：**
```python
def make_dedup_key(event_type: str, entity_key: str, variant: str | None = None) -> str:
    return f"{event_type}|{entity_key}" + (f"|{variant}" if variant else "")
```
**DoD：** new_assignment/new_announcement/graded 无 variant；deadline_changed 带 `variant=new_due_utc` → 同列改两次期产生两个不同 key（不被误挡）；同 (type,entity) 无 variant 两次 → 同 key（去重）。

TDD：测试 4 条断言，红→绿→提交。

---

## Task 3: Store —— schema、迁移、连接

**Files:** Create `src/bbwatch/store/__init__.py`, `store/schema.sql`, `store/store.py`, `tests/test_store.py`

**schema.sql（要点，完整字段见详细设计 §5.3 + 附录 C）：**
```sql
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS seen_entity(
  entity_key TEXT PRIMARY KEY, kind TEXT NOT NULL, course_id TEXT NOT NULL,
  bb_id TEXT NOT NULL, due_utc TEXT, grade_status TEXT, grade_score REAL,
  payload_json TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
  first_seen_scan INTEGER, last_seen_scan INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS event(
  id INTEGER PRIMARY KEY AUTOINCREMENT, dedup_key TEXT NOT NULL UNIQUE,
  entity_key TEXT NOT NULL, event_type TEXT NOT NULL, state TEXT NOT NULL,
  title TEXT NOT NULL, detail TEXT, notify_attempts INTEGER NOT NULL DEFAULT 0,
  next_retry_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS course_baseline(
  course_id TEXT NOT NULL, dimension TEXT NOT NULL, established_at TEXT NOT NULL,
  PRIMARY KEY(course_id, dimension));
CREATE TABLE IF NOT EXISTS scan_run(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
  finished_at TEXT, status TEXT NOT NULL DEFAULT 'running');
CREATE TABLE IF NOT EXISTS task_override(
  entity_key TEXT PRIMARY KEY, manual_done INTEGER NOT NULL DEFAULT 0, updated_at TEXT);
```

**store.py 要点：**
- `Store(path)`：`sqlite3.connect`，`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON`，`row_factory=Row`，建表，写 `meta.schema_version=1`。支持 `path=":memory:"` 便于测试。
- 迁移：读 `meta.schema_version`，缺失则建库；为将来留 `_migrate()` 钩子。
- 时间统一 `now_utc() -> str`（ISO8601 Z），**作为可注入参数**便于测试确定性（Date.now 在脚本环境受限，但本地 datetime 可用；测试用固定时钟传入）。

**DoD：** 新建库 schema_version=1；二次打开不重建；WAL 生效；`:memory:` 可用。TDD 红→绿→提交。

---

## Task 4: 已知集合 + 全量 diff + 事件状态机（核心，纯逻辑）

**Files:** Modify `store/store.py`；Create `tests/test_diff.py`

**store.py 新增（签名）：**
```python
def known_entities(self, course_id, kind) -> dict[str, sqlite3.Row]: ...  # 含 archived！
def upsert_seen(self, *, entity_key, kind, course_id, bb_id, due_utc=None,
                grade_status=None, grade_score=None, payload, scan_id, now): ...
def baseline_established(self, course_id, dimension) -> bool: ...
def establish_baseline(self, course_id, dimension, now): ...
def stage_event(self, *, dedup_key, entity_key, event_type, title, detail, now) -> bool:
    # INSERT OR IGNORE; 返回是否真插入（已存在=False，天然去重）
def stage_seen_and_event(self, *, seen_kwargs, event: dict | None, now):
    # 单事务: upsert_seen + (可选)stage_event。event=None 表示冷启动/已知态只写快照
def claim_pending_events(self, limit=50) -> list[sqlite3.Row]:
    # 单事务取 state=PENDING_NOTIFY 且(next_retry_at 到点) 的事件（不在此处置 NOTIFIED）
def mark_notified(self, event_id, now): ...
def mark_failed(self, event_id, now, backoff_s): ...   # attempts++ ; >=5 → FAILED_NOTIFY
def outstanding_tasks(self) -> list[dict]:
    # 带 due 的 column 类 seen_entity, 未完成(grade_status not in {NeedsGrading,Graded} 且 score 空 且未手动勾选), 未 archived, 按 due 升序
def mark_manual_done(self, entity_key, done, now): ...
```

**diff（不变量验证用例，写在 test_diff.py）：**
- D1 注入新 column（基线已建）→ 恰好 1 个 new_assignment 事件；再 diff 同一状态 → 0 新事件（去重）。
- D2 冷启动（无基线）→ 0 通知，只写 seen；维度完整 → 建基线；下一轮新列才通知。
- D3 改期：已知列 due 变化 → 1 个 deadline_changed；同 due 再 diff → 0；再改一次新 due → 1（variant 不同）。
- D4 出分：status None→Graded（或 score 由空变非空）→ 1 个 graded；重复 → 0。
- D5 软删再现：列消失后再出现，entity_key 仍在 known（含 archived）→ 不重发 new_assignment。
- D6 改名：name 变、id 不变 → 不发 new_assignment（payload 更新）。
- D7 同事务：`stage_seen_and_event` 崩溃模拟（event 插入冲突回滚）→ seen 与 event 一致（要么都在要么都不在）。

TDD：逐条红→绿，**这是 M2 最关键的测试集**，全绿再提交。

---

## Task 5: Scanner —— 编排 + 冷启动 + complete 闸门 + 课程过滤

**Files:** Create `src/bbwatch/scanner.py`, `tests/test_scanner.py`

**签名与流程：**
```python
@dataclass
class ScanResult:
    new_events: list[dict]; courses_scanned: int; failures: list[str]

def scan(client: BbClient, store: Store, uid: str, *, now,
         current_terms: set[str] | None = None,
         course_filter: Callable[[Course], bool] | None = None) -> ScanResult:
```
流程（每门在读课，串行 + 维度独立）：
1. `courses = [c for c in client.list_courses(uid) if c.is_active and (term ok) and (filter ok)]`。
2. 对每门课，每个维度（columns、announcements）**各自 try/except**：
   - 拉全（分页）。任一异常 → 记 failure、`complete=False`、**跳过该维度的 diff/基线/通知**（不污染）。
   - columns 维度：对每列（带 due）取 status；任一 status 拉取失败 → 整维 `complete=False`（全有或全无）。
   - diff（见 Task 4 函数），`suppress = not store.baseline_established(cid, dim)`：
     - 新列：`stage_seen_and_event(event=new_assignment if not suppress else None)`。
     - due 变：`deadline_changed`（同样受 suppress）。
     - 出分：`graded`。
   - 维度 `complete and not baseline_established` → `establish_baseline(cid, dim)`。
3. 返回 ScanResult。

**DoD（test_scanner.py，用 FakeTransport+BbClient + 内存 Store + 固定 now）：**
- 首扫某课（多列+公告）→ 0 通知、建立两维基线、seen 写入。
- 第二扫注入 1 新列 + 1 改期 + 1 出分 + 1 新公告 → 恰好 4 个事件，各 1 次。
- 某课 announcements 维度抛错 → 该维度无事件/无基线，其它课与该课 columns 维度照常（基线独立）。
- 非在读课被跳过。

TDD 红→绿→提交。

---

## Task 6: Notifier（macOS）+ outbox

**Files:** Create `src/bbwatch/notifier.py`, `tests/test_notifier.py`

```python
class Notifier(Protocol):
    def send(self, title: str, message: str) -> None: ...   # 失败抛异常

class MacNotifier:
    def send(self, title, message):
        import subprocess
        script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=10)

def deliver_pending(store: Store, notifier: Notifier, now) -> int:
    sent = 0
    for ev in store.claim_pending_events():
        try:
            notifier.send(ev["title"], ev["detail"] or "")
            store.mark_notified(ev["id"], now)   # 投递与置位：成功后置 NOTIFIED
            sent += 1
        except Exception:
            store.mark_failed(ev["id"], now, backoff_s=300)   # 有限退避重投, >=5 终态
    return sent
```
**DoD：** 用 FakeNotifier（记录调用/可抛错）：成功 → NOTIFIED 且不再被 claim；失败 → attempts++ 仍 PENDING，第 5 次失败 → FAILED_NOTIFY 不再 claim；NOTIFIED 的不重发（去重）。TDD 红→绿→提交。

> 说明（附录 C M3）：`claim_pending_events` 取出后由 `deliver_pending` 在成功时 `mark_notified`、失败时 `mark_failed`；崩溃在投递后/置位前最坏重弹一次——可接受取舍，已在文档登记。

---

## Task 7: CLI —— scan / tasks

**Files:** Modify `src/bbwatch/cli.py`, Create `tests/test_cli_scan.py`

- `run_scan(transport, store, notifier, creds, login_fn, now)`：login → BbClient → `scan()` → `deliver_pending()` → 返回摘要文本（新增 N 条、未完成 M 条、最近 ddl）。
- `cmd_scan`：装配真实 `CurlCffiTransport / Store(AppPaths.db_path) / MacNotifier / load_credentials`。
- `cmd_tasks`：`store.outstanding_tasks()` → 按 ddl 升序打印（逾期标 [逾期]、≤24h 标 [紧急]），due 转 +8。
- main 注册 `scan` / `tasks`。

**DoD（test_cli_scan.py）：** 注入 FakeTransport(fixtures)+内存 Store+FakeNotifier+login no-op+固定 now：首扫摘要含"建立基线/0 新提醒"；二扫摘要含新事件计数；`outstanding_tasks` 排序正确、已完成不列。TDD 红→绿→提交。

---

## Task 8: 真实 e2e（人工，一次）

- [ ] `bbwatch scan`（首次）→ 应"静默建立基线、0 通知"，DB 生成。
- [ ] 再次 `bbwatch scan` → 若期间有新作业/改期/公告则弹通知，否则"无新增"。
- [ ] `bbwatch tasks` → 列出当前未完成作业，按 ddl 排序，时间为 +8。
- [ ] `grep -E "Lbw|Password|JSESSIONID|code=" ~/.bbwatch/bbwatch.log` → 无敏感。

---

## Self-Review
- **Spec 覆盖**：对应详细设计 §4(数据模型)/§5(状态机+幂等)/§8(扫描 diff)/附录 C(C.1 M3·M4·M9、C.2 改期/冷启动/原子/含 archived)。会话缓存/熔断/dashboard/下载/插件 → M3（本计划显式排除）。
- **占位符**：无 TBD；关键逻辑给了签名与核心代码，DDL 完整。
- **类型一致性**：`make_dedup_key(event_type,entity_key,variant)` 单一来源；`stage_seen_and_event(seen_kwargs,event,now)`；`ColumnStatus(status,score)`；`Store` 方法签名贯穿 scanner/notifier/cli；`now` 全程注入。
- **TDD/提交**：每任务红→绿→独立提交；Task 4 为核心不变量测试集。
