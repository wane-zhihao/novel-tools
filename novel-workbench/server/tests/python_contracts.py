"""P1 契约回归：N13 状态矩阵、N16 幂等、N21 不可投阻断。

由 server/tests/python-contracts.test.mjs 调用；所有数据在临时目录。
"""
import hashlib
import json
import os
import shutil
import subprocess
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
    repeat = max(80, (words + 14) // 15)
    write(book / "正文" / "第001章_测试.md", "顶职名单今天换名字，换掉的正是他。" * repeat)
    write(book / "大纲" / "卷纲_第一卷.md", "- 主线：顶职名额与三天期限\n")
    write(book / "投稿包" / "全文.md", "# 全文\n" + "正文" * 100)
    write(book / "_pipeline" / "endgame_findings.json", '{"items": []}')
    write(book / "_pipeline" / "judgments_N20.json", '{"verdict": "pass", "items": []}')
    write(book / "追踪" / "_tracking-state.json", json.dumps({
        "last_committed_chapter": 1,
        "wordcount_records": {"1": {"actual": words}},
    }, ensure_ascii=False))
    return book


def test_body_file_rules_t2b(root: Path) -> None:
    """T-2/T-2b 的 Python 侧契约。

    - `_原稿_` 备份稿不算现役正文
    - 章号认 1–4 位（个人作者的「第1章_开局.md」不是隐形章）
    - 同章多候选全部返回，不替作者挑一个
    - 正文里声明的章号与文件名冲突 → 报歧义，不猜
    """
    from pipeline_spec import (body_files, chapter_number_in_body,
                               is_backup_body_file, parse_body_file_name)
    from commit_chapter import CommitError, _chapter_body

    book = root / "t2b"
    write(book / "正文" / "第001章_换名.md", "# 第001章 换名\n\n正文。\n")
    write(book / "正文" / "第2章_茶缸.md", "# 第2章 茶缸\n\n正文。\n")
    write(book / "正文" / "第002章_茶缸_原稿_20261028.md", "# 第002章 茶缸\n\n（旧稿）\n")
    write(book / "正文" / "第003章_名额.md", "# 第003章 名额\n\n正文。\n")
    write(book / "正文" / "第003章_名额_v2.md", "# 第003章 名额\n\n（改稿版本）\n")

    names = [p.name for p in body_files(book)]
    assert names == ["第001章_换名.md", "第2章_茶缸.md",
                     "第003章_名额.md", "第003章_名额_v2.md"], names
    assert is_backup_body_file("第002章_茶缸_原稿_20261028.md")
    assert not is_backup_body_file("第003章_名额_v2.md"), "改稿版本不是备份稿"
    assert parse_body_file_name("第2章_茶缸.md") == (2, "茶缸")
    assert parse_body_file_name("随手记.md") is None
    assert chapter_number_in_body("# 第005章 换名\n\n正文。\n") == 5
    assert chapter_number_in_body("# 榜上换了名\n\n正文。\n") is None

    # 同章多候选 → 定稿时报歧义，不猜
    try:
        _chapter_body(book, 3)
        raise AssertionError("同章多候选时定稿必须报歧义")
    except CommitError as exc:
        assert exc.code == "chapter_ambiguous", exc.code

    # 正文声明的章号与文件名冲突 → 定稿时报歧义，不猜
    conflict = root / "t2b-conflict"
    write(conflict / "正文" / "第001章_换名.md", "# 第005章 换名\n\n正文写的是第 5 章。\n")
    try:
        _chapter_body(conflict, 1)
        raise AssertionError("章号冲突时定稿必须报歧义")
    except CommitError as exc:
        assert exc.code == "chapter_ambiguous", exc.code


def test_outline_wordcount_contract(root: Path) -> None:
    config = make_config(root / "outline-contract-config", "ready")
    cfg = json.loads(config.read_text(encoding="utf-8"))
    cfg["NODE"] = os.environ.get("FAKE_NODE") or shutil.which("node")
    cfg["CHECK_OUTLINE"] = str(Path.home() / ".qoder" / "skills" / "story-long-write" / "scripts" / "check-outline-contract.js")
    write(config, json.dumps(cfg, ensure_ascii=False))
    previous_config = os.environ.get("WORKBENCH_CONFIG")
    os.environ["WORKBENCH_CONFIG"] = str(config)
    try:
        from inline_runner import _outline_gate

        book = root / "outline-contract-book"
        book.mkdir(parents=True, exist_ok=True)
        for filename, target, word_range, expected_rc, expected_id, detail_fragment in [
            ("目标低于正文硬下限.md", 1900, "2000-2300", 1, "outline.wordcount-contract", "目标须不低于 2000"),
            # 实现给出的是更精确的两条独立文案（目标 / 范围下限各一条，均带当前值），
            # 此处按实际文案断言——原先写的「范围须落在 2000-2400」是更早版本的措辞。
            ("范围低于正文硬下限.md", 2200, "1800-2400", 1, "outline.wordcount-contract", "范围下限须不低于 2000"),
            ("目标不在范围内.md", 2300, "2000-2200", 1, "outline.wordcount-contract", "目标 2300 不在明确字数范围"),
            ("目标范围有效.md", 2200, "2000-2400", 0, None, None),
            ("无明确范围目标有效.md", 2200, "", 0, None, None),
        ]:
            write(book / "大纲" / "细纲_第001章.md", f'''- 核心事件：测试事件
- 字数目标：{target}字
- 字数口径：visible_chars_v1
- 单元ID/位置：U01/第1章
- 目标情绪：紧张到释然
- 主角目标/关键选择：找到线索并决定追查
- 章首钩子：门外有人敲门
- 爽点：主角当面拆穿谎言
- 本章禁止提前释放：幕后身份
### 内容概括
- 起因：冲突开始
- 发展：线索出现
- 转折：证词矛盾
- 高潮：主角拿出证据
- 结尾：对方留下新线索
### 情节安排
### 人物关系和出场顺序
### 情节细化
| # | 情节点 | 功能标签 | 执行边界（禁＋放） |
| 1 | 找到证据 | 推进 | 禁：公布身份；放：当面质问 |
- 字数范围：{word_range}
''')
            rc, detail = _outline_gate(book, 1)
            report = json.loads(detail)
            assert rc == expected_rc, (filename, rc, detail)
            failures = report.get("failures") or []
            if expected_id:
                assert any(item.get("id") == expected_id for item in failures), report
                assert any(detail_fragment in item.get("detail", "") for item in failures), report
            else:
                assert report["ok"] is True, report
    finally:
        if previous_config is None:
            os.environ.pop("WORKBENCH_CONFIG", None)
        else:
            os.environ["WORKBENCH_CONFIG"] = previous_config


def test_next_exposes_node_attempt_budget(root: Path) -> None:
    config = make_config(root / "attempt-budget-config", "ready")
    book = make_state_book(root, "attempt-budget", cursor="C1.N12", current_chapter=0,
                           chapters_committed=0, plan=None, platform="zongheng",
                           run_started_at="2026-10-27T00:00:00+0800")
    write(book / "追踪" / "_tracking-state.json", json.dumps({
        "last_committed_chapter": 0,
        "wordcount_records": {},
    }, ensure_ascii=False))
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "autopilot.py"), "next", "--book", str(book)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "WORKBENCH_CONFIG": str(config)}, check=False,
    )
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    payload = json.loads(proc.stdout)
    assert payload["node"] == "C1.N12", payload
    assert payload["max_attempts"] == 4, payload


