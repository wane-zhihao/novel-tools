"""P1 契约回归：N13 状态矩阵、N16 幂等、N21 不可投阻断。

由 server/tests/python-contracts.test.mjs 调用；所有数据在临时目录。
"""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_config(root: Path, story_status: str) -> Path:
    fake = root / "fake"
    fake.mkdir(parents=True, exist_ok=True)
    write(fake / "storyctl.py", """
import json, os
status = os.environ.get("FAKE_STORY_STATUS", "ready")
if status == "ready":
    doc = {"status": "ready", "quality": {"status": "pass", "blocking_findings": []},
           "length": {"status": "internal_pass", "actual": 2200, "target": 2200}}
elif status == "blocked":
    doc = {"status": "blocked",
           "quality": {"status": "fail", "blocking_findings": [
               {"type": "QUALITY", "message": "动作链流水账", "line": 3, "excerpt": "他走过去"}]},
           "length": {"status": "internal_pass", "actual": 2200, "target": 2200}}
elif status == "invalid":
    doc = {"status": "invalid", "error_code": "INVALID", "message": "章节结构无效"}
elif status == "tool_unavailable":
    doc = {"status": "tool_unavailable", "message": "质量工具缺失"}
else:
    doc = {"status": "error", "error_code": "CHECK_FAILED", "message": "boom"}
print(json.dumps(doc, ensure_ascii=False))
""")
    write(fake / "gate_runner.py", """
import json
print(json.dumps([{"file": "第001章_测试.md", "status": "pass",
                   "metrics": {"visible_chars": 2200}, "findings": []}], ensure_ascii=False))
""")
    write(fake / "style_probe.py", """
def probe(path):
    return {"dialog_ratio": 0.2}
""")
    write(fake / "check-outline.js", "process.exit(0);\n")
    write(fake / "ai-patterns.js", "console.log(JSON.stringify({findings: []}));\n")
    write(fake / "degeneration.js", "console.log(JSON.stringify({findings: []}));\n")
    write(fake / "tracking_commit.py", "print('{}')\n")
    write(fake / "genre.json", json.dumps({"baselines": {"niandai": {
        "sent_len_median": [15, 27], "paragraphs_per_1000": [33.1, 54.1],
        "dialog_ratio": [0.1, 0.4]}}}, ensure_ascii=False))
    write(fake / "style_anchor.json", json.dumps({"samples": []}, ensure_ascii=False))
    config = {
        "PYTHON": sys.executable,
        "NODE": os.environ.get("FAKE_NODE") or sys.executable,
        "STORY_LONG_WRITE": str(fake),
        "STORYCTL": str(fake / "storyctl.py"),
        "TRACKING_COMMIT": str(fake / "tracking_commit.py"),
        "CHECK_OUTLINE": str(fake / "check-outline.js"),
        "AI_PATTERNS_CHECK": str(fake / "ai-patterns.js"),
        "DEGENERATION_CHECK": str(fake / "degeneration.js"),
        "QIMAO_PLATFORM_DIR": str(fake),
        "QIMAO_GENRE_BASE": str(fake / "genre.json"),
        "GATE_RUNNER": str(fake / "gate_runner.py"),
        "STYLE_PROBE": str(fake / "style_probe.py"),
        "QIMAO_STYLE_ANCHOR": str(fake / "style_anchor.json"),
        "PLATFORMS": {
            "qimao": {
                "DIR": str(fake),
                "GENRE_BASE": str(fake / "genre.json"),
                "STYLE_ANCHOR": str(fake / "style_anchor.json"),
                "GENRES": ["niandai"],
            },
        },
    }
    path = root / "pipeline.json"
    write(path, json.dumps(config, ensure_ascii=False))
    return path


