#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_judgment_normalize.py — 裁决归一化层 + 自解释报错 + 失败反馈回灌

**为什么要有**（2026-11-19 冷启动书 N06 实测）：

judge 写了一份内容完全正确的裁决（verdict=pass、5 个维度全查了、引文是从
`设定/文风.md` 逐字抄的），但它把裁决项放在 `checks` 而不是 `items` 里，
键名用的是 `name` / `result` / `detail` 而不是 `criterion` / `verdict` / `quote`。
`done` 于是以 `quote_check_failed: items 为空` 把它**整份判废**。

被废掉的是「语义有效的工作」，丢掉的只是「接口约定」。更糟的是报错只说
「items 为空」，执行方看不出正确的结构该长什么样——对话路径又没有 P2 那样的
自愈回路，于是一轮好裁决白白空转。

修法（三层）：
  ① 提示词层：verify 节点自带完整结构（见 test_gate_prompt_schema.py）
  ② **归一化层**（本夹具）：键名/容器别名 → canonical；从散文回捞逐字引文；
     引文命中但 source 写错 → 改指。**只搬运不造事实**：捞不到就如实报错。
  ③ **报错层**：quote_check_failed 的载荷自带结构、字段规则、允许引用的文件、
     可抄示例；失败落盘 state.last_failure，下次 `autopilot.py next` 把它作为
     「上一轮失败反馈」灌回 subagent_prompt（与 P2 无人值守路径同一抬头）。

**实质要求一点没放松**（本夹具下半段逐条钉住）：
  - fail 项必须给命中 source 的逐字引文；
  - 整份裁决至少要有一条命中引文（否则无法证明 judge 读过源文件）；
  - 引文不命中 / source 不在允许范围 / verdict=block 却无 fail 项 → 一律拒。

跑法：
  python -X utf8 server/tests/test_judgment_normalize.py
  → 全过打印 JUDGMENT_NORMALIZE_OK 并 exit 0
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from commands import _validate_judgment                            # noqa: E402
from judgment_norm import (normalize_judgment, judgment_schema_hint,  # noqa: E402
                           _extract_quote_from_prose, _cjk_only)

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'} [{name}]" + ("" if ok else f" —— {detail}"))
    if not ok:
        FAILS.append(name)


# ─── 夹具：造一本最小可用的书 ────────────────────────────────────────────────

STYLE = """# 文风

## 硬规则
- 每个技术段落至少 1 个可核数字。
- 对白占比 0.05–0.30。
- 不使用连续三段以上的纯说明句。

## 例句
陈铁生蹲在床脚边，手指顺着皮带内侧摸过去，摸到一道发亮的痕。
"""

ROLE = """# 角色

## 陈铁生
- 动机：把这台趴窝的老车床跑通，然后证明合同工也能在工艺卡上签字。
- 困境：车间主任不肯把工艺卡交给他。
"""


def make_book(root: Path) -> Path:
    book = root / "book"
    (book / "设定").mkdir(parents=True, exist_ok=True)
    (book / "设定" / "文风.md").write_text(STYLE, encoding="utf-8")
    (book / "设定" / "角色.md").write_text(ROLE, encoding="utf-8")
    (book / "设定" / "题材定位.md").write_text(
        "# 题材定位\n\n年代：1990 年。\n题材：职场逆袭。\n", encoding="utf-8")
    (book / "设定" / "世界观.md").write_text(
        "# 世界观\n\n厂区、车间、宿舍三条线，无超自然元素。\n", encoding="utf-8")
    (book / "_pipeline").mkdir(parents=True, exist_ok=True)
    return book


