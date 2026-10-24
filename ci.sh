#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PY="${WORKBENCH_PYTHON:-python}"

# engines 要求 Node >=24：低于 24 会在 api.test.mjs 撞 fs.cpSync 段错误
# （Node 22 便携构建的问题，非产品缺陷）。提前失败并给 hint，别跑一半才炸。
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
if [ "$NODE_MAJOR" -lt 24 ]; then
  echo "ERROR: 需要 Node >=24（当前 $(node -v 2>/dev/null || echo 未知)）。" >&2
  echo "本机可用：C:\\Users\\Wane-\\AppData\\Local\\Programs\\nodejs\\node-v24.16.0-win-x64\\node.exe" >&2
  exit 1
fi

while IFS= read -r -d '' file; do
  node --check "$file"
done < <(find novel-workbench/server -name '*.mjs' -print0)

"$PY" -m compileall -q novel-workbench/scripts novel-workbench/server/tests/python_contracts.py
npm test --prefix novel-workbench
