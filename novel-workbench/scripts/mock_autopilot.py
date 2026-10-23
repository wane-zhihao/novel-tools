#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mock_autopilot.py — Phase 0 纪律阈值实测（约 90 行）

目的：测出「next → 唤起真 subagent → subagent 返回 → done」这个三步闭环，
在 WorkBuddy 主会话里能连续跑多少轮不触发「礼貌停机」。

15 个节点，交替 script（内联自动顺延）与 llm（需真 subagent）：
  - script 节点由本脚本内部执行，对主会话透明，不消耗一轮交互
  - llm 节点必须主会话唤起真 subagent 落盘一个小文件，再调 done --token

用法:
  python mock_autopilot.py init   --book <book_dir>
  python mock_autopilot.py next
  python mock_autopilot.py done   --node <id> --token <t> --evidence <path>
  python mock_autopilot.py status
"""
import argparse, json, os, random, subprocess, sys, time
from pathlib import Path

NODES = [f"{'S' if i % 3 == 0 else 'L'}{i + 1:02d}" for i in range(15)]  # S=script, L=llm
STATE_NAME = "_pipeline/state.json"
TOKEN_LEN = 6

IRON_RULES = [
    "禁止向用户提问或请求确认——全部按默认决策继续",
    "每步必须调 mock_autopilot.py 拿指令，不许自行决定下一步",
    "subagent 只做创作，返回 ≤100 字回执，禁止返回大段内容",
    "只有脚本硬失败才允许停",
]


def state_path(book: Path) -> Path:
    return book / STATE_NAME


def load(book: Path) -> dict:
    p = state_path(book)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save(book: Path, st: dict) -> None:
    p = state_path(book)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def fresh(book: Path) -> dict:
    return {
        "schema_version": 2,
        "book": book.name,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "nodes": {n: {"status": "pending"} for n in NODES},
        "cursor": NODES[0],
        "step_token": None,
        "llm_rounds": 0,
        "script_rounds": 0,
    }


def run_inline(book: Path, node: str) -> int:
    """script 节点：本脚本内部执行，返回退出码。这里只做一次空转统计。"""
    st = load(book)
    st["script_rounds"] = st.get("script_rounds", 0) + 1
    st["nodes"][node]["status"] = "done"
    st["nodes"][node]["at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    advance(book, st)
    save(book, st)          # ← 必须落盘，否则游标不前进，cmd_next 死循环
    return 0


def advance(book: Path, st: dict) -> None:
    i = NODES.index(st["cursor"])
    st["cursor"] = NODES[i + 1] if i + 1 < len(NODES) else "__done__"


def new_token() -> str:
    return f"{random.randrange(16 ** TOKEN_LEN):0{TOKEN_LEN}x}"


def cmd_init(a):
    book = Path(a.book)
    book.mkdir(parents=True, exist_ok=True)
    (book / "_pipeline").mkdir(exist_ok=True)
    save(book, fresh(book))
    print(json.dumps({"ok": True, "nodes": NODES, "cursor": NODES[0]}, ensure_ascii=False))
    return 0


def cmd_next(a):
    book = Path(a.book)
    st = load(book)
    if not st:
        print("ERROR: 未初始化", file=sys.stderr); return 2
    if st["cursor"] == "__done__":
        print(json.dumps({"done": True, "llm_rounds": st["llm_rounds"],
                          "script_rounds": st["script_rounds"]}, ensure_ascii=False))
        return 0
    # 内联顺延：把所有连续 script 节点一次跑完，直到撞上下一个 llm 节点
    while st["cursor"].startswith("S") and st["cursor"] != "__done__":
        run_inline(book, st["cursor"])
        st = load(book)
    if st["cursor"] == "__done__":
        print(json.dumps({"done": True, "llm_rounds": st["llm_rounds"],
                          "script_rounds": st["script_rounds"]}, ensure_ascii=False))
        return 0

    node = st["cursor"]
    if st["nodes"][node]["status"] == "running" and st.get("step_token"):
        print(json.dumps({"error": "node_running", "node": node,
                          "hint": "先调 done 完成当前节点"}, ensure_ascii=False), file=sys.stderr)
        return 2
    token = new_token()
    st["step_token"] = token
    st["step_token_node"] = node
    st["nodes"][node]["status"] = "running"
    save(book, st)
    out = book / "_pipeline" / f"out_{node}.txt"
    print(json.dumps({
        "node": node,
        "kind": "llm",
        "_iron_rules": IRON_RULES,
        "done_cmd_for_orchestrator_only":
            f"python mock_autopilot.py done --book {a.book} --node {node} --token {token} --evidence {out}",
        "subagent_role": "mock-writer",
        "subagent_prompt": (
            f"请用 Write 工具把一行文字 `phase0-{node}-ok` 写入文件：{out}\n"
            f"写完只回执 ≤100 字，不要复述内容，不要提问。"
        ),
    }, ensure_ascii=False))
    return 0


def cmd_done(a):
    book = Path(a.book)
    st = load(book)
    node = a.node
    if node not in st["nodes"]:
        print(f"ERROR: 未知节点 {node}", file=sys.stderr); return 2
    if st.get("step_token") != a.token or st.get("step_token_node") != node:
        print("ERROR: token 不匹配或节点不匹配（可能是 subagent 越权调用）", file=sys.stderr); return 2
    ev = Path(a.evidence)
    if not ev.is_file() or ev.stat().st_size == 0:
        print(f"ERROR: evidence 缺失或为空：{ev}", file=sys.stderr); return 2
    st["nodes"][node] = {"status": "done", "evidence": [str(ev)],
                         "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    st["llm_rounds"] = st.get("llm_rounds", 0) + 1
    st["step_token"] = None
    st["step_token_node"] = None
    advance(book, st)
    save(book, st)
    print(json.dumps({"ok": True, "node": node, "next": st["cursor"],
                      "llm_rounds": st["llm_rounds"]}, ensure_ascii=False))
    return 0


def cmd_status(a):
    st = load(Path(a.book))
    if not st:
        print("ERROR: 未初始化", file=sys.stderr); return 2
    done = sum(1 for n in st["nodes"].values() if n["status"] == "done")
    print(json.dumps({"cursor": st["cursor"], "done": done, "total": len(NODES),
                      "llm_rounds": st.get("llm_rounds", 0),
                      "script_rounds": st.get("script_rounds", 0)}, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "next", "status"):
        p = sub.add_parser(name); p.add_argument("--book", required=True)
    d = sub.add_parser("done")
    for x in ("--book", "--node", "--token", "--evidence"):
        d.add_argument(x, required=True)
    a = ap.parse_args()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    os.environ.update(env)
    return {"init": cmd_init, "next": cmd_next, "done": cmd_done, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
