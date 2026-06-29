#!/usr/bin/env bash
# 插件安装后自举(由 Setup 钩子在 `claude --init-only` 时触发)：
# 在持久目录 CLAUDE_PLUGIN_DATA 建 venv 并安装引擎(插件缓存里没有 .venv)。
# 若设置了 BBWATCH_USERNAME/BBWATCH_PASSWORD，顺手非交互写入钥匙串。
set -e

DATA="${CLAUDE_PLUGIN_DATA:?CLAUDE_PLUGIN_DATA 未设置}"
ROOT="${CLAUDE_PLUGIN_ROOT:?CLAUDE_PLUGIN_ROOT 未设置}"
VENV="$DATA/.venv"

mkdir -p "$DATA"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --upgrade pip >/dev/null 2>&1 || true
"$VENV/bin/pip" install -q "$ROOT"                                  # 首次装依赖+引擎
# 同版本号更新时 pip 会跳过，故强制刷新引擎代码(仅包本身，不重装依赖)
"$VENV/bin/pip" install -q --no-deps --force-reinstall "$ROOT"

# 可选：随安装一并配置凭据(同学的 AI 可在 --init-only 前导出这两个变量)
if [ -n "$BBWATCH_USERNAME" ] && [ -n "$BBWATCH_PASSWORD" ]; then
  "$VENV/bin/bbwatch" setup >/dev/null 2>&1 || true
fi

echo "bbwatch: 引擎已安装到 $VENV"
exit 0
