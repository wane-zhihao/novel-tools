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


def render_prompt(template: str, book: Path) -> str:
    """渲染 prompt 模板里的路径占位符。

    节点表只存模板（与具体书无关）；{QIMAO} / {STORY_LONG_WRITE} / {BOOK}
    在 next 输出时才替换成真实绝对路径——换一本书跑，同一份节点表不用改。
    """
    return (template
            .replace("{QIMAO}", CFG["QIMAO_PLATFORM_DIR"])
            .replace("{STORY_LONG_WRITE}", CFG["STORY_LONG_WRITE"])
            .replace("{SKILL}", str(SKILL_DIR))
            .replace("{BOOK}", str(book)))


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
    GLOBAL_NODES, chapter_nodes, next_cursor, first_cursor,
)

# ─── 内联脚本执行（PART 2 填充） ──────────────────────────────────────────────

from inline_runner import (          # noqa: E402
    run_node_script, verify_quote, chapter_has_blocking, append_chapter_findings,
    snapshot_chapter, snapshot_body, MILESTONE_CHAPTERS, extract_json,
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
        if ch not in MILESTONE_CHAPTERS:
            return f"第{ch}章非里程碑章（只在 {MILESTONE_CHAPTERS} 做阶段审查）"
    return None


def _write_relay(book: Path, st: dict) -> None:
    content = (
        "# 接力口令\n\n"
        f"- 书：{st['book']}\n"
        f"- 已封板：{st['chapters_committed']} 章 / {st['words_total']} 字\n"
        f"- 当前 cursor：`{st['cursor']}`\n"
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


def cmd_next(a) -> int:
    book = Path(a.book).resolve()
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
        if spec["id"].endswith(".N16") and st["chapters_committed"] % 3 == 0:
            _write_relay(book, st)
        save_state(book, st)

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
        "subagent_prompt": render_prompt(spec["prompt"], book),
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
    return GLOBAL_NODES.get(cur)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    for x in ("--title", "--genre", "--target-words", "--max-chapters", "--book"):
        p.add_argument(x, required=(x != "--title"))

    for name in ("next", "status"):
        p = sub.add_parser(name); p.add_argument("--book", required=True)

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
