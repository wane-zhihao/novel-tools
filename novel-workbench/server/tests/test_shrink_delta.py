#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_shrink_delta.py — delta 自动压缩器的夹具

**为什么要有**：实测第 8 章的 tracking 事务写完是 3087 字节，上限 3072——
**差 15 字节**就被打回，让 writer 回去再改一轮。内容完全合格，只差字节数。
writer 估算字节数不准是常态（中文 UTF-8 每字 3 字节、JSON 转义、缩进都影响）。

修法：超限时自动截断**长度类字段**，不碰 id / status / 枚举 / 必填结构。

跑法：
  python -X utf8 server/tests/test_shrink_delta.py
  → 全过打印 SHRINK_DELTA_OK 并 exit 0
"""
import sys
import json
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from commands import _shrink_delta_to_fit                     # noqa: E402

LIMIT = 3072


def _size(d: dict) -> int:
    return len(json.dumps(d, ensure_ascii=False).encode("utf-8"))


def _over() -> dict:
    return {
        "result": "很长的结果描述。" * 40,
        "character_changes": [{"name": "陆明", "change": "变化描述。" * 20}],
        "foreshadow_changes": [{
            "id": "F001", "summary": "摘要内容。" * 30, "status": "已回收",
            "planted_chapter": 1, "planned_resolution_chapter": 5, "importance": "高",
        }],
        "timeline_events": [{
            "id": "E001", "objective_fact": "事实。" * 30, "reader_knowledge": "知识。" * 20,
            "reveal_status": "部分揭示", "reveal_chapter": 8, "characters": ["陆明"],
        }],
        "constraints": ["约束一" * 30],
        "next_chapter_commitments": ["承诺一" * 30],
    }


def _any_shrunk(out: dict, orig: dict) -> bool:
    """是否有任意长度类字段被截短（压的是最长的那个，不一定是 summary）。"""
    for k, v_out in out.items():
        v_in = orig.get(k)
        if isinstance(v_out, str) and isinstance(v_in, str) and len(v_out) < len(v_in):
            return True
        if isinstance(v_out, list) and isinstance(v_in, list):
            for a, b in zip(v_out, v_in):
                if isinstance(a, str) and isinstance(b, str) and len(a) < len(b):
                    return True
                if isinstance(a, dict) and isinstance(b, dict):
                    for kk in a:
                        x, y = a.get(kk), b.get(kk)
                        if isinstance(x, str) and isinstance(y, str) and len(x) < len(y):
                            return True
    return False


def main() -> int:
    print("== delta 自动压缩 ==")
    fails = []

    # ① 超限 → 压到过线
    d = _over()
    before = _size(d)
    out = _shrink_delta_to_fit(d, LIMIT)
    after = _size(out) if out else -1
    ok = (out is not None and after <= LIMIT)
    print(f"  {'PASS' if ok else 'FAIL'} [超限 {before} → {after} 字节 ≤{LIMIT}]")
    if not ok:
        fails.append("shrink")

    # ② 枚举/ID/结构不许被破坏
    if out:
        o = out["foreshadow_changes"][0]
        t = out["timeline_events"][0]
        intact = (
            o["status"] == "已回收"
            and o["id"] == "F001"
            and o["planted_chapter"] == 1
            and o["planned_resolution_chapter"] == 5
            and o["importance"] == "高"
            and t["reveal_status"] == "部分揭示"
            and t["reveal_chapter"] == 8
            and t["characters"] == ["陆明"]
            and bool(out["result"])
        )
        print(f"  {'PASS' if intact else 'FAIL'} [枚举 / ID / 必填结构完好]")
        if not intact:
            fails.append("intact")

        cut = _any_shrunk(out, d)
        print(f"  {'PASS' if cut else 'FAIL'} [长度类字段确实被截短]")
        if not cut:
            fails.append("cut")

    # ③ 本来就合规 → 原样返回（不无谓改动）
    small = {"result": "短"}
    out2 = _shrink_delta_to_fit(small, LIMIT)
    ok2 = (out2 == small)
    print(f"  {'PASS' if ok2 else 'FAIL'} [合规输入原样返回]")
    if not ok2:
        fails.append("noop")

    # ④ 压不动时返回 None（让上层报明确错误，而不是静默破坏结构）
    rigid = {"id": "x" * 4000}
    out3 = _shrink_delta_to_fit(rigid, LIMIT)
    ok3 = (out3 is None)
    print(f"  {'PASS' if ok3 else 'FAIL'} [无可截断字段 → 返回 None]")
    if not ok3:
        fails.append("rigid")

    total = 5
    passed = total - len(fails)
    print(f"\n{passed}/{total} 通过")
    if fails:
        print("SHRINK_DELTA_FAILED")
        return 1
    print("SHRINK_DELTA_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
