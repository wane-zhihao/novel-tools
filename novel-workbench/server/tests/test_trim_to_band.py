#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_trim_to_band.py — 字数裁剪器的确定性夹具

**为什么要有**：storyctl 对超长章只**报**「remove_to_internal_band: min..max」，
不提供任何压缩实现。结果每一章超长都要人工逐段读、逐段判断删哪句——
第 2 章手工裁 689 字，第 4 章补 500 字。这是全流程最大的一块人工成本。

**这个夹具保护什么**（首版被自己的测试抓出三个真缺陷）：
  1. 打分含 `- len(para)//60` 长度惩罚 → 「那张纸上有三个字：抚恤金。」(11 字)
     被判低价值删掉，旁边 20 个重复动作段全留。→ 已删掉长度惩罚。
  2. 缺「信息承载」信号 → 抚恤金段与环境段同为 0 分，排序随机删。
     → 已补「纸/信/账/单/写着/三个字…」等信号。
  3. 贪心删到不达标就继续删 → 吃掉 15 分的信息段。
     → 已加 `_TRIM_SCORE_FLOOR`，分数超阈值即停。
  4. 下限保护只在全删完后检查一次 → 712→163 裁穿 band_min 却仍写入。
     → 已改逐段累计检查，并加「区间不可满足」前置判定。

跑法：
  python -X utf8 server/tests/test_trim_to_band.py
  → 全过打印 TRIM_TO_BAND_OK 并 exit 0
"""
import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, r"C:\Users\Wane-\.qoder\skills\story-long-write\scripts")

import inline_runner as ir                                       # noqa: E402
from wordcount_core import count_visible_chars                    # noqa: E402

ENV_PARA = ("屋里的光从高窗斜下来，落在地上一片灰白，远处传来机器声，"
            "空气里有股铁锈味，风从窗缝钻进来，整个屋子安静得能听见心跳，"
            "影子拉得很长很长。")

# 每例都必须活下来的三处关键内容（首段冲突 / 信息承载段 / 末段钩子）
MUST_SURVIVE = ("推开门", "抚恤金", "脚步声")


def _build(env_count: int) -> Path:
    tmp = Path(tempfile.mkdtemp())
    (tmp / "正文").mkdir()
    paras = ["他推开门，屋里的东西全被翻乱了。", "“你回来得正好。”那人说。"]
    for i in range(env_count):
        paras.append(ENV_PARA)
        paras.append(f"他走到第{i}个柜子前，拉开抽屉。")
    paras += ["那张纸上有三个字：抚恤金。", "门外传来脚步声，停在了门口。"]
    (tmp / "正文" / "第001章_测试.md").write_text("\n\n".join(paras), encoding="utf-8")
    return tmp


def _case(env_count, band_max, band_min=None, label="", expect_noop=False) -> bool:
    tmp = _build(env_count)
    try:
        body = tmp / "正文" / "第001章_测试.md"
        before = count_visible_chars(body.read_text(encoding="utf-8"))
        n, notes = ir.trim_to_band(tmp, 1, band_max=band_max, band_min=band_min)
        after = count_visible_chars(body.read_text(encoding="utf-8"))
        txt = body.read_text(encoding="utf-8")
        joined = " ".join(notes)

        if expect_noop:
            ok = (n == 0 and after == before)
            why = "" if ok else "应原样不动却改了"
        else:
            keep_min = (band_min is None or after >= band_min)
            keep_info = all(k in txt for k in MUST_SURVIVE)
            reached = (after <= band_max) or ("可删段落已用尽" in joined)
            ok = keep_min and keep_info and reached
            why = ("" if ok else
                   f"下限={keep_min} 内容={keep_info} 达标或报告={reached}")
        print(f"  {'PASS' if ok else 'FAIL'} [{label}] {before} → {after} 删{n}段")
        if why:
            print(f"        {why}")
        if not ok:
            missing = [k for k in MUST_SURVIVE if k not in txt]
            if missing:
                print(f"        丢失关键内容: {missing}")
        return ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("== 裁剪器 ==")
    results = [
        _case(6, 400, label="小超长"),
        _case(10, 300, label="中超长"),
        _case(12, 220, label="临界"),
        _case(4, 200, label="少环境段"),
        _case(3, 150, label="极短"),
        _case(20, 250, 200, label="大章+下限"),
        _case(8, 100, 150, label="区间不可满足", expect_noop=True),
    ]
    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} 通过")
    if passed != total:
        print("TRIM_TO_BAND_FAILED")
        return 1
    print("TRIM_TO_BAND_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
