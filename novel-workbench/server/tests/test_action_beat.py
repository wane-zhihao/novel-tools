#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_action_beat.py — 动作节拍密度检测的夹具

**为什么要有**：2026-11-17 拿到七猫侧第 2 个真实拒稿本（《边镇小吏》）后，
`action_beat_per_1000` 在 2 本拒稿书上 AUC=0.972、簇自助 CI [0.931, 1.0]、
按书簇 permutation_p=0.0095 —— 已满足 auc/ci_low/p 三条门槛，
**只差「拒稿书≥3」**（实际 2/14，预注册门槛 3/6）。

因此本指标**只能作定位线索、不能作改稿目标**（判别力不变式），
工具侧只报 advisory。

**这个夹具保护什么**：
首版检测器**自己实现了一遍口径**，结果对两本真实拒稿样本都报 0 条
——口径错了等于没测。权威定义在 `craft_probe.py`
（「对话之外、含身体动作动词的分句数」），必须**直接复用**，不得自研。
这个夹具用真实拒稿样本当断言基准：口径再错一次立刻失败。

跑法：
  python -X utf8 server/tests/test_action_beat.py
  → 全过打印 ACTION_BEAT_OK 并 exit 0
"""
import sys
import pathlib
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
TOOLS = pathlib.Path(r"F:\小说\platforms\_core\tools")
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TOOLS))

import inline_runner as ir                                       # noqa: E402

REJECTED = [pathlib.Path(r"F:\小说\works\顶职\正文"),
            pathlib.Path(r"F:\小说\works\边镇小吏\正文")]
SIGNED = pathlib.Path(r"F:\小说\platforms\qimao\corpus\positive_niandai")


def main() -> int:
    print("== 动作节拍密度检测 ==")
    fails = []

    # ① 两本真实拒稿样本必须报出（口径对不对，一次见分晓）
    total_rej = 0
    for d in REJECTED:
        if not d.exists():
            print(f"  SKIP [{d.name}] 样本目录不存在")
            continue
        files = sorted(d.glob("*.md"))
        items = ir._action_beat_findings(files)
        total_rej += len(items)
        ok = len(items) >= 5          # 拒稿样本大部分章节应超阈值
        print(f"  {'PASS' if ok else 'FAIL'} [{d.name}] {len(files)} 章 → 报 {len(items)} 条")
        if not ok:
            fails.append(d.name)
            print("        口径可能又错了——必须复用 craft_probe.metrics()")

    # ② 签约样本不应大面积报出（否则阈值过松，全是误报）
    if SIGNED.exists():
        files = sorted(SIGNED.glob("*.md"))
        items = ir._action_beat_findings(files)
        rate = len(items) / max(len(files), 1)
        ok = rate <= 0.25
        print(f"  {'PASS' if ok else 'FAIL'} [签约样本 {len(files)} 章 → 报 {len(items)} 条，"
              f"比例 {rate:.0%} ≤25%]")
        if not ok:
            fails.append("signed_fp")

    # ③ 本书的实测值必须落在拒稿区间（记录基线，防止口径漂移）
    mine = pathlib.Path(r"F:\小说\works\逆袭从掌控系统开始\正文")
    if mine.exists():
        files = sorted(mine.glob("*.md"))
        items = ir._action_beat_findings(files)
        print(f"  INFO [本书] {len(files)} 章 → 报 {len(items)} 条"
              f"（2026-11-17 基线：8 章全报，本书动作节拍落在拒稿区间）")
        # 基线漂移告警：若突然变成 0 条，说明口径变了
        if files and len(items) == 0:
            print("        !! 与基线不符（当时 8 章全报）——口径可能漂移")
            fails.append("baseline")

    total = 3
    passed = total - len(fails)
    print(f"\n{passed}/{total} 通过")
    if fails:
        print("ACTION_BEAT_FAILED")
        return 1
    print("ACTION_BEAT_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
