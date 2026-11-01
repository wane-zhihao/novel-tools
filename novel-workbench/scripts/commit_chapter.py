#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单章定稿（手动 commit）：把某一章按 append / revision 提交进故事账本。

与 C{n}.N16 共用同一个底层工具 `storyctl chapter commit`，所以「定稿」只有一处
实现；与 next/done 共用 `_pipeline/.next.lock`，保证单写者。

可靠性约定（四条硬属性）：
  · 幂等：正文 sha 与账本 body_sha256 一致 → already，不写任何东西
  · 原子：提交前备份账本与逐章记录，任一步失败原样回滚
  · fail-closed：缺事务 / 闸门阻断 / 字数越界一律拒绝，**绝不自动建空事务**
    （空事务会写进账本，导致后续章节丢掉本章全部剧情事实，且不报错）
  · 游标：只有流水线正停在本章时才推进 cursor，其余情况一律不动
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from autopilot import (acquire_next_lock, load_state, now, read_token,
                       release_next_lock, save_state, sha256_of)
from inline_runner import (_cfg, _chapter_check, _mark_findings_resolved,
                           _normalize_tracking, run_script)
from pipeline_spec import next_cursor


class CommitError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail or {}


def _read(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _fact_check(book: Path):
    """跑全书事实闸门（platforms/_core/tools/fact_gate.py）。

    为什么要在提交时跑：章级 quality check 只测文风与 AI 句式，
    结构上测不出「昨天该是今天」「不合格是3个字」「两万四写成两万三」。
    2026-10-28《顶职》栽过一次：9 个章级检查全过，事实错误照样进了账本。

    返回 (rc, payload)。rc: 0 过 / 1 有阻断 / 2 工具不可用。
    """
    cfg = _cfg()
    tool = Path(cfg.get("FACT_GATE") or r"F:\小说\platforms\_core\tools\fact_gate.py")
    if not tool.is_file():
        return 2, {"error": f"fact_gate 不存在：{tool}"}
    args = [cfg["PYTHON"], str(tool), str(book / "正文"), "--json"]
    conf = book / "追踪" / "_fact-assertions.json"
    if conf.is_file():
        args += ["--config", str(conf)]
    rc, out = run_script(args, book, timeout=120)
    try:
        return rc, json.loads(out)
    except ValueError:
        return rc, {"raw": str(out)[-400:]}


def _chapter_body(book: Path, chapter: int) -> Path:
    files = sorted((book / "正文").glob(f"第{chapter:03d}章_*.md"))
    if not files:
        raise CommitError("chapter_missing", f"找不到第 {chapter} 章正文", 409)
    if len(files) > 1:
        raise CommitError("chapter_ambiguous",
                          f"第 {chapter} 章有 {len(files)} 个正文文件，先清理歧义", 409,
                          {"files": [f.name for f in files]})
    return files[0]


def _backup(book: Path, chapter: int, revision: int) -> Path:
    """提交前备份账本与本章逐章记录；失败时按此原样回滚。"""
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = book / "_pipeline" / "snapshots" / f"C{chapter}_before_commit_r{revision}_{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    for src in (book / "追踪" / "_tracking-state.json",
                book / "追踪" / "逐章记录" / f"第{chapter:03d}章.md"):
        if src.is_file():
            shutil.copy2(src, dest / src.name)
    return dest


def _restore(book: Path, chapter: int, backup: Path) -> None:
    for name, target in (
        ("_tracking-state.json", book / "追踪" / "_tracking-state.json"),
        (f"第{chapter:03d}章.md", book / "追踪" / "逐章记录" / f"第{chapter:03d}章.md"),
    ):
        src = backup / name
        if src.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)


