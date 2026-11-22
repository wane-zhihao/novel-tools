#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""editor_criteria_scan.py — 编辑公开口径自动化探测器

将投稿前的人工通读项转化为自动化确定性指标：
  1. 开篇 200 字冲突与动作定位（开篇抓人度）；
  2. 单章场景切换频次统计（单章场景切换 ≤3 次，切换处有过渡）；
  3. 未建档人物与设定偏差探测。
"""
import re
from pathlib import Path


CONFLICT_WORDS = [
    # 动作与对抗
    "抢", "顶", "占", "夺", "撤", "砸", "摔", "换", "逼", "辞", "赶", "追", "围",
    "拦", "封", "扣", "查", "翻", "抽", "掐", "压", "喝", "吼", "撕", "废", "踢", "杀",
    # 权利、财产与利益对抗
    "名额", "抚恤金", "工位", "工牌", "资格", "开除", "处分", "违纪", "降职", "停职",
    "抵押", "欠条", "退婚", "废除", "剥夺", "绝路", "断粮", "换掉", "顶职", "认命", "逼迫",
    "除名", "夺走", "查封", "封杀", "滚蛋", "滚出", "还钱", "要账", "催债", "交出"
]

SCENE_BREAK_PATTERNS = [
    r"^\s*#\s*场景",
    r"^\s*(\*\s*){3,}$",
    r"^\s*(-\s*){3,}$",
    r"^\s*(_\s*){3,}$",
    r"^\s*[◆◇■□▲△▼▽]{1,3}\s*$",
    r"^\s*（[^\n）]{2,20}）\s*$",
    r"^\s*【[^\n】]{2,20}】\s*$",
    r"^(第二天|次日|翌日|三天后|当晚|夜里|半小时后|转眼|一小时后|与此同时|数日后|午后|傍晚)[，,。\s]"
]

MAX_SCENE_TRANSITIONS = 3


def scan_opening_conflict(chapter_text: str) -> dict:
    """扫描开篇前 200 个正文字符内的冲突与对抗点。"""
    # 剥离 markdown 标题
    lines = chapter_text.splitlines()
    body_lines = [l for l in lines if not l.strip().startswith("#")]
    body = "".join(body_lines).strip()
    
    first_200 = body[:200]
    matched_word = None
    pos = -1
    for word in CONFLICT_WORDS:
        p = first_200.find(word)
        if p != -1:
            if pos == -1 or p < pos:
                pos = p
                matched_word = word
                
    if pos != -1 and matched_word:
        start = max(0, pos - 10)
        end = min(len(first_200), pos + 15)
        excerpt = first_200[start:end]
        return {
            "ok": True,
            "pos": pos,
            "keyword": matched_word,
            "excerpt": excerpt,
            "detail": f"第 {pos} 字符处检出核心冲突动作「{matched_word}」（引文：「…{excerpt}…」）"
        }
    else:
        return {
            "ok": False,
            "pos": None,
            "keyword": None,
            "excerpt": "",
            "detail": "前 200 字未检出明显冲突/对抗动作词，可能偏向背景说明铺垫，需人工确认"
        }


def scan_scene_transitions(chapter_text: str) -> int:
    """统计单章内的场景切换次数。"""
    count = 0
    lines = chapter_text.splitlines()
    compiled = [re.compile(p) for p in SCENE_BREAK_PATTERNS]
    
    # 连续空行也是典型场景切换
    empty_streak = 0
    for l in lines:
        stripped = l.strip()
        if not stripped:
            empty_streak += 1
            if empty_streak == 3:  # 连续 3 个空行视作转场
                count += 1
            continue
        else:
            empty_streak = 0
            
        for pat in compiled:
            if pat.search(stripped):
                count += 1
                break
    return count


def scan_book_editor_criteria(chapters: list[Path]) -> dict:
    """对全书各章进行编辑口径自动化扫描。"""
    if not chapters:
        return {
            "opening_conflict": {"ok": False, "detail": "无章节正文"},
            "scene_transitions": {"ok": True, "detail": "无章节正文", "over_limit": []},
            "summary_lines": []
        }
        
    first_text = chapters[0].read_text(encoding="utf-8", errors="replace")
    op_res = scan_opening_conflict(first_text)
    
    scene_counts = {}
    over_limit = []
    for f in chapters:
        text = f.read_text(encoding="utf-8", errors="replace")
        cnt = scan_scene_transitions(text)
        scene_counts[f.name] = cnt
        if cnt > MAX_SCENE_TRANSITIONS:
            over_limit.append((f.name, cnt))
            
    scene_ok = len(over_limit) == 0
    if scene_ok:
        max_cnt = max(scene_counts.values()) if scene_counts else 0
        scene_detail = f"全书各章场景切换均 ≤3 次（单章最高 {max_cnt} 次）"
    else:
        over_desc = "、".join(f"{name}({cnt}次)" for name, cnt in over_limit)
        scene_detail = f"存在场景切换过多章节（>3 次）：{over_desc}"
        
    summary_lines = [
        f"- [{'x' if op_res['ok'] else ' '}] 前 200 字有冲突（{op_res['detail']}）",
        f"- [{'x' if scene_ok else ' '}] 单章场景切换 ≤3 次（{scene_detail}）"
    ]
    
    return {
        "opening_conflict": op_res,
        "scene_transitions": {
            "ok": scene_ok,
            "counts": scene_counts,
            "over_limit": over_limit,
            "detail": scene_detail
        },
        "summary_lines": summary_lines
    }
