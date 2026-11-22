#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_foreshadow_due.py — 到期伏笔显式标注的夹具

**为什么要有**：伏笔只以「埋第2章｜第6章」这种形式出现时，writer 看到
「第6章」无法判断是「本章到期」还是「已经过了」。实测 F005/F008 的计划回收章
都是 6，第 6 章封板后仍未回收，N19 只给 advisory 不阻断——**伏笔逾期没有机制拦住**。

修法：N11 组装上下文时，把「计划回收章 ≤ 本章」且未回收的伏笔显式列进 S6，
并区分「本章到期」与「已逾期」，writer 一眼能看出轻重。

跑法：
  python -X utf8 server/tests/test_foreshadow_due.py
  → 全过打印 FORESHADOW_DUE_OK 并 exit 0
"""
import sys
import json
import tempfile
import shutil
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import inline_runner as ir                                       # noqa: E402


def _due_block(ledger: dict, chapter: int) -> str:
    """复刻 N11 里生成「到期伏笔」那段逻辑，便于单测。

    与 inline_runner._assemble_context 内的实现保持同构；
    这里只取判定与文案，不依赖整本书的结构。
    """
    must_resolve, overdue = [], []
    for fid, row in sorted(ledger.items()):
        if not isinstance(row, dict):
            continue
        planned = row.get("planned_resolution_chapter")
        if not isinstance(planned, int) or planned > chapter:
            continue
        if row.get("status") in ("已回收", "放弃", "已过期"):
            continue
        summary = str(row.get("summary") or "")[:60]
        if planned < chapter:
            overdue.append(f"{fid}（计划第{planned}章回收，已逾期）：{summary}")
        else:
            must_resolve.append(f"{fid}（本章到期）：{summary}")
    parts = []
    if overdue:
        parts.append("**已逾期未回收的伏笔，本章必须收掉或明确交代延后原因**：" + "；".join(overdue))
    if must_resolve:
        parts.append("**本章到期、必须在正文里回收的伏笔**：" + "；".join(must_resolve))
    return "\n".join(parts)


def main() -> int:
    print("== 到期伏笔标注 ==")
    ledger = {
        "F001": {"planned_resolution_chapter": 5, "status": "已回收", "summary": "已回收的"},
        "F005": {"planned_resolution_chapter": 6, "status": "已埋", "summary": "旧点检卡"},
        "F008": {"planned_resolution_chapter": 6, "status": "已埋", "summary": "手电光"},
        "F002": {"planned_resolution_chapter": 8, "status": "已埋", "summary": "抚恤金"},
        "F003": {"planned_resolution_chapter": None, "status": "已埋", "summary": "无计划"},
        "F009": {"planned_resolution_chapter": 6, "status": "放弃", "summary": "已放弃"},
    }
    fails = []

    # 第6章：F005/F008 本章到期
    b6 = _due_block(ledger, 6)
    ok6 = ("F005（本章到期）" in b6 and "F008（本章到期）" in b6
           and "逾期" not in b6 and "F002" not in b6 and "F009" not in b6)
    print(f"  {'PASS' if ok6 else 'FAIL'} [第6章：本章到期]")
    if not ok6:
        print(f"        {b6}"); fails.append("c6")

    # 第7章：F005/F008 已逾期
    b7 = _due_block(ledger, 7)
    ok7 = ("已逾期" in b7 and "F005（计划第6章回收，已逾期）" in b7
           and "F008（计划第6章回收，已逾期）" in b7 and "F002" not in b7)
    print(f"  {'PASS' if ok7 else 'FAIL'} [第7章：已逾期]")
    if not ok7:
        print(f"        {b7}"); fails.append("c7")

    # 第5章：都还没到期（F005 计划6 > 5）
    b5 = _due_block(ledger, 5)
    ok5 = (b5 == "")
    print(f"  {'PASS' if ok5 else 'FAIL'} [第5章：无到期]")
    if not ok5:
        print(f"        {b5!r}"); fails.append("c5")

    # 已回收/已放弃/无计划 都不应出现
    ok_excl = ("F001" not in b7 and "F009" not in b7 and "F003" not in b7)
    print(f"  {'PASS' if ok_excl else 'FAIL'} [排除已回收/已放弃/无计划]")
    if not ok_excl:
        fails.append("excl")

    total = 4
    passed = total - len(fails)
    print(f"\n{passed}/{total} 通过")
    if fails:
        print("FORESHADOW_DUE_FAILED")
        return 1
    print("FORESHADOW_DUE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