def make_book(root: Path, words: int = 2380) -> Path:
    book = root / "book"
    write(book / "正文" / "第001章_测试.md", "顶职名单今天换名字，换掉的正是他。" * 80)
    write(book / "大纲" / "卷纲_第一卷.md", "- 主线：顶职名额与三天期限\n")
    write(book / "投稿包" / "全文.md", "# 全文\n" + "正文" * 100)
    write(book / "_pipeline" / "endgame_findings.json", '{"items": []}')
    write(book / "_pipeline" / "judgments_N20.json", '{"verdict": "pass", "items": []}')
    write(book / "追踪" / "_tracking-state.json", json.dumps({
        "last_committed_chapter": 1,
        "wordcount_records": {"1": {"actual": words}},
    }, ensure_ascii=False))
    return book


def test_n13(root: Path) -> None:
    from inline_runner import _chapter_check
    for status, expected in [
        ("ready", 0),
        ("blocked", 0),
        ("invalid", 2),
        ("tool_unavailable", 2),
        ("error", 2),
    ]:
        os.environ["FAKE_STORY_STATUS"] = status
        book = make_book(root / f"n13-{status}")
        rc, message = _chapter_check(book, 1, {"genre": "niandai"})
        assert rc == expected, f"N13 {status}: rc={rc}, {message}"
        if expected == 0:
            assert (book / "_pipeline" / "findings_C1.json").is_file()


def test_n16_idempotent(root: Path) -> None:
    from inline_runner import _commit_chapter
    book = make_book(root / "n16")
    state = {"words_total": 0, "chapters_committed": 0}
    rc, message = _commit_chapter(book, 1, state)
    assert rc == 0, message
    assert state["words_total"] == 2380, state


def test_n21_blocked(root: Path) -> None:
    from inline_runner import _package
    book = make_book(root / "n21-low", words=2380)
    st = {"book": "测试", "genre": "niandai", "chapters_committed": 1}
    rc, message = _package(book, st)
    assert rc != 0, message
    assert "NOT_SUBMITTABLE" in message, message
    assert (book / "投稿包" / "体检报告.md").is_file()

    book = make_book(root / "n21-ok", words=20000)
    rc, message = _package(book, st)
    assert rc == 0, message


def test_next_lock(root: Path) -> None:
    from autopilot import acquire_next_lock, release_next_lock
    book = root / "lockbook"
    (book / "_pipeline").mkdir(parents=True, exist_ok=True)
    first = acquire_next_lock(book)
    assert first is not None
    assert acquire_next_lock(book) is None
    release_next_lock(first)
    again = acquire_next_lock(book)
    assert again is not None
    release_next_lock(again)


# ─── 2026-10-25 P0/P1 波新增契约 ─────────────────────────────────────────────

class Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def make_state_book(root: Path, name: str, **overrides) -> Path:
    book = make_book(root / name)
    st = {
        "schema_version": 2, "book": "测试书", "genre": "niandai",
        "target_words": 20000, "max_chapters": 12,
        "created_at": "2026-10-25T00:00:00+0800",
        "current_chapter": 0, "chapters_committed": 0, "words_total": 0,
        "cursor": "N03", "nodes": {}, "decisions_taken": [],
        "pending_questions": [], "blocked_reason": None, "relay_count": 0,
    }
    st.update(overrides)
    write(book / "_pipeline" / "state.json", json.dumps(st, ensure_ascii=False))
    return book


def test_evidence_pattern_mismatch(root: Path) -> None:
    """审查 A2：持令牌者不能指认任意大文件冒充本节点产物。"""
    from commands import cmd_done
    from autopilot import write_token
    book = make_state_book(root, "a2")
    write_token(book, "tok-a2", "N03")
    # 尺寸够但不匹配 spec 期望模式（大纲/选题报告.md）
    write(book / "设定" / "冒充.md", "冒" * 800)
    rc = cmd_done(Args(book=str(book), node="N03", token="tok-a2",
                       evidence=["设定/冒充.md"]))
    assert rc == 2, "evidence 不匹配 spec 模式必须被拒"
    # 匹配模式 → 放行
    write(book / "大纲" / "选题报告.md", "选题" * 300)
    rc = cmd_done(Args(book=str(book), node="N03", token="tok-a2",
                       evidence=["大纲/选题报告.md"]))
    assert rc == 0, f"匹配模式的证据应放行：rc={rc}"


