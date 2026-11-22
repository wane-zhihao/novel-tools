#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_coldstart.py — 冷启动夹具：新书开箱即具备检测能力

**为什么要有**：2026-11-18~19 四轮红队挖出 28 项 B 级缺陷，其中「日期网断裂」
占 8+ 项。这些**可自动检出**——但前提是书里有两样东西：
  1. `追踪/时间线/日链.md`：把每章钉到绝对日 + 逐字锚句
  2. `追踪/复沓锚句.md`：登记**刻意**的跨章呼应（白名单）

**问题**：此前这两样只在被调试过的书里手工长出来——**新书一开就没有**，
于是这些检测对新书全部失效（冷启动实测：日链检测对新书静默返回 0 条，
既不报也不提示）。

**修法**：`autopilot.py init` 生成两个骨架模板（`autopilot._scaffold_book_files`），
新书开箱即具备检测能力；日链表虽空，但校验器立刻生效。

跑法：
  python -X utf8 server/tests/test_coldstart.py
  → 全过打印 COLDSTART_OK 并 exit 0
"""
import sys
import os
import json
import pathlib
import shutil
import tempfile
import subprocess
from pathlib import Path

WORKBENCH = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(WORKBENCH))

import autopilot as ap                                        # noqa: E402

PY = sys.executable
AUT = str(WORKBENCH / "autopilot.py")
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def main() -> int:
    print("== 冷启动：新书开箱即具备检测能力 ==")
    fails = []

    # ① init 后应生成两个骨架文件
    tmp = Path(tempfile.mkdtemp())
    try:
        book = tmp / "冷启动书"
        r = subprocess.run(
            [PY, "-X", "utf8", AUT, "init", "--book", str(book),
             "--title", "冷启动书", "--genre", "niandai",
             "--target-words", "20000", "--max-chapters", "12"],
            capture_output=True, encoding="utf-8", env=ENV)
        ok = (r.returncode == 0)
        print(f"  {'PASS' if ok else 'FAIL'} [init 成功]")
        if not ok:
            fails.append("init")
        for rel in ("追踪/时间线/日链.md", "追踪/复沓锚句.md"):
            p = book / rel
            o = p.is_file() and p.stat().st_size > 200
            print(f"  {'PASS' if o else 'FAIL'} [骨架已生成：{rel}]")
            if not o:
                fails.append(rel)
        # 骨架里应有关键说明，不能是空壳
        dc = (book / "追踪" / "时间线" / "日链.md")
        if dc.is_file():
            t = dc.read_text(encoding="utf-8")
            o = ("日链" in t and "绝对日" in t and "锚句" in t)
            print(f"  {'PASS' if o else 'FAIL'} [日链模板含填写说明]")
            if not o:
                fails.append("dc_template")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ② 已存在的书不应被覆盖（init 会拒绝，但 _scaffold 本身要幂等）
    tmp2 = Path(tempfile.mkdtemp())
    try:
        book2 = tmp2 / "已有书"
        (book2 / "追踪" / "时间线").mkdir(parents=True)
        custom = book2 / "追踪" / "时间线" / "日链.md"
        custom.write_text("# 我自己写的日链\n| 章 | 绝对日 | 锚句 |\n", encoding="utf-8")
        made = ap._scaffold_book_files(book2)
        ok = (custom.read_text(encoding="utf-8") == "# 我自己写的日链\n| 章 | 绝对日 | 锚句 |\n"
              and "追踪/时间线/日链.md" not in made)
        print(f"  {'PASS' if ok else 'FAIL'} [已存在的日链文件不被覆盖]")
        if not ok:
            fails.append("idempotent")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    # ③ 缺日链文件时，检测器应明确报出而不是静默返回 0
    tmp3 = Path(tempfile.mkdtemp())
    try:
        from inline_runner import _daychain_findings
        book3 = tmp3 / "无基准书"
        (book3 / "正文").mkdir(parents=True)
        (book3 / "正文" / "第001章_甲.md").write_text("今天才断。\n", encoding="utf-8")
        found = _daychain_findings(book3)
        has = any("日链" in x.get("message", "") or "基准" in x.get("message", "")
                  for x in found)
        print(f"  {'PASS' if found and has else 'FAIL'} "
              f"[缺日链基准 → 明确报出（不静默通过）]")
        if not found or not has:
            fails.append("no_baseline")
            for x in found:
                print("        ", x.get("message", "")[:110])
    finally:
        shutil.rmtree(tmp3, ignore_errors=True)

    # ④ 真实书：init 不应影响它（骨架已存在）
    real = pathlib.Path(r"F:\小说\works\逆袭从掌控系统开始")
    if (real / "追踪" / "时间线" / "日链.md").is_file():
        before = (real / "追踪" / "时间线" / "日链.md").read_text(encoding="utf-8")
        made = ap._scaffold_book_files(real)
        after = (real / "追踪" / "时间线" / "日链.md").read_text(encoding="utf-8")
        ok = (before == after and "追踪/时间线/日链.md" not in made)
        print(f"  {'PASS' if ok else 'FAIL'} [真实书的日链文件不被覆盖]")
        if not ok:
            fails.append("real_safe")

    total = 5
    passed = total - len(fails)
    print(f"\n{passed}/{total} 通过")
    if fails:
        print("COLDSTART_FAILED")
        return 1
    print("COLDSTART_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