def test_next_renders_selected_platform_paths(root: Path) -> None:
    config = make_config(root / "platform-prompt-config", "ready")
    cfg = json.loads(config.read_text(encoding="utf-8"))
    cfg["PLATFORMS"]["zongheng"] = {
        "DIR": "F:/novel/zongheng",
        "GENRE_BASE": "F:/novel/zongheng/harness/genre-baselines.json",
        "STYLE_ANCHOR": "F:/novel/zongheng/harness/style-anchor.json",
        "GENRES": ["dushi"],
    }
    write(config, json.dumps(cfg, ensure_ascii=False))
    book = make_state_book(root, "platform-prompt", cursor="C1.N12", current_chapter=0,
                           chapters_committed=0, plan=None, platform="zongheng", genre="dushi")
    write(book / "追踪" / "_tracking-state.json", json.dumps({
        "last_committed_chapter": 0,
        "wordcount_records": {},
    }, ensure_ascii=False))
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "autopilot.py"), "next", "--book", str(book)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "WORKBENCH_CONFIG": str(config)}, check=False,
    )
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    payload = json.loads(proc.stdout)
    assert "F:/novel/zongheng/harness/style-anchor.json" in payload["subagent_prompt"], payload
    assert "fake/harness/style-anchor.json" not in payload["subagent_prompt"], payload


def test_manifest_uses_book_platform(root: Path) -> None:
    config = make_config(root / "manifest-config", "ready")
    cfg = json.loads(config.read_text(encoding="utf-8"))
    cfg["PLATFORMS"]["zongheng"] = {
        "DIR": "F:/novel/zongheng",
        "GENRE_BASE": "F:/novel/zongheng/harness/genre-baselines.json",
        "STYLE_ANCHOR": "F:/novel/zongheng/harness/style-anchor.json",
    }
    write(config, json.dumps(cfg, ensure_ascii=False))
    env = {**os.environ, "WORKBENCH_CONFIG": str(config)}
    script = SCRIPTS / "autopilot.py"
    book = root / "manifest-book"
    book.mkdir(parents=True, exist_ok=True)
    init = subprocess.run(
        [sys.executable, str(script), "init", "--title", "纵横 manifest 测试",
         "--genre", "dushi", "--target-words", "6000", "--max-chapters", "3",
         "--platform", "zongheng", "--vol-size", "3", "--book", str(book)],
        capture_output=True, text=True, encoding="utf-8", env=env, check=False,
    )
    assert init.returncode == 0, (init.returncode, init.stdout, init.stderr)
    manifest = json.loads((book / "_pipeline" / "workbench.json").read_text(encoding="utf-8"))
    assert manifest["platform"] == "F:/novel/zongheng", manifest


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


def test_n21_volume_shortfall_report(root: Path) -> None:
    from inline_runner import _package
    book = make_book(root / "n21-short-volume", words=20000)
    st = {"book": "短卷测试", "genre": "niandai", "chapters_committed": 1,
          "max_chapters": 3, "plan": {"vol_size": 3, "vol_llm_cap": 2},
          "degraded_volumes": [1], "current_chapter": 1}
    st["nodes"] = {"V1.N20": {"status": "done"}}
    write(book / "_pipeline" / "judgments_V1.N20.json", '{"verdict":"pass","items":[]}')
    write(book / "_pipeline" / "judgments_N20g.json", '{"verdict":"pass","items":[]}')
    rc, message = _package(book, st)
    assert rc == 0, message
    report = (book / "投稿包" / "体检报告.md").read_text(encoding="utf-8")
    assert "第1卷 1/3章" in report, report
    assert "全书汇总终审" in report, report
    write(book / "_pipeline" / "judgments_N20g.json",
          '{"verdict":"block","items":[{"id":"arc","criterion":"主线收束","verdict":"fail","level":"B"}]}')
    rc, message = _package(book, st)
    assert rc != 0 and "NOT_SUBMITTABLE" in message, message
    report = (book / "投稿包" / "体检报告.md").read_text(encoding="utf-8")
    assert "主线收束" in report, report
    book = make_book(root / "n21-advisory", words=20000)
    st["book"] = "卷末遗留测试"
    st["degraded_volumes"] = []
    st["nodes"] = {"V1.N20": {"status": "done", "advisory": True}}
    st["volume_advisories"] = {"V1.N20": {"summary": "主线收束不足", "items": [
        {"id": "arc", "criterion": "主线收束不足", "fix": "补足主角抉择后果"}]}}
    write(book / "_pipeline" / "judgments_V1.N20.json", json.dumps({
        "verdict": "block", "items": [{"id": "arc", "criterion": "主线收束不足",
                                           "verdict": "fail", "level": "B"}]},
        ensure_ascii=False))
    write(book / "_pipeline" / "judgments_N20g.json", '{"verdict":"pass","items":[]}')
    from inline_runner import _merge_global
    assert _merge_global(book, st)[0] == 0
    summary = (book / "投稿包" / "全书概要.md").read_text(encoding="utf-8")
    assert "V1.N20" in summary and "补足主角抉择后果" in summary, summary
    from autopilot import _write_relay
    relay = book / "_pipeline" / "relay.json"
    st.update({"current_chapter": 1, "chapters_committed": 1,
               "volume_shortfall": {"1": {"planned": 3, "actual": 1}},
               "nodes": {"V1.N07": {"status": "done", "at": "2026-10-26T12:00:00+0800"}}})
    _write_relay(book, st, "测试卷预算缺口")
    relay_doc = json.loads(relay.read_text(encoding="utf-8"))
    assert relay_doc["volume_shortfall"]["1"] == {"planned": 3, "actual": 1}
    assert relay_doc["last_node"]["id"] == "V1.N07", relay_doc
    assert relay_doc["last_volume"] == 1, relay_doc


