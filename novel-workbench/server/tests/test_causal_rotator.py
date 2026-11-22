#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_causal_rotator.py — 因果词多样性自动轮换器测试"""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import causal_rotator as cr


def test_suggest_causal_replacements():
    text = """第1段：他走过去，果然发现锁被撬开了。
第2段：账本上果然少了一页。
第3段：院子里的脚印果然通向库房。
第4段：天黑的时候，事情果然如他所料。"""

    suggestions = cr.suggest_causal_replacements(text, "果然", max_suggestions=3)
    assert len(suggestions) == 3, f"Expected 3 suggestions, got {len(suggestions)}"
    assert suggestions[0]["line"] == 1
    assert suggestions[0]["original"] == "果然"
    assert suggestions[0]["replacement"] in ("果不其然", "不出所料", "正如所料")
    assert "果然发现锁" in suggestions[0]["context_before"]


def test_auto_rotate_excess_causal():
    text = "果然一。果然二。果然三。果然四。果然五。"
    new_text, cnt = cr.auto_rotate_excess_causal(text, "果然", max_keep=2)
    assert cnt == 3, f"Expected 3 replacements, got {cnt}"
    assert new_text.count("果然") == 2, f"Expected 2 '果然', got {new_text.count('果然')}"
    assert "果不其然" in new_text or "不出所料" in new_text


if __name__ == "__main__":
    test_suggest_causal_replacements()
    test_auto_rotate_excess_causal()
    print("CAUSAL_ROTATOR_OK")
