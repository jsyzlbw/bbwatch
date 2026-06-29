---
description: 增量镜像某门课的全部课件到本地
argument-hint: "<课程编号或代码，如 MAT3007>"
---

先运行 `"${CLAUDE_PLUGIN_DATA}/.venv/bin/bbwatch" courses` 让用户确认课程编号（若用户已给出课程代码可跳过）。
然后运行 `"${CLAUDE_PLUGIN_DATA}/.venv/bin/bbwatch" download $ARGUMENTS`，把下载结果（新下载/跳过/失败数与落盘目录）汇报给用户。
