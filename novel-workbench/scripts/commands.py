#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""commands.py — done / skip / block / resume 四个子命令

done 的验收（2026-10-25 审查后修订，此前的"四道验收"表述不实，已撤回）：
  1. token 必须与在途节点匹配（一次性握手）
  2. evidence 存在 ∧ 非空 ∧ 大小 > 节点阈值，且与 spec 的期望证据模式一致
     （审查 A2：此前用调用方传入值、不与 spec 比对，持令牌者可指认任意大文件）
  3. evidence 不得早于本轮 token 签发时间（防沿用旧产物）
  4. verify 节点额外：judgments 结构校验 + 原文引用硬校验（归一化后子串匹配）
注意：sha256 记录在 evidence_ledger；**已封板章节的正文会被比对**（见 _body_post_commit_drift），
未封板章节只记录不比对（同一节点重试时正文本来就会变）。
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
from pipeline_spec import GLOBAL_NODES, chapter_nodes, next_cursor, judgment_policy, body_file_for_chapter
from inline_runner import (run_node_script, verify_quote, append_chapter_findings,
                           append_endgame_findings, _cfg)
from judgment_norm import (normalize_judgment, judgment_schema_hint,
                           QUOTE_REQUIRED_FOR, MIN_GROUNDED_QUOTES)


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


def _mojibake_count(f: Path) -> int:
    """数证据文件里的 U+FFFD 替换字符（编码损坏的确定性指纹）。

    **为什么要有**（2026-11-15 实测）：写文件链路若按 Windows locale(cp936) 转码，
    中文会变成 U+FFFD 并吞掉全角字符后的换行——35 行的细纲塌成 17 行。
    这种文件**仍是合法 UTF-8**，严格解码查不出来，只会在下游以
    「字段缺失/五段式缺失/字数未识别」的形式反复报错，把轮次烧光。
    在证据入口拦一次，比让 judge 反复重写便宜得多。
    """
    try:
        return f.read_text(encoding="utf-8").count("\ufffd")
    except (OSError, UnicodeError):
        return 0


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
        bad = _mojibake_count(f)
        if bad:
            return False, (f"evidence 编码损坏：{f} 含 {bad} 个 U+FFFD 替换字符"
                           f"（写文件时按 cp936 转码所致）。"
                           f"请用 UTF-8 重写该文件，不要经 PowerShell echo/Out-File 转码"), []
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
    save_state(book, st, reason)
    print(json.dumps({"error": "verify_blocked", "reason": reason, **(extra or {})},
                     ensure_ascii=False))
    return 1


def _budget_scope(node: str) -> str:
    """按问题所属范围持久化阻断预算，跨 run 保留。"""
    match = re.match(r"^C(\d+)\.N15$", node)
    return f"C{match.group(1)}" if match else node


def _record_block_budget(st: dict, node: str, items: list) -> tuple[int, int, str]:
    """以 finding id/criterion 作为稳定指纹，同时保留总次数兜底。"""
    ids = sorted({str(x.get("id") or x.get("criterion") or "unknown")
                  for x in items if isinstance(x, dict) and x.get("blocking", True)})
    if not ids:
        ids = sorted({str(x.get("criterion") or x.get("id") or "unknown")
                      for x in items if isinstance(x, dict)})
    if not ids:
        ids = ["<empty-block>"]
    fp = hashlib.sha1("\n".join(ids).encode("utf-8")).hexdigest()[:12]
    scope = _budget_scope(node)
    budgets = st.setdefault("budget", {})
    old = budgets.get(scope) or {}
    rounds = int(old.get("rounds", 0)) + 1 if old.get("fp") == fp else 1
    total = int(old.get("total", 0)) + 1
    budgets[scope] = {"rounds": rounds, "total": total, "fp": fp,
                      "ids": ids, "updated_at": now()}
    return rounds, total, fp


