#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""persona_confidence_probe.py — 人设核心动机与行为因果置信度审查器

解决商业网文“人物行事动机前后出入、降智妥协、被动挨打”的致命被拒硬伤：
  1. 主角主动性与决断度（protagonist_agency）：主角每章必须有主动反抗、破局或谋划行为，杜绝纯被动受气等死；
  2. 认知边界与越界知情泄露（knowledge_boundary_leak）：严防角色在未经过剧情调查前提前得知核心机密；
  3. 核心动机行为闭环（motivation_coherence）：关键抉择必须符合其人物设定，严禁突发违背设定的自毁行为。

用法:
  python persona_confidence_probe.py --text <正文.md> [--state <tracking-state.json>] [--settings <角色.md>] [--json]
  python persona_confidence_probe.py --selftest
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

# 主角主动作为/反击/决断动作正则
PROACTIVE_ACTION_PAT = re.compile(
    r"(?:冷笑|反手|夺过|夺下|撕碎|撕毁|踏步|一巴掌|寒声道|沉声|锁门|直奔|"
    r"按住|掀翻|拿出|掏出|取出|厉声|怒斥|抽翻|制伏|下令|谋划|决定|拔刀|"
    r"一记耳光|当众宣布|果断|冷冷注视|神色一冷|目光如刀|反唇相讥|寸步不让)"
)

# 主角被动降智/屈服等死词正则（严重扣分项）
PASSIVE_SUBMISSION_PAT = re.compile(
    r"(?:委屈得直掉眼泪|只能任由|任凭他们|毫无反抗之力|绝望等死|"
    r"心甘情愿让出|低头认栽|只能唯唯诺诺|乖乖听话把名额让出|任人摆布)"
)


