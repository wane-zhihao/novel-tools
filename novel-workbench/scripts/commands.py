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
from datetime import datetime
from pathlib import Path

from autopilot import (HERE, IRON_RULES, load_state, save_state, sha256_of, now,
                       clear_token, cursor_spec, norm_cjk, token_ok, read_token,
                       resolve_evidence)
from pipeline_spec import GLOBAL_NODES, chapter_nodes, next_cursor, judgment_policy
from inline_runner import (run_node_script, verify_quote, append_chapter_findings,
                           append_endgame_findings)


def _evidence_ok(book: Path, paths: list[str], min_size: int) -> tuple[bool, str, list[Path]]:
    resolved_names, errors = resolve_evidence(book, paths)
    if errors:
        return False, "；".join(errors), []
    resolved = []
    for name in resolved_names:
        f = Path(name) if Path(name).is_absolute() else (book / name)
        if not f.is_file():
            return False, f"evidence 不存在：{f}", []
        if f.stat().st_size < min_size:
            return False, f"evidence 过小（{f.stat().st_size}B < {min_size}B）：{f}", []
        resolved.append(f)
    return True, "", resolved


def _fail_items(j: dict) -> list[dict]:
    """judge 的 fail 项 → 单章 findings 的统一形状。"""
    out = []
    for item in j.get("items", []):
        if item.get("verdict") != "fail":
            continue
        out.append({
            "id": item.get("id") or item.get("criterion"),
            "criterion": item.get("criterion"),
            "name": item.get("criterion"),
            "level": item.get("level", "B"),
            "blocking": item.get("level", "B") == "B",
            "quote": item.get("quote"),
            "file": item.get("source"),
            "fix": item.get("fix"),
            "note": item.get("fix") or "",
        })
    return out


def _terminal_block(book: Path, st: dict, node: str, reason: str, jf: Path, extra: dict | None = None) -> int:
    st["blocked_reason"] = reason
    st["nodes"][node] = {"status": "failed", "at": now(), "judgments": str(jf)}
    save_state(book, st)
    print(json.dumps({"error": "verify_blocked", "reason": reason, **(extra or {})},
                     ensure_ascii=False))
    return 1


def _handle_verify_block(book: Path, st: dict, node: str, j: dict, jf: Path) -> int:
    """block 不是终点：单章回 N14，终审回 N20f，超预算才停机。"""
    summary = j.get("summary", "")
    items = _fail_items(j)

    if node.startswith("C") and node.endswith(".N15"):
        ch = int(node.split(".")[0][1:])
        append_chapter_findings(book, ch, items, "N15")
        rounds = int(st.get("fix_rounds", {}).get(str(ch), 0))
        if rounds >= 2:
            return _terminal_block(
                book, st, node,
                f"{node} 连续 {rounds + 1} 轮仍 block：{summary[:300]}", jf,
                {"hint": "同一处失败 2 次，按铁律停机，需人工判断"})
        st["fix_rounds"] = {**(st.get("fix_rounds") or {}), str(ch): rounds + 1}
        st["cursor"] = f"C{ch}.N14"
        st["nodes"][node] = {"status": "failed", "at": now(), "judgments": str(jf)}
        save_state(book, st)
        print(json.dumps({"ok": True, "blocked": True, "routed_to": st["cursor"],
                          "round": rounds + 1, "reason": summary[:300]}, ensure_ascii=False))
        return 1

    if node == "N20":
        append_endgame_findings(book, items, "N20")
        rounds = int(st.get("n20_rounds", 0))
        if rounds >= 2:
            return _terminal_block(
                book, st, node,
                f"N20 终审连续 {rounds + 1} 轮仍 block：{summary[:300]}", jf,
                {"hint": "N20f 已修 2 轮，剩余问题需人工决策"})
        st["n20_rounds"] = rounds + 1
        st["cursor"] = "N20f"
        st["nodes"][node] = {"status": "failed", "at": now(), "judgments": str(jf)}
        save_state(book, st)
        print(json.dumps({"ok": True, "blocked": True, "routed_to": "N20f",
                          "round": rounds + 1, "reason": summary[:300]}, ensure_ascii=False))
        return 1

    return _terminal_block(book, st, node, f"{node} 判定 block：{summary[:300]}", jf)


def _source_file(book: Path, source: str):
    if not source:
        return None
    candidate = Path(source) if Path(source).is_absolute() else (book / source)
    try:
        resolved = candidate.resolve()
        root = book.resolve()
    except OSError:
        return None
    if resolved != root and root not in resolved.parents:
        return None
    return resolved if resolved.is_file() else None


def _source_allowed(source: str, patterns: list[str]) -> bool:
    normalized = str(source).replace("\\", "/")
    for pattern in patterns:
        if pattern.endswith("/") or pattern.endswith("_"):
            if normalized.startswith(pattern):
                return True
        elif normalized == pattern:
            return True
    return False


