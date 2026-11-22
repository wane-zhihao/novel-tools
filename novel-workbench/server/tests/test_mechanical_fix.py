#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_mechanical_fix.py — 机械修复器的确定性夹具

**为什么要有**：这四类句式被 check-ai-patterns.js 登记为 mechanical+blocking
（源码注释：「blocking 的含义就是不需判断、必须清零」），但 inline_runner 的
mechanical_fix() 此前一次都没修过它们，全部落到 N14 烧 LLM 轮次，而 N14 还会漏。

**这个夹具保护什么**：修复器的产物必须是**通顺的中文**。
首版实现在这里被自己的测试抓出两个病句：
  · 「这不是害怕，是愤怒」→「这愤怒」     （系词「是」被吃掉）
  · 「声音不大，却让所有人停了手」→「屋里的让所有人停了手」  （主语残缺）
所以每条断言都同时检查：①与期望完全一致；②产物不含主语残缺特征串。

跑法：
  python -X utf8 server/tests/test_mechanical_fix.py
  → 全过打印 MECHANICAL_FIX_OK 并 exit 0
"""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import inline_runner as ir                                    # noqa: E402

# 主语残缺 / 系词丢失的特征串：产物里出现任意一个即判失败
BROKEN_PATTERNS = ("的让", "的走", "的说", "的停", "的响", "的看", "，这愤怒", "，那齿轮")

CASES = [
    # (输入, 期望产物 或 None=应原样不动, 说明)
    ("他站起来，这不是害怕，是愤怒。", "他站起来，这是愤怒。", "不是X是Y → 留后项且保留系词"),
    ("那声音是齿轮，不是风声。", "那声音是齿轮。", "是X不是Y → 留前项"),
    ("这不是工期的问题，是他最后一次机会。", "这是他最后一次机会。", "长后项不被截断"),
    ("屋里的声音不大，却让所有人都停了手。", "屋里的声音让所有人都停了手。", "音量反差 → 保留主语"),
    ("他的嗓门不高，但整条通道都听见了。", "他的嗓门整条通道都听见了。", "音量反差（但）"),
    ("没有人说话，也没有人走。", "没有人走。", "否定排比 → 留末项"),
    ("他说：“这不是我的错，是他们的。”", None, "引号内台词豁免"),
    ("是不是他干的？", None, "「是不是」疑问句豁免"),
]

DASH_CASES = [
    ("他往前走——手里攥着那张纸——一步也没停。", "他往前走，手里攥着那张纸，一步也没停。", "破折号停顿 → 逗号"),
    ("他没有停——“你来了。”", "他没有停。“你来了。”", "破折号接引号 → 断句（打断语义用句号）"),
    ("没有破折号的句子。", "没有破折号的句子。", "无破折号不动"),
]


def _check(fn, cases, label):
    failed = []
    for src, expect, why in cases:
        out, notes = fn(src)
        ok = (out == expect) if expect is not None else (out == src)
        broken = [p for p in BROKEN_PATTERNS if p in out]
        if not ok or broken:
            failed.append((src, expect, out, why, broken))
        mark = "PASS" if (ok and not broken) else "FAIL"
        print(f"  {mark} [{why}]")
        print(f"        in : {src}")
        print(f"        out: {out}")
        if expect is not None and out != expect:
            print(f"        期望: {expect}")
        if broken:
            print(f"        !! 主语/系词残缺: {broken}")
    return failed


def main() -> int:
    print("== 固定句式模板 ==")
    f1 = _check(ir._fix_quote_patterns, CASES, "quote")
    print("\n== 破折号 ==")
    f2 = _check(ir._fix_em_dash, DASH_CASES, "dash")

    total = len(CASES) + len(DASH_CASES)
    failed = len(f1) + len(f2)
    print(f"\n{total - failed}/{total} 通过")
    if failed:
        print("MECHANICAL_FIX_FAILED")
        return 1
    print("MECHANICAL_FIX_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
