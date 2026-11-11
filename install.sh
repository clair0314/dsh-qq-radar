#!/usr/bin/env bash
# 把 qq-digest 安装成 DeepSeek Harness 技能：默认 ~/.agents/skills/qq-digest
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${1:-${QQDIGEST_SKILL_DIR:-$HOME/.agents/skills/qq-digest}}"
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"

if [ "$DEST" = "$HERE" ]; then
  echo "目标目录就是源码目录，换个位置或直接运行 ./bin/qq-digest" >&2
  exit 1
fi

if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    --exclude 'examples/shot*.png' \
    --exclude 'examples/demo-open.html' \
    "$HERE"/ "$DEST"/
else
  cp -R "$HERE"/. "$DEST"/
  rm -rf "$DEST/.git" "$DEST/examples/shot"*.png "$DEST/examples/demo-open.html"
fi

chmod +x "$DEST/bin/qq-digest"

echo "已安装到：$DEST"
echo
echo "下一步："
echo "  $DEST/bin/qq-digest init       # 生成配置文件"
echo "  $DEST/bin/qq-digest check      # 测试 OneBot 连接"
echo "  $DEST/bin/qq-digest digest --since 24h --open"
echo
echo "重启 DeepSeek Harness 后，技能列表里会出现 qq-digest。"