def test_volume_n20_routes_through_global_gate_and_package(root: Path) -> None:
    """计划书完整走 V.N20 → N17g → N20g → N21，卷末遗留由全书审查收口。"""
    import contextlib
    import io
    from autopilot import load_state, write_token
    from commands import cmd_done
    from inline_runner import _merge_global, _mainline, _package
    from pipeline_spec import next_cursor

    config = make_config(root / "volume-route-config", "ready")
    previous_config = os.environ.get("WORKBENCH_CONFIG")
    os.environ["WORKBENCH_CONFIG"] = str(config)
    try:
        book = make_state_book(
            root, "volume-n20-route", max_chapters=3, current_chapter=3,
            chapters_committed=3, words_total=21000, cursor="V1.N20",
            plan={"vol_size": 3, "vol_llm_cap": 80},
            nodes={"V1.N17": {"status": "done"},
                   "V1.N18": {"status": "done"},
                   "V1.N19": {"status": "done"}},
        )
        for chapter in range(1, 4):
            write(book / "正文" / f"第{chapter:03d}章_测试.md", f"第{chapter}章内容。" * 400)
        write(book / "大纲" / "卷纲_第1卷.md", "- 主线：主角查清账目并追回损失\n")
        volume_full = "# 第一卷\n" + "卷末主线遗留问题仍未解决，需要后续审查。\n" * 20
        write(book / "投稿包" / "卷1_全文.md", volume_full)
        write(book / "_pipeline" / "judgments_V1.N20.json", json.dumps({
            "verdict": "block", "summary": "卷末主线收束仍有遗留。", "items": [{
                "id": "arc", "criterion": "主线收束不足", "quote": "卷末主线遗留问题仍未解决需要后续审查",
                "source": "投稿包/卷1_全文.md", "verdict": "fail", "level": "B",
                "fix": "概要应记录此遗留供全书终审。",
            }],
        }, ensure_ascii=False))
        write_token(book, "tok-v1n20-route", "V1.N20")
        with contextlib.redirect_stdout(io.StringIO()):
            assert cmd_done(Args(book=str(book), node="V1.N20", token="tok-v1n20-route",
                                 evidence=["_pipeline/judgments_V1.N20.json"])) == 0
        st = load_state(book)
        assert st["cursor"] == "N17g", st["cursor"]
        assert st["nodes"]["V1.N20"]["status"] == "done", st["nodes"]
        assert "V1.N20" in st["volume_advisories"], st["volume_advisories"]

        assert _merge_global(book, st)[0] == 0
        summary = (book / "投稿包" / "全书概要.md").read_text(encoding="utf-8")
        assert "V1.N20" in summary and "概要应记录此遗留" in summary, summary
        assert next_cursor(st) == "N20g"
        st["cursor"] = "N20g"
        write(book / "_pipeline" / "state.json", json.dumps(st, ensure_ascii=False))
        global_quote = "概要应记录此遗留供全书终审"
        write(book / "_pipeline" / "judgments_N20g.json", json.dumps({
            "verdict": "pass", "summary": "全书检查通过，已处理卷末遗留。", "items": [{
                "id": "arc-review", "criterion": "卷末遗留已纳入全书审查",
                "quote": global_quote, "source": "投稿包/全书概要.md",
                "verdict": "pass", "level": "A",
            }],
        }, ensure_ascii=False))
        write_token(book, "tok-n20g-route", "N20g")
        with contextlib.redirect_stdout(io.StringIO()):
            assert cmd_done(Args(book=str(book), node="N20g", token="tok-n20g-route",
                                 evidence=["_pipeline/judgments_N20g.json"])) == 0
        st = load_state(book)
        assert st["cursor"] == "N21", st["cursor"]

        tracking = {"last_committed_chapter": 3,
                    "wordcount_records": {str(n): {"actual": 7000} for n in range(1, 4)}}
        write(book / "追踪" / "_tracking-state.json", json.dumps(tracking, ensure_ascii=False))
        for chapter in range(1, 4):
            write(book / "正文" / f"第{chapter:03d}章_测试.md", f"第{chapter}章内容。" * 1800)
        rc, message = _package(book, st)
        assert rc == 0, message
        report = (book / "投稿包" / "体检报告.md").read_text(encoding="utf-8")
        assert "第1卷：block （已转全书汇总终审）" in report and "全书 pass" in report, report
        assert _mainline(book, st) == "主角查清账目并追回损失"
        email = (book / "投稿包" / "投稿邮件.md").read_text(encoding="utf-8")
        assert "卷纲_第1卷.md" in email and "目录_第1卷.md" in email, email
        assert "主角查清账目并追回损失" in email, email
    finally:
        if previous_config is None:
            os.environ.pop("WORKBENCH_CONFIG", None)
        else:
            os.environ["WORKBENCH_CONFIG"] = previous_config


def test_status_exposes_volume_degradation(root: Path) -> None:
    import contextlib
    import io
    from autopilot import cmd_status
    book = make_state_book(root, "status-volume", plan={"vol_size": 3, "vol_llm_cap": 2},
                           degraded_volumes=[1],
                           volume_shortfall={"1": {"planned": 3, "actual": 1}})
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert cmd_status(Args(book=str(book))) == 0
    status = json.loads(output.getvalue())
    assert status["degraded_volumes"] == [1], status
    assert status["volume_shortfall"]["1"] == {"planned": 3, "actual": 1}, status


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

    # DL-004 对齐：可见字符 >= 20000，但纯汉字 < 20000 且分歧率 > 10% 必须 fail-closed 阻断
    book_div = make_book(root / "n21-divergence-block", words=20500)
    body_text = ("汉字" * 8500) + ("abcde," * 700)  # CJK = 17000, visible = 21200, div = 19.8%
    write(book_div / "正文" / "第001章_测试.md", body_text)
    rc, message = _package(book_div, st)
    assert rc != 0, "Divergence > 10% and CJK < 20000 should block submission"
    assert "NOT_SUBMITTABLE" in message, message
    assert "分歧率" in message, message
    report = (book_div / "投稿包" / "体检报告.md").read_text(encoding="utf-8")
    assert "双口径分歧阻断（DL-004）" in report, report


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


def test_resume_preserves_scoped_budget_reset(root: Path) -> None:
    """普通 resume 保留失败预算；显式按 scope 重置只清目标预算。"""
    from commands import cmd_resume
    from autopilot import load_state
    book = make_state_book(
        root, "b2", fix_rounds={"1": 2, "2": 1}, n20_rounds=2,
        global_fix_rounds={"V1.N08": 1, "N20g": 1},
        budget={"C1": {"rounds": 2}, "C2": {"rounds": 1},
                "V1.N08": {"rounds": 1}, "N20g": {"rounds": 1}},
        blocked_reason="终停待人工")
    rc = cmd_resume(Args(book=str(book), token="", force=True))
    assert rc == 0
    st = load_state(book)
    assert st["fix_rounds"] == {"1": 2, "2": 1}, st["fix_rounds"]
    assert st["n20_rounds"] == 2, st["n20_rounds"]
    assert st["budget"]["C1"]["rounds"] == 2, st["budget"]
    assert "run_started_at" in st

    st["blocked_reason"] = "再次终停"
    write(book / "_pipeline" / "state.json", json.dumps(st, ensure_ascii=False))
    rc = cmd_resume(Args(book=str(book), token="", force=True,
                         reset_budget=True, scope="C1"))
    assert rc == 0
    st = load_state(book)
    assert st["fix_rounds"] == {"2": 1}, st["fix_rounds"]
    assert "C1" not in st["budget"] and "C2" in st["budget"], st["budget"]
    assert st["fix_rounds"] == {"2": 1}, st["fix_rounds"]
    assert st["n20_rounds"] == 2, st["n20_rounds"]
    assert st["global_fix_rounds"] == {"V1.N08": 1, "N20g": 1}, st["global_fix_rounds"]

    st["blocked_reason"] = "无效范围不得改变预算"
    write(book / "_pipeline" / "state.json", json.dumps(st, ensure_ascii=False))
    rc = cmd_resume(Args(book=str(book), token="", force=True,
                         reset_budget=True, scope="N17g"))
    assert rc == 2, "未知预算范围必须拒绝"
    assert load_state(book)["budget"] == st["budget"]

    st["blocked_reason"] = "全局重置"
    write(book / "_pipeline" / "state.json", json.dumps(st, ensure_ascii=False))
    rc = cmd_resume(Args(book=str(book), token="", force=True,
                         reset_budget=True, scope="GLOBAL"))
    assert rc == 0
    st = load_state(book)
    assert st["fix_rounds"] == {}, st["fix_rounds"]
    assert st["budget"] == {}, st["budget"]
    assert st["global_fix_rounds"] == {}, st["global_fix_rounds"]
    assert st["n20_rounds"] == 0, st["n20_rounds"]


def test_resume_preserves_n14_recheck(root: Path) -> None:
    """resume 中断恢复不能丢弃 N14 改写后的 N13 复检标记。"""
    from commands import cmd_resume
    from autopilot import load_state
    book = make_state_book(root, "resume-recheck", cursor="C1.N13",
                           recheck_chapter=1)
    rc = cmd_resume(Args(book=str(book), token="", force=True))
    assert rc == 0
    st = load_state(book)
    assert st["cursor"] == "C1.N13", st["cursor"]
    assert st.get("recheck_chapter") == 1, st


