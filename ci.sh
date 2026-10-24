#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PY="${WORKBENCH_PYTHON:-python}"

while IFS= read -r -d '' file; do
  node --check "$file"
done < <(find novel-workbench/server -name '*.mjs' -print0)

"$PY" -m compileall -q novel-workbench/scripts novel-workbench/server/tests/python_contracts.py
npm test --prefix novel-workbench
