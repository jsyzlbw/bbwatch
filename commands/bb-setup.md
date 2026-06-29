---
description: 配置 bbwatch（把学校账号密码存入 macOS 钥匙串）
---

向用户索取 `学号@link.cuhk.edu.cn` 与密码。拿到后，**把凭据放进环境变量**（避免明文出现在命令参数里）并运行：

```
BBWATCH_USERNAME='学号@link.cuhk.edu.cn' BBWATCH_PASSWORD='密码' "${CLAUDE_PLUGIN_DATA}/.venv/bin/bbwatch" setup
```

随后运行 `"${CLAUDE_PLUGIN_DATA}/.venv/bin/bbwatch" doctor` 确认配置成功。提醒用户：密码只存于本机 macOS 钥匙串。