def test_tracking_heal_routes_volume_boundary_to_audit(root: Path) -> None:
    """tracking 自愈发现卷末已封板时，必须先进入该卷 N17 审核。"""
    from autopilot import _heal_from_tracking, load_state
    book = make_state_book(root, "heal-volume-boundary", plan={"vol_size": 2},
                           max_chapters=4, current_chapter=1,
                           chapters_committed=1, words_total=0, cursor="C2.N16")
    write(book / "追踪" / "_tracking-state.json", json.dumps({
        "last_committed_chapter": 2,
        "wordcount_records": {"1": {"actual": 2000}, "2": {"actual": 2100}},
    }, ensure_ascii=False))
    st = load_state(book)
    _heal_from_tracking(book, st)
    st = load_state(book)
    assert st["cursor"] == "V1.N17", st["cursor"]

    final_book = make_state_book(root, "heal-final-volume-boundary",
                                 plan={"vol_size": 3}, max_chapters=3,
                                 current_chapter=2, chapters_committed=2,
                                 words_total=0, cursor="C3.N16")
    write(final_book / "追踪" / "_tracking-state.json", json.dumps({
        "last_committed_chapter": 3,
        "wordcount_records": {str(n): {"actual": 2000} for n in range(1, 4)},
    }, ensure_ascii=False))
    st = load_state(final_book)
    _heal_from_tracking(final_book, st)
    st = load_state(final_book)
    assert st["cursor"] == "V1.N17", st["cursor"]


def test_next_cursor_runs_final_volume_audit_before_global(root: Path) -> None:
    """计划书最后一章封板后仍须执行末卷 N17-N20，再进 N17g。"""
    from pipeline_spec import next_cursor
    st = {"cursor": "C4.N16", "current_chapter": 3,
          "chapters_committed": 3, "max_chapters": 4,
          "plan": {"vol_size": 2}, "words_total": 0, "target_words": 20000}
    assert next_cursor(st) == "V2.N17"
    st["cursor"] = "V2.N20"
    assert next_cursor(st) == "N17g"


def test_volume_budget_at_volume_terminal_uses_terminal_volume(root: Path) -> None:
    """卷末终审发令前应按节点所属卷检查预算，不应误读下一章所在卷。"""
    import contextlib
    import io
    from autopilot import cmd_next
    book = make_state_book(
        root, "budget-volume-terminal", cursor="V1.N20", current_chapter=2,
        chapters_committed=2, max_chapters=4,
        plan={"vol_size": 2, "vol_llm_cap": 2},
        nodes={"V1.N07": {"status": "done"}, "V1.N08": {"status": "done"}})
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        rc = cmd_next(Args(book=str(book)))
    assert rc == 0, output.getvalue()
    result = json.loads(output.getvalue())
    assert result["status"] == "volume_degraded", result
    assert result["cursor"] == "V1.N17", result


def test_block_budget_uses_chapter_scope() -> None:
    from commands import _record_block_budget
    st = {}
    rounds, total, _ = _record_block_budget(
        st, "C3.N15", [{"id": "continuity", "blocking": True}])
    assert (rounds, total) == (1, 1)
    assert st["budget"]["C3"]["rounds"] == 1, st["budget"]


def test_terminal_block_writes_relay_reason(root: Path) -> None:
    from commands import _terminal_block
    from autopilot import load_state
    book = make_state_book(root, "terminal-relay", cursor="N20")
    judgment = book / "_pipeline" / "judgments_N20.json"
    write(judgment, '{"verdict":"block","items":[]}')
    st = load_state(book)
    rc = _terminal_block(book, st, "N20", "预算停止测试", judgment)
    assert rc == 1
    relay = json.loads((book / "_pipeline" / "relay.json").read_text(encoding="utf-8"))
    assert relay["stop_reason"] == "预算停止测试", relay
    assert relay["blocked_reason"] == "预算停止测试", relay


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




def test_init_persists_platform_volume_and_one_line(root: Path) -> None:
    """开书字段必须真的落进 state：platform / plan.vol_size / one_line。

    此前 JS 层从不传 --platform / --vol-size，Python 支持了也没人用；
    one_line 更是没有任何消费者，属于纯空转字段。
    """
    config = make_config(root / "init-fields-config", "ready")
    script = SCRIPTS / "autopilot.py"
    book = root / "init-fields-book"
    book.mkdir(parents=True, exist_ok=True)
    init = subprocess.run(
        [sys.executable, str(script), "init", "--title", "开书字段",
         "--genre", "shuangwen", "--target-words", "20000", "--max-chapters", "12",
         "--platform", "zongheng", "--vol-size", "20",
         "--one-line", "重回九零，从厂办开始", "--book", str(book)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "WORKBENCH_CONFIG": str(config)}, check=False,
    )
    assert init.returncode == 0, (init.returncode, init.stdout, init.stderr)
    st = json.loads((book / "_pipeline" / "state.json").read_text(encoding="utf-8"))
    assert st["platform"] == "zongheng", st.get("platform")
    assert (st.get("plan") or {}).get("vol_size") == 20, st.get("plan")
    assert st.get("one_line") == "重回九零，从厂办开始", st.get("one_line")


def test_render_prompt_injects_one_line(root: Path) -> None:
    """{ONE_LINE} 必须被替换：填了就带原话，没填也要给人话，不能漏出占位符或 None。"""
    config = make_config(root / "one-line-config", "ready")
    previous = os.environ.get("WORKBENCH_CONFIG")
    os.environ["WORKBENCH_CONFIG"] = str(config)
    try:
        from autopilot import render_prompt

        book = root / "one-line-book"
        filled = render_prompt("卖点：{ONE_LINE}", book, {"one_line": "重回九零，从厂办开始"})
        assert "重回九零，从厂办开始" in filled, filled

        empty = render_prompt("卖点：{ONE_LINE}", book, {})
        assert "{ONE_LINE}" not in empty, empty
        assert "None" not in empty, empty
        assert len(empty) > 4, empty
    finally:
        if previous is None:
            os.environ.pop("WORKBENCH_CONFIG", None)
        else:
            os.environ["WORKBENCH_CONFIG"] = previous


def test_tracking_fill_dry_run_then_apply(root: Path) -> None:
    """tracking-fill：默认预演不写盘，--apply 才把账本值校正进 state。

    背景：_heal_from_tracking 只在 next 时跑，而投稿预检读的是 state 里的
    words_total。漂移后、下次运行前，预检会拿错数字判断字数。
    """
    config = make_config(root / "tracking-fill-config", "ready")
    book = make_state_book(root, "tracking-fill", cursor="C1.N09",
                           current_chapter=0, chapters_committed=0, words_total=0)
    # 账本说 2 章已封板、共 4500 字；state 一无所知
    write(book / "追踪" / "_tracking-state.json", json.dumps({
        "last_committed_chapter": 2,
        "wordcount_records": {"1": {"actual": 2200}, "2": {"actual": 2300}},
    }, ensure_ascii=False))
    script = SCRIPTS / "autopilot.py"
    env = {**os.environ, "WORKBENCH_CONFIG": str(config)}

    dry = subprocess.run([sys.executable, str(script), "tracking-fill", "--book", str(book)],
                         capture_output=True, text=True, encoding="utf-8", env=env, check=False)
    assert dry.returncode == 0, (dry.returncode, dry.stdout, dry.stderr)
    out = json.loads(dry.stdout)
    assert out["applied"] is False, out
    assert out["changed"] is True, out
    assert out["after"]["words_total"] == 4500, out
    # 预演绝不能写盘
    on_disk = json.loads((book / "_pipeline" / "state.json").read_text(encoding="utf-8"))
    assert on_disk["words_total"] == 0, "dry-run 竟然改了 state"

    applied = subprocess.run([sys.executable, str(script), "tracking-fill", "--book", str(book), "--apply"],
                             capture_output=True, text=True, encoding="utf-8", env=env, check=False)
    assert applied.returncode == 0, (applied.returncode, applied.stdout, applied.stderr)
    st = json.loads((book / "_pipeline" / "state.json").read_text(encoding="utf-8"))
    assert st["words_total"] == 4500, st["words_total"]
    assert st["chapters_committed"] == 2, st["chapters_committed"]
    # 游标从第 1 章推进到第 3 章起点（账本说第 2 章已封板）
    assert st["cursor"].startswith("C3."), st["cursor"]

    # 幂等：再跑一次不该再产生变更
    again = subprocess.run([sys.executable, str(script), "tracking-fill", "--book", str(book)],
                           capture_output=True, text=True, encoding="utf-8", env=env, check=False)
    assert again.returncode == 0
    assert json.loads(again.stdout)["changed"] is False, again.stdout


