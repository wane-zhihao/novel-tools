#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_architecture_consistency_gate.py — 架构因果门禁单元测试"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKBENCH_ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPTS_DIR = os.path.join(WORKBENCH_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from architecture_consistency_gate import (
    parse_outline_spec,
    check_advance_reveal_violations,
    verify_outline_beat_execution,
    check_narrative_drift,
    verify_chapter_architecture,
)


class TestArchitectureConsistencyGate(unittest.TestCase):
    def setUp(self):
        self.outline_content = """# 第001章 细纲：退婚风波
- 核心事件：前任上门逼迫退婚，主角反手迎娶前任小姑
- 本章禁止提前释放：禁止第一章提前暴露仙帝修为、禁止让前任下跪求饶
### 情节安排
- 第一拍（登门受辱）：前任带着族老上门撕毁婚约，言语极尽刻薄。
- 第二拍（主角反击）：主角撕碎婚书，当众宣布退婚。
- 第三拍（迎娶小姑）：主角反手向坐在一旁的前任小姑提亲，小姑当场应允，全场震撼。
- 第四拍（章末悬念）：族长震怒欲出手，门外突然传来警钟长鸣。
"""
        self.good_chapter = """“陈枫，把这纸婚书撕碎，你一个废脉不配娶我！”前任林薇带着族老踏进陈家大门，神态倨傲。
陈枫冷笑一声，反手夺过婚书，当众撕成漫天碎纸！
接着陈枫目光转向坐在一旁的绝美女长老：“林小姑，你可愿嫁我？”绝美女长老当场嫣然一笑应允，全场族老倒吸一口凉气！
族长大怒拍案而起，就在这时，门外突然传来一声轰鸣警钟！到底发生了什么？"""

    def test_parse_outline_spec(self):
        spec = parse_outline_spec(self.outline_content)
        self.assertIn("逼迫退婚", spec["core_event"])
        self.assertEqual(len(spec["bans"]), 2)
        self.assertEqual(len(spec["beats"]), 4)

    def test_beat_execution_pass(self):
        res = verify_outline_beat_execution(self.outline_content, self.good_chapter)
        self.assertTrue(res["ok"])
        self.assertGreaterEqual(res["rate"], 0.75)

    def test_advance_reveal_violation(self):
        # 违规文本：让前任下跪求饶
        bad_chapter = self.good_chapter + "\n林薇吓得花容失色，当场跪地求饶：“陈枫我错了，求你放过我！”"
        violations = check_advance_reveal_violations(self.outline_content, bad_chapter)
        self.assertGreater(len(violations), 0)
        self.assertIn("下跪", violations[0]["matched"])

    def test_advance_reveal_cultivation_leak(self):
        # 违规文本：暴露仙帝修为
        bad_chapter = self.good_chapter + "\n陈枫周身金光暴涨，提前暴露仙帝修为，神威压盖万古！"
        violations = check_advance_reveal_violations(self.outline_content, bad_chapter)
        self.assertGreater(len(violations), 0)

    def test_narrative_drift_detection(self):
        drift_block = "\n" + ("山上的积雪正在慢慢融化，远处的河流静静流淌，万物复苏，岁月静好，没有人知道明天会怎样，生活就像一首诗歌一样悠扬平和。" * 8)
        drifting_chapter = self.good_chapter + drift_block
        res = check_narrative_drift(self.outline_content, drifting_chapter)
        self.assertFalse(res["ok"])
        self.assertGreaterEqual(res["drift_block_count"], 1)

    def test_verify_chapter_architecture_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "outline.md")
            ch_file = os.path.join(tmpdir, "ch01.md")
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(self.outline_content)
            with open(ch_file, "w", encoding="utf-8") as f:
                f.write(self.good_chapter)
            res = verify_chapter_architecture(out_file, ch_file)
            self.assertTrue(res["ok"])


if __name__ == "__main__":
    unittest.main()
