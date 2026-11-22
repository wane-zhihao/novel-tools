#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""commercial_tension_probe.py — 七猫商业张力与情绪兑现探针

基于七猫官方编辑桑葚《什么样的稿子会被一眼拒》与多位五星作家实战复盘，
机械量化网文商业过签的四大核心硬指标：
  1. 开篇危机紧迫度（opening_urgency）：第 1 章前 200 字必须直入极端矛盾，前 500 字必须有不可调和的利益危机。
  2. 围观震惊与对手失态（crowd_reaction）：前 3 章必须有配角倒吸凉气、目瞪口呆或对手失态的即时反馈（围观震惊流）。
  3. 章末断章悬念（cliffhanger_tension）：每章末尾必须卡在矛盾高潮或突发变故，严禁平淡泄气收尾（断章留存）。
  4. 利益与痛感具体化（tangible_stakes）：危机与打脸收益必须有具体的数字、物品或名分，禁止抽象大话。

用法:
  python commercial_tension_probe.py <正文文件或目录> [--json] [--chapter N]
  python commercial_tension_probe.py --selftest
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

# ─── 模式库 ──────────────────────────────────────────────────────────────────

# 极端危机与冲突信号（第 1 章前 200-500 字）
OPENING_CONFLICT_PAT = re.compile(
    r"撕碎|撕破|按手印|签字|抢走|逼迫|逐出|退婚|顶替|冒名|撤销|除名|"
    r"换名|跪下|扇|耳光|滚出去|陷害|克扣|打死|绝境|剥离|抽骨|废掉|"
    r"假账|亏空|死账|夺走|霸占|断绝关系|扫地出门|分家|毒打|砸烂|抄家"
)

# 泄气平淡式开篇信号（扣分项：环境、天气、历史漫长铺垫）
OPENING_FLAT_SETUP_PAT = re.compile(
    r"^\s*(晨曦微露|阳光透过|那是一个|清晨的阳光|微风拂过|春暖花开|群山连绵|"
    r"在浩瀚的大陆|故事要从|众所周知|话说当年|历史悠久|这是一个关于)"
)

# 对手失态与负面情绪破防信号
OPPONENT_BREAKDOWN_PAT = re.compile(
    r"脸色(?:煞白|惨白|铁青|骤变|铁黑|僵住|挂不住|难看|阴沉)|"
    r"双腿一软|面如死灰|冷汗涔涔|额头冒汗|瘫倒在地|浑身发抖|气急败坏|"
    r"难以置信|不敢置信|目眦欲裂|瞠目结舌|神色大变|吓得倒退|声音发颤|"
    r"如遭雷击|心头巨震|脑海嗡的一声|当场僵在原地|说不出话来|哑口无言|惊恐"
)

# 围观群众震惊反馈信号（围观震惊流）
CROWD_SHOCK_PAT = re.compile(
    r"倒吸一?口凉气|全场死寂|鸦雀无声|一片哗然|所有人惊呆|满场惊骇|"
    r"目瞪口呆|窃窃私语|炸开了锅|纷纷侧目|屏住呼吸|如见鬼魅|倒吸冷气|"
    r"满脸骇然|震动全场|无不骇然|惊呼出声|所有人都在看着|投去惊愕的目光|"
    r"惊骇的目光|满脸惊骇|惊愕|倒吸一口气|不敢相信自己的眼睛|倒吸凉气"
)

# 章末断章悬念信号（最后 250 字）
CLIFFHANGER_PAT = re.compile(
    r"[？！\?!]{1,2}|"
    r"就在这时|突然|猛然|骤然|赫然|门外传来|门外猛地|一道冰冷的声音|"
    r"脚步声骤停|轰然|谁|怎么可能|难道是|到底是谁|嘴角勾起一抹|"
    r"冷笑一声|杀机毕露|推门而入|快步冲了进来|脸色剧变|急促的敲门声|"
    r"一把抓住|一把按住|停在半空|悬在头顶|拔出|当啷一声"
)

# 章末泄气平淡收尾信号（严重扣分项：无悬念、日常化洗漱睡觉平稳收场）
FLAT_DROPOFF_PAT = re.compile(
    r"(?:回宿舍|回房|上床|倒头)?(?:睡了|睡觉|入睡|睡去|合眼|沉沉睡去)|"
    r"一切归于平静|夜色渐深|各自散去|天色已晚|洗漱完毕|静静看着天花板|"
    r"今天先这样|事情总算办完|舒了一口气|明天再说"
)

# 具体利益/财物/凭证信号
TANGIBLE_REWARD_PAT = re.compile(
    r"\d+\s*(?:元|块|万|两|文|斤|尺|亩|株|阶|张|条|本|间|座)|"
    r"大团结|存折|支票|黑卡|房本|地契|借条|印章|公章|红头文件|"
    r"任命书|工牌|介绍信|调令|灵石|神丹|仙草|法宝|至尊骨|重瞳"
)