def test_mechanical_fix_contract() -> None:
    """机械修复器夹具必须全过（见 test_mechanical_fix.py）。

    为什么单独一个文件而不是内联：夹具里每条都带「期望产物 + 主语残缺检查」，
    放在这里会让本文件再长 100 行；而且它值得被单独运行来定位失败。
    """
    script = Path(__file__).resolve().parent / "test_mechanical_fix.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("机械修复器夹具未通过（MECHANICAL_FIX_FAILED）")


def test_trim_to_band_contract() -> None:
    """字数裁剪器夹具必须全过（见 test_trim_to_band.py）。

    裁剪器的失败模式很危险：它可能**删掉承载信息的段落**来凑字数
    （首版就删掉了「那张纸上有三个字：抚恤金。」）。夹具用
    MUST_SURVIVE 三处关键内容做守卫，任何一处丢失即失败。
    """
    script = Path(__file__).resolve().parent / "test_trim_to_band.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("字数裁剪器夹具未通过（TRIM_TO_BAND_FAILED）")


def test_context_md_guard_contract() -> None:
    """N11 产物防篡改守卫夹具必须全过（见 test_context_md_guard.py）。

    `追踪/上下文.md` 由脚本节点 N11 生成，是 writer 的**只读输入**。
    实测第 6 章的 writer 把它整个覆写成了另一种七栏结构，导致下一章
    读到缺 S2 上一章锚点 / S5 文风锚 / S7 事务说明的坏文件。
    """
    script = Path(__file__).resolve().parent / "test_context_md_guard.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("上下文防篡改守卫夹具未通过（CONTEXT_GUARD_FAILED）")


def test_foreshadow_due_contract() -> None:
    """到期伏笔显式标注夹具必须全过（见 test_foreshadow_due.py）。

    伏笔只写「埋第2章｜第6章」时，writer 看到「第6章」无法判断是本章到期
    还是已经过了。实测 F005/F008 计划回收章都是 6，第 6 章封板后仍未回收。
    修法：N11 把「计划回收章 ≤ 本章」且未回收的伏笔显式列进 S6，区分到期/逾期。
    """
    script = Path(__file__).resolve().parent / "test_foreshadow_due.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("到期伏笔标注夹具未通过（FORESHADOW_DUE_FAILED）")


def test_causal_diversity_contract() -> None:
    """因果词多样性检测夹具必须全过（见 test_causal_diversity.py）。

    因果**总量**指标测不出 AI 指纹：实测「果然」x44 时 causal_per_1000
    只报 3.07（区间内）、闸门全绿。只有占比看得出。
    阈值 0.60 来自 18 个七猫年代签约样本实测上界；**只报 advisory 不阻断**
    （没过配对判别验证，按项目不变式不进 gates.json 区间判定）。
    """
    script = Path(__file__).resolve().parent / "test_causal_diversity.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("因果词多样性夹具未通过（CAUSAL_DIVERSITY_FAILED）")


def test_partial_body_digest_contract() -> None:
    """断点续写 digest 夹具必须全过（见 test_partial_body_digest.py）。

    实测第 8 章的 writer 写到 1511 字卡死在高潮对峙戏，磁盘留下半截正文
    就再也动不了；新 subagent 从零重写会丢掉已有内容，编排者只能手工续写。
    修法：把已有正文尾部注入 N12 提示词，让接手者接着写。
    """
    script = Path(__file__).resolve().parent / "test_partial_body_digest.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("断点续写 digest 夹具未通过（PARTIAL_BODY_DIGEST_FAILED）")


def test_shrink_delta_contract() -> None:
    """delta 自动压缩器夹具必须全过（见 test_shrink_delta.py）。

    实测第 8 章事务写完 3087 字节、上限 3072——**差 15 字节**被打回，
    内容完全合格却要 writer 回来再改一轮。修法：超限时自动截断长度类字段，
    不碰 id / status / 枚举 / 必填结构。
    """
    script = Path(__file__).resolve().parent / "test_shrink_delta.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("delta 自动压缩夹具未通过（SHRINK_DELTA_FAILED）")


def test_ledger_integrity_contract() -> None:
    """账本完整性与封板后漂移夹具必须全过（见 test_ledger_integrity.py）。

    ① 旧压缩器用固定长度 +「…」后缀截断字段，「…」被 storyctl 当成**原文**
       写进账本——截断伪装成了原始内容。实测 E002–E005 被截成半句。
    ② sha256 此前只记录不比对：第 8 章 N16 封板后正文又被改（2383 字），
       账本仍记 2348 字，伏笔/漂移/字数基线全部错位。
    """
    script = Path(__file__).resolve().parent / "test_ledger_integrity.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("账本完整性夹具未通过（LEDGER_INTEGRITY_FAILED）")


def test_action_beat_contract() -> None:
    """动作节拍密度检测夹具必须全过（见 test_action_beat.py）。

    2026-11-17 拿到七猫侧第 2 个真实拒稿本后，action_beat_per_1000 的 AUC=0.972，
    只差「拒稿书≥3」这一条门槛 → 只能作定位线索、不能作改稿目标（判别力不变式）。

    **首版检测器自己实现了一遍口径，对两本真实拒稿样本都报 0 条**——
    口径错了等于没测。夹具用真实拒稿样本当断言基准：口径再错一次立刻失败。
    """
    script = Path(__file__).resolve().parent / "test_action_beat.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("动作节拍密度夹具未通过（ACTION_BEAT_FAILED）")


def test_shrink_delta_floor_contract() -> None:
    """压缩器保底长度夹具必须全过（见 test_shrink_delta_floor.py）。

    2026-11-17 实测：`cut = max(4, len-over-8)` 在 over 大于字段长度时下溢成负数，
    max 取 4 —— 一句 60 字的 result 被砍成「陆明用爹」四个字，然后原样写进账本与
    上下文.md。**这不是截断，是信息摧毁，且伪装成了原始内容。**
    """
    script = Path(__file__).resolve().parent / "test_shrink_delta_floor.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("压缩器保底长度夹具未通过（SHRINK_DELTA_FLOOR_FAILED）")


def test_relative_days_contract() -> None:
    """相对日互斥检查夹具必须全过（见 test_relative_days.py）。

    2026-11-18 第二轮红队挖出全书 7 处日期网断裂（同一份报告的落款日在
    相隔 ≥3 天的两章里都叫「前天」；父亲死亡时间三处互斥；顶职半年却写
    「这两年签字都落在他一人身上」；月产 87 件与停一天少交 24 件差 8 倍）。

    **这类缺陷是可计算的**，但当时工具查不出——账本 story_time 全是
    「1983年某日」，没有具体日期锚。
    """
    script = Path(__file__).resolve().parent / "test_relative_days.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("相对日互斥夹具未通过（RELATIVE_DAYS_FAILED）")


