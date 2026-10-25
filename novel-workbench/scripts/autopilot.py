#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""autopilot.py — 一键小说工作台状态机 + 内联驱动器

设计要点（见 references/ 与 C:\\Users\\Wane-\\.workbuddy-ai\\plans\\cosmic-nebula-babbage-MnzvwCoC.md）：
  1. 瘦主控：主会话只调本脚本，禁读正文；next 输出含路径的 subagent_prompt
  2. 脚本内联：所有 script 节点由本脚本内部 subprocess.run 执行，LLM 物理上无法篡改参数
  3. step_token：一次性握手令牌（存 _pipeline/.step_token，与 state.json 分离），
     拦截无令牌调用；能读 state 的 agent 拿不到令牌（安全审查 A1 修复）
  4. 先审后封：commit 必须是单章生命周期的最后一个动作
  5. 进度只信磁盘：resume 重新扫盘重建，不信任内存

用法:
  autopilot.py init    --title 《xxx》 --genre niandai --target-words 20000 --book <dir>
  autopilot.py next    --book <dir>
  autopilot.py done    --book <dir> --node <id> --token <t> --evidence <path> [--evidence <path>...]
  autopilot.py skip    --book <dir> --node <id> --token <t> --reason "..."
  autopilot.py block   --book <dir> --reason "..."
  autopilot.py status  --book <dir>
  autopilot.py resume  --book <dir>
退出码: 0 正常 / 1 有阻断 / 2 用法或状态错误
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

# 主控进程的 stdout 直接交给宿主读取（subagent_prompt 全是中文）。
# Windows 管道默认按 locale(cp936) 编码，宿主按 UTF-8 解码就会乱码，必须强制。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# ─── 路径与配置 ───────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
CONFIG_PATH = SKILL_DIR / "config.json"
STATE_REL = "_pipeline/state.json"
# 令牌熵（审查 C1）：secrets 生成 8 hex（32bit）——random 可预测、原 24bit 爆破
# 成本低。注意令牌语义是"速度 bump"不是隔离（审查 A1），别再写成机械隔离。
TOKEN_HEX_LEN = 8

IRON_RULES = [
    "禁止向用户提问或请求确认——分歧按 references/decisions.md 默认决策表处理，记入 pending_questions 后继续",
    "每步必须调 autopilot.py next 拿指令，不许自行决定下一步",
    "只把 subagent_prompt 转交 subagent；done_cmd 与 step_token 绝不出现在 subagent 可见内容里",
    "subagent 只准返回 ≤100 字回执，禁止返回大段正文",
    "只有脚本硬失败 / 闸门 BLOCK / 用户明确喊停 三种情况允许停",
    "spawn subagent 必须按 subagent_model 传 model 参数——writer 用 default/lite，judge 用 reasoning，不得统一用默认值",
]


def config_path() -> Path:
    """发版时由 Node 写入 <dataDir>/pipeline.json，并通过 WORKBENCH_CONFIG 指定。"""
    override = os.environ.get("WORKBENCH_CONFIG")
    return Path(override) if override else CONFIG_PATH


def load_config() -> dict:
    path = config_path()
    if not path.is_file():
        sys.exit(f"ERROR: 找不到配置文件 {path}（可用 WORKBENCH_CONFIG 指定）")
    data = json.loads(path.read_text(encoding="utf-8"))
    required = ("PYTHON", "NODE", "STORY_LONG_WRITE", "QIMAO_PLATFORM_DIR",
                "STORYCTL", "TRACKING_COMMIT", "CHECK_OUTLINE", "GATE_RUNNER")
    missing = [key for key in required if not data.get(key)]
    if missing:
        sys.exit(f"ERROR: {path} 缺少配置键：{', '.join(missing)}")
    return data


CFG = load_config()
PYTHON = CFG["PYTHON"]
NODE_BIN = CFG.get("NODE", "")


def _genre_bounds_text(st: dict, plats: dict, name: str) -> str:
    """把本书 genre 的 p05-p95 区间格式化成 prompt 文字。

    为什么要有：N12 的「生死线」此前写死七猫年代文数字
    （因果 1.0-6.0 / 动作 <=41 / 段 33-54 / 字数 >=2000），
    而纵横都市实测是因果 0.05-7.49 / 动作 12.83-46.53 / 段 24.79-74.78 / 中位 1925 字。
    writer 冲七猫的目标、闸门按纵横查，必然对不上。
    """
    genre = (st or {}).get("genre") or ""
    gb = ((plats.get(name) or {}).get("GENRE_BASE")) or ""
    try:
        doc = json.loads(Path(gb).read_text(encoding="utf-8")) if gb else {}
    except Exception:
        doc = {}
    blk = ((doc.get("baselines") or {}).get(genre)) or {}
    if not blk:
        return "  （未取到题材基准，按 N13 闸门实测反馈调整）"
    labels = [("visible_chars", "单章字数"),
              ("paragraphs_per_1000", "段/千字"),
              ("sent_len_median", "句长中位"),
              ("dialog_ratio", "对话占比"),
              ("causal_per_1000", "因果连接/千字"),
              ("narration_action_density", "叙述动作词/千字"),
              ("question_per_1000", "问句/千字")]
    out = []
    for k, cn in labels:
        v = blk.get(k)
        if isinstance(v, list) and len(v) == 2:
            out.append(f"  \u00b7 {cn} **{v[0]}\u2013{v[1]}**")
    if not out:
        return "  （题材基准缺字段）"
    return f"（{name} / {genre}，p05\u2013p95 实测）\n" + "\n".join(out)