def analyze_chapter_text(text: str, chapter_num: int = 1) -> dict:
    """分析单章正文的商业张力、情绪反差与断章留存。"""
    clean = re.sub(r"^#.*$", "", text, flags=re.MULTILINE).strip()
    total_chars = len(clean.replace(" ", "").replace("\n", ""))
    
    # 1. 开篇危机检测（主要针对第 1 章）
    opening_500 = clean[:500]
    opening_200 = clean[:200]
    
    conflict_pos = -1
    conflict_match = OPENING_CONFLICT_PAT.search(opening_500)
    if conflict_match:
        conflict_pos = conflict_match.start()
        
    flat_setup_match = OPENING_FLAT_SETUP_PAT.search(opening_200)
    
    opening_urgency = {
        "conflict_pos": conflict_pos,
        "conflict_in_200": conflict_pos != -1 and conflict_pos <= 200,
        "conflict_in_500": conflict_pos != -1 and conflict_pos <= 500,
        "flat_setup_detected": bool(flat_setup_match),
        "score": 100 if (conflict_pos != -1 and conflict_pos <= 200 and not flat_setup_match) else (
            70 if (conflict_pos != -1 and conflict_pos <= 500) else 30
        )
    }

    # 2. 围观群众与对手失态检测
    opponent_hits = [m.group(0) for m in OPPONENT_BREAKDOWN_PAT.finditer(clean)]
    crowd_hits = [m.group(0) for m in CROWD_SHOCK_PAT.finditer(clean)]
    reaction_total = len(opponent_hits) + len(crowd_hits)
    
    crowd_reactions = {
        "opponent_count": len(opponent_hits),
        "crowd_count": len(crowd_hits),
        "total_reactions": reaction_total,
        "sample_hits": (opponent_hits + crowd_hits)[:5],
        # 前 3 章打脸必须有对手破防或围观震惊
        "ok": reaction_total >= (1 if chapter_num <= 3 else 0)
    }

    # 3. 章末断章悬念检测（取末尾 250 字窗口）
    tail_window = clean[-250:] if len(clean) >= 250 else clean
    cliffhanger_hits = [m.group(0) for m in CLIFFHANGER_PAT.finditer(tail_window)]
    flat_dropoff_hit = FLAT_DROPOFF_PAT.search(tail_window)
    
    has_cliffhanger = len(cliffhanger_hits) > 0 and not flat_dropoff_hit
    cliffhanger = {
        "has_cliffhanger": has_cliffhanger,
        "flat_dropoff": bool(flat_dropoff_hit),
        "cliffhanger_hits": cliffhanger_hits[:3],
        "tail_snippet": tail_window[-80:].strip()
    }

    # 4. 具象利益与财物奖励
    reward_hits = [m.group(0) for m in TANGIBLE_REWARD_PAT.finditer(clean)]
    tangible = {
        "reward_count": len(reward_hits),
        "sample_rewards": reward_hits[:5]
    }

    # 综合评估评级
    blocking_reasons = []
    if chapter_num == 1:
        if not opening_urgency["conflict_in_500"]:
            blocking_reasons.append("第1章前500字无直接危机/矛盾冲突（七猫开篇大忌：平铺叙事）")
        if opening_urgency["flat_setup_detected"]:
            blocking_reasons.append("第1章开头存在大段天气/风景/背景说明套话（开篇泄气）")
            
    if chapter_num <= 3 and reaction_total == 0:
        blocking_reasons.append(f"第{chapter_num}章缺少配角震惊反馈或对手破防描写（围观震惊流缺失，爽感不落地）")
        
    if cliffhanger["flat_dropoff"]:
        blocking_reasons.append(f"第{chapter_num}章章末出现日常化洗漱睡觉等泄气收尾（读者流失毒点：章末无悬念）")

    status = "pass" if not blocking_reasons else "block"

    return {
        "chapter": chapter_num,
        "words": total_chars,
        "status": status,
        "opening_urgency": opening_urgency,
        "crowd_reactions": crowd_reactions,
        "cliffhanger": cliffhanger,
        "tangible_stakes": tangible,
        "blocking_reasons": blocking_reasons
    }


def analyze_book_dir(book_dir: Path) -> dict:
    """批量扫描某本书的全部正文章节。"""
    body_dir = book_dir / "正文" if (book_dir / "正文").is_dir() else book_dir
    files = sorted(body_dir.glob("第*.md"))
    if not files:
        files = sorted(body_dir.glob("*.md"))
        
    results = []
    overall_blocking = []
    
    for f in files:
        m = re.search(r"第(\d{1,4})章", f.name)
        ch_num = int(m.group(1)) if m else 1
        # 跳过备份稿
        if "_原稿_" in f.name or f.name.endswith("~"):
            continue
        try:
            content = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = f.read_text(encoding="gb18030", errors="replace")
            
        res = analyze_chapter_text(content, ch_num)
        res["file"] = f.name
        results.append(res)
        if res["blocking_reasons"]:
            for r in res["blocking_reasons"]:
                overall_blocking.append({"chapter": ch_num, "file": f.name, "reason": r})
                
    return {
        "status": "pass" if not overall_blocking else "block",
        "total_chapters": len(results),
        "blocking_count": len(overall_blocking),
        "blocking": overall_blocking,
        "chapters": results
    }


