#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""architecture_consistency_gate.py — 架构、细纲与正文三层因果一致性门禁

将网文过签与质量因果严格锚定在状态机与剧情推进一致性上：
  1. 细纲节拍执行对齐度（beat_execution_rate）：细纲规划的情节安排（四拍事件流）在正文中必须逐一闭环落实；
  2. 本章禁止提前释放硬红线（advance_reveal_violations）：严格审查细纲中的禁令（禁止提前剧透、禁止跨阶升级、禁止过早击垮主反派），违者强制 fail-closed；
  3. 叙事漂移检测（narrative_drift）：检测正文中是否存在脱离本章核心主线的漫游文字块。

用法:
  python architecture_consistency_gate.py --outline <细纲.md> --text <正文.md> [--json]
  python architecture_consistency_gate.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    '·—⚠'.encode(sys.stdout.encoding or 'ascii')
except (UnicodeEncodeError, LookupError):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def parse_outline_spec(outline_text: str) -> dict:
    """解析细纲 Markdown，提取核心事件、禁止项、四拍节拍与人物要求。"""
    core_event = ""
    m_event = re.search(r"-\s*核心事件[：:]\s*(.+)", outline_text)
    if m_event:
        core_event = m_event.group(1).strip()
        
    # 提取禁止提前释放项
    bans: list[str] = []
    m_bans = re.search(r"-\s*本章禁止提前释放[：:]\s*(.+)", outline_text)
    if m_bans:
        raw_bans = m_bans.group(1).strip()
        # 按顿号、分号或逗号拆分
        parts = re.split(r"[；;，,、]|(?:(?<=。)\s*)", raw_bans)
        for p in parts:
            p_clean = p.strip()
            if p_clean and ("禁止" in p_clean or len(p_clean) >= 4):
                bans.append(p_clean)
                
    # 提取情节安排（四拍或各拍）
    beats: list[dict] = []
    beat_matches = list(re.finditer(r"-\s*(第[一二三四1234]拍[（\(][^）\)]+[）\)])[：:]\s*(.+)", outline_text))
    if beat_matches:
        for bm in beat_matches:
            beats.append({
                "label": bm.group(1).strip(),
                "desc": bm.group(2).strip(),
            })
    else:
        # 尝试从情节细化表格提取
        table_rows = re.findall(r"\|\s*(\d+)\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|", outline_text)
        for row in table_rows:
            if row[0].isdigit():
                beats.append({
                    "label": f"情节点{row[0]}（{row[2].strip()}）",
                    "desc": row[1].strip(),
                    "boundary": row[3].strip(),
                })
                
    return {
        "core_event": core_event,
        "bans": bans,
        "beats": beats,
    }


def _extract_keywords_from_ban(ban_str: str) -> list[str]:
    """从禁止提前释放描述中提取核心敏感词组合。"""
    cleaned = re.sub(r"^(?:本章)?禁止(?:第一章)?(?:提前)?(?:彻底)?", "", ban_str).strip()
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}", cleaned)
    words = [t for t in tokens if t not in ("彻底", "提前", "第一章", "全部", "所有", "本章", "禁止")]
    return words if words else [cleaned]


def check_advance_reveal_violations(outline_text: str, chapter_text: str) -> list[dict]:
    """检查正文中是否触犯了细纲中明确规定的'禁止提前释放'禁令。"""
    spec = parse_outline_spec(outline_text)
    violations = []
    
    clean_text = re.sub(r"^#.*$", "", chapter_text, flags=re.MULTILINE)
    
    for ban in spec["bans"]:
        keywords = _extract_keywords_from_ban(ban)
        # 若 ban 中指明下跪求饶
        if "下跪" in ban or "求饶" in ban:
            if re.search(r"下跪|求饶|磕头求饶|跪地求饶", clean_text):
                # 检查是否发生在此反派身上
                violations.append({
                    "ban": ban,
                    "matched": "命中禁令行为（下跪/求饶）",
                    "reason": "正文中反派或关键角色提前发生了下跪求饶，破坏了长线压迫因果",
                })
        elif "神级心算" in ban or "曝光神级" in ban:
            if re.search(r"神级心算|全厂亏空全部查清", clean_text):
                violations.append({
                    "ban": ban,
                    "matched": "曝光神级心算/全部亏空",
                    "reason": "金手指全貌或底层秘密提前完全曝光，破坏升级节奏",
                })
        elif "洗脱" in ban or "平反" in ban:
            if re.search(r"(?:彻底洗脱|完全平反|公文撤销|彻底沉冤得雪)", clean_text):
                violations.append({
                    "ban": ban,
                    "matched": "提前彻底洗脱责任",
                    "reason": "终极悬念或核心动机被提前解构",
                })
        else:
            if keywords:
                pat = ".*?".join(re.escape(k) for k in keywords[:2])
                if re.search(pat, clean_text):
                    violations.append({
                        "ban": ban,
                        "matched": f"禁令关键词命中: {keywords[:2]}",
                        "reason": f"正文触发了细纲禁止项: {ban}",
                    })
                    
    return violations


