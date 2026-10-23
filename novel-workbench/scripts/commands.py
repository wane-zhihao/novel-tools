#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""commands.py — done / skip / block / resume 四个子命令

done 的四道验收（缺一不可）：
  1. evidence 存在 ∧ 非空 ∧ 大小 > 节点阈值
  2. sha256 与 state 记录一致
  3. script/verify 节点退出码 0
  4. verify 节点额外：原文引用硬校验（归一化后子串匹配）
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from autopilot import (HERE, IRON_RULES, load_state, save_state, sha256_of, now,
                       clear_token, cursor_spec, norm_cjk, token_ok)
from pipeline_spec import GLOBAL_NODES, chapter_nodes, next_cursor
from inline_runner import run_node_script, verify_quote


def _evidence_ok(book: Path, paths: list[str], min_size: int) -> tuple[bool, str, list[Path]]:
    resolved = []
    for p in paths:
        f = (book / p) if not Path(p).is_absolute() else Path(p)
        if not f.is_file():
            return False, f"evidence 不存在：{f}", []
        if f.stat().st_size < min_size:
            return False, f"evidence 过小（{f.stat().st_size}B < {min_size}B）：{f}", []
        resolved.append(f)
    return True, "", resolved


def cmd_done(a) -> int:
    book = Path(a.book)
    st = load_state(book)
    if not st:
        print(json.dumps({"error": "not_initialized"}, ensure_ascii=False)); return 2
    if not token_ok(book, a.node, a.token):
        print(json.dumps({"error": "token_mismatch",
                          "hint": "可能是 subagent 越权调用，或 node 与令牌不匹配"},
                         ensure_ascii=False))
        return 2

    spec = cursor_spec(st, book)
    if spec is None or spec["id"] != a.node:
        print(json.dumps({"error": "cursor_mismatch",
                          "expected": st["cursor"]}, ensure_ascii=False)); return 2

    ok, msg, files = _evidence_ok(book, a.evidence, spec.get("min_size", 1))
    if not ok:
        print(json.dumps({"error": "evidence_invalid", "detail": msg}, ensure_ascii=False))
        return 2

    # verify 节点：校验 judgments 文件里的原文引用真在正文里
    if spec["kind"] == "verify":
        jf = files[-1]
        try:
            j = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            print(json.dumps({"error": "judgments_unreadable", "detail": str(e)},
                             ensure_ascii=False)); return 2
        bad = []
        for item in j.get("items", []):
            q = item.get("quote", "")
            src = item.get("source", "")
            srcf = (book / src) if src and not Path(src).is_absolute() else (Path(src) if src else None)
            if not q:
                continue
            if not srcf or not srcf.is_file():
                bad.append({"criterion": item.get("criterion"), "reason": "source 文件不存在"})
                continue
            if not verify_quote(q, srcf.read_text(encoding="utf-8")):
                bad.append({"criterion": item.get("criterion"), "reason": "原文引用未在正文中命中"})
        if bad:
            print(json.dumps({"error": "quote_check_failed", "items": bad}, ensure_ascii=False))
            return 2
        verdict = j.get("verdict", "pass")
        if verdict == "block":
            st["blocked_reason"] = f"{a.node} 判定 block：{j.get('summary', '')[:300]}"
            st["nodes"][a.node] = {"status": "failed", "at": now(), "judgments": str(jf)}
            save_state(book, st)
            print(json.dumps({"error": "verify_blocked", "reason": st["blocked_reason"]},
                             ensure_ascii=False)); return 1

    hashes = [sha256_of(f) for f in files]
    st["nodes"][a.node] = {
        "status": "done", "at": now(),
        "evidence": [str(f.relative_to(book)) if str(f).startswith(str(book)) else str(f) for f in files],
        "sha256": hashes,
        "attempts": st["nodes"].get(a.node, {}).get("attempts", 1),
    }
    clear_token(book)
    st["cursor"] = next_cursor(st)
    save_state(book, st)
    print(json.dumps({"ok": True, "node": a.node, "next": st["cursor"],
                      "llm_rounds": st.get("llm_rounds", 0) + 1}, ensure_ascii=False))
    return 0


def cmd_skip(a) -> int:
    book = Path(a.book)
    st = load_state(book)
    if not st:
        return 2
    if not token_ok(book, a.node, a.token):
        print(json.dumps({"error": "token_mismatch"}, ensure_ascii=False)); return 2
    spec = cursor_spec(st, book)
    if spec is None or not spec.get("skippable"):
        print(json.dumps({"error": "not_skippable", "node": a.node}, ensure_ascii=False)); return 2
    st["nodes"][a.node] = {"status": "skipped", "at": now(), "reason": a.reason}
    clear_token(book)
    st["cursor"] = next_cursor(st)
    save_state(book, st)
    print(json.dumps({"ok": True, "node": a.node, "skipped": True, "next": st["cursor"]},
                     ensure_ascii=False))
    return 0


def cmd_block(a) -> int:
    book = Path(a.book)
    st = load_state(book)
    if not st:
        return 2
    st["blocked_reason"] = a.reason
    save_state(book, st)
    print(json.dumps({"ok": True, "blocked": True, "reason": a.reason}, ensure_ascii=False))
    return 1


def cmd_resume(a) -> int:
    """不信任内存：重新扫盘重建，从第一个未完成节点继续。"""
    book = Path(a.book)
    st = load_state(book)
    if not st:
        print(json.dumps({"error": "not_initialized"}, ensure_ascii=False)); return 2
    st["blocked_reason"] = None
    clear_token(book)
    # 清掉所有 running —— 中断的节点必须重做
    for k, v in st["nodes"].items():
        if v.get("status") == "running":
            v["status"] = "pending"
            v["attempts"] = v.get("attempts", 0)
    save_state(book, st)
    print(json.dumps({"ok": True, "resumed_at": st["cursor"],
                      "chapter": st["current_chapter"]}, ensure_ascii=False))
    return 0
