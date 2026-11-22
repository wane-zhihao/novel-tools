#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_signed_corpus_anatomy.py — 签约语料解构工具单元测试"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts")
for p in (HERE, SCRIPTS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from signed_corpus_anatomy import (
    clean_markdown_chapter,
    extract_chapter_metrics,
    compute_distribution,
    analyze_corpus,
)


class TestSignedCorpusAnatomy(unittest.TestCase):
    def test_clean_markdown_chapter(self):
        raw = "---\ntitle: ch1\n---\n# 第1章 开局\n\n“逆子，退婚！”\n这是正文。"
        clean = clean_markdown_chapter(raw)
        self.assertNotIn("title: ch1", clean)
        self.assertNotIn("# 第1章", clean)
        self.assertTrue(clean.startswith("“逆子，退婚！”"))

    def test_extract_chapter_metrics(self):
        text = """“把这一纸转让协议签了，滚出去！”大伯厉声威胁。
陈砚反手夺过协议撕碎，当场撕成粉碎！
大伯气急败坏，甩出100元存折。
门外突然传来一声轰鸣！到底是谁来了？"""
        m = extract_chapter_metrics(text, is_chapter_1=True)
        self.assertTrue(m["cjk_chars"] > 30)
        self.assertLess(m["conflict_offset"], 50)
        self.assertEqual(m["preamble_len"], 0)
        self.assertTrue(m["cliffhanger"])
        self.assertGreater(m["stakes_count"], 0)

    def test_compute_distribution(self):
        vals = [10.0, 20.0, 30.0, 40.0, 50.0]
        dist = compute_distribution(vals)
        self.assertEqual(dist["min"], 10.0)
        self.assertEqual(dist["max"], 50.0)
        self.assertEqual(dist["p50"], 30.0)
        self.assertEqual(dist["n"], 5)

    def test_analyze_corpus_mock(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            book_dir = os.path.join(tmpdir, "mock_book")
            os.makedirs(book_dir, exist_ok=True)
            ch1 = os.path.join(book_dir, "ch01_第1章.md")
            with open(ch1, "w", encoding="utf-8") as f:
                f.write("# 第1章\n“退婚！”大伯冷笑。\n突然门开了！")
            baseline = analyze_corpus([tmpdir])
            self.assertEqual(baseline["sample_books_count"], 1)
            self.assertEqual(baseline["total_chapters_analyzed"], 1)
            self.assertIn("benchmarks", baseline)


if __name__ == "__main__":
    unittest.main()
