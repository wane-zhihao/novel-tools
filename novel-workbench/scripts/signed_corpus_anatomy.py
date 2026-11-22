#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""signed_corpus_anatomy.py — 签约语料深层因果特征解构与基准提炼器

从成熟商业平台（七猫等）的已签约样本黄金三章中，反向推导并量化出客观的商业叙事标准：
  1. 开篇危机切入点（opening_conflict_offset）：第1章首个不可调和矛盾/迫害/突发事件的字符偏移；
  2. 开篇纯背景阻滞长度（opening_preamble_chars）：正文首处对话或极端动作前的连续叙述长度；
  3. 利益与筹码具象化密度（tangible_stakes_per_kilo）：实体物资、凭证、金额、级别在每千字中出现的频次；
  4. 章末断章悬念率（cliffhanger_ratio）：章尾最后 200 字具备突发危机或未决悬念的比例；
  5. 黄金三章篇幅分布（chapter_words_distribution）：单章纯汉字与总字符的分布基准。

用法:
  python signed_corpus_anatomy.py [--corpus-dir <目录>] [--output-json <路径>] [--selftest]
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
from pathlib import Path

try:
    '·—⚠'.encode(sys.stdout.encoding or 'ascii')
except (UnicodeEncodeError, LookupError):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 冲突/危机动作正则（第一章前部）
CONFLICT_ACTION_PAT = re.compile(
    r"撕碎|撕破|撕毁|按手印|签字|抢走|抢夺|逼迫|逐出|退婚|顶替|冒名|撤销|除名|"
    r"换名|跪下|扇|耳光|滚出去|陷害|克扣|打死|绝境|剥离|抽骨|废掉|死刑|入狱|出狱|"
    r"假账|亏空|死账|夺走|霸占|断绝关系|扫地出门|分家|毒打|砸烂|抄家|未婚先孕|浸猪笼|"
    r"换亲|换了未婚夫|抢走|嫌弃|弄死|卖了|打断腿|偷走|抓走|绑架|强占"
)

# 具象物资/凭证/筹码正则
TANGIBLE_STAKES_PAT = re.compile(
    r"\d+\s*(?:元|块|角|分|万|两|文|斤|尺|亩|株|阶|张|条|本|间|座|批|支|盒|瓶|根)|"
    r"大团结|存折|支票|黑卡|房本|地契|借条|印章|公章|红头文件|介绍信|调令|粮票|"
    r"布票|肉票|青霉素|抚恤金|任命书|工牌|工作证|特种钢|领料单|提货单|抵押字据|"
    r"灵石|神丹|仙草|法宝|至尊骨|重瞳|神脉|天凰血|金塔|玉佩|手镯|戒指"
)

# 章末悬念钩子正则（末尾 200 字）
CLIFFHANGER_PAT = re.compile(
    r"[？！\?!]{1,2}|"
    r"就在这时|突然|猛然|骤然|赫然|门外传来|门外猛地|一道冰冷的声音|"
    r"脚步声骤停|轰然|谁|怎么可能|难道是|到底是谁|嘴角勾起一抹|"
    r"冷笑一声|杀机毕露|推门而入|快步冲了进来|脸色剧变|急促的敲门声|"
    r"一把抓住|一把按住|停在半空|悬在头顶|拔出|当啷一声|直奔|冷冷一笑|"
    r"倒吸一口凉气|目瞪口呆|瞳孔猛缩|僵在原地"
)

# 对白开头特征
DIALOGUE_START_PAT = re.compile(r"^[“\"「『]")

CJK_CHAR_PAT = re.compile(r"[\u4e00-\u9fff]")


def clean_markdown_chapter(raw_text: str) -> str:
    """去除 Markdown 头部元数据 (YAML frontmatter) 与标题行，保留干净正文。"""
    # 去除 yaml frontmatter
    text = re.sub(r"^---\n.*?\n---\n", "", raw_text, flags=re.DOTALL)
    # 去除 markdown 标题 (# 第1章 ...)
    text = re.sub(r"^#+.*$", "", text, flags=re.MULTILINE)
    return text.strip()


