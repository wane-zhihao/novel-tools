#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_context_md_guard.py — N11 产物防篡改守卫的夹具

**为什么要有**：`追踪/写作上下文.md` 由 C{n}.N11 **脚本**生成（S1–S7 七栏），
writer 在 N12 只读它。实测第 6 章的 writer 交稿时把该文件**整个覆写**成了
另一种结构（「写作连续性上下文 / 当前位置 / 活跃伏笔 / 近三章速记…」），
全仓库搜不到这个标题——是 subagent 自己编的七栏。

后果很重：下一章的 writer 会读到缺 S2 上一章锚点、缺 S5 文风锚、缺 S7 事务说明
的坏文件，而这三栏正是「无缝衔接」与「事务怎么填」的唯一来源。
N11 是脚本节点，产物必须只读；writer 只该写 正文/ 与 .story/work/。

跑法：
  python -X utf8 server/tests/test_context_md_guard.py
  → 全过打印 CONTEXT_GUARD_OK 并 exit 0
"""
import sys
import tempfile
import shutil
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from commands import _context_md_intact                        # noqa: E402

GOOD = """# 第 007 章写作上下文

## S1 本章场景卡（细纲原文）
场景
## S2 上一章结尾原文锚点
锚点
## S3 主线
主线
## S4 角色当前状态
角色
## S5 文风锚
文风
## S6 禁写与连续性约束
- 约束
## S7 追踪事务填写说明
说明
"""

# 实测里 writer 覆写出来的那种结构（七栏但完全不对）
WRITER_OVERWRITE = """# 写作连续性上下文 — 《某书》

## 当前位置
- 当前章：第6章
## 长期约束
- 某约束
## 核心角色状态
- 某人｜身份
## 活跃伏笔
- F002｜某伏笔
## 近三章速记
- 第4章｜某事
## 下一章承诺
- 某事
## 连贯性风险
- 某风险
"""


def _mk(content: str) -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "追踪").mkdir()
    (d / "追踪" / "写作上下文.md").write_text(content, encoding="utf-8")
    return d


def _case(content, expect_ok, label) -> bool:
    d = _mk(content)
    try:
        ok, msg = _context_md_intact(d, 7)
        good = (ok == expect_ok)
        print(f"  {'PASS' if good else 'FAIL'} [{label}] 期望{'完好' if expect_ok else '损坏'}，实判{'完好' if ok else '损坏'}")
        if not good:
            print(f"        {msg[:160]}")
        return good
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _case_missing() -> bool:
    """文件不存在时**放行**：那是 N11 该管的事，不是本守卫的职责。

    首版把「不存在」也判失败，结果把没有上下文的夹具书与 N14 定点改写
    一并挡住——既有回归 test_n14_done_rechecks_n13 直接挂。已改为放行。
    """
    d = Path(tempfile.mkdtemp())
    try:
        ok, _ = _context_md_intact(d, 7)
        print(f"  {'PASS' if ok else 'FAIL'} [文件不存在→放行] 期望放行，实判{'放行' if ok else '拦下'}")
        return ok
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    print("== 上下文防篡改守卫 ==")
    results = [
        _case(GOOD, True, "N11 正常产物"),
        _case(WRITER_OVERWRITE, False, "writer 覆写的七栏"),
        _case(GOOD.replace("## S2 上一章结尾原文锚点\n锚点\n", ""), False, "缺 S2 一栏"),
        _case(GOOD.replace("## S5 文风锚\n文风\n", ""), False, "缺 S5 一栏"),
        _case("", False, "空文件"),
        _case_missing(),
    ]
    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} 通过")
    if passed != total:
        print("CONTEXT_GUARD_FAILED")
        return 1
    print("CONTEXT_GUARD_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
