#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""autopilot.py — 一键小说工作台状态机 + 内联驱动器

设计要点（见 references/ 与 C:\\Users\\Wane-\\.workbuddy-ai\\plans\\cosmic-nebula-babbage-MnzvwCoC.md）：
  1. 瘦主控：主会话只调本脚本，禁读正文；next 输出含路径的 subagent_prompt
  2. 脚本内联：所有 script 节点由本脚本内部 subprocess.run 执行，LLM 物理上无法篡改参数
  3. step_token：一次性握手令牌，subagent 越权调用被 100% 机械拦截
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
import random
import re
import subprocess
import sys
import time
from pathlib import Path

# ─── 路径与配置 ───────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
CONFIG_PATH = SKILL_DIR / "config.json"
STATE_REL = "_pipeline/state.json"
TOKEN_LEN = 6

IRON_RULES = [
    "禁止向用户提问或请求确认——分歧按 references/decisions.md 默认决策表处理，记入 pending_questions 后继续",
    "每步必须调 autopilot.py next 拿指令，不许自行决定下一步",
    "只把 subagent_prompt 转交 subagent；done_cmd 与 step_token 绝不出现在 subagent 可见内容里",
    "subagent 只准返回 ≤100 字回执，禁止返回大段正文",
    "只有脚本硬失败 / 闸门 BLOCK / 用户明确喊停 三种情况允许停",
]


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        sys.exit(f"ERROR: 找不到 {CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


CFG = load_config()
PYTHON = CFG["PYTHON"]
NODE_BIN = CFG.get("NODE", "")


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
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)          # 原子替换，避免中断写出半截文件


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def norm_cjk(s: str) -> str:
    """暗坑6：原文引用归一化——只留中英文字符与数字，避免标点差异误杀。"""
    return re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", s)


# ─── 令牌 ─────────────────────────────────────────────────────────────────────

def new_token() -> str:
    return f"{random.randrange(16 ** TOKEN_LEN):0{TOKEN_LEN}x}"


def token_ok(st: dict, node: str, token: str) -> bool:
    return bool(token) and st.get("step_token") == token and st.get("step_token_node") == node


# ─── 节点表（PART 3 填充） ────────────────────────────────────────────────────

from pipeline_spec import (          # noqa: E402
    GLOBAL_NODES, chapter_nodes, next_cursor, first_cursor,
)

# ─── 内联脚本执行（PART 2 填充） ──────────────────────────────────────────────

from inline_runner import run_node_script, verify_quote   # noqa: E402


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
        "step_token": None,
        "step_token_node": None,
        "relay_count": 0,
    }
    save_state(book, st)
    print(json.dumps({"ok": True, "book": st["book"], "cursor": st["cursor"],
                      "nodes_total": len(GLOBAL_NODES)}, ensure_ascii=False))
    return 0


def cmd_status(a) -> int:
    st = load_state(Path(a.book))
    if not st:
        print(json.dumps({"error": "not_initialized"}, ensure_ascii=False))
        return 2
    nodes = st["nodes"]
    done = sum(1 for v in nodes.values() if v.get("status") == "done")
    print(json.dumps({
        "book": st["book"], "genre": st["genre"],
        "cursor": st["cursor"], "current_chapter": st["current_chapter"],
        "chapters_committed": st["chapters_committed"], "words_total": st["words_total"],
        "target_words": st["target_words"],
        "nodes_done": done, "nodes_recorded": len(nodes),
        "blocked_reason": st["blocked_reason"],
        "pending_questions": len(st["pending_questions"]),
    }, ensure_ascii=False))
    return 0


def cmd_next(a) -> int:
    book = Path(a.book)
    st = load_state(book)
    if not st:
        print(json.dumps({"error": "not_initialized"}, ensure_ascii=False)); return 2
    if st["blocked_reason"]:
        print(json.dumps({"error": "blocked", "reason": st["blocked_reason"],
                          "hint": "修复后调 resume"}, ensure_ascii=False)); return 2

    # 内联顺延：把所有连续 script 节点一次跑完，直到撞上下一个 llm/verify
    guard = 0
    while True:
        guard += 1
        if guard > 200:
            print(json.dumps({"error": "inline_loop_guard"}, ensure_ascii=False)); return 2
        spec = cursor_spec(st, book)
        if spec is None:
            print(json.dumps({"done": True, "chapters_committed": st["chapters_committed"],
                              "words_total": st["words_total"]}, ensure_ascii=False))
            return 0
        if spec["kind"] != "script":
            break
        rc, out = run_node_script(book, st, spec)
        if rc != 0:
            st["blocked_reason"] = f"{spec['id']} 内联脚本失败：{out[-400:]}"
            st["nodes"][spec["id"]] = {"status": "failed", "at": now(), "output": out[-2000:]}
            save_state(book, st)
            print(json.dumps({"error": "script_failed", "node": spec["id"],
                              "output": out[-800:]}, ensure_ascii=False))
            return 1
        st["nodes"][spec["id"]] = {"status": "done", "at": now(), "output": out[-1500:]}
        st["cursor"] = next_cursor(st)
        save_state(book, st)

    # llm / verify 节点：发令牌，交还控制权
    spec = cursor_spec(st, book)
    if spec is None:
        print(json.dumps({"done": True}, ensure_ascii=False)); return 0
    if st["nodes"].get(spec["id"], {}).get("status") == "running" and st.get("step_token"):
        print(json.dumps({"error": "node_running", "node": spec["id"],
                          "hint": "先调 done 完成当前节点"}, ensure_ascii=False))
        return 2
    token = new_token()
    st["step_token"] = token
    st["step_token_node"] = spec["id"]
    st["nodes"][spec["id"]] = {"status": "running", "attempts":
                               st["nodes"].get(spec["id"], {}).get("attempts", 0) + 1,
                               "started_at": now()}
    save_state(book, st)

    print(json.dumps({
        "node": spec["id"],
        "kind": spec["kind"],
        "chapter": st["current_chapter"] + 1 if spec["id"].startswith("C") else None,
        "_iron_rules": IRON_RULES,
        "done_cmd_for_orchestrator_only":
            f'"{PYTHON}" "{HERE / "autopilot.py"}" done --book "{a.book}" '
            f'--node {spec["id"]} --token {token} --evidence {spec["evidence"]}',
        "subagent_role": spec["role"],
        "subagent_prompt": spec["prompt"],
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

    for name in ("next", "status", "resume"):
        p = sub.add_parser(name); p.add_argument("--book", required=True)

    d = sub.add_parser("done")
    d.add_argument("--book", required=True)
    d.add_argument("--node", required=True)
    d.add_argument("--token", required=True)
    d.add_argument("--evidence", action="append", required=True)

    s = sub.add_parser("skip")
    for x in ("--book", "--node", "--token", "--reason"):
        s.add_argument(x, required=True)

    b = sub.add_parser("block")
    b.add_argument("--book", required=True)
    b.add_argument("--reason", required=True)

    a = ap.parse_args()
    os.environ.update(utf8_env())

    if a.cmd == "init":
        return cmd_init(a)
    if a.cmd == "status":
        return cmd_status(a)
    if a.cmd == "next":
        return cmd_next(a)
    if a.cmd in ("done", "skip", "block", "resume"):
        from commands import cmd_done, cmd_skip, cmd_block, cmd_resume
        return {"done": cmd_done, "skip": cmd_skip,
                "block": cmd_block, "resume": cmd_resume}[a.cmd](a)
    return 2


if __name__ == "__main__":
    sys.exit(main())
