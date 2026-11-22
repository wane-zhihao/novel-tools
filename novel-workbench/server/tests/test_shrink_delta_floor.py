#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_shrink_delta_floor.py — 压缩器保底长度的夹具

**为什么要有**：2026-11-17 实测踩到压缩器把 `delta.result` 砍成 **4 个字**
（「陆明用爹」），然后原样写进账本与 `追踪/上下文.md` 的近三章速记里——
一句 60 字的结果说明变成了碎片。根因：

    cut = max(4, len(val) - over - 8)

`over`（超出的字节数）大于字段长度时，`len(val) - over - 8` 下溢成负数，
`max(4, ...)` 取 4 → 字段被砍到 4 字。

**性质**：这不是「截断」，是**信息摧毁**。而且它发生在封板前的自动规整里，
结果会被当成原文写进账本——**伪装成了原始内容**。

修法：每个字段有保底长度（result ≥24 字、summary/objective_fact ≥16 字…），
砍到保底就换砍下一个字段；全部到保底则返回 None（交上层报明确错误）。

**首版修法仍有洞**：只把 `max(4, ...)` 换成 `max(floor, ...)`，
但没处理「所有字段都到保底」的情况——那会死循环 600 轮后返回 None，
虽然不摧毁信息，但会浪费 600 轮。夹具逼出「先筛 viable 再砍」的结构。

跑法：
  python -X utf8 server/tests/test_shrink_delta_floor.py
  → 全过打印 SHRINK_DELTA_FLOOR_OK 并 exit 0
"""
import sys
import json
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from inline_runner import shrink_delta_to_fit                      # noqa: E402

LIMIT = 3072
BASE = "陆明用爹的笔记做出双锥夹头工装绕开设备科自行立项"


def main() -> int:
    print("== 压缩器保底长度 ==")
    fails = []

    # ① 超限很大时，result 不得被砍成碎片
    d = {"result": BASE * 60,
         "character_changes": [{"name": "陆明", "change": "由硬顶规矩转成用签字权绕开规矩"}],
         "timeline_events": [{"id": "E019", "objective_fact": "陆明递草图被压回"}]}
    out = shrink_delta_to_fit(d, LIMIT)
    if out is None:
        print("  PASS [超限过大 → 返回 None（交上层报错），不摧毁信息]")
    else:
        r = out["result"]
        ok = len(r) >= 24
        print(f"  {'PASS' if ok else 'FAIL'} [result 保底 ≥24 字，实得 {len(r)} 字]")
        if not ok:
            fails.append("floor")
            print(f"        「{r}」")

    # ② result 内容不得残缺成半句（不以省略号结尾、不超长）
    if out is not None:
        r = out["result"]
        ok = len(r) >= 24 and "…" not in r
        print(f"  {'PASS' if ok else 'FAIL'} [result 不含省略号、不成半句]")
        if not ok:
            fails.append("ellipsis")

    # ③ 常规超限：按比例缩、结果在限内、关键字段仍完整
    d2 = {"result": BASE * 12,
          "character_changes": [{"name": "陆明", "change": "由硬顶规矩转成用签字权绕开规矩" * 3}],
          "timeline_events": [{"id": "E019",
                               "objective_fact": "陆明递草图被压回，设备科等件单跟着压下来" * 3}]}
    out2 = shrink_delta_to_fit(d2, LIMIT)
    if out2 is None:
        print("  FAIL [常规超限不应返回 None]")
        fails.append("normal")
    else:
        sz = len(json.dumps(out2, ensure_ascii=False).encode("utf-8"))
        ok = sz <= LIMIT and len(out2["result"]) >= 24
        print(f"  {'PASS' if ok else 'FAIL'} [常规超限 → {sz}B ≤{LIMIT}，result {len(out2['result'])} 字]")
        if not ok:
            fails.append("normal")

    # ④ 所有字段都到保底 → 必须返回 None（不是死循环，不是摧毁）
    tiny = {"result": "短", "summary": "短"}
    out3 = shrink_delta_to_fit(tiny, LIMIT)
    ok = (out3 is not None)          # 本来就没超限，应原样返回
    print(f"  {'PASS' if ok else 'FAIL'} [未超限 → 原样返回]")
    if not ok:
        fails.append("noop")

    # ⑤ 枚举与结构不得被破坏
    d5 = {"result": BASE * 40,
          "foreshadow_changes": [{"id": "F001", "summary": "摘要" * 30, "status": "已回收",
                                  "planted_chapter": 1, "planned_resolution_chapter": 5,
                                  "importance": "高"}]}
    out5 = shrink_delta_to_fit(d5, LIMIT)
    if out5 is None:
        print("  INFO [全到保底 → 返回 None]")
    else:
        f = out5["foreshadow_changes"][0]
        ok = (f["status"] == "已回收" and f["id"] == "F001"
              and f["planted_chapter"] == 1 and f["importance"] == "高")
        print(f"  {'PASS' if ok else 'FAIL'} [伏笔枚举 / ID / 必填结构完好]")
        if not ok:
            fails.append("intact")

    total = 5
    passed = total - len(fails)
    print(f"\n{passed}/{total} 通过")
    if fails:
        print("SHRINK_DELTA_FLOOR_FAILED")
        return 1
    print("SHRINK_DELTA_FLOOR_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
