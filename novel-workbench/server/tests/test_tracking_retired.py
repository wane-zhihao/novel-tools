#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_tracking_retired.py — 退役声明必须是被删条目的**逐字原文**

**为什么要有**（2026-11-20 实测，第 6 章卡了一轮）：

writer 把一条 context 条目从 `continuity_risks` 里换成了新条目，同时在
`delta.retired_context_items` 里声明了它——但 `done` 被拒：

```
tracking_preflight_failed: ERROR: context items were dropped without being
declared in delta.retired_context_items: 三号床精度攻关刚开刀，圆度病灶拆成
床子与炉子两处，扭曲量与淬火余量都还没数出来。
```

writer 明明写了。根因是**工作台的规整器把声明截断了**：
`inline_runner._normalize_tracking` 的 delta 压缩器为了把体积压到 3072 字节，
把 `retired_context_items` 的每一项截到 30 字：

```python
delta["retired_context_items"] = [(x[:30]) if isinstance(x, str) and len(x) > 32 else x ...]
```

而工具（`tracking_commit`）对退役声明做的是**精确集合比对**：
`undeclared = dropped - set(retired_context_items)`。截断一个字就匹配不上，
于是报「未声明」——一个和真实原因（截断）完全对不上的错误信息，把执行方
引向「再写一遍」，而再写一遍还会被截断。**死循环。**

更深一层的口径不一致：
  · `commands._tracking_preflight` 的体积检查**排除** retired_context_items；
  · `_normalize_tracking` 的压缩器口径**包含**它。
  同一份事务两处口径不同 → 压缩被触发 → 截断 → 声明失效。

修法：
  ① 压缩器口径与预检对齐（都排除 retired_context_items）；
  ② 退役声明**不截断**——它是「被删原文的逐字」，工具提交时会按账本重新生成；
  ③ 规整器顺手**还原**历史上被截断过的声明（前缀命中即认定为截断）。

本夹具覆盖三条：
  ① 超限时压缩**不截断**退役声明；
  ② 已经被截断的声明会被**还原**成原文；
  ③ 正常路径下退役声明与账本掉落项**逐字相等**。

跑法：
  python -X utf8 server/tests/test_tracking_retired.py
  → 全过打印 TRACKING_RETIRED_OK 并 exit 0
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from inline_runner import _normalize_tracking        # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'} [{name}]" + ("" if ok else f" —— {detail}"))
    if not ok:
        FAILS.append(name)


ITEM = ("三号床精度攻关刚开刀，圆度病灶拆成床子与炉子两处，"
        "扭曲量与淬火余量都还没数出来")


def write_txn(root: Path, payload: dict) -> Path:
    txn = root / "txn.json"
    txn.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return txn


def base_txn(retired=None, extra_bytes: int = 0) -> dict:
    delta = {
        "result": "本章结果",
        "character_changes": [{"name": "陈铁生", "change": "变了"}],
        "foreshadow_changes": [],
        "timeline_events": [{"action": "upsert", "id": "E001", "story_time": "某日",
                             "objective_fact": "x", "reader_knowledge": "y",
                             "reveal_status": "已揭示", "reveal_chapter": 1,
                             "characters": ["陈铁生"]}],
        "constraints": [],
        "next_chapter_commitments": [],
        "retired_context_items": list(retired or []),
    }
    if extra_bytes:
        delta["result"] = "结" * extra_bytes
    return {
        "schema_version": 1, "mode": "append", "chapter": 1,
        "chapter_title": "测试", "expected_state_revision": 0,
        "delta": delta,
        "context": {"position": {"volume": "第一卷", "volume_start_chapter": 1,
                                 "story_time": "某日", "scene": "车间"},
                    "long_term_constraints": [], "active_character_names": [],
                    "continuity_risks": ["新的一条风险"]},
        "character_snapshots": {"陈铁生": {"identity": "钳工", "location": "车间",
                                          "goal": "修好床子", "state": "在忙",
                                          "abilities_resources": [], "relationships": [],
                                          "knowledge": [], "open_threads": []}},
    }


def main() -> int:
    print("== 退役声明：逐字原文，不得截断 ==")
    root = Path(tempfile.mkdtemp(prefix="tracking_retired_"))
    try:
        prev_ctx = {"continuity_risks": [ITEM], "long_term_constraints": [],
                    "next_chapter_commitments": []}

        # ① 超限（触发压缩）时，退役声明不得被截断
        root_a = root / "a"
        root_a.mkdir(exist_ok=True)
        txn = write_txn(root_a, base_txn(extra_bytes=1200))
        _normalize_tracking(txn, {}, prev_ctx)
        d = json.loads(txn.read_text(encoding="utf-8"))
        retired = d["delta"].get("retired_context_items") or []
        check("掉落项已被自动声明", ITEM in retired, str(retired))
        check("退役声明未被截断（与账本逐字相等）",
              ITEM in retired and all(r == ITEM for r in retired if r.startswith("三号床精度攻关")),
              str(retired))

        # ② 已被截断的声明会被还原成原文
        root_b = root / "b"
        root_b.mkdir(exist_ok=True)
        txn = write_txn(root_b, base_txn(retired=[ITEM[:20]]))
        _normalize_tracking(txn, {}, prev_ctx)
        d = json.loads(txn.read_text(encoding="utf-8"))
        retired = d["delta"].get("retired_context_items") or []
        check("被截断的声明被还原成原文",
              ITEM in retired and ITEM[:20] not in retired, str(retired))

        # ③ 体积口径：排除 retired_context_items 后，压缩器不得动它
        root_c = root / "c"
        root_c.mkdir(exist_ok=True)
        big = base_txn(retired=[ITEM])
        big["delta"]["result"] = "结" * 1200
        txn = write_txn(root_c, big)
        _normalize_tracking(txn, {}, prev_ctx)
        d = json.loads(txn.read_text(encoding="utf-8"))
        retired = d["delta"].get("retired_context_items") or []
        check("体积超限时退役声明仍逐字保留",
              ITEM in retired, str(retired))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print(f"\n{'全部通过' if not FAILS else '失败: ' + ', '.join(FAILS)}")
    if FAILS:
        print("TRACKING_RETIRED_FAILED")
        return 1
    print("TRACKING_RETIRED_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