def extract_chapter_metrics(clean_text: str, is_chapter_1: bool = False) -> dict:
    """提取单章的因果与节奏量化指标。"""
    lines = [line.strip() for line in clean_text.splitlines() if line.strip()]
    cjk_chars = len(CJK_CHAR_PAT.findall(clean_text))
    total_visible = len(re.sub(r"\s+", "", clean_text))
    
    # 1. 具象筹码密度 (每千字出现次数)
    stakes_matches = list(TANGIBLE_STAKES_PAT.finditer(clean_text))
    stakes_count = len(stakes_matches)
    stakes_per_kilo = round((stakes_count / max(1, cjk_chars)) * 1000, 2)
    
    # 2. 章末断章悬念检测（最后 250 字）
    tail_chars = clean_text[-250:] if len(clean_text) >= 250 else clean_text
    cliffhanger = bool(CLIFFHANGER_PAT.search(tail_chars))
    
    result = {
        "cjk_chars": cjk_chars,
        "visible_chars": total_visible,
        "stakes_count": stakes_count,
        "stakes_per_kilo": stakes_per_kilo,
        "cliffhanger": cliffhanger,
    }
    
    # 3. 第一章专用指标：开篇危机进入点与纯背景阻滞
    if is_chapter_1:
        # 首个冲突动作位置
        conflict_match = CONFLICT_ACTION_PAT.search(clean_text[:1200])
        conflict_offset = conflict_match.start() if conflict_match else -1
        
        # 纯背景长度：从开头到第一处对白或极端动作前的字符数
        preamble_len = 0
        current_len = 0
        found_action_or_dialogue = False
        for line in lines:
            line_len = len(line)
            if DIALOGUE_START_PAT.search(line) or CONFLICT_ACTION_PAT.search(line):
                found_action_or_dialogue = True
                preamble_len = current_len
                break
            current_len += line_len
            if current_len > 1500:
                break
        if not found_action_or_dialogue:
            preamble_len = current_len
            
        result["conflict_offset"] = conflict_offset
        result["preamble_len"] = preamble_len
        
    return result


def compute_distribution(values: list[float | int]) -> dict:
    """计算指标分布：min, p10, p50 (中位数), p90, max, mean。"""
    if not values:
        return {"min": 0, "p10": 0, "p50": 0, "p90": 0, "max": 0, "mean": 0, "n": 0}
    s = sorted(values)
    n = len(s)
    
    def percentile(p: float) -> float:
        idx = p * (n - 1)
        lower = math.floor(idx)
        upper = math.ceil(idx)
        if lower == upper:
            return round(float(s[int(idx)]), 2)
        weight = idx - lower
        return round(float(s[lower] * (1 - weight) + s[upper] * weight), 2)

    return {
        "n": n,
        "min": round(float(s[0]), 2),
        "p10": percentile(0.10),
        "p50": percentile(0.50),
        "p90": percentile(0.90),
        "max": round(float(s[-1]), 2),
        "mean": round(sum(values) / n, 2),
    }