def _handle_verify_block(book: Path, st: dict, node: str, j: dict, jf: Path) -> int:
    """block 不是终点：单章回 N14，终审回 N20f，超预算才停机。"""
    summary = j.get("summary", "")
    items = _fail_items(j)
    fingerprint_rounds, total_rounds, fingerprint = _record_block_budget(st, node, items)

    if node.startswith("C") and node.endswith(".N15"):
        ch = int(node.split(".")[0][1:])
        append_chapter_findings(book, ch, items, "N15")
        rounds = max(int(st.get("fix_rounds", {}).get(str(ch), 0)), fingerprint_rounds - 1)
        if rounds >= 2 or total_rounds >= 4:
            return _terminal_block(
                book, st, node,
                f"{node} 连续 {rounds + 1} 轮仍 block：{summary[:300]}", jf,
                {"hint": "同一问题失败 2 次或累计 block 4 次，需人工判断",
                 "budget": {"same_fingerprint": fingerprint_rounds, "total": total_rounds,
                            "fingerprint": fingerprint}})
        st["fix_rounds"] = {**(st.get("fix_rounds") or {}), str(ch): rounds + 1}
        st["cursor"] = f"C{ch}.N14"
        st["nodes"][node] = {"status": "failed", "at": now(), "judgments": str(jf)}
        save_state(book, st, f"{node} block，已回退到 {st['cursor']}")
        print(json.dumps({"ok": True, "blocked": True, "routed_to": st["cursor"],
                          "round": rounds + 1, "reason": summary[:300]}, ensure_ascii=False))
        return 1

    if node.startswith("V") and node.endswith(".N08"):
        target = node.rsplit(".", 1)[0] + ".N07"
        rounds = int(st.get("global_fix_rounds", {}).get(node, 0))
        if rounds >= 2 or total_rounds >= 4:
            return _terminal_block(book, st, node,
                f"{node} 连续审核仍 block：{summary[:300]}", jf,
                {"hint": "本卷主线修改达到预算上限，需人工判断",
                 "budget": {"same_fingerprint": fingerprint_rounds, "total": total_rounds,
                            "fingerprint": fingerprint}})
        st["global_fix_rounds"] = {**(st.get("global_fix_rounds") or {}), node: rounds + 1}
        st["cursor"] = target
        st["nodes"][node] = {"status": "failed", "at": now(), "judgments": str(jf)}
        save_state(book, st, f"{node} block，已回退到 {target}")
        print(json.dumps({"ok": True, "blocked": True, "routed_to": target,
                          "round": rounds + 1, "reason": summary[:300]}, ensure_ascii=False))
        return 1

    if node.startswith("V") and node.endswith(".N20"):
        append_endgame_findings(book, [{**item, "level": "A", "blocking": False,
                                         "original_level": item.get("level", "B"),
                                         "name": item.get("name") or item.get("criterion"),
                                         "note": item.get("note") or item.get("fix") or "卷末审查遗留"}
                                        for item in items], node)
        # 卷内章节已封板，不能用全书 N20f 去改写；记录为 advisory 并进入后续全书汇总。
        try:
            from pipeline_spec import next_cursor
            target = next_cursor(st)
        except Exception:
            target = "__done__"
        st.setdefault("volume_advisories", {})[node] = {
            "at": now(), "summary": summary[:500], "items": items,
            "fingerprint": fingerprint, "rounds": fingerprint_rounds,
        }
        st["nodes"][node] = {"status": "done", "at": now(), "judgments": str(jf),
                             "advisory": True}
        st["cursor"] = target
        save_state(book, st, f"{node} findings 已记为后续终审 advisory")
        print(json.dumps({"ok": True, "blocked": True, "advisory": True,
                          "routed_to": target, "reason": summary[:300]}, ensure_ascii=False))
        return 0

    if node == "N20g":
        rounds = int(st.get("global_fix_rounds", {}).get(node, 0))
        if rounds >= 1 or total_rounds >= 3:
            return _terminal_block(book, st, node,
                f"N20g 全书终审复核仍 block：{summary[:300]}", jf,
                {"hint": "全书概要修订一次后仍未通过，需人工判断",
                 "budget": {"same_fingerprint": fingerprint_rounds, "total": total_rounds,
                            "fingerprint": fingerprint}})
        st["global_fix_rounds"] = {**(st.get("global_fix_rounds") or {}), node: rounds + 1}
        st["cursor"] = "N17g"
        st["nodes"][node] = {"status": "failed", "at": now(), "judgments": str(jf)}
        save_state(book, st, "N20g block，已回到 N17g 重合成概要")
        print(json.dumps({"ok": True, "blocked": True, "routed_to": "N17g",
                          "round": rounds + 1, "reason": summary[:300]}, ensure_ascii=False))
        return 1

    if node == "N20":
        append_endgame_findings(book, items, "N20")
        rounds = int(st.get("n20_rounds", 0))
        if rounds >= 2 or total_rounds >= 4:
            return _terminal_block(
                book, st, node,
                f"N20 终审连续 {rounds + 1} 轮仍 block：{summary[:300]}", jf,
                {"hint": "N20f 已修 2 轮或累计 block 4 次，剩余问题需人工决策",
                 "budget": {"same_fingerprint": fingerprint_rounds, "total": total_rounds,
                            "fingerprint": fingerprint}})
        st["n20_rounds"] = rounds + 1
        st["cursor"] = "N20f"
        st["nodes"][node] = {"status": "failed", "at": now(), "judgments": str(jf)}
        save_state(book, st, "N20 block，已进入 N20f 定点修复")
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
        if rounds >= 2 or total_rounds >= 4:
            return _terminal_block(
                book, st, node,
                f"{node} 连续 {rounds + 1} 轮仍 block：{summary[:300]}", jf,
                {"hint": "闸门打回 2 轮或累计 block 4 次仍未过，需人工判断",
                 "budget": {"same_fingerprint": fingerprint_rounds, "total": total_rounds,
                            "fingerprint": fingerprint}})
        st["global_fix_rounds"] = {**(st.get("global_fix_rounds") or {}), node: rounds + 1}
        st["cursor"] = target
        st["nodes"][node] = {"status": "failed", "at": now(), "judgments": str(jf)}
        save_state(book, st, f"{node} block，已回退到 {target}")
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
    """judge 必须给出显式裁决；**阻断性结论**必须有可命中原文的 quote。

    裁决契约（2026-11-19 修订，根因见 judgment_norm.py 文件头）：
      - items 非空，每项要有 criterion / verdict / 合法 source；
      - fail 项必须给逐字引文——阻断与修复指令要落到具体字句上，必须有据；
      - pass 项可省引文（结构性检查本来就没有可引的句子，硬要引文只会逼
        judge 编），但整份裁决至少要有一条命中引文，证明它真读了源文件；
      - 机械项（有 metric）本身即是取证，不计入引文条数。
    此前「每项都要引文」是形式要求压在实质校验上：一份内容正确、引文真实
    的裁决，只要键名写得不一样就整份判废（冷启动书 N06 实测）。
    """
    policy = judgment_policy(node_id)
    bad: list[dict] = []
    items = judgment.get("items")
    if not isinstance(items, list) or not items:
        bad.append({"reason": "items 为空：judge 必须给出裁决项"})
        items = []
    verdict = judgment.get("verdict")
    if verdict not in ("pass", "block"):
        bad.append({"reason": f"verdict 必须显式写 pass 或 block（当前 {verdict!r}）"})
    grounded = 0
    has_mechanical = False
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
            else:
                has_mechanical = True
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
            # 引文是阻断的依据：fail 项没有引文 = 修复指令无处落地。
            if item.get("verdict") == QUOTE_REQUIRED_FOR:
                bad.append({"criterion": criterion,
                            "reason": "fail 项必须给出命中 source 的逐字 quote（连续 12-30 字）"})
        elif "…" in quote or "..." in quote:
            bad.append({"criterion": criterion, "reason": "quote 不得使用省略号"})
        elif not verify_quote(quote, source_file.read_text(encoding="utf-8")):
            bad.append({"criterion": criterion, "reason": "quote 未在 source 中命中"})
        else:
            grounded += 1
    if verdict == "block" and not any(
        isinstance(item, dict) and item.get("verdict") == "fail" for item in items
    ):
        bad.append({"reason": "verdict=block 但没有 fail 明细"})
    if items and grounded < MIN_GROUNDED_QUOTES and not has_mechanical:
        bad.append({"reason": "整份裁决没有任何一条命中原文的 quote——"
                              "无法确认 judge 真读过源文件（至少给一条逐字引文，"
                              "或把机械项的 metric 补上）"})
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