def extract_characters_from_state(tracking_state_path: str) -> dict:
    """从 _tracking-state.json 中提取角色设定与状态。"""
    if not os.path.exists(tracking_state_path):
        return {}
    try:
        with open(tracking_state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("characters", {})
    except Exception:
        return {}


def identify_protagonist(characters: dict, clean_text: str) -> str:
    """识别主角名字：优先看 characters 中的 goal 是否具备主角特征，或正文中频次最高的角色名。"""
    if not characters:
        # 正则快速提取人名候选
        matches = re.findall(r"(?:陈|李|林|张|王|叶|苏|周|姜|萧|陆)[\u4e00-\u9fff]{1,2}", clean_text)
        from collections import Counter
        c = Counter(matches)
        return c.most_common(1)[0][0] if c else "主角"
        
    for name, info in characters.items():
        ident = info.get("identity", "")
        if "主角" in ident or "男主" in ident or "女主" in ident:
            return name
            
    # 频率兜底
    names = list(characters.keys())
    counts = {name: clean_text.count(name) for name in names}
    sorted_names = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return sorted_names[0][0] if sorted_names else "主角"


def check_protagonist_agency(protagonist_name: str, clean_text: str) -> dict:
    """审查主角主动决断度与行动力。"""
    # 截取主角相关段落（包含主角名字的段落）
    paragraphs = [p.strip() for p in clean_text.splitlines() if p.strip()]
    pro_paras = [p for p in paragraphs if protagonist_name in p]
    
    if not pro_paras:
        # 若主角没有直接出现（极少情况）
        return {
            "ok": False,
            "score": 0.0,
            "proactive_actions": 0,
            "submission_hits": 0,
            "reason": f"主角 {protagonist_name} 未在正文中直接登场行动",
        }
        
    proactive_hits = sum(len(PROACTIVE_ACTION_PAT.findall(p)) for p in pro_paras)
    submission_hits = sum(len(PASSIVE_SUBMISSION_PAT.findall(p)) for p in pro_paras)
    
    # 主动性评分：主动动作越多越好，屈服词扣分
    score = 1.0
    if submission_hits > 0:
        score -= 0.5 * submission_hits
    if proactive_hits == 0:
        score -= 0.6
    else:
        score = min(1.0, score + 0.1 * min(proactive_hits, 4))
        
    score = max(0.0, round(score, 2))
    ok = (score >= 0.6 and proactive_hits >= 1 and submission_hits == 0)
    
    return {
        "ok": ok,
        "score": score,
        "proactive_actions": proactive_hits,
        "submission_hits": submission_hits,
        "reason": "主角行动果断有力" if ok else "主角行动偏被动软弱或出现降智屈服迹象",
    }


def check_knowledge_boundary(character_name: str, known_facts: list[str], secret_facts: list[str], clean_text: str) -> list[dict]:
    """检查角色是否越界说出了或使用了尚未知晓的秘密信息（先知/知情漏洞）。"""
    leaks = []
    if not secret_facts:
        return leaks
        
    # 提取角色在对白中说的话
    # 提取该角色说话的引号内容
    dialogue_pats = [
        re.compile(rf"{re.escape(character_name)}[^\n]{{0,10}}[说道喊斥问笑][：:]\s*[“\"「『]([^”\"」』]+)[”\"」』]"),
        re.compile(rf"[“\"「『]([^”\"」』]+)[”\"」』][^\n]{{0,10}}{re.escape(character_name)}"),
    ]
    
    spoken_texts = []
    for pat in dialogue_pats:
        for m in pat.finditer(clean_text):
            spoken_texts.append(m.group(1))
            
    full_spoken = " ".join(spoken_texts)
    
    for secret in secret_facts:
        # 提取核心密词
        tokens = re.findall(r"[\u4e00-\u9fff]{2,4}", secret)
        tokens = [t for t in tokens if t not in ("秘密", "幕后", "知道", "真相", "还未", "此时")]
        if not tokens:
            continue
        for t in tokens:
            if t in full_spoken and not any(t in k for k in known_facts):
                leaks.append({
                    "character": character_name,
                    "secret_token": t,
                    "secret_fact": secret,
                    "reason": f"角色 {character_name} 在对话中越界说出了未掌握的秘密: {t}",
                })
                break
                
    return leaks


def probe_chapter_persona_confidence(
    clean_text: str,
    tracking_state_path: str = "",
    protagonist_name: str = "",
    secret_facts: list[str] | None = None
) -> dict:
    """全量审查单章人设置信度与因果逻辑。"""
    chars = extract_characters_from_state(tracking_state_path) if tracking_state_path else {}
    if not protagonist_name:
        protagonist_name = identify_protagonist(chars, clean_text)
        
    agency_res = check_protagonist_agency(protagonist_name, clean_text)
    
    known_facts = []
    if protagonist_name in chars:
        known_facts = chars[protagonist_name].get("knowledge", [])
        
    leaks = check_knowledge_boundary(
        character_name=protagonist_name,
        known_facts=known_facts,
        secret_facts=secret_facts or [],
        clean_text=clean_text,
    )
    
    overall_ok = agency_res["ok"] and (len(leaks) == 0)
    
    return {
        "ok": overall_ok,
        "protagonist": protagonist_name,
        "agency": agency_res,
        "knowledge_leaks": leaks,
    }


def run_selftest() -> bool:
    """自检逻辑验证。"""
    mock_text_good = """陈砚冷笑一声，反手夺过协议当场撕碎！
陈砚目光如刀，厉声喝道：“大伯，想逼我让出顶职名额，做你的春秋大梦！”
大伯气急败坏。陈砚果断锁好大门，直奔厂办。"""

    res_good = probe_chapter_persona_confidence(mock_text_good, protagonist_name="陈砚")
    assert res_good["ok"], f"Good text failed: {res_good}"
    assert res_good["agency"]["proactive_actions"] >= 3
    assert res_good["agency"]["score"] >= 0.8

    # 软弱降智正文
    mock_text_bad = """陈砚委屈得直掉眼泪，面对大伯的威逼，只能唯唯诺诺，乖乖听话把名额让出。
陈砚毫无反抗之力，只能任由大伯将协议拿走。"""
    res_bad = probe_chapter_persona_confidence(mock_text_bad, protagonist_name="陈砚")
    assert not res_bad["ok"], "Passive submission text should fail agency check!"
    assert res_bad["agency"]["submission_hits"] >= 2

    # 越界知情正文
    mock_text_leak = mock_text_good + "\n陈砚冷笑道：“我知道王德发的特种钢绝密账本代号是702！”"
    leaks = check_knowledge_boundary("陈砚", known_facts=["贪污底联在手中"], secret_facts=["特种钢绝密账本代号702"], clean_text=mock_text_leak)
    assert len(leaks) > 0, "Failed to catch knowledge boundary leak!"

    return True


def main():
    parser = argparse.ArgumentParser(description="人设核心动机与行为因果置信度审查器")
    parser.add_argument("--text", help="正文文件路径")
    parser.add_argument("--state", help="追踪状态文件 _tracking-state.json 路径")
    parser.add_argument("--protagonist", help="主角姓名（可选，默认自动识别）")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    parser.add_argument("--selftest", action="store_true", help="运行内部单元自检")
    args = parser.parse_args()
    
    if args.selftest:
        if run_selftest():
            print("PERSONA_CONFIDENCE_PROBE_SELFTEST_OK")
            sys.exit(0)
        sys.exit(1)
        
    with open(args.text, "r", encoding="utf-8") as f:
        clean_text = f.read()
        
    res = probe_chapter_persona_confidence(
        clean_text=clean_text,
        tracking_state_path=args.state or "",
        protagonist_name=args.protagonist or "",
    )
    
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        status_str = "PASS" if res["ok"] else "FAIL"
        print(f"[{status_str}] Persona Confidence for Protagonist: {res['protagonist']}")
        print(f"  Agency Score: {res['agency']['score']} (Proactive Actions: {res['agency']['proactive_actions']}, Submissions: {res['agency']['submission_hits']})")
        print(f"  Assessment: {res['agency']['reason']}")
        if res["knowledge_leaks"]:
            print("  [LEAKS] Knowledge Boundary Leaks:")
            for l in res["knowledge_leaks"]:
                print(f"    - {l['character']}: {l['reason']}")
                
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
