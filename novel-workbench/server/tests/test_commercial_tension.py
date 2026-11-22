#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_commercial_tension.py — 七猫商业签约级张力探针与模板套件回归测试

覆盖以下核心断言：
1. commercial_tension_probe 自检（正反样本识别力、开篇前200字冲突、围观震惊、断章悬念）
2. 七猫商业爆款开书模板解析（年代逆袭、都市神豪、玄幻横推的黄金三章节拍与角色档案）
3. autopilot init --template 开书初始化（state.json 记录模板、落盘 _商业策划方案.md）
4. _chapter_check 商业探针集成（mock 与短稿宽容不坏老套件，实稿平淡硬阻断，爆款高能全绿灯）
5. _endgame_scan 全书扫描集成（全书平淡章节汇入 endgame_findings.json 阻断，高能章节全绿灯）
"""
import os
import sys
import json
import shutil
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent.parent
SCRIPTS = WORKBENCH / "scripts"
PLATFORMS_QIMAO = Path(r"F:\小说\platforms\qimao")

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from commercial_tension_probe import (
    analyze_chapter_text,
    selftest as probe_selftest,
)
from inline_runner import _chapter_check, _endgame_scan, write_chapter_findings, _tracking_init


def test_probe_selftest():
    """断言1：探针核心正反样本自检必须 100% 通过。"""
    rc = probe_selftest()
    assert rc == 0, "commercial_tension_probe.selftest 必须返回 0"
    print("  PASS [探针核心正反样本自检通过]")


def test_template_integrity():
    """断言2：七猫开书母版库格式完备性与黄金三章强节拍定义。"""
    tpl_dir = PLATFORMS_QIMAO / "templates"
    assert tpl_dir.is_dir(), f"模板目录 {tpl_dir} 必须存在"
    
    idx_path = tpl_dir / "index.json"
    assert idx_path.is_file(), "index.json 索引文件必须存在"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    assert "templates" in idx and len(idx["templates"]) >= 3, "至少登记 3 个成熟商业爆款模版"

    for tpl_info in idx["templates"]:
        tid = tpl_info["id"]
        tfile = tpl_dir / f"{tid}.json"
        assert tfile.is_file(), f"模板文件 {tfile} 必须存在"
        data = json.loads(tfile.read_text(encoding="utf-8"))
        
        # 必须具备核心商业字段
        assert data.get("id") == tid
        assert data.get("platform") == "qimao"
        assert "recommended_titles" in data
        assert "golden_three_chapters" in data

        beats = data["golden_three_chapters"]
        for ch in ("ch1", "ch2", "ch3"):
            assert ch in beats, f"模版 {tid} 必须包含 {ch} 节拍"
            b = beats[ch]
            assert "title" in b and "cliffhanger" in b, f"模版 {tid} 的 {ch} 必须有标题与断章"

    print("  PASS [七猫爆款模版库解析与节拍完备性验证通过]")


def test_autopilot_init_with_template():
    """断言3：autopilot init --template 开书自动注入商业策划案。"""
    import subprocess
    with tempfile.TemporaryDirectory() as tmp:
        book_dir = Path(tmp) / "test_hit_book"
        cmd = [
            sys.executable,
            str(SCRIPTS / "autopilot.py"),
            "init",
            "--book", str(book_dir),
            "--title", "重回七九测试",
            "--genre", "niandai",
            "--target-words", "20000",
            "--max-chapters", "12",
            "--template", "niandai_regret_counterattack"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        assert res.returncode == 0, f"autopilot init 失败: {res.stderr}"

        # 检查 state.json 字段
        state_file = book_dir / "_pipeline" / "state.json"
        assert state_file.is_file(), "state.json 必须生成"
        st = json.loads(state_file.read_text(encoding="utf-8"))
        assert st.get("template") == "niandai_regret_counterattack"

        # 检查商业策划方案.md
        plan_md = book_dir / "大纲" / "_商业策划方案.md"
        assert plan_md.is_file(), "大纲/_商业策划方案.md 必须生成"
        plan_content = plan_md.read_text(encoding="utf-8")
        assert "商业策划方案" in plan_content
        assert "黄金三章生死节拍" in plan_content
        assert "ch1" in plan_content and "ch2" in plan_content and "ch3" in plan_content

    print("  PASS [autopilot init --template 商业策划注入验证通过]")


def test_chapter_check_commercial_gate():
    """断言4：_chapter_check 对真实平淡稿阻断，对高能商业稿放行。"""
    with tempfile.TemporaryDirectory() as tmp:
        book = Path(tmp) / "book"
        book.mkdir()
        (book / "_pipeline").mkdir()
        (book / "正文").mkdir()
        (book / "大纲").mkdir()
        (book / "追踪").mkdir()
        _tracking_init(book, {"book": "测试书", "genre": "niandai"})

        (book / "大纲" / "卷纲_第一卷.md").write_text("# 第一卷\n- 主线：撕毁协议，掀翻假账\n", encoding="utf-8")
        (book / "大纲" / "细纲_第001章.md").write_text(
            "# 细纲_第001章\n\n"
            "- 核心事件：主角在厂办当众撕毁协议，爆发冲突。\n"
            "- 字数目标：2200字\n"
            "- 字数口径：visible_chars_v1\n"
            "- 单元ID/位置：U1-3 / 全章\n"
            "- 目标情绪：憋屈到爆发\n"
            "- 主角目标/关键选择：拒绝妥协，当场撕毁协议\n"
            "- 关键阻碍/冲突方：大伯陈富贵、堂弟陈志强\n"
            "- 进展/代价：撕毁协议断绝关系，亮出全厂假账第一条死穴\n"
            "- 出场人物：陈砚、陈富贵、陈志强\n"
            "- 场景/时间：轧钢厂厂办门前 / 1979年秋 清晨\n"
            "- 伏笔/钩子：门外警笛声与砸门声\n",
            encoding="utf-8"
        )

        # 1. 真实平淡稿（复现《顶职》被退稿特征：开头晨曦微露，全篇无围观震惊反馈，章末回房睡觉）
        flat_paragraphs = [
            f"第{i}车间的机器轰鸣着，工人们都在各自的工位上忙碌着，陈砚手里拿着图纸，挨个工位核对设备编号与出厂日期，车间主任刘海中背着手在过道上走来走去，偶尔停下来指点几句，气氛平静而单调。"
            for i in range(1, 25)
        ]
        flat_body = (
            "# 第001章 报到\n\n"
            "晨曦微露，清晨的阳光透过树梢洒在红星轧钢厂的红砖围墙上。\n"
            "陈砚背着旧书包慢慢走在厂区的主道上。轧钢厂始建于上世纪五十年代，如今已经有上万名在职职工。\n\n"
            + "\n\n".join(flat_paragraphs)
            + "\n\n今天的工作就这样按部就班地结束了，陈砚回到宿舍洗漱完毕，静静躺在床上沉沉睡去。\n"
        )
        ch1_file = book / "正文" / "第001章_报到.md"
        ch1_file.write_text(flat_body, encoding="utf-8")

        # 强制启用商业探针阻断（模拟真实生产模式）
        os.environ["FORCE_COMMERCIAL_BLOCKING"] = "1"
        try:
            rc, out = _chapter_check(book, 1, {"genre": "niandai", "template": "niandai_regret_counterattack"})
            assert rc == 0, f"check 正常返回 0（交由 N14）: {out}"

            # 读取生成的 findings_C1.json
            findings_path = book / "_pipeline" / "findings_C1.json"
            assert findings_path.is_file()
            findings = json.loads(findings_path.read_text(encoding="utf-8"))

            # 必须检出商业阻断项
            blocking_items = [it for it in findings.get("items", []) if it.get("blocking")]
            comm_blocks = [it for it in blocking_items if it.get("id") == "COMMERCIAL_TENSION"]
            assert len(comm_blocks) >= 2, f"平淡稿必须命中商业阻断项，实际: {comm_blocks}"
            notes = " ".join(it.get("note", "") for it in comm_blocks)
            assert "缺少配角震惊反馈" in notes or "开篇泄气" in notes
        finally:
            os.environ.pop("FORCE_COMMERCIAL_BLOCKING", None)

        # 2. 商业高能爆款稿（前200字撕破脸逼签字，第1200字当众打脸围观倒吸凉气，章末突发杀机断章）
        hit_paragraphs = [
            f"陈砚直接翻开第{i}本账册，当众念出上面的假批条：“一九七八年三月，第{i}车间虚报轴承损耗八百套，折合金额三千六百元，经手人正是你王主任！”"
            for i in range(1, 20)
        ]
        hit_body = (
            "# 第001章 抢我顶职，反手掀翻全厂假账\n\n"
            "大伯将转让铁饭碗的协议狠狠拍在桌上，厉声逼迫陈砚按手印：“今天这个字你必须签，否则老子把你扫地出门！”\n"
            "堂弟陈志强在旁边冷笑连连，手里得意地晃着录取介绍信。\n"
            "陈砚冷冷看着这对贪婪无耻的亲戚，胸中三年的怒火轰然爆发！\n"
            "他一步上前抓起协议，在所有人惊骇的目光中，当场撕成粉碎！\n"
            "“想霸占我爸拿命换来的工位？做你们的春秋大梦！”\n\n"
            + "\n\n".join(hit_paragraphs)
            + "\n\n全场死寂！在场的所有领导与工友齐齐倒吸一口凉气，目瞪口呆！"
            + "\n大伯脸色煞白，浑身发抖，双腿一软险些瘫倒在地！"
            + "\n就在这时，门外猛地传来一声刺耳的警笛声与急促的砸门声！到底是谁来了？！\n"
        )
        ch1_file.write_text(hit_body, encoding="utf-8")

        os.environ["FORCE_COMMERCIAL_BLOCKING"] = "1"
        try:
            rc, out = _chapter_check(book, 1, {"genre": "niandai", "template": "niandai_regret_counterattack"})
            assert rc == 0
            findings = json.loads(findings_path.read_text(encoding="utf-8"))
            comm_blocks = [it for it in findings.get("items", []) if it.get("id") == "COMMERCIAL_TENSION" and it.get("blocking")]
            assert len(comm_blocks) == 0, f"商业爆款高能稿商业探针阻断必须为 0，实际: {comm_blocks}"
        finally:
            os.environ.pop("FORCE_COMMERCIAL_BLOCKING", None)

    print("  PASS [_chapter_check 商业探针精准阻断平淡稿、放行爆款稿]")


def test_endgame_scan_commercial_gate():
    """断言5：_endgame_scan (N19) 全书终审精准捕捉平淡章节并汇入 endgame_findings。"""
    with tempfile.TemporaryDirectory() as tmp:
        book = Path(tmp) / "book"
        book.mkdir()
        (book / "_pipeline").mkdir()
        (book / "正文").mkdir()
        (book / "追踪").mkdir()

        # 写入一本平淡稿
        flat_paragraphs = [
            f"第{i}车间的机器运转平稳，工人们各自忙碌着，陈砚站在一旁认真观察记录各项数据。"
            for i in range(1, 40)
        ]
        flat_body = (
            "# 第001章 报到\n\n"
            "晨曦微露，春暖花开，那是一个风和日丽的早晨。\n\n"
            + "\n\n".join(flat_paragraphs)
            + "\n\n天色渐晚，陈砚回宿舍洗漱完毕睡觉了。\n"
        )
        (book / "正文" / "第001章_报到.md").write_text(flat_body, encoding="utf-8")

        os.environ["FORCE_COMMERCIAL_BLOCKING"] = "1"
        try:
            rc, msg = _endgame_scan(book, {"genre": "niandai", "template": "niandai_regret_counterattack"})
            assert rc == 0, f"endgame scan 必须正常退出: {msg}"
            
            endgame_file = book / "_pipeline" / "endgame_findings.json"
            assert endgame_file.is_file()
            data = json.loads(endgame_file.read_text(encoding="utf-8"))
            items = data.get("items") or []
            comm_items = [it for it in items if it.get("tool") == "commercial-tension"]
            assert len(comm_items) > 0, f"终审扫描必须捕捉到平淡章节的商业阻断，实际: {items}"
            assert any("洗漱睡觉" in it.get("message", "") or "开篇泄气" in it.get("message", "") for it in comm_items)
        finally:
            os.environ.pop("FORCE_COMMERCIAL_BLOCKING", None)

    print("  PASS [_endgame_scan 终审扫描平淡章节成功汇入 endgame_findings]")


def main():
    print("== 七猫商业签约级张力探针与模板套件回归测试 ==")
    test_probe_selftest()
    test_template_integrity()
    test_autopilot_init_with_template()
    test_chapter_check_commercial_gate()
    test_endgame_scan_commercial_gate()
    print("\nALL COMMERCIAL TENSION TESTS PASSED!")


if __name__ == "__main__":
    main()
