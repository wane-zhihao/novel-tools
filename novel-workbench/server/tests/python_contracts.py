"""P1 契约回归：N13 状态矩阵、N16 幂等、N21 不可投阻断。

由 server/tests/python-contracts.test.mjs 调用；所有数据在临时目录。
"""
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
    write(fake / "genre.json", "{}\n")
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="wb-py-contracts-") as tmp:
        root = Path(tmp)
        os.environ["WORKBENCH_CONFIG"] = str(make_config(root, "ready"))
        test_n13(root)
        test_n16_idempotent(root)
        test_n21_blocked(root)
        test_next_lock(root)
    print("PY_CONTRACTS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