def test_done_token_via_env(root: Path) -> None:
    """审查 C1：令牌可经 WB_STEP_TOKEN 环境变量传递（不走 argv）。"""
    from commands import cmd_done
    from autopilot import write_token
    book = make_state_book(root, "c1")
    write_token(book, "tok-c1", "N03")
    write(book / "大纲" / "选题报告.md", "选题" * 300)
    os.environ["WB_STEP_TOKEN"] = "tok-c1"
    try:
        # 不传 --token，靠环境变量
        rc = cmd_done(Args(book=str(book), node="N03", token="",
                           evidence=["大纲/选题报告.md"]))
        assert rc == 0, f"env 令牌应放行：rc={rc}"
        # 环境变量错了仍然拒（--token 空 + env 错 = 空令牌）
        write_token(book, "tok-c1b", "N04")
        os.environ["WB_STEP_TOKEN"] = "wrong"
        rc = cmd_done(Args(book=str(book), node="N04", token="",
                           evidence=["大纲/选题报告.md"]))
        assert rc == 2, "错误的 env 令牌必须被拒"
    finally:
        os.environ.pop("WB_STEP_TOKEN", None)


def test_block_and_resume_need_token(root: Path) -> None:
    """审查 B5：在途 block 要令牌；人工阻断态 resume 要 --force。"""
    from commands import cmd_block, cmd_resume
    from autopilot import write_token, load_state, clear_token
    book = make_state_book(root, "b5")
    write_token(book, "tok-b5", "N03")
    rc = cmd_block(Args(book=str(book), reason="测试停机", token=""))
    assert rc == 2, "有节点在途时无令牌 block 必须被拒"
    rc = cmd_block(Args(book=str(book), reason="测试停机", token="tok-b5"))
    assert rc == 1, "持令牌 block 应生效"

    st = load_state(book)
    assert st["blocked_reason"] == "测试停机"
    # 令牌仍在途：无令牌/无 force 的 resume 被拒
    rc = cmd_resume(Args(book=str(book), token="", force=False))
    assert rc == 2, "有节点在途时无令牌 resume 必须被拒"
    # 清掉在途令牌（空闲态）后：人工阻断态仍需 --force
    clear_token(book)
    rc = cmd_resume(Args(book=str(book), token="", force=False))
    assert rc == 2, "人工阻断态无 --force 的 resume 必须被拒"
    rc = cmd_resume(Args(book=str(book), token="", force=True))
    assert rc == 0, f"--force resume 应放行：rc={rc}"
    st = load_state(book)
    assert st["blocked_reason"] is None


def test_resume_resets_rounds(root: Path) -> None:
    """审查 B2：终停后 resume 必须返还修复预算，否则第一次再 block 即再次终停。"""
    from commands import cmd_resume
    from autopilot import load_state
    book = make_state_book(root, "b2", fix_rounds={"1": 2}, n20_rounds=2,
                           blocked_reason="终停待人工")
    rc = cmd_resume(Args(book=str(book), token="", force=True))
    assert rc == 0
    st = load_state(book)
    assert st["fix_rounds"] == {}, st["fix_rounds"]
    assert st["n20_rounds"] == 0, st["n20_rounds"]


def test_n14_done_rechecks_n13(root: Path) -> None:
    """审查 B1 / plan 5.2.2：N14 真实改写后必须先过 N13 复检。"""
    from commands import cmd_done
    from autopilot import write_token, load_state
    book = make_state_book(root, "b1", cursor="C1.N14")
    write_token(book, "tok-b1", "C1.N14")
    rc = cmd_done(Args(book=str(book), node="C1.N14", token="tok-b1",
                       evidence=["正文/第001章_测试.md"]))
    assert rc == 0, f"N14 done 应放行：rc={rc}"
    st = load_state(book)
    assert st["cursor"] == "C1.N13", st["cursor"]
    assert st.get("recheck_chapter") == 1