def _map_commit_failure(out: object) -> tuple[str, str, int]:
    text = str(out or "")
    if "LENGTH_OUT_OF_BAND" in text:
        return "length_out_of_band", "字数不在本章目标区间内", 422
    if "QUALITY_BLOCKED" in text:
        return "quality_blocked", "正文有硬伤，未通过质量复检", 422
    if "TOOL_UNAVAILABLE" in text or "NODE_REQUIRED" in text:
        return "quality_tool_unavailable", "闸门工具不可用", 400
    return "commit_failed", "封板失败", 500


def _book_summary(book: Path, tk: dict) -> dict:
    records = tk.get("wordcount_records") or {}
    last = int(tk.get("last_committed_chapter") or 0)
    dirty = 0
    for key, rec in records.items():
        if not isinstance(rec, dict) or not rec.get("body_sha256"):
            continue
        try:
            n = int(key)
        except (TypeError, ValueError):
            continue
        files = sorted((book / "正文").glob(f"第{n:03d}章_*.md"))
        if len(files) == 1 and sha256_of(files[0]) != rec["body_sha256"]:
            dirty += 1
    return {
        "chapters_committed": last,
        "words_total": sum(int(v.get("actual") or 0) for v in records.values()
                           if isinstance(v, dict)),
        "dirty_count": dirty,
        "next_chapter": last + 1,
    }