def test_cross_chapter_duplicate_contract() -> None:
    """跨章逐字重复检测夹具必须全过（见 test_cross_chapter_duplicate.py）。

    2026-11-18 三轮红队反复挖出「爽点只有一个模子」：
    第3↔4章 26 字逐字相同、第7↔9章塞尺句 18 字逐字相同。
    **我自己也制造过新的逐字重复**——为消除第9章的重复而改成与第7章一字不差。
    人工逐章比对抓不到这种自己制造的重复，必须靠最长公共子串检测。
    """
    script = Path(__file__).resolve().parent / "test_cross_chapter_duplicate.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("跨章逐字重复夹具未通过（CROSS_CHAPTER_DUP_FAILED）")


def test_daychain_contract() -> None:
    """日链一致性校验夹具必须全过（见 test_daychain.py）。

    2026-11-19：四轮红队最重的一类缺陷是**日期网断裂**（8+ 处）——
    同一份报告的落款日在相隔 3 天的两章里都叫「前天」；第5章「封条是事故
    前一天贴的」而第3/4章锁死封条于事故**后**一天；第9章「这两年…鉴定签字」
    而签字权 4 天前才拿到。

    **这些全部可计算**——把每章钉到绝对日（D0…D7）就能交叉校验。
    此前查不出，因为账本 story_time 全是「1983年某日」，没有绝对日锚。
    """
    script = Path(__file__).resolve().parent / "test_daychain.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("日链一致性夹具未通过（DAYCHAIN_FAILED）")


def test_coldstart_contract() -> None:
    """冷启动夹具必须全过（见 test_coldstart.py）。

    2026-11-19：四轮红队的「日期网断裂」检测可自动检出，但前提是书里有
    `追踪/时间线/日链.md` 与 `追踪/复沓锚句.md`——**此前这两样只在被调试过
    的书里手工长出来**，新书一开就没有，于是这些检测对新书全部失效
    （冷启动实测：日链检测对新书静默返回 0 条，既不报也不提示）。

    修法：`autopilot.py init` 生成两个骨架模板；缺基准文件时检测器
    **明确报出**而不是静默通过。
    """
    script = Path(__file__).resolve().parent / "test_coldstart.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("冷启动夹具未通过（COLDSTART_FAILED）")


def test_unattended_contract() -> None:
    """无人值守夹具必须全过（见 test_unattended.py）。

    2026-11-19：本会话 subagent 静默死亡 10+ 次，每次都要求人工发现 → resume → 重派。
    **一条每隔一次就断的流水线，不能声称「工具能完成目标」。**

    P2 运行时（server/cli.mjs run）由后端自己调模型、写证据、交令牌，
    **没有对话 subagent 这个失败模式**——实测一次无人值守推进 12 个节点。

    用 --mock（零成本）验证：状态机能无人值守前进，且守卫会在该拦的地方
    （mock 不填追踪事务 → tracking_incomplete）精确停下。拦不住才是缺陷。
    """
    script = Path(__file__).resolve().parent / "test_unattended.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("无人值守夹具未通过（UNATTENDED_FAILED）")


def test_wordcount_caliber_contract() -> None:
    """字数双口径夹具必须全过（见 test_wordcount_caliber.py）。

    2026-11-19 实测：同一套工具里两个检查器对**同一份稿子**给出相反结论——
    `体检报告.md` 说 20903 字可投，`qimao_check.py` 报 BLOCK Q01（纯汉字 17822）。

    根因：Q01 旧实现「判定仍按纯汉字（保持历史可比）」，而平台 api_words
    **实测与可见字符同口径、与纯汉字 0 章相等**（24 章实测）。拿汉字口径去
    BLOCK 一份平台会收的稿子，是**假 BLOCK**，会挡住合法投稿。

    修法：可见字符不达标才 BLOCK；仅汉字不足则降 CONFIRM（人工后台核对）。
    """
    script = Path(__file__).resolve().parent / "test_wordcount_caliber.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("字数双口径夹具未通过（WORDCOUNT_CALIBER_FAILED）")


def test_gate_prompt_schema_contract() -> None:
    """闸门提示词必须自带裁决结构（见 test_gate_prompt_schema.py）。

    2026-11-19 冷启动实测：`N06 设定校验` 的提示词只写「格式同 N04」，
    而 subagent **看不到 N04 的提示词**，于是自己发明键名（`checks` / 顶层 `quote`），
    `done` 以 `quote_check_failed: items 为空` 拒收——**任何书跑到 N06 都会卡住**。

    这类缺陷的特点：**提示词之间互相引用，但引用目标对执行者不可见。**
    写法上「格式同 N04」读起来自然，运行时等于没说。

    本夹具要求每个 verify 节点自带完整 JSON 结构、无跨节点引用、说明 items 不可为空。
    """
    script = Path(__file__).resolve().parent / "test_gate_prompt_schema.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("闸门提示词结构夹具未通过（GATE_SCHEMA_FAILED）")


def test_judgment_normalize_contract() -> None:
    """裁决归一化层必须全过（见 test_judgment_normalize.py）。

    2026-11-19 冷启动实测：judge 写了一份内容全对的裁决（5 个维度都查了、
    引文是从 `设定/文风.md` 逐字抄的），只因把裁决项放在 `checks` 而不是
    `items`、键名用 `name`/`result`/`detail`，就被 `done` 以
    `quote_check_failed: items 为空` **整份判废**。被废掉的是语义有效的工作，
    丢掉的只是接口约定；报错又不自解释（只说「items 为空」），
    加上对话路径没有 P2 那样的自愈回路，一轮好裁决就此空转。

    修法三层：① 提示词自带结构（test_gate_prompt_schema）
    ② 归一化层（键名/容器别名 → canonical；散文回捞逐字引文；引文命中但
    source 写错 → 改指）③ 报错自带结构/规则/允许清单/可抄示例，失败落盘
    state.last_failure 并在下次 `next` 回灌「上一轮失败反馈」。

    实质要求一条没放松：fail 项必须给命中源文件的逐字引文；整份裁决至少
    要有一条命中引文；引文不命中 / source 越界 / block 无 fail 项 一律拒。
    """
    script = Path(__file__).resolve().parent / "test_judgment_normalize.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-3000:])
        print(proc.stderr[-1000:])
        raise AssertionError("裁决归一化夹具未通过（JUDGMENT_NORMALIZE_FAILED）")


def test_stale_gate_contract() -> None:
    """过期绿灯必须被检出、阻断，且有可走的补救路径（见 test_stale_gate.py）。

    2026-11-20 实测：逆袭书 C1/C3/C6 的章级裁决共 10 条引文在当前正文里
    已经找不到——正文被微改过（「又添**了**一句」→「又添一句」）。
    **漂移检测看不见它**：revision 定稿把正文和账本 sha 一起更新，账本与磁盘
    一致，看不出「被合法修订但没重裁」。于是裁决的节点状态仍写着 done，
    是一份**过期绿灯**。

    修法三件套：① 检测（逐条验证引文是否仍命中当前正文）② 阻断（N21 打包
    判不可投）③ 补救（`autopilot.py rejudge` 带外重判，**不动流水线游标**——
    流水线是线性的，回退会把已完稿的书重新拉回写作链）。
    """
    script = Path(__file__).resolve().parent / "test_stale_gate.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-3000:])
        print(proc.stderr[-1000:])
        raise AssertionError("过期绿灯夹具未通过（STALE_GATE_FAILED）")