def test_n13_recheck_routes(root: Path) -> None:
    """复检通过 → N15；仍 blocking → 记一轮 fix_rounds 回 N14（有界）。"""
    from commands import cmd_done
    from autopilot import write_token, load_state, cmd_next
    from inline_runner import restore_best_snapshot

    # 通过路径：fake storyctl ready + gate 无 findings
    os.environ["FAKE_STORY_STATUS"] = "ready"
    book = make_state_book(root, "b1-pass", cursor="C1.N14")
    write_token(book, "tok-p", "C1.N14")
    assert cmd_done(Args(book=str(book), node="C1.N14", token="tok-p",
                         evidence=["正文/第001章_测试.md"])) == 0
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cmd_next(Args(book=str(book)))
    assert rc == 0, buf.getvalue()
    st = load_state(book)
    assert st.get("recheck_chapter") is None, "复检标志必须被消费"
    assert st["cursor"] == "C1.N15", f"复检通过应进 N15：{st['cursor']}"

    # 仍 blocking：fake storyctl blocked → fix_rounds+1，回 N14
    # 注意：done 现在有正文预检（_text_preflight），连续拒 2 次后才放行，
    # 所以这里要按它文档化的行为循环，而不是假设一次就过。
    os.environ["FAKE_STORY_STATUS"] = "blocked"
    book = make_state_book(root, "b1-block", cursor="C1.N14")
    write_token(book, "tok-b", "C1.N14")
    released = False
    for _ in range(3):
        if cmd_done(Args(book=str(book), node="C1.N14", token="tok-b",
                         evidence=["正文/第001章_测试.md"])) == 0:
            released = True
            break
    assert released, "正文预检连续 2 次不过后必须放行，否则整条链会死锁"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cmd_next(Args(book=str(book)))
    assert rc == 0, buf.getvalue()
    st = load_state(book)
    assert st["cursor"] == "C1.N14", st["cursor"]
    assert st.get("fix_rounds", {}).get("1") == 1, st.get("fix_rounds")
    os.environ.pop("FAKE_STORY_STATUS", None)


def test_rollback_best_snapshot(root: Path) -> None:
    """审查 B3：终停时回滚 Blocker 最少的快照（plan 补丁4）。"""
    from inline_runner import restore_best_snapshot
    book = make_book(root / "b3")
    snaps = book / "_pipeline" / "snapshots"
    for tag, blocking in (("C1_before_N14_r1", 3), ("C1_before_N14_r2", 1)):
        (snaps / tag).mkdir(parents=True, exist_ok=True)
        write(snaps / tag / "第001章_测试.md", f"快照 {tag}")
    write(snaps / "manifest_C1.json", json.dumps({
        "C1_before_N14_r1": {"blocking_count": 3},
        "C1_before_N14_r2": {"blocking_count": 1},
    }, ensure_ascii=False))
    restored = restore_best_snapshot(book, 1)
    assert restored == "C1_before_N14_r2", restored
    assert "快照 C1_before_N14_r2" in (book / "正文" / "第001章_测试.md").read_text(encoding="utf-8")
    assert (book / "_pipeline" / "rollback_log.json").is_file()


