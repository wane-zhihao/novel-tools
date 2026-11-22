#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_auto_trim.py — 自动裁剪必须真的发生（它此前是死代码）

**为什么要有**（2026-11-20 实测）：

`_text_preflight` 里的自动裁剪写于 2026-11-17，用来替掉「超长章由人逐段读、
逐段删」的手工活（实测第 2 章手工裁了 689 字）。但它**从来没有跑起来过**：

```python
try:
    from inline_runner import trim_to_band, CHAPTER_BODY_MAX_CHARS   # ← 抛 ImportError
    from wordcount_core import count_visible_chars                  # ← 模块不在 sys.path
    ...
except Exception:
    trim_notes = []          # ← 吞掉，什么都不说
```

两个错：
1. `CHAPTER_BODY_MAX_CHARS` 在 `pipeline_spec` 里，不在 `inline_runner`；
2. `wordcount_core`（平台字数口径的唯一实现）只存在于 story-long-write
   技能的 scripts 目录里，工作台从没把它挂到 `sys.path`。

裸 `except Exception` 把这两个错吞成「没什么可裁」——于是**工具坏了**和
**没发现问题**变成了同一件事（和之前 `IndexError` 被吞成「无问题」是
同一类病）。实测：第 1 章正文 2695 可见字，判 over 打回，裁剪一次没跑。

修法：
- 常量从 `pipeline_spec` 导入；
- `inline_runner.visible_chars()` / `_ensure_skill_path()`：按 config 里的
  `STORY_LONG_WRITE` 挂技能 scripts 目录，**只挂路径不抄实现**（抄一份就
  会漂移）；
- 裁剪失败不再吞：把原因写进返回载荷的 `auto_trim_error`。

跑法：
  python -X utf8 server/tests/test_auto_trim.py
  → 全过打印 AUTO_TRIM_OK 并 exit 0
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'} [{name}]" + ("" if ok else f" —— {detail}"))
    if not ok:
        FAILS.append(name)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    print("== 自动裁剪：必须真的发生 ==")
    root = Path(tempfile.mkdtemp(prefix="auto_trim_"))
    try:
        # ① 平台字数口径可用（技能目录已挂上 sys.path）
        try:
            from inline_runner import visible_chars
            n = visible_chars("他走了。\n")
            ok = isinstance(n, int) and n > 0
            check("visible_chars 可用（技能目录已挂路径）", ok, f"n={n}")
        except Exception as exc:
            check("visible_chars 可用（技能目录已挂路径）", False,
                  f"{type(exc).__name__}: {exc}")

        # ② 裁剪前那一行 import 不再抛 ImportError
        #    （CHAPTER_BODY_MAX_CHARS 在 pipeline_spec，不在 inline_runner）
        try:
            from inline_runner import trim_to_band
            from pipeline_spec import CHAPTER_BODY_MAX_CHARS
            check("裁剪依赖的 import 不再抛 ImportError", True)
        except Exception as exc:
            check("裁剪依赖的 import 不再抛 ImportError", False,
                  f"{type(exc).__name__}: {exc}")

        # ③ 超长章真的被裁进带内
        from inline_runner import trim_to_band, visible_chars

        book = root / "book"
        # 注意：段落里不能有数字/引号/【】——裁剪规则会**硬保护**这些段
        #（对话段、数字段、系统提示不许删），否则一个候选都没有，裁不动。
        filler = [
            "他把工具放回架子上，又看了一眼停着的机器。",
            "车间里很静，只有远处传来的敲击声。",
            "他伸手抹掉台面上的机油，指尖留下一点黑。",
            "窗外的天色暗下来，玻璃上映出他自己的脸。",
            "有人在走廊里说话，声音隔着墙听不清。",
        ]
        paras = [filler[i % len(filler)] for i in range(24)]
        # 末段要像钩子，首段要像开场冲突：这两段受保护，不该被删
        paras[0] = "辞呈压在桌角，他伸手把纸角按平。"
        paras[-1] = "门外有人在敲铁皮，一下，两下。"
        write(book / "正文" / "第001章_测试.md", "\n\n".join(paras))
        write(book / "追踪" / "_tracking-state.json", json.dumps(
            {"last_committed_chapter": 1, "wordcount_records": {}}, ensure_ascii=False))

        target = book / "正文" / "第001章_测试.md"
        before = visible_chars(target.read_text(encoding="utf-8"))
        removed, notes = trim_to_band(book, 1, band_max=180, band_min=60)
        after = visible_chars(target.read_text(encoding="utf-8"))
        check("超长章被裁到上限以内", after <= 180, f"before={before} after={after}")
        check("裁剪确实删了内容", removed > 0 and after < before,
              f"removed={removed} before={before} after={after}")
        check("裁剪有可读说明", bool(notes), str(notes))
        text = target.read_text(encoding="utf-8")
        check("开场段与末段（钩子）受保护未删",
              "辞呈压在桌角" in text and "铁皮" in text, text[:60])

        # ④ 不吞异常：裁剪失败时错误必须可见
        #    （用不存在的 chapter 触发 body_file_for_chapter 返回空 → 返回 0 条，
        #      这里验证「无章可裁」是正常返回而不是异常被吞）
        removed2, notes2 = trim_to_band(book, 99, band_max=100, band_min=10)
        check("无此章时返回空说明而非抛异常",
              removed2 == 0 and isinstance(notes2, list), f"{removed2} {notes2}")

        # ⑤ 真实书上的预检不再静默：裁剪要么发生、要么把原因报出来
        real = Path(r"F:\小说\works\冷启动验收书")
        if (real / "正文").is_dir() and (real / "大纲").is_dir():
            from autopilot import load_state
            from commands import _text_preflight
            st = load_state(real)
            if st and st.get("cursor") == "C1.N12":
                err = _text_preflight(real, "C1.N12", st)
                body_files = sorted((real / "正文").glob("第001章_*.md"))
                if body_files:
                    now_n = visible_chars(body_files[0].read_text(encoding="utf-8"))
                    ok = now_n <= 2600 or (err or {}).get("auto_trim_error")
                    check("真实书第 1 章：已裁进带内，或裁剪失败原因被报出来", bool(ok),
                          f"n={now_n} err={json.dumps(err, ensure_ascii=False)[:200] if err else None}")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print(f"\n{'全部通过' if not FAILS else '失败: ' + ', '.join(FAILS)}")
    if FAILS:
        print("AUTO_TRIM_FAILED")
        return 1
    print("AUTO_TRIM_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
