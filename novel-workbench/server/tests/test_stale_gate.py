#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_stale_gate.py — 过期绿灯：章被改写后，裁决依据必须失效且能补救

**为什么要有**（2026-11-20 实测）：

《逆袭从掌控系统开始》的 C1/C3/C6 章级裁决共 10 条引文，在当前正文里
**已经找不到了**。失配原因是正文被微改过：

    旧裁决引的：「翠芬婶看他不说话，又添**了**一句。」
    当前正文是：「翠芬婶看他不说话，又添一句。」

关键在于**漂移检测看不见它**：`_body_post_commit_drift` 比对的是「账本记的
body_sha256 vs 磁盘」，而 revision 定稿会把正文和账本 sha **一起**更新——
账本与磁盘完全一致，看不出「被合法修订但没重裁」。于是那份裁决的节点状态
仍写着 done——一份**过期绿灯**。

后果：闸门说「这章审过了」，而它依据的原文已经不存在。

修法三件套：
  ① **检测**：`_stale_gate_evidence(book, chapter)` 逐条验证裁决引文是否仍
     命中当前正文；
  ② **阻断**：N21 打包时，��何一章的裁决依据过期 → 判不可投（体检报告写明
     是哪几章、哪几条引文）；
  ③ **补救**：`autopilot.py rejudge --book <书> --chapter N` 带外重判该章
     裁决（**不动流水线游标**——流水线是线性的，回不了头；回退会把已完稿的
     书重新拉回写作链）。

跑法：
  python -X utf8 server/tests/test_stale_gate.py
  → 全过打印 STALE_GATE_OK 并 exit 0
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from inline_runner import _stale_gate_evidence          # noqa: E402

FAILS: list[str] = []
BODY_V1 = ("陆明把内兜按了一下，才开门。\n"
           "翠芬婶看他不说话，又添了一句。\n"
           "屋里没人说话，只有钟摆在墙上走。\n")
BODY_V2 = ("陆明把内兜按了一下，才开门。\n"
           "翠芬婶看他不说话，又添一句。\n"
           "屋里没人说话，只有钟摆在墙上走。\n")
QUOTE_V1 = "翠芬婶看他不说话，又添了一句。"
QUOTE_V2 = "翠芬婶看他不说话，又添一句。"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'} [{name}]" + ("" if ok else f" —— {detail}"))
    if not ok:
        FAILS.append(name)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_book(root: Path) -> Path:
    book = root / "book"
    write(book / "正文" / "第001章_测试.md", BODY_V1)
    write(book / "_pipeline" / "judgments_C1.N15.json", json.dumps({
        "verdict": "pass",
        "summary": "首章铁律齐备。",
        "items": [{
            "criterion": "章末留下钩子",
            "quote": QUOTE_V1,
            "source": "正文/第001章_测试.md",
            "verdict": "pass",
            "level": "A",
        }],
    }, ensure_ascii=False))
    write(book / "_pipeline" / "state.json", json.dumps({
        "schema_version": 2, "book": "测试书", "genre": "niandai",
        "target_words": 20000, "max_chapters": 12,
        "current_chapter": 1, "chapters_committed": 1, "words_total": 2200,
        "cursor": "C2.N09", "nodes": {"C1.N16": {"status": "done"}},
        "blocked_reason": None, "pending_questions": [], "decisions_taken": [],
    }, ensure_ascii=False))
    write(book / "追踪" / "_tracking-state.json", json.dumps({
        "last_committed_chapter": 1,
        "wordcount_records": {"1": {"actual": 2200}},
    }, ensure_ascii=False))
    return book