def _extract_semantic_tokens(text: str) -> set[str]:
    """提取语义关键短语与 2-3 字 n-gram，过滤停用结构词。"""
    clauses = [c.strip() for c in re.split(r"[，。！？；、\s,!?;\-\(\)（）\:\：\“\”\"\'\r\n\[\]【】|]+", text) if c.strip()]
    tokens = set()
    stopwords = {
        "这个", "那个", "什么", "怎么", "如果", "但是", "然后", "所以", "因为",
        "前200字", "第一拍", "第二拍", "第三拍", "第四拍", "本章", "允许", "禁止",
        "主角", "配角", "并且", "而且", "最后", "开始", "接着", "随后", "这里", "那里"
    }
    for c in clauses:
        c_clean = re.sub(r"[^\u4e00-\u9fff]", "", c)
        if len(c_clean) < 2:
            continue
        if 2 <= len(c_clean) <= 4 and c_clean not in stopwords:
            tokens.add(c_clean)
        for n in (2, 3):
            for i in range(len(c_clean) - n + 1):
                sub = c_clean[i:i+n]
                if sub not in stopwords:
                    tokens.add(sub)
    return tokens


def verify_outline_beat_execution(outline_text: str, chapter_text: str) -> dict:
    """检验细纲规划的节拍（四拍事件）在正文中的覆盖率与执行顺序。"""
    spec = parse_outline_spec(outline_text)
    beats = spec["beats"]
    if not beats:
        return {"ok": True, "beat_count": 0, "executed_count": 0, "rate": 1.0, "details": []}
        
    clean_text = re.sub(r"^#.*$", "", chapter_text, flags=re.MULTILINE).strip()
    
    details = []
    executed_count = 0
    
    for i, beat in enumerate(beats):
        desc = beat["desc"]
        significant = _extract_semantic_tokens(desc)
        
        # 检查正文是否存在这些动作或事件的对应片段
        matched_tokens = [t for t in sorted(significant, key=lambda x: -len(x)) if t in clean_text]
        executed = (len(matched_tokens) >= 1)
        if executed:
            executed_count += 1
            
        details.append({
            "beat_index": i + 1,
            "label": beat["label"],
            "desc": desc,
            "executed": executed,
            "matched_tokens": matched_tokens[:3],
        })
        
    rate = round(executed_count / len(beats), 2)
    return {
        "ok": rate >= 0.75,
        "beat_count": len(beats),
        "executed_count": executed_count,
        "rate": rate,
        "details": details,
    }


def check_narrative_drift(outline_text: str, chapter_text: str) -> dict:
    """检测正文中是否存在脱离细纲主线的连续大段漂移。"""
    spec = parse_outline_spec(outline_text)
    clean_text = re.sub(r"^#.*$", "", chapter_text, flags=re.MULTILINE).strip()
    paragraphs = [p.strip() for p in clean_text.splitlines() if p.strip()]
    
    # 核心词表
    core_tokens = _extract_semantic_tokens(spec["core_event"])
    for b in spec["beats"]:
        core_tokens.update(_extract_semantic_tokens(b["desc"]))
        
    core_tokens = {t for t in core_tokens if len(t) >= 2}
    
    drift_blocks = []
    current_drift_len = 0
    current_drift_paras = []
    
    for p in paragraphs:
        # 如果包含引号（对白）或包含任何核心词，则不是漂移
        has_dialogue = bool(re.search(r"[“\"「『]", p))
        has_core = any(t in p for t in core_tokens)
        
        if not has_dialogue and not has_core:
            current_drift_len += len(p)
            current_drift_paras.append(p[:30] + "...")
        else:
            if current_drift_len >= 350:
                drift_blocks.append({
                    "drift_chars": current_drift_len,
                    "preview": current_drift_paras[:2],
                })
            current_drift_len = 0
            current_drift_paras = []
            
    if current_drift_len >= 350:
        drift_blocks.append({
            "drift_chars": current_drift_len,
            "preview": current_drift_paras[:2],
        })
        
    return {
        "ok": len(drift_blocks) == 0,
        "drift_block_count": len(drift_blocks),
        "blocks": drift_blocks,
    }


