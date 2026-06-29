---
name: bb-assistant
description: 当用户询问作业、截止日期(ddl)、课程公告、成绩，或想下载课件、查看/勾选待办时，使用 bbwatch 的 MCP 工具帮助管理 CUHK-SZ Blackboard。
---

# bbwatch 助手

通过 bbwatch 的 MCP 工具帮用户管理港中深 Blackboard。按意图选择工具：

- 用户问"我有什么作业 / 还有哪些 ddl / 待办" → 调用 `list_tasks`。若怀疑数据过时，可先 `scan_now` 再 `list_tasks`。
- "扫一下 / 有没有新作业、新公告、出分了吗" → 调用 `scan_now`，汇报新事件。
- "把第 N 个标记完成 / 没做完" → 调用 `mark_task_done`（n 为 list_tasks 中的编号，done 为 true/false）。
- "下载 X 课的课件 / 把某门课的资料下下来" → 先 `list_courses` 确认课程，再 `download_course`（ref=编号或课程代码，dest 可选目录）。
- "我有哪些课" → 调用 `list_courses`。

用简洁中文汇报结果。截止时间已是本地时间(+8)。若工具返回"未找到凭据"，提示用户先在终端运行 `bbwatch setup`。