def _text_preflight(book: Path, node: str, st: dict) -> dict | None:
    """N12/N14 的正文预检：在 done 时就跑一遍闸门，不合格就拒绝 done。

    **为什么要有**：验证本来就存在（C{n}.N13 每轮重跑 storyctl+gate 并**覆盖**
    findings），但它发生在**下一步**。所以 N14 漏改一处，就要等下一轮 N13 才发现，
    白白烧掉一整轮 LLM（实测第 1 章第 2 轮说「三条已逐条处理」，实际漏了
    「不是工期，是他最后一次机会」，第 3 轮才被 N13 抓回来）。

    把这一步前移到 done，subagent 就能在**同一轮内**改完再交。

    拦截线 = blocking + 高优 advisory（HIGH_PRIORITY_ADVISORY）。
    其余 advisory 只记录不拦——否则会逼模型重写整段、把轮次耗光。

    fail-open：同一节点连续拒绝 2 次后放行，交给 N13/N14 的 fix_rounds 接管。
    没有这个兜底，一个改不动的章会让 done 永远失败、整条链卡死。
    """
    m = re.match(r"^C(\d+)\.N(12|14)$", node)
    if not m:
        return None
    chapter = int(m.group(1))
    if not (book / "正文").exists():
        return None

    attempts = int((st.get("text_preflight_attempts") or {}).get(node, 0))
    if attempts >= 2:
        # 已连续拒过 2 次 → 放行，让下游的 fix_rounds / 终停机制接管
        return None

    # 先自动裁字数（2026-11-17 加）：超长章此前只会被判 over 并打回，
    # 由人逐段读、逐段删——第 2 章手工裁 689 字，第 4 章补 500 字。
    #
    # **上限必须读细纲的「字数范围」，不能用常量**：实测第 6 章细纲写的是
    # 2000-2400，正文 2553 字被判 over，但这里硬编码 2600 就认为"没超"、
    # 不裁，于是预检放行、到 N16 才被字数闸门打回——白烧一轮。
    # 现在按细纲的实际上限裁，缺省才退回 CHAPTER_BODY_MAX_CHARS。
    trim_notes: list[str] = []
    trim_error = ""
    if m.group(2) == "12":
        try:
            from inline_runner import trim_to_band, visible_chars
            from pipeline_spec import CHAPTER_BODY_MAX_CHARS
            files = body_file_for_chapter(book, chapter)
            if len(files) == 1:
                band_max, band_min = CHAPTER_BODY_MAX_CHARS, 2000
                ol = sorted((book / "大纲").glob(f"细纲_第{chapter:03d}章.md"))
                if ol:
                    import re as _re2
                    ol_text = ol[0].read_text(encoding="utf-8")
                    rng = _re2.search(
                        r"字数范围\s*[：:]\s*(\d+)\s*[-–—~至]\s*(\d+)", ol_text)
                    if rng:
                        band_min, band_max = int(rng.group(1)), int(rng.group(2))
                before = visible_chars(files[0].read_text(encoding="utf-8"))
                if before > band_max:
                    _n, trim_notes = trim_to_band(
                        book, chapter, band_max=band_max, band_min=band_min)
        except Exception as exc:
            # 2026-11-20：此前这里是裸 `except Exception: trim_notes = []`，
            # 把「自动裁剪这步根本跑不起来」（常量从错误的模块 import，抛
            # ImportError）与「没什么可裁」混成同一件事——于是自动裁剪成了
            # 死代码，超长章只会被判 over 打回，由人逐段删。
            # 现在把失败原因记下来，带到返回载荷里。
            trim_error = f"自动裁剪不可用：{type(exc).__name__}: {exc}"
            trim_notes = []

    try:
        from inline_runner import _chapter_check, HIGH_PRIORITY_ADVISORY
        rc, res = _chapter_check(book, chapter, st, _write=False)
    except Exception:
        return None                       # 预检自身失败不阻断流程
    if rc != 0 or not isinstance(res, dict):
        return None                       # 工具硬失败 → 交给 N13/N16 兜底

    items = res.get("items") or []
    blocking = [x for x in items if isinstance(x, dict) and x.get("blocking")]
    high_adv = [x for x in items if isinstance(x, dict)
                and not x.get("blocking")
                and str(x.get("id") or x.get("criterion") or "") in HIGH_PRIORITY_ADVISORY]
    if not blocking and not high_adv:
        st.pop("text_preflight_attempts", None)
        return None

    st["text_preflight_attempts"] = {**(st.get("text_preflight_attempts") or {}),
                                     node: attempts + 1}
    save_state(book, st)

    def _brief(x: dict) -> dict:
        return {"id": x.get("id") or x.get("criterion"),
                "level": x.get("level", "B"),
                "name": str(x.get("name") or "")[:160]}

    out = {
        "blocking": [_brief(x) for x in blocking[:8]],
        "advisory": [_brief(x) for x in high_adv[:5]],
        "attempt": attempts + 1,
    }
    # 把自动裁剪的结果一并返回：编排者/日志能看到「工具自己裁了多少」，
    # 而不是以为 writer 写短了（trim_notes 此前只赋值不用，是死变量）。
    if trim_notes:
        out["auto_trim"] = trim_notes
    if trim_error:
        out["auto_trim_error"] = trim_error
    return out


# 追踪事务 `delta` 段的序列化字节硬上限（与 N12 预检一致）
TRACKING_DELTA_MAX_BYTES = 3072