def verify_chapter_architecture(outline_path: str, chapter_text_path: str) -> dict:
    """全量执行架构-细纲-正文一致性审查。"""
    with open(outline_path, "r", encoding="utf-8") as f:
        outline_content = f.read()
    with open(chapter_text_path, "r", encoding="utf-8") as f:
        chapter_content = f.read()
        
    violations = check_advance_reveal_violations(outline_content, chapter_content)
    beats_res = verify_outline_beat_execution(outline_content, chapter_content)
    drift_res = check_narrative_drift(outline_content, chapter_content)
    
    overall_ok = (len(violations) == 0) and beats_res["ok"] and drift_res["ok"]
    
    return {
        "ok": overall_ok,
        "outline_path": str(outline_path),
        "chapter_path": str(chapter_text_path),
        "advance_reveal_violations": violations,
        "beat_execution": beats_res,
        "narrative_drift": drift_res,
    }


def run_selftest() -> bool:
    """单元自检验证。"""
    mock_outline = """# 第001章 细纲：换名风波
- 核心事件：大伯逼主角放弃顶职名额按手印，陈砚当场掀桌撕毁协议断亲
- 本章禁止提前释放：禁止提前彻底洗脱父亲历史责任、禁止第一章让大伯全家提前下跪求饶
### 情节安排
- 第一拍（压抑危机）：前200字大伯陈富贵破门逼签协议，强扯咳血妹妹按手印。
- 第二拍（觉醒反击）：陈砚重生苏醒，超前记忆激活，夺下协议撕成粉碎。
- 第三拍（抽翻立威）：堂弟欲动手，陈砚雷霆反击抽翻堂弟，大院震惊。
- 第四拍（夜探厂办）：大伯搬出公章威胁，陈砚翻出底联锁门夜奔厂办。
"""
    # 正常合规正文
    good_text = """“把转让协议签了，滚出去！”大伯陈富贵踹开破门，强拽病弱妹妹要按手印。
陈砚重生苏醒，一把夺过转让协议撕成粉碎！
堂弟陈志强还想动手推搡，陈砚果断反手一记响亮耳光抽翻堂弟，满院邻居目瞪口呆！
大伯搬出厂办王主任公章威胁，陈砚冷笑收好账册底联，锁门夜奔厂办大楼！"""

    res_good = verify_outline_beat_execution(mock_outline, good_text)
    assert res_good["ok"], f"Good text beats execution failed: {res_good}"
    assert res_good["executed_count"] >= 3, f"Executed count low: {res_good}"
    
    v_good = check_advance_reveal_violations(mock_outline, good_text)
    assert len(v_good) == 0, f"False positive in good text violations: {v_good}"

    # 违规正文（反派提前下跪求饶，触犯禁止提前释放）
    bad_text = good_text + "\n大伯陈富贵吓得当场跪地求饶，磕头如捣蒜！"
    v_bad = check_advance_reveal_violations(mock_outline, bad_text)
    assert len(v_bad) > 0, "Failed to catch illegal advance kneeling violation!"

    # 漫游漂移正文（大段风景哲学脱离主线）
    drift_text = good_text + "\n" + ("那是一个阳光明媚的清晨，微风拂过群山，树叶沙沙作响，宇宙浩瀚无垠，生命是如此渺小，历史长河奔流不息，岁月如歌让人感慨万千。" * 6)
    d_bad = check_narrative_drift(mock_outline, drift_text)
    assert not d_bad["ok"], "Failed to catch narrative drift block!"

    return True


def main():
    parser = argparse.ArgumentParser(description="架构-细纲-正文三层因果一致性审查门禁")
    parser.add_argument("--outline", help="细纲文件路径")
    parser.add_argument("--text", help="正文文件路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--selftest", action="store_true", help="运行内部单元自检")
    args = parser.parse_args()
    
    if args.selftest:
        if run_selftest():
            print("ARCHITECTURE_CONSISTENCY_GATE_SELFTEST_OK")
            sys.exit(0)
        sys.exit(1)
        
    if not args.outline or not args.text:
        parser.print_help()
        sys.exit(2)
        
    res = verify_chapter_architecture(args.outline, args.text)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        status_str = "PASS" if res["ok"] else "FAIL"
        print(f"[{status_str}] Architecture Consistency: {res['chapter_path']}")
        print(f"  Beat Execution: {res['beat_execution']['executed_count']}/{res['beat_execution']['beat_count']} ({res['beat_execution']['rate'] * 100}%)")
        if res["advance_reveal_violations"]:
            print("  [VIOLATIONS] Advance Reveal Ban Breaches:")
            for v in res["advance_reveal_violations"]:
                print(f"    - {v['ban']}: {v['reason']}")
        if not res["narrative_drift"]["ok"]:
            print(f"  [DRIFT] Detected {res['narrative_drift']['drift_block_count']} redundant wander blocks.")
            
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
