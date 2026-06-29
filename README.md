# bbwatch

> 港中深（CUHK-SZ）Blackboard 的**作业雷达 + 课件下载器**——装进 Claude Code，平时**跟它说话**就行，不用记命令。

老师在 `bb.cuhk.edu.cn` 上布置作业、发公告**不一定发邮件**，很容易漏做；期末想**批量下课件**又得一个个点。bbwatch 帮你盯着 BB，有新作业 / 改期 / 公告 / 出分就提醒你，维护一份漂亮的任务清单，还能一句话把整门课的课件下到本地。

---

## ✨ 能帮你做什么

- 🔔 **自动发现**新作业、**作业改期**、新公告、**出分**、新课件——不再靠刷 BB。
- 📋 **任务清单**：未完成作业按截止排序，逾期标红、临近标橙，可勾选完成（网页 + 命令行）。
- ⬇️ **一键下课件**：按"课程/文件夹"结构增量镜像，往年卷自动归到 `_exams/`。
- 💬 **对话式**：直接跟 Claude 说"我有什么作业 / 扫一下 / 下 MAT3007 的课件"。
- 🌗 **日夜双主题**看板，跟随系统、记住选择。
- 🔒 密码只存你本机钥匙串，只读你自己的账号。

---

## 🚀 安装（最省事：让 AI 帮你装）

打开 Claude Code，在对话框发**一句话**：

> 帮我安装这个 Claude Code 插件：`https://github.com/jsyzlbw/bbwatch`
> 我的账号是 `你的学号@link.cuhk.edu.cn`，密码是 `你的密码`

AI 会自动建环境、装好、把密码存进你本机钥匙串。**新开一个 Claude Code 会话**即可使用。

> 前提：已装 **Claude Code** 和 **Python ≥ 3.11**（macOS / Linux）。
> 想手动安装 / 排障 / 给同学发说明，见 **[INSTALL.md](INSTALL.md)**。

---

## 💬 怎么用：直接跟 Claude 说话

装好后**新开会话**，像聊天一样用（举几个例子）：

**① 查作业 / 看 ddl**
> **你：** 我还有什么作业？
> **bbwatch：** 📋 2 项未完成
> · Homework 4（MAT3007）07-01 23:59 — 还剩 2 天
> · Mock Quiz（DDA2001）— 已逾期 5 天

**② 扫一下有没有新东西**
> **你：** 扫一下，有没有新作业或者出分了
> **bbwatch：** 扫描完成（17 门课，约 40 秒）。发现：
> · 🆕 新作业 CSC3001《Assignment 3》，截止 07-05
> · ✅ MAT3007《Homework 2》出分了

**③ 下载某门课的课件**
> **你：** 把 MAT3350 的课件都下下来
> **bbwatch：** 已下载 13 个文件到 `~/Downloads/bbwatch/MAT3350:Information_Theory/`（讲义 ch1–12、作业、往年卷已归入 `_exams/`）。

**④ 标记完成 / 撤销**
> **你：** 第 1 个我做完了
> **bbwatch：** 已把 Homework 4（MAT3007）标记为已完成 ✓

> 也支持斜杠命令：`/bb-scan`、`/bb-tasks`、`/bb-download`、`/bb-setup`。

---

## 📋 任务看板（网页）

让 Claude 运行、或自己在终端跑：

```bash
bbwatch dashboard
```

浏览器打开 **http://127.0.0.1:8765/**：按截止排序、逾期/紧急高亮、点复选框勾选完成、右上角 **☀ / 🌙 切换日间/夜间**、"立即扫描"按钮。（只绑本机，别人访问不到。）

---

## 🔔 开 Claude Code 就自动提醒

插件的 SessionStart 钩子：**每天打开 Claude Code**，开头就会自动告诉你最近的 ddl 和新变化，并在后台静默刷新（超过 2 小时才扫，不卡你）。

---

## ⌨️ 命令行（不想说话也行）

| 命令 | 作用 |
|---|---|
| `bbwatch setup` | 录入学校账号密码（存钥匙串） |
| `bbwatch scan` | 扫描 BB，检测新变化并桌面通知 |
| `bbwatch tasks` | 列出可跟踪作业（编号 + ○/✓） |
| `bbwatch done N` / `undone N` | 标记第 N 项 完成 / 未完成 |
| `bbwatch courses` | 列出在读课程 |
| `bbwatch download MAT3007` | 下载某门课全部课件 |
| `bbwatch find slides` | 在已下载课件里检索（不联网） |
| `bbwatch dashboard` | 打开网页任务看板 |
| `bbwatch doctor` | 自检（凭据/会话/数据库/端口） |
| `bbwatch uninstall` | 清除凭据/会话（可 `--purge-db`） |

---

## 🔒 隐私与安全

- 密码**只存你本机 macOS 钥匙串**，绝不上传、不进日志、不进仓库。
- 只读**你自己**的账号数据；除下载课件外只发只读请求；**从不访问花名册等含他人信息的接口**。
- 任务、下载的课件都只在你本机。
- 下载的课件仅供个人学习，请勿二次分发。

---

## 🧰 给开发者

```bash
git clone https://github.com/jsyzlbw/bbwatch && cd bbwatch
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q          # 全套测试
```

引擎为纯 Python（`src/bbwatch/`），核心机制：用稳定 id 与本地 SQLite 全量 diff 保证**绝不漏**、稳定 id + 去重保证**绝不重复**；并行抓取提速。详尽设计与对抗式审计文档在 [`docs/superpowers/`](docs/superpowers/)。

---

## 🗺️ 路线图

- ✅ 已做：作业/改期/公告/出分/新课件 监控、任务清单（网页+CLI）、增量下载、往年卷归集、本地检索、会话缓存与熔断、并行扫描、日夜主题。
- ⏳ 待做：邮件 / Telegram 通知渠道、Windows 原生支持（暂可用 WSL）。

---

## 工作原理（一句话）

全校统一 AD FS 登录 → 用会话 cookie 调 Blackboard 官方 REST API → SQLite 全量 diff 检出变化 → 桌面通知 + 任务清单。是"用你自己的账号、读你自己的数据"，合理合规。
