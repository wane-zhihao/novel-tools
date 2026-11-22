#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_causal_diversity.py — 因果词多样性检测的夹具

**为什么要有**：因果**总量**指标测不出 AI 指纹。实测第 3 章「果然」x44 时，
causal_per_1000 只报 3.07（在区间内）、闸门全绿——但 44 次同一个词
是编辑一眼就看得出的机械指纹。只有**占比**看得出。

阈值 0.60 来自 18 个七猫年代签约样本的实测上界（最大 0.600、中位 0.429）。

**只报 advisory 不阻断**：本指标没过配对判别验证（《顶职》第1章 0.375
落在七猫签约样本区间内），按项目判别力不变式，不能进 gates.json 的区间判定。

跑法：
  python -X utf8 server/tests/test_causal_diversity.py
  → 全过打印 CAUSAL_DIVERSITY_OK 并 exit 0
"""
import sys
import tempfile
import shutil
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import inline_runner as ir                                       # noqa: E402


def _write(tmp: Path, name: str, paras: list[str]) -> Path:
    d = tmp / "正文"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text("\n\n".join(paras), encoding="utf-8")
    return p


def main() -> int:
    print("== 因果词多样性检测 ==")
    fails = []

    # ① 刷同一词 → 必报
    t1 = Path(tempfile.mkdtemp())
    try:
        p = _write(t1, "第001章_刷词.md",
                   ["开场。"] + [f"他做了第{i}件事，果然没有下文。" for i in range(20)] + ["结尾。"])
        items = ir._causal_diversity_findings([p])
        ok = len(items) == 1 and items[0]["type"] == "causal-word-repetition" \
            and items[0]["blocking"] is False
        print(f"  {'PASS' if ok else 'FAIL'} [刷同一词 20 次 → 报 1 条 advisory]")
        if not ok:
            fails.append("brush")
            print(f"        实得 {len(items)} 条")
    finally:
        shutil.rmtree(t1, ignore_errors=True)

    # ② 正常轮换 → 不报
    t2 = Path(tempfile.mkdtemp())
    try:
        paras = []
        for i, w in enumerate(["原来", "难怪", "结果", "所以", "于是", "因此", "这才"]):
            paras.append(f"第{i}件事，{w}有下文。")
        paras += ["他站起来。", "门开了。", "他走了。"]
        p = _write(t2, "第001章_轮换.md", paras)
        items = ir._causal_diversity_findings([p])
        ok = len(items) == 0
        print(f"  {'PASS' if ok else 'FAIL'} [7 种词轮换 → 不报]")
        if not ok:
            fails.append("rotate")
            for x in items:
                print(f"        {x['message'][:80]}")
    finally:
        shutil.rmtree(t2, ignore_errors=True)

    # ③ 因果词太少 → 不报（占比无意义）
    t3 = Path(tempfile.mkdtemp())
    try:
        p = _write(t3, "第001章_稀疏.md", ["他站起来。", "原来是这样。", "门开了。"])
        items = ir._causal_diversity_findings([p])
        ok = len(items) == 0
        print(f"  {'PASS' if ok else 'FAIL'} [因果词仅 2 个 → 不报（样本不足）]")
        if not ok:
            fails.append("sparse")
    finally:
        shutil.rmtree(t3, ignore_errors=True)

    # ④ 单个词占比 0.5 → 不报（低于 0.60 上界）
    t4 = Path(tempfile.mkdtemp())
    try:
        paras = []
        for i in range(5):
            paras.append(f"第{i}件事，果然有下文。")
        for i, w in enumerate(["原来", "难怪", "结果", "所以"]):
            paras.append(f"第{i}件事，{w}有下文。")
        p = _write(t4, "第001章_半超.md", paras)
        items = ir._causal_diversity_findings([p])
        ok = len(items) == 0
        print(f"  {'PASS' if ok else 'FAIL'} [占比 5/9=0.56 → 不报]")
        if not ok:
            fails.append("under")
            for x in items:
                print(f"        {x['message'][:90]}")
    finally:
        shutil.rmtree(t4, ignore_errors=True)

    total = 4
    passed = total - len(fails)
    print(f"\n{passed}/{total} 通过")
    if fails:
        print("CAUSAL_DIVERSITY_FAILED")
        return 1
    print("CAUSAL_DIVERSITY_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