def test_auto_trim_contract() -> None:
    """自动裁剪必须真的发生（见 test_auto_trim.py）。

    2026-11-20 实测：`_text_preflight` 的自动裁剪写于 2026-11-17，用来替掉
    「超长章由人逐段读、逐段删」的手工活（实测第 2 章手工裁了 689 字）。
    但它**从来没跑起来过**：`from inline_runner import trim_to_band,
    CHAPTER_BODY_MAX_CHARS` 抛 ImportError（常量在 pipeline_spec 里），
    且 `wordcount_core`（平台字数口径的唯一实现）只在技能目录里、从没挂到
    sys.path——而裸 `except Exception` 把这两个错吞成「没什么可裁」。
    于是「工具坏了」和「没发现问题」变成同一件事。

    修法：常量从 pipeline_spec 导入；`inline_runner.visible_chars()` 按 config
    里的 STORY_LONG_WRITE 挂技能 scripts 目录（只挂路径不抄实现）；裁剪失败
    不再吞，原因写进返回载荷的 `auto_trim_error`。
    """
    script = Path(__file__).resolve().parent / "test_auto_trim.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-3000:])
        print(proc.stderr[-1000:])
        raise AssertionError("自动裁剪夹具未通过（AUTO_TRIM_FAILED）")


def test_causal_contract() -> None:
    """因果收尾率与因果密度上限不得互相矛盾（见 test_causal_contract.py）。

    2026-11-20 实测：写作契约同一页上写了两条互斥的规则——「收尾带因果词的段落
    应 60%–85%」与「因果密度 ≤6.0/千字」。算术：2200 字 ÷ 段/千字 33 ⇒ 73 段；
    60% ⇒ 44 段要带因果词；而 6.0/千字 × 2.2 ⇒ 全章只允许 13 个计分因果词。
    44 > 13，不可达。

    实测后果：一个 writer 直接向编排者报告「这两条规则互斥」；另一个只好用
    **闸门不统计**的词凑收尾率——达标了，但那些词不在 CAUSAL 词表里，等于用漏洞
    而不是用规则。

    修法：以标定过的密度区间为准（1.0–6.0/千字，14 本签约书，不能改），收尾率
    改成算得通的 1/4–1/3，并把算术写进契约。夹具会**自己算一遍**：从契约读出
    段/千字带与密度上限，算出收尾率可达上限，断言契约写下的目标 ≤ 该上限——
    以后谁再写死一个数，这个夹具会当场挂掉。
    """
    script = Path(__file__).resolve().parent / "test_causal_contract.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-3000:])
        print(proc.stderr[-1000:])
        raise AssertionError("因果契约夹具未通过（CAUSAL_CONTRACT_FAILED）")


def test_tracking_retired() -> None:
    """退役声明必须逐字原文（见 test_tracking_retired.py）。

    2026-11-20 实测：writer 明明在 delta.retired_context_items 里声明了被删的
    context 条目，`done` 仍报「context items were dropped without being
    declared」——**报错信息与真实原因完全对不上**。真因是工作台的规整器为了把
    delta 压到 3072 字节，把退役声明**截到 30 字**，而工具做的是精确集合比对，
    截断一个字就匹配不上。更深一层：预检的体积口径**排除** retired，
    压缩器的口径**包含**它，同一份事务两处口径不同 → 压缩被触发 → 截断。

    修法：压缩器口径与预检对齐（都排除 retired）；退役声明不截断（工具提交时
    会按账本重新生成）；规整器顺手还原历史上被截断过的声明。
    """
    script = Path(__file__).resolve().parent / "test_tracking_retired.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-3000:])
        print(proc.stderr[-1000:])
        raise AssertionError("退役声明夹具未通过（TRACKING_RETIRED_FAILED）")


def test_editor_criteria_scan_contract() -> None:
    """编辑口径自动化扫描夹具必须全过（见 test_editor_criteria_scan.py）。"""
    script = Path(__file__).resolve().parent / "test_editor_criteria_scan.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("编辑口径自动化扫描夹具未通过（EDITOR_CRITERIA_SCAN_FAILED）")


def test_causal_rotator_contract() -> None:
    """因果词多样性自动轮换器夹具必须全过（见 test_causal_rotator.py）。"""
    script = Path(__file__).resolve().parent / "test_causal_rotator.py"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-1000:])
        raise AssertionError("因果词多样性轮换夹具未通过（CAUSAL_ROTATOR_FAILED）")


def test_partial_outline_digest_contract() -> None:
    """细纲断点续写注入：当 writer 中途死掉留下半成品细纲时，必须生成断点摘要。"""
    from pipeline_spec import _partial_outline_digest
    with tempfile.TemporaryDirectory() as tmp:
        book = Path(tmp)
        outline_dir = book / "大纲"
        outline_dir.mkdir(parents=True, exist_ok=True)
        st = {}
        assert _partial_outline_digest(st, book, 1) == ""

        draft_text = """# 第001章 细纲

- 核心事件：换名字引发冲突
- 字数目标：2200字
- 字数口径：visible_chars_v1
- 目标情绪：憋屈转震惊
### 内容概括
起因：名单被调换。
发展：陆明前去质问。
"""
        (outline_dir / "细纲_第001章.md").write_text(draft_text, encoding="utf-8")
        digest = _partial_outline_digest(st, book, 1)
        assert "【本章细纲已有草稿" in digest, digest
        assert "请基于已有结构补完" in digest, digest


def test_architecture_consistency_gate_contract() -> None:
    """架构因果门禁：细纲四拍节拍流执行率与提前释放禁令 fail-closed 阻断。"""
    from architecture_consistency_gate import verify_chapter_architecture
    with tempfile.TemporaryDirectory() as tmp:
        book = Path(tmp)
        outline_file = book / "细纲.md"
        text_file = book / "正文.md"
        outline_file.write_text("""# 第001章 细纲
- 核心事件：大伯逼主角放弃顶职名额按手印，陈砚当场掀桌撕毁协议断亲
- 本章禁止提前释放：禁止第一章让大伯全家提前下跪求饶
### 情节安排
- 第一拍（压抑危机）：前200字大伯陈富贵破门逼签协议。
- 第二拍（觉醒反击）：陈砚夺下协议撕成粉碎。
- 第三拍（抽翻立威）：陈砚反手抽翻堂弟立威。
- 第四拍（夜探厂办）：陈砚锁门夜奔厂办。
""", encoding="utf-8")
        good_text = """大伯陈富贵破门逼签协议，强扯重病妹妹按手印！
陈砚夺下协议撕成粉碎，冷眼看着贪婪的大伯。
堂弟还想逞凶，陈砚反手一巴掌抽翻堂弟！
陈砚锁好房门，收起底联，夜奔厂办！"""
        text_file.write_text(good_text, encoding="utf-8")
        res = verify_chapter_architecture(str(outline_file), str(text_file))
        assert res["ok"], res
        assert res["beat_execution"]["rate"] >= 0.75

        # 触犯禁止提前释放禁令
        bad_text = good_text + "\n大伯陈富贵吓得浑身发抖，当场跪地求饶！"
        text_file.write_text(bad_text, encoding="utf-8")
        res_bad = verify_chapter_architecture(str(outline_file), str(text_file))
        assert not res_bad["ok"], "Advance reveal violation must fail architecture gate!"
        assert len(res_bad["advance_reveal_violations"]) > 0


