---
description: 查看未完成作业清单（按截止排序，带编号与完成状态）
---

运行 `${CLAUDE_PLUGIN_ROOT}/.venv/bin/bbwatch tasks` 并把清单原样展示给用户。
如用户随后说"第 N 个做完了/没做"，对应运行 `bbwatch done N` 或 `bbwatch undone N`。
