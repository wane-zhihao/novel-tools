#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_editor_criteria_scan.py — 编辑口径自动化探测器单元与契约测试"""
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import editor_criteria_scan as ecs


def test_opening_conflict_positive():
    text = "# 第001章 顶职\n\n顶职名单今天换名字，换掉的正是陆明。厂办主任把公章一扣，冷笑道：你认命吧。"
    res = ecs.scan_opening_conflict(text)
    assert res["ok"] is True, res
    assert res["keyword"] in ("顶", "换", "顶职", "扣"), res
    assert "第" in res["detail"], res


def test_opening_conflict_negative():
    text = "# 第001章 清晨\n\n那是一个阳光明媚的清晨。微风吹过树梢，鸟儿在枝头欢快地唱歌。小镇的街道安静而平和，人们慢慢地走着。"
    res = ecs.scan_opening_conflict(text)
    assert res["ok"] is False, res
    assert res["pos"] is None, res
    assert "未检出明显冲突" in res["detail"], res


def test_scene_transitions_normal_and_over():
    text_normal = """# 第001章 测试
段落一。

***

段落二。
翌日，清晨的阳光洒下来。
段落三。
"""
    cnt = ecs.scan_scene_transitions(text_normal)
    assert cnt == 2, f"Expected 2 transitions, got {cnt}"

    text_over = """# 第002章 频繁切换
段落一。
***
段落二。
---
段落三。
翌日，阳光明媚。
段落四。
当晚，夜色如墨。
段落五。
三天后，风起云涌。
段落六。
"""
    cnt_over = ecs.scan_scene_transitions(text_over)
    assert cnt_over >= 4, f"Expected >= 4 transitions, got {cnt_over}"


def test_book_scan_integration():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        ch1 = p / "第001章_开端.md"
        ch2 = p / "第002章_发展.md"
        
        ch1.write_text("# 第001章 开端\n\n开局名额被抢走，陆明拍桌而起。\n", encoding="utf-8")
        ch2.write_text("# 第002章 发展\n\n正常叙述。\n\n***\n\n下一幕。\n", encoding="utf-8")
        
        rep = ecs.scan_book_editor_criteria([ch1, ch2])
        assert rep["opening_conflict"]["ok"] is True
        assert rep["scene_transitions"]["ok"] is True
        assert len(rep["summary_lines"]) == 2
        assert "前 200 字有冲突" in rep["summary_lines"][0]
        assert "单章场景切换" in rep["summary_lines"][1]


if __name__ == "__main__":
    test_opening_conflict_positive()
    test_opening_conflict_negative()
    test_scene_transitions_normal_and_over()
    test_book_scan_integration()
    print("EDITOR_CRITERIA_SCAN_OK")
