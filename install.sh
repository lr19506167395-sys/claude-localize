#!/bin/bash
# 安装 claude-localize 并立即打补丁
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if ! command -v pipx >/dev/null 2>&1; then
  echo "未找到 pipx。请先安装：brew install pipx"
  exit 1
fi

echo "安装 claude-localize…"
if pipx list 2>/dev/null | grep -q "claude-localize"; then
  pipx uninstall claude-localize >/dev/null 2>&1 || true
fi
pipx install --force "$DIR"

pipx ensurepath >/dev/null 2>&1 || true

CLI="$(command -v claude-localize || echo "$HOME/.local/bin/claude-localize")"
if [ ! -x "$CLI" ]; then
  CLI="$HOME/.local/bin/claude-localize"
fi

echo
echo "安装完成。"
echo
echo "接下来可以运行："
echo "  $CLI install --dry-run   # 预览"
echo "  $CLI install             # 正式安装"
echo "  $CLI status              # 查看状态"