def analyze_corpus(corpus_dirs: list[str]) -> dict:
    """扫描指定语料库目录下的所有签约书黄金三章，产出宏观基准。"""
    books_data = {}
    ch1_conflicts = []
    ch1_preambles = []
    all_stakes_per_kilo = []
    all_cjk_words = []
    all_cliffhangers = []
    
    book_dirs = []
    for cd in corpus_dirs:
        if not os.path.isdir(cd):
            continue
        for entry in os.scandir(cd):
            if entry.is_dir() and not entry.name.startswith(('.', '_')):
                book_dirs.append(entry.path)
                
    for bdir in sorted(book_dirs):
        bname = os.path.basename(bdir)
        ch_files = sorted(glob.glob(os.path.join(bdir, "*.md")))
        if not ch_files:
            continue
            
        book_info = {"chapters": []}
        for idx, ch_path in enumerate(ch_files):
            try:
                with open(ch_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                with open(ch_path, "r", encoding="gb18030", errors="replace") as f:
                    content = f.read()
            clean = clean_markdown_chapter(content)
            is_ch1 = (idx == 0 or "第1章" in ch_path or "ch01" in ch_path)
            metrics = extract_chapter_metrics(clean, is_chapter_1=is_ch1)
            
            all_cjk_words.append(metrics["cjk_chars"])
            all_stakes_per_kilo.append(metrics["stakes_per_kilo"])
            all_cliffhangers.append(1 if metrics["cliffhanger"] else 0)
            
            if is_ch1:
                if metrics.get("conflict_offset", -1) != -1:
                    ch1_conflicts.append(metrics["conflict_offset"])
                ch1_preambles.append(metrics.get("preamble_len", 0))
                
            book_info["chapters"].append({
                "file": os.path.basename(ch_path),
                "is_ch1": is_ch1,
                "metrics": metrics,
            })
            
        books_data[bname] = book_info
        
    baseline = {
        "version": 1,
        "sample_books_count": len(books_data),
        "total_chapters_analyzed": len(all_cjk_words),
        "benchmarks": {
            "opening_conflict_offset": compute_distribution(ch1_conflicts),
            "opening_preamble_chars": compute_distribution(ch1_preambles),
            "tangible_stakes_per_kilo": compute_distribution(all_stakes_per_kilo),
            "chapter_cjk_words": compute_distribution(all_cjk_words),
            "cliffhanger_rate": round(sum(all_cliffhangers) / max(1, len(all_cliffhangers)), 3),
        },
        "books": books_data,
    }
    return baseline


def run_selftest() -> bool:
    """运行自检，验证提取器与分布统计逻辑。"""
    sample_ch1 = """---
title: 第1章 测试
---
# 第1章 测试
“把这一式婚书撕碎，你滚出我们家！”大伯厉声喝斥。
大伯狠狠将100元存折摔在桌上。
窗外突然传来一声枪响！
"""
    clean = clean_markdown_chapter(sample_ch1)
    m = extract_chapter_metrics(clean, is_chapter_1=True)
    assert m["cjk_chars"] > 0, "CJK chars extraction failed"
    assert m["conflict_offset"] >= 0, "Conflict offset extraction failed"
    assert m["stakes_count"] >= 1, "Tangible stakes extraction failed"
    assert m["cliffhanger"] is True, "Cliffhanger detection failed"
    assert m["preamble_len"] == 0, "Preamble calculation failed (dialogue at line 1)"
    
    dist = compute_distribution([10, 20, 30, 40, 50])
    assert dist["p50"] == 30.0, f"Distribution p50 mismatch: {dist['p50']}"
    assert dist["min"] == 10.0 and dist["max"] == 50.0
    return True


def main():
    parser = argparse.ArgumentParser(description="签约语料商业因果与节奏基准解构工具")
    parser.add_argument("--corpus-dir", nargs="*", help="语料库根目录列表")
    parser.add_argument("--output-json", help="输出基准 JSON 文件路径")
    parser.add_argument("--selftest", action="store_true", help="运行内部单元自检")
    args = parser.parse_args()
    
    if args.selftest:
        if run_selftest():
            print("SIGNED_CORPUS_ANATOMY_SELFTEST_OK")
            sys.exit(0)
        sys.exit(1)
        
    script_dir = os.path.dirname(os.path.abspath(__file__))
    platforms_root = os.path.dirname(os.path.dirname(script_dir))
    repo_root = os.path.dirname(platforms_root)
    
    corpus_dirs = args.corpus_dir
    if not corpus_dirs:
        corpus_dirs = [
            os.path.join(platforms_root, "qimao", "corpus", "positive"),
            os.path.join(platforms_root, "qimao", "corpus", "positive_niandai"),
        ]
        
    baseline = analyze_corpus(corpus_dirs)
    
    output_path = args.output_json
    if not output_path:
        output_path = os.path.join(platforms_root, "qimao", "harness", "signed_anatomy_baseline.json")
        
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)
        
    print(f"ANATOMY_COMPLETE: Analyzed {baseline['sample_books_count']} books, "
          f"{baseline['total_chapters_analyzed']} chapters -> {output_path}")
    print(f"Opening Conflict p50: {baseline['benchmarks']['opening_conflict_offset']['p50']} chars")
    print(f"Preamble Length p50: {baseline['benchmarks']['opening_preamble_chars']['p50']} chars")
    print(f"Tangible Stakes p50: {baseline['benchmarks']['tangible_stakes_per_kilo']['p50']} /kilo")
    print(f"Cliffhanger Rate: {baseline['benchmarks']['cliffhanger_rate'] * 100:.1f}%")


if __name__ == "__main__":
    main()