def main() -> int:
    print("== 过期绿灯：检测 / 阻断 / 补救 ==")
    root = Path(tempfile.mkdtemp(prefix="stale_gate_"))
    try:
        book = make_book(root)

        # ── ① 引文命中 → 不是过期 ───────────────────────────────────────
        stale = _stale_gate_evidence(book, 1)
        check("引文命中正文 → 裁决依据有效", not stale, str(stale))

        # ── ② 改了正文 → 过期被检出 ─────────────────────────────────────
        write(book / "正文" / "第001章_测试.md", BODY_V2)
        stale = _stale_gate_evidence(book, 1)
        check("正文被改写 → 检出过期引文",
              len(stale) == 1 and stale[0]["chapter"] == 1, str(stale))
        check("过期条目带可读原因",
              bool(stale) and "不命中" in stale[0].get("reason", ""),
              str(stale[:1]))

        # ── ③ rejudge：签令牌 + 给提示词 + 不动游标 ──────────────────────
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "autopilot.py"), "rejudge",
             "--book", str(book), "--chapter", "1"],
            capture_output=True, text=True, encoding="utf-8", check=False)
        check("rejudge 签发成功", proc.returncode == 0,
              f"rc={proc.returncode} {proc.stdout[:200]} {proc.stderr[:200]}")
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        check("rejudge 指向 C1.N15 裁决节点",
              payload.get("node") == "C1.N15", str(payload.get("node")))
        check("rejudge 带完整裁决提示词",
              "items" in payload.get("subagent_prompt", "")
              and "fail 项必填" in payload.get("subagent_prompt", ""),
              payload.get("subagent_prompt", "")[:120])
        token = (book / "_pipeline" / ".step_token").read_text(encoding="utf-8")
        token = json.loads(token)["token"]
        state = json.loads((book / "_pipeline" / "state.json").read_text(encoding="utf-8"))
        check("rejudge 标记了带外重判节点",
              state.get("rejudge_node") == "C1.N15", str(state.get("rejudge_node")))
        check("rejudge 不动流水线游标",
              state.get("cursor") == "C2.N09", str(state.get("cursor")))

        # ── ④ 重判的裁决写回 → 过期消除 + done 接受且不动游标 ────────────
        write(book / "_pipeline" / "judgments_C1.N15.json", json.dumps({
            "verdict": "pass",
            "summary": "重判后首章铁律齐备。",
            "items": [{
                "criterion": "章末留下钩子",
                "quote": QUOTE_V2,
                "source": "正文/第001章_测试.md",
                "verdict": "pass",
                "level": "A",
            }],
        }, ensure_ascii=False))
        done = subprocess.run(
            [sys.executable, str(SCRIPTS / "autopilot.py"), "done", "--book", str(book),
             "--node", "C1.N15", "--token", token,
             "--evidence", "_pipeline/judgments_C1.N15.json"],
            capture_output=True, text=True, encoding="utf-8", check=False)
        check("重判的裁决被 done 接受", done.returncode == 0,
              f"rc={done.returncode} {done.stdout[:300]} {done.stderr[:200]}")
        state = json.loads((book / "_pipeline" / "state.json").read_text(encoding="utf-8"))
        check("done 后游标未动（带外重判不推进流水线）",
              state.get("cursor") == "C2.N09", str(state.get("cursor")))
        check("done 后清掉 rejudge_node",
              "rejudge_node" not in state, str(state.get("rejudge_node")))
        check("重判后过期引文清零",
              not _stale_gate_evidence(book, 1),
              str(_stale_gate_evidence(book, 1)))

        # ── ⑤ 带外重判的 block：报错但绝不回退流水线 ─────────────────────
        subprocess.run(
            [sys.executable, str(SCRIPTS / "autopilot.py"), "rejudge",
             "--book", str(book), "--chapter", "1"],
            capture_output=True, text=True, encoding="utf-8", check=False)
        token = json.loads((book / "_pipeline" / ".step_token")
                           .read_text(encoding="utf-8"))["token"]
        write(book / "_pipeline" / "judgments_C1.N15.json", json.dumps({
            "verdict": "block",
            "summary": "章末没钩子。",
            "items": [{
                "criterion": "章末留下钩子",
                "quote": QUOTE_V2,
                "source": "正文/第001章_测试.md",
                "verdict": "fail",
                "level": "B",
                "fix": "章末补一个具体钩子",
            }],
        }, ensure_ascii=False))
        blocked = subprocess.run(
            [sys.executable, str(SCRIPTS / "autopilot.py"), "done", "--book", str(book),
             "--node", "C1.N15", "--token", token,
             "--evidence", "_pipeline/judgments_C1.N15.json"],
            capture_output=True, text=True, encoding="utf-8", check=False)
        check("重判判 block → 拒收", blocked.returncode == 2,
              f"rc={blocked.returncode} {blocked.stdout[:200]}")
        check("block 报错是 rejudge_blocked（不触发回退）",
              "rejudge_blocked" in blocked.stdout, blocked.stdout[:200])
        state = json.loads((book / "_pipeline" / "state.json").read_text(encoding="utf-8"))
        check("重判 block 后游标仍未动",
              state.get("cursor") == "C2.N09", str(state.get("cursor")))
        check("重判 block 被记为 failed（打包时章级 block 才算数）",
              (state.get("nodes") or {}).get("C1.N15", {}).get("status") == "failed",
              str((state.get("nodes") or {}).get("C1.N15")))
        check("重判 block 记下了裁决结论",
              bool((state.get("nodes") or {}).get("C1.N15", {}).get("summary")),
              str((state.get("nodes") or {}).get("C1.N15", {}).get("summary"))[:80])
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print(f"\n{'全部通过' if not FAILS else '失败: ' + ', '.join(FAILS)}")
    if FAILS:
        print("STALE_GATE_FAILED")
        return 1
    print("STALE_GATE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
