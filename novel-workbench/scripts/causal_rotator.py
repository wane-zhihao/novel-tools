#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""causal_rotator.py — 因果词多样性自动轮换与保义替换器

针对因果词过度集中（如「果然」刷屏占 >60% 的 AI 指纹问题）：
提供签约样本实测有效的因果词轮换候选，支持自动给出保义替换建议。
"""
import re

CAUSAL_ROTATION_MAP = {
    "果然": ["果不其然", "不出所料", "正如所料", "结果", "这下", "这才", "当真"],
    "于是": ["随后", "随即", "顺势", "借此", "转而", "为此", "紧接着"],
    "原来": ["这才发觉", "方知", "怪不得", "难怪", "原是", "这才看出"],
    "所以": ["因而", "正因如此", "以至于", "这才", "为此", "故而"],
    "因为": ["由于", "因着", "全因", "只因", "鉴于"],
    "因此": ["为此", "由此", "正因如此", "因而", "遂"],
    "难怪": ["怪不得", "原来如此", "这就难怪", "由不得"],
    "结果": ["到头来", "末了", "反倒", "最后", "终究"],
    "这才": ["方才", "直到此时", "方", "这时才"],
    "偏偏": ["恰恰", "反倒", "偏生", "硬是", "横竖"]
}


def get_rotation_candidates(word: str) -> list[str]:
    """获取指定因果词的保义轮换候选词列表。"""
    return CAUSAL_ROTATION_MAP.get(word, ["因此", "于是", "结果", "这才"])


def suggest_causal_replacements(text: str, top_word: str, max_suggestions: int = 8) -> list[dict]:
    """在文本中查找高频因果词的位置，并按轮换池提供精准替换建议。"""
    candidates = get_rotation_candidates(top_word)
    if not candidates:
        return []

    lines = text.splitlines()
    suggestions = []
    c_idx = 0

    for line_no, line in enumerate(lines, start=1):
        if top_word not in line:
            continue
        # 寻找匹配
        for match in re.finditer(re.escape(top_word), line):
            start = match.start()
            end = match.end()
            cand = candidates[c_idx % len(candidates)]
            c_idx += 1
            
            # 截取前后上下文
            ctx_start = max(0, start - 12)
            ctx_end = min(len(line), end + 12)
            orig_snippet = line[ctx_start:ctx_end]
            repl_snippet = line[ctx_start:start] + cand + line[end:ctx_end]
            
            suggestions.append({
                "line": line_no,
                "original": top_word,
                "replacement": cand,
                "context_before": orig_snippet,
                "context_after": repl_snippet
            })
            if len(suggestions) >= max_suggestions:
                return suggestions

    return suggestions


def auto_rotate_excess_causal(text: str, top_word: str, max_keep: int = 3) -> tuple[str, int]:
    """自动将超出保持数量的同名因果词按轮换池替换，降低其集中度。
    
    返回 (新文本, 替换数量)。
    """
    candidates = get_rotation_candidates(top_word)
    if not candidates:
        return text, 0

    lines = text.splitlines()
    new_lines = []
    seen = 0
    replaced_count = 0
    c_idx = 0

    for line in lines:
        if top_word not in line:
            new_lines.append(line)
            continue

        parts = line.split(top_word)
        rebuilt = [parts[0]]
        for part in parts[1:]:
            seen += 1
            if seen <= max_keep:
                # 保留前 max_keep 个
                rebuilt.append(top_word + part)
            else:
                # 超额部分轮换
                cand = candidates[c_idx % len(candidates)]
                c_idx += 1
                rebuilt.append(cand + part)
                replaced_count += 1
        new_lines.append("".join(rebuilt))

    return "\n".join(new_lines), replaced_count