STATEFUL_STORYCTL = r'''
import hashlib, json, os, sys
from pathlib import Path

args = sys.argv[1:]
status = os.environ.get("FAKE_STORY_STATUS", "ready")


def opt(name):
    return args[args.index(name) + 1] if name in args else None


if status != "ready":
    print(json.dumps({"status": "blocked",
                      "quality": {"status": "fail", "blocking_findings": [
                          {"type": "QUALITY", "message": "动作链流水账", "line": 3,
                           "excerpt": "他走过去"}]},
                      "length": {"status": "internal_pass", "actual": 2200, "target": 2200}},
                     ensure_ascii=False))
    sys.exit(0)

if "commit" not in args:
    print(json.dumps({"status": "ready", "quality": {"status": "pass", "blocking_findings": []},
                      "length": {"status": "internal_pass", "actual": 2200, "target": 2200}},
                     ensure_ascii=False))
    sys.exit(0)

project = Path(opt("--project"))
chapter = int(opt("--chapter"))
files = sorted((project / "正文").glob(f"第{chapter:03d}章_*.md"))
body = files[0].read_bytes()
state_path = project / "追踪" / "_tracking-state.json"
state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
records = state.setdefault("wordcount_records", {})
records[str(chapter)] = {"actual": len(body.decode("utf-8")), "target": 2200,
                         "body_sha256": hashlib.sha256(body).hexdigest()}
state["last_committed_chapter"] = max(int(state.get("last_committed_chapter") or 0), chapter)
state["state_revision"] = int(state.get("state_revision") or 0) + 1
state_path.parent.mkdir(parents=True, exist_ok=True)
state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({"status": "ok",
                  "last_committed_chapter": state["last_committed_chapter"],
                  "state_revision": state["state_revision"]}, ensure_ascii=False))
'''

# 先写坏账本再失败：验证备份回滚是真的生效，而不是「没写所以没变」
FAILING_COMMIT_STORYCTL = STATEFUL_STORYCTL.replace(
    'print(json.dumps({"status": "ok",',
    'print(json.dumps({"status": "error", "error_code": "QUALITY_BLOCKED", "message": "boom"}))\n'
    'sys.exit(1)\nprint(json.dumps({"status": "ok",')


def _txn(chapter: int, revision: int) -> dict:
    return {
        "schema_version": 1, "mode": "append", "chapter": chapter,
        "chapter_title": "测试章", "expected_state_revision": revision,
        "delta": {"result": "测试结果摘要", "character_changes": [],
                  "foreshadow_changes": [], "timeline_events": [], "constraints": [],
                  "next_chapter_commitments": [], "retired_context_items": [],
                  "retired_characters": []},
        "context": {"position": {"volume": "第一卷", "volume_start_chapter": 1,
                                 "story_time": "1990 年", "scene": "厂办"},
                    "long_term_constraints": [], "active_character_names": [],
                    "continuity_risks": []},
        "character_snapshots": {},
    }


def make_commit_book(root: Path, name: str, *, last: int = 0, cursor: str | None = None,
                     with_txn: bool = True, chapters: int = 2) -> Path:
    book = root / name
    for n in range(1, chapters + 1):
        write(book / "正文" / f"第{n:03d}章_测试.md", f"第{n}章正文。" * 120)
    records = {}
    for n in range(1, last + 1):
        body = book / "正文" / f"第{n:03d}章_测试.md"
        records[str(n)] = {"actual": 2200, "target": 2200,
                           "body_sha256": hashlib.sha256(body.read_bytes()).hexdigest()}
    write(book / "追踪" / "_tracking-state.json", json.dumps({
        "schema_version": 1, "state_revision": last, "last_committed_chapter": last,
        "wordcount_records": records, "context": {}, "characters": {},
        "foreshadow": {}, "timeline": {},
    }, ensure_ascii=False))
    target = last + 1
    if with_txn:
        write(book / ".story" / "work" / f"第{target:03d}章" / "tracking.json",
              json.dumps(_txn(target, last), ensure_ascii=False))
    write(book / "_pipeline" / "state.json", json.dumps({
        "book": "测试", "genre": "niandai",
        "cursor": cursor or f"C{target}.N16",
        "current_chapter": last, "chapters_committed": last, "words_total": 0,
        "target_words": 20000, "max_chapters": 12,
        "blocked_reason": None, "pending_questions": [], "nodes": {},
    }, ensure_ascii=False))
    return book


def _ledger(book: Path) -> dict:
    return json.loads((book / "追踪" / "_tracking-state.json").read_text(encoding="utf-8"))