def main() -> int:
    print("== 裁决归一化层 ==")
    root = Path(tempfile.mkdtemp(prefix="judgment_norm_"))
    try:
        book = make_book(root)

        # ── ① 别名容器 + 别名键 → canonical ────────────────────────────────
        raw = {
            "verdict": "pass",
            "ruling": "设定无硬伤。",
            "checks": [
                {"id": "C1", "name": "角色有明确动机", "result": "pass",
                 "level": "A", "source": "设定/角色.md",
                 "detail": "动机明确：把这台趴窝的老车床跑通，然后证明合同工也能在工艺卡上签字。"},
                {"id": "C2", "name": "文风内部数值自洽", "result": "pass",
                 "source": "设定/文风.md",
                 "detail": "自洽：每个技术段落至少 1 个可核数字这条规则写在文风里。"},
            ],
        }
        norm, repairs = normalize_judgment(raw, book, "N06")
        check("别名容器 checks → items",
              isinstance(norm.get("items"), list) and len(norm["items"]) == 2,
              f"items={norm.get('items')}")
        check("别名键 name/result → criterion/verdict",
              norm["items"][0].get("criterion") == "角色有明确动机"
              and norm["items"][0].get("verdict") == "pass",
              str(norm["items"][0]))
        check("别名容器被记为修复", any("容器键" in r for r in repairs), str(repairs))
        bad = _validate_judgment(book, "N06", norm)
        check("归一后通过校验（内容对、键名不同不该判废）", not bad, str(bad))

        # ── ② 散文回捞逐字引文 ────────────────────────────────────────────
        raw2 = {
            "verdict": "pass", "summary": "ok",
            "items": [
                {"criterion": "文风内部数值自洽", "verdict": "pass",
                 "source": "设定/文风.md",
                 "detail": "文风里写明每个技术段落至少 1 个可核数字，与正文一致。"},
            ],
        }
        norm2, repairs2 = normalize_judgment(raw2, book, "N06")
        quote = norm2["items"][0].get("quote") or ""
        check("从散文回捞出逐字引文", len(_cjk_only(quote)) >= 12, f"quote={quote!r}")
        check("回捞的引文确实逐字命中源文件",
              "每个技术段落至少 1 个可核数字" in quote or "每个技术段落至少1个可核数字" in quote,
              f"quote={quote!r}")
        check("回捞被记为修复", any("回捞" in r for r in repairs2), str(repairs2))
        check("回捞后通过校验", not _validate_judgment(book, "N06", norm2),
              str(_validate_judgment(book, "N06", norm2)))

        # ── ③ 引文质量门槛：路径碎片不算引文 ─────────────────────────────
        junk = book / "设定" / "题材定位.md"
        junk.write_text("# 题材定位\n\n见 `设定/势力.md`，另有 `设定/文风.md`。\n",
                        encoding="utf-8")
        hit = _extract_quote_from_prose(
            "详见 `设定/势力.md`），另有 `设定/文风.md` 这两件。", junk)
        check("路径碎片不被回捞为引文（<12 汉字）",
              hit == "" or len(_cjk_only(hit)) >= 12, f"hit={hit!r}")

        # ── ④ 实质要求一条都不能松 ────────────────────────────────────────
        no_quote_fail = {"verdict": "block", "summary": "有问题", "items": [
            {"criterion": "世界观过载", "verdict": "fail", "level": "B",
             "source": "设定/世界观.md", "fix": "砍掉三处支线"}]}
        bad4 = _validate_judgment(book, "N06", no_quote_fail)
        check("fail 项缺引文 → 拒",
              any("fail 项必须给出" in b.get("reason", "") for b in bad4), str(bad4))

        grounded_missing = {"verdict": "pass", "summary": "都挺好", "items": [
            {"criterion": "文件齐全", "verdict": "pass", "source": "设定/题材定位.md"},
            {"criterion": "角色动机", "verdict": "pass", "source": "设定/角色.md"}]}
        bad5 = _validate_judgment(book, "N06", grounded_missing)
        check("整份裁决零引文 → 拒（防空洞通过）",
              any("没有任何一条命中原文" in b.get("reason", "") for b in bad5), str(bad5))

        bad_quote = {"verdict": "pass", "summary": "x", "items": [
            {"criterion": "文风", "verdict": "pass", "source": "设定/文风.md",
             "quote": "这是一段根本不存在于源文件里的凭空引文内容"}]}
        bad6 = _validate_judgment(book, "N06", bad_quote)
        check("引文不命中源文件 → 拒（不造事实）",
              any("quote 未在 source 中命中" in b.get("reason", "") for b in bad6), str(bad6))

        bad_scope = {"verdict": "pass", "summary": "x", "items": [
            {"criterion": "越界取证", "verdict": "pass", "source": "大纲/选题报告.md",
             "quote": "陈铁生蹲在床脚边，手指顺着皮带内侧摸过去，摸到一道发亮的痕。"}]}
        bad7 = _validate_judgment(book, "N06", bad_scope)
        check("source 不在允许范围 → 拒",
              any("不在允许范围" in b.get("reason", "") for b in bad7), str(bad7))

        bad_block = {"verdict": "block", "summary": "x", "items": [
            {"criterion": "c", "verdict": "pass", "source": "设定/文风.md",
             "quote": "陈铁生蹲在床脚边，手指顺着皮带内侧摸过去，摸到一道发亮的痕。"}]}
        bad8 = _validate_judgment(book, "N06", bad_block)
        check("verdict=block 却无 fail 项 → 拒",
              any("没有 fail 明细" in b.get("reason", "") for b in bad8), str(bad8))

        # ── ⑤ canonical 文件不该被改写（repairs 为空 → 不落盘）───────────
        canonical = {"verdict": "pass", "summary": "ok", "items": [
            {"criterion": "文风", "quote": "陈铁生蹲在床脚边，手指顺着皮带内侧摸过去，摸到一道发亮的痕。",
             "source": "设定/文风.md", "verdict": "pass", "level": "A"}]}
        _, repairs5 = normalize_judgment(canonical, book, "N06")
        check("canonical 文件无修复记录（不 gratuitously 改写历史裁决）",
              repairs5 == [], str(repairs5))

        # ── ⑥ 自解释报错载荷 ──────────────────────────────────────────────
        hint = judgment_schema_hint("N06", book)
        payload = {"error": "quote_check_failed", "detail": hint["feedback"],
                   "items": bad5, "required_schema": hint["schema"],
                   "allowed_sources": hint["allowed_sources"],
                   "example": hint["example"]}
        text = json.dumps(payload, ensure_ascii=False)
        check("报错载荷自带 schema", '"items"' in text and "criterion" in text)
        ex_quote = hint["example"]["items"][0].get("quote") or ""
        ex_source = hint["example"]["items"][0].get("source") or ""
        check("报错载荷自带可抄示例", bool(ex_quote) and ex_quote in text, ex_quote)
        check("示例自洽：引文真命中它标的 source",
              bool(ex_source) and ex_quote in (book / ex_source).read_text(encoding="utf-8"),
              f"{ex_quote!r} @ {ex_source}")
        check("报错载荷自带允许引用清单", "设定/文风.md" in text)
        check("报错载荷自带字段规则（fail 必填引文）",
              "fail 项必填" in hint["feedback"] or "fail 项必须给出" in hint["feedback"],
              hint["feedback"][:200])
        check("报错载荷自带 items 不可空", "不可为空" in hint["feedback"])

        # ── ⑦ 失败落盘 + next 回灌「上一轮失败反馈」 ───────────────────────
        state_dir = book / "_pipeline"
        state = {"schema_version": 2, "book": "测试", "genre": "niandai",
                 "cursor": "N06", "current_chapter": 0, "chapters_committed": 0,
                 "words_total": 0, "target_words": 20000, "max_chapters": 12,
                 "nodes": {}, "blocked_reason": None, "pending_questions": []}
        (state_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False),
                                               encoding="utf-8")
        (state_dir / "judgments_N06.json").write_text(
            json.dumps({"verdict": "pass", "summary": "设定五件齐全但一条引文都没有，"
                                                       "无法证明评审真的读过源文件，"
                                                       "因此这份裁决必须被闸门拒收。",
                        "items": [
                            {"criterion": "文件齐全", "verdict": "pass",
                             "source": "设定/题材定位.md"},
                            {"criterion": "角色动机", "verdict": "pass",
                             "source": "设定/角色.md"}]},
                       ensure_ascii=False), encoding="utf-8")
        token = "tok-norm-test"
        (state_dir / ".step_token").write_text(
            json.dumps({"token": token, "node": "N06",
                        "issued_at": "2026-11-19T10:00:00+0800"}, ensure_ascii=False),
            encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "autopilot.py"), "done", "--book", str(book),
             "--node", "N06", "--token", token,
             "--evidence", "_pipeline/judgments_N06.json"],
            capture_output=True, text=True, encoding="utf-8", check=False)
        check("零引文的裁决被 done 拒收", proc.returncode == 2,
              f"rc={proc.returncode} out={proc.stdout[:200]}")
        payload_out = json.loads(proc.stdout) if proc.stdout.strip() else {}
        check("拒收载荷自带 detail（自解释）",
              "fail 项必填" in str(payload_out.get("detail", ""))
              and '"items"' in str(payload_out.get("detail", "")),
              str(payload_out.get("detail", ""))[:120])
        check("拒收载荷自带 allowed_sources",
              isinstance(payload_out.get("allowed_sources"), list)
              and "设定/文风.md" in payload_out["allowed_sources"],
              str(payload_out.get("allowed_sources")))

        state_after = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
        failure = state_after.get("last_failure") or {}
        check("失败落盘 state.last_failure",
              failure.get("node") == "N06" and failure.get("error") == "quote_check_failed",
              str(failure)[:160])

        nxt = subprocess.run(
            [sys.executable, str(SCRIPTS / "autopilot.py"), "next", "--book", str(book)],
            capture_output=True, text=True, encoding="utf-8", check=False)
        next_payload = json.loads(nxt.stdout) if nxt.stdout.strip() else {}
        prompt = next_payload.get("subagent_prompt", "")
        check("next 的 subagent_prompt 回灌「上一轮失败反馈」",
              "【上一轮失败反馈】" in prompt and "fail 项必填" in prompt,
              prompt[:160])
        check("回灌抬头与 P2 无人值守路径一致",
              prompt.startswith("【上一轮失败反馈】"), prompt[:40])

        # ── ⑧ 归一化落盘：修正后的文件写回磁盘 ────────────────────────────
        shutil.rmtree(state_dir / "judgments_N06.json", ignore_errors=True)
        (state_dir / "judgments_N06.json").write_text(json.dumps({
            "verdict": "pass",
            "summary": "设定五件齐全，文风与 niandai 基准兼容，角色动机明确，无硬伤。",
            "checks": [{"name": "文风", "result": "pass", "source": "设定/文风.md",
                        "detail": "文风写明每个技术段落至少 1 个可核数字这条规则。"}],
        }, ensure_ascii=False), encoding="utf-8")
        token2 = "tok-norm-test-2"
        (state_dir / ".step_token").write_text(
            json.dumps({"token": token2, "node": "N06",
                        "issued_at": "2026-11-19T11:00:00+0800"}, ensure_ascii=False),
            encoding="utf-8")
        proc2 = subprocess.run(
            [sys.executable, str(SCRIPTS / "autopilot.py"), "done", "--book", str(book),
             "--node", "N06", "--token", token2,
             "--evidence", "_pipeline/judgments_N06.json"],
            capture_output=True, text=True, encoding="utf-8", check=False)
        check("键名不对但内容对的裁决被放行", proc2.returncode == 0,
              f"rc={proc2.returncode} out={proc2.stdout[:300]} err={proc2.stderr[:200]}")
        written = json.loads((state_dir / "judgments_N06.json").read_text(encoding="utf-8"))
        check("归一化结果写回磁盘（下游读到的形状一致）",
              isinstance(written.get("items"), list) and len(written["items"]) == 1
              and written["items"][0].get("criterion") == "文风"
              and len(_cjk_only(written["items"][0].get("quote") or "")) >= 12,
              json.dumps(written, ensure_ascii=False)[:240])
        state_final = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
        check("成功后消费掉 last_failure",
              "last_failure" not in state_final, str(state_final.get("last_failure"))[:120])
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print(f"\n{'全部通过' if not FAILS else '失败: ' + ', '.join(FAILS)}")
    if FAILS:
        print("JUDGMENT_NORMALIZE_FAILED")
        return 1
    print("JUDGMENT_NORMALIZE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