def commit_chapter(book: Path, chapter: int, *, mode: str = "auto", verify: bool = True,
                   accept_current_length: bool = False) -> dict:
    book = Path(book).resolve()
    lock = acquire_next_lock(book)
    if lock is None:
        raise CommitError("chapter_busy", "流水线正忙（另一个 next/done/commit 在跑）", 409)
    try:
        st = load_state(book)
        if not st:
            raise CommitError("not_initialized", "这本书还没初始化", 400)
        if read_token(book).get("token"):
            raise CommitError("chapter_busy", "流水线正卡在一个节点上，先恢复或停止", 409)

        tk = _read(book / "追踪" / "_tracking-state.json", {}) or {}
        last = int(tk.get("last_committed_chapter") or 0)
        if chapter < 1:
            raise CommitError("invalid_chapter", "章节号必须 ≥ 1", 400)
        if chapter > last + 1:
            raise CommitError("chapter_gap",
                              f"第 {last + 1} 章还没定稿，不能跳着定第 {chapter} 章", 409)

        body = _chapter_body(book, chapter)
        resolved = ("append" if chapter == last + 1 else "revision") if mode == "auto" else mode
        if resolved == "append" and chapter != last + 1:
            raise CommitError("chapter_gap", f"第 {chapter} 章不是下一章，不能按 append 定稿", 409)
        if resolved == "revision" and chapter > last:
            raise CommitError("chapter_gap", f"第 {chapter} 章还没定稿过，不能按 revision 定稿", 409)

        body_sha = sha256_of(body)
        record = (tk.get("wordcount_records") or {}).get(str(chapter)) or {}
        if resolved == "revision" and record.get("body_sha256") == body_sha:
            return {
                "committed": True, "already": True, "chapter": chapter, "mode": resolved,
                "words": record.get("actual"), "state_revision": tk.get("state_revision"),
                "gate": {"status": "skipped", "blocking_count": 0, "advisory_count": 0},
                "book": _book_summary(book, tk), "warnings": [],
            }

        gate = {"status": "skipped", "blocking_count": 0, "advisory_count": 0}
        if verify:
            rc, out = _chapter_check(book, chapter, st)
            if rc != 0:
                raise CommitError("quality_tool_unavailable", str(out)[-300:], 400)
            findings = _read(book / "_pipeline" / f"findings_C{chapter}.json", {}) or {}
            blocking = int(findings.get("blocking_count") or 0)
            gate = {"status": "pass" if blocking == 0 else "blocked",
                    "blocking_count": blocking,
                    "advisory_count": int(findings.get("advisory_count") or 0)}
            if blocking:
                raise CommitError("gate_blocked", f"没过闸门：{blocking} 条阻断", 422,
                                  {"gate": gate,
                                   "findings": (findings.get("items") or [])[:10]})

            # 事实层：章级检查测不出的东西，在这里拦（2026-10-28 增补）
            frc, fpayload = _fact_check(book)
            if frc == 2:
                raise CommitError("fact_tool_unavailable",
                                  f"事实闸门不可用：{str(fpayload)[-200:]}", 400)
            fblock = fpayload.get("blocking") or []
            # 状态直接取闸门回报：pass / unverified / advisory / blocked。
            # 没有事实台账时是 unverified —— 绝不能记成 pass，否则又变成假绿灯。
            gate["fact"] = {"status": fpayload.get("status", "unverified"),
                            "blocking_count": len(fblock),
                            "advisory_count": len(fpayload.get("advisory") or [])}
            if fblock:
                raise CommitError(
                    "fact_blocked",
                    f"事实层阻断 {len(fblock)} 条（日期/数值/字数断言）", 422,
                    {"fact_gate": gate["fact"], "findings": fblock[:10]})

        cfg = _cfg()
        txn = book / ".story" / "work" / f"第{chapter:03d}章" / "tracking.json"
        warnings: list[str] = []
        if not txn.is_file():
            if resolved == "append":
                # fail-closed：空事务会静默污染账本，宁可拒绝
                raise CommitError("tracking_missing",
                                  "这一章还没有故事记录（tracking 事务），需要先让 AI 补齐", 409)
            rc, out = run_script([cfg["PYTHON"], cfg["TRACKING_COMMIT"], "draft",
                                  "--project", str(book), "--chapter", str(chapter),
                                  "--out", str(txn), "--force"], book, timeout=120)
            if rc != 0 or not txn.is_file():
                raise CommitError("tracking_invalid",
                                  f"重建本章故事记录失败：{str(out)[-200:]}", 409)

        prev_ctx = (tk.get("context") or {}) if isinstance(tk, dict) else {}
        try:
            warnings.extend(_normalize_tracking(txn, st, prev_ctx) or [])
        except Exception as exc:                       # 规整失败不阻断，交给上游校验
            warnings.append(f"规整跳过：{exc}")

        backup = _backup(book, chapter, last)
        cmd = [cfg["PYTHON"], cfg["STORYCTL"], "chapter", "commit",
               "--project", str(book), "--chapter", str(chapter), "--input", str(txn)]
        if accept_current_length:
            cmd += ["--accept-current-length", "--force"]
        rc, out = run_script(cmd, book, timeout=360)
        if rc != 0:
            _restore(book, chapter, backup)
            code, message, status = _map_commit_failure(out)
            raise CommitError(code, message, status, {"detail": str(out)[-400:]})

        state2 = _read(book / "追踪" / "_tracking-state.json", {}) or {}
        records = state2.get("wordcount_records") or {}
        st["words_total"] = sum(int(v.get("actual") or 0) for v in records.values()
                                if isinstance(v, dict))
        st["chapters_committed"] = int(state2.get("last_committed_chapter") or 0)
        st.setdefault("nodes", {})[f"C{chapter}.N16"] = {
            "status": "done", "at": now(), "source": "manual",
        }
        cur = str(st.get("cursor") or "")
        if cur.startswith(f"C{chapter}."):
            st["cursor"] = next_cursor(st)
            st.pop("recheck_chapter", None)
            if resolved == "append":
                st["current_chapter"] = max(int(st.get("current_chapter") or 0), chapter)
        save_state(book, st)

        rec = records.get(str(chapter)) or {}
        _mark_findings_resolved(book, chapter, {
            k: rec.get(k) for k in ("actual", "target", "metric", "status", "resolution")
            if rec.get(k) is not None
        })
        return {
            "committed": True, "already": False, "chapter": chapter, "mode": resolved,
            "words": rec.get("actual"), "state_revision": state2.get("state_revision"),
            "committed_at": now(), "gate": gate,
            "book": _book_summary(book, state2), "warnings": warnings,
        }
    finally:
        release_next_lock(lock)
