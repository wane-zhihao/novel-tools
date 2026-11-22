#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_gate_prompt_schema.py — 闸门提示词必须自带裁决结构

**为什么要有**（2026-11-19 冷启动实测）：

`N06 设定校验` 的提示词只写「**格式同 N04**」，而 subagent 根本看不到 N04 的提示词。
于是 judge 自己发明了一套键名（`checks` / 顶层 `quote`），
`done` 以 `quote_check_failed: items 为空` 拒收——**任何书跑到 N06 都会卡在这里**。

这类缺陷的特点：**提示词之间互相引用，但引用目标对执行者不可见。**
写法上「格式同 N04」读起来很自然，运行时却等于没说。

修法：每个 verify 节点的提示词必须**自带完整 JSON 结构**，不依赖跨节点引用。

本夹具检查：
  ① 每个 verify 节点提示词都含 `"items"` 结构
  ② 每个 verify 节点提示词都含 `quote` / `source` / `verdict` / `level` 四个键名
  ③ 提示词里不得出现「格式同 N」这种对执行者不可见的引用
  ④ 提示词必须说明 `items` 不可为空（否则 judge 可能只写 summary）

跑法：
  python -X utf8 server/tests/test_gate_prompt_schema.py
  → 全过打印 GATE_SCHEMA_OK 并 exit 0
"""
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pipeline_spec as ps                                      # noqa: E402

NEEDED_KEYS = ('"items"', '"quote"', '"source"', '"verdict"', '"level"')
# 「格式同 N04」这类引用：执行者看不到 N04 提示词，等于没说
BAD_REF = re.compile(r"格式同\s*N\d", re.I)


def main() -> int:
    print("== 闸门提示词自带裁决结构 ==")
    fails = []
    gates = {k: v for k, v in ps.GLOBAL_NODES.items() if v.get("kind") == "verify"}
    print(f"  （共 {len(gates)} 个 verify 节点：{', '.join(sorted(gates))}）")

    for k in sorted(gates):
        p = gates[k].get("prompt") or ""
        name = gates[k].get("name")

        missing = [key for key in NEEDED_KEYS if key not in p]
        ok = not missing
        print(f"  {'PASS' if ok else 'FAIL'} [{k} {name}：结构键齐全]"
              + ("" if ok else f" —— 缺 {missing}"))
        if not ok:
            fails.append(f"{k}-keys")

        bad = BAD_REF.search(p)
        print(f"  {'PASS' if not bad else 'FAIL'} [{k} {name}：无跨节点格式引用]"
              + ("" if not bad else f" —— 出现「{bad.group(0)}」"))
        if bad:
            fails.append(f"{k}-ref")

        nonempty = ("不可为空" in p) or ("为空会被" in p) or ("items 不可" in p)
        print(f"  {'PASS' if nonempty else 'FAIL'} [{k} {name}：说明 items 不可为空]")
        if not nonempty:
            fails.append(f"{k}-empty")

    total = 3 * len(gates)
    passed = total - len(fails)
    print(f"\n{passed}/{total} 通过")
    if fails:
        print("GATE_SCHEMA_FAILED")
        return 1
    print("GATE_SCHEMA_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
