#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""commands.py — done / skip / block / resume 四个子命令

done 的验收（2026-10-25 审查后修订，此前的"四道验收"表述不实，已撤回）：
  1. token 必须与在途节点匹配（一次性握手）
  2. evidence 存在 ∧ 非空 ∧ 大小 > 节点阈值，且与 spec 的期望证据模式一致
     （审查 A2：此前用调用方传入值、不与 spec 比对，持令牌者可指认任意大文件）
  3. evidence 不得早于本轮 token 签发时间（防沿用旧产物）
  4. verify 节点额外：judgments 结构校验 + 原文引用硬校验（归一化后子串匹配）
注意：sha256 只记录不比对（曾声称"与 state 一致"，未实现，已从声称中删除）；
script 节点由 autopilot 内联执行，不到本命令。
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from autopilot import (HERE, IRON_RULES, load_state, save_state, sha256_of, now,
                       clear_token, cursor_spec, norm_cjk, token_ok, read_token,
                       resolve_evidence)
from pipeline_spec import GLOBAL_NODES, chapter_nodes, next_cursor, judgment_policy
from inline_runner import (run_node_script, verify_quote, append_chapter_findings,
                           append_endgame_findings, _cfg)


def _evidence_matches_spec(book: Path, spec: dict, files: list[Path]) -> str | None:
    """done 的 evidence 必须匹配节点 spec 的期望模式（审查 A2）。

    返回 None 表示通过，否则返回错误描述。防止持令牌者指认任意大文件
    冒充本节点产物——通配符按 fnmatch 语义比对。
    """
    patterns = spec.get("evidence")
    if not patterns:
        return None
    if isinstance(patterns, str):
        patterns = [patterns]
    patterns = [str(p).replace("\\", "/") for p in patterns]
    rels: list[str] = []
    for f in files:
        try:
            rels.append(str(f.resolve().relative_to(book.resolve())).replace("\\", "/"))
        except ValueError:
            return f"evidence 不在书目录内：{f}"
    for rel in rels:
        if not any(fnmatch.fnmatch(rel, p) for p in patterns):
            return f"evidence {rel} 不匹配本节点期望模式 {patterns}"
    for p in patterns:
        if not any(fnmatch.fnmatch(rel, p) for rel in rels):
            return f"本节点期望证据未提供：{p}"
    return None