def _shrink_delta_to_fit(delta: dict, limit: int) -> dict | None:
    """把 delta 的**长度类字段**截到字节上限内。返回新 delta，或 None（压不动）。

    只截断这些键：summary / objective_fact / reader_knowledge / change / result /
    constraints / next_chapter_commitments / detail / note。
    **不碰** id / status / importance / planted_chapter / planned_resolution_chapter /
    reveal_status / reveal_chapter / characters —— 那些是枚举与形态，动了就非法。

    实现复用 inline_runner.shrink_delta_to_fit（单一事实源）：
    2026-11-17 之前这里有一份**自己的副本**，两处规则不同（副本用固定长度 + 加「…」），
    于是同一份事务在两条路径上被截成不同的样子——**同一约束两处实现**的典型坑。
    """
    try:
        from inline_runner import shrink_delta_to_fit as _impl
    except ImportError:
        return None
    return _impl(delta, limit)


def _tracking_txn_ok(book: Path, node_id: str, st: dict) -> tuple[bool, str]:
    """C{n}.N12 的追踪事务必填项检查（2026-11-15 加）。

    **为什么要有**：实跑第 2、3 章时，writer 交来的
    `.story/work/第N章/tracking.json` 都是 `delta.*` 全空数组、
    `character_snapshots` 空、`context.position` 的 story_time / scene 空字符串。
    这些空事务到 N16 才被 tracking_commit 拒收，于是每章都要多烧一轮人工补填。
    在 `done` 的证据入口一次性拦下来，比让 N16 拒收便宜。

    **只查空，不查对错**：内容对不对归 N13/N16 和 judge 判，这里只兜住
    「一个字都没填」这种必然失败的情况。
    """
    m = re.match(r"^C(\d+)\.N12$", node_id)
    if not m:
        return True, ""
    chapter = int(m.group(1))
    txn = book / ".story" / "work" / f"第{chapter:03d}章" / "tracking.json"
    if not txn.is_file():
        # 事务文件本身不存在：留给 N16 报（那里有更具体的错误码）
        return True, ""
    try:
        raw = txn.read_text(encoding="utf-8")
        d = json.loads(raw)
    except (OSError, ValueError) as exc:
        return False, f"追踪事务无法解析：{exc}"
    if raw.count("\ufffd"):
        return False, f"追踪事务编码损坏（含 U+FFFD）：{txn.name}"
    problems = []
    delta = d.get("delta") or {}
    pos = (d.get("context") or {}).get("position") or {}
    if not str(delta.get("result") or "").strip():
        problems.append("delta.result")
    if not (delta.get("character_changes") or []):
        problems.append("delta.character_changes")
    if not (delta.get("timeline_events") or []):
        problems.append("delta.timeline_events")
    if not (d.get("character_snapshots") or {}):
        problems.append("character_snapshots")
    if not str(pos.get("story_time") or "").strip():
        problems.append("context.position.story_time")
    if not str(pos.get("scene") or "").strip():
        problems.append("context.position.scene")
    # 控制字段被改会被 N16 以 tracking state changed 拒收，这里提前报
    if d.get("mode") != "append":
        problems.append(f"mode={d.get('mode')!r}（应为 'append'）")
    if d.get("chapter") != chapter:
        problems.append(f"chapter={d.get('chapter')!r}（应为 {chapter!r}）")
    # 伏笔 status 枚举：N12 预检只认这四个，自造值会被判非法
    for f in (delta.get("foreshadow_changes") or []):
        st = f.get("status") if isinstance(f, dict) else None
        if st not in ("已埋", "已回收", "已过期", "放弃"):
            problems.append(f"foreshadow[{f.get('id')}].status={st!r}"
                            "（只允许 已埋/已回收/已过期/放弃）")
    # delta 序列化字节上限：N12 预检硬卡 3072。
    # **排除 retired_context_items**：那些条目是 preflight 自己填的（上一章被取代的
    # continuity_risks / constraints），writer 并不写它，也无从预知系统会替它填多少。
    # 2026-11-16 实测第 5 章卡在这里三轮：writer 把 delta 写到 3014B（合规），
    # preflight 自动填入 636B 退役条目后变成 3088B，而本守卫抢在 preflight 之前
    # 拒收，writer 无论怎么写都过不了。改由 preflight 的压缩器兜底（已补退役条目压缩）。
    writer_delta = {k: v for k, v in delta.items() if k != "retired_context_items"}
    delta_bytes = len(json.dumps(writer_delta, ensure_ascii=False).encode("utf-8"))
    if delta_bytes > TRACKING_DELTA_MAX_BYTES:
        # **自动压到刚好过线**（2026-11-17 加）。
        # 为什么要有：writer 估算字节数不准，实测第 8 章差 15 字节（3087 vs 3072）
        # 就被拒——让人回去再改一轮纯属浪费，因为内容本身完全合格。
        # 压缩只截断**长度类字段**（summary/objective_fact/reader_knowledge 等），
        # 不动 id / status / 枚举 / 必填结构——截断后仍是有效事务。
        trimmed = _shrink_delta_to_fit(writer_delta, TRACKING_DELTA_MAX_BYTES)
        if trimmed is not None:
            full = dict(d)
            full["delta"] = {**d.get("delta", {}), **trimmed}
            try:
                txn.write_text(json.dumps(full, ensure_ascii=False,
                                          separators=(",", ":")), encoding="utf-8")
                d = full
                delta = full["delta"]
                writer_delta = {k: v for k, v in delta.items() if k != "retired_context_items"}
                delta_bytes = len(json.dumps(writer_delta, ensure_ascii=False).encode("utf-8"))
                if delta_bytes <= TRACKING_DELTA_MAX_BYTES:
                    problems = [x for x in problems if not x.startswith("delta 序列化")]
            except OSError:
                pass
        if delta_bytes > TRACKING_DELTA_MAX_BYTES:
            problems.append(f"delta 序列化 {delta_bytes} 字节（上限 {TRACKING_DELTA_MAX_BYTES}）")
    if problems:
        return False, ("追踪事务必填项为空/不符，N16 封板必然被拒：" + "、".join(problems) +
                       f"。请回读 {txn} 逐项补齐后重新提交（控制字段原样保留）。")
    return True, ""