def test_persona_confidence_probe_contract() -> None:
    """人设置信度审查：主角主动性决策评分与被动降智屈服拦截。"""
    from persona_confidence_probe import probe_chapter_persona_confidence
    good_prose = """陈砚冷笑一声，反手夺过协议当场撕碎！
陈砚目光如刀，厉声喝道：“休想拿走我父亲的名额！”
随后陈砚果断锁门，直奔厂办。"""
    res = probe_chapter_persona_confidence(good_prose, protagonist_name="陈砚")
    assert res["ok"], res
    assert res["agency"]["proactive_actions"] >= 2

    # 被动降智屈服文本
    passive_prose = """陈砚委屈得直掉眼泪，面对大伯只能唯唯诺诺，心甘情愿让出顶职名额，毫无反抗之力任由摆布。"""
    res_passive = probe_chapter_persona_confidence(passive_prose, protagonist_name="陈砚")
    assert not res_passive["ok"], "Passive submission must fail agency check!"
    assert res_passive["agency"]["submission_hits"] >= 1


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="wb-py-contracts-") as tmp:
        root = Path(tmp)
        os.environ["WORKBENCH_CONFIG"] = str(make_config(root, "ready"))
        test_outline_wordcount_contract(root)
        test_next_exposes_node_attempt_budget(root)
        test_next_renders_selected_platform_paths(root)
        test_manifest_uses_book_platform(root)
        test_n13(root)
        test_n16_idempotent(root)
        test_n21_volume_shortfall_report(root)
        test_n21_blocked(root)
        test_next_lock(root)
        test_evidence_pattern_mismatch(root)
        test_done_token_via_env(root)
        test_block_and_resume_need_token(root)
        test_resume_preserves_scoped_budget_reset(root)
        test_resume_preserves_n14_recheck(root)
        test_tracking_heal_routes_volume_boundary_to_audit(root)
        test_next_cursor_runs_final_volume_audit_before_global(root)
        test_volume_budget_at_volume_terminal_uses_terminal_volume(root)
        test_volume_n20_routes_through_global_gate_and_package(root)
        test_block_budget_uses_chapter_scope()
        test_n14_done_rechecks_n13(root)
        test_n13_recheck_routes(root)
        test_rollback_best_snapshot(root)
        test_commit_suite(root)
        test_init_persists_platform_volume_and_one_line(root)
        test_render_prompt_injects_one_line(root)
        test_tracking_fill_dry_run_then_apply(root)
        # 下面两条 2026-10-28 定义后一直未注册进 main()，等于从未执行过。
        # 覆盖的是「卷预算降级要在 status 里说出来」与「终停要写 relay 原因」——
        # 后者正是 relay 那条被列为「从未实测」的路径。
        test_status_exposes_volume_degradation(root)
        test_terminal_block_writes_relay_reason(root)
        # T-2/T-2b：正文文件判定（备份排除 / 章号解析 / 歧义报错）的 Python 侧契约
        test_body_file_rules_t2b(root)
        # 2026-11-17：机械修复器夹具。这四类句式被 check-ai-patterns.js 登记为
        # mechanical+blocking（「不需判断、必须清零」），但 mechanical_fix 此前
        # 一次都没修过它们，全靠 N14 烧 LLM 轮次、还会漏。
        # 这里调子进程跑夹具，避免把断言逻辑复制两份。
        test_mechanical_fix_contract()
        # 2026-11-17：字数裁剪器夹具。storyctl 只报「该删多少字」却不实现压缩，
        # 每章超长都靠人手工裁（第2章 689 字、第4章 500 字）。裁剪器的危险失败模式
        # 是「删掉承载信息的段落来凑字数」，夹具用三处关键内容做守卫。
        test_trim_to_band_contract()
        # 2026-11-17：N11 产物防篡改守卫。writer 实测把脚本生成的
        # 追踪/上下文.md 覆写成了另一种结构，下一章会读到缺栏的坏文件。
        test_context_md_guard_contract()
        # 2026-11-17：到期伏笔显式标注。此前伏笔逾期没有任何机制拦住，
        # 实测 F005/F008 计划回收章都是 6、第 6 章封板后仍未回收。
        test_foreshadow_due_contract()
        # 2026-11-17：因果词多样性检测。总量指标测不出「同一词刷满全章」这种
        # AI 指纹（实测「果然」x44 时 causal_per_1000 只报 3.07、闸门全绿）。
        test_causal_diversity_contract()
        # 2026-11-17：断点续写 digest。writer 写到一半卡死时，新 subagent 要能
        # 接着写而不是从头重来——此前只能靠编排者手工续写。
        test_partial_body_digest_contract()
        # 2026-11-17：delta 自动压缩器。事务差 15 字节就被打回太浪费，
        # 改成超限时自动截断长度类字段。
        test_shrink_delta_contract()
        # 2026-11-17：账本完整性 + 封板后漂移。旧压缩器把「…」当原文写进账本；
        # sha256 此前只记录不比对，封板后偷改正文无人发现。
        test_ledger_integrity_contract()
        # 2026-11-17：动作节拍密度检测。判别力只差「拒稿书≥3」，故只作定位线索。
        # 夹具用真实拒稿样本当基准——口径再错一次立刻失败。
        test_action_beat_contract()
        # 2026-11-17：压缩器保底长度。result 曾被砍成 4 个字后原样写进账本。
        test_shrink_delta_floor_contract()
        # 2026-11-17：相对日互斥检查。全书日期网断裂是可计算的，
        # 但账本没有具体日期锚，此前完全查不出。
        test_relative_days_contract()
        # 2026-11-18：跨章逐字重复检测。「爽点只有一个模子」，
        # 且我自己也制造过新的逐字重复——人工比对抓不到。
        test_cross_chapter_duplicate_contract()
        # 2026-11-19：日链一致性校验。日期网断裂是四轮红队最重的一类缺陷，
        # 全部可计算——此前查不出是因为账本没有绝对日锚。
        test_daychain_contract()
        # 2026-11-19：冷启动。新书开箱即具备检测能力（init 生成日链/锚句骨架）。
        test_coldstart_contract()
        # 2026-11-19：无人值守。subagent 死亡 10+ 次是操作面最大问题，
        # P2 运行时没有这个失败模式。
        test_unattended_contract()
        # 2026-11-19：字数双口径。两个检查器不得对同一份稿子给出相反结论。
        test_wordcount_caliber_contract()
        # 2026-11-19：闸门提示词必须自带裁决结构（「格式同 N04」对执行者不可见）。
        test_gate_prompt_schema_contract()
        # 2026-11-19：裁决归一化层 + 自解释报错 + 失败反馈回灌。
        # judge 键名写错就整份判废，一轮好裁决白费；报错还不自解释。
        test_judgment_normalize_contract()
        # 2026-11-20：过期绿灯。章被改写后裁决依据失效，漂移检测看不见
        # （revision 把正文和账本 sha 一起更新）。检测/阻断/补救三件套。
        test_stale_gate_contract()
        # 2026-11-20：自动裁剪此前是死代码（常量 import 错 + wordcount_core
        # 不在 sys.path + 裸 except 吞掉）。超长章只会判 over 打回，由人裁。
        test_auto_trim_contract()
        # 2026-11-20：因果收尾率 60%–85% 与因果密度 ≤6.0/千字 在数学上互斥，
        # writer 实测撞上并报告。以标定区间为准，收尾率改成 1/4–1/3 并写清算术。
        test_causal_contract()
        # 2026-11-20：退役声明被压缩器截到 30 字 → 工具的精确比对失败，
        # 报错信息与真实原因对不上。压缩器口径与预检对齐 + 不截断 + 自动还原。
        test_tracking_retired()
        # 2026-11-22：编辑口径自动化扫描（开篇冲突/单章场景切换）
        test_editor_criteria_scan_contract()
        # 2026-11-22：因果词多样性保义自动轮换
        test_causal_rotator_contract()
        # 2026-11-22：细纲断点续写注入（防止 subagent 死亡后从头重写超时）
        test_partial_outline_digest_contract()
        # 2026-11-22：架构因果与细纲节拍/禁止提前释放门禁
        test_architecture_consistency_gate_contract()
        # 2026-11-22：人设核心动机与主动性置信度审查
        test_persona_confidence_probe_contract()
    print("PY_CONTRACTS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