def test_commit_append(root: Path) -> None:
    """append 定稿成功：账本前进、state 记 N16、游标推到下一章。"""
    from commit_chapter import commit_chapter
    book = make_commit_book(root / "commit-append", "book", last=0)
    result = commit_chapter(book, 1)
    assert result["committed"] and not result["already"], result
    assert result["mode"] == "append", result
    assert _ledger(book)["last_committed_chapter"] == 1
    st = json.loads((book / "_pipeline" / "state.json").read_text(encoding="utf-8"))
    assert st["cursor"] == "C2.N09", st["cursor"]
    assert st["nodes"]["C1.N16"]["source"] == "manual", st["nodes"]


def test_commit_already(root: Path) -> None:
    """同内容重复定稿：already 短路，revision 不前进。"""
    from commit_chapter import commit_chapter
    book = make_commit_book(root / "commit-already", "book", last=0)
    commit_chapter(book, 1)
    rev_before = _ledger(book)["state_revision"]
    second = commit_chapter(book, 1)
    assert second["already"] is True, second
    assert _ledger(book)["state_revision"] == rev_before


def test_commit_gap(root: Path) -> None:
    """跳章定稿被拒，账本不动。"""
    from commit_chapter import CommitError, commit_chapter
    book = make_commit_book(root / "commit-gap", "book", last=0)
    try:
        commit_chapter(book, 3)
    except CommitError as exc:
        assert exc.code == "chapter_gap", exc.code
    else:
        raise AssertionError("跳章定稿必须被拒")
    assert _ledger(book)["last_committed_chapter"] == 0


def test_commit_revision(root: Path) -> None:
    """已定稿章重新定稿：章数不变、dirty 归零、revision 前进。"""
    from commit_chapter import commit_chapter
    book = make_commit_book(root / "commit-revision", "book", last=1, with_txn=False)
    body = book / "正文" / "第001章_测试.md"
    body.write_text(body.read_text(encoding="utf-8") + "\n补一句。", encoding="utf-8")
    result = commit_chapter(book, 1)
    assert result["mode"] == "revision", result
    assert result["book"]["chapters_committed"] == 1, result["book"]
    assert result["book"]["dirty_count"] == 0, result["book"]


def test_commit_tracking_missing(root: Path) -> None:
    """手写章缺事务：必须拒绝，且绝不生成空事务文件。"""
    from commit_chapter import CommitError, commit_chapter
    book = make_commit_book(root / "commit-no-txn", "book", last=0, with_txn=False)
    try:
        commit_chapter(book, 1)
    except CommitError as exc:
        assert exc.code == "tracking_missing", exc.code
    else:
        raise AssertionError("缺事务必须拒绝")
    assert not (book / ".story" / "work" / "第001章" / "tracking.json").exists(), \
        "绝不能自动建空事务（会静默污染账本）"


def test_commit_gate_blocked(root: Path) -> None:
    """闸门阻断：拒绝且账本不动。"""
    from commit_chapter import CommitError, commit_chapter
    book = make_commit_book(root / "commit-blocked", "book", last=0)
    os.environ["FAKE_STORY_STATUS"] = "blocked"
    try:
        try:
            commit_chapter(book, 1)
        except CommitError as exc:
            assert exc.code == "gate_blocked", exc.code
        else:
            raise AssertionError("闸门阻断必须拒绝")
        assert _ledger(book)["last_committed_chapter"] == 0
    finally:
        os.environ.pop("FAKE_STORY_STATUS", None)


def test_commit_busy(root: Path) -> None:
    """持锁期间拒绝并发定稿。"""
    from autopilot import acquire_next_lock, release_next_lock
    from commit_chapter import CommitError, commit_chapter
    book = make_commit_book(root / "commit-busy", "book", last=0)
    lock = acquire_next_lock(book)
    try:
        try:
            commit_chapter(book, 1)
        except CommitError as exc:
            assert exc.code == "chapter_busy", exc.code
        else:
            raise AssertionError("持锁时必须拒绝")
    finally:
        release_next_lock(lock)


