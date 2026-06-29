---
description: 配置 bbwatch（录入学校账号密码到 macOS 钥匙串）
---

`bbwatch setup` 需要在**终端**里交互输入密码（不回显），无法在对话里安全完成。请告诉用户：

在终端运行：
```
cd <bbwatch 目录> && .venv/bin/bbwatch setup
```
按提示输入 `学号@link.cuhk.edu.cn` 与密码（存入 macOS 钥匙串，不落盘明文）。
完成后即可使用 /bb-scan、/bb-tasks、/bb-download。
