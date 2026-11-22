#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_persona_confidence_probe.py — 人设置信度与动机因果单元测试"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKBENCH_ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPTS_DIR = os.path.join(WORKBENCH_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from persona_confidence_probe import (
    identify_protagonist,
    check_protagonist_agency,
    check_knowledge_boundary,
    probe_chapter_persona_confidence,
)


class TestPersonaConfidenceProbe(unittest.TestCase):
    def setUp(self):
        self.good_text = """陈砚冷笑一声，反手夺过协议当场撕碎！
陈砚目光如刀，厉声喝道：“大伯，想逼我让出父亲的顶职名额，你休想！”
大伯气急败坏。陈砚果断锁好大门，沉声叮嘱妹妹，随后直奔厂办大楼。"""

        self.weak_text = """陈砚委屈得直掉眼泪，面对大伯的凶狠威逼，陈砚只能唯唯诺诺，心甘情愿让出一切。
陈砚毫无反抗之力，只能任由大伯夺走名额，绝望等死。"""

    def test_protagonist_agency_good(self):
        res = check_protagonist_agency("陈砚", self.good_text)
        self.assertTrue(res["ok"])
        self.assertGreaterEqual(res["score"], 0.7)
        self.assertGreaterEqual(res["proactive_actions"], 2)
        self.assertEqual(res["submission_hits"], 0)

    def test_protagonist_agency_passive_fail(self):
        res = check_protagonist_agency("陈砚", self.weak_text)
        self.assertFalse(res["ok"])
        self.assertLess(res["score"], 0.6)
        self.assertGreaterEqual(res["submission_hits"], 1)

    def test_knowledge_boundary_leak(self):
        text_with_leak = self.good_text + "\n陈砚冷笑道：“王德发藏在天花板暗格里的绝密账本代号是702！”"
        known = ["父亲抚恤金被克扣", "大伯逼签转让协议"]
        secrets = ["天花板暗格绝密账本代号702"]
        leaks = check_knowledge_boundary("陈砚", known, secrets, text_with_leak)
        self.assertGreater(len(leaks), 0)
        self.assertEqual(leaks[0]["character"], "陈砚")

    def test_probe_chapter_persona_confidence_overall(self):
        res = probe_chapter_persona_confidence(
            clean_text=self.good_text,
            protagonist_name="陈砚",
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["protagonist"], "陈砚")


if __name__ == "__main__":
    unittest.main()
