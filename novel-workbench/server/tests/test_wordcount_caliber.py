#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_wordcount_caliber.py — 字数双口径不得产生"假 BLOCK"

**为什么要有**（2026-11-19）：

同一套工具里的两个检查器，对**同一份稿子**给出了相反结论：

```
体检报告.md        → 20903 字，可投
qimao_check.py     → BLOCK Q01-wordcount（纯汉字 17822 < 20000）
```

用户无法判断信谁。根因：`Q01` 旧实现「判定仍按纯汉字（保持历史可比）」，
而平台 `api_words` **实测与可见字符同口径、与纯汉字 0 章相等**
（`platforms/_core/data/wordcount-caliber-finding-2026-11-13.md`：24 章中
api_words 与可见字符精确相等 20 章，与纯汉字相等 0 章）。

拿汉字口径去 BLOCK 一份平台会收的稿子，是**假 BLOCK**——它会挡住合法投稿。

修法（分级）：
  · 可见字符 < need            → BLOCK（两种口径都不达标）
  · 可见字符 ≥ need > 纯汉字   → CONFIRM（平台口径达标，人工在后台核对显示字数）
  · 都达标                     → INFO

跑法：
  python -X utf8 server/tests/test_wordcount_caliber.py
  → 全过打印 WORDCOUNT_CALIBER_OK 并 exit 0