def _context_md_intact(book: Path, chapter: int) -> tuple[bool, str]:
    """校验 `追踪/写作上下文.md` 仍是 N11 生成的结构（2026-11-17 加）。

    **为什么要有**：N11 生成 `追踪/写作上下文.md`（S1–S7 七栏），writer 在 N12 读它。
    但实测第 6 章的 writer 交稿时**把该文件整个覆写**成了另一种结构
    （「写作连续性上下文 / 当前位置 / 活跃伏笔 / 近三章速记…」七栏），
    全仓库搜不到这个标题——是 subagent 自己编的。
    后果：下一章的 writer 读到的是缺 S2 上一章锚点、缺 S5 文风锚、缺 S7 事务说明的坏文件，
    而这些正是「无缝衔接」与「事务怎么填」的唯一来源。
    N11 是脚本节点，产物应当是**只读**的；writer 只该写 正文/ 与 .story/work/。

    **文件名的来历**（2026-11-20 修）：原先叫 `上下文.md`，但那个名字是
    **tracking_commit.py 的派生视图**——工作台抢着写它，技能每次封板又覆写
    回去，于是 `tracking_commit.py check` 对工作台的书永远报
    「derived view differs」（假警报）。现在各写各的：技能管 `上下文.md`，
    工作台管 `写作上下文.md`。
    """
    p = book / "追踪" / "写作上下文.md"
    if not p.is_file():
        # 文件不存在**不归本守卫管**：那是 N11 该生成的东西，缺了应由 N11/N12
        # 的上游检查报。这里若也判失败，会把没有上下文的夹具书与 N14 定点改写
        # 一并挡住（实测：既有回归 test_n14_done_rechecks_n13 直接挂）。
        # 本守卫只负责一件事——**存在但被改写**。
        return True, ""
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"追踪/写作上下文.md 无法读取：{exc}"
    missing = [f"S{i}" for i in range(1, 8) if f"## S{i} " not in text and f"## S{i}　" not in text]
    if missing:
        return False, (
            f"追踪/写作上下文.md 结构被破坏（缺 {'、'.join(missing)} 栏）。"
            "该文件由 C{N}.N11 脚本生成，是 writer 的**只读输入**，"
            "不要在 N12 里改写它——请从 git/快照恢复，或重跑 C{n}.N11。"
            "writer 只应写 正文/第NNN章_*.md 与 .story/work/第NNN章/tracking.json。")
    return True, ""


def _n20f_touched_chapters(book: Path) -> set[int]:
    """N20f 补丁日志里声明被改过的章。"""
    path = book / "_pipeline" / "n20f_patch_log.json"
    try:
        log = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    out: set[int] = set()
    for item in (log.get("chapters") or []):
        if not isinstance(item, dict):
            continue
        try:
            out.add(int(item.get("chapter")))
        except (TypeError, ValueError):
            continue
    return out


def _body_post_commit_drift(book: Path, st: dict, exempt_chapter=None,
                            exempt_chapters=None) -> list[str]:
    """检测**已封板章节的正文被事后改动**（2026-11-17 加）。

    **为什么要有**：sha256 此前只记录不比对。实测第 8 章：N16 封板后，
    有人又给正文补了因果收尾词（2383 字），而账本仍记 2348 字、sha 也不同。
    后果不只是字数对不上——**伏笔回收率、文风漂移、字数统计全部基于错位的基线**，
    而且没有任何机制会告诉任何人。

    现在：比对「**账本**（追踪/_tracking-state.json）记录的 body_sha256」与磁盘现状，
    不一致就报出来。这属于**已封板产物被篡改**，必须让人知道。

    **为什么比对账本而不是 state 节点**：首版比的是 state.json 里 `C{n}.N12` 的
    `sha256`，结果**误报**——第 8 章走 revision 路径（`storyctl chapter commit`）
    重新封板时，账本更新了、state 节点的 sha 没更新（revision 不经过 `done`），
    于是工具报「正文被改」，而磁盘与账本其实完全一致。
    账本才是权威——state 只是它的派生缓存。

    只查有 body_sha256 的记录；旧记录没有该字段就跳过（不误报）。

    `exempt_chapter`：带外重判（`rejudge`）时豁免该章——**目标章的正文本来
    就该与账本不一致**（这正是要重判的原因：正文改过了）。不豁免的话，
    「改正文 → 重判 → 提交」这条最自然的顺序会被漂移检测拦死，
    逼人先走一遍 revision 重新封板，白费一轮。

    `exempt_chapters`：一组豁免章。**N20f（全书终审的定点修复节点）用它**——
    那个节点的设计就是改已封板章节的正文，漂移检测在这里必须放行，
    否则终审判 block 之后**再也走不动**（N20f 提交被拦 → 账本不同步 →
    之后每一次 done 都被漂移拦下 → 整条链死锁）。
    """
    drift: list[str] = []
    p = book / "追踪" / "_tracking-state.json"
    if not p.is_file():
        return drift
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return drift
    records = state.get("wordcount_records") or {}
    exempt = set()
    if exempt_chapter is not None:
        exempt.add(int(exempt_chapter))
    if exempt_chapters:
        exempt.update(int(x) for x in exempt_chapters)
    for key, rec in records.items():
        if not isinstance(rec, dict):
            continue
        want = rec.get("body_sha256")
        if not want:
            continue
        try:
            ch = int(key)
        except (TypeError, ValueError):
            continue
        if ch in exempt:
            continue
        files = body_file_for_chapter(book, ch)
        if not files:
            drift.append(f"第{ch}章正文文件已消失（账本有记录）")
            continue
        got = sha256_of(files[0])
        if got != want:
            drift.append(f"第{ch}章正文在封板后被改动（sha 不一致）："
                         f"账本 {want[:12]}… / 磁盘 {got[:12]}…")
    return drift


def _ledger_integrity(book: Path) -> list[str]:
    """账本完整性检查：找出被静默截断的字符串（2026-11-17 加）。

    **为什么要有**：封板前的自动规整曾用**固定长度 + 「…」后缀**截断
    objective_fact / summary 等字段。结果「…」被 storyctl 当成**原文**写进账本，
    此后每次读账本都以为那是原文的一部分——**截断伪装成了原始内容**。
    实测账本里有 6 处这样的字符串（E002–E005 的 objective_fact、recent_chapters 摘要、
    next_chapter_commitments 首条）。

    现在改成比例压缩且不加省略号后，新写入的不会再有这个问题；
    但**历史遗留的截断还在账本里**，需要能查出来、并且能定位到具体是哪一章。
    """
    out: list[str] = []
    p = book / "追踪" / "_tracking-state.json"
    if not p.is_file():
        return out
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out

    def _walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]")
        elif isinstance(node, str) and node.rstrip().endswith("…"):
            out.append(f"{path}：{node[-40:]}")

    for key in ("timeline", "context", "foreshadow", "characters"):
        _walk(state.get(key), key)
    return out


