#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_partial_body_digest.py — 断点续写 digest 的夹具

**为什么要有**：实测第 8 章的 writer 写到 1511 字卡死在高潮对峙戏，
磁盘上留下半截正文就再也动不了。新的 subagent 若从零重写会丢掉已有内容，
编排者只能手工续写——这是第 4 类人工介入。

修法：`pipeline_spec._partial_body_digest()` 把已有正文的尾部注入 N12 提示词，
并明确「接着往下写，不要从头重写」。

跑法：
  python -X utf8 server/tests/test_partial_body_digest.py
  → 全过打印 PARTIAL_BODY_DIGEST_OK 并 exit 0
"""
import sys
import tempfile
import shutil
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pipeline_spec as ps                                       # noqa: E402


def _book_with_body(tmp: Path, paras: list[str]) -> Path:
    book = tmp / "book"
    (book / "正文").mkdir(parents=True)
    (book / "正文" / "第008章_第三联.md").write_text(
        "\n\n".join(paras), encoding="utf-8")
    return book


def main() -> int:
    print("== 断点续写 digest ==")
    fails = []

    st = {"genre": "niandai", "platform": "qimao", "plan": None, "max_chapters": 12}

    # ① 有半成品 → 必须注入续写指令
    t1 = Path(tempfile.mkdtemp())
    try:
        paras = [f"第{i}段内容。" for i in range(30)]
        book = _book_with_body(t1, paras)
        d = ps._partial_body_digest(st, book, 8)
        ok = ("接着往下写" in d and "不要从头重写" in d
              and "第{i}" not in d and str(30) not in d)  # 需带实际字数
        ok = ok and "已有正文" in d and "续写要求" in d
        print(f"  {'PASS' if ok else 'FAIL'} [有半成品 → 注入续写指令]")
        if not ok:
            print(f"        {d[:200]}")
            fails.append("inject")
    finally:
        shutil.rmtree(t1, ignore_errors=True)

    # ② 无半成品 → 返回空（不污染提示词）
    t2 = Path(tempfile.mkdtemp())
    try:
        book = t2 / "book"
        (book / "正文").mkdir(parents=True)
        d = ps._partial_body_digest(st, book, 8)
        ok = (d == "")
        print(f"  {'PASS' if ok else 'FAIL'} [无半成品 → 空]")
        if not ok:
            fails.append("empty")
    finally:
        shutil.rmtree(t2, ignore_errors=True)

    # ③ 空文件 → 返回空
    t3 = Path(tempfile.mkdtemp())
    try:
        book = _book_with_body(t3, [])
        d = ps._partial_body_digest(st, book, 8)
        ok = (d == "")
        print(f"  {'PASS' if ok else 'FAIL'} [空文件 → 空]")
        if not ok:
            fails.append("blank")
    finally:
        shutil.rmtree(t3, ignore_errors=True)

    # ④ digest 必须包含正文尾部内容（接手者要能看到最后一句）
    t4 = Path(tempfile.mkdtemp())
    try:
        paras = [f"第{i}段内容。" for i in range(20)] + ["最后一句是这里。"]
        book = _book_with_body(t4, paras)
        d = ps._partial_body_digest(st, book, 8)
        ok = "最后一句是这里。" in d
        print(f"  {'PASS' if ok else 'FAIL'} [digest 含正文尾部]")
        if not ok:
            print(f"        {d[-160:]}")
            fails.append("tail")
    finally:
        shutil.rmtree(t4, ignore_errors=True)

    total = 4
    passed = total - len(fails)
    print(f"\n{passed}/{total} 通过")
    if fails:
        print("PARTIAL_BODY_DIGEST_FAILED")
        return 1
    print("PARTIAL_BODY_DIGEST_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
