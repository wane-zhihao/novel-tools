#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v node >/dev/null 2>&1; then
  echo "[错误] 没有找到 Node.js。请安装 Node.js 24 或更高版本。" >&2
  exit 1
fi

major="$(node -p "process.versions.node.split('.')[0]")"
if [ "$major" -lt 24 ]; then
  echo "[错误] 需要 Node.js 24 或更高版本，当前主版本为 ${major}。" >&2
  exit 1
fi

exec node novel-workbench/server/index.mjs --open "$@"