"""
import importlib.util
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

TOOL = Path(r"F:\小说\platforms\_core\tools\qimao_check.py")


def _load():
    spec = importlib.util.spec_from_file_location("qimao_check_probe", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(chapters: dict) -> str:
    tmp = Path(tempfile.mkdtemp())
    try:
        for name, body in chapters.items():
            (tmp / name).write_text(body, encoding="utf-8")
        r = subprocess.run(
            [sys.executable, "-X", "utf8", str(TOOL),
             "--manuscript", str(tmp), "--title", "口径测试"],
            capture_output=True, encoding="utf-8")
        return (r.stdout or "") + (r.stderr or "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("== 字数双口径：不得产生假 BLOCK ==")
    fails = []
    if not TOOL.is_file():
        print(f"  FAIL [找不到 {TOOL}]")
        print("WORDCOUNT_CALIBER_FAILED")
        return 1

    # ① 纯汉字少、可见字符够 → 必须是 CONFIRM，不能是 BLOCK
    #    构造：汉字 15400（< 20000）+ 标点 7200（可见字符）= 22600 可见字符（≥ 20000）
    han = "他把工件卡上盘刀具抵上去试车一刀屑花卷成一条" * 700      # 22×700 = 15400 汉字
    padded = "\n\n".join(han[i:i + 900] for i in range(0, len(han), 900))
    punct = "，。！？；：" * 1200                                   # 7200 个标点，全是可见字符
    out1 = _run({"第001章_甲.md": padded + "\n\n" + punct})
    confirm_ok = ("Q01-wordcount" in out1 and "CONFIRM" in out1
                  and "BLOCK Q01-wordcount" not in out1)
    print(f"  {'PASS' if confirm_ok else 'FAIL'} "
          f"[汉字不足但平台口径够 → CONFIRM（不 BLOCK）]")
    if not confirm_ok:
        fails.append("confirm")
        for line in out1.splitlines():
            if "Q01" in line:
                print("        ", line.strip()[:170])

    # ② 两种口径都不够 → 必须 BLOCK
    tiny = _run({"第001章_乙.md": "他走了。\n" * 20})
    block_ok = "BLOCK Q01-wordcount" in tiny
    print(f"  {'PASS' if block_ok else 'FAIL'} [两种口径都不足 → BLOCK]")
    if not block_ok:
        fails.append("block")
        for line in tiny.splitlines():
            if "Q01" in line:
                print("        ", line.strip()[:150])

    # ③ 工具自检必须仍然通过
    r = subprocess.run([sys.executable, "-X", "utf8", str(TOOL), "--selftest"],
                       capture_output=True, encoding="utf-8")
    sel_ok = r.returncode == 0 and "SELFTEST PASS" in ((r.stdout or "") + (r.stderr or ""))
    print(f"  {'PASS' if sel_ok else 'FAIL'} [qimao_check --selftest 仍通过]")
    if not sel_ok:
        fails.append("selftest")

    # ④ 真实书：逆袭书不得有 BLOCK
    real = Path(r"F:\小说\works\逆袭从掌控系统开始\正文")
    if real.is_dir():
        out4 = _run.__wrapped__ if hasattr(_run, "__wrapped__") else None
        r4 = subprocess.run(
            [sys.executable, "-X", "utf8", str(TOOL),
             "--manuscript", str(real), "--title", "逆袭从掌控系统开始"],
            capture_output=True, encoding="utf-8")
        s4 = (r4.stdout or "") + (r4.stderr or "")
        real_ok = "BLOCK 0 项" in s4
        print(f"  {'PASS' if real_ok else 'FAIL'} [逆袭书 BLOCK 0 项]")
        if not real_ok:
            fails.append("real")
            for line in s4.splitlines():
                if "BLOCK" in line:
                    print("        ", line.strip()[:150])

    # ⑤ 合并稿与逐章口径必须**逐字一致**（2026-11-20 新增）。
    #    此前 qimao_check 只认章节目录，**检不了真正的投稿物**
    #    （编辑实际收到的是 `投稿包/全文.md`）。补上合并稿入口后，
    #    两条路径给出的字数/章数必须完全相同——否则「用哪份稿子体检」
    #    会改变结论，检查器就成了薛定谔的检查器。
    checks_run = 4
    book = Path(r"F:\小说\works\逆袭从掌控系统开始")
    merged_file = book / "投稿包" / "全文.md"
    if book.is_dir() and merged_file.is_file():
        import re as _re

        def _numbers(out: str) -> list:
            m = _re.search(r"（(\d+) 章 / (\d+) 汉字", out)
            return [m.group(1), m.group(2)] if m else []

        r_a = subprocess.run(
            [sys.executable, "-X", "utf8", str(TOOL),
             "--manuscript", str(book / "正文"), "--title", "口径测试"],
            capture_output=True, encoding="utf-8")
        r_b = subprocess.run(
            [sys.executable, "-X", "utf8", str(TOOL),
             "--manuscript", str(merged_file), "--title", "口径测试"],
            capture_output=True, encoding="utf-8")
        na, nb = _numbers(r_a.stdout or ""), _numbers(r_b.stdout or "")
        merged_ok = bool(na) and na == nb
        checks_run += 1
        print(f"  {'PASS' if merged_ok else 'FAIL'} "
              f"[合并稿与逐章口径一致] 逐章={na} 合并稿={nb}")
        if not merged_ok:
            fails.append("merged")
            print("        ", (r_b.stdout or "")[:300].replace("\n", " "))

        # 书目录自动走正文/，不混入投稿包里的体检报告/投稿邮件
        r_c = subprocess.run(
            [sys.executable, "-X", "utf8", str(TOOL),
             "--manuscript", str(book), "--title", "口径测试"],
            capture_output=True, encoding="utf-8")
        nc = _numbers(r_c.stdout or "")
        dir_ok = nc == na
        checks_run += 1
        print(f"  {'PASS' if dir_ok else 'FAIL'} "
              f"[书目录自动走正文，不混入体检报告等非稿件] {nc}")
        if not dir_ok:
            fails.append("dir-auto")
    else:
        print("  SKIP [合并稿一致性：找不到真实书，跳过]")

    passed = checks_run - len(fails)
    print(f"\n{passed}/{checks_run} 通过")
    if fails:
        print("WORDCOUNT_CALIBER_FAILED")
        return 1
    print("WORDCOUNT_CALIBER_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
