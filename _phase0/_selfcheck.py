# -*- coding: utf-8 -*-
"""联试前自检：章节节点键可解析 + 游标链推进。"""
import sys
from pathlib import Path

sys.path.insert(0, r"F:\WorkBuddy AI\Novel Platform\novel-workbench\scripts")
from pipeline_spec import chapter_nodes, next_cursor  # noqa: E402

d = chapter_nodes({}, Path("."), 1)
print("keys:", list(d.keys()))
print("get C1.N12:", d.get("C1.N12") is not None)

st = {"cursor": "C1.N09", "current_chapter": 0, "max_chapters": 12,
      "words_total": 0, "target_words": 20000}
seq = []
for _ in range(9):
    st["cursor"] = next_cursor(st)
    seq.append(st["cursor"])
print("cursor chain:", seq)
ok = True
for c in seq:
    if not c.startswith("C"):
        continue
    n = int(c[1:].split(".")[0])          # 章号从游标自身解析
    if chapter_nodes(st, Path("."), n).get(c) is None:
        ok = False
        print("UNRESOLVABLE:", c)
print("all chapter nodes resolvable:", ok)