def _control_log(book: Path, entry: dict) -> None:
    """block/resume 的调用方留痕（审查 B5：无令牌免费停机键的对策之一是记账）。"""
    p = book / "_pipeline" / "control_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": now(), "pid": os.getpid(), **entry}
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _cross_check_tracking(book: Path, st: dict) -> None:
    """resume 纪律（plan 第七节）：不信任内存——与 _tracking-state.json 交叉校验。

    此前 docstring 声称"扫盘重建"从未实现（审查 B2）。这里是轻量版：
    章号不一致时以 tracking 账本为准，越界章节节点打回 pending 并记入
    decisions_taken。
    """
    tracking = book / "追踪" / "_tracking-state.json"
    try:
        doc = json.loads(tracking.read_text(encoding="utf-8"))
        last = int(doc.get("last_committed_chapter") or 0)
    except (OSError, ValueError, json.JSONDecodeError):
        return
    committed = int(st.get("chapters_committed") or 0)
    if last == committed:
        return
    if last < committed:
        for nid, node in st.get("nodes", {}).items():
            m = re.match(r"C(\d+)\.", str(nid))
            if m and int(m.group(1)) > last and node.get("status") == "done":
                node["status"] = "pending"
    st["chapters_committed"] = last
    st["current_chapter"] = max(int(st.get("current_chapter") or 0), last)
    st["decisions_taken"].append({
        "at_node": "resume",
        "issue": f"state 记 {committed} 章 / tracking 账本记 {last} 章",
        "applied_rule": "以 tracking 账本为准，越界章节节点打回 pending",
    })


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

    # 全局闸门（plan 5.1）：N04/N06/N08 block 回对应 writer 节点，≤2 轮，
    # 第 3 轮终停。打回意见在 _pipeline/judgments_<node>.json，writer 节点
    # 的 prompt 会读它（真书首日实证：缺此回路时一次 block 就整链终停）。
    global_retry = {"N04": "N03", "N06": "N05", "N08": "N07"}
    if node in global_retry:
        target = global_retry[node]
        rounds = int(st.get("global_fix_rounds", {}).get(node, 0))
        if rounds >= 2:
            return _terminal_block(
                book, st, node,
                f"{node} 连续 {rounds + 1} 轮仍 block：{summary[:300]}", jf,
                {"hint": "闸门打回 2 轮仍未过，需人工判断"})
        st["global_fix_rounds"] = {**(st.get("global_fix_rounds") or {}), node: rounds + 1}
        st["cursor"] = target
        st["nodes"][node] = {"status": "failed", "at": now(), "judgments": str(jf)}
        save_state(book, st)
        print(json.dumps({"ok": True, "blocked": True, "routed_to": target,
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


def _tracking_preflight(book: Path, node: str) -> str:
    """N12/N14 的封板预检：在书副本上跑真实 tracking_commit commit。

    真书实证：事务的 schema 违规（字段名自造、id 非 F001/E001 形态、
    result 超 480 字节、控制字段被改）此前只能在 N16 封板时才暴露，每错一项
    就要烧一整轮 N14 定点改写（5-8 分钟 + 十几万 token）。这里把上游校验器
    提前到 done 时用：副本上 commit 通过才放行，不通过就把原文错误回喂模型，
    让它在本轮内改完。

    返回空串 = 通过或无法预检（缺事务文件/缺工具，交给 N16 兜底）；
    非空 = 上游错误原文。
    """
    m = re.match(r"^C(\d+)\.N(12|14)$", node)
    if not m:
        return ""
    chapter = int(m.group(1))
    tx = book / ".story" / "work" / f"第{chapter:03d}章" / "tracking.json"
    if not tx.is_file():
        return ""
    # 先用与 N16 封板前同一套规则规整事务。
    # 为什么：这些都是**结构性不一致**（快照与 delta 不对齐、risks 超限、
    # 体积超限、context 条目被丢却没声明 retired），不是内容错误。
    # 让模型重写整章来修是浪费（端到端实跑时第 1 章因此烧了 4 轮）。
    try:
        from inline_runner import _normalize_tracking
        _prev = {}
        try:
            _prev = (json.loads((book / "追踪" / "_tracking-state.json")
                                .read_text(encoding="utf-8")) or {}).get("context") or {}
        except (OSError, ValueError):
            _prev = {}
        _normalize_tracking(tx, {}, _prev)
    except Exception:
        pass
    cfg = _cfg()
    tool = cfg.get("TRACKING_COMMIT")
    python = cfg.get("PYTHON")
    if not tool or not python or not Path(tool).is_file():
        return ""
    # 先跑与 N16 同一个确定性规整器：预检不能比管线自己的规整更严，
    # 否则 N12/N14 会被 N16 本来能自动修好的结构问题卡住（第 2 章实证）。
    try:
        from inline_runner import _normalize_tracking
        prev = json.loads((book / "追踪" / "_tracking-state.json").read_text(encoding="utf-8"))
        _normalize_tracking(tx, st, (prev.get("context") or {}) if isinstance(prev, dict) else {})
    except Exception:
        pass
    import shutil
    import subprocess
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="wb-preflight-"))
    try:
        shutil.copytree(book / "追踪", tmp / "追踪")
        work = tmp / ".story" / "work" / f"第{chapter:03d}章"
        work.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(tx.parent, work)
        proc = subprocess.run(
            [python, tool, "commit", "--project", str(tmp), "--input", str(work / "tracking.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            # 工具版本不支持 commit（旧版/测试夹具）时 fail-open：真正的校验
            # 留给 N16，别让预检把整条链卡死。
            if "invalid choice" in detail or "unrecognized arguments" in detail:
                return ""
            return detail[-800:] or f"tracking_commit rc={proc.returncode}"
        return ""
    except Exception as exc:                      # 预检本身失败不阻断流程
        return ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_done(a) -> int:
    book = Path(a.book).resolve()
    st = load_state(book)
    if not st:
        print(json.dumps({"error": "not_initialized"}, ensure_ascii=False)); return 2
    # 令牌优先取 --token；省略时回退 WB_STEP_TOKEN 环境变量（审查 C1：运行时
    # 改走 env，避免令牌出现在本机进程管理器的命令行里）。
    token = a.token or os.environ.get("WB_STEP_TOKEN", "")
    if not token_ok(book, a.node, token):
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

    # 审查 A2：evidence 必须与 spec 期望模式一致，不能指认任意大文件
    mismatch = _evidence_matches_spec(book, spec, files)
    if mismatch:
        print(json.dumps({"error": "evidence_pattern_mismatch", "detail": mismatch},
                         ensure_ascii=False))
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
        # 裁决文件按名定位（不假设 files 顺序）：evidence-spec 模式比对已保证
        # 每个文件匹配某模式，这里取 judgments 文件本身。
        jf = next((f for f in files if f.name.startswith("judgments")), files[-1])
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

    # N12/N14：事务先过上游校验器，别等到 N16 才发现 schema 不合规
    preflight_error = _tracking_preflight(book, a.node)
    if preflight_error:
        print(json.dumps({
            "error": "tracking_preflight_failed",
            "detail": (f"{preflight_error}。修法：只改 .story/work/第NNN章/tracking.json "
                       "（字段名/枚举/id 形态/字节上限），正文不用重写；改完重新 done。"),
        }, ensure_ascii=False))
        return 2

    hashes = [sha256_of(f) for f in files]
    st["nodes"][a.node] = {
        "status": "done", "at": now(),
        "evidence": [str(f.relative_to(book)) if str(f).startswith(str(book)) else str(f) for f in files],
        "sha256": hashes,
        "attempts": st["nodes"].get(a.node, {}).get("attempts", 1),
    }
    st["llm_rounds"] = int(st.get("llm_rounds", 0)) + 1
    clear_token(book)
    # 审查 B1：N14 真实改写后必须先回 C{n}.N13 复检平台闸门，通过才进 N15
    # （plan 5.2.2）。自动跳过的 N14 不经此路（cmd_next 里 next_cursor 直达 N15）。
    if a.node.startswith("C") and a.node.endswith(".N14"):
        ch = int(a.node.split(".")[0][1:])
        st["recheck_chapter"] = ch
        st["cursor"] = f"C{ch}.N13"
    else:
        st["cursor"] = next_cursor(st)
    save_state(book, st)
    print(json.dumps({"ok": True, "node": a.node, "next": st["cursor"],
                      "llm_rounds": st["llm_rounds"]}, ensure_ascii=False))
    return 0


def cmd_skip(a) -> int:
    book = Path(a.book).resolve()
    st = load_state(book)
    if not st:
        print(json.dumps({"error": "not_initialized"}, ensure_ascii=False)); return 2
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
        print(json.dumps({"error": "not_initialized"}, ensure_ascii=False)); return 2
    # 审查 B5：有节点在途时，block 也要令牌——否则任何进程一句话停摆整链。
    held = read_token(book)
    token = getattr(a, "token", "") or ""
    if held.get("token") and not token_ok(book, held.get("node"), token):
        print(json.dumps({"error": "token_mismatch",
                          "hint": "有节点正在进行中，block 需要该节点的令牌"},
                         ensure_ascii=False))
        return 2
    st["blocked_reason"] = a.reason
    save_state(book, st)
    _control_log(book, {"cmd": "block", "reason": a.reason, "node": held.get("node")})
    print(json.dumps({"ok": True, "blocked": True, "reason": a.reason}, ensure_ascii=False))
    return 1


def cmd_resume(a) -> int:
    """不信任内存：清阻断、返还修复预算、与 tracking 账本交叉校验后继续。"""
    book = Path(a.book).resolve()
    st = load_state(book)
    if not st:
        print(json.dumps({"error": "not_initialized"}, ensure_ascii=False)); return 2
    was_blocked = bool(st.get("blocked_reason"))
    held = read_token(book)
    token = getattr(a, "token", "") or ""
    forced = bool(getattr(a, "force", False))
    # --force 是显式的人工/驱动意图（UI 恢复按钮、运行时恢复都会传）：放行在途
    # 令牌校验；否则有节点在途时 resume 需要该节点的令牌。
    if held.get("token") and not token_ok(book, held.get("node"), token) and not forced:
        print(json.dumps({"error": "token_mismatch",
                          "hint": "有节点正在进行中，resume 需要该节点的令牌或 --force"},
                         ensure_ascii=False))
        return 2
    # 审查 B5：人工阻断态（无在途令牌）必须显式 --force 才能恢复——
    # 防止被劫持的 subagent 把用户喊停的书重新跑起来烧钱。
    if was_blocked and not held.get("token") and not forced:
        print(json.dumps({"error": "blocked_needs_force",
                          "reason": st["blocked_reason"],
                          "hint": "该书处于人工阻断态；确认要继续请加 --force"},
                         ensure_ascii=False))
        return 2
    st["blocked_reason"] = None
    # 审查 B2：终停后人工修复再 resume，必须返还修复预算，否则第一次再 block
    # 就再次终停——恢复路径只给一次机会。
    st["fix_rounds"] = {}
    st["n20_rounds"] = 0
    st["global_fix_rounds"] = {}
    st.pop("recheck_chapter", None)
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
    _cross_check_tracking(book, st)
    save_state(book, st)
    _control_log(book, {"cmd": "resume", "was_blocked": was_blocked,
                        "forced": bool(getattr(a, "force", False))})
    print(json.dumps({"ok": True, "resumed_at": st["cursor"],
                      "chapter": st["current_chapter"]}, ensure_ascii=False))
    return 0
