#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_daychain.py — 日链一致性校验的夹具

**为什么要有**：2026-11-18 四轮红队（agent-47/51/52/53）挖出的最重一类缺陷
是**日期网断裂**，共 8+ 处：

  · 同一份报告的落款日在第1章与第5章都叫「前天」（相隔 3 天）
  · 第5章「落款是事故前两天」紧跟「断轴是那天早上七点二十」被读成同日
  · 第6章「二叔前天就停职」早于第5章的停职日
  · 父亲死亡时间三处互斥（半年前 / 挂了两年 / 档案 1981-11）
  · 第5章「封条是事故前一天贴的」，而第3章「今天才贴」+第4章「昨天贴」
    锁死封条于 D1（事故**后**一天）——差两天
  · 第9章「这两年…鉴定签字」而签字权第5章才拿到（D3 → D7 只隔 4 天）

**这些全部可计算**——把每章钉到绝对日（D0…D7）上，交叉校验就能查出来。
本夹具验证 `daychain_check.py` 按 `追踪/时间线/日链.md` 逐条核对正文锚句。

跑法：
  python -X utf8 server/tests/test_daychain.py
  → 全过打印 DAYCHAIN_OK 并 exit 0
"""
import sys
import subprocess
import tempfile
import shutil
from pathlib import Path

TOOLS = Path(r"F:\小说\platforms\_core\tools")
sys.path.insert(0, str(TOOLS))

import importlib.util                                        # noqa: E402

spec = importlib.util.spec_from_file_location("daychain_check", TOOLS / "daychain_check.py")
dc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dc)


def _book(tmp: Path, chain_md: str, chapters: dict) -> Path:
    book = tmp / "book"
    (book / "正文").mkdir(parents=True)
    (book / "追踪" / "时间线").mkdir(parents=True)
    (book / "追踪" / "时间线" / "日链.md").write_text(chain_md, encoding="utf-8")
    for name, body in chapters.items():
        (book / "正文" / name).write_text(body, encoding="utf-8")
    return book


def main() -> int:
    print("== 日链一致性校验 ==")
    fails = []

    # ① 锚句全部命中 → PASS
    t1 = Path(tempfile.mkdtemp())
    try:
        chain = """# 日链

| 章 | 绝对日 | 锚句 |
|---|---|---|
| 1 | **D0** | 「今天才断」「落款日期那一栏，写的是前两天」 |
| 2 | **D1** | 「封条是今天才贴的」 |
"""
        chapters = {
            "第001章_甲.md": "主轴带病转了半个月，今天才断。\n落款日期那一栏，写的是前两天。\n",
            "第002章_乙.md": "封条是今天才贴的。\n",
        }
        book = _book(t1, chain, chapters)
        ok, rep = dc.check_daychain(book)
        print(f"  {'PASS' if ok else 'FAIL'} [锚句全部命中 → pass]")
        if not ok:
            fails.append("hit")
            for i in rep:
                print("        ", i)
    finally:
        shutil.rmtree(t1, ignore_errors=True)

    # ② 锚句被改 → fail
    t2 = Path(tempfile.mkdtemp())
    try:
        chain = """# 日链

| 章 | 绝对日 | 锚句 |
|---|---|---|
| 1 | **D0** | 「今天才断」 |
"""
        chapters = {"第001章_甲.md": "主轴带病转了半个月，前天才断。\n"}
        book = _book(t2, chain, chapters)
        ok, rep = dc.check_daychain(book)
        print(f"  {'PASS' if not ok else 'FAIL'} [锚句被改 → fail]")
        if ok:
            fails.append("miss")
    finally:
        shutil.rmtree(t2, ignore_errors=True)

    # ③ 缺日链文件 → 明确报错（不静默通过）
    t3 = Path(tempfile.mkdtemp())
    try:
        book = t3 / "book"
        (book / "正文").mkdir(parents=True)
        (book / "正文" / "第001章_甲.md").write_text("今天才断。\n", encoding="utf-8")
        ok, rep = dc.check_daychain(book)
        has = any("日链" in x or "缺失" in x for x in rep)
        print(f"  {'PASS' if not ok and has else 'FAIL'} [缺日链文件 → 明确报错]")
        if ok or not has:
            fails.append("nofile")
    finally:
        shutil.rmtree(t3, ignore_errors=True)

    # ④ 真实书籍：按现有日链校验应通过
    real = Path(r"F:\小说\works\逆袭从掌控系统开始")
    if (real / "追踪" / "时间线" / "日链.md").is_file():
        ok, rep = dc.check_daychain(real)
        print(f"  {'PASS' if ok else 'FAIL'} [真实书籍日链一致]")
        if not ok:
            fails.append("real")
            for i in rep[:6]:
                print("        ", i)

    total = 4
    passed = total - len(fails)
    print(f"\n{passed}/{total} 通过")
    if fails:
        print("DAYCHAIN_FAILED")
        return 1
    print("DAYCHAIN_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