def render_prompt(template: str, book: Path, st: dict | None = None) -> str:
    """渲染 prompt 模板里的路径占位符。

    节点表只存模板（与具体书无关）；{QIMAO} / {STORY_LONG_WRITE} / {BOOK}
    在 next 输出时才替换成真实绝对路径——换一本书跑，同一份节点表不用改。

    **{QIMAO} 按书所属平台解析**（2026-10-25 修）：
    此前无论什么书都替换成七猫目录，导致纵横书读七猫的判据文档、跑七猫的闸门。
    {PLATFORM} 给平台名（用于文案里的「七猫/纵横」），{PLATFORM_DIR} 同 {QIMAO}。
    """
    st = st or {}
    name = st.get("platform") or "qimao"
    plats = CFG.get("PLATFORMS") or {}
    pdir = (plats.get(name) or {}).get("DIR") or CFG["QIMAO_PLATFORM_DIR"]
    label = {"qimao": "七猫", "zongheng": "纵横"}.get(name, name)
    # 归一成 / 分隔，避免 "F:////小说////platforms/zongheng" 这类混合斜杠
    pdir = str(pdir).replace("\\", "/")
    bounds = _genre_bounds_text(st, plats, name)
    return (template
            .replace("{QIMAO}", pdir)
            .replace("{PLATFORM_DIR}", pdir)
            .replace("{PLATFORM}", label)
            .replace("{GENRE_BOUNDS}", bounds)
            .replace("{STORY_LONG_WRITE}", CFG["STORY_LONG_WRITE"])
            .replace("{SKILL}", str(SKILL_DIR))
            .replace("{BOOK}", str(book).replace("\\", "/")))


def _genre_bounds_text(st: dict, plats: dict, name: str) -> str:
    """把本书 genre 的 p05-p95 区间格式化成 prompt 文字。

    为什么要有：N12 的「生死线」此前写死七猫年代文数字
    （因果 1.0-6.0 / 动作 <=41 / 段 33-54 / 字数 >=2000），
    而纵横都市实测是因果 0.05-7.49 / 动作 12.83-46.53 / 段 24.79-74.78 / 中位 1925 字。
    writer 冲七猫的目标、闸门按纵横查，必然对不上。
    """
    genre = (st or {}).get("genre") or ""
    gb = ((plats.get(name) or {}).get("GENRE_BASE")) or ""
    try:
        doc = json.loads(Path(gb).read_text(encoding="utf-8")) if gb else {}
    except Exception:
        doc = {}
    blk = ((doc.get("baselines") or {}).get(genre)) or {}
    if not blk:
        return "  （未取到题材基准，按 N13 闸门实测反馈调整）"
    labels = [("visible_chars", "单章字数"),
              ("paragraphs_per_1000", "段/千字"),
              ("sent_len_median", "句长中位"),
              ("dialog_ratio", "对话占比"),
              ("causal_per_1000", "因果连接/千字"),
              ("narration_action_density", "叙述动作词/千字"),
              ("question_per_1000", "问句/千字")]
    out = []
    for k, cn in labels:
        v = blk.get(k)
        if isinstance(v, list) and len(v) == 2:
            out.append(f"  · {cn} **{v[0]}–{v[1]}**")
    if not out:
        return "  （题材基准缺字段）"
    return f"（{name} / {genre}，p05–p95 实测）\n" + "\n".join(out)


def utf8_env() -> dict:
    """暗坑7：Windows 下 Python 默认 stdio 用 GBK，遇中文必崩。"""
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ─── 状态读写 ─────────────────────────────────────────────────────────────────

def state_path(book: Path) -> Path:
    return book / STATE_REL


def load_state(book: Path) -> dict:
    p = state_path(book)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: state.json 损坏：{e}")


def save_state(book: Path, st: dict) -> None:
    p = state_path(book)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 旧版把令牌写在 state 里；现在令牌在 _pipeline/.step_token，落盘时清掉残留字段
    st.pop("step_token", None)
    st.pop("step_token_node", None)
    _atomic_write(p, json.dumps(st, ensure_ascii=False, indent=1) + "\n")