def selftest() -> int:
    """自检函数：验证正反样本的识别能力。"""
    # 坏样本：开局描写天气，无矛盾，末尾睡觉收工
    bad_sample = """# 第001章 阳光正好

晨曦微露，春暖花开，那是一个风和日丽的早晨。
陈砚走在青石板小路上，呼吸着新鲜的空气。
红星轧钢厂有着上万名工人，始建于上世纪五十年代。
陈砚走进厂区，领了表格，坐在椅子上开始登记。
一天的时间很快过去了，陈砚回到宿舍洗漱完毕，躺在床上沉沉睡去。
"""
    bad_res = analyze_chapter_text(bad_sample, 1)
    assert bad_res["status"] == "block", f"坏样本必须被阻断，实际结果: {bad_res}"
    assert bad_res["opening_urgency"]["flat_setup_detected"] is True
    assert bad_res["cliffhanger"]["flat_dropoff"] is True
    assert len(bad_res["blocking_reasons"]) >= 2

    # 好样本：前200字撕破脸逼签字，第2章当众反杀围观倒吸凉气，章末留下致命断章
    good_sample_1 = """# 第001章 撕毁协议

大伯将那份转让铁饭碗的协议狠狠拍在桌上，指着陈砚的鼻子厉声逼迫：“今天这个手印你按也得按，不按也得按！否则老子砸烂你的铺盖，把你和那个病秧子妹妹一起扫地出门！”
堂弟陈志强在一旁冷笑不止，手里玩弄着原本属于陈砚的录取介绍信。
陈砚冷冷看着这一对贪得无厌的恶亲戚，胸膛中积压了三年的怒火轰然爆发！
他抓起桌上的协议，在所有人惊骇的目光中，当场撕成粉碎！
【叮！感应到宿主强烈反抗意志，超脑审计系统觉醒……】
陈砚冷笑一声，抓起墙角生锈的铁尺，猛地一脚踹开院门，直奔厂长办公室！
门外夜风呼啸，一道冰冷刺骨的杀机在黑夜中骤然锁定了他……
"""
    good_res_1 = analyze_chapter_text(good_sample_1, 1)
    assert good_res_1["status"] == "pass", f"好样本1必须通过，实际: {good_res_1['blocking_reasons']}"
    assert good_res_1["opening_urgency"]["conflict_in_200"] is True
    assert good_res_1["cliffhanger"]["has_cliffhanger"] is True

    good_sample_3 = """# 第003章 当众翻盘

全厂几百名工友里三层外三层围在厂办门口。
大伯瘫倒在地，王主任脸色铁青，浑身发抖，指着账本说不出话来！
陈砚三分钟心算直接报出三万两千元假账亏空，铁证如山！
全场死寂！
周围的工人们齐齐倒吸一口凉气，满脸难以置信！
厂长当场拍板，补发安家费五百元！
陈砚接过红头任命书，正要离开，门外猛地传来一声凄厉的尖叫：“杀人啦！保卫科出事了！”
"""
    good_res_3 = analyze_chapter_text(good_sample_3, 3)
    assert good_res_3["status"] == "pass", f"好样本3必须通过，实际: {good_res_3['blocking_reasons']}"
    assert good_res_3["crowd_reactions"]["ok"] is True
    assert good_res_3["cliffhanger"]["has_cliffhanger"] is True

    print("commercial_tension_probe: SELFTEST_OK")
    return 0


def main():
    parser = argparse.ArgumentParser(description="七猫商业张力探针")
    parser.add_argument("target", nargs="?", help="正文文件或目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--chapter", type=int, default=1, help="章节序号")
    parser.add_argument("--selftest", action="store_true", help="自检")
    args = parser.parse_args()

    if args.selftest:
        sys.exit(selftest())

    if not args.target:
        parser.print_help()
        sys.exit(2)

    target_path = Path(args.target).resolve()
    if not target_path.exists():
        print(json.dumps({"error": f"Path not found: {target_path}"}))
        sys.exit(2)

    if target_path.is_file():
        content = target_path.read_text(encoding="utf-8", errors="replace")
        report = analyze_chapter_text(content, args.chapter)
    else:
        report = analyze_book_dir(target_path)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Status: {report['status']}")
        if report.get("blocking_reasons"):
            for b in report["blocking_reasons"]:
                print(f"  BLOCK: {b}")
        if report.get("blocking"):
            for b in report["blocking"]:
                print(f"  BLOCK [Ch{b['chapter']}]: {b['reason']}")

    sys.exit(0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
