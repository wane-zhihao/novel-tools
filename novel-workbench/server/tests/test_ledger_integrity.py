#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ledger_integrity.py — 账本完整性与封板后漂移的夹具

两个缺陷，同源：

1. **截断伪装成原文**：封板前的旧压缩器用**固定长度 +「…」后缀**截断
   objective_fact / summary 等字段，「…」被 storyctl 当成**原文**写进账本，
   此后每次读账本都以为那是原文的一部分。实测 E002–E005 都被截成半句。

2. **封板后正文被改、账本不跟随**：sha256 此前只记录不比对。实测第 8 章
   N16 封板后正文又被改（2383 字），账本仍记 2348 字、sha 也不同——
   伏笔回收率、文风漂移、字数统计全部基于错位的基线。

跑法：
  python -X utf8 server/tests/test_ledger_integrity.py
  → 全过打印 LEDGER_INTEGRITY_OK 并 exit 0
"""
import sys
import json
import tempfile
import shutil
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from commands import _ledger_integrity, _body_post_commit_drift     # noqa: E402


def _book(tmp: Path, state: dict) -> Path:
    book = tmp / "book"
    (book / "追踪").mkdir(parents=True)
    (book / "追踪" / "_tracking-state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return book


def main() -> int:
    print("== 账本完整性 ==")
    fails = []

    # ① 以「…」结尾 = 被截断
    t1 = Path(tempfile.mkdtemp())
    try:
        st = {"timeline": {"E002": {"objective_fact": "陆明取回旧点检卡，三号…"}},
              "context": {"next_chapter_commitments": ["第9章陆明把…"]}}
        book = _book(t1, st)
        items = _ledger_integrity(book)
        ok = len(items) == 2
        print(f"  {'PASS' if ok else 'FAIL'} [检出 2 处以「…」结尾的截断]")
        if not ok:
            fails.append("detect")
            print(f"        实得 {len(items)}: {items}")
    finally:
        shutil.rmtree(t1, ignore_errors=True)

    # ② 正常账本 → 无报告
    t2 = Path(tempfile.mkdtemp())
    try:
        st = {"timeline": {"E002": {"objective_fact": "陆明取回旧点检卡，三号床子封存。"}},
              "context": {"next_chapter_commitments": ["第9章陆明报复产鉴定"]}}
        book = _book(t2, st)
        items = _ledger_integrity(book)
        ok = len(items) == 0
        print(f"  {'PASS' if ok else 'FAIL'} [正常账本 → 无报告]")
        if not ok:
            fails.append("clean")
    finally:
        shutil.rmtree(t2, ignore_errors=True)

    # ③ 已封板正文被改 → 报漂移
    # 比对口径：**账本**（追踪/_tracking-state.json）的 body_sha256。
    # 首版比的是 state 节点 → revision 路径（storyctl commit）更新账本但没更新
    # state 节点，工具**误报**「正文被改」，而磁盘与账本其实完全一致。夹具立刻抓到。
    t3 = Path(tempfile.mkdtemp())
    try:
        book = t3 / "book"
        (book / "正文").mkdir(parents=True)
        (book / "追踪").mkdir()
        body = book / "正文" / "第008章_第三联.md"
        body.write_text("原来的一千字。", encoding="utf-8")
        import hashlib
        good = hashlib.sha256(body.read_bytes()).hexdigest()
        ledger = {"wordcount_records": {"8": {"actual": 6, "body_sha256": good}}}
        (book / "追踪" / "_tracking-state.json").write_text(
            json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
        st = {"nodes": {"C8.N12": {"status": "done",
                                   "evidence": ["正文/第008章_第三联.md"],
                                   "sha256": ["旧值"]}}}
        ok_drift = (len(_body_post_commit_drift(book, st)) == 0)
        print(f"  {'PASS' if ok_drift else 'FAIL'} [正文未被改 → 无漂移（state 节点旧值不误报）]")
        if not ok_drift:
            fails.append("no_drift")
        # 改了正文 → 必须报
        body.write_text("被偷改过的一千字。", encoding="utf-8")
        items = _body_post_commit_drift(book, st)
        ok2 = (len(items) == 1 and "被改动" in items[0])
        print(f"  {'PASS' if ok2 else 'FAIL'} [正文被改 → 报出漂移]")
        if not ok2:
            fails.append("drift")
            print(f"        实得 {items}")
    finally:
        shutil.rmtree(t3, ignore_errors=True)

    # ④ 旧记录没有 body_sha256 → 跳过（不误报）
    t4 = Path(tempfile.mkdtemp())
    try:
        book = t4 / "book"
        (book / "正文").mkdir(parents=True)
        (book / "追踪").mkdir()
        body = book / "正文" / "第009章_测试.md"
        body.write_text("新写的正文。", encoding="utf-8")
        ledger = {"wordcount_records": {"9": {"actual": 6}}}   # 无 body_sha256
        (book / "追踪" / "_tracking-state.json").write_text(
            json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
        st = {"nodes": {"C9.N12": {"status": "running",
                                   "evidence": ["正文/第009章_测试.md"],
                                   "sha256": ["deadbeef"]}}}
        items = _body_post_commit_drift(book, st)
        ok = (len(items) == 0)
        print(f"  {'PASS' if ok else 'FAIL'} [无 body_sha256 / 未封板 → 不报漂移]")
        if not ok:
            fails.append("running")
    finally:
        shutil.rmtree(t4, ignore_errors=True)

    total = 5
    passed = total - len(fails)
    print(f"\n{passed}/{total} 通过")
    if fails:
        print("LEDGER_INTEGRITY_FAILED")
        return 1
    print("LEDGER_INTEGRITY_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