def _atomic_write(p: Path, text: str) -> None:
    """原子写：临时文件 + replace，避免中断写出半截文件（C 级：manifest/relay 同理）。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


def write_workbench_manifest(book: Path) -> None:
    """给 Dashboard 留一份可执行路径清单：它不读 config.json，只认这本书的清单。"""
    manifest = {
        "schema_version": 1,
        "python": CFG["PYTHON"],
        "autopilot": str(HERE / "autopilot.py"),
        "gate_runner": CFG.get("GATE_RUNNER", ""),
        "platform": CFG.get("QIMAO_PLATFORM_DIR", ""),
    }
    p = book / "_pipeline" / "workbench.json"
    if p.is_file():
        try:
            if json.loads(p.read_text(encoding="utf-8")) == manifest:
                return
        except json.JSONDecodeError:
            pass
    _atomic_write(p, json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def norm_cjk(s: str) -> str:
    """暗坑6：原文引用归一化——只留中英文字符与数字，避免标点差异误杀。"""
    return re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", s)


# ─── 令牌 ─────────────────────────────────────────────────────────────────────
# 令牌与 state.json 分离：安全审查（adversarial-review A1）实证，subagent 读
# state.json 即可拿到明文令牌并越权 done。令牌改存 _pipeline/.step_token 独立
# 文件，state.json 不再含任何令牌字段。

def new_token() -> str:
    return secrets.token_hex(TOKEN_HEX_LEN // 2)


def token_path(book: Path) -> Path:
    return book / "_pipeline" / ".step_token"


def write_token(book: Path, token: str, node: str) -> None:
    p = token_path(book)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps({"token": token, "node": node, "issued_at": now()},
                              ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def read_token(book: Path) -> dict:
    p = token_path(book)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def clear_token(book: Path) -> None:
    try:
        token_path(book).unlink()
    except FileNotFoundError:
        pass


def token_ok(book: Path, node: str, token: str) -> bool:
    held = read_token(book)
    return bool(token) and held.get("token") == token and held.get("node") == node


NEXT_LOCK_TTL = 600


def acquire_next_lock(book: Path) -> Path | None:
    """跨驱动互斥：UI 与对话编排者同时 next 时只放行一个。"""
    path = book / "_pipeline" / ".next.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            return path
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age <= NEXT_LOCK_TTL:
                return None
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    return None


def release_next_lock(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


# ─── 节点表（PART 3 填充） ────────────────────────────────────────────────────

from pipeline_spec import (          # noqa: E402
    GLOBAL_NODES, chapter_nodes, volume_nodes, next_cursor, first_cursor, milestones, vol_of, span, vol_size,
)

# ─── 内联脚本执行（PART 2 填充） ──────────────────────────────────────────────

from inline_runner import (          # noqa: E402
    run_node_script, verify_quote, chapter_has_blocking, append_chapter_findings,
    snapshot_chapter, snapshot_body, extract_json,
    record_snapshot_manifest, restore_best_snapshot,
)


# ─── 子命令 ───────────────────────────────────────────────────────────────────

def cmd_init(a) -> int:
    book = Path(a.book).resolve()
    if load_state(book):
        print(json.dumps({"error": "already_initialized",
                          "hint": "用 resume 续跑，或删除 _pipeline/ 后重来"}, ensure_ascii=False))
        return 2
    for sub in ("正文", "大纲", "设定", "追踪", "投稿包", "_pipeline"):
        (book / sub).mkdir(parents=True, exist_ok=True)
    st = {
        "schema_version": 2,
        "book": a.title or book.name,
        "genre": a.genre,
        "target_words": int(a.target_words),
        "max_chapters": int(a.max_chapters),
        # 平台与卷计划：老书没有这两个键，代码里一律用 .get() 回落（七猫兼容）
        "platform": getattr(a, "platform", None) or "qimao",
        "plan": ({"vol_size": int(a.vol_size), "vol_llm_cap": int(getattr(a, "vol_llm_cap", 0) or 80)} if getattr(a, "vol_size", 0) else None),
        "uncontrollable": [],
        "created_at": now(),
        "current_chapter": 0,
        "chapters_committed": 0,
        "words_total": 0,
        "cursor": first_cursor(),
        "nodes": {},
        "decisions_taken": [],
        "pending_questions": [],
        "blocked_reason": None,
        "relay_count": 0,
    }
    save_state(book, st)
    write_workbench_manifest(book)
    print(json.dumps({"ok": True, "book": st["book"], "cursor": st["cursor"],
                      "nodes_total": len(GLOBAL_NODES)}, ensure_ascii=False))
    return 0


def _heal_from_tracking(book: Path, st: dict) -> list[str]:
    """以 追踪/_tracking-state.json 为账本，修正 state.json 里的派生字段。

    **为什么要有**：words_total 和 chapters_committed 都是 tracking 账本的派生值，
    但写回 state 的路径不止一条（N16 封板、崩溃窗口重跑、人工 resume）。
    端到端实跑时出现过「封板 1 章但 words_total=0」——状态漂移后
    字数检查、卷预算、里程碑判定全部失真。

    账本是真值，state 是缓存。这里单向以账本覆盖，并返回修正说明。
    """
    notes: list[str] = []
    try:
        tk = json.loads((book / "追踪" / "_tracking-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return notes
    if not isinstance(tk, dict):
        return notes

    records = tk.get("wordcount_records") or {}
    expected_words = sum(int(v.get("actual") or 0) for v in records.values()
                         if isinstance(v, dict))
    if expected_words != int(st.get("words_total") or 0):
        notes.append(f"words_total {st.get('words_total')} → {expected_words}")
        st["words_total"] = expected_words

    try:
        last = int(tk.get("last_committed_chapter") or 0)
    except (TypeError, ValueError):
        last = 0
    if last > int(st.get("chapters_committed") or 0):
        notes.append(f"chapters_committed {st.get('chapters_committed')} → {last}")
        st["chapters_committed"] = last
        # **不要动 current_chapter**：它是游标派生的（C{n} 节点用 current_chapter+1），
        # 由 next_cursor 推进。这里改它会让 C1.N14 解析成 C2.N14 而失效。
        # 2026-10-25 实测踩过：自愈把 current_chapter 从 0 改成 1，
        # cursor 仍是 C1.N14，cursor_spec 返回 None，next 直接报 done。

    # 游标落后于账本：第 n 章已经封板（账本有记录），游标却还在改它。
    # 这是崩溃/人工 resume 留下的悬空态，按账本往前推。
    # **例外**：N14 改写后的复检态（recheck_chapter 已置位）必须保留——那时
    # 账本里 last 仍是上一章，游标 C{n}.N13 是正常的「改完待复检」，前推会把
    # 平台闸门复检整段吞掉（python_contracts.test_n13_recheck_routes 回归）。
    cur = st.get("cursor") or ""
    m = re.match(r"C(\d+)\.", cur)
    if m and last >= 1 and st.get("recheck_chapter") is None:
        n = int(m.group(1))
        if n <= last:
            st["current_chapter"] = last
            if last >= int(st.get("max_chapters") or 0):
                st["cursor"] = "N17"
            else:
                st["cursor"] = f"C{last + 1}.N09"
            notes.append(f"游标 {cur} 落后于已封板第 {last} 章 → {st['cursor']}")

    if notes:
        save_state(book, st)
    return notes


_NODE_ID_RE = re.compile(r"^(?:N\d+[a-z]?|V\d+\.N\d+[a-z]?|C\d+\.N\d+)$")
_BOOK_SUBDIRS = ("正文", "大纲", "设定", "追踪", "投稿包", "_pipeline", ".story")


def cmd_doctor(a) -> int:
    """静态契约自检：节点表的配置漂移不该靠人肉发现。

    **为什么要有**（2026-10-26）：发现两个**确定性死路**——
    `V{v}.N20` 的 prompt 要求 `source 填 投稿包/卷{v}_全文.md`，
    而 `judgment_policy` 只允许 `正文/`；`N20g` 的 prompt 要求
    `source 填 投稿包/全书概要.md`，而 policy 落到 `sources: []`。
    两者都会让该节点**永远**过不了 done，但单测和端到端都只在跑到那一步才暴露。

    这里把「prompt 说的」和「policy 允许的」做机械比对，同类漂移以后跑一次就能抓。
    """
    from commands import _source_allowed

    book = Path(a.book).resolve()
    st = load_state(book)
    if not st:
        print(json.dumps({"error": "not_initialized"}, ensure_ascii=False))
        return 2

    issues: list[dict] = []
    checked = skipped = 0

    specs = list(GLOBAL_NODES.values())
    vs = vol_size(st) or 0
    total = int(st.get("max_chapters") or 0)
    nvol = max(1, (total + vs - 1) // vs) if vs else 1
    for v in range(1, nvol + 1):
        try:
            specs.extend(volume_nodes(st, book, v).values())
        except Exception as exc:                       # 节点表本身构造不出来也是问题
            issues.append({"node": f"V{v}.*", "kind": "volume_nodes_failed",
                           "detail": str(exc)[:200]})
    try:
        specs.extend(chapter_nodes(st, book, 1).values())
    except Exception as exc:
        issues.append({"node": "C1.*", "kind": "chapter_nodes_failed",
                       "detail": str(exc)[:200]})

    seen: dict[str, str] = {}
    for spec in specs:
        nid = spec.get("id") or ""
        checked += 1
        if not _NODE_ID_RE.match(nid):
            issues.append({"node": nid, "kind": "bad_node_id",
                           "detail": "节点 id 不符合 N / V{v}.N / C{n}.N 形态"})
            continue
        if nid in seen:
            issues.append({"node": nid, "kind": "duplicate_node_id",
                           "detail": f"与「{seen[nid]}」重名"})
        seen[nid] = spec.get("name") or ""

        ev = spec.get("evidence") or ""
        if ev and "/" in ev:
            head = ev.split("/", 1)[0]
            if head not in _BOOK_SUBDIRS:
                issues.append({"node": nid, "kind": "bad_evidence_dir",
                               "detail": f"evidence 前缀 {head!r} 不是书的子目录"})

        if spec.get("kind") != "verify":
            continue

        srcs = (judgment_policy(nid).get("sources") or [])
        if not srcs:
            issues.append({"node": nid, "kind": "empty_policy",
                           "detail": "verify 节点没有任何允许的 source，任何文本项都会被拒"})
            continue
        m = re.search(r"source\s*填\s*([^\s，。；,;）)]+)", spec.get("prompt") or "")
        if not m:
            skipped += 1
            continue
        declared = m.group(1)
        if not _source_allowed(declared, srcs):
            issues.append({"node": nid, "kind": "source_mismatch",
                           "detail": f"prompt 要求 source={declared!r}，"
                                     f"但 policy 只允许 {srcs} → 该节点永远过不了 done"})

    print(json.dumps({"book": st.get("book"), "checked": checked,
                      "skipped_no_source": skipped,
                      "ok": not issues, "issues": issues},
                     ensure_ascii=False, indent=1))
    return 0 if not issues else 1


def cmd_status(a) -> int:
    book = Path(a.book).resolve()
    st = load_state(book)
    if not st:
        print(json.dumps({"error": "not_initialized"}, ensure_ascii=False))
        return 2
    write_workbench_manifest(book)
    nodes = st["nodes"]
    done = sum(1 for v in nodes.values() if v.get("status") == "done")
    # 审查 B7：words_total 与 tracking 账本漂移时显式报告（旧书的 state 可能
    # 是修复前数据），不静默。
    drift = None
    try:
        tracking = json.loads((book / "追踪" / "_tracking-state.json").read_text(encoding="utf-8"))
        records = tracking.get("wordcount_records") or {}
        expected = sum(int(v.get("actual") or 0) for v in records.values()
                       if isinstance(v, dict))
        if expected != st["words_total"]:
            drift = {"state": st["words_total"], "tracking": expected}
    except (OSError, ValueError):
        pass
    print(json.dumps({
        "book": st["book"], "genre": st["genre"],
        "cursor": st["cursor"], "current_chapter": st["current_chapter"],
        "chapters_committed": st["chapters_committed"], "words_total": st["words_total"],
        "target_words": st["target_words"], "max_chapters": st["max_chapters"],
        "nodes_done": done, "nodes_recorded": len(nodes),
        "blocked_reason": st["blocked_reason"],
        "pending_questions": len(st["pending_questions"]),
        "fix_rounds": st.get("fix_rounds") or {},
        "global_fix_rounds": st.get("global_fix_rounds") or {},
        "n20_rounds": st.get("n20_rounds", 0),
        "llm_rounds": st.get("llm_rounds", 0),
        "words_total_drift": drift,
    }, ensure_ascii=False))
    return 0


def evidence_hits(book: Path, pattern: str) -> list[Path]:
    if "*" not in pattern:
        return [book / pattern] if (book / pattern).is_file() else []
    return sorted(book.glob(pattern))


def resolve_evidence(book: Path, patterns: list[str]) -> tuple[list[str], list[str]]:
    """done 前的严格解析：通配符必须唯一命中；返回（相对路径, 错误）。"""
    out: list[str] = []
    errors: list[str] = []
    for pattern in patterns:
        if "*" not in pattern:
            out.append(pattern)
            continue
        hits = evidence_hits(book, pattern)
        if len(hits) == 1:
            out.append(str(hits[0].relative_to(book)))
        elif not hits:
            errors.append(f"证据通配符无匹配：{pattern}")
        else:
            errors.append(f"证据通配符匹配到 {len(hits)} 个文件（应唯一）：{pattern}")
    return out, errors


def _resolve_evidence(book: Path, patterns: list[str]) -> list[str]:
    """next 时的宽松解析：尚无产出时保留模式串，交给 writer；唯一命中则展开。"""
    out: list[str] = []
    for pattern in patterns:
        hits = evidence_hits(book, pattern) if "*" in pattern else []
        if len(hits) == 1:
            out.append(str(hits[0].relative_to(book)))
        else:
            out.append(pattern)
    return out


def _auto_skip_reason(book: Path, st: dict, spec: dict) -> str | None:
    """确定性跳过：N14 无 B 级 findings、N15 非里程碑章。"""
    nid = spec["id"]
    if nid.endswith(".N14"):
        ch = st["current_chapter"] + 1
        if not chapter_has_blocking(book, ch):
            return "N13 无 B 级 findings，无需定点改写"
    if nid.endswith(".N15"):
        ch = st["current_chapter"] + 1
        _ms = milestones(st)
        if ch not in _ms:
            return f"第{ch}章非里程碑章（只在 {_ms} 做阶段审查）"
    return None


# 计入预算的节点类型（llm 创作 + judge 审查；script 节点不花 chat 轮）
_BILLABLE = {"N07", "N08", "N09d", "N09", "N12", "N14", "N15", "N20", "N20f", "N20g"}


def _vol_llm_used(st: dict) -> int:
    """当前卷已消耗的 llm/verify 节点数（近似 chat 轮消耗）。"""
    if not st.get("plan"):
        return 0
    v = vol_of(st, st["current_chapter"] + 1)
    n = 0
    for nid, rec in (st.get("nodes") or {}).items():
        if not isinstance(rec, dict) or rec.get("status") != "done":
            continue
        stage = nid.split(".", 1)[-1]
        if stage not in _BILLABLE:
            continue
        if nid.startswith("V"):
            if int(nid[1:].split(".", 1)[0]) == v:
                n += 1
        elif nid.startswith("C"):
            if vol_of(st, int(nid[1:].split(".", 1)[0])) == v:
                n += 1
    return n


def vol_budget(st: dict) -> int:
    return int((st.get("plan") or {}).get("vol_llm_cap", 80))


def _write_relay(book: Path, st: dict, reason: str = "定期接力") -> None:
    _vol = ""
    if st.get("plan"):
        _v = vol_of(st, st["chapters_committed"] + 1)
        _s, _e = span(st, _v)
        _vol = (f"- 当前卷：第 {_v} 卷（第 {_s}–{_e} 章），本卷已用 "
                f"{_vol_llm_used(st)}/{vol_budget(st)} 预算\n")
    content = (
        "# 接力口令\n\n"
        f"- 书：{st['book']}\n"
        f"- 已封板：{st['chapters_committed']} 章 / {st['words_total']} 字\n"
        f"- 当前 cursor：`{st['cursor']}`\n"
        f"- 停止原因：{reason}\n"
        f"{_vol}"
        f"- 续跑命令：\n\n"
        "```\n"
        f'"{PYTHON}" "{HERE / "autopilot.py"}" next --book "{book}"\n'
        "```\n\n"
        "> 人工粘贴上一条命令即可接管；不要手动改 state.json。\n"
    )
    p = book / "_pipeline" / "relay.md"
    _atomic_write(p, content)


def _recoverable_commit_error(out: str) -> bool:
    """N16 封板失败是否可由 N14 修正文/事务解决。

    事务缺失是 writer 没在 N12 按 shapes 写 tracking.json，属于可定点修复；
    工具不可用（node 缺失等）与环境相关，重试再多次也没用。
    """
    if "TOOL_UNAVAILABLE" in out or "NODE_REQUIRED" in out or "tool_unavailable" in out:
        return False
    data = extract_json(out)
    if isinstance(data, dict) and data.get("status") == "error":
        return data.get("error_code") != "TOOL_UNAVAILABLE"
    return "找不到追踪事务" in out


def cmd_relay(a) -> int:
    """只写接力口令并打印，不推进游标。runtime 预算耗尽时调它。"""
    book = Path(a.book).resolve()
    st = load_state(book)
    _write_relay(book, st, getattr(a, "reason", None) or "手动/预算耗尽")
    print((book / "_pipeline" / "relay.md").read_text(encoding="utf-8"))
    return 0


def cmd_next(a) -> int:
    book = Path(a.book).resolve()
    # 每次推进前先以账本校正派生字段（漂移会让字数检查/卷预算/里程碑全失真）
    _st0 = load_state(book)
    if _st0:
        _heal_from_tracking(book, _st0)
    st = load_state(book)
    if not st:
        print(json.dumps({"error": "not_initialized"}, ensure_ascii=False)); return 2
    write_workbench_manifest(book)
    if st["blocked_reason"]:
        print(json.dumps({"error": "blocked", "reason": st["blocked_reason"],
                          "hint": "修复后调 resume"}, ensure_ascii=False)); return 2

    # 内联顺延：把所有连续 script 节点一次跑完，直到撞上下一个 llm/verify
    guard = 0
    while True:
        guard += 1
        if guard > 200:
            print(json.dumps({"error": "inline_loop_guard"}, ensure_ascii=False)); return 2
        ch = st["current_chapter"] + 1
        spec = cursor_spec(st, book)
        if spec is None:
            print(json.dumps({"done": True, "chapters_committed": st["chapters_committed"],
                              "words_total": st["words_total"]}, ensure_ascii=False))
            return 0
        if spec["kind"] != "script":
            reason = _auto_skip_reason(book, st, spec)
            if reason:
                clear_token(book)
                st["nodes"][spec["id"]] = {"status": "skipped", "at": now(), "reason": reason}
                st["cursor"] = next_cursor(st)
                save_state(book, st)
                continue
            break
        rc, out = run_node_script(book, st, spec)
        if rc != 0:
            # N16 封板复检失败（质量/字数/事务）→ 退章内定点改写，而不是整条链停死
            if spec["id"].endswith(".N16") and _recoverable_commit_error(out):
                ch = st["current_chapter"] + 1
                data = extract_json(out) or {}
                code = data.get("error_code") or "COMMIT_FAILED"
                append_chapter_findings(book, ch, [{
                    "id": code, "criterion": code,
                    "name": data.get("message") or "封板复检失败",
                    "level": "B", "blocking": True, "source": "N16",
                    "note": data.get("message") or out[-300:],
                }], "N16")
                rounds = int(st.get("fix_rounds", {}).get(str(ch), 0))
                if rounds >= 2:
                    st["blocked_reason"] = f"N16 连续 {rounds + 1} 轮封板失败：{code}"
                    st["nodes"][spec["id"]] = {"status": "failed", "at": now(), "output": out[-2000:]}
                    save_state(book, st)
                    print(json.dumps({"error": "script_failed", "node": spec["id"],
                                      "output": out[-800:]}, ensure_ascii=False))
                    return 1
                st["fix_rounds"] = {**(st.get("fix_rounds") or {}), str(ch): rounds + 1}
                st["nodes"][spec["id"]] = {"status": "failed", "at": now(), "output": out[-2000:]}
                st["cursor"] = f"C{ch}.N14"
                save_state(book, st)
                continue
            st["blocked_reason"] = f"{spec['id']} 内联脚本失败：{out[-400:]}"
            st["nodes"][spec["id"]] = {"status": "failed", "at": now(), "output": out[-2000:]}
            save_state(book, st)
            print(json.dumps({"error": "script_failed", "node": spec["id"],
                              "output": out[-800:]}, ensure_ascii=False))
            return 1
        st["nodes"][spec["id"]] = {"status": "done", "at": now(), "output": out[-1500:]}
        if spec["id"].endswith(".N13") and st.get("recheck_chapter") == ch:
            # 审查 B1：N14 改写后的复检。通过 → N15；仍 blocking → 记一轮
            # fix_rounds 回 N14（与 N15/N16 失败路径同一预算），超预算则回滚
            # Blocker 最少的快照后终停（plan 5.2.2 + 补丁4）。
            st.pop("recheck_chapter", None)
            if chapter_has_blocking(book, ch):
                rounds = int(st.get("fix_rounds", {}).get(str(ch), 0))
                if rounds >= 2:
                    restored = restore_best_snapshot(book, ch)
                    note = f"；已回滚至快照 {restored}" if restored else ""
                    st["blocked_reason"] = f"C{ch}.N14 复检第 {rounds + 1} 轮仍 blocking{note}"
                    st["nodes"][spec["id"]] = {"status": "failed", "at": now(),
                                                "output": out[-2000:]}
                    save_state(book, st)
                    print(json.dumps({"error": "recheck_blocked", "node": spec["id"],
                                      "reason": st["blocked_reason"]}, ensure_ascii=False))
                    return 1
                st["fix_rounds"] = {**(st.get("fix_rounds") or {}), str(ch): rounds + 1}
                st["cursor"] = f"C{ch}.N14"
                save_state(book, st)
                continue
            st["cursor"] = f"C{ch}.N15"
        else:
            st["cursor"] = next_cursor(st)
        if spec["id"].endswith(".N16"):
            _done = st["chapters_committed"]
            if st.get("plan"):
                # 卷边界才写接力口令（每 22 章一次，不是每 3 章）
                if _done and _done % vol_size(st) == 0:
                    _write_relay(book, st, f"第 {vol_of(st, _done)} 卷写完")
            elif _done % 3 == 0:
                _write_relay(book, st)
        save_state(book, st)

    # 卷预算闸门：超了就在卷内停下，写接力口令，不发令牌
    if st.get("plan") and spec and spec.get("kind") in ("llm", "verify"):
        used, cap = _vol_llm_used(st), vol_budget(st)
        if used >= cap:
            _write_relay(book, st, f"第 {vol_of(st, st['current_chapter'] + 1)} 卷预算用尽（{used}/{cap}）")
            save_state(book, st)
            print(json.dumps({"status": "volume_budget", "node": spec["id"],
                              "vol_used": used, "vol_cap": cap,
                              "relay": str(book / "_pipeline" / "relay.md")},
                             ensure_ascii=False))
            return 0

    # llm / verify 节点：发令牌，交还控制权
    spec = cursor_spec(st, book)
    if spec is None:
        print(json.dumps({"done": True}, ensure_ascii=False)); return 0
    if st["nodes"].get(spec["id"], {}).get("status") == "running" and read_token(book).get("token"):
        print(json.dumps({"error": "node_running", "node": spec["id"],
                          "hint": "先调 done 完成当前节点"}, ensure_ascii=False))
        return 2

    ch = st["current_chapter"] + 1
    if spec["id"].endswith(".N14"):
        tag = f"C{ch}_before_N14_r{st.get('fix_rounds', {}).get(str(ch), 0) + 1}"
        snapshot_chapter(book, ch, tag)
        record_snapshot_manifest(book, ch, tag)
    if spec["id"] == "N20f":
        snapshot_body(book, f"N20f_round{st.get('n20_rounds', 1)}")

    token = new_token()
    write_token(book, token, spec["id"])
    st["nodes"][spec["id"]] = {"status": "running", "attempts":
                               st["nodes"].get(spec["id"], {}).get("attempts", 0) + 1,
                               "started_at": now()}
    save_state(book, st)

    evidence = spec["evidence"] if isinstance(spec["evidence"], list) else [spec["evidence"]]
    evidence = _resolve_evidence(book, evidence)
    done_cmd = (f'"{PYTHON}" "{HERE / "autopilot.py"}" done --book "{book}" '
                f'--node {spec["id"]} --token {token} '
                + " ".join(f'--evidence "{e}"' for e in evidence))
    print(json.dumps({
        "node": spec["id"],
        "kind": spec["kind"],
        "chapter": ch if spec["id"].startswith("C") else None,
        "evidence": evidence,
        "resolved_evidence": evidence,
        "_iron_rules": IRON_RULES,
        "done_cmd_for_orchestrator_only": done_cmd,
        "subagent_role": spec["role"],
        "subagent_model": spec.get("model", "default"),
        "subagent_prompt": render_prompt(spec["prompt"], book, st),
    }, ensure_ascii=False))
    return 0


def cursor_spec(st: dict, book: Path):
    """按 cursor 取节点定义；章节循环节点动态生成。"""
    cur = st["cursor"]
    if cur == "__done__":
        return None
    if cur.startswith("C"):
        n = st["current_chapter"] + 1
        return chapter_nodes(st, book, n).get(cur)
    if cur.startswith("V"):
        v = int(cur.split(".", 1)[0][1:])
        return volume_nodes(st, book, v).get(cur)
    return GLOBAL_NODES.get(cur)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    for x in ("--title", "--genre", "--target-words", "--max-chapters", "--book"):
        p.add_argument(x, required=(x != "--title"))
    # 平台与卷：不传则 platform=qimao、plan=None（完全走老路径）
    p.add_argument("--platform", default="qimao", help="qimao | zongheng")
    p.add_argument("--vol-size", type=int, default=0, help="每卷章数，0=不分卷")
    p.add_argument("--vol-llm-cap", type=int, default=80,
                   help="每卷 llm 节点预算上限（默认 80，约够 22 章）")

    for name in ("next", "status", "doctor"):
        p = sub.add_parser(name); p.add_argument("--book", required=True)

    rl = sub.add_parser("relay", help="只写接力口令，不推进游标（预算耗尽时调）")
    rl.add_argument("--book", required=True)
    rl.add_argument("--reason", default="")

    d = sub.add_parser("done")
    d.add_argument("--book", required=True)
    d.add_argument("--node", required=True)
    # --token 可省略：运行时经 WB_STEP_TOKEN 环境变量传递（审查 C1，argv 对本机
    # 进程管理器可见）；编排者按 next 输出的 done_cmd 显式传 --token。
    d.add_argument("--token", required=False, default="")
    d.add_argument("--evidence", action="append", required=True)

    s = sub.add_parser("skip")
    for x in ("--book", "--node", "--token", "--reason"):
        s.add_argument(x, required=True)

    b = sub.add_parser("block")
    b.add_argument("--book", required=True)
    b.add_argument("--reason", required=True)
    b.add_argument("--token", required=False, default="")

    r = sub.add_parser("resume")
    r.add_argument("--book", required=True)
    r.add_argument("--token", required=False, default="")
    r.add_argument("--force", action="store_true",
                   help="确认恢复处于人工阻断态的书（blocked 且无在途令牌时必须）")

    cm = sub.add_parser("commit", help="单章定稿（append/revision 自动分流）")
    cm.add_argument("--book", required=True)
    cm.add_argument("--chapter", type=int, required=True)
    cm.add_argument("--mode", choices=("auto", "append", "revision"), default="auto")
    cm.add_argument("--accept-current-length", action="store_true",
                    help="字数不在带内时按当前长度定稿")
    cm.add_argument("--no-verify", action="store_true",
                    help="跳过闸门复检（仅排障用）")

    a = ap.parse_args()
    os.environ.update(utf8_env())

    if a.cmd == "init":
        return cmd_init(a)
    if a.cmd == "status":
        return cmd_status(a)
    if a.cmd == "next":
        book = Path(a.book).resolve()
        lock = acquire_next_lock(book)
        if lock is None:
            print(json.dumps({"error": "driver_locked",
                              "hint": "另一个驱动正在执行 next，稍后重试或先 resume"},
                             ensure_ascii=False))
            return 2
        try:
            return cmd_next(a)
        finally:
            release_next_lock(lock)
    if a.cmd == "relay":
        return cmd_relay(a)
    if a.cmd == "commit":
        # 锁由 commit_chapter 自己取（它要在锁内做备份/回滚，粒度比这里更细）
        from commit_chapter import CommitError, commit_chapter
        try:
            result = commit_chapter(Path(a.book), a.chapter, mode=a.mode,
                                    verify=not a.no_verify,
                                    accept_current_length=a.accept_current_length)
        except CommitError as exc:
            payload = {"error": exc.code, "message": exc.message, **exc.detail}
            print(json.dumps(payload, ensure_ascii=False))
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if a.cmd in ("done", "skip", "block", "resume"):
        from commands import cmd_done, cmd_skip, cmd_block, cmd_resume
        handlers = {"done": cmd_done, "skip": cmd_skip,
                    "block": cmd_block, "resume": cmd_resume}
        # 审查 B4：done/skip 也要跨驱动锁——next 持锁内联可跑数分钟，期间另一
        # 驱动 done 推进会用陈旧 state 覆盖游标。block/resume 不推进游标，不加锁。
        if a.cmd in ("done", "skip"):
            book = Path(a.book).resolve()
            lock = acquire_next_lock(book)
            if lock is None:
                print(json.dumps({"error": "driver_locked",
                                  "hint": "另一个驱动正在执行，稍后重试"},
                                 ensure_ascii=False))
                return 2
            try:
                return handlers[a.cmd](a)
            finally:
                release_next_lock(lock)
        return handlers[a.cmd](a)
    return 2


if __name__ == "__main__":
    sys.exit(main())
