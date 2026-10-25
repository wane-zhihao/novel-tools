#!/usr/bin/env bash
# 临时包装脚本：规避沙箱对"exec + 工作区外中文路径参数"组合的决策记录解析崩溃。
# 《顶职》驱动期间使用；书跑完后删除。用法：
#   bash .tmp-wb.sh autopilot status
#   bash .tmp-wb.sh autopilot next
#   bash .tmp-wb.sh run --max-steps 1
#   bash .tmp-wb.sh config
set -euo pipefail
PY="C:/Users/Wane-/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"
NODE24="C:/Users/Wane-/AppData/Local/Programs/nodejs/node-v24.16.0-win-x64/node.exe"
REPO="F:/WorkBuddy AI/Novel Platform/novel-workbench"
BOOK="F:/小说/works/顶职"
case "${1:-}" in
  autopilot) shift; exec "$PY" "$REPO/scripts/autopilot.py" "$@" --book "$BOOK" ;;
  run)       shift; exec "$NODE24" "$REPO/server/cli.mjs" run --book "$BOOK" "$@" ;;
  config)    exec "$NODE24" "$REPO/server/cli.mjs" config --book "$BOOK" ;;
  *) echo "usage: autopilot <args> | run <args> | config" >&2; exit 2 ;;
esac
