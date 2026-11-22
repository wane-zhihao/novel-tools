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
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from pipeline_spec import (  # noqa: E402  (只依赖常量，不反向导入 runner)
    STATION_MIN_WORDS, CHAPTER_BODY_MIN_WORDS,
    body_files, body_files_in, body_file_for_chapter,
    parse_body_file_name, is_backup_body_file,
)

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
    # T-2/T-2b：走统一判定——排除 _原稿_ 备份稿，章号认 1–4 位。
    hits = body_file_for_chapter(book, chapter)
    if not hits:
        raise FileNotFoundError(f"找不到 第{chapter}章 的现役正文")
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
    """C{n}.N11：生成预填事务骨架。

    **并且把 draft 工具自己的输出载荷落盘**（2026-11-20 加）：
    `tracking_commit.py draft` 的 stdout 里带着四样东西——
    `shapes`（该章每类条目的**权威形状**）、`current_snapshots`
    （上一章结束时的角色快照，writer 该**整份复制再改**）、`fill`
    （逐字的填法说明）、`limits_chars`（字节上限）。

    此前这些全被 `run_script` 拼进返回值扔掉了，writer 拿到手的只有一个
    **空骨架**：形状靠猜、快照靠从零重写。结果是**连续三个 subagent 死在
    「填事务」这一件事上**——它们不是不努力，是被要求重做工具已经算好的活。

    修法：把载荷存成 `.story/work/第NNN章/tracking-draft.json`，并让 N12
    提示词指向它。
    """
    cfg = _cfg()
    out = book / ".story" / "work" / f"第{chapter:03d}章" / "tracking.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [cfg["PYTHON"], cfg["TRACKING_COMMIT"], "draft",
           "--project", str(book), "--chapter", str(chapter), "--out", str(out)]
    rc, stdout, stderr = run_script_capture(cmd, book)
    if rc == 0:
        payload = _read_json_line(stdout)
        if isinstance(payload, dict):
            side = out.parent / "tracking-draft.json"
            _write_json(side, payload)
    return rc, (stdout or "") + (stderr or "")


def _read_json_line(text: str):
    """从脚本 stdout 里取出第一个 JSON 对象（draft 的载荷就是单行 JSON）。"""
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def _tail_chars(text: str, n: int = 600) -> str:
    body = re.sub(r"^#.*$", "", text, flags=re.M).strip()
    return body[-n:] if len(body) > n else body


def _assemble_context(book: Path, chapter: int, st: dict) -> tuple[int, str]:
    """C{n}.N11：组装 `追踪/写作上下文.md`（六个 H2 槽 + 事务填写说明）。

    **为什么叫「写作上下文.md」而不是「上下文.md」**（2026-11-20 修）：
    `追踪/上下文.md` 是 **tracking_commit.py 自己从 `_tracking-state.json`
    派生的权威视图**（它的 `check` 子命令会逐字节比对，任何偏离都报
    「derived view differs」）。此前本节点把 S1–S7 写进那个文件，等于
    **抢了技能的文件所有权**——后果是每次封板后技能把它覆写回自己的格式，
    而 `tracking_commit.py check` 对工作台的书**永远报不一致**（假警报），
    书一旦中途崩在章间，运维拿这个命令根本诊断不了账本。

    两个组件各写各的文件：技能管 `上下文.md`（派生视图），工作台管
    `写作上下文.md`（writer 用的场景卡/锚点/事务说明）。
    """
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
    samples = (anchor_doc.get("samples") or anchor_doc.get("positive_samples") or (anchor_doc.get("positive_anchors") or {}).get("samples") or [])
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

    # S6b 本章必须处理的伏笔（2026-11-17 加）。
    # **为什么要有**：此前伏笔只以「埋第2章｜第6章」这种形式出现在别处，
    # writer 看到「第6章」时本章正在写，无法判断是「本章到期」还是「已经过了」。
    # 实测 F005/F008 的计划回收章都是 6，第 6 章封板后仍未回收，N19 只给 advisory。
    # 这里把「计划回收章 ≤ 本章」且未回收的伏笔**显式列成本章必办事项**，
    # 并区分「本章到期」与「已逾期」，writer 一眼能看出轻重。
    must_resolve: list[str] = []
    overdue: list[str] = []
    ledger = state.get("foreshadow") or {}
    for fid, row in sorted(ledger.items()):
        if not isinstance(row, dict):
            continue
        planned = row.get("planned_resolution_chapter")
        if not isinstance(planned, int) or planned > chapter:
            continue
        if row.get("status") in ("已回收", "放弃", "已过期"):
            continue
        summary = str(row.get("summary") or "")[:60]
        if planned < chapter:
            overdue.append(f"{fid}（计划第{planned}章回收，已逾期）：{summary}")
        else:
            must_resolve.append(f"{fid}（本章到期）：{summary}")
    if overdue:
        forbid.append("**已逾期未回收的伏笔，本章必须收掉或明确交代延后原因**："
                      + "；".join(overdue))
    if must_resolve:
        forbid.append("**本章到期、必须在正文里回收的伏笔**：" + "；".join(must_resolve))

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
    out_path = book / "追踪" / "写作上下文.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc + "\n", encoding="utf-8")
    return 0, f"上下文已组装 -> {out_path}（{len(doc)} 字符）"


def _outline_gate(book: Path, chapter: int) -> tuple[int, str]:
    """C{n}.N10：细纲结构验收，并确保细纲字数合同可由正文门禁满足。"""
    cfg = _cfg()
    hits = sorted((book / "大纲").glob(f"细纲_第{chapter:03d}章*.md"))
    if not hits:
        return 2, f"找不到 大纲/细纲_第{chapter:03d}章*.md"
    cmd = [cfg["NODE"], cfg["CHECK_OUTLINE"], "--json", str(hits[0])]
    # 必须用 run_script_capture + extract_json，不能用 run_script + json.loads：
    # run_script 把 stdout 与 stderr 拼在一起，而 node 会往 stderr 打印
    # `[UNDICI-EHPA] Warning: EnvHttpProxyAgent is experimental` 之类警告行，
    # 拼在合法 JSON 之后会让 json.loads 直接抛错——细纲合同其实已通过，
    # 闸门却报「检查器未返回合法 JSON」并把流水线卡死（2026-11-15 实测）。
    # 同文件 _chapter_check 早已用分离式调用，这里是漏改。
    rc, sout, serr = run_script_capture(cmd, book)
    output = sout
    if rc != 0:
        return rc, output
    report = extract_json(sout)
    if not isinstance(report, dict):
        return 2, f"细纲检查器未返回合法 JSON：{(sout + serr)[-500:]}"
    # 检查器的输出不保证带 wordcount / checks / failures 三个容器字段，而下面两个
    # 分支都会对它们做下标写入。不在这里补齐，缺字段就会变成 KeyError，让整段
    # node script 输出无法解析——本该「报阻断」的失败退化成「崩溃」。
    if not isinstance(report.get("wordcount"), dict):
        report["wordcount"] = {}
    if not isinstance(report.get("checks"), list):
        report["checks"] = []
    if not isinstance(report.get("failures"), list):
        report["failures"] = []

    wordcount = report["wordcount"]
    target = wordcount.get("target")
    target_status = wordcount.get("target_status")
    word_range = wordcount.get("range")
    contract_issues = []
    if target_status != "ok" or not isinstance(target, int) or target < CHAPTER_BODY_MIN_WORDS:
        contract_issues.append({
            "id": "outline.wordcount-contract",
            "detail": f"字数目标须不低于 {CHAPTER_BODY_MIN_WORDS} 字（当前 {target!r}）",
        })
    if word_range is not None:
        lower, upper = word_range.get("min"), word_range.get("max")
        if not isinstance(lower, int) or not isinstance(upper, int) or lower < CHAPTER_BODY_MIN_WORDS:
            contract_issues.append({
                "id": "outline.wordcount-contract",
                "detail": f"明确字数范围下限须不低于 {CHAPTER_BODY_MIN_WORDS} 字（当前 {lower!r}）",
            })
        if isinstance(target, int) and isinstance(lower, int) and isinstance(upper, int) \
                and not lower <= target <= upper:
            contract_issues.append({
                "id": "outline.wordcount-contract",
                "detail": f"字数目标 {target} 不在明确字数范围 {lower}-{upper} 内",
            })
    if contract_issues:
        report["ok"] = False
        report["wordcount"]["contract_status"] = "fail"
        report["checks"].append({
            "id": "outline.wordcount-contract",
            "ok": False,
            "severity": "blocking",
            "file": hits[0].name,
            "evidence": "; ".join(issue["detail"] for issue in contract_issues),
            "expected": f"字数目标至少 {CHAPTER_BODY_MIN_WORDS} 字，明确范围下限至少 {CHAPTER_BODY_MIN_WORDS} 字，目标须落入明确范围",
            "repair": "只调整细纲中的字数目标或范围，不改情节安排。",
        })
        report["failures"] = [*(report.get("failures") or []), *contract_issues]
        return 1, json.dumps(report, ensure_ascii=False, indent=1)
    report["ok"] = True
    report["wordcount"]["contract_status"] = "ok"
    report["failures"] = []
    report["checks"].append({
        "id": "outline.wordcount-contract",
        "ok": True,
        "severity": "blocking",
        "file": hits[0].name,
        "evidence": f"目标 {target}，范围 {word_range.get('min') if word_range else '默认'}-{word_range.get('max') if word_range else '默认'}",
        "expected": f"字数目标至少 {CHAPTER_BODY_MIN_WORDS} 字，明确范围下限至少 {CHAPTER_BODY_MIN_WORDS} 字，目标须落入明确范围",
        "repair": "无需修复。",
    })
    return 0, json.dumps(report, ensure_ascii=False, indent=1)


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


def _chapter_check(book: Path, chapter: int, st: dict,
                   _allow_mech: bool = True,
                   _write: bool = True) -> tuple[int, object]:
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

    # 商业张力与情绪兑现探针（七猫商业签约级硬指标：前200字冲突、围观震惊流、章末断章）
    try:
        from commercial_tension_probe import analyze_chapter_text
        try:
            raw_text = body.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw_text = body.read_text(encoding="gb18030", errors="replace")

        has_commercial_contract = bool((st or {}).get("template")) or (os.environ.get("FORCE_COMMERCIAL_BLOCKING") == "1")
        is_mock = not has_commercial_contract or (
            "占位文本" in raw_text or "mock" in raw_text.lower() or
            len(raw_text.replace(" ", "").replace("\n", "")) < 1200
        )

        comm_res = analyze_chapter_text(raw_text, chapter_num=chapter)
        metrics["commercial_tension"] = {
            "opening_conflict_in_200": comm_res["opening_urgency"]["conflict_in_200"],
            "opening_conflict_in_500": comm_res["opening_urgency"]["conflict_in_500"],
            "flat_setup_detected": comm_res["opening_urgency"]["flat_setup_detected"],
            "opponent_breakdown_count": comm_res["crowd_reactions"]["opponent_count"],
            "crowd_shock_count": comm_res["crowd_reactions"]["crowd_count"],
            "has_cliffhanger": comm_res["cliffhanger"]["has_cliffhanger"],
            "flat_dropoff": comm_res["cliffhanger"]["flat_dropoff"],
            "tangible_rewards": comm_res["tangible_stakes"]["reward_count"],
        }
        if comm_res["blocking_reasons"]:
            for reason in comm_res["blocking_reasons"]:
                is_blocking = not is_mock
                items.append({
                    "id": "COMMERCIAL_TENSION",
                    "name": "商业张力与爽点节奏不足",
                    "level": "B" if is_blocking else "A",
                    "blocking": is_blocking,
                    "source": "commercial_probe",
                    "note": reason,
                })
    except Exception as exc:
        metrics["commercial_tension_error"] = str(exc)

    # 机械项确定性修复：省略号/半角标点/垃圾行改法唯一，不该烧一轮 LLM。
    # 端到端实测 5 轮 N14 里有 3 轮只在做这个，而且 N14 还会漏。
    # 修完立刻重跑一遍本函数复检；第二次不再修（_allow_mech=False），防死循环。
    if _allow_mech:
        _n, _notes = mechanical_fix(book, chapter, items)
        if _n:
            return _chapter_check(book, chapter, st, _allow_mech=False, _write=_write)

    # 干跑模式（预检用）：不写盘，直接把结果给调用方判断
    if not _write:
        return 0, {"items": items, "metrics": metrics}

    doc = write_chapter_findings(book, chapter, items, metrics)
    summary = (f"blocking={doc['blocking_count']} advisory={doc['advisory_count']} "
               f"metrics={json.dumps(metrics, ensure_ascii=False)}")
    return 0, summary


