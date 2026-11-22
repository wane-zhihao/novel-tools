#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_causal_contract.py — 因果收尾率与因果密度上限不得互相矛盾

**为什么要有**（2026-11-20 实测）：

写作契约同一页上写了两条互相打架的规则：

  · 手法 1 自检：「收尾带因果词的段落应 **60%–85%**」
  · 生死线：「因果密度 **≤6.0/千字**」（14 本签约书标定的区间）

它们**在数学上不可兼得**：
  · 闸门按 `clarity_org_probe` 的 `CAUSAL` 词表（16 个词）算因果密度；
  · 生死线「段/千字 33.1–54.1」⇒ 2200 字的稿子有 **73–119 段**；
  · 6.0/千字 × 2.2 千字 = **全章最多 13 个**被计分的因果词；
  · 而 60% 的 73 段 = **44 段**要带因果词 → 需要 44 个词，上限只给 13 个。

后果实测到了：一个 writer 在第 6 章直接向编排者报告「这两条规则互斥」，
另一个 writer 只好用**闸门不统计**的词（反倒/到底/毕竟）凑收尾率——
收尾率达标了，但那些词根本不在 `CAUSAL` 词表里，等于用漏洞而不是用规则。

修法：以**标定过的密度区间**为准（1.0–6.0/千字，14 本签约书，不能改），
把收尾率改成算得通的值（约 1/4–1/3），并把算术写进契约，让下一个 writer
不必再自己发现矛盾。

本夹具做三件事：
  ① 契约与 N12 提示词里**不得**再出现 60%–85% / ≥60% 这类算不通的收尾率；
  ② 必须同时写明密度区间与算术说明；
  ③ **算术自检**：从契约里读出段/千字带与密度上限，直接算出收尾率可达上限，
     断言「契约写下的收尾率目标 ≤ 算出来的上限」——以后谁再写死一个数，
     这个夹具会当场挂掉。

跑法：
  python -X utf8 server/tests/test_causal_contract.py
  → 全过打印 CAUSAL_CONTRACT_OK 并 exit 0
"""
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
CONTRACT = SCRIPTS.parent / "references" / "writing-contract.md"
sys.path.insert(0, str(SCRIPTS))

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'} [{name}]" + ("" if ok else f" —— {detail}"))
    if not ok:
        FAILS.append(name)


def main() -> int:
    print("== 因果收尾率 × 因果密度：不得互相矛盾 ==")
    if not CONTRACT.is_file():
        print(f"  FAIL [找不到 {CONTRACT}]")
        print("CAUSAL_CONTRACT_FAILED")
        return 1
    text = CONTRACT.read_text(encoding="utf-8")

    # ① 旧的算不通目标必须消失——只查**规则句**（`**自检**` 那一行），
    #    修正说明里为了讲清来龙去脉会**引述**旧规则，那不算违规。
    selfcheck = next((ln for ln in text.splitlines() if ln.startswith("**自检**")), "")
    check("契约自检行不再要求 60% 的因果收尾率",
          bool(selfcheck) and "60" not in selfcheck,
          f"自检行仍有 60%：{selfcheck[:120]}")
    check("契约给出算得通的收尾率（约 1/4–1/3）",
          "1/4" in text and "1/3" in text,
          "契约没写算得通的收尾率")

    # ② 密度区间与算术说明必须都在
    check("契约写明密度区间 1.0–6.0/千字",
          ("1.0" in text and "6.0" in text and "千字" in text),
          "契约没写密度区间")
    check("契约给出互斥的算术说明（13 个词 vs 44 段）",
          "互斥" in text and "44" in text and "13" in text,
          "契约没解释为什么 60% 不可行")

    # ③ 算术自检：从契约的**目标值表**里读出段/千字带与密度上限，算出收尾率可达上限
    band = re.search(r"段/千字\s*\|[^\n]*?\*\*\s*([\d.]+)\s*[^0-9\n]{1,5}\s*([\d.]+)", text)
    cap = re.search(r"因果连接词/千字\s*\|[^\n]*?\*\*\s*([\d.]+)\s*[^0-9\n]{1,5}\s*([\d.]+)", text)
    target = re.search(r"收尾带因果词的段落[^\n]{0,16}?(\d)\s*/\s*(\d)[^\n]{0,4}?(\d)\s*/\s*(\d)", text)
    if not (band and cap and target):
        check("能从契约里读出段/千字带、密度上限与收尾率目标", False,
              f"band={bool(band)} cap={bool(cap)} target={bool(target)}")
    else:
        para_lo = float(band.group(1))
        cap_hi = float(cap.group(2))
        t_lo = float(target.group(1)) / float(target.group(2))
        t_hi = float(target.group(3)) / float(target.group(4))
        # 全章允许的「计分」因果词数（6.0/千字 × 2.2 千字）
        words = 2.2 * cap_hi
        # 再加「不计分但可进 diversity 检查」的轮换词：果然/正因为/偏偏/以至于，
        # 各 ≤3 次（契约里的轮换硬规矩）→ 最多 12 个。
        # 这两个词表在 clarity_org_probe.py 里是分开的，契约与算术自检必须同一口径。
        rotation_allowance = 4 * 3
        # 段/千字带下段数；收尾率上限按**最少段数**（最宽松）算
        paras_lo = 2.2 * para_lo
        reachable = (words + rotation_allowance) / paras_lo
        check("收尾率目标 ≤ 密度带下的可达上限",
              t_hi <= reachable,
              f"目标上限 {t_hi:.2f} > 可达 {reachable:.3f}"
              f"（{cap_hi}/千字 × 2.2 = {words:.1f} 个计分词 + {rotation_allowance} 个轮换词"
              f" ÷ {paras_lo:.1f} 段）")

    # ④ N12 提示词同步
    import pipeline_spec as ps
    st = {"cursor": "C1.N12", "current_chapter": 0, "chapters_committed": 0,
          "max_chapters": 12, "plan": None, "genre": "niandai", "words_total": 0,
          "target_words": 20000, "nodes": {}}
    prompt = ps.chapter_nodes(st, Path("F:/小说/works/冷启动验收书"), 1)["C1.N12"]["prompt"]
    check("N12 提示词不再要求 ≥60% 的因果收尾率",
          "带因果词收尾的段落 ≥60%" not in prompt
          and "收尾带因果词的段落 ≥60%" not in prompt,
          "N12 提示词里还有旧的 60% 目标")
    check("N12 提示词写明密度区间与算术上限",
          "1.0–6.0/千字" in prompt and "13 个" in prompt,
          "N12 提示词没写密度区间/算术")
    check("N12 提示词保留「密度超上限是硬闸门」",
          "密度超 6.0" in prompt or "密度超上限" in prompt,
          "N12 提示词没强调密度是硬闸门")

    print(f"\n{'全部通过' if not FAILS else '失败: ' + ', '.join(FAILS)}")
    if FAILS:
        print("CAUSAL_CONTRACT_FAILED")
        return 1
    print("CAUSAL_CONTRACT_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