def _cmd_ledger_repair(a) -> int:
    """账本修复：把被截断的字符串用**正规 revision 路径**重提（2026-11-17 加）。

    **为什么要有**：封板前的旧压缩器用固定长度 +「…」后缀截断字段，
    「…」被 storyctl 当成**原文**写进账本——**截断伪装成了原始内容**。
    实测 E002–E005 的 objective_fact 被截成半句（「...，三号…」）。

    修法不能是「直接改 state.json」——那是铁律禁止的（绕过事务与审计）。
    正解是走 `storyctl.py chapter commit` 的 revision 模式重提该章，
    由账本自己重新落一遍。

    本命令做三件事：
      1. 找出所有被截断的字段，定位到**属于哪一章**
      2. 用该章的正文 + 逐章记录，重建一份 revision 事务
      3. 交给 storyctl commit 落盘
    """
    book = Path(a.book).resolve()
    st = load_state(book)
    if not st:
        print(json.dumps({"error": "not_initialized"}, ensure_ascii=False))
        return 2
    problems = _ledger_integrity(book)
    if not problems:
        print(json.dumps({"ok": True, "repaired": [], "note": "账本无截断项"},
                         ensure_ascii=False))
        return 0
    print(json.dumps({
        "error": "ledger_truncated",
        "items": problems,
        "hint": ("账本里有被截断的字符串。修复需走 revision 路径逐章重提："
                 "对每章跑 `tracking_commit.py draft --chapter N` → 补全被截字段 → "
                 "`storyctl.py chapter commit --chapter N --input <事务>`。"
                 "不要直接改 _tracking-state.json（绕过审计）。"),
    }, ensure_ascii=False))
    return 2


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
    # 带外重判（autopilot.py rejudge）：单独签的令牌，**不动游标**。
    # 流水线是线性的，回不了头；但「章被改写后裁决依据过期」必须能补救。
    rejudging = False
    rejudge_chapter: int | None = None
    if st.get("rejudge_node") == a.node:
        if a.node.startswith("C") and ".N15" in a.node:
            rejudge_chapter = int(a.node.split(".")[0][1:])
            spec = chapter_nodes(st, book, rejudge_chapter).get(a.node)
            rejudging = True
        else:
            from pipeline_spec import GLOBAL_NODES, volume_nodes
            if a.node in GLOBAL_NODES:
                spec = GLOBAL_NODES[a.node]
                rejudging = True
            elif a.node.startswith("V") and a.node.endswith(".N20"):
                v = int(a.node[1:].split(".", 1)[0])
                spec = volume_nodes(st, book, v).get(a.node)
                rejudging = True
    if spec is None or spec["id"] != a.node:
        print(json.dumps({"error": "cursor_mismatch",
                          "expected": st.get("rejudge_node") or st["cursor"]},
                         ensure_ascii=False)); return 2

    ok, msg, files = _evidence_ok(book, a.evidence, spec.get("min_size", 1))
    if not ok:
        print(json.dumps({"error": "evidence_invalid", "detail": msg}, ensure_ascii=False))
        return 2

    txn_ok, txn_msg = _tracking_txn_ok(book, a.node, st)
    if not txn_ok:
        print(json.dumps({"error": "tracking_incomplete", "detail": txn_msg},
                         ensure_ascii=False))
        return 2

    # N12/N14 交稿时校验 N11 产物的结构完整性：上下文.md 是只读输入，
    # 被 writer 覆写会让下一章读到缺栏的坏文件（实测第 6 章踩到）。
    if re.match(r"^C(\d+)\.N1[24]$", a.node):
        ch = int(a.node.split(".")[0][1:])
        ctx_ok, ctx_msg = _context_md_intact(book, ch)
        if not ctx_ok:
            print(json.dumps({"error": "context_md_corrupted",
                              "detail": ctx_msg.replace("{N}", f"{ch:02d}").replace("{n}", str(ch))},
                             ensure_ascii=False))
            return 2

    # 已封板章节的正文被事后改动 → 报出来（2026-11-17 加）。
    # 实测第 8 章：N16 封板后正文又被改（2383 字），账本仍记 2348 字、
    # sha 也不同——伏笔回收率、文风漂移、字数统计全部基于错位的基线，
    # 而没有任何机制会告诉任何人。sha256 此前只记录不比对。
    # N20f 节点**设计上就要改已封板章节**（全书终审的定点修复）。
    # ① 漂移检测在这里必须放行：它拦的正是 N20f 要干的事；
    # ② 更要紧的是——不同步账本的话，之后每一次 done 都会被漂移拦下，整条链死锁。
    exempt_chapters: set[int] = set()
    if a.node == "N20f":
        exempt_chapters = _n20f_touched_chapters(book)
    drift = _body_post_commit_drift(
        book, st,
        exempt_chapter=rejudge_chapter,
        exempt_chapters=exempt_chapters)
    if drift:
        print(json.dumps({"error": "post_commit_drift", "items": drift,
                          "hint": "已封板章节的正文在封板后被改动。"
                                  "账本里的字数/伏笔/漂移基线因此失准。"
                                  "若改动是有意的，请重新走该章的 N12 提交并封板；"
                                  "若不是，请从快照恢复正文。"},
                         ensure_ascii=False))
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
            prior = st.get("nodes", {}).get(a.node) or {}
            if prior.get("status") != "done":
                prior = {}
            prior_evidence = prior.get("evidence") or []
            prior_hashes = dict(zip(prior_evidence, prior.get("sha256") or []))
            ledger = st.get("evidence_ledger") or {}
            reused_evidence = []
            stale = []
            run_started_at = st.get("run_started_at")
            try:
                run_started_ts = datetime.strptime(run_started_at, "%Y-%m-%dT%H:%M:%S%z").timestamp()
            except (TypeError, ValueError):
                run_started_ts = None
            for f in files:
                rel = str(f.relative_to(book)) if str(f).startswith(str(book)) else str(f)
                content_hash = sha256_of(f)
                accepted_hashes = set(ledger.get(f"{a.node}::{rel}", []))
                if rel in prior_hashes:
                    accepted_hashes.add(prior_hashes[rel])
                if content_hash in accepted_hashes:
                    reused_evidence.append(rel)
                    continue
                fresh_for_run = run_started_ts is not None and f.stat().st_mtime + 2 >= run_started_ts
                if f.stat().st_mtime + 2 < issued_ts and not fresh_for_run:
                    stale.append(str(f))
            if stale:
                print(json.dumps({
                    "error": "stale_evidence",
                    "detail": f"以下证据早于本轮 token 且不在本轮生成或已验收 hash 白名单中：{stale}",
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
        # 归一化层（2026-11-19）：把 judge 的别名键/别名容器搬到 canonical 形状，
        # 并从散文里回捞真正命中源文件的逐字引文。**只搬运不造事实**：
        # 引文捞不到就如实报错，不替 judge 圆场。
        j, norm_repairs = normalize_judgment(j, book, spec["id"])
        bad = _validate_judgment(book, spec["id"], j)
        if bad:
            # 自解释报错：把目标结构 + 字段规则 + 允许引用的文件 + 可抄示例
            # 一起吐出来。P2 的 node_retry 与对话路径的重派都靠它一次改对。
            hint = judgment_schema_hint(spec["id"], book)
            failure = {
                "node": spec["id"], "error": "quote_check_failed", "at": now(),
                "items": bad, "detail": hint["feedback"],
            }
            st["last_failure"] = failure
            save_state(book, st, f"{spec['id']} 裁决结构校验失败")
            print(json.dumps({"error": "quote_check_failed",
                              "detail": hint["feedback"],
                              "items": bad,
                              "required_schema": hint["schema"],
                              "allowed_sources": hint["allowed_sources"],
                              "example": hint["example"]},
                             ensure_ascii=False))
            return 2
        # 归一化真的改写了内容 → 落盘（repairs 为空 = 文件本来就是 canonical，
        # 不动它，避免给已完成的书 gratuitously 改写历史裁决）。
        # 下游（writer 读 fail 项的 fix 意见、N20/N15 读 items）必须拿到同
        # 一形状，否则归一化只在闸门内部有效。
        if norm_repairs:
            jf.write_text(json.dumps(j, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        verdict = j.get("verdict")
        # 不信任总 verdict 与明细矛盾：任何一条 B 级 fail 都必须 block
        if any(x.get("verdict") == "fail" and x.get("level", "B") == "B"
               for x in j.get("items", [])):
            verdict = "block"
        if verdict == "block":
            if rejudging:
                # 带外重判的 block：**不回退流水线**。C{n}.N15 的常规 block 会把
                # 游标拨回 C{n}.N14 去改写正文——但带外重判用于「已完稿的书」，
                # 回退等于把整条写作链重新拉回来。改成如实报出：这章有硬伤，
                # 先按 fix 改写正文，再重跑 rejudge。
                # 状态必须记成 failed：否则打包时会以为这章闸门是过的。
                st["nodes"][a.node] = {"status": "failed", "at": now(),
                                       "judgments": str(jf),
                                       "rejudge_blocked": True,
                                       "summary": str(j.get("summary") or "")[:300]}
                save_state(book, st, f"{a.node} 带外重判判定 block")
                print(json.dumps({
                    "error": "rejudge_blocked", "node": a.node,
                    "summary": str(j.get("summary") or "")[:300],
                    "items": (j.get("items") or [])[:10],
                    "hint": "该章裁决判定为 block——先按 fail 项的 fix 改写正文，"
                            "再重新跑 rejudge。流水线游标未动。",
                }, ensure_ascii=False))
                return 2
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

    # N12/N14：正文也先过一遍闸门，别等下一轮 N13 才发现漏改
    text_err = _text_preflight(book, a.node, st)
    if text_err:
        print(json.dumps({
            "error": "text_preflight_failed",
            "blocking": text_err["blocking"],
            "high_priority_advisory": text_err["advisory"],
            "attempt": text_err["attempt"],
            "hint": ("闸门仍未通过，**在同一轮内改完再交**，不要另起一轮。"
                     "按 id 逐条处理：em-dash/not-is-comparison 等句式类需重写该句；"
                     "长段需断段；字数超需删冗余但不删剧情。"
                     "连续 2 次不过会自动放行，交给 N13/N14 走定点改写。"),
        }, ensure_ascii=False))
        return 2

    # N20f：把它改过的章**重封**——账本的字数/sha/伏笔基线必须跟着正文走。
    # 不同步的后果不只是体检报告的字数是旧值：漂移检测会一直报这几章，
    # 于是之后**每一次** done 都被拦下，整条链死锁。
    resealed: list[int] = []
    reseal_errors: list[str] = []
    if a.node == "N20f" and exempt_chapters:
        from commit_chapter import CommitError, commit_chapter
        for ch in sorted(exempt_chapters):
            try:
                commit_chapter(book, ch, mode="revision", verify=False, _lock_held=True)
                resealed.append(ch)
            except CommitError as exc:
                reseal_errors.append(f"第{ch}章重封失败：{exc.code} {exc.message}")
            except Exception as exc:                       # 重封失败不阻断 N20f 本身
                reseal_errors.append(f"第{ch}章重封失败：{type(exc).__name__}: {exc}")

    reused_evidence = locals().get("reused_evidence", [])
    hashes = [sha256_of(f) for f in files]
    evidence_paths = [str(f.relative_to(book)) if str(f).startswith(str(book)) else str(f) for f in files]
    evidence_ledger = st.setdefault("evidence_ledger", {})
    for rel, content_hash in zip(evidence_paths, hashes):
        key = f"{a.node}::{rel}"
        accepted = list(evidence_ledger.get(key) or [])
        if content_hash in accepted:
            accepted.remove(content_hash)
        accepted.append(content_hash)
        evidence_ledger[key] = accepted[-3:]
    st["nodes"][a.node] = {
        "status": "done", "at": now(),
        "evidence": evidence_paths,
        "sha256": hashes,
        "reused_evidence": bool(reused_evidence),
        "attempts": st["nodes"].get(a.node, {}).get("attempts", 1),
    }
    # 成功即消费上一轮失败反馈：否则下次 next 还会把陈旧反馈灌给 agent。
    failure = st.get("last_failure")
    if isinstance(failure, dict) and failure.get("node") == a.node:
        st.pop("last_failure", None)
    st["llm_rounds"] = int(st.get("llm_rounds", 0)) + 1
    clear_token(book)
    if rejudging:
        # 带外重判：游标一动不动，只把这章的裁决刷新掉。
        st.pop("rejudge_node", None)
        st.setdefault("nodes", {})[a.node] = {
            **st["nodes"].get(a.node, {}),
            "status": "done", "at": now(), "rejudged_at": now(),
            "evidence": evidence_paths, "sha256": hashes,
        }
    elif a.node.startswith("C") and a.node.endswith(".N14"):
        # 审查 B1：N14 真实改写后必须先回 C{n}.N13 复检平台闸门，通过才进 N15
        # （plan 5.2.2）。自动跳过的 N14 不经此路（cmd_next 里 next_cursor 直达 N15）。
        ch = int(a.node.split(".")[0][1:])
        st["recheck_chapter"] = ch
        st["cursor"] = f"C{ch}.N13"
    else:
        st["cursor"] = next_cursor(st)
    save_state(book, st)
    print(json.dumps({"ok": True, "node": a.node, "next": st["cursor"],
                      "llm_rounds": st["llm_rounds"],
                      "rejudged": rejudging,
                      "resealed": resealed,
                      "reseal_errors": reseal_errors,
                      "reused_evidence": bool(reused_evidence)}, ensure_ascii=False))
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
    """不信任内存：清阻断、保留失败预算、与 tracking 账本交叉校验后继续。"""
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
    reset_budget = bool(getattr(a, "reset_budget", False))
    scope = str(getattr(a, "scope", "") or "").strip()
    if scope and not reset_budget:
        print(json.dumps({"error": "scope_requires_reset_budget",
                          "hint": "--scope 只能与 --reset-budget 一起使用"}, ensure_ascii=False))
        return 2
    # 作用域（2026-11-17 扩）：此前只认章级 `C{n}` 与固定节点名，
    # **不认节点级 `C{n}.Nxx`**——而僵死恢复恰恰需要节点粒度：
    # 一个 subagent 死在 C8.N09 时，没有办法只重置这一个节点的尝试计数。
    # 现在 `C{n}.Nxx` 合法，且额外清掉该节点的 attempts（重试预算）。
    valid_scope = re.fullmatch(
        r"C\d+(\.N\d+)?|V\d+\.N08|N04|N06|N08|N20|N20g|GLOBAL", scope) if scope else None
    if scope and not valid_scope:
        print(json.dumps({"error": "invalid_budget_scope", "scope": scope,
                          "hint": "scope 格式：C{n}、C{n}.N{xx}、V{v}.N08、N04/N06/N08/N20/N20g/GLOBAL"},
                         ensure_ascii=False))
        return 2

    st["blocked_reason"] = None
    # run_started_at 记的是「**这一轮 run 什么时候开始的**」，而 resume 是
    # **继续同一轮**（解卡），不是开新一轮。
    # 2026-11-20 实测踩坑：这里原本无条件 `= now()`，于是「resume 一次」
    # 把「本轮已产出、只是还没提交」的证据判成 stale——因为防陈旧闸门
    # （_body_post_commit_drift 之外的 stale_evidence 检查）把
    # run_started_at 当作「本轮新鲜度」的逃生口：
    #     fresh_for_run = 文件 mtime + 2 >= run_started_at
    # resume 一改，逃生口就失效了。实测顺序：writer 写完第 8 章 → 为重封
    # 第 7 章先 resume → 再提交第 8 章 → 「证据早于本轮 token」被拒，
    # 而那份证据明明是本轮刚写的。
    # 只有从来没有记录过（老书补字段）时才补一个。
    st.setdefault("run_started_at", now())
    # 预算属于书的持久状态，不属于某次 run。普通 resume/--force 只解除运行阻断。
    # 仅显式 --reset-budget 才重置，并记录控制日志便于审计。
    if reset_budget:
        if not scope or scope == "GLOBAL":
            st["fix_rounds"] = {}
            st["n20_rounds"] = 0
            st["global_fix_rounds"] = {}
            st["budget"] = {}
        elif scope.startswith("C"):
            chapter = scope[1:]
            st.get("fix_rounds", {}).pop(chapter, None)
            st.get("budget", {}).pop(scope, None)
            # 节点级作用域：C{n}.Nxx → 清掉该节点的尝试计数（重试预算）。
            # 此前只能章级，导致僵死节点无法单独重置。
            if "." in scope:
                node_id = scope
                node_rec = (st.get("nodes") or {}).get(node_id)
                if isinstance(node_rec, dict):
                    node_rec["attempts"] = 0
                    node_rec["status"] = "pending"
        else:
            st.get("budget", {}).pop(scope, None)
            st.get("global_fix_rounds", {}).pop(scope, None)
            if scope == "N20":
                st["n20_rounds"] = 0
    # N14 后的 N13 是待完成的质量复检；resume 必须保留其路由标记。
    # 其他游标不依赖该标记时才清除遗留值。
    if st.get("cursor") != f"C{st.get('recheck_chapter')}.N13":
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
    save_state(book, st, "resume 后续跑")
    _control_log(book, {"cmd": "resume", "was_blocked": was_blocked,
                        "forced": forced, "reset_budget": reset_budget, "scope": scope or "GLOBAL"})
    print(json.dumps({"ok": True, "resumed_at": st["cursor"],
                      "chapter": st["current_chapter"],
                      "budget_reset": reset_budget}, ensure_ascii=False))
    return 0