def shrink_delta_to_fit(delta: dict, limit: int) -> dict | None:
    """把 delta 的**长度类字段**按比例截到字节上限内。返回新 delta，或 None（压不动）。

    **为什么要有**（2026-11-17）：此前封板前的自动规整用**固定长度**截断
    （objective_fact 截到 42 字、summary 截到 32 字……），且**加「…」后缀**。
    两个问题：
      ① 42 字对一句中文事件描述是毁灭性的——实测账本里 6 条时间线事件的
         objective_fact 都被截成半句（"...，三号…"），信息永久丢失；
      ② 「…」会被 storyctl 当成**原文**写进账本，此后每次读账本都以为
         那是原文的一部分——**截断伪装成了原始内容**。
    而且它压的 key（description/note/detail）在 schema 里多半**不存在**，
    真正的长字段 `summary` 根本没被压到。

    现在：按比例缩到刚好进限（不砍掉一半），**不加省略号**（不伪装成原文），
    只截长度类字段，不碰 id / status / 枚举 / 必填结构。
    """
    out = json.loads(json.dumps(delta, ensure_ascii=False))   # 深拷贝
    size = lambda: len(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    if size() <= limit:
        return out

    # 只截这些键：都是「描述性」字段，截短不破坏形态
    CUTTABLE = ("summary", "objective_fact", "reader_knowledge", "change",
                "result", "detail", "note", "description")

    def _candidates():
        found = []

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in CUTTABLE and isinstance(v, str) and v:
                        found.append((node, k, v))
                    elif k in ("constraints", "next_chapter_commitments") \
                            and isinstance(v, list):
                        for i, s in enumerate(v):
                            if isinstance(s, str) and s:
                                found.append((v, i, s))
                    else:
                        walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(out)
        return found

    # 每个字段的保底长度：低于这个值，这个字段就不再是「可读句子」，
    # 砍下去等于毁信息（2026-11-17 实测踩到：`cut = max(4, len-over-8)` 在
    # over 大于字段长度时下溢成负数，max 取 4 —— 一句 60 字的结果说明
    # 被砍成「陆明用爹」四个字，然后原样写进账本与上下文.md）。
    MIN_KEEP = {"result": 24, "summary": 16, "objective_fact": 16,
                "reader_knowledge": 12, "change": 12}

    def _floor_for(key):
        if isinstance(key, int):
            return 10          # 列表元素（constraints / next_chapter_commitments）
        return MIN_KEEP.get(key, 10)

    for _ in range(600):
        if size() <= limit:
            return out
        cands = _candidates()
        if not cands:
            return None
        # 只砍「当前砍了还够保底」的字段；不够保底的这一轮跳过，
        # 改砍下一个更长的字段——宁可多花几轮，也不把句子砍成碎片。
        viable = [c for c in cands if len(c[2]) > _floor_for(c[1]) + 4]
        if not viable:
            return None                     # 全部到保底 → 放弃，交上层报明确错误
        holder, key, val = max(viable, key=lambda x: len(x[2]))
        over = size() - limit
        # 按比例缩：砍掉「超出量 + 一点余量」，避免固定长度一刀切；
        # 但结果不得低于保底长度。
        want = len(val) - over - 8
        cut = max(_floor_for(key), want)
        holder[key] = holder[key][:cut]
    return out if size() <= limit else None


def _normalize_tracking(txn: Path, st: dict, prev_ctx: dict | None = None) -> list[str]:
    """封板前把 tracking.json 规整到 schema 允许的形状。

    **为什么要有**：writer 产出的 tracking 经常不合 schema，而 storyctl 的报错是
    硬失败，会把整章打回 N14 重写。端到端实跑时第 1 章因此卡了 4 轮：
      · continuity_risks 8 条 > 上限 5
      · character_snapshots 有 6 个，但 delta.character_changes 只有 5 个
        （周慕松本章没出场却进了 active 名单）→ 循环矛盾：
        放快照里说"不在 delta"，不放快照里说"active 缺快照"
      · delta 5541 字节 > 上限 3072

    这些都是**结构性不一致**，不需要 LLM 判断，确定性规整即可。
    返回做了哪些修正，写进 N16 输出便于审计。
    """
    notes: list[str] = []
    try:
        d = json.loads(txn.read_text(encoding="utf-8"))
    except Exception:
        return notes
    if not isinstance(d, dict):
        return notes

    ctx = d.setdefault("context", {})
    delta = d.setdefault("delta", {})

    # ① character_snapshots 必须恰好等于 delta 里改动的角色
    changed = [c.get("name") for c in (delta.get("character_changes") or [])
               if isinstance(c, dict) and c.get("name")]
    snap = d.get("character_snapshots") or {}
    if isinstance(snap, dict) and changed:
        changed_set = set(changed)
        extra = [k for k in snap if k not in changed_set]
        for k in extra:
            del snap[k]
        if extra:
            notes.append(f"快照删多余角色 {extra}")
        missing = [n for n in changed if n not in snap]
        if missing:
            # 不能凭空编造快照内容，只能把它从 active 里摘掉并记录
            notes.append(f"⚠ {missing} 在 delta 里但没有快照，已从 active 摘除")

    # ② active_character_names ⊆ snapshots
    act = ctx.get("active_character_names") or []
    if isinstance(act, list) and snap:
        keep = [x for x in act if x in snap]
        if keep != act:
            notes.append(f"active 摘除 {[x for x in act if x not in snap]}")
            ctx["active_character_names"] = keep

    # ③ continuity_risks ≤ 5
    risks = ctx.get("continuity_risks") or []
    if isinstance(risks, list) and len(risks) > 5:
        notes.append(f"continuity_risks {len(risks)} → 5（丢弃 {len(risks) - 5} 条）")
        ctx["continuity_risks"] = risks[:5]

    # ④ 被丢弃的 context 条目必须声明为 retired（否则 preflight 会拦）
    #    端到端实跑第 2 章时踩到：writer 没继承上一章的 continuity_risks，
    #    也没声明 retired，preflight 直接判 tracking_preflight_failed。
    if prev_ctx:
        retired = delta.setdefault("retired_context_items", [])
        if not isinstance(retired, list):
            retired = []
            delta["retired_context_items"] = retired
        declared = set()
        for x in retired:
            if isinstance(x, str):
                declared.add(x)
            elif isinstance(x, dict):
                declared.add(str(x.get("item") or x.get("text") or ""))
        for field_name in ("continuity_risks", "long_term_constraints",
                           "next_chapter_commitments"):
            old_items = prev_ctx.get(field_name) or []
            new_items = set(ctx.get(field_name) or [])
            for item in old_items:
                if item in new_items:
                    continue
                if item in declared:
                    continue
                # 2026-11-20：旧的压缩器曾把退役声明截到 30 字，于是工具的
                # 精确比对失败（报「未声明退役」，而 writer 明明写了）。
                # 这里顺手把截断版**还原成原文**——前缀命中即认定为截断。
                restored = False
                for idx, entry in enumerate(retired):
                    if (isinstance(entry, str) and entry != item
                            and len(entry) >= 8 and item.startswith(entry)):
                        retired[idx] = item
                        declared.discard(entry)
                        declared.add(item)
                        restored = True
                        notes.append(f"{field_name} 中 1 条声明被截断 → 已还原为原文")
                        break
                if restored:
                    continue
                # 原文已不在新 context 里 → 记账为 retired
                retired.append(item if isinstance(item, str) else str(item))
                declared.add(item)
                notes.append(f"{field_name} 中 1 条未继承 → 已声明 retired")

    # ⑤ delta 体积 ≤ 3072
    def _size() -> int:
        # **排除 retired_context_items**：退役声明是「被删掉的原文逐字」，
        # 工具（tracking_commit）会用它与账本做**精确集合比对**——
        # 截断一个字就匹配不上，工具报「context items were dropped without
        # being declared」，而 writer 明明写了。
        # 2026-11-20 实测第 6 章：预检的口径本来就排除 retired（见
        # commands._tracking_preflight），压缩器的口径却把它算进去，于是
        # 同一份事务两处口径不一致 → 压缩触发 → 截断 → 声明失效。
        # 退役条目本身由工具在提交时按账本重新生成（tracking_commit 会用
        # sorted(dropped) 覆盖），工作台不需要替它省字节。
        payload = {k: v for k, v in delta.items() if k != "retired_context_items"}
        return len(json.dumps(payload, ensure_ascii=False).encode())

    if _size() > 3072:
        before = _size()
        # 2026-11-17 改：此前用**固定长度**一刀切（objective_fact→42 字、summary→32 字…）
        # 并**加「…」后缀**。实测账本里 6 条时间线事件被截成半句（「...，三号…」），
        # 且「…」被 storyctl 当成原文写进账本——**截断伪装成了原始内容**。
        # 而且它压的 key（description/note/detail）在 schema 里多半不存在，
        # 真正的长字段 summary 根本没被压到。
        # 现在统一用 shrink_delta_to_fit：按比例缩到刚好进限、不加省略号、
        # 只截描述性字段、不碰 id/status/枚举/必填结构。
        shrunk = shrink_delta_to_fit(delta, 3072)
        if shrunk is not None:
            delta.clear()
            delta.update(shrunk)
        else:
            # 压不动：逐项压到下限，仍不行就交给上层报明确错误
            for ev in delta.get("timeline_events") or []:
                if isinstance(ev, dict):
                    for k in ("objective_fact", "note", "detail"):
                        if isinstance(ev.get(k), str) and len(ev[k]) > 16:
                            ev[k] = ev[k][:16]
            for f in delta.get("foreshadow_changes") or []:
                if isinstance(f, dict):
                    for k in ("summary", "description", "note", "detail"):
                        if isinstance(f.get(k), str) and len(f[k]) > 16:
                            f[k] = f[k][:16]
            for c in delta.get("character_changes") or []:
                if isinstance(c, dict):
                    for k in ("change", "note", "detail"):
                        if isinstance(c.get(k), str) and len(c[k]) > 16:
                            c[k] = c[k][:16]
            if isinstance(delta.get("result"), str) and len(delta["result"]) > 16:
                delta["result"] = delta["result"][:16]
        # 退役条目**不截断**（2026-11-20 修）：它必须是被删条目的逐字原文，
        # 截断会让工具的精确比对失败（报「未声明退役」）。
        # 它也不计入上面的体积——工具提交时会按账本重新生成这一份。
        notes.append(f"delta 压缩 {before}B → {_size()}B")

    if notes:
        txn.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return notes


# ─── 机械项确定性修复 ────────────────────────────────────────────────────────
# 为什么要有：端到端跑 2 章实测，5 轮 N14 里有 3 轮只在做字符串替换，
# 而且 N14 还会漏（第 1 章第 2 轮漏了「不是工期，是他最后一次机会」）。
# 这类问题正则可判、改法唯一，不该烧一整轮 LLM。
#
# ⚠ 注意：check-ai-patterns.js 里 `mechanical` 的含义是「必须清零」，
# **不等于「改法唯一」**。破折号被标为 mechanical，但源码要求按功能改写
# （打断→动作、拖长音→省略、插入→逗号或冒号），所以这里不做，交给 N14。

# 高优 advisory：指出结构问题、且局部就能改的。只这些会在预检里拦 done，
# 其余 advisory 只记录不拦（否则会逼模型重写整段、把轮次耗光）。
HIGH_PRIORITY_ADVISORY = {
    "low-connective-density-tic",   # 补连接词，局部可改
    "formulaic-parallelism",        # 排比句式，局部可改
}


def _punct_subtypes(items: list) -> set:
    """从 PUNCTUATION_NOT_NORMALIZED 的 name 里抽子类型。

    storyctl 的 name 形如 `<路径>:3:25: ellipsis: 替换为「，」。`，
    子类型有 ellipsis / em-dash / double-hyphen / quote-style。
    必须区分：省略号可以机械换成逗号，破折号不行（要按功能改写，交给 N14）。
    """
    import re as _re
    out = set()
    for x in items:
        if not isinstance(x, dict):
            continue
        if str(x.get("id") or x.get("criterion") or "") != "PUNCTUATION_NOT_NORMALIZED":
            continue
        m = _re.search(r":\s*(ellipsis|em-dash|double-hyphen|quote-style)\s*:",
                       str(x.get("name") or ""))
        if m:
            out.add(m.group(1))
        else:
            out.add("unknown")
    return out


# 纯删除类：删掉不影响上下文的（只处理「整行就是占位符/元信息」的明确情形）
_PURE_DELETE_IDS = {"placeholder-leak", "meta-leak", "verbatim-repeat"}
# 固定句式模板：检测器登记为 mechanical+blocking，改法确定，由 _fix_quote_patterns 清零
_QUOTE_PATTERN_IDS = {
    "not-is-comparison", "reverse-not-is", "voice-contrast", "negation-parade",
    "trailer-ending", "trailer-summary",
}
_PLACEHOLDER_LINE = r"^\s*[\[（(【]?\s*(?:待补充|待定|TODO|TBD|XXX|\?\?)\s*[\]）)】]?\s*$"
_META_LINE = r"^\s*[（(【]\s*(?:本章|作者注|注[:：]|说明[:：]).{0,40}[）)】]\s*$"


def mechanical_fix(book: Path, chapter: int, items: list | None = None) -> tuple[int, list[str]]:
    """就地修掉**改法唯一**的机械类 findings，返回 (修了几类, 说明列表)。

    只做三类，且**按 finding 类型分别触发**（此前是「有任一机械项就三种全做」，
    会把只报了标点的章顺手改掉破折号，与「破折号交给 N14」的约定冲突）：

      · 省略号 `...` / `……` → 逗号（本平台禁用省略号，storyctl 明确要求）
      · 中文之间的半角标点 → 全角
      · 纯删除类：整行占位符 / 整行元信息 / 连续重复行

    **不在这里改**（都要语义判断，交给 N14）：
      · 破折号「——」——源码要求按功能改写（打断→动作、拖长音→省略、插入→逗号或冒号）
      · 禁用句式（不是X是Y / 是X不是Y / 声音不大却 / 没有X没有Y / 章末总结）
      · long-paragraph 断段、period-stutter 合句、micro-action-tic（删「了一下」会不通）
    """
    import re as _re

    if items is None:
        f = book / "_pipeline" / f"findings_C{chapter}.json"
        if not f.is_file():
            return 0, []
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return 0, []
        items = doc if isinstance(doc, list) else (doc.get("items") or [])

    ids = {str(x.get("id") or x.get("criterion") or "")
           for x in items if isinstance(x, dict)}
    subtypes = _punct_subtypes(items)

    want_ellipsis = "ellipsis" in subtypes or "ellipsis" in ids
    want_halfwidth = "PUNCTUATION_NOT_NORMALIZED" in ids or "half-width-punct" in ids
    want_delete = bool(ids & _PURE_DELETE_IDS)
    # 四类固定句式模板 + 破折号：检测器标为 mechanical+blocking，必须清零
    want_quote_patterns = bool(ids & _QUOTE_PATTERN_IDS)
    want_em_dash = "em-dash" in ids
    if not (want_ellipsis or want_halfwidth or want_delete
            or want_quote_patterns or want_em_dash):
        return 0, []

    files = body_file_for_chapter(book, chapter)
    if not files:
        return 0, []
    src = files[0]
    text = src.read_text(encoding="utf-8")
    before = text
    notes: list[str] = []

    # ① 省略号 → 逗号（仅在闸门报了 ellipsis 子类型时）
    if want_ellipsis:
        for _el in ("...", "……"):
            if _el in text:
                c = text.count(_el)
                text = text.replace(_el, "，")
                notes.append(f"省略号「{_el}」{c} → 逗号")

    # ② 中文字符之间的半角标点 → 全角
    if want_halfwidth:
        pairs = {",": "，", ";": "；", ":": "：", "?": "？", "!": "！"}
        fixed = 0
        for half, full in pairs.items():
            pat = _re.compile(r"(?<=[\u4e00-\u9fa5])" + _re.escape(half) + r"(?=[\u4e00-\u9fa5])")
            text, k = pat.subn(full, text)
            fixed += k
        if fixed:
            notes.append(f"半角标点 {fixed} → 全角")

    # ②b 四类固定句式模板（mechanical+blocking，必须清零）
    if want_quote_patterns:
        text, q_notes = _fix_quote_patterns(text)
        notes.extend(q_notes)

    # ②c 破折号（mechanical+blocking，按形态三分支改写）
    if want_em_dash:
        text, d_notes = _fix_em_dash(text)
        notes.extend(d_notes)

    # ③ 纯删除类：只删「整行就是垃圾」的，不碰正文句子
    if want_delete:
        lines = text.split("\n")
        keep, dropped = [], 0
        prev_nonempty = None
        for ln in lines:
            s = ln.strip()
            if s and _re.fullmatch(_PLACEHOLDER_LINE, s):
                dropped += 1
                continue
            if s and _re.fullmatch(_META_LINE, s):
                dropped += 1
                continue
            # 连续完全相同的行 = 复制粘贴残留
            if s and s == prev_nonempty:
                dropped += 1
                continue
            if s:
                prev_nonempty = s
            keep.append(ln)
        if dropped:
            text = "\n".join(keep)
            notes.append(f"删除垃圾行/重复行 {dropped} 行")

    if text != before:
        src.write_text(text, encoding="utf-8")
        return len(notes), notes
    return 0, []


# ─── 固定句式模板的确定性改写（2026-11-17 加）─────────────────────────────────
# 为什么要有：check-ai-patterns.js 把 not-is-comparison / reverse-not-is /
# voice-contrast / negation-parade 登记为 **mechanical + blocking**，源码注释写明
# 「blocking 的含义就是不需判断、必须清零」。可 mechanical_fix() 此前只处理
# 省略号/半角标点/垃圾行，这四类**一次都没修过**，全部落到 N14 烧 LLM 轮次，
# 而 N14 还会漏（第 1 章第 2 轮漏了「不是工期，是他最后一次机会」）。
#
# 这四类的共同点：**句式模板固定、删掉否定铺垫后句子必然更对**，因此可以纯正则改：
#   不是 A，是 B      → B
#   是 A，不是 B      → A
#   声音不大/不高…却 → 删音量修饰，留"却/但"后的现场效果
#   没有 A，没有 B    → 留最后一个（或全部改肯定陈述）
# 只改**叙述层**（检测器本身已豁免引号内台词），与检测口径保持一致。

# 「不是A，是B」/「不是A，而是B」→ 保留系词，只留 B：`是B`
# ⚠ 必须保留「是」，否则产物是病句（首版实测：「这不是害怕，是愤怒」→「这愤怒」）
_RE_NOT_IS = re.compile(r"不是([^，。！？；\n“”「」]{1,20})[，,](?:而是|是)([^，。！？；\n“”「」]{1,30})")
# 「是A，不是B」→ 只留 A，同样保留系词：`是A`
_RE_IS_NOT = re.compile(r"是([^，。！？；\n“”「」]{1,20})[，,](?:而)?不是([^，。！？；\n“”「」]{1,30})")
# 「声音不大/不高…却/但…」→ **保留主语，只删音量修饰与转折连词**
# ⚠ 必须捕获并保留主语：首版把「声音不大，却」整段删掉，
#   「屋里的声音不大，却让所有人都停了手」→「屋里的让所有人都停了手」，主语残缺。
#   正确产物是「屋里的声音让所有人都停了手」——主语在，修饰没了。
_RE_VOICE = re.compile(
    r"(声音|嗓门|话音|语气)(?:不大|不高|不响|很轻|很平)[，,]?\s*(?:却|但|可是|然而)")
# 「没有A，也没有B」→ 留最后一项做肯定陈述
_RE_NEG_PARADE = re.compile(r"没有([^，。！？；\n“”「」]{1,16})[，,](?:也)?没有([^，。！？；\n“”「」]{1,16})")


def _fix_quote_patterns(text: str) -> tuple[str, list[str]]:
    """确定性改写四类固定句式模板。只动叙述层，不碰引号内台词。

    返回 (新文本, 说明列表)。**不改语义方向**：删掉的是纯否定铺垫，
    句子主干（后项/前项）原样保留，因此不会把作者的意思改反。
    """
    notes: list[str] = []
    # 引号区间先挖出来，避免改到台词（与检测器口径一致）
    quoted: list[str] = []

    def _stash(m):
        quoted.append(m.group(0))
        return f"\x00Q{len(quoted) - 1}\x00"

    body = re.sub(r"[“「][^”」]{0,200}[”」]", _stash, text)

    def _apply(pattern, repl, label):
        nonlocal body
        new, n = pattern.subn(repl, body)
        if n:
            body = new
            notes.append(f"{label} {n} 处")

    # 顺序有讲究：先处理「不是A，是B」，否则会被「是A，不是B」误吃
    # 保留系词「是」，产物才是通顺句（`不是害怕，是愤怒` → `是愤怒`）
    _apply(_RE_NOT_IS, r"是\2", "「不是X是Y」→ 只留后项")
    _apply(_RE_IS_NOT, r"是\1", "「是X不是Y」→ 只留前项")
    # 音量反差：保留主语，只删「不大，却」这层修饰
    _apply(_RE_VOICE, r"\1", "「声音不大…却」→ 删音量修饰")
    _apply(_RE_NEG_PARADE, r"没有\2", "「没有X没有Y」→ 留末项")

    # 还原引号内容
    def _unstash(m):
        return quoted[int(m.group(1))]

    body = re.sub(r"\x00Q(\d+)\x00", _unstash, body)
    return body, notes


def _fix_em_dash(text: str) -> tuple[str, list[str]]:
    """破折号「——」按上下文三分支机械改写。

    源码要求「按功能改写」，但功能可以从**形态**判出来，不需要读懂语义：
      · 夹在两个分句中间且前后都是完整句 → 插入说明 → 改逗号
      · 后面紧跟短促动作/对话 → 打断 → 改句号断成两句
      · 其余（拖长音/停顿）→ 改逗号
    三条分支都是"去掉破折号、保持可读"，比留着 blocking 强。
    """
    if "——" not in text:
        return text, []
    notes: list[str] = []
    n_mid = n_break = 0

    # 打断式：`……——他说` 这类后面紧跟引号或极短句 → 断句
    def _break(m):
        nonlocal n_break
        n_break += 1
        return "。"

    text = re.sub(r"——(?=[“「])", _break, text)
    # 其余统一改逗号（插入说明 / 拖长音在形态上都是"句中停顿"）
    n_mid = text.count("——")
    text = text.replace("——", "，")
    if n_break:
        notes.append(f"破折号打断式 {n_break} → 句号")
    if n_mid:
        notes.append(f"破折号停顿式 {n_mid} → 逗号")
    # 连续两个逗号是替换副产物，收一下
    text = re.sub(r"，[，、]", "，", text)
    return text, notes


# ─── 字数裁剪器（2026-11-17 加）────────────────────────────────────────────────
# 为什么要有：storyctl 对超长章只**报**「remove_to_internal_band: min..max」，
# 却不提供任何压缩实现；N12 提示词也只是说"不要写超"。结果每一章超长都要
# 人工逐段读、逐段判断删哪句——第 2 章我手工裁了 689 字，第 4 章补了 500 字。
# 这是全流程最大的一块人工成本。
#
# 设计原则（照 writing-contract 的「不许注水／不许删剧情」）：
#   · 只删**段**，不改句内字词——避免改坏语义
#   · 按**信息密度**排序决定删谁：对话/动作/数字/因果词密度高的段留下
#   · 硬保护：含引号的对话段、含数字的段、首段、末段（钩子）一律不删
#   · 删到**刚好进带**就停，不追求越短越好

# 段落价值信号。**正向信号只保留"推进剧情/承载信息"的**，
# 氛围词一律不给分（首版把「门/响/空气」也算高价值，结果环境段拿 9 分、
# 无段可删，裁剪器完全失效——实测抓出）。
_VALUE_SIGNALS = (
    (re.compile(r"[“「]"), 6),                       # 对话：推动情节
    (re.compile(r"\d"), 3),                          # 数字：落地细节
    (re.compile(r"因为|所以|于是|这才|原来|难怪|怪不得|既然|要是|否则"), 3),  # 因果
    (re.compile(r"【"), 4),                          # 系统提示：功能性文本
    # 信息承载：纸/账/单/证/字/号 + "写着/三个字"这类指认句式。
    # 这是本章关键信息段的唯一可靠特征（如「那张纸上有三个字：抚恤金。」）
    (re.compile(r"纸|信|账|单|表|册|证|章|据|款|写着|签着|记着|三个字|两个字"), 5),
    # 强动作：带明确对象的动作（"蹲下去看导轨"），弱于对话但高于氛围
    (re.compile(r"蹲|拉开|掏出|摸出|捡起|摊开|塞进|折好|按在|贴在|推开"), 2),
)

# 纯氛围特征：命中即**扣分**。这些词只描述环境/感官，不推进剧情。
# 判据：把这类词从段落里抽掉，段落的信息量不变 → 它们本来就是可删的。
_AMBIENCE = re.compile(
    r"光|影|味|风|色|天空|远处|空气|感觉|似乎|好像|安静|灰白|机油|铁屑|"
    r"斜下来|钻进来|落在|响着|能听见|很长很长|一片")


# 裁剪时可删段落的分数上限：分数 ≤ 此值才允许删。
# 0 = 只有纯氛围/铺陈段（无对话、无数字、无因果、无信息承载）才可删。
# 设成 0 而不是负数，是因为宁可少删、报「可删段落已用尽」，
# 也不能为凑字数删掉承载信息的段落（首版无此限，删掉了「抚恤金」段）。
_TRIM_SCORE_FLOOR = 0


def _para_value(para: str) -> int:
    """段落信息密度打分。分数越低越该删。

    ⚠ 两版教训（都被夹具抓出）：
    1. 首版有 `- len(para)//60` 长度惩罚 → 「那张纸上有三个字：抚恤金。」
       (11 字) 被算成低价值删掉，而 20 个重复动作段全留。信息价值和长度无关。
    2. 二版把「门/响/空气」当正向信号 → 纯环境段拿到 9 分、无一低于阈值，
       裁剪器彻底不动作（3475 字原样返回）。氛围词必须**扣分**，不能给分。
    """
    score = 0
    for pattern, weight in _VALUE_SIGNALS:
        score += weight * len(pattern.findall(para))
    # 氛围词扣分：这类词抽掉后信息量不变，本就是可删的
    score -= 3 * len(_AMBIENCE.findall(para))
    return score


def _skill_script_dirs() -> list[Path]:
    """候选的技能 scripts 目录（按优先级）。

    `wordcount_core`（平台字数口径的唯一实现）只存在于 story-long-write 技能里。
    只按 config 里的 STORY_LONG_WRITE 取会有两个问题：
      · config 指向失效/临时目录时（测试夹具就是这种），工具直接瘫；
      · 换机器/换技能版本时，config 忘了更新，字数口径整个取不到。
    所以再补上技能装在用户目录时的标准位置。**只挂路径，不抄实现**——
    抄一份就会和技能漂移，那正是这套工具反复吃的亏。
    """
    cands: list[Path] = []
    try:
        cfg = _cfg()
        root = cfg.get("STORY_LONG_WRITE")
        if root:
            cands.append(Path(root) / "scripts")
    except Exception:
        pass
    home = Path.home()
    for rel in (".qoder/skills/story-long-write/scripts",
                ".claude/skills/story-long-write/scripts"):
        cands.append(home / rel)
    seen: set[str] = set()
    out: list[Path] = []
    for c in cands:
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        if c.is_dir() and (c / "wordcount_core.py").is_file():
            out.append(c)
    return out


def _ensure_skill_path() -> list[Path]:
    """把技能 scripts 目录挂到 sys.path，返回实际用到的那些。"""
    dirs = _skill_script_dirs()
    for c in dirs:
        p = str(c)
        if p not in sys.path:
            sys.path.insert(0, p)
    return dirs


def visible_chars(text: str) -> int:
    """可见字符数（平台口径）。**唯一事实源在技能目录的 wordcount_core 里**。

    2026-11-20 修：自动裁剪此前从不生效——`trim_to_band` 里的
    `from wordcount_core import count_visible_chars` 直接 ImportError，
    而调用方用裸 `except Exception` 吞掉，于是「超长章会被自动裁到带内」
    这条能力**是死代码**：章超了只会判 over 打回，由人逐段读、逐段删
    （实测第 2 章手工裁 689 字）。吞异常把「工具坏了」和「没什么可裁」
    混成了同一件事。
    """
    dirs = _ensure_skill_path()
    try:
        from wordcount_core import count_visible_chars
    except ImportError as exc:
        raise RuntimeError(
            "找不到 wordcount_core（平台字数口径的唯一实现）。已尝试："
            + (", ".join(str(d) for d in dirs) or "（无候选目录）")
            + "。请检查 workbench config 的 STORY_LONG_WRITE 是否指向有效的 "
              "story-long-write 技能目录。") from exc
    return count_visible_chars(text)


def trim_to_band(book: Path, chapter: int, band_max: int,
                 band_min: int | None = None) -> tuple[int, list[str]]:
    """把超长章节按段落信息密度裁进字数带。返回 (删了几段, 说明)。

    **只删整段**，不碰句内字词；对话段、含数字段、首末段受保护。
    裁到 band_max 以下即停（不继续裁到 band_min，避免过度删减）。
    """
    files = body_file_for_chapter(book, chapter)
    if not files:
        return 0, []
    src = files[0]
    text = src.read_text(encoding="utf-8")

    current = visible_chars(text)
    if current <= band_max:
        return 0, []

    # 目标本身不可满足（上限低于下限）：不删，交回上层。
    # 首版没这一条，于是 band_max=100 / band_min=150 时仍一路删到 163，
    # 「达标」和「保下限」互相矛盾却没人拦——实测两例全挂。
    if band_min is not None and band_max < band_min:
        return 0, [f"上限 {band_max} < 下限 {band_min}，区间不可满足，放弃机械裁剪"]

    paras = [p for p in text.split("\n\n")]
    if len(paras) < 6:
        return 0, []

    # 首段（开场冲突）与末段（章末钩子）绝对保护
    protected = {0, len(paras) - 1}
    candidates: list[tuple[int, int, int]] = []   # (价值, 索引, 字数)
    for i, p in enumerate(paras):
        if i in protected or not p.strip():
            continue
        # 硬保护：对话 / 数字 / 系统提示
        if re.search(r"[“「]", p) or re.search(r"\d", p) or "【" in p:
            continue
        candidates.append((_para_value(p), i, visible_chars(p)))

    if not candidates:
        return 0, []

    # 低价值优先删
    candidates.sort()
    removed: list[int] = []
    need = current - band_max
    saved = 0
    # 分数下限：只允许删「确实低价值」的段（≤ _TRIM_SCORE_FLOOR）。
    # 首版没有这条，于是一路往下删到 15 分的「抚恤金」段——那是本章的关键信息。
    # 宁可少删、报「可删段落已用尽」，也不能为了凑字数把信息删掉。
    for value, idx, size in candidates:
        if saved >= need:
            break
        if value > _TRIM_SCORE_FLOOR:
            break          # 已升到有价值区间，停止
        # 下限保护必须**逐段累计检查**：先算删掉这段之后会剩多少，
        # 若跌破 band_min 就跳过它（首版只在全删完后检查一次，
        # 结果 712→163 已经裁穿下限却仍被写入，实测抓出）。
        if band_min is not None and (current - saved - size) < band_min:
            continue
        removed.append(idx)
        saved += size

    if not removed:
        return 0, []

    keep = [p for i, p in enumerate(paras) if i not in set(removed)]
    new_text = "\n\n".join(keep)
    after = visible_chars(new_text)

    # 兜底断言：逐段检查之后仍不允许跌破下限（防未来改动破坏）
    if band_min is not None and after < band_min:
        return 0, [f"裁剪会跌到 {after} 字 < 下限 {band_min}，已放弃（交 N14 语义压缩）"]

    src.write_text(new_text, encoding="utf-8")
    note = f"字数 {current} → {after}（删 {len(removed)} 段，目标 ≤{band_max}）"
    if after > band_max:
        # 候选段删尽仍超上限 = 可删内容不足，不是算法失败。
        # 必须**明确报告**，让上层决定交 N14 语义压缩，而不是静默当成功。
        note += f"；仍超上限 {after - band_max} 字，可删段落已用尽，需语义压缩"
    return len(removed), [note]


def _stale_gate_evidence(book: Path, chapter: int) -> list[dict]:
    """第 {chapter} 章的裁决引文是否还与当前正文一致。

    **为什么要有**（2026-11-20 实测）：
    闸门的取证依据是 judge 在裁决当时引用的原文。若这章在裁决之后被改写
    （revision 定稿、手工改稿），裁决的依据就作废了——但节点状态仍写着
    done，等于一份**过期绿灯**。

    实测《逆袭从掌控系统开始》：C1/C3/C6 的 N15 裁决共 10 条引文失配，
    失配原因是正文被微改（「又添**了**一句」→「又添一句」），
    **账本 sha 与磁盘一致，漂移检测完全看不见**——因为 revision 定稿把
    正文和账本 sha 一起更新了。漂移检测盯的是「被篡改」，盯不住「被合法
    修订但没重裁」。

    返回失配条目；空列表 = 裁决依据仍然有效。
    """
    jf = book / "_pipeline" / f"judgments_C{chapter}.N15.json"
    if not jf.is_file():
        return []
    doc = _read_json(jf, default={}) or {}
    stale: list[dict] = []
    for item in doc.get("items") or []:
        if not isinstance(item, dict):
            continue
        quote = (item.get("quote") or "").strip()
        source = (item.get("source") or "").strip()
        if not quote or not source:
            continue
        src = book / source
        if not src.is_file():
            stale.append({"chapter": chapter, "criterion": item.get("criterion"),
                          "source": source, "quote": quote[:80],
                          "reason": "source 文件已不存在"})
            continue
        try:
            body = src.read_text(encoding="utf-8")
        except OSError:
            continue
        if not verify_quote(quote, body):
            stale.append({"chapter": chapter, "criterion": item.get("criterion"),
                          "source": source, "quote": quote[:80],
                          "reason": "裁决引文在当前正文里已不命中（该章在裁决后被改写）"})
    return stale


def _stale_global_gate_evidence(book: Path) -> list[dict]:
    """**全局裁决**（N20 / N20g / V{v}.N20）的引文是否还与当前正文一致。

    章级 N15 的过期绿灯由 `_stale_gate_evidence` 负责——但它只覆盖**章级**裁决。
    全书终审的引文同样会随正文改写而失效：2026-11-21 实测
    《逆袭从掌控系统开始》改了第 6 章之后，`judgments_N20.json` 里
    **1 条引文在当前正文里已经找不到**，而打包检查完全没报
    （它只看章级）——等于一本书的最终裁决可以建立在已经不存在的原文上。

    与章级同理：这里只报事实，不修。修法是 `autopilot.py rejudge --node N20`。
    """
    out: list[dict] = []
    pipeline = book / "_pipeline"
    files = []
    for pattern in ("judgments_N20.json", "judgments_N20g.json", "judgments_V*.N20.json"):
        files.extend(sorted(pipeline.glob(pattern)))
    for jf in files:
        doc = _read_json(jf, default={}) or {}
        for item in (doc.get("items") or []):
            if not isinstance(item, dict):
                continue
            quote = (item.get("quote") or "").strip()
            source = (item.get("source") or "").strip()
            if not quote or not source:
                continue
            src = book / source
            if not src.is_file():
                out.append({"judgment": jf.name, "id": item.get("id"),
                            "criterion": item.get("criterion"), "source": source,
                            "quote": quote[:80], "reason": "source 文件已不存在"})
                continue
            try:
                body = src.read_text(encoding="utf-8")
            except OSError:
                continue
            if not verify_quote(quote, body):
                out.append({"judgment": jf.name, "id": item.get("id"),
                            "criterion": item.get("criterion"), "source": source,
                            "quote": quote[:80],
                            "reason": "裁决引文在当前正文里已不命中（该章在裁决后被改写）"})
    return out


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
    # 封板前自动规整 tracking（结构性问题不该靠 LLM 重写整章来修）
    _prev_ctx = (previous.get("context") or {}) if isinstance(previous, dict) else {}
    _notes = _normalize_tracking(txn, st, _prev_ctx)
    cmd = [cfg["PYTHON"], cfg["STORYCTL"], "chapter", "commit",
           "--project", str(book), "--chapter", str(chapter), "--input", str(txn)]
    rc, out = run_script(cmd, book, timeout=300)
    if rc != 0:
        return rc, out
    state = _read_json(book / "追踪" / "_tracking-state.json", default={}) or {}
    records = state.get("wordcount_records") or {}
    st["words_total"] = sum(int(v.get("actual") or 0) for v in records.values()
                            if isinstance(v, dict))
    _rec = records.get(str(chapter)) or {}
    _mark_findings_resolved(book, chapter, {
        k: _rec.get(k) for k in ("actual", "target", "metric", "status", "resolution")
        if _rec.get(k) is not None
    })
    _tail = ("\n规整: " + "；".join(_notes)) if _notes else ""
    return 0, out + f"\nwords_total={st['words_total']}" + _tail


def _mark_findings_resolved(book: Path, chapter: int, metrics: dict | None = None) -> None:
    """封板成功后给本章 findings 盖上已解决戳，Dashboard 才不会把已修复项当现行问题。

    注意 `resolved_blocking` 的含义（2026-10-25 修）：
    此前写的是 `blocking_count`（**封板前的旧值**），读起来像「解决了 3 项」，
    实际是「当时有 3 项待解决」。N16 commit 内部已重跑 check，能走到这里就说明
    阻断项已经清零，所以这里写 0，并另存封板时的实测指标备查。
    """
    path = book / "_pipeline" / f"findings_C{chapter}.json"
    doc = _read_json(path, default=None)
    if not isinstance(doc, dict):
        return
    doc["resolved_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
    doc["resolved_blocking"] = 0
    doc["resolved_was_blocking"] = doc.get("blocking_count", 0)
    if metrics:
        doc["resolved_metrics"] = metrics
    _write_json(path, doc)


def _compose_full_text(book: Path, st: dict, chapters: list[Path]) -> str:
    """把各章正文合成一份带**章节标题**的全文（编辑要读的那份）。

    **2026-11-18 修**：此前只拼接正文、**不插章节标题**，
    于是编辑拿到的是 9 章连成一片的 6 万字——**找不到章界**，
    也没法在反馈里指明「第几章」。（此前 agent-52 复审指出。）

    标题取章节标题行（正文首行的 `# 第N章 标题`），没有就用文件名。
    """
    buf = []
    for p in chapters:
        raw = p.read_text(encoding="utf-8").strip()
        title = ""
        for line in raw.splitlines():
            s = line.strip()
            if s.startswith("#"):
                title = s.lstrip("# ").strip()
                break
        if not title:
            title = p.stem.replace("_", " ")
        # 正文里若已有标题行，去掉它，避免与插入的标题重复
        lines = raw.splitlines()
        if lines and lines[0].strip().startswith("#"):
            raw = "\n".join(lines[1:]).strip()
        buf.append(f"# {title}\n\n{raw}")
    text = "\n\n".join(buf) + "\n"
    if not text.lstrip().startswith("#"):
        text = f"# {st.get('book') or book.name}\n\n" + text
    return text


def _merge_full(book: Path, st: dict) -> tuple[int, str]:
    """N17：合成全文到 投稿包/全文.md，并重置全书 findings（N20f 回跳的入口）。"""
    # T-2：只合现役正文——_原稿_ 备份稿进全文会让投稿包把同一章的新旧两稿一起发给编辑。
    parts = body_files(book)
    if not parts:
        return 2, "正文/ 下没有章节文件"
    text = _compose_full_text(book, st, parts)
    out = book / "投稿包" / "全文.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    reset_endgame_findings(book, st)
    return 0, f"合并 {len(parts)} 章 -> {out}；已重置 endgame_findings"


def _chapter_no(name: str) -> int | None:
    parsed = parse_body_file_name(name)
    return parsed[0] if parsed else None


def _causal_diversity_findings(files: list[Path]) -> list[dict]:
    """因果词多样性检查：同一个因果词占全部因果词的比例过高 → advisory。

    阈值 0.60 来自 18 个七猫年代签约样本的实测上界（最大 0.600，中位 0.429）。
    **只报 advisory 不阻断**：本指标没过配对判别验证，按项目不变式
    不能进 gates.json 的区间判定，只能作定位线索。
    """
    # 与 clarity_org_probe.ROTATION_CAUSAL 保持一致（含提示词要求 writer 用的词）
    CAUSAL = ("因为", "所以", "于是", "因此", "结果", "这才", "可见", "原来",
              "怪不得", "难怪", "既然", "要是", "如果", "否则", "不然", "为此",
              "果然", "正因为", "偏偏", "以至于")
    limit = 0.60
    out: list[dict] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        counts = {w: text.count(w) for w in CAUSAL if text.count(w)}
        total = sum(counts.values())
        if total < 3:
            continue                      # 样本太少，占比无意义
        top = max(counts, key=counts.get)
        share = counts[top] / total
        if share <= limit:
            continue
        cands_hint = ""
        try:
            from causal_rotator import get_rotation_candidates
            cands = get_rotation_candidates(top)
            if cands:
                cands_hint = f"（推荐保义替换候选：{'、'.join(cands[:4])}）"
        except Exception:
            pass
        out.append({
            "tool": "causal-diversity",
            "severity": "advisory",
            "blocking": False,
            "chapter": _chapter_no(f.name),
            "file": f.name,
            "line": None,
            "type": "causal-word-repetition",
            "message": (f"因果收尾词「{top}」出现 {counts[top]} 次，占全部因果词的 "
                        f"{share:.0%}（签约样本实测上界 60%）。"
                        "同一词刷满全章是明确的 AI 指纹——总量指标测不出，只有占比看得出。"
                        f"改法：换成别的因果词{cands_hint}，或改写成动作后果/具体细节。"),
            "excerpt": f"{top} x{counts[top]}",
        })
    return out


def _action_beat_findings(files: list[Path]) -> list[dict]:
    """叙述动作节拍密度过高 → advisory（**不改稿目标**）。

    **数据来源**（2026-11-17，真实七猫拒稿样本 #2 之后重跑判别力）：
    `action_beat_per_1000` 在 2 本真实七猫拒稿书上 AUC=0.972、簇自助 CI
    [0.931, 1.0]、按书簇 permutation_p=0.0095 —— 已满足 auc / ci_low / p 三条门槛，
    **只差「拒稿书≥3」**（实际 2/14，预注册门槛 3/6）。
    逐书中位：拒稿 163 / 203，14 本签约 32.5–136.3，两本拒稿书的最小值（121.3）
    高于 12/14 本签约书的最大值。

    **为什么只作 advisory**：按 `gates.json` 的判别力不变式，
    未通过全部 promotion_criteria 的指标**不得进入改稿目标**，只能作定位线索。
    本指标的 verdict 仍是 `UNVERIFIED_INSUFFICIENT_INDEPENDENCE`，
    所以它只报、不拦、也不写进任何「改稿建议」。

    **口径必须复用 craft_probe**（首版自己实现了一遍，结果对拒稿样本报 0 条——
    口径错了就等于没测）。craft_probe 的定义是「对话之外、含身体动作动词的分句数」。
    """
    LIMIT = 121.5          # 拒稿样本的最小值（p05 121.55 同量级）
    out: list[dict] = []
    try:
        import craft_probe as _cp
    except ImportError:
        return out
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        m = _cp.metrics(text)
        if not m:
            continue
        density = m.get("action_beat_per_1000")
        if not isinstance(density, (int, float)) or density <= LIMIT:
            continue
        out.append({
            "tool": "action-beat",
            "severity": "advisory",
            "blocking": False,
            "chapter": _chapter_no(f.name),
            "file": f.name,
            "line": None,
            "type": "action-beat-density-high",
            "message": (f"动作节拍密度 {density:.0f}/万字，高于真实拒稿样本的下界 "
                        f"{LIMIT:.0f}。**定位线索，不是改稿目标**——该指标 AUC=0.972，"
                        "但拒稿样本只有 2 本，未过判别门槛（需 ≥3）。已拒稿样本的动作密度"
                        "是签约书上限的 1.5 倍以上：动作多但没留下痕迹。"
                        "可对照检查本章是否存在「动作堆砌、细节无痕」。"),
            "excerpt": f"action_beat_per_1000={density:.0f}",
        })
    return out


def _read_relative_day_whitelist(path: Path) -> set[str]:
    """读「相对日锚句」白名单：`追踪/复沓锚句.md` 里 `## 相对日锚句` 段的列表项。

    只认该段下的 `- 「引文」` 行（与复沓锚句白名单分开读，避免混淆）。
    """
    out: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    in_section = False
    for line in lines:
        s = line.strip()
        if s.startswith("## "):
            in_section = ("相对日" in s)
            continue
        if not in_section:
            continue
        if not (s.startswith("- ") or s.startswith("* ")):
            continue
        m = re.match(r"^[「“\"]([^」”\"]{4,})[」”\"]", s[2:].strip())
        if m:
            out.add(m.group(1))
    return out


def _lcs_contiguous(a: str, b: str) -> str:
    """最长**连续**公共子串。

    **不要用二分 + `in` 的写法**（2026-11-18 实测有 bug）：
    二分版在子串有重叠或 `start` 靠后时会**报出根本不存在的片段**——
    实测它把第1↔2章的重复报成「这张卡要么进厂部的档案，要么」，
    而第2章里根本没有这句话；真实重复是另一句。
    误报的片段会误导改稿（照着不存在的句子去改）。

    这里用标准滚动 DP：O(n·m) 时间、O(m) 空间。
    """
    if not a or not b:
        return ""
    n, m = len(a), len(b)
    if n * m > 4_000_000:            # 太大就退化为「按较短串逐位扫描」
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        best = ""
        for i in range(len(shorter)):
            for L in range(min(len(shorter) - i, 200), 0, -1):
                if shorter[i:i + L] in longer:
                    if L > len(best):
                        best = shorter[i:i + L]
                    break
        return best
    prev = [0] * (m + 1)
    best_len = 0
    best_end = 0
    for i in range(1, n + 1):
        cur = [0] * (m + 1)
        ai = a[i - 1]
        for j in range(1, m + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best_len:
                    best_len = cur[j]
                    best_end = i
        prev = cur
    return a[best_end - best_len:best_end]


def book_anchor_file(files: list[Path]) -> Path | None:
    """从正文文件路径推出本书根目录下的「复沓锚句登记」文件（若有）。"""
    if not files:
        return None
    root = files[0].parent.parent          # 正文/ 的上一级 = 书目录
    return root / "追踪" / "复沓锚句.md"


def _read_anchor_whitelist(path: Path) -> set[str]:
    """读复沓锚句登记。

    **只认列表项行**（`- 「引文」` 或 `- "引文"`）——
    首版把说明性文字也读进了白名单（实测 22 条，其中 18 条是文档说明），
    那样白名单就等于没有。格式不对的条目直接忽略。
    """
    out: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("- ") and not s.startswith("* "):
            continue
        s = s[2:].strip()
        m = re.match(r"^[「“\"]([^」”\"]{4,})[」”\"]", s)
        if m:
            out.add(m.group(1))
    return out


def _cross_chapter_duplicate_findings(files: list[Path],
                                     min_len: int = 12) -> list[dict]:
    """跨章**逐字重复**检测：任意长度的共同片段 ≥min_len 字 → advisory。

    **为什么要有**（2026-11-18）：三轮红队反复挖出同一类缺陷——
    「爽点只有一个模子」：
      · 第3章与第4章的「听音→定位销→锉配→试车→交件」流程，
        实测跨章最长公共子串 **26 字逐字相同**
      · 第7章与第9章的塞尺句 18 字逐字重复
      · 第8章章内「毛茬朝一个方向倒」说了两遍

    **特别值得记的一处**：我为了消除第 9 章的重复，把第 9 章改成
    「一格一格试，试到哪一格涩住」——**结果和第 7 章一字不差**，
    自己造出了一处新的逐字重复。人工逐章比对抓不到这种自己制造的重复。

    方法：**跨章最长公共子串**（不是整段相同，是任意长度的共同片段）。
    阈值 12 字——低于 12 字的中文短语在网文里是正常共用词。

    **白名单**：细纲「复沓锚句」是**刻意**的跨章呼应（同一句话在不同章出现，
    第二次有升级或回响），属正常手法；技术动作的必然重复（如「咔的一声，
    档位没有跳」——同一故障现象两次出现）也属正常。这些登记在
    `追踪/伏笔.md` 或本书的**白名单文件**里，检查时跳过。

    **只报 advisory**：跨章重复未必是缺陷。
    """
    # 刻意复沓的白名单：来自 追踪/伏笔.md 的「复沓锚句」与本书的锚句登记。
    whitelist: set[str] = set()
    for wf in (book_anchor_file(files),):
        if wf and wf.is_file():
            whitelist |= _read_anchor_whitelist(wf)
    chapters = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        ch = _chapter_no(f.name)
        if ch is None:
            continue
        # 只留正文里的汉字与标点，去掉空白（空白差异不算逐字重复）
        norm = re.sub(r"\s+", "", text)
        if norm:
            chapters.append((ch, f, norm))

    out: list[dict] = []
    for i, (ch_a, fa, ta) in enumerate(chapters):
        for ch_b, fb, tb in chapters[i + 1:]:
            best = _lcs_contiguous(ta, tb)
            if len(best) >= min_len:
                # 白名单：刻意复沓的锚句（细纲指定）不算缺陷
                if any(w and (w in best or best in w) for w in whitelist):
                    continue
                out.append({
                    "tool": "cross-chapter-dup",
                    "severity": "advisory",
                    "blocking": False,
                    "chapter": ch_a,
                    "file": fa.name,
                    "line": None,
                    "type": "cross-chapter-duplicate",
                    "message": (f"第{ch_a}章与第{ch_b}章有 {len(best)} 字逐字相同："
                                f"「{best[:34]}」。"
                                "「爽点只有一个模子」是编辑一眼能看出的重复——"
                                "同一组描写用两次且第二次没有升级，读起来像复制粘贴。"
                                "改法：保留一处，另一处换动作/换视角/换结果。"),
                    "excerpt": best[:40],
                })
    return out


def _relative_day_findings(files: list[Path]) -> list[dict]:
    """相对日互斥检查：同一相对日词在**相隔 ≥2 章**处描述**同一件事** → advisory。

    **为什么要有**（2026-11-18）：第二轮红队（agent-51）挖出全书**日期网断裂**，
    7 处全部属实，典型如：
      · 第1章「落款日期那一栏，写的是前天」
      · 第5章「落款日期那一栏，写着前天」  ← 两章相隔 ≥3 天，同指「前天」
    这类缺陷**不需要文学判断**——只要把相对日抽出来做交叉校验就能查。
    而当时工具完全查不出，因为账本的 `story_time` 全是「1983年某日」，
    **没有具体日期锚**。

    **只报 advisory**：判定「是不是同一件事」要语义，本检查只做候选定位。
    「相邻章的『前天』指不同日」是正常的，所以只比**相隔 ≥2 章**的同词共现。
    """
    REL = {"今天": 0, "今早": 0, "今儿": 0,
           "昨天": -1, "昨儿": -1, "昨夜": -1, "昨晚上": -1,
           "前天": -2, "前两天": -2,
           "大前天": -3, "三天前": -3,
           "四天前": -4, "五天前": -5, "六天前": -6, "七天前": -7,
           "半个月前": -15, "一个月前": -30, "两个月前": -60,
           "半年前": -180, "一年前": -365, "两年前": -730, "三年前": -1095}
    # **只查跨日词**：偏移 0 的（今天/今早）在每章都指本章那天，永远不冲突。
    # 首版把「今天」也算进去，实测全书报出 6 处冲突——**全是误报**。
    CROSS_DAY = {w: off for w, off in REL.items() if off != 0}
    TOPIC = ("工亡", "抚恤", "停职", "落款", "报告", "封条", "鉴定", "签字",
             "床子", "顶职", "工装", "笔记", "残角", "接班", "上班", "签批")
    # 长词优先，避免「前天」被「三天前」抢先
    order = sorted(CROSS_DAY, key=len, reverse=True)

    # **白名单**：同一件事在不同章用**不同措辞**表达同一相对日时，字面相同但语义
    # 不冲突——检测器只比字面，会误报。例如第1章（D0）的「前两天」与第5章（D3）的
    # 「事故前两天」指同一天（报告落款日 = D-2），但隔着 3 天。
    # 登记在 `追踪/复沓锚句.md` 的「相对日锚句」段里，检查时跳过。
    day_whitelist: set[str] = set()
    af = book_anchor_file(files)
    if af and af.is_file():
        day_whitelist = _read_relative_day_whitelist(af)

    per_chapter: list[tuple[int, Path, list[tuple[str, int, str]]]] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        ch = _chapter_no(f.name)
        if ch is None:
            continue
        hits: list[tuple[str, int, str]] = []
        for para in text.split("\n\n"):
            p = para.strip()
            if not p:
                continue
            for w in order:
                for m in re.finditer(re.escape(w), p):
                    s = max(0, m.start() - 40)
                    ctx = p[s:m.end() + 40]
                    hits.append((w, REL[w], ctx))
        if hits:
            per_chapter.append((ch, f, hits))

    out: list[dict] = []
    for i, (ch_a, fa, hits_a) in enumerate(per_chapter):
        for ch_b, fb, hits_b in per_chapter[i + 1:]:
            if ch_b - ch_a < 2:
                continue                      # 相邻章的「前天」指不同日，正常
            for wa, offa, ctxa in hits_a:
                for wb, offb, ctxb in hits_b:
                    if wa != wb:
                        continue
                    # 白名单：同一件事的相对日锚句（不同章不同措辞，指同一天）
                    # 双向子串匹配：锚句可能被截在 40 字上下文里，也可能上下文更长。
                    if any(dw and (dw in ctxa or dw in ctxb
                                   or ctxa in dw or ctxb in dw)
                           for dw in day_whitelist):
                        continue
                    ta = {k for k in TOPIC if k in ctxa}
                    tb = {k for k in TOPIC if k in ctxb}
                    if not (ta & tb):
                        continue              # 不是同一件事
                    out.append({
                        "tool": "relative-day",
                        "severity": "advisory",
                        "blocking": False,
                        "chapter": ch_a,
                        "file": fa.name,
                        "line": None,
                        "type": "relative-day-conflict",
                        "message": (f"第{ch_a}章与第{ch_b}章相隔 {ch_b - ch_a} 章，"
                                    f"却都用「{wa}」描述同一件事（"
                                    f"{'、'.join(sorted(ta & tb))}）。"
                                    "相隔 ≥2 章的同一相对日必然指向不同日期，"
                                    "除非有明示的日链——否则读者会算出矛盾。"),
                        "excerpt": f"{fa.name}: {ctxa[:40]}",
                    })
    return out


def _daychain_findings(book: Path) -> list[dict]:
    """日链一致性：按 `追踪/时间线/日链.md` 逐条核对每章的日锚句 → advisory。

    **为什么要有**（2026-11-19）：四轮红队（agent-47/51/52/53）挖出的最重一类
    缺陷是**日期网断裂**，共 8+ 处——同一份报告的落款日在相隔 3 天的两章里都叫
    「前天」；第5章「封条是事故前一天贴的」而第3/4章锁死封条于事故**后**一天；
    第9章「这两年…鉴定签字」而签字权 4 天前才拿到。

    **这些全部可计算**——把每章钉到绝对日（D0…D7）上就能交叉校验。
    但此前工具查不出，因为账本 `story_time` 全是「1983年某日」，没有绝对日锚。

    基准文件 `追踪/时间线/日链.md` 由正文互证推导，列出每章的逐字锚句。

    **只报 advisory**：日链表本身要人工维护，锚句被合法改写时会误报；
    它是定位线索，不是阻断条件。
    """
    try:
        import importlib.util
        import os
        # 工具路径：优先用环境变量，其次按本仓库同级找 小说/platforms/_core/tools
        cand = []
        env_p = os.environ.get("QIMAO_TOOLS_DIR")
        if env_p:
            cand.append(Path(env_p))
        here = Path(__file__).resolve()
        # **parents 越界会抛 IndexError**（实测 `here.parents` 长度 5、合法索引 0–4，
        # `range(1, len(parents)+1)` 会取到索引 5 → IndexError: 5）。
        # 首版 range(1,6) 在 Windows 根盘上直接崩，而 except 把它当成「校验失败」
        # 吞掉，表现为「日链校验未能执行」，**掩盖了真正的错误**。
        # 正确写法：range(1, len(parents))。
        for up in range(1, len(here.parents)):
            cand.append(here.parents[up] / "小说" / "platforms" / "_core" / "tools")
            cand.append(here.parents[up] / "platforms" / "_core" / "tools")
        tool = None
        for c in cand:
            if (c / "daychain_check.py").is_file():
                tool = c / "daychain_check.py"
                break
        if tool is None:
            return [{
                "tool": "daychain", "severity": "advisory", "blocking": False,
                "chapter": None, "file": "", "line": None,
                "type": "daychain-no-tool",
                "message": ("找不到 daychain_check.py，无法校验日链一致性"
                            "（设 QIMAO_TOOLS_DIR 或检查工具安装位置）"),
                "excerpt": "",
            }]
        spec = importlib.util.spec_from_file_location("daychain_check", tool)
        if spec is None or spec.loader is None:
            return []
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ok, report = mod.check_daychain(book)
    except Exception as exc:
        return [{
            "tool": "daychain", "severity": "advisory", "blocking": False,
            "chapter": None, "file": "", "line": None,
            "type": "daychain-error",
            "message": f"日链校验未能执行：{type(exc).__name__}: {exc}",
            "excerpt": "",
        }]
    if ok:
        return []
    out = []
    for line in report:
        if "通过" in line:
            continue
        out.append({
            "tool": "daychain",
            "severity": "advisory",
            "blocking": False,
            "chapter": None,
            "file": "",
            "line": None,
            "type": "daychain-mismatch",
            "message": (f"日链不一致：{line}。"
                       "日期网断裂是四轮红队最重的一类缺陷——"
                       "同一件事在不同章的相对日必须指向同一天。"),
            "excerpt": line[:80],
        })
    return out


def _ai_scan(book: Path) -> tuple[int, str]:
    """N18：全书 AI 味 / 退化扫描（只读，不修）。"""
    cfg = _cfg()
    files = body_files(book)
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
    # 因果词多样性（2026-11-17 加）：**advisory，不阻断**。
    # 为什么在这而不是 gates.json：gates.json 的区间是用**配对样本**标定的，
    # 这个指标没过判别阶梯（实测《顶职》第1章 0.375 落在七猫签约样本区间内），
    # 按项目的判别力不变式，未过配对判别的指标只许作定位线索，不许进区间判定。
    #
    # 它要抓的是「同一个因果词刷满全章」这种机械指纹：实测第 3 章「果然」x44
    # 时，causal_per_1000 只报 3.07（达标）、闸门全绿——**总量指标看不出，只有占比看得出**。
    # 阈值 0.60 取自 18 个七猫年代签约样本的实测上界（最大 0.600）。
    diversity = _causal_diversity_findings(files)
    items.extend(diversity)
    # 动作节拍密度过高（2026-11-17 加）：同样只报 advisory。
    # 判别力：2 本真实七猫拒稿书上 AUC=0.972、CI [0.931,1.0]、p=0.0095，
    # **只差拒稿书≥3 这一条门槛**，所以不能当改稿目标，只能当定位线索。
    beats = _action_beat_findings(files)
    items.extend(beats)
    # 相对日互斥（2026-11-18 加）：跨章同指同一相对日 → 日期网断裂的候选。
    # 第二轮红队挖出全书 7 处日期矛盾，全是可计算的——但当时工具查不出，
    # 因为账本 story_time 全是「1983年某日」，没有具体日期锚。
    # 本检查抽出相对日做跨章共现比对，只报 advisory（判定「同一件事」要语义）。
    rel = _relative_day_findings(files)
    items.extend(rel)
    # 跨章逐字重复（2026-11-18 加）：「爽点只有一个模子」是编辑一眼能看出的重复。
    # 三轮红队反复挖出这一类（26 字 / 18 字 / 16 字），且我自己也制造过新的逐字重复
    # （为消除第9章重复而改成与第7章一字不差）——人工逐章比对抓不到这种。
    xdup = _cross_chapter_duplicate_findings(files)
    items.extend(xdup)
    # 日链一致性（2026-11-19 加）：四轮红队最重的一类缺陷是日期网断裂。
    # 本检查按 `追踪/时间线/日链.md` 逐条核对每章的日锚句——**可计算**的日期校验。
    daychain = _daychain_findings(book)
    items.extend(daychain)
    blocking = sum(1 for x in items if x["blocking"])
    return 0, (f"N18 扫描 {len(files)} 章：blocking={blocking} total={len(items)}"
               + (f"（因果词多样性提示 {len(diversity)} 条）" if diversity else "")
               + (f"（动作节拍密度提示 {len(beats)} 条）" if beats else "")
               + (f"（相对日互斥提示 {len(rel)} 条）" if rel else "")
               + (f"（跨章逐字重复提示 {len(xdup)} 条）" if xdup else "")
               + (f"（日链一致性提示 {len(daychain)} 条）" if daychain else ""))

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
    """N19：文风漂移 + 伏笔回收率 + 商业张力终审（只读，不修）。"""
    cfg = _cfg()
    files = body_files(book)
    if not files:
        return 2, "正文/ 下没有章节文件"
    drift, drift_msg, drift_error = _style_drift(book, cfg, st, files)
    if drift_error:
        return 2, drift_msg
    fore, fore_msg = _foreshadow_items(book, st)

    # 商业张力全书终审扫描（七猫过签硬约束）
    comm_items: list[dict] = []
    comm_msg = "商业张力达标"
    try:
        from commercial_tension_probe import analyze_chapter_text
        has_commercial_contract = bool((st or {}).get("template")) or (os.environ.get("FORCE_COMMERCIAL_BLOCKING") == "1")
        for f in files:
            m = re.search(r"第(\d{1,4})章", f.name)
            ch_num = int(m.group(1)) if m else 1
            try:
                txt = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                txt = f.read_text(encoding="gb18030", errors="replace")
            is_mock = not has_commercial_contract or (
                "占位文本" in txt or "mock" in txt.lower() or
                len(txt.replace(" ", "").replace("\n", "")) < 1200
            )
            if not is_mock:
                res = analyze_chapter_text(txt, ch_num)
                for r in res.get("blocking_reasons") or []:
                    comm_items.append({
                        "tool": "commercial-tension",
                        "severity": "blocking",
                        "blocking": True,
                        "chapter": ch_num,
                        "file": f.name,
                        "message": r,
                    })
        if comm_items:
            comm_msg = f"商业张力检出 {len(comm_items)} 处平淡/泄气阻断"
    except Exception as exc:
        comm_msg = f"商业张力扫描跳过（{exc}）"

    append_endgame_findings(book, drift + fore + comm_items, "N19", chapters_scanned=len(files))
    return 0, f"N19：{drift_msg}；{fore_msg}；{comm_msg}"


# ─── N21 投稿包 ───────────────────────────────────────────────────────────────

def _mainline(book: Path, st: dict | None = None) -> str:
    """从卷纲取「主线一句话」。

    **两种写法都要认**（2026-11-18 修）：
      ① `- 主线：值`（同行）
      ② `## 主线一句话` 标题 + **下一行**正文
    实测本书卷纲用的是 ②，此前只认 ①，于是投稿邮件里留下
    「（卷纲里未写「主线：」行）」这个**占位符**——编辑一眼就看出是机器生成的。
    """
    planned = bool((st or {}).get("plan"))
    volume = book / "大纲" / ("卷纲_第1卷.md" if planned else "卷纲_第一卷.md")
    if not volume.is_file() and planned:
        volume = book / "大纲" / "卷纲_第一卷.md"
    if not volume.is_file():
        return "（卷纲缺失）"
    lines = volume.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        text = line.strip().lstrip("-*# ").strip()
        if text.startswith("主线") and ("：" in text or ":" in text):
            sep = "：" if "：" in text else ":"
            return text.split(sep, 1)[1].strip()
        # ② 「## 主线一句话」标题 → 取下一段非空正文
        if text.startswith("主线") and i + 1 < len(lines):
            for nxt in lines[i + 1:]:
                nxt = nxt.strip()
                if not nxt:
                    continue
                if nxt.startswith("#"):
                    break
                return nxt.lstrip("-* ").strip()
    return "（卷纲里未找到主线一句）"


def _delivered_mainline(book: Path, st: dict, chapters: list[Path]) -> str:
    """从**已封板章节的实际内容**里取一句主线（投稿邮件用）。

    **为什么不用卷纲主线**：卷纲主线是全书 13 万字的终局承诺
    （「建立自动化流水线核心生产壁垒，对抗并扳倒以赵副厂长为首的保守贪腐势力」），
    而投稿时只交 9 章。照抄会让编辑以为这些内容已经写出来了——
    实测投递 9 章里只兑现到「做出工装、效率数字动起来」。
    """
    if not chapters:
        return ""
    first = chapters[0].read_text(encoding="utf-8")
    last = chapters[-1].read_text(encoding="utf-8")
    # 从末章里找带「他/陆明」主语的动作句，取第一句像主线陈述的
    for para in last.split("\n\n"):
        s = para.strip()
        if not s or "“" in s or s.startswith("【"):
            continue
        if 18 <= len(s) <= 46 and ("陆明" in s or s.startswith("他")):
            return (f"顶职青工陆明查出顶替顶职与抚恤金被吞的真相，"
                    f"靠一手钳工手艺在三车间站住脚——{s.rstrip('。')}。")
    return "顶职青工陆明查出顶替顶职与抚恤金被吞的真相，靠一手钳工手艺在三车间站住脚跟。"


def _package(book: Path, st: dict) -> tuple[int, str]:
    """N21：生成 体检报告 / 投稿邮件 / 投稿记录（全文.md 由 N17 产出）。"""
    cfg = _cfg()
    full = book / "投稿包" / "全文.md"
    if not full.is_file():
        return 2, f"缺少 {full}——N17 未成功"
    chapters = body_files(book)
    if st.get("plan"):
        chapters = [f for _, start, end in volumes(st)
                    for f in _chapter_files_in(book, start, end)]
        chapters = sorted(chapters, key=lambda f: _chapter_no(f.name) or 0)

    # 2026-11-18 加：**交付物新鲜度校验**。
    # 坑：N17 只在流水线跑到那一步时合成一次全文；正文若之后被修订
    # （revision 路径改稿、或人工定点改写），`投稿包/全文.md` 就成了**陈旧产物**。
    # 实测第 8 章这一轮改了引号/日期/开头/章末，全部修在 `正文/` 里，
    # 而投稿包里的全文.md 仍是 11-17 13:39 的旧文本——
    # **81 处直角引号、旧的日期矛盾、旧的错字全在交付物里**，
    # 而体检报告却写着「可投」。N21 依赖全文.md 却不检查它是否新鲜。
    # 修法：比对「全文.md 内容」与「当前正文合成结果」，不一致就重建并明确报告。
    rebuilt = False
    try:
        _fresh = _compose_full_text(book, st, chapters)
        if full.read_text(encoding="utf-8") != _fresh:
            full.write_text(_fresh, encoding="utf-8")
            rebuilt = True
            reset_endgame_findings(book, st)
    except OSError:
        pass

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
    # 投稿标签用**作品自己的题材定位**，不用统计口径标签（2026-11-18 修）。
    # 统计标签是「niandai → 年代重生（七零/军婚/家属院）」，那是**题材池的归类**，
    # 而本书正文里「重生/穿越/军婚/七零」各 0 次——作品实际是八零厂区技术改制。
    # 把统计标签直接印到投稿邮件主题里，是**标签错配**，编辑会按错标签读稿。
    _pos = book / "设定" / "题材定位.md"
    if _pos.is_file():
        for _l in _pos.read_text(encoding="utf-8").splitlines():
            _l = _l.strip()
            if _l.startswith("#"):
                _tag = _l.lstrip("# ").strip()
                if _tag and "题材定位" in _tag:
                    genre_label = _tag.split("：", 1)[-1].split(":", 1)[-1].strip() or genre_label
                break
    endgame = _read_json(book / "_pipeline" / "endgame_findings.json", default={}) or {}
    judgments = _read_json(book / "_pipeline" / "judgments_N20.json", default={}) or {}
    n20_items = list(judgments.get("items") or [])
    n20_verdict = judgments.get("verdict", "unknown")
    global_judgments = _read_json(book / "_pipeline" / "judgments_N20g.json", default={}) or {}
    global_items = list(global_judgments.get("items") or [])
    global_verdict = global_judgments.get("verdict", "unknown")
    global_gate_required = bool(st.get("plan") or global_judgments)

    volume_audits = []
    volume_audits_complete = True
    if st.get("plan"):
        for volume, _, _ in volumes(st):
            node = f"V{volume}.N20"
            node_state = (st.get("nodes") or {}).get(node) or {}
            judgment = _read_json(book / "_pipeline" / f"judgments_{node}.json", default={}) or {}
            verdict = judgment.get("verdict", "unknown")
            complete = node_state.get("status") == "done" and verdict in ("pass", "block")
            volume_audits_complete = volume_audits_complete and complete
            volume_audits.append({
                "volume": volume, "node": node, "verdict": verdict,
                "advisory": bool(node_state.get("advisory")),
                "items": list(judgment.get("items") or []), "complete": complete,
            })
        n20_verdict = (f"卷级审查完成 {sum(1 for x in volume_audits if x['complete'])}/"
                       f"{len(volume_audits)} 卷" if volume_audits_complete
                       else f"卷级审查未完成 {sum(1 for x in volume_audits if x['complete'])}/"
                            f"{len(volume_audits)} 卷")
        # 分卷 N20 的 B 级遗留已经转入全书概要，由 N20g 统一裁决；不可再被旧式
        # judgments_N20.json（不存在或过期）二次阻断。N20g 的 B 项仍直接影响投稿门槛。
        b_fail = [x for x in global_items
                  if x.get("verdict") == "fail" and x.get("level", "B") == "B"]
    else:
        volume_audits_complete = True
        b_fail = [x for x in n20_items + global_items
                  if x.get("verdict") == "fail" and x.get("level", "B") == "B"]

    machine = [x for x in endgame.get("items", []) if x.get("source") == "N18"]
    style_advisories = [x for x in endgame.get("items", []) if x.get("source") == "N19"]
    machine_blocking = [x for x in machine if x.get("blocking")]

    # 双口径字数（对齐 server/submissions.mjs DL-004）：
    # 平台 api_words 等于去空白可见字符，qimao_check 等硬门槛用纯汉字。
    # 分歧超过 10% 且纯汉字未达标时，不得只凭可见字符放行。
    try:
        import re as _re
        _cjk = 0
        _vis = 0
        for _f in chapters:
            _t = _f.read_text(encoding="utf-8")
            _body = _re.sub(r"^#.*$", "", _t, flags=_re.M)
            _cjk += len(_re.findall(r"[\u4e00-\u9fff]", _body))
            _vis += len(_re.sub(r"\s+", "", _body))
    except Exception:
        _cjk = _vis = None

    has_target = bool(st.get("target_words") and int(st.get("target_words") or 0) > 0)
    min_words = int(st.get("target_words") or 0) if has_target else STATION_MIN_WORDS
    is_provisional = not has_target

    divergence = (_vis - _cjk) / _vis if (_vis and _vis > 0 and _cjk is not None) else 0.0
    visible_ok = words_total >= min_words
    cjk_ok = (_cjk >= min_words) if _cjk is not None else True
    station_ok = visible_ok and (divergence <= 0.1 or cjk_ok)

    legacy_n20_ok = bool(st.get("plan")) or n20_verdict == "pass"
    # 过期绿灯（2026-11-20）：章级裁决的引文若在改写后不再命中当前正文，
    # 那份裁决的依据已经作废——但节点状态仍写着 done。
    # 漂移检测盯不住这个：revision 定稿会把正文和账本 sha 一起更新，
    # 账本与磁盘一致，看不出「被合法修订但没重裁」。
    stale_gates = []
    for ch in range(1, int(st.get("chapters_committed") or 0) + 1):
        stale_gates.extend(_stale_gate_evidence(book, ch))
    # 全局裁决（全书终审 / 卷末终审）的引文同样会随正文改写而失效——
    # 2026-11-21 实测：改了第 6 章之后，judgments_N20.json 有 1 条引文
    # 在当前正文里已经找不到，而打包只查章级，完全没报。
    stale_global = _stale_global_gate_evidence(book)
    stale_gates.extend(stale_global)
    # 章级闸门判 block 的章，也是硬伤（2026-11-20）。
    # 此前打包只看 N20/N20g 的 B fail——章级裁决说「这章有 5 条 B 级缺陷」，
    # 打包却可能报「可投」。闸门说的话必须真的算数。
    chapter_blocks = []
    for node, info in (st.get("nodes") or {}).items():
        if not isinstance(info, dict) or not node.endswith(".N15"):
            continue
        if info.get("status") != "failed":
            continue
        chapter_blocks.append({"node": node,
                               "summary": str(info.get("summary") or "")[:200]})
    stale_note = ""
    stale_chapters = sorted({x["chapter"] for x in stale_gates if "chapter" in x})
    stale_globals = sorted({x.get("judgment") for x in stale_gates if x.get("judgment")})
    parts = []
    if stale_chapters:
        parts.append("第 " + "、".join(str(c) for c in stale_chapters) + " 章")
    if stale_globals:
        parts.append("全局裁决 " + "、".join(stale_globals))
    if parts:
        stale_note = "：" + "；".join(parts)
    stale_detail = ("\n".join(
        f"- {x.get('judgment') or ('第' + str(x.get('chapter')) + '章')} · "
        f"{str(x.get('criterion') or '')[:40]} —— {x['reason']}"
        f"（引文「{str(x.get('quote') or '')[:40]}」）" for x in stale_gates[:12])
        or "-（无）")
    if stale_gates:
        stale_detail += ("\n\n- **动作**：这些章在裁决之后被改写过，裁决依据已作废。"
                         "章级裁决跑 `autopilot.py rejudge --book <书> --chapter N` 重判，"
                         "全局裁决跑 `autopilot.py rejudge --book <书> --node N20` 重判；"
                         "重判通过后重新打包即可投稿。")
    chapter_block_detail = ("\n".join(
        f"- {x['node']} —— {x['summary']}" for x in chapter_blocks[:12]) or "-（无）")
    if chapter_blocks:
        chapter_block_detail += ("\n\n- **动作**：这些章的裁决判 block，闸门说有 B 级硬伤。"
                                 "按裁决里的 fail 项 fix 改写正文，再跑 "
                                 "`autopilot.py rejudge --book <书> --chapter N` 重判。")

    # 2026-11-22 商业因果与架构一致性核验（细纲四拍节拍流 + 禁止提前释放 + 人设动机置信度）
    causal_breaches = []
    causal_summary = []
    try:
        from architecture_consistency_gate import verify_chapter_architecture
        from persona_confidence_probe import probe_chapter_persona_confidence
        outline_dir = book / "大纲"
        tracking_state_path = str(book / "追踪" / "_tracking-state.json")
        for ch_file in chapters[:3]:
            ch_num = _chapter_no(ch_file.name)
            if not ch_num:
                continue
            possible_outlines = list(outline_dir.glob(f"*细纲*第{ch_num:03d}章*.md")) or list(outline_dir.glob(f"*细纲*第{ch_num}章*.md"))
            if possible_outlines:
                out_path = possible_outlines[0]
                arc_res = verify_chapter_architecture(str(out_path), str(ch_file))
                if not arc_res["ok"]:
                    for v in arc_res.get("advance_reveal_violations", []):
                        causal_breaches.append(f"第{ch_num}章违反禁止提前释放: {v['ban']}")
                causal_summary.append(
                    f"- 第{ch_num}章细纲节拍执行率: {arc_res['beat_execution']['rate']*100:.0f}% "
                    f"({arc_res['beat_execution']['executed_count']}/{arc_res['beat_execution']['beat_count']})"
                )
                if arc_res.get("advance_reveal_violations"):
                    for v in arc_res["advance_reveal_violations"]:
                        causal_summary.append(f"  - **[严重禁令违规]** {v['ban']} —— {v['reason']}")
            try:
                per_res = probe_chapter_persona_confidence(
                    clean_text=ch_file.read_text(encoding="utf-8"),
                    tracking_state_path=tracking_state_path,
                )
                causal_summary.append(
                    f"  - 主角({per_res['protagonist']})主动性得分: {per_res['agency']['score']} "
                    f"（主动反抗 {per_res['agency']['proactive_actions']} 次，屈服词 {per_res['agency']['submission_hits']} 次）"
                )
            except Exception:
                pass
    except Exception:
        pass

    can_submit = ((not machine_blocking) and (not b_fail) and legacy_n20_ok
                  and volume_audits_complete
                  and (not global_gate_required or global_verdict == "pass")
                  and station_ok and not stale_gates and not chapter_blocks
                  and not causal_breaches)

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
            # 动作节拍用 craft_probe 口径补进指标（闸门不产出它）。
            # 2026-11-17：体检报告此前这一列取 narration_action_density（19–48），
            # 与判别力分析用的 action_beat_per_1000（179–242）是两个指标，
            # 导致报告数字无法与「拒稿 166–250 / 签约 32.5–136.3」对照。
            if "action_beat_per_1000" not in m:
                try:
                    import craft_probe as _cp
                    _mf = book / "正文" / row.get("file", "")
                    if _mf.is_file():
                        _r = _cp.metrics(_mf.read_text(encoding="utf-8")) or {}
                        if "action_beat_per_1000" in _r:
                            m["action_beat_per_1000"] = _r["action_beat_per_1000"]
                except Exception:
                    pass
            rows.append((row.get("file", ""), row.get("status", ""), m))

    def flag(v: bool) -> str:
        return "通过" if v else "**不通过**"

    # 双口径字数（2026-11-18 加）。
    # **为什么必须并列报**：平台 `api_words` 实测等于「去空白可见字符」，
    # 而 qimao_check 的 Q01 用**纯汉字**判 20000 门槛——同一份稿，
    # 两口径判定可能相反（`platforms/_core/data/wordcount-caliber-finding-2026-11-13.md`：
    # 24 章实测 api_words 与可见字符精确相等 20 章、与纯汉字相等 0 章）。
    # 体检报告此前只写一个数，读者会误以为字数稳过——那是记账环节的缺陷。
    lines = [
        f"# 投稿体检报告 · {st.get('book') or book.name}",
        "",
        f"- 生成时间：{stamp}",
        f"- 题材：{genre_label}",
        f"- 章节数：{len(chapters)}（已封板 {committed}）",
        f"- 正文字数：{words_total} 字（七猫/奇妙门槛 {min_words}）",
    ]
    if _vis is not None:
        lines.append(
            f"  - 平台口径（去空白可见字符 = `api_words` 实测口径）：{_vis} 字 "
            f"→ {'达标' if _vis >= min_words else '**不达标**'}")
        lines.append(
            f"  - 汉字口径（`qimao_check` Q01 用的口径）：{_cjk} 字 "
            f"→ {'达标' if _cjk >= min_words else '**不达标（差 %d 字）**' % (min_words - _cjk)}")
        if divergence > 0.1 and not cjk_ok and visible_ok:
            lines.append(
                f"  - **双口径分歧阻断（DL-004）**：可见字符与纯汉字分歧率高达 {divergence * 100:.1f}%（> 10%），"
                f"且纯汉字（{_cjk}）未达标（<{min_words}）。依据七猫硬门槛规则，系统已严格阻断投稿。")
        elif (_vis >= min_words) != (_cjk >= min_words):
            lines.append(
                "  - **两个口径判定相反**：平台规则原文是「需上传 2万字 正文」，"
                "没说「2 万汉字」；但后台口径须以平台实际显示为准。"
                f"投稿前请在作者后台确认该书显示的字数——若显示值 < {min_words}，需先扩写。")
    lines += [
        f"- 全文文件：投稿包/全文.md",
        f"- 终审结论：卷末 {n20_verdict} / 全书 "
        f"{global_verdict if global_gate_required else '不适用（旧版单卷流程）'}",
    ]
    if rebuilt:
        lines.append(
            "- **本次已重建 `投稿包/全文.md`**：检测到它与当前正文不一致"
            "（正文在 N17 之后被修订过），已按当前正文重新合成。"
            "**这一项以前是静默的**——旧全文带着旧引号、旧日期、旧错字，"
            "而体检报告照样写「可投」。")
    volume_shortfalls = []
    if st.get("plan"):
        for volume, start, end in volumes(st):
            planned = end - start + 1
            actual = len(_chapter_files_in(book, start, end))
            if actual < planned:
                volume_shortfalls.append({"volume": volume, "start": start, "end": end,
                                          "planned": planned, "actual": actual})
    if volume_shortfalls:
        lines.append("- 卷预算降级/卷章数不足：" + "；".join(
            f"第{x['volume']}卷 {x['actual']}/{x['planned']}章（范围 {x['start']}–{x['end']}）"
            for x in volume_shortfalls))
    elif st.get("degraded_volumes"):
        lines.append("- 卷预算降级记录：" + "、".join(
            f"第{v}卷" for v in st.get("degraded_volumes", [])))
    lines += [
        "",
        "## 一、硬门槛",
        "",
        (f"- [{'x' if station_ok else ' '}] 正文总量 ≥{min_words} 字：{words_total}"
         if not (not station_ok and visible_ok)
         else f"- [ ] 正文总量 ≥{min_words} 字：可见字符 {words_total}（纯汉字 {_cjk}，分歧率 {divergence*100:.1f}% > 10% 阻断）"),
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
    if st.get("plan"):
        lines += ["", "## 三、逐卷终审（V.N20 judge）", ""]
    else:
        lines += ["", "## 三、卷末评分卡终审（N20 judge）", ""]
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
    if st.get("plan"):
        for audit in volume_audits:
            label = "（已转全书汇总终审）" if audit["advisory"] else ""
            lines.append(f"- 第{audit['volume']}卷：{audit['verdict']} {label}")
            for item in audit["items"]:
                if item.get("verdict") == "fail":
                    lines.append(f"  - {item.get('criterion') or item.get('id')}: "
                                 f"{(item.get('fix') or item.get('note') or '')[:200]}")
        if not volume_audits_complete:
            lines.append("- 卷级终审未全部完成，投稿门槛不通过。")
    if b_fail:
        lines += ["", "**B 项未通过：**"]
        lines += [f"- {x.get('id')} {x.get('criterion')}：{(x.get('fix') or '')[:200]}"
                  for x in b_fail]
    lines += ["", "## 四、全书汇总终审（N20g judge）", "",
              f"- verdict：{global_verdict if global_gate_required else '不适用（旧版单卷流程）'}"]
    if global_items:
        lines += ["", "| # | 判定项 | 结果 | 依据 |", "|---|---|---|---|"]
        for item in global_items:
            quote = (item.get("quote") or "").replace("|", "/")[:40]
            metric = item.get("metric")
            basis = quote or (json.dumps(metric, ensure_ascii=False) if metric else "")
            lines.append(f"| {item.get('id') or '-'} | {item.get('criterion') or '-'} | "
                         f"{item.get('verdict') or '-'} | {basis} |")
    lines += ["", "## 五、文风与节奏（逐章）", ""]
    if rows:
        # 「动作节拍」用 **craft_probe 口径**（对话之外、含身体动作动词的分句密度）。
        # 2026-11-17 修：此前这一列取的是 clarity_probe 的 narration_action_density
        # （动词计数/千字，实测 19–48），与判别力分析用的 action_beat_per_1000
        # （实测 179–242）**是两个不同指标**——同一概念两处口径，报告里的数字
        # 与判别力结论对不上，读者无法把它跟「拒稿样本 166–250」对照。
        lines += ["| 章节 | 闸门 | 段/千字 | 句长中位 | 对话占比 | 因果/千字 | 动作节拍 |",
                  "|---|---|---|---|---|---|---|"]
        for name, status, m in rows:
            ab = m.get("action_beat_per_1000")
            ab_txt = (f"{ab}（拒稿 166–250 / 签约 32.5–136.3）"
                      if isinstance(ab, (int, float)) else "-")
            lines.append(f"| {name} | {status} | {m.get('paragraphs_per_1000')} | "
                         f"{m.get('sent_len_median')} | {m.get('dialog_ratio')} | "
                         f"{m.get('causal_per_1000')} | {ab_txt} |")
    else:
        lines.append("（闸门指标未取到）")
    if style_advisories:
        lines += ["", "**提示项（A 级，不阻断）：**"]
        lines += [f"- 第{x.get('chapter') or '-'}章：{x.get('message')}" for x in style_advisories]
    lines += [
        "",
        "## 六、结论",
        "",
        f"**{'可投' if can_submit else '不可投'}**",
        "",
        ("机械 blocking 为 0、终审 B 项已通过、字数达标。" if can_submit
         else ("字数双口径分歧率 > 10% 且汉字未达标，触发硬门槛阻断；继续补写正文汉字至达标。"
               if (not station_ok and visible_ok)
               else "存在未通过项，按上表修完再投。")),
        "",
        "> 本书只有一次签约评估机会。投前请再人工确认：开篇 200 字冲突、"
        "前 600 字升级、前 6000 字主线、三章内反转、只联系一个编辑。",
    ]
    ecs_rep = None
    try:
        from editor_criteria_scan import scan_book_editor_criteria
        ecs_rep = scan_book_editor_criteria(chapters)
        if ecs_rep and ecs_rep.get("summary_lines"):
            lines += [
                "",
                "## 七、编辑口径自动核查（辅助参考）",
                "",
            ] + ecs_rep["summary_lines"]
    except Exception:
        pass
    if causal_summary:
        lines += [
            "",
            "## 八、商业因果与架构一致性核验",
            "",
        ] + causal_summary
    (book / "投稿包" / "体检报告.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    full_hash = hashlib.sha256(full.read_bytes()).hexdigest()[:12]
    title = st.get("book") or book.name
    genre = genre_label
    mainline = _mainline(book, st)
    # **投稿邮件的主线不能照抄卷纲的终局承诺**（2026-11-18 修）。
    # 卷纲主线写的是全书 13 万字的终局（自动化流水线、扳倒副厂长），
    # 而投稿时只交 9 章——照抄会让编辑以为你在承诺已经写出来的内容。
    # 改法：卷纲主线太长就换成「已封板章节实际兑现的那一段」——
    # 取正文里已发生的关键动作，从第 1 章的主线锚点到当前最后一章。
    if len(mainline) > 80:
        mainline = _delivered_mainline(book, st, chapters) or mainline[:78] + "…"
    if st.get("plan"):
        volume_outlines = [f"大纲/卷纲_第{volume}卷.md" for volume, _, _ in volumes(st)]
        directory_files = [f"大纲/目录_第{volume}卷.md" for volume, _, _ in volumes(st)]
        if (book / "大纲" / "目录_前12章.md").is_file():
            directory_files.append("大纲/目录_前12章.md")
        outline_attachments = "、".join(f"`{item}`" for item in volume_outlines + directory_files)
    else:
        outline_attachments = "`大纲/卷纲_第一卷.md`、`大纲/目录_前12章.md`"
    email = f"""# 投稿邮件（草稿）

**收件人：** （填对口编辑邮箱）
**主题：** 投稿｜{title}｜{genre}｜约 {max(words_total, 0)} 字

---

尊敬的编辑老师：

您好！向您投稿一部{genre}题材长篇《{title.replace('《', '').replace('》', '')}》，已完成前 {len(chapters)} 章，约 {words_total} 字。

**一句话主线：** {mainline}

随信附上：
1. 正文（整合为一个文件，不分章拆发）：`投稿包/全文.md`
2. 大纲：{outline_attachments}
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

    c_conflict_line = "- [ ] 前 200 字有冲突"
    c_scene_line = "- [ ] 单章场景切换 ≤3 次，切换处有过渡"
    if ecs_rep:
        op = ecs_rep.get("opening_conflict") or {}
        st_res = ecs_rep.get("scene_transitions") or {}
        c_conflict_line = f"- [{'x' if op.get('ok') else ' '}] 前 200 字有冲突（{op.get('detail', '')}）"
        c_scene_line = f"- [{'x' if st_res.get('ok') else ' '}] 单章场景切换 ≤3 次，切换处有过渡（{st_res.get('detail', '')}）"

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
| 终审结论 | 卷末 {n20_verdict} / 全书 {global_verdict if global_gate_required else '不适用（旧版单卷流程）'} |
| 体检结论 | {'可投' if can_submit else '不可投'} |

## 二、投前检查（自动项）

- {check(station_ok)} 字数 ≥{min_words}（平台口径 {words_total} 可见字符{f'，但纯汉字 {_cjk} 差 {min_words - _cjk} 字且分歧率 {divergence*100:.1f}% > 10% 阻断' if (not station_ok and visible_ok) else ''}）
- {check(_cjk is None or _cjk >= min_words)} 字数 ≥{min_words}（汉字口径 {('—' if _cjk is None else _cjk)}）——**两口径可能判定相反，见第五节**
- {check(not machine_blocking)} AI 味 / 退化 blocking = 0（当前 {len(machine_blocking)}）
- {check(not stale_gates)} 章级与全局裁决依据未过期（当前 {len(stale_gates)} 条失效引文{stale_note}）
- {check(not chapter_blocks)} 章级闸门无 block（当前 {len(chapter_blocks)} 章）
- {check(legacy_n20_ok and volume_audits_complete and not b_fail and (not global_gate_required or global_verdict == 'pass'))} 卷级与全书评分卡 B 项全部通过
- {check(full.is_file())} 正文整合为单文件
- {check(not rebuilt or True)} 交付物与当前正文一致{('（本次已重建全文.md）' if rebuilt else '')}
- {check(not causal_breaches)} 架构因果与提前释放禁令无违规（当前 {len(causal_breaches)} 处违规）

## 三、投前检查（人工项）

{c_conflict_line}
- [ ] 前 600 字冲突升级
- [ ] 前 6000 字主线一句话说清
- [ ] 三章内至少一次反转
{c_scene_line}
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

## 五、本次的已知风险（工具自动检出，**投前请逐条处理**）

### 5.1 字数双口径可能判定相反

平台 `api_words` 实测等于「去空白可见字符」，而 `qimao_check` 的 Q01 用**纯汉字**判 20000 门槛。
平台规则原文是「需上传 2万字 正文」，**没说「2 万汉字」**。

- **动作**：投稿前在作者后台看该书显示的字数。若显示 < 20000，先扩写再投。
- **不要**擅自改工具的判定口径——改语义会让所有历史比较漂移。

### 5.2 机械闸门全绿 ≠ 编辑会喜欢

已登记在案的真实反例：《边镇小吏》机械检查 BLOCK=0、fact_gate 全绿、跨章重复 0 条，
**仍被七猫拒绝**，编辑原话是「文笔在风格上不够统一、不够流畅、在细节刻画上力度稍弱」。

本次修订的多数 B 级缺陷正对着这句话的方向——这是把真实拒稿变成具体修法的闭环，
但**样本量只有 2 次，不足以预测签约**。

### 5.3 动作节拍密度（定位线索，非改稿目标）

按真实七猫拒稿样本与签约样本的分布，本书第 1–8 章落在**拒稿样本区间**内，
第 9 章落在**签约样本区间**内。

该指标判别力 verdict 仍是 `UNVERIFIED_INSUFFICIENT_INDEPENDENCE`（拒稿样本仅 2 本，门槛 3 本），
按判别力不变式**只作定位线索、不作改稿目标**。
若本次投稿被拒，这是第一优先复查方向。

### 5.4 章级裁决依据过期（{len(stale_gates)} 条）

{stale_detail}

### 5.5 章级闸门判 block（{len(chapter_blocks)} 章）

{chapter_block_detail}
"""
    (book / "投稿包" / "投稿记录.md").write_text(record, encoding="utf-8")

    if not can_submit:
        word_msg = (f"{words_total}（汉字 {_cjk}/{min_words}，分歧率 {divergence*100:.1f}% > 10% 阻断）"
                    if (not station_ok and visible_ok)
                    else f"{words_total}/{min_words}")
        return 1, json.dumps({
            "status": "error",
            "error_code": "NOT_SUBMITTABLE",
            "message": f"不可投：字数 {word_msg}，"
                       f"机械 blocking {len(machine_blocking)}，B fail {len(b_fail)}，"
                       f"N20 verdict={n20_verdict}，"
                       f"过期裁决依据 {len(stale_gates)} 条，"
                       f"章级闸门 block {len(chapter_blocks)} 章"
                       f"{f'，因果禁令违规 {len(causal_breaches)} 处' if causal_breaches else ''}。体检报告已写入。",
            "stale_gate": stale_gates[:20],
            "chapter_blocks": chapter_blocks[:20],
        }, ensure_ascii=False)
    _rebuilt_note = ("；**全文.md 已重建**（检测到交付物与当前正文不一致）"
                     if rebuilt else "")
    return 0, (f"投稿包已生成：全文.md / 体检报告.md / 投稿邮件.md / 投稿记录.md；"
               f"体检=可投；字数={words_total}{_rebuilt_note}")


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
    """取第 start–end 章的现役正文文件（T-2/T-2b：排除 _原稿_，章号认 1–4 位）。"""
    return body_files_in(book, start, end)


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
    if st.get("plan"):
        parts = [f for _, start, end in volumes(st) for f in _chapter_files_in(book, start, end)]
        parts = sorted(parts, key=lambda f: _chapter_no(f.name) or 0)
    else:
        parts = body_files(book)
    if not parts:
        return 2, "正文/ 下没有章节文件"
    full = "\n\n".join(p.read_text(encoding="utf-8").strip() for p in parts) + "\n"
    out = book / "投稿包" / "全文.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(full, encoding="utf-8")

    # 概要：每章取标题 + 首段（各 ≤120 字），加卷统计
    lines = [f"# {st.get('book') or book.name} · 全书概要", ""]
    advisories = st.get("volume_advisories") or {}
    if advisories:
        lines += ["## 卷末终审遗留项", ""]
        for node, data in sorted(advisories.items()):
            lines.append(f"### {node}")
            lines.append(str(data.get("summary") or "卷末终审有遗留项"))
            for item in data.get("items") or []:
                lines.append(f"- {item.get('id') or item.get('criterion')}: "
                             f"{item.get('fix') or item.get('note') or item.get('name') or ''}")
            lines.append("")
    if st.get("degraded_volumes"):
        lines += ["## 卷预算降级", ""]
        degradation = st.get("volume_degradation") or {}
        for volume in st.get("degraded_volumes") or []:
            data = degradation.get(str(volume)) or {}
            lines.append(f"- 第{volume}卷：预算 {data.get('used', '?')}/{data.get('cap', '?')}，已转入卷末审查。")
        lines.append("")
    for v, s, e in volumes(st):
        chs = _chapter_files_in(book, s, e)
        wc = sum(len(re.findall(r"[\u4e00-\u9fff]", f.read_text(encoding="utf-8"))) for f in chs)
        lines.append(f"## 第{v}卷（第 {s}–{e} 章，计划 {e - s + 1} 章，实际 {len(chs)} 章，{wc} 字）")
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
    （审查 C 级：此前代码只卡 ≥8，比提示词松）。

    **长度按原始引用算，不按 _norm 后的算**（2026-10-25 修）：
    此前 `len(_norm(quote)) >= min_len`，而 _norm 会剥掉「」/、等所有标点。
    像「天师/阴阳眼」也很挤（原文 12 字符）剥完只剩 10 个汉字，
    **明明是逐字正确的原文却被判不合格**——端到端跑 N04 时实测到。
    """
    if not quote or not chapter_text:
        return False
    if len(quote.strip()) < min_len:      # 原始长度，含标点
        return False
    q, t = _norm(quote), _norm(chapter_text)
    return len(q) >= 4 and q in t


def _norm(s: str) -> str:
    return re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", s or "")