def _validate_judgment(book: Path, node_id: str, judgment: dict) -> list[dict]:
    """judge 必须给出显式裁决；文本项必须有可命中原文的 quote。"""
    policy = judgment_policy(node_id)
    bad: list[dict] = []
    items = judgment.get("items")
    if not isinstance(items, list) or not items:
        bad.append({"reason": "items 为空：judge 必须给出裁决项"})
        items = []
    verdict = judgment.get("verdict")
    if verdict not in ("pass", "block"):
        bad.append({"reason": f"verdict 必须显式写 pass 或 block（当前 {verdict!r}）"})
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            bad.append({"index": index, "reason": "item 不是对象"})
            continue
        criterion = item.get("criterion") or f"item-{index}"
        if item.get("verdict") not in ("pass", "fail"):
            bad.append({"criterion": criterion, "reason": "item.verdict 必须是 pass 或 fail"})
        item_id = str(item.get("id", ""))
        mechanical = item_id in policy["mechanical_ids"]
        source = item.get("source") or ""
        source_file = _source_file(book, source) if source else None
        if mechanical:
            metric = item.get("metric")
            if not isinstance(metric, dict) or metric.get("actual") is None or metric.get("expected") is None:
                bad.append({"criterion": criterion, "reason": "机械项必须提供 metric.actual/expected"})
            if source and not source_file:
                bad.append({"criterion": criterion, "reason": "source 文件不存在"})
            quote = item.get("quote") or ""
            if quote and source_file and not verify_quote(quote, source_file.read_text(encoding="utf-8")):
                bad.append({"criterion": criterion, "reason": "quote 未在 source 中命中"})
            continue
        if not source:
            bad.append({"criterion": criterion, "reason": "文本项缺少 source"})
            continue
        if not _source_allowed(source, policy["sources"]):
            bad.append({"criterion": criterion, "reason": f"source 不在允许范围：{source}"})
            continue
        if not source_file:
            bad.append({"criterion": criterion, "reason": "source 文件不存在"})
            continue
        quote = item.get("quote") or ""
        if not quote:
            bad.append({"criterion": criterion, "reason": "文本项缺少 quote"})
        elif "…" in quote or "..." in quote:
            bad.append({"criterion": criterion, "reason": "quote 不得使用省略号"})
        elif not verify_quote(quote, source_file.read_text(encoding="utf-8")):
            bad.append({"criterion": criterion, "reason": "quote 未在 source 中命中"})
    if verdict == "block" and not any(
        isinstance(item, dict) and item.get("verdict") == "fail" for item in items
    ):
        bad.append({"reason": "verdict=block 但没有 fail 明细"})
    return bad


def cmd_done(a) -> int:
    book = Path(a.book).resolve()
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

    held = read_token(book)
    issued_at = held.get("issued_at")
    if issued_at:
        try:
            issued_ts = datetime.strptime(issued_at, "%Y-%m-%dT%H:%M:%S%z").timestamp()
        except ValueError:
            issued_ts = None
        if issued_ts is not None:
            stale = [str(f) for f in files if f.stat().st_mtime + 2 < issued_ts]
            if stale:
                print(json.dumps({
                    "error": "stale_evidence",
                    "detail": f"以下证据早于本轮 token 签发时间，可能沿用了旧产物：{stale}",
                }, ensure_ascii=False))
                return 2

    # verify 节点：裁决必须有证据；文本项 quote 必须命中允许的 source
    if spec["kind"] == "verify":
        jf = files[-1]
        try:
            j = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            print(json.dumps({"error": "judgments_unreadable", "detail": str(e)},
                             ensure_ascii=False)); return 2
        bad = _validate_judgment(book, spec["id"], j)
        if bad:
            print(json.dumps({"error": "quote_check_failed", "items": bad}, ensure_ascii=False))
            return 2
        verdict = j.get("verdict")
        # 不信任总 verdict 与明细矛盾：任何一条 B 级 fail 都必须 block
        if any(x.get("verdict") == "fail" and x.get("level", "B") == "B"
               for x in j.get("items", [])):
            verdict = "block"
        if verdict == "block":
            return _handle_verify_block(book, st, a.node, j, jf)

    hashes = [sha256_of(f) for f in files]
    st["nodes"][a.node] = {
        "status": "done", "at": now(),
        "evidence": [str(f.relative_to(book)) if str(f).startswith(str(book)) else str(f) for f in files],
        "sha256": hashes,
        "attempts": st["nodes"].get(a.node, {}).get("attempts", 1),
    }
    st["llm_rounds"] = int(st.get("llm_rounds", 0)) + 1
    clear_token(book)
    st["cursor"] = next_cursor(st)
    save_state(book, st)
    print(json.dumps({"ok": True, "node": a.node, "next": st["cursor"],
                      "llm_rounds": st["llm_rounds"]}, ensure_ascii=False))
    return 0


def cmd_skip(a) -> int:
    book = Path(a.book).resolve()
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
    book = Path(a.book).resolve()
    st = load_state(book)
    if not st:
        return 2
    st["blocked_reason"] = a.reason
    save_state(book, st)
    print(json.dumps({"ok": True, "blocked": True, "reason": a.reason}, ensure_ascii=False))
    return 1


def cmd_resume(a) -> int:
    """不信任内存：重新扫盘重建，从第一个未完成节点继续。"""
    book = Path(a.book).resolve()
    st = load_state(book)
    if not st:
        print(json.dumps({"error": "not_initialized"}, ensure_ascii=False)); return 2
    st["blocked_reason"] = None
    clear_token(book)
    try:
        (book / "_pipeline" / ".next.lock").unlink()
    except FileNotFoundError:
        pass
    # 清掉所有 running —— 中断的节点必须重做
    for k, v in st["nodes"].items():
        if v.get("status") == "running":
            v["status"] = "pending"
            v["attempts"] = v.get("attempts", 0)
    save_state(book, st)
    print(json.dumps({"ok": True, "resumed_at": st["cursor"],
                      "chapter": st["current_chapter"]}, ensure_ascii=False))
    return 0
