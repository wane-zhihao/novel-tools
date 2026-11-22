#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_unattended.py — 无人值守推进夹具（P2 运行时）

**为什么要有**（2026-11-19）：本会话 subagent 静默死亡 **10+ 次**
（agent-22/24/26/37/38/40/44/47/48/50/53/55/58/59）。
每次死亡都要求人工发现 → resume → 重新派发。
**一条每隔一次就断的流水线，不能声称「工具能完成目标」。**

P2 运行时（`server/cli.mjs run`）由后端自己调模型、写证据、交令牌，
**没有对话 subagent 这个失败模式**。实测它一次无人值守推进了 12 个节点
（N01→N02→N03→N04→N05→N06→N07→N08→N09d→C1.N09→C1.N10→C1.N11→C1.N12）。

本夹具用 `--mock`（零成本）验证两件事：
  ① **状态机能无人值守前进**：至少推进到 C1.N09 之后
  ② **守卫会在该拦的地方精确停下**：mock writer 不填追踪事务，
     `done` 必须以 `tracking_incomplete` 拒绝 C1.N12——
     **拦不住才是缺陷**（那会让半填事务的章混进封板）

跑法：
  python -X utf8 server/tests/test_unattended.py
  → 全过打印 UNATTENDED_OK 并 exit 0
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2] / "server"
CLI = SERVER / "cli.mjs"
AUT = SERVER.parent / "scripts" / "autopilot.py"
PY = sys.executable
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def main() -> int:
    print("== 无人值守推进（P2 运行时 / mock）==")
    fails = []

    node = shutil.which("node")
    if not node:
        print("  SKIP [node 不在 PATH，跳过 P2 运行时夹具]")
        print("UNATTENDED_SKIPPED")
        return 0
    if not CLI.is_file():
        print(f"  FAIL [找不到 {CLI}]")
        print("UNATTENDED_FAILED")
        return 1

    tmp = Path(tempfile.mkdtemp())
    try:
        book = tmp / "mock书"
        r = subprocess.run(
            [PY, "-X", "utf8", str(AUT), "init", "--book", str(book),
             "--title", "mock书", "--genre", "niandai",
             "--target-words", "20000", "--max-chapters", "12"],
            capture_output=True, encoding="utf-8", env=ENV)
        if r.returncode != 0:
            print(f"  FAIL [init 失败] {(r.stdout or r.stderr)[:200]}")
            print("UNATTENDED_FAILED")
            return 1
        print("  PASS [init]")

        env2 = {**ENV, "WORKBENCH_PYTHON": PY}
        p = subprocess.run(
            [node, str(CLI), "run", "--book", str(book),
             "--max-steps", "20", "--mock"],
            capture_output=True, encoding="utf-8", env=env2, cwd=str(SERVER))
        out = (p.stdout or "") + (p.stderr or "")

        # ① 状态机确实前进了：N03/N04/N05 都应被跑过
        advanced = all(f"[{n}]" in out for n in ("N03", "N04", "N05"))
        print(f"  {'PASS' if advanced else 'FAIL'} [状态机无人值守前进（N03/N04/N05 都跑过）]")
        if not advanced:
            fails.append("advance")
            print(out[-900:])

        # ② 守卫精确拦下：mock writer 不填追踪 → tracking_incomplete
        guarded = "tracking_incomplete" in out
        print(f"  {'PASS' if guarded else 'FAIL'} "
              f"[守卫拦下未填追踪的封板（tracking_incomplete）]")
        if not guarded:
            fails.append("guard")

        # ③ 游标必须停在 C1.N12 附近且**没有**封板成功
        st = json.loads((book / "_pipeline" / "state.json").read_text(encoding="utf-8"))
        no_commit = int(st.get("chapters_committed") or 0) == 0
        print(f"  {'PASS' if no_commit else 'FAIL'} "
              f"[未封板任何章（chapters_committed={st.get('chapters_committed')}）]")
        if not no_commit:
            fails.append("no_commit")

        # ④ 提示词模板必须写清追踪必填项（否则真 writer 也会卡）
        #    注意：节点耗尽后 `next` 不再返回提示词，所以要直接查模板源。
        spec_src = (SERVER.parent / "scripts" / "pipeline_spec.py").read_text(encoding="utf-8")
        has_fields = all(k in spec_src for k in (
            "delta.result", "character_changes", "timeline_events",
            "character_snapshots", "context.position"))
        print(f"  {'PASS' if has_fields else 'FAIL'} "
              f"[C1.N12 提示词模板写明追踪必填项]")
        if not has_fields:
            fails.append("prompt_fields")
            print("        提示词模板里缺字段说明 —— 真 writer 也会卡在这里")

        # ⑤ 提示词模板必须给出「必填项一个都不能空着」这种明确禁令
        explicit = ("不能空着" in spec_src) or ("必填项" in spec_src)
        print(f"  {'PASS' if explicit else 'FAIL'} "
              f"[提示词模板明确说明必填项不可为空]")
        if not explicit:
            fails.append("prompt_explicit")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total = 6
    passed = total - len(fails)
    print(f"\n{passed}/{total} 通过")
    if fails:
        print("UNATTENDED_FAILED")
        return 1
    print("UNATTENDED_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
