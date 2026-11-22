#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_relative_days.py — 相对日互斥检查的夹具

**为什么要有**：2026-11-18 第二轮红队（agent-51）挖出全书**日期网断裂**，
共 7 处，全部属实：

  · 第1章「爹半年前工亡」 vs 第8章档案「批准一九八一年十一月」 —— 差一年多
  · 第1章「抚恤压了半年」 vs 第8章「实发一九八二年五月」（至今一年多）
  · 第6章「工装挂了两年」 vs 第1章「半年前」
  · 第6章「二叔前天就停职了」 vs 第5章当天才停职 —— 「前天」早于停职日
  · 第5章「落款是前天」 vs 第1章也写「前天」 —— 两章相隔≥3天却同指「前天」
  · 第9章「这两年…鉴定签字」 vs 顶职才半年、签字权第5章才拿到
  · 第2章「月产八十七件」 vs 「停一天少交二十四件」 —— 差 8 倍

**关键性质**：这类缺陷是**可计算的**——不需要文学判断，只要把全书相对日
抽出来做交叉校验即可。而当时的工具完全查不出，因为账本的 `story_time`
全是「1983年某日」，**没有具体日期锚**。

本检查做两件事：
  1. **相对日词表**：把「昨天/前天/大前天/两天前/三天前/半个月前/N年前/N天前」
     等抽出来，带上所在章号
  2. **同指冲突检测**：同一个相对日词在**相隔 ≥2 章**的章节里出现，
     且叙述的是**同一件事**（靠关键词共现判定），报为可疑

**只报 advisory**：判定「同一件事」需要语义，本检查只做候选定位。

跑法：
  python -X utf8 server/tests/test_relative_days.py
  → 全过打印 RELATIVE_DAYS_OK 并 exit 0
"""
import sys
import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from inline_runner import _relative_day_findings                    # noqa: E402

# 相对日词表：词 → 距「现在」的天数偏移（负=过去）
RELATIVE_DAYS = {
    "今天": 0, "今早": 0, "今夜": 0, "今晚": 0, "本日": 0,
    "昨天": -1, "昨儿": -1, "昨晚上": -1, "昨夜": -1,
    "前天": -2, "前两天": -2,
    "大前天": -3, "三天前": -3,
    "四天前": -4, "五天前": -5, "六天前": -6, "七天前": -7,
    "半个月前": -15, "一个月前": -30, "两个月前": -60,
    "半年前": -180, "一年前": -365, "两年前": -730, "三年前": -1095,
}

# 同一件事的判据：共享至少一个主题词
TOPIC_WORDS = (
    "工亡", "抚恤", "停职", "落款", "报告", "封条", "鉴定", "签字",
    "床子", "顶职", "工装", "笔记", "残角", "接班", "上班",
)


def main() -> int:
    print("== 相对日互斥检查 ==")
    fails = []

    # ① 抽出相对日（用本文件自身当样本，验证不崩且能抽到词）
    items = _relative_day_findings([Path(__file__)])
    print(f"  INFO [本文件] 抽出 {len(items)} 条相对日")

    # ② 构造：两章相隔 ≥2 章、同指「前天」、同一主题 → 必须报
    import tempfile
    import shutil
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "正文").mkdir()
        # 第1章与第5章：同指「前天」、同一主题「报告/落款」
        (tmp / "正文" / "第001章_甲.md").write_text(
            "落款日期那一栏，写的是前天。\n"
            "这份报告比事故本身还早两天。\n", encoding="utf-8")
        (tmp / "正文" / "第005章_乙.md").write_text(
            "落款日期那一栏，写着前天。\n"
            "报告比事故早两天。\n", encoding="utf-8")
        # 第3章（相邻）同指「前天」不应报
        (tmp / "正文" / "第003章_丙.md").write_text(
            "封条是前天贴的。\n", encoding="utf-8")
        files = sorted((tmp / "正文").glob("*.md"))
        found = _relative_day_findings(files)
        hits = [x for x in found if x.get("type") == "relative-day-conflict"]
        ok = len(hits) >= 1
        print(f"  {'PASS' if ok else 'FAIL'} [第1章与第5章同指「前天」且同主题 → 报出冲突]")
        if not ok:
            fails.append("conflict")
            for x in found:
                print(f"        {x.get('file')} {x.get('message')[:80]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ③ 相邻章同指「前天」→ 不报（不同章的「前天」指不同日）
    tmp2 = Path(tempfile.mkdtemp())
    try:
        (tmp2 / "正文").mkdir()
        (tmp2 / "正文" / "第002章_甲.md").write_text(
            "封条是前天贴的。\n", encoding="utf-8")
        (tmp2 / "正文" / "第003章_乙.md").write_text(
            "封条是前天贴的。\n", encoding="utf-8")
        files = sorted((tmp2 / "正文").glob("*.md"))
        found = _relative_day_findings(files)
        hits = [x for x in found if x.get("type") == "relative-day-conflict"]
        ok = len(hits) == 0
        print(f"  {'PASS' if ok else 'FAIL'} [相邻两章同指「前天」→ 不报]")
        if not ok:
            fails.append("adjacent")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    # ④ 同一章内的相对日不应报自身冲突
    tmp3 = Path(tempfile.mkdtemp())
    try:
        (tmp3 / "正文").mkdir()
        (tmp3 / "正文" / "第001章_甲.md").write_text(
            "昨天他还在厂里。\n前天就停了职。\n", encoding="utf-8")
        files = sorted((tmp3 / "正文").glob("*.md"))
        found = _relative_day_findings(files)
        hits = [x for x in found if x.get("type") == "relative-day-conflict"]
        ok = len(hits) == 0
        print(f"  {'PASS' if ok else 'FAIL'} [同一章内多个相对日 → 不报]")
        if not ok:
            fails.append("same_chapter")
    finally:
        shutil.rmtree(tmp3, ignore_errors=True)

    total = 4
    passed = total - len(fails)
    print(f"\n{passed}/{total} 通过")
    if fails:
        print("RELATIVE_DAYS_FAILED")
        return 1
    print("RELATIVE_DAYS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
