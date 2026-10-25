#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""inline_runner.py — script 节点内联执行 + findings 汇流

被 autopilot.py 导入。所有确定性校验脚本都在这里由 subprocess 执行，
LLM 只发指令、不碰参数——这是「物理上无法篡改脚本参数」的实现基础。

两类 findings 通道：
  _pipeline/findings_C{n}.json      单章问题 → C{n}.N14 定点改写消费
  _pipeline/endgame_findings.json   全书问题 → N20 复核、N20f 单点修补消费
"""
from __future__ import annotations

import json
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from pipeline_spec import milestones, span, volumes  # 里程碑/卷边界的唯一定义处

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent

from pipeline_spec import STATION_MIN_WORDS  # noqa: E402  (只依赖常量，不反向导入 runner)

DRIFT_PARAGRAPH_DELTA = 5.0
FORESHADOW_MIN_RATE = 0.70


def _cfg():
    override = os.environ.get("WORKBENCH_CONFIG")
    path = Path(override) if override else (SKILL_DIR / "config.json")
    return json.loads(path.read_text(encoding="utf-8"))


def utf8_env() -> dict:
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def platform_cfg(st: dict) -> dict:
    """取当前书的平台配置。st["platform"] 缺省 qimao（老书兼容）。

    返回 {DIR, GENRE_BASE, STYLE_ANCHOR, GENRES}。
    """
    cfg = _cfg()
    name = (st or {}).get("platform") or "qimao"
    plats = cfg.get("PLATFORMS") or {}
    if name not in plats:
        raise RuntimeError(f"UNKNOWN_PLATFORM {name!r}，可用：{list(plats)}")
    return plats[name]


def genre_baseline(st: dict) -> dict:
    """取本书题材的基准区间。

    **取不到必须硬报错**——此前是 `.get(genre) or {}` 静默返回空，
    导致纵横的 dushi/xuanhuan/lishi 在七猫 baselines 里查不到时，
    S5 上下文静默输出 null 指标而不报错（2026-10-25 修）。
    """
    pc = platform_cfg(st)
    genre = (st or {}).get("genre") or ""
    doc = _read_json(Path(pc["GENRE_BASE"]), default={}) or {}
    baselines = doc.get("baselines") or {}
    blk = baselines.get(genre)
    if blk is None and not (st or {}).get("platform"):
        # 老书（state 里没有 platform 键）：保留原静默回落，避免破坏七猫既有书
        return {}
    if blk is None:
        raise RuntimeError(
            f"GENRE_BASELINE_MISSING genre={genre!r} 白名单={list(baselines)} "
            f"平台={(st or {}).get('platform') or 'qimao'}")
    return blk


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


def run_script_capture(cmd: list[str], cwd: Path, timeout: int = 180) -> tuple[int, str, str]:
    """stdout/stderr 分开返回（审查 B6）：第三方脚本 stderr 里的 warning/traceback
    会污染拼接输出的 JSON 提取（花括号错位导致误硬停）。"""
    try:
        r = subprocess.run(cmd, cwd=str(cwd), env=utf8_env(),
                           encoding="utf-8", errors="replace",
                           capture_output=True, timeout=timeout)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", f"TIMEOUT after {timeout}s: {' '.join(cmd)}"
    except FileNotFoundError as e:
        return 127, "", f"NOT_FOUND: {e}"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def extract_json(text: str):
    """从脚本输出里取第一个完整 JSON 值。

    storyctl / gate_runner 只打印 JSON，但第三方脚本可能混入 warning 行，
    所以按最先出现的 { 或 [ 决定容器类型，再取到最后一个对应闭合符。
    """
    text = (text or "").strip()
    i_obj, i_arr = text.find("{"), text.find("[")
    if i_arr >= 0 and (i_obj < 0 or i_arr < i_obj):
        opener, closer = "[", "]"
    else:
        opener, closer = "{", "}"
    i = text.find(opener)
    j = text.rfind(closer)
    if i >= 0 and j > i:
        try:
            return json.loads(text[i:j + 1])
        except json.JSONDecodeError:
            return None
    return None


def find_chapter_file(book: Path, chapter: int) -> Path:
    hits = sorted((book / "正文").glob(f"第{chapter:03d}章_*.md"))
    if not hits:
        raise FileNotFoundError(f"找不到 正文/第{chapter:03d}章_*.md")
    if len(hits) > 1:
        names = "、".join(p.name for p in hits)
        raise RuntimeError(f"第{chapter}章正文文件不唯一：{names}")
    return hits[0]


# ─── findings 汇流 ────────────────────────────────────────────────────────────

def _recount(doc: dict) -> dict:
    items = doc.get("items", [])
    doc["blocking_count"] = sum(1 for x in items if x.get("blocking"))
    doc["advisory_count"] = sum(1 for x in items if not x.get("blocking"))
    doc["items"] = items
    return doc


def _write_counted(path: Path, doc: dict) -> dict:
    _recount(doc)
    _write_json(path, doc)
    return doc


def write_chapter_findings(book: Path, chapter: int, items: list[dict],
                           metrics: dict | None = None) -> dict:
    doc = {
        "chapter": chapter,
        "generated_at": _now(),
        "items": items,
        "metrics": metrics or {},
    }
    return _write_counted(book / "_pipeline" / f"findings_C{chapter}.json", doc)


def append_chapter_findings(book: Path, chapter: int, items: list[dict],
                            source: str) -> dict:
    """把 N15 失败项并入单章 findings；替换同源旧项，避免多轮重复堆积。"""
    path = book / "_pipeline" / f"findings_C{chapter}.json"
    doc = _read_json(path, default={}) or {}
    kept = [x for x in doc.get("items", []) if x.get("source") != source]
    for item in items:
        kept.append({**item, "source": source})
    doc.update({"chapter": chapter, "generated_at": _now(), "items": kept})
    return _write_counted(path, doc)


def chapter_has_blocking(book: Path, chapter: int) -> bool:
    doc = _read_json(book / "_pipeline" / f"findings_C{chapter}.json", default={}) or {}
    return bool(doc.get("blocking_count"))


def next_fix_round(st: dict, chapter: int) -> int:
    rounds = st.setdefault("fix_rounds", {})
    rounds[str(chapter)] = int(rounds.get(str(chapter), 0)) + 1
    return rounds[str(chapter)]


def reset_endgame_findings(book: Path, st: dict) -> None:
    _write_counted(book / "_pipeline" / "endgame_findings.json", {
        "book": st.get("book") or book.name,
        "chapters_scanned": 0,
        "generated_at": _now(),
        "items": [],
    })


def append_endgame_findings(book: Path, items: list[dict], source: str,
                            chapters_scanned: int | None = None) -> dict:
    path = book / "_pipeline" / "endgame_findings.json"
    doc = _read_json(path, default={}) or {}
    kept = [x for x in doc.get("items", []) if x.get("source") != source]
    for item in items:
        kept.append({**item, "source": source})
    doc["items"] = kept
    doc["generated_at"] = _now()
    if chapters_scanned is not None:
        doc["chapters_scanned"] = chapters_scanned
    return _write_counted(path, doc)


# ─── 各 script 节点的具体动作 ─────────────────────────────────────────────────

def _tracking_init(book: Path, st: dict) -> tuple[int, str]:
    """N01：初始化追踪状态（tracking_commit.py init）。

    上游 draft / check / commit 全部要求 追踪/_tracking-state.json 存在，
    没有它 C{n}.N11 第一步就 "tracking state is missing; run init first"。
    init 永不覆盖：已初始化过就跳过。
    """
    cfg = _cfg()
    if (book / "追踪" / "_tracking-state.json").is_file():
        return 0, "tracking state 已存在，跳过 init"
    doc = {
        "schema_version": 1,
        "book_title": st.get("book") or book.name,
        "last_chapter": 0,
        "context": {
            "position": {"volume": "第一卷", "volume_start_chapter": 1,
                         "story_time": "待补充", "scene": "待补充"},
            "long_term_constraints": [],
            "active_character_names": [],
            "continuity_risks": [],
            "recent_chapters": [],
            "next_chapter_commitments": [],
        },
        "character_snapshots": {},
        "foreshadow": [],
        "timeline_events": [],
    }
    inp = book / "_pipeline" / "tracking_init.json"
    _write_json(inp, doc)
    cmd = [cfg["PYTHON"], cfg["TRACKING_COMMIT"], "init",
           "--project", str(book), "--input", str(inp)]
    return run_script(cmd, book)


def _tracking_draft_raw(book: Path, chapter: int) -> tuple[int, str]:
    cfg = _cfg()
    out = book / ".story" / "work" / f"第{chapter:03d}章" / "tracking.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [cfg["PYTHON"], cfg["TRACKING_COMMIT"], "draft",
           "--project", str(book), "--chapter", str(chapter), "--out", str(out)]
    return run_script(cmd, book)


def _tail_chars(text: str, n: int = 600) -> str:
    body = re.sub(r"^#.*$", "", text, flags=re.M).strip()
    return body[-n:] if len(body) > n else body


def _assemble_context(book: Path, chapter: int, st: dict) -> tuple[int, str]:
    """C{n}.N11：组装 追踪/上下文.md（六个 H2 槽 + 事务填写说明）。"""
    cfg = _cfg()
    rc, out = _tracking_draft_raw(book, chapter)
    if rc != 0:
        return rc, out
    draft = extract_json(out) or {}
    if not isinstance(draft, dict) or "shapes" not in draft:
        return 2, f"tracking draft 输出无法解析：{out[-500:]}"

    # S1 场景卡
    outlines = sorted((book / "大纲").glob(f"细纲_第{chapter:03d}章*.md"))
    card = outlines[0].read_text(encoding="utf-8").strip() if outlines else "（缺细纲）"

    # S2 上一章结尾锚点
    anchor = "（本章是第 1 章，无上一章）"
    if chapter > 1:
        try:
            prev_file = find_chapter_file(book, chapter - 1)
            anchor = f"【{prev_file.name} 最后 600 字】\n" + _tail_chars(
                prev_file.read_text(encoding="utf-8"), 600)
        except (FileNotFoundError, RuntimeError) as exc:
            anchor = f"（上一章正文读取失败：{exc}）"

    # S3 主线
    mainline = "（卷纲缺失）"
    volume = book / "大纲" / "卷纲_第一卷.md"
    if volume.is_file():
        lines = [ln.strip() for ln in volume.read_text(encoding="utf-8").splitlines() if ln.strip()]
        picked = [ln for ln in lines if "主线" in ln][:3]
        mainline = "\n".join(picked) if picked else "\n".join(lines[:3])

    # S4 角色当前状态
    snaps = draft.get("current_snapshots") or {}
    snap_lines = []
    for name, snap in snaps.items():
        if not isinstance(snap, dict):
            continue
        snap_lines.append(f"### {name}")
        for key in ("identity", "location", "goal", "state"):
            if snap.get(key):
                snap_lines.append(f"- {key}：{snap[key]}")
        for key, label in (("abilities_resources", "资源"), ("relationships", "关系"),
                           ("knowledge", "认知"), ("open_threads", "未结线索")):
            vals = snap.get(key) or []
            if vals:
                snap_lines.append(f"- {label}：" + "；".join(str(v) for v in vals))
        snap_lines.append("")
    characters = "\n".join(snap_lines).strip() or "（无角色快照）"

    # S5 文风锚
    _pc = platform_cfg(st)
    anchor_doc = _read_json(Path(_pc["STYLE_ANCHOR"]), default={}) or {}
    target = anchor_doc.get("target") or {}
    genre = st.get("genre") or ""
    baseline = genre_baseline(st)
    style_lines = [
        "平台实测区间（并集）：" + json.dumps(
            {k: target.get(k) for k in ("paragraphs_per_1000", "sent_len_median", "dialog_ratio")},
            ensure_ascii=False),
        f"本书题材（{genre or '未指定'}）基准：" + json.dumps(
            {k: baseline.get(k) for k in ("paragraphs_per_1000", "sent_len_median", "dialog_ratio")},
            ensure_ascii=False),
    ]
    samples = anchor_doc.get("samples") or anchor_doc.get("positive_samples") or []
    if samples:
        for i, s in enumerate(samples[:2], 1):
            text = s if isinstance(s, str) else (s.get("text") if isinstance(s, dict) else "")
            if text:
                style_lines.append(f"【正例切片 {i}】\n{text}")
    else:
        style_lines.append("（style-anchor.json 未存原文切片：按上面的指标对齐，不要模仿具体作品句子。）")
    style = "\n".join(style_lines)

    # S6 禁写与约束
    state = _read_json(book / "追踪" / "_tracking-state.json", default={}) or {}
    ctx = state.get("context") or {}
    forbid = [
        "不得新增设定里没有的人物（N20 会按 设定/角色.md 核对）。",
        "不得把细纲、情节点、本章、下一章等工程词写进正文。",
        "不得偏离本章主线；不得提前释放「本章禁止提前释放」的内容。",
    ]
    for key, label in (("long_term_constraints", "长期约束"),
                       ("continuity_risks", "连贯性风险"),
                       ("next_chapter_commitments", "下一章承诺")):
        vals = ctx.get(key) or []
        if vals:
            forbid.append(f"{label}：" + "；".join(str(v) for v in vals))
    position = draft.get("previous_position") or ctx.get("position") or {}
    if position:
        forbid.append("上一章结束时位置：" + json.dumps(position, ensure_ascii=False))

    shapes = draft.get("shapes") or {}
    limits = draft.get("limits_chars") or {}
    doc = f"""# 第 {chapter:03d} 章写作上下文

> 本文件由 C{chapter:03d}.N11 自动生成。writer 必须读它 + 细纲 + tracking.json。

## S1 本章场景卡（细纲原文）
{card}

## S2 上一章结尾原文锚点
{anchor}
（请无缝衔接语气与场景，不要复述这段内容。）

## S3 主线
{mainline}

## S4 角色当前状态
{characters}

## S5 文风锚
{style}

## S6 禁写与连续性约束
""" + "\n".join(f"- {x}" for x in forbid) + f"""

## S7 追踪事务填写说明
- 目标文件：`{draft.get('draft', '.story/work/第%03d章/tracking.json' % chapter)}`
- 当前状态修订号：`{draft.get('expected_state_revision')}`（draft 已按它预填，勿改）
- 填写要求：{draft.get('fill', '')}
- 字段上限：`{json.dumps(limits, ensure_ascii=False)}`
- 字段形状：
```json
{json.dumps(shapes, ensure_ascii=False, indent=1)}
```
"""
    out_path = book / "追踪" / "上下文.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc + "\n", encoding="utf-8")
    return 0, f"上下文已组装 -> {out_path}（{len(doc)} 字符）"


def _outline_gate(book: Path, chapter: int) -> tuple[int, str]:
    """C{n}.N10：细纲九字段结构验收。"""
    cfg = _cfg()
    hits = sorted((book / "大纲").glob(f"细纲_第{chapter:03d}章*.md"))
    if not hits:
        return 2, f"找不到 大纲/细纲_第{chapter:03d}章*.md"
    cmd = [cfg["NODE"], cfg["CHECK_OUTLINE"], str(hits[0])]
    return run_script(cmd, book)


def _story_findings(story: dict) -> list[dict]:
    items: list[dict] = []
    quality = story.get("quality") or {}
    for f in quality.get("blocking_findings") or []:
        items.append({
            "id": f.get("type") or "QUALITY",
            "name": f.get("message") or "正文质量问题",
            "level": "B", "blocking": True, "source": "storyctl",
            "line": f.get("line"), "note": f.get("excerpt") or "",
        })
    length = story.get("length") or {}
    status = length.get("status")
    if status in ("under", "over", "invalid"):
        items.append({
            "id": f"LENGTH_{str(status).upper()}",
            "name": {"under": "字数不足", "over": "字数超限", "invalid": "字数口径无效"}.get(
                str(status), "字数异常"),
            "metric": "visible_chars", "level": "B", "blocking": True,
            "source": "storyctl", "actual": length.get("actual"), "target": length.get("target"),
            "note": f"user_band={length.get('user_band')}",
        })
    return items


def _gate_findings(result: dict) -> tuple[list[dict], dict]:
    items: list[dict] = []
    for f in result.get("findings") or []:
        level = f.get("level", "A")
        items.append({
            "id": f.get("id") or f.get("metric"),
            "name": f.get("name") or f.get("metric"),
            "metric": f.get("metric"),
            "level": level,
            "blocking": level == "B",
            "actual": f.get("actual"),
            "expected": f.get("range") or [f.get("min"), f.get("max")],
            "note": f.get("note") or "",
            "source": "gate",
        })
    return items, result.get("metrics") or {}


def _chapter_check(book: Path, chapter: int, st: dict) -> tuple[int, str]:
    """C{n}.N13：storyctl 质量复检 + 平台闸门，统一写 findings_C{n}.json。

    有阻断项也返回 0：交给 C{n}.N14 定点改写，而不是把整条流水线停死。
    只有工具缺失 / 输出无法解析才返回非 0（硬失败）。
    """
    cfg = _cfg()
    try:
        body = find_chapter_file(book, chapter)
    except (FileNotFoundError, RuntimeError) as exc:
        return 2, str(exc)

    story_cmd = [cfg["PYTHON"], cfg["STORYCTL"], "chapter", "check",
                 "--project", str(book), "--chapter", str(chapter)]
    src, sout, serr = run_script_capture(story_cmd, book, timeout=300)
    story = extract_json(sout)
    if not isinstance(story, dict):
        return 2, f"storyctl check 输出无法解析（rc={src}）：{(sout + serr)[-500:]}"
    status = story.get("status")
    if status in ("invalid", "tool_unavailable", "error"):
        code = story.get("error_code") or status
        return 2, f"storyctl 复检硬失败（{code}）：{story.get('message') or (sout + serr)[-500:]}"
    if status not in ("ready", "blocked", "needs_decision"):
        return 2, f"storyctl 返回未知状态：{status!r}"

    gate_cmd = [cfg["PYTHON"], cfg["GATE_RUNNER"],
                "--platform", platform_cfg(st)["DIR"], "--json"]
    genre = st.get("genre")
    if genre:
        gate_cmd += ["--genre", genre]
    gate_cmd.append(str(body))
    grc, gout, gerr = run_script_capture(gate_cmd, book, timeout=300)
    # 审查 B6：gate_runner 退出码 0=pass/warn、1=block 都是有效结果；其余码
    # 才是工具失败。此前 grc 只进错误文案，阻断全靠 JSON 推断。
    if grc not in (0, 1):
        return 2, f"gate_runner 异常退出（rc={grc}）：{(gout + gerr)[-500:]}"
    gates = extract_json(gout)
    if not isinstance(gates, list) or not gates or "error" in gates[0]:
        return 2, f"gate_runner 输出无法解析（rc={grc}）：{(gout + gerr)[-500:]}"

    items = _story_findings(story)
    if status in ("blocked", "needs_decision") and not items:
        items.append({
            "id": f"STORY_{status.upper()}",
            "name": f"storyctl 判定 {status}",
            "level": "B",
            "blocking": True,
            "source": "storyctl",
            "note": story.get("message") or "复检要求人工/定点处理",
        })
    gate_items, metrics = _gate_findings(gates[0])
    items += gate_items
    doc = write_chapter_findings(book, chapter, items, metrics)
    summary = (f"blocking={doc['blocking_count']} advisory={doc['advisory_count']} "
               f"metrics={json.dumps(metrics, ensure_ascii=False)}")
    return 0, summary


def _commit_chapter(book: Path, chapter: int, st: dict) -> tuple[int, str]:
    """C{n}.N16：原子封板 + 回写 words_total。

    只调 storyctl.py chapter commit——它内部依次做：chapter check 质量复检 →
    构建字数记录 → tracking.apply_transaction 落追踪状态 → 清理本章工作目录。
    注意：不能再另调 tracking_commit.py commit——storyctl 已经内部 apply 过事务，
    双调会「append chapter must be N+1」二次 apply 失败（联试实测）。
    """
    cfg = _cfg()
    state_path = book / "追踪" / "_tracking-state.json"
    previous = _read_json(state_path, default={}) or {}
    try:
        last_committed = int(previous.get("last_committed_chapter") or 0)
    except (TypeError, ValueError):
        last_committed = 0
    if last_committed >= chapter:
        # 崩溃窗口：commit 已落 tracking，但 state.json 还没写回。重跑必须幂等。
        records = previous.get("wordcount_records") or {}
        st["words_total"] = sum(int(v.get("actual") or 0) for v in records.values()
                                if isinstance(v, dict))
        _mark_findings_resolved(book, chapter)
        return 0, f"第{chapter}章已封板（last_committed={last_committed}），跳过重复提交"

    txn = book / ".story" / "work" / f"第{chapter:03d}章" / "tracking.json"
    if not txn.is_file():
        return 2, f"找不到追踪事务 {txn}——writer 必须在 N12 按 shapes 填好它"
    cmd = [cfg["PYTHON"], cfg["STORYCTL"], "chapter", "commit",
           "--project", str(book), "--chapter", str(chapter), "--input", str(txn)]
    rc, out = run_script(cmd, book, timeout=300)
    if rc != 0:
        return rc, out
    state = _read_json(book / "追踪" / "_tracking-state.json", default={}) or {}
    records = state.get("wordcount_records") or {}
    st["words_total"] = sum(int(v.get("actual") or 0) for v in records.values()
                            if isinstance(v, dict))
    _mark_findings_resolved(book, chapter)
    return 0, out + f"\nwords_total={st['words_total']}"


def _mark_findings_resolved(book: Path, chapter: int) -> None:
    """封板成功后给本章 findings 盖上已解决戳，Dashboard 才不会把已修复项当现行问题。"""
    path = book / "_pipeline" / f"findings_C{chapter}.json"
    doc = _read_json(path, default=None)
    if not isinstance(doc, dict):
        return
    doc["resolved_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
    doc["resolved_blocking"] = doc.get("blocking_count", 0)
    _write_json(path, doc)


def _merge_full(book: Path, st: dict) -> tuple[int, str]:
    """N17：合成全文到 投稿包/全文.md，并重置全书 findings（N20f 回跳的入口）。"""
    parts = sorted((book / "正文").glob("*.md"))
    if not parts:
        return 2, "正文/ 下没有章节文件"
    buf = []
    for p in parts:
        buf.append(p.read_text(encoding="utf-8").strip())
    text = "\n\n".join(buf) + "\n"
    if not text.lstrip().startswith("#"):
        text = f"# {st.get('book') or book.name}\n\n" + text
    out = book / "投稿包" / "全文.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    reset_endgame_findings(book, st)
    return 0, f"合并 {len(parts)} 章 -> {out}；已重置 endgame_findings"


def _chapter_no(name: str) -> int | None:
    m = re.match(r"第(\d{3})章", name)
    return int(m.group(1)) if m else None


def _ai_scan(book: Path) -> tuple[int, str]:
    """N18：全书 AI 味 / 退化扫描（只读，不修）。"""
    cfg = _cfg()
    files = sorted((book / "正文").glob("*.md"))
    if not files:
        return 2, "正文/ 下没有章节文件"
    args = [str(p) for p in files]
    items: list[dict] = []
    for tool, script in (("ai-pattern", cfg["AI_PATTERNS_CHECK"]),
                         ("degeneration", cfg["DEGENERATION_CHECK"])):
        rc, out = run_script([cfg["NODE"], script, "--json", "--fail-on=blocking"] + args,
                             book, timeout=300)
        if rc == 2:
            return 2, f"{tool} 扫描失败（rc=2）：{out[-500:]}"
        data = extract_json(out)
        if not isinstance(data, dict) or "findings" not in data:
            return 2, f"{tool} 输出无法解析（rc={rc}）：{out[-500:]}"
        for f in data.get("findings") or []:
            fpath = f.get("file") or ""
            try:
                rel = str(Path(fpath).resolve().relative_to(book))
            except (ValueError, OSError):
                rel = Path(fpath).name
            items.append({
                "tool": tool,
                "severity": f.get("severity", "advisory"),
                "blocking": f.get("severity") == "blocking",
                "chapter": _chapter_no(Path(fpath).name),
                "file": rel,
                "line": f.get("line"),
                "type": f.get("type"),
                "message": f.get("message") or "",
                "excerpt": f.get("excerpt") or "",
            })
    append_endgame_findings(book, items, "N18", chapters_scanned=len(files))
    blocking = sum(1 for x in items if x["blocking"])
    return 0, f"N18 扫描 {len(files)} 章：blocking={blocking} total={len(items)}"


def _load_genre_baselines(book: Path, cfg: dict, genre: str, st: dict | None = None) -> dict:
    """题材基准。走 platform_cfg 以便纵横用 dushi/xuanhuan/lishi。"""
    pc = platform_cfg(st or {})
    doc = _read_json(Path(pc["GENRE_BASE"]), default={}) or {}
    return (doc.get("baselines") or {}).get(genre) or {}


def _style_probe_module(cfg: dict):
    spec = importlib.util.spec_from_file_location("_wb_style_probe", cfg["STYLE_PROBE"])
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None
    return mod


def _style_drift(book: Path, cfg: dict, st: dict, files: list[Path]) -> tuple[list[dict], str, bool]:
    cmd = [cfg["PYTHON"], cfg["GATE_RUNNER"],
           "--platform", platform_cfg(st)["DIR"], "--json"]
    genre = st.get("genre")
    if genre:
        cmd += ["--genre", genre]
    cmd += [str(p) for p in files]
    rc, out, err = run_script_capture(cmd, book, timeout=300)
    if rc not in (0, 1):
        return [], f"style 指标工具异常退出（rc={rc}）：{(out + err)[-300:]}", True
    rows = extract_json(out)
    if not isinstance(rows, list) or not rows:
        return [], f"style 指标不可用（rc={rc}）：{out[-300:]}", True

    items: list[dict] = []
    baseline = _load_genre_baselines(book, cfg, genre or "", st)
    sp = _style_probe_module(cfg)
    dialog_by_name = {}
    if sp is not None:
        for p in files:
            try:
                dialog_by_name[p.name] = sp.probe(str(p))["dialog_ratio"]
            except Exception:
                pass
    prev = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "error" in row:
            return [], f"style 指标有章节读取失败：{row}", True
        name = row.get("file") or ""
        ch = _chapter_no(name)
        m = dict(row.get("metrics") or {})
        if "dialog_ratio" not in m:
            m["dialog_ratio"] = dialog_by_name.get(name)
        para = m.get("paragraphs_per_1000")
        if prev is not None and isinstance(para, (int, float)) and isinstance(prev[1], (int, float)):
            delta = abs(para - prev[1])
            if delta > DRIFT_PARAGRAPH_DELTA:
                items.append({
                    "chapter": ch, "file": f"正文/{name}", "metric": "paragraphs_per_1000",
                    "blocking": False, "actual": para, "previous_chapter": prev[0],
                    "previous": prev[1], "delta": round(delta, 2),
                    "message": f"段/千字相对第{prev[0]}章漂移 {delta:.1f}（阈值 {DRIFT_PARAGRAPH_DELTA}）",
                })
        if isinstance(para, (int, float)):
            prev = (ch, para)
        for metric, label in (("sent_len_median", "句长中位"),
                              ("dialog_ratio", "对话占比")):
            rng = baseline.get(metric)
            val = m.get(metric)
            if isinstance(rng, list) and len(rng) == 2 and isinstance(val, (int, float)):
                if val < rng[0] or val > rng[1]:
                    items.append({
                        "chapter": ch, "file": f"正文/{name}", "metric": metric,
                        "blocking": False, "actual": val, "expected": rng,
                        "message": f"{label} {val} 超出题材基准 {rng}",
                    })
    return items, f"style 扫描 {len(rows)} 章，漂移提示 {len(items)} 条", False


def _foreshadow_items(book: Path, st: dict) -> tuple[list[dict], str]:
    state = _read_json(book / "追踪" / "_tracking-state.json", default={}) or {}
    last = int(state.get("last_committed_chapter") or st.get("chapters_committed") or 0)
    ledger = state.get("foreshadow") or {}
    if not ledger:
        return [], "伏笔账本为空，跳过回收率检查"
    due = []
    for fid, row in ledger.items():
        if not isinstance(row, dict):
            continue
        planned = row.get("planned_resolution_chapter")
        if not isinstance(planned, int) or planned > last:
            continue
        if row.get("status") == "放弃":
            continue
        due.append((fid, row))
    if not due:
        return [], f"截至第{last}章无到期伏笔"
    resolved = [fid for fid, row in due if row.get("status") == "已回收"]
    rate = len(resolved) / len(due)
    if rate >= FORESHADOW_MIN_RATE:
        return [], f"伏笔回收率 {rate:.0%}（{len(resolved)}/{len(due)}）"
    unresolved = [{"id": fid, "status": row.get("status"),
                   "planned": row.get("planned_resolution_chapter")}
                  for fid, row in due if row.get("status") != "已回收"]
    return [{
        "chapter": None,
        "blocking": False,
        "metric": "foreshadow_recovery_rate",
        "actual": round(rate, 3),
        "expected": [FORESHADOW_MIN_RATE, 1.0],
        "message": f"伏笔回收率 {rate:.0%} 低于 {FORESHADOW_MIN_RATE:.0%}",
        "unresolved": unresolved,
    }], f"伏笔回收率 {rate:.0%}（{len(resolved)}/{len(due)}）"


def _endgame_scan(book: Path, st: dict) -> tuple[int, str]:
    """N19：文风漂移 + 伏笔回收率（只读，不修）。"""
    cfg = _cfg()
    files = sorted((book / "正文").glob("*.md"))
    if not files:
        return 2, "正文/ 下没有章节文件"
    drift, drift_msg, drift_error = _style_drift(book, cfg, st, files)
    if drift_error:
        return 2, drift_msg
    fore, fore_msg = _foreshadow_items(book, st)
    append_endgame_findings(book, drift + fore, "N19", chapters_scanned=len(files))
    return 0, f"N19：{drift_msg}；{fore_msg}"


# ─── N21 投稿包 ───────────────────────────────────────────────────────────────

def _mainline(book: Path) -> str:
    volume = book / "大纲" / "卷纲_第一卷.md"
    if not volume.is_file():
        return "（卷纲缺失）"
    for line in volume.read_text(encoding="utf-8").splitlines():
        text = line.strip().lstrip("-*# ").strip()
        if text.startswith("主线") and "：" in text:
            return text.split("：", 1)[1].strip()
        if text.startswith("主线") and ":" in text:
            return text.split(":", 1)[1].strip()
    return "（卷纲里未写「主线：」行）"


def _package(book: Path, st: dict) -> tuple[int, str]:
    """N21：生成 体检报告 / 投稿邮件 / 投稿记录（全文.md 由 N17 产出）。"""
    cfg = _cfg()
    full = book / "投稿包" / "全文.md"
    if not full.is_file():
        return 2, f"缺少 {full}——N17 未成功"
    chapters = sorted((book / "正文").glob("*.md"))
    today = datetime.now()
    stamp = today.strftime("%Y-%m-%d")

    state = _read_json(book / "追踪" / "_tracking-state.json", default={}) or {}
    records = state.get("wordcount_records") or {}
    words_total = sum(int(v.get("actual") or 0) for v in records.values()
                      if isinstance(v, dict))
    committed = int(state.get("last_committed_chapter") or st.get("chapters_committed") or 0)
    _pc = platform_cfg(st)
    genre_doc = _read_json(Path(_pc["GENRE_BASE"]), default={}) or {}
    genre_label = ((genre_doc.get("baselines") or {}).get(st.get("genre") or "") or {}).get(
        "label") or st.get("genre") or "未指定"
    endgame = _read_json(book / "_pipeline" / "endgame_findings.json", default={}) or {}
    judgments = _read_json(book / "_pipeline" / "judgments_N20.json", default={}) or {}
    n20_items = judgments.get("items") or []
    n20_verdict = judgments.get("verdict", "unknown")

    machine = [x for x in endgame.get("items", []) if x.get("source") == "N18"]
    style_advisories = [x for x in endgame.get("items", []) if x.get("source") == "N19"]
    machine_blocking = [x for x in machine if x.get("blocking")]
    b_fail = [x for x in n20_items if x.get("verdict") == "fail"]
    station_ok = words_total >= STATION_MIN_WORDS
    can_submit = (not machine_blocking) and (not b_fail) and n20_verdict == "pass" and station_ok

    # 逐章指标（取不到就写未取到，不阻断打包）
    rows = []
    sp = _style_probe_module(cfg)
    gate_cmd = [cfg["PYTHON"], cfg["GATE_RUNNER"],
                "--platform", platform_cfg(st)["DIR"], "--json"]
    if st.get("genre"):
        gate_cmd += ["--genre", st["genre"]]
    gate_cmd += [str(p) for p in chapters]
    rc, out, err = run_script_capture(gate_cmd, book, timeout=300)
    if rc not in (0, 1):
        out = ""
    parsed = extract_json(out)
    if isinstance(parsed, list):
        for row in parsed:
            if not isinstance(row, dict) or "metrics" not in row:
                continue
            m = dict(row.get("metrics") or {})
            if "dialog_ratio" not in m and sp is not None:
                try:
                    m["dialog_ratio"] = sp.probe(str(book / "正文" / row.get("file", "")))["dialog_ratio"]
                except Exception:
                    pass
            rows.append((row.get("file", ""), row.get("status", ""), m))

    def flag(v: bool) -> str:
        return "通过" if v else "**不通过**"

    lines = [
        f"# 投稿体检报告 · {st.get('book') or book.name}",
        "",
        f"- 生成时间：{stamp}",
        f"- 题材：{genre_label}",
        f"- 章节数：{len(chapters)}（已封板 {committed}）",
        f"- 正文字数：{words_total} 字（七猫/奇妙门槛 20000）",
        f"- 全文文件：投稿包/全文.md",
        f"- 终审结论：{n20_verdict}",
        "",
        "## 一、硬门槛",
        "",
        f"- [{ 'x' if station_ok else ' ' }] 正文总量 ≥{STATION_MIN_WORDS} 字：{words_total}",
        f"- [{'x' if chapters else ' '}] 每章有正文文件：{len(chapters)} 章",
        f"- [x] 正文整合为单文件：投稿包/全文.md",
        "",
        "## 二、AI 味 / 退化（N18 机械扫描）",
        "",
        f"- blocking：{len(machine_blocking)}",
        f"- 全部 findings：{len(machine)}",
    ]
    if machine:
        lines += ["", "| 章 | 文件 | 类型 | 级别 | 说明 |", "|---|---|---|---|---|"]
        for x in machine[:80]:
            lines.append(f"| {x.get('chapter') or '-'} | {x.get('file') or '-'} | "
                         f"{x.get('type') or '-'} | {x.get('severity') or '-'} | "
                         f"{(x.get('message') or '').replace('|', '/')[:80]} |")
    lines += ["", "## 三、评分卡终审（N20 judge）", ""]
    if n20_items:
        lines += ["| # | 判定项 | 结果 | 依据 |", "|---|---|---|---|"]
        for x in n20_items:
            quote = (x.get("quote") or "").replace("|", "/")[:40]
            metric = x.get("metric")
            basis = quote or (json.dumps(metric, ensure_ascii=False) if metric else "")
            lines.append(f"| {x.get('id') or '-'} | {x.get('criterion') or '-'} | "
                         f"{x.get('verdict') or '-'} | {basis} |")
    else:
        lines.append("（未找到 judgments_N20.json）")
    if b_fail:
        lines += ["", "**B 项未通过：**"]
        lines += [f"- {x.get('id')} {x.get('criterion')}：{(x.get('fix') or '')[:200]}"
                  for x in b_fail]
    lines += ["", "## 四、文风与节奏（逐章）", ""]
    if rows:
        lines += ["| 章节 | 闸门 | 段/千字 | 句长中位 | 对话占比 | 因果/千字 | 动作密度 |",
                  "|---|---|---|---|---|---|---|"]
        for name, status, m in rows:
            lines.append(f"| {name} | {status} | {m.get('paragraphs_per_1000')} | "
                         f"{m.get('sent_len_median')} | {m.get('dialog_ratio')} | "
                         f"{m.get('causal_per_1000')} | {m.get('narration_action_density')} |")
    else:
        lines.append("（闸门指标未取到）")
    if style_advisories:
        lines += ["", "**提示项（A 级，不阻断）：**"]
        lines += [f"- 第{x.get('chapter') or '-'}章：{x.get('message')}" for x in style_advisories]
    lines += [
        "",
        "## 五、结论",
        "",
        f"**{'可投' if can_submit else '不可投'}**",
        "",
        ("机械 blocking 为 0、N20 无 B 项 fail、字数达标。" if can_submit
         else "存在未通过项，按上表修完再投。"),
        "",
        "> 本书只有一次签约评估机会。投前请再人工确认：开篇 200 字冲突、"
        "前 600 字升级、前 6000 字主线、三章内反转、只联系一个编辑。",
    ]
    (book / "投稿包" / "体检报告.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    full_hash = hashlib.sha256(full.read_bytes()).hexdigest()[:12]
    title = st.get("book") or book.name
    genre = genre_label
    mainline = _mainline(book)
    email = f"""# 投稿邮件（草稿）

**收件人：** （填对口编辑邮箱）
**主题：** 投稿｜{title}｜{genre}｜约 {max(words_total, 0)} 字

---

尊敬的编辑老师：

您好！向您投稿一部{genre}题材长篇《{title.replace('《', '').replace('》', '')}》，已完成前 {len(chapters)} 章，约 {words_total} 字。

**一句话主线：** {mainline}

随信附上：
1. 正文（整合为一个文件，不分章拆发）：`投稿包/全文.md`
2. 大纲：`大纲/卷纲_第一卷.md`、`大纲/目录_前12章.md`
3. 设定：`设定/` 下五份文件
4. 投稿申请表：（按七猫后台/编辑要求填写后附上）

期待您的意见，感谢审阅。

此致
敬礼

（作者署名）
（联系方式）
（日期：{stamp}）

---

> 发送前确认：① 文档没有拆成多封邮件；② 只联系一个编辑；③ 这本书还没用过唯一一次评估机会。
"""
    (book / "投稿包" / "投稿邮件.md").write_text(email, encoding="utf-8")

    def check(ok: bool) -> str:
        return "[x]" if ok else "[ ]"

    record = f"""# 投稿记录

## 一、版本

| 项 | 值 |
|---|---|
| 书名 | {title} |
| 题材 | {genre} |
| 正文字数 | {words_total} |
| 章节数 | {len(chapters)} |
| 全文 SHA256（前12位） | `{full_hash}` |
| 打包日期 | {stamp} |
| 终审结论 | {n20_verdict} |
| 体检结论 | {'可投' if can_submit else '不可投'} |

## 二、投前检查（自动项）

- {check(station_ok)} 字数 ≥{STATION_MIN_WORDS}（当前 {words_total}）
- {check(not machine_blocking)} AI 味 / 退化 blocking = 0（当前 {len(machine_blocking)}）
- {check(n20_verdict == 'pass' and not b_fail)} 评分卡 B 项全部通过
- {check(full.is_file())} 正文整合为单文件

## 三、投前检查（人工项）

- [ ] 前 200 字有冲突
- [ ] 前 600 字冲突升级
- [ ] 前 6000 字主线一句话说清
- [ ] 三章内至少一次反转
- [ ] 单章场景切换 ≤3 次，切换处有过渡
- [ ] 未出现设定外人物
- [ ] 合规：无涉政涉军 / 低俗 / 暴力 / 侵权；非外站已发布、非抄袭

## 四、投稿信息（提交后填）

| 项 | 值 |
|---|---|
| 投稿日期 | （待填） |
| 投稿方式 | 直投 / 内投（待填） |
| 站点 | 七猫 / 奇妙 / 纵横（待填） |
| 编辑 | （待填，只填一位） |
| 状态 | 待投稿 |
| 预计反馈 | 3–5 个工作日（双休与法定节假日不计） |
| 实际反馈日期 | （待填） |
| 编辑意见 | （待填） |
| 签约结果 | （待填：签约 / 修改后再投 / 退稿） |

> 反馈到期未回：按同一封邮件礼貌追问一次，不要同时投第二个编辑。
> 结果出来后回填本表——这是下一本书唯一的真实校准依据。
"""
    (book / "投稿包" / "投稿记录.md").write_text(record, encoding="utf-8")

    if not can_submit:
        return 1, json.dumps({
            "status": "error",
            "error_code": "NOT_SUBMITTABLE",
            "message": f"不可投：字数 {words_total}/{STATION_MIN_WORDS}，"
                       f"机械 blocking {len(machine_blocking)}，B fail {len(b_fail)}，"
                       f"N20 verdict={n20_verdict}。体检报告已写入。",
        }, ensure_ascii=False)
    return 0, (f"投稿包已生成：全文.md / 体检报告.md / 投稿邮件.md / 投稿记录.md；"
               f"体检=可投；字数={words_total}")


# ─── 快照 ─────────────────────────────────────────────────────────────────────
def snapshot_chapter(book: Path, chapter: int, tag: str) -> None:
    dest = book / "_pipeline" / "snapshots" / tag
    dest.mkdir(parents=True, exist_ok=True)
    try:
        body = find_chapter_file(book, chapter)
        shutil.copy2(body, dest / body.name)
    except (FileNotFoundError, RuntimeError):
        pass
    txn = book / ".story" / "work" / f"第{chapter:03d}章" / "tracking.json"
    if txn.is_file():
        shutil.copy2(txn, dest / "tracking.json")


def record_snapshot_manifest(book: Path, chapter: int, tag: str) -> None:
    """进 N14 前记录当时本章 blocking 数，供终停时选 Blocker 最少的快照（B3）。"""
    path = book / "_pipeline" / "snapshots" / f"manifest_C{chapter}.json"
    doc = _read_json(path, default={}) or {}
    findings = _read_json(book / "_pipeline" / f"findings_C{chapter}.json", default={}) or {}
    doc[tag] = {"blocking_count": int(findings.get("blocking_count") or 0), "at": _now()}
    _write_json(path, doc)


def restore_best_snapshot(book: Path, chapter: int) -> str | None:
    """审查 B3 / plan 补丁4：同处失败 2 次后回滚到 Blocker 数量最少的快照。

    候选 blocking 数在进 N14 前记录于 manifest_C{n}.json；并列取最早 tag。
    没有快照返回 None（调用方按无回滚处理）。
    """
    snap_dir = book / "_pipeline" / "snapshots"
    manifest = _read_json(snap_dir / f"manifest_C{chapter}.json", default={}) or {}
    candidates = []
    for tag in sorted(manifest):
        info = manifest[tag]
        if not isinstance(info, dict):
            continue
        src = snap_dir / tag
        if not src.is_dir():
            continue
        candidates.append((int(info.get("blocking_count") or 0), tag, src))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]))
    _, tag, src = candidates[0]
    for p in sorted(src.glob("第*.md")):
        shutil.copy2(p, book / "正文" / p.name)
    txn_src = src / "tracking.json"
    if txn_src.is_file():
        dest = book / ".story" / "work" / f"第{chapter:03d}章" / "tracking.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(txn_src, dest)
    _write_json(book / "_pipeline" / "rollback_log.json", {
        "chapter": chapter, "restored": tag, "at": _now(),
        "candidates": [{"tag": t, "blocking_count": c} for c, t, _ in candidates],
    })
    return tag


def snapshot_body(book: Path, tag: str) -> None:
    dest = book / "_pipeline" / "snapshots" / tag
    dest.mkdir(parents=True, exist_ok=True)
    for p in sorted((book / "正文").glob("*.md")):
        shutil.copy2(p, dest / p.name)


# ─── 节点 → 动作 分发表 ───────────────────────────────────────────────────────

def _chapter_files_in(book: Path, start: int, end: int) -> list[Path]:
    """取第 start–end 章的文件（按章号前缀 第NNN章 匹配）。"""
    want = {f"{n:03d}" for n in range(start, end + 1)}
    out = []
    for f in sorted((book / "正文").glob("*.md")):
        m = re.match(r"第(\d{3})章", f.name)
        if m and m.group(1) in want:
            out.append(f)
    return out


def _merge_volume(book: Path, st: dict, v: int) -> tuple[int, str]:
    """V{v}.N17：只合本卷，单卷 ≤6 万字。25 万字整本合成会超出 judge 上下文。"""
    start, end = span(st, v)
    parts = _chapter_files_in(book, start, end)
    if not parts:
        return 2, f"第{v}卷（第 {start}–{end} 章）没有章节文件"
    text = "\n\n".join(p.read_text(encoding="utf-8").strip() for p in parts) + "\n"
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    out = book / "投稿包" / f"卷{v}_全文.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    warn = "" if cjk <= 60000 else f"  ⚠ 超 6 万字（{cjk}），N20 可能吃紧"
    return 0, f"第{v}卷合成 {len(parts)} 章 / {cjk} 汉字 -> {out.name}{warn}"


def _merge_global(book: Path, st: dict) -> tuple[int, str]:
    """N17g：合全书 全文.md + 全书概要.md。

    概要给 N20g 读——N20g **不许读正文**（25 万字 ≈ 20 万 token 必爆）。
    """
    parts = sorted((book / "正文").glob("*.md"))
    if not parts:
        return 2, "正文/ 下没有章节文件"
    full = "\n\n".join(p.read_text(encoding="utf-8").strip() for p in parts) + "\n"
    out = book / "投稿包" / "全文.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(full, encoding="utf-8")

    # 概要：每章取标题 + 首段（各 ≤120 字），加卷统计
    lines = [f"# {st.get('book') or book.name} · 全书概要", ""]
    for v, s, e in volumes(st):
        chs = _chapter_files_in(book, s, e)
        wc = sum(len(re.findall(r"[\u4e00-\u9fff]", f.read_text(encoding="utf-8"))) for f in chs)
        lines.append(f"## 第{v}卷（第 {s}–{e} 章，{len(chs)} 章，{wc} 字）")
        for f in chs:
            body = [x.strip() for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
            title = body[0].lstrip("# ").strip() if body else f.stem
            head = body[1][:120] if len(body) > 1 else ""
            lines.append(f"- {title}：{head}")
        lines.append("")
    summary = book / "投稿包" / "全书概要.md"
    summary.write_text("\n".join(lines), encoding="utf-8")

    total = len(re.findall(r"[\u4e00-\u9fff]", full))
    return 0, f"全书合成 {len(parts)} 章 / {total} 汉字；概要 {summary.name}"


def run_node_script(book: Path, st: dict, spec: dict) -> tuple[int, str]:
    nid = spec["id"]
    ch = st["current_chapter"] + 1
    if nid == "N01":
        return _tracking_init(book, st)
    if nid.endswith(".N10"):
        return _outline_gate(book, ch)
    if nid.endswith(".N11"):
        return _assemble_context(book, ch, st)
    if nid.endswith(".N13"):
        return _chapter_check(book, ch, st)
    if nid.endswith(".N16"):
        return _commit_chapter(book, ch, st)
    if nid == "N17":
        return _merge_full(book, st)
    if nid == "N17g":
        return _merge_global(book, st)
    if nid.startswith("V") and nid.endswith(".N17"):
        return _merge_volume(book, st, int(nid[1:].split(".", 1)[0]))
    if nid.startswith("V") and nid.endswith((".N18", ".N19")):
        # 卷级扫描复用全书扫描，只按卷范围取文件——先按全书跑，口径一致
        return _ai_scan(book) if nid.endswith(".N18") else _endgame_scan(book, st)
    if nid == "N18":
        return _ai_scan(book)
    if nid == "N19":
        return _endgame_scan(book, st)
    if nid == "N21":
        return _package(book, st)
    if nid == "N02":
        # 诚实化（C 级）：N02 不创作文风锚，只校验上游 style-anchor.json 的
        # 校准状态；缺失时按 plan 5.1 的失败处置标 provisional 续跑。
        anchor = Path(platform_cfg(st)["STYLE_ANCHOR"])
        if anchor.is_file():
            doc = _read_json(anchor, default={}) or {}
            samples = len(doc.get("samples") or doc.get("positive_samples") or [])
            return 0, f"style anchor 已校准：{anchor}（正例切片 {samples} 段）"
        return 0, "style anchor 缺失，文风锚标记为 provisional（按 plan 5.1 续跑）"
    # 未实现的 script 节点必须硬失败：静默放行会让「确定性闸门」形同虚设
    return 2, f"{nid} 未实现内联动作——拒绝放行，请先实现再跑"


# ─── verify 节点的原文引用硬校验（暗坑6） ─────────────────────────────────────

def verify_quote(quote: str, chapter_text: str, min_len: int = 12) -> bool:
    """原文引用硬校验（暗坑6）。min_len=12 与 judge prompt 的"连续 12–30 字"对齐
    （审查 C 级：此前代码只卡 ≥8，比提示词松）。"""
    q, t = _norm(quote), _norm(chapter_text)
    return len(q) >= min_len and q in t


def _norm(s: str) -> str:
    return re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", s or "")