def test_commit_rollback(root: Path) -> None:
    """提交失败：备份回滚生效（坏工具先写账本再报错）。"""
    from commit_chapter import CommitError, commit_chapter
    book = make_commit_book(root / "commit-rollback", "book", last=0)
    before = (book / "追踪" / "_tracking-state.json").read_bytes()
    cfg_path = Path(os.environ["WORKBENCH_CONFIG"])
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    storyctl = Path(cfg["STORYCTL"])
    write(storyctl, FAILING_COMMIT_STORYCTL)
    try:
        try:
            commit_chapter(book, 1)
        except CommitError as exc:
            assert exc.code == "quality_blocked", exc.code
        else:
            raise AssertionError("提交失败必须抛错")
    finally:
        write(storyctl, STATEFUL_STORYCTL)
    assert (book / "追踪" / "_tracking-state.json").read_bytes() == before, "失败必须回滚账本"
    assert list((book / "_pipeline" / "snapshots").glob("C1_before_commit_*")), "提交前必须留备份"


FAKE_TRACKING_COMMIT = r'''
import argparse, json, sys
from pathlib import Path

ap = argparse.ArgumentParser()
sub = ap.add_subparsers(dest="cmd", required=True)
d = sub.add_parser("draft")
for x in ("--project", "--out"):
    d.add_argument(x, required=True)
d.add_argument("--chapter", type=int, required=True)
d.add_argument("--force", action="store_true")
c = sub.add_parser("commit")
for x in ("--project", "--input"):
    c.add_argument(x, required=True)
a = ap.parse_args()
if a.cmd == "draft":
    txn = {"schema_version": 1, "mode": "revision", "chapter": a.chapter,
           "chapter_title": "重建章", "expected_state_revision": 1,
           "delta": {"result": "重建摘要", "character_changes": [], "foreshadow_changes": [],
                     "timeline_events": [], "constraints": [], "next_chapter_commitments": [],
                     "retired_context_items": [], "retired_characters": []},
           "context": {"position": {"volume": "第一卷", "volume_start_chapter": 1,
                                    "story_time": "1990", "scene": "厂办"},
                       "long_term_constraints": [], "active_character_names": [],
                       "continuity_risks": []},
           "character_snapshots": {}}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(txn, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"status": "ok"}))
    sys.exit(0)
print(json.dumps({"status": "ok"}))
'''


def test_commit_suite(root: Path) -> None:
    cfg_path = make_config(root / "commitcfg", "ready")
    write(root / "commitcfg" / "fake" / "storyctl.py", STATEFUL_STORYCTL)
    write(root / "commitcfg" / "fake" / "tracking_commit.py", FAKE_TRACKING_COMMIT)
    prev = os.environ.get("WORKBENCH_CONFIG")
    os.environ["WORKBENCH_CONFIG"] = str(cfg_path)
    try:
        test_commit_append(root)
        test_commit_already(root)
        test_commit_gap(root)
        test_commit_revision(root)
        test_commit_tracking_missing(root)
        test_commit_gate_blocked(root)
        test_commit_busy(root)
        test_commit_rollback(root)
    finally:
        if prev is None:
            os.environ.pop("WORKBENCH_CONFIG", None)
        else:
            os.environ["WORKBENCH_CONFIG"] = prev


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="wb-py-contracts-") as tmp:
        root = Path(tmp)
        os.environ["WORKBENCH_CONFIG"] = str(make_config(root, "ready"))
        test_n13(root)
        test_n16_idempotent(root)
        test_n21_blocked(root)
        test_next_lock(root)
        test_evidence_pattern_mismatch(root)
        test_done_token_via_env(root)
        test_block_and_resume_need_token(root)
        test_resume_resets_rounds(root)
        test_n14_done_rechecks_n13(root)
        test_n13_recheck_routes(root)
        test_rollback_best_snapshot(root)
        test_commit_suite(root)
    print("PY_CONTRACTS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
