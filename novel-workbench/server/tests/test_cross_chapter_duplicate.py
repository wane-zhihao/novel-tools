#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_cross_chapter_duplicate.py — 跨章逐字重复检测的夹具

**为什么要有**：2026-11-18 三轮红队反复挖出同一类缺陷——「爽点只有一个模子」：

  · 第3章与第4章都是「听音→定位销→锉配代销→试车一刀→交件单打钩」，
    实测跨章最长公共子串 **26 字逐字相同**
  · 第7章与第9章塞尺句 18 字逐字重复
  · 第8章章内「毛茬朝一个方向倒」说了两遍

**特别值得记的一处**：我为了消除第 9 章的重复，把第 9 章改成
「一格一格试，试到哪一格涩住」——**结果和第 7 章一字不差**，
自己造出了一处新的逐字重复。**靠人工逐章比对抓不到这种自己制造的重复。**

检测方式：**跨章最长公共子串**（不是整段相同，是任意长度的共同片段）。
阈值 12 字——低于 12 字的中文短语在网文里是正常共用词（如「主轴断口上一层暗锈」）。

跑法：
  python -X utf8 server/tests/test_cross_chapter_duplicate.py
  → 全过打印 CROSS_CHAPTER_DUP_OK 并 exit 0
"""
import sys
import tempfile
import shutil
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from inline_runner import _cross_chapter_duplicate_findings          # noqa: E402

MIN_LEN = 12


def main() -> int:
    print("== 跨章逐字重复检测 ==")
    fails = []

    # ① 26 字逐字相同 → 必须报
    t1 = Path(tempfile.mkdtemp())
    try:
        (t1 / "正文").mkdir()
        shared = "陆明把工件卡上盘，刀具抵上去试车一刀，屑花卷成一条"
        (t1 / "正文" / "第003章_甲.md").write_text(
            f"他把锉刀放下。\n{shared}。\n交件单打钩。\n", encoding="utf-8")
        (t1 / "正文" / "第004章_乙.md").write_text(
            f"他换了把锉刀。\n{shared}。\n交件单打钩。\n", encoding="utf-8")
        files = sorted((t1 / "正文").glob("*.md"))
        found = _cross_chapter_duplicate_findings(files)
        hits = [x for x in found if x.get("type") == "cross-chapter-duplicate"]
        ok = len(hits) >= 1
        print(f"  {'PASS' if ok else 'FAIL'} [26 字逐字相同 → 报出]")
        if not ok:
            fails.append("long")
            print(f"        实得 {len(found)} 条")
        else:
            print(f"        {hits[0].get('message')[:90]}")
    finally:
        shutil.rmtree(t1, ignore_errors=True)

    # ② 11 字（低于阈值）→ 不报
    t2 = Path(tempfile.mkdtemp())
    try:
        (t2 / "正文").mkdir()
        short = "他伸手在棉纱上收拢"
        (t2 / "正文" / "第003章_甲.md").write_text(f"手停住了。\n{short}。\n", encoding="utf-8")
        (t2 / "正文" / "第004章_乙.md").write_text(f"他站着。\n{short}。\n", encoding="utf-8")
        files = sorted((t2 / "正文").glob("*.md"))
        found = _cross_chapter_duplicate_findings(files)
        hits = [x for x in found if x.get("type") == "cross-chapter-duplicate"]
        ok = len(hits) == 0
        print(f"  {'PASS' if ok else 'FAIL'} [11 字（低于阈值 {MIN_LEN}）→ 不报]")
        if not ok:
            fails.append("short")
    finally:
        shutil.rmtree(t2, ignore_errors=True)

    # ③ 同一章内的重复 → 不报（这是另一个检查的事）
    t3 = Path(tempfile.mkdtemp())
    try:
        (t3 / "正文").mkdir()
        dup = "他把那半截断销攥回手心"
        (t3 / "正文" / "第003章_甲.md").write_text(
            f"{dup}。\n隔了一会儿。\n{dup}。\n", encoding="utf-8")
        files = sorted((t3 / "正文").glob("*.md"))
        found = _cross_chapter_duplicate_findings(files)
        hits = [x for x in found if x.get("type") == "cross-chapter-duplicate"]
        ok = len(hits) == 0
        print(f"  {'PASS' if ok else 'FAIL'} [同章内重复 → 不报]")
        if not ok:
            fails.append("same")
    finally:
        shutil.rmtree(t3, ignore_errors=True)

    # ④ 完全不同的两章 → 不报
    t4 = Path(tempfile.mkdtemp())
    try:
        (t4 / "正文").mkdir()
        (t4 / "正文" / "第001章_甲.md").write_text(
            "劳资科的窗口刚拉开半扇，屋里一股纸墨味。\n", encoding="utf-8")
        (t4 / "正文" / "第009章_乙.md").write_text(
            "第二天他照常上工，卡尺挂回腰上。\n", encoding="utf-8")
        files = sorted((t4 / "正文").glob("*.md"))
        found = _cross_chapter_duplicate_findings(files)
        hits = [x for x in found if x.get("type") == "cross-chapter-duplicate"]
        ok = len(hits) == 0
        print(f"  {'PASS' if ok else 'FAIL'} [完全不同的两章 → 不报]")
        if not ok:
            fails.append("diff")
    finally:
        shutil.rmtree(t4, ignore_errors=True)

    # ⑤ LCS 实现本身必须正确（2026-11-18 修过一个真 bug）
    # **首版用「二分 + `in`」求最长公共子串，实测会报出根本不存在的片段**——
    # 它把第1↔2章的重复报成「这张卡要么进厂部的档案，要么」，
    # 而第2章里根本没有这句话。误报片段会误导改稿（照着不存在的句子去改）。
    from inline_runner import _lcs_contiguous
    ok1 = (_lcs_contiguous("abcXYZdef", "123XYZ789") == "XYZ")
    print(f"  {'PASS' if ok1 else 'FAIL'} [LCS 连续性：'abcXYZdef' vs '123XYZ789' → 'XYZ']")
    if not ok1:
        fails.append("lcs")
    ok2 = (_lcs_contiguous("甲乙丙丁", "戊己庚辛") == "")
    print(f"  {'PASS' if ok2 else 'FAIL'} [LCS 无公共串 → 空]")
    if not ok2:
        fails.append("lcs_empty")

    total = 6
    passed = total - len(fails)
    print(f"\n{passed}/{total} 通过")
    if fails:
        print("CROSS_CHAPTER_DUP_FAILED")
        return 1
    print("CROSS_CHAPTER_DUP_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
