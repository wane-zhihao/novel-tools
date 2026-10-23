#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""inline_runner.py — script 节点内联执行 + done 验收工具

被 autopilot.py 导入。所有确定性校验脚本都在这里由 subprocess 执行，
LLM 只发指令、不碰参数——这是「物理上无法篡改脚本参数」的实现基础。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent


def _cfg():
    return json.loads((SKILL_DIR / "config.json").read_text(encoding="utf-8"))


def utf8_env() -> dict:
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def run_script(cmd: list[str], cwd: Path, timeout: int = 180) -> tuple[int, str]:
    """统一的脚本执行入口：锁 UTF-8、锁 cwd、锁超时。"""
    try:
        r = subprocess.run(cmd, cwd=str(cwd), env=utf8_env(),
                           encoding="utf-8", errors="replace",
                           capture_output=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s: {' '.join(cmd)}"
    except FileNotFoundError as e:
        return 127, f"NOT_FOUND: {e}"


# ─── 各 script 节点的具体动作 ─────────────────────────────────────────────────

def _tracking_draft(book: Path, chapter: int) -> tuple[int, str]:
    """C{n}.N11：生成预填追踪事务 + shapes，供 writer 填写。"""
    cfg = _cfg()
    out = book / ".story" / "work" / f"第{chapter:03d}章" / "tracking.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [cfg["PYTHON"], cfg["TRACKING_COMMIT"], "draft",
           "--project", str(book), "--chapter", str(chapter), "--out", str(out)]
    return run_script(cmd, book)


def _outline_gate(book: Path, chapter: int) -> tuple[int, str]:
    """C{n}.N10：细纲九字段结构验收。"""
    cfg = _cfg()
    hits = sorted((book / "大纲").glob(f"细纲_第{chapter:03d}章*.md"))
    if not hits:
        return 2, f"找不到 大纲/细纲_第{chapter:03d}章*.md"
    cmd = [cfg["NODE"], cfg["CHECK_OUTLINE"], str(hits[0])]
    return run_script(cmd, book)


def _chapter_check(book: Path, chapter: int) -> tuple[int, str]:
    """C{n}.N13：合并检查 = 原 storyctl check + 平台闸门 G8/G9。"""
    cfg = _cfg()
    cmd = [cfg["PYTHON"], cfg["QIMAO_CHAPTER_CHECK"],
           "--project", str(book), "--chapter", str(chapter),
           "--genre", "niandai"]
    return run_script(cmd, book, timeout=300)


def _commit_chapter(book: Path, chapter: int) -> tuple[int, str]:
    """C{n}.N16：原子封板 = tracking commit + storyctl chapter commit（两个都要调）。"""
    cfg = _cfg()
    txn = book / ".story" / "work" / f"第{chapter:03d}章" / "tracking.json"
    if not txn.is_file():
        return 2, f"找不到追踪事务 {txn}——writer 必须在 N12 按 shapes 填好它"
    rc1, o1 = run_script([cfg["PYTHON"], cfg["TRACKING_COMMIT"], "commit",
                          "--project", str(book), "--input", str(txn)], book)
    if rc1 != 0:
        return rc1, f"tracking_commit 失败：{o1[-500:]}"
    rc2, o2 = run_script([cfg["PYTHON"], cfg["STORYCTL"], "chapter", "commit",
                          "--project", str(book), "--chapter", str(chapter)], book)
    if rc2 != 0:
        return rc2, f"storyctl chapter commit 失败：{o2[-500:]}"
    return 0, o1[-300:] + "\n" + o2[-300:]


def _merge_full(book: Path) -> tuple[int, str]:
    """N17：合成全文到 投稿包/全文.md。"""
    parts = sorted((book / "正文").glob("*.md"))
    if not parts:
        return 2, "正文/ 下没有章节文件"
    buf = []
    for p in parts:
        buf.append(p.read_text(encoding="utf-8").strip())
    out = book / "投稿包" / "全文.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n\n".join(buf) + "\n", encoding="utf-8")
    return 0, f"合并 {len(parts)} 章 -> {out}"


# ─── 节点 → 动作 分发表 ───────────────────────────────────────────────────────

def run_node_script(book: Path, st: dict, spec: dict) -> tuple[int, str]:
    nid = spec["id"]
    ch = st["current_chapter"] + 1
    if nid == "N01":
        return 0, "init ok"
    if nid.endswith(".N10"):
        return _outline_gate(book, ch)
    if nid.endswith(".N11"):
        return _tracking_draft(book, ch)
    if nid.endswith(".N13"):
        return _chapter_check(book, ch)
    if nid.endswith(".N16"):
        return _commit_chapter(book, ch)
    if nid == "N17":
        return _merge_full(book)
    if nid == "N02":
        return 0, "style anchor: 见 qimao/harness/style-anchor.json（已 calibrated）"
    return 0, f"{nid} 无内联动作（占位）"


# ─── verify 节点的原文引用硬校验（暗坑6） ─────────────────────────────────────

def verify_quote(quote: str, chapter_text: str, min_len: int = 8) -> bool:
    q, t = _norm(quote), _norm(chapter_text)
    return len(q) >= min_len and q in t


def _norm(s: str) -> str:
    import re
    return re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", s or "")
