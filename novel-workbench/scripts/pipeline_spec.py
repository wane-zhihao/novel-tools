#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline_spec.py — 节点表（流程唯一真相）

节点类型：
  script  → autopilot.py 内联 subprocess 执行，对 LLM 透明
  llm     → 需 subagent 创作，落盘后调 done
  verify  → 需 subagent 红队审查，落盘 judgments JSON 后调 done

章节循环用复合键 C{n}.Nxx，避免扁平字典写第 2 章时覆盖第 1 章状态。
"""
from __future__ import annotations

from pathlib import Path

# ─── 全局节点 N01–N09d ────────────────────────────────────────────────────────

GLOBAL_NODES: dict[str, dict] = {
    "N01": {
        "id": "N01", "kind": "script", "name": "初始化",
        "evidence": "_pipeline/state.json", "min_size": 100,
    },
    "N02": {
        "id": "N02", "kind": "script", "name": "文风锚定",
        "evidence": "_pipeline/state.json", "min_size": 100,
        "note": "qimao/harness/style-anchor.json 已 calibrated（42 章正例）",
    },
    "N03": {
        "id": "N03", "kind": "llm", "role": "scout", "name": "选题报告",
        "evidence": "大纲/选题报告.md", "min_size": 600,
        "prompt": (
            "你是七猫系选题参谋。请读取：\n"
            "  {QIMAO}/01_投稿方案.md   （站点门槛 / 收稿范围 / 唯一一次评估机会）\n"
            "  {QIMAO}/02_写作方案.md   （平台调性 / 开篇四关）\n"
            "  {QIMAO}/harness/genre-baselines.json  （爽文/年代两组的实测基准）\n"
            "产出 5 个题材候选，按客观标准打分：平台收稿匹配度 / 榜单热度 / "
            "创新配比(90%套路+10%创新) / 是否避开已写烂的梗 / 与年代题材基准兼容性。\n"
            "写入 {BOOK}/大纲/选题报告.md，必须含「结论」一节，明确给出选定题材与理由。\n"
            "回执 ≤100 字。"
        ),
    },
    "N04": {
        "id": "N04", "kind": "verify", "role": "judge", "name": "选题闸门",
        "evidence": "_pipeline/judgments_N04.json", "min_size": 200,
        "prompt": (
            "你是七猫签约评审（红队模式）。只读 {BOOK}/大纲/选题报告.md，"
            "对照 {QIMAO}/01_投稿方案.md 的收稿范围与「每部作品只有一次评估机会」的约束。\n"
            "先输出「这个选题最可能被七猫编辑拒掉的 3 个理由」，再做裁决。\n"
            "写入 {BOOK}/_pipeline/judgments_N04.json：\n"
            '  {"verdict":"pass|block","summary":"...","items":[{"criterion":"...","quote":"报告原文连续12-30字","source":"大纲/选题报告.md","verdict":"pass|fail"}]}\n'
            "quote 必须是报告中连续原文，严禁省略号跳跃。回执 ≤100 字。"
        ),
    },
    "N05": {
        "id": "N05", "kind": "llm", "role": "writer", "name": "设定",
        "evidence": "设定/题材定位.md", "min_size": 300,
        "prompt": (
            "读取 {BOOK}/大纲/选题报告.md 的结论一节，产出 5 个设定文件：\n"
            "  设定/题材定位.md  设定/文风.md  设定/角色.md  设定/世界观.md  设定/势力.md\n"
            "文风.md 必须引用 {QIMAO}/harness/genre-baselines.json 的年代组基准"
            "（句长中位 15-27、段/千字 33.1-54.1）。\n"
            "角色.md 每个角色需有：名字 / 一句话动机 / 核心困境 / 与主角的关系。\n"
            "回执 ≤100 字。"
        ),
    },
    "N06": {
        "id": "N06", "kind": "verify", "role": "judge", "name": "设定校验",
        "evidence": "_pipeline/judgments_N06.json", "min_size": 200,
        "prompt": (
            "只读 {BOOK}/设定/ 下 5 个文件。核查：五个文件是否齐全、角色是否有明确动机、"
            "世界观是否过度庞杂（七猫清单：设定太庞杂是硬伤）、文风是否与年代基准兼容。\n"
            "先输出 3 个最可能被拒的理由再裁决。写入 {BOOK}/_pipeline/judgments_N06.json，"
            "格式同 N04，source 填具体文件名。回执 ≤100 字。"
        ),
    },
    "N07": {
        "id": "N07", "kind": "llm", "role": "writer", "name": "卷纲",
        "evidence": "大纲/卷纲_第一卷.md", "min_size": 800,
        "prompt": (
            "读取 {BOOK}/设定/ 全部文件，产出 {BOOK}/大纲/卷纲_第一卷.md：\n"
            "  主线一句话（必须能一句话说清「主角要做什么、谁在挡」）\n"
            "  卷末高潮 / 13 万字配比 / 每 3 章一个爽点峰值\n"
            "  悬念接力表：每个大悬念的埋设章与揭晓章，确保任一时刻并行钩子 ≥1.75\n"
            "回执 ≤100 字。"
        ),
    },
    "N08": {
        "id": "N08", "kind": "verify", "role": "judge", "name": "主线闸门",
        "evidence": "_pipeline/judgments_N08.json", "min_size": 200,
        "prompt": (
            "只读 {BOOK}/大纲/卷纲_第一卷.md。核心判据只有一条：\n"
            "  **主线能否用一句话说清「主角要做什么、谁在挡」**\n"
            "说不清 = 前 6000 字主线不清晰，约 80% 评估不通过。\n"
            "顺带查：悬念接力表是否存在空窗（大悬念解决后 3 章内无新钩子接入）。\n"
            "先输出 3 个拒稿理由再裁决。写入 {BOOK}/_pipeline/judgments_N08.json。回执 ≤100 字。"
        ),
    },
    "N09d": {
        "id": "N09d", "kind": "llm", "role": "writer", "name": "前12章目录骨架",
        "evidence": "大纲/目录_前12章.md", "min_size": 500,
        "prompt": (
            "读取 {BOOK}/大纲/卷纲_第一卷.md，产出 {BOOK}/大纲/目录_前12章.md：\n"
            "  每章一行：章号 / 一句话事件 / 本章爽点 / 章末钩子\n"
            "生成 **12 章**而非 10 章——预留 2 章冗余，防第 10 章写完差几百字不够 2 万。\n"
            "每章标注预估字数（保底 ≥2000）。回执 ≤100 字。"
        ),
    },
}


# ─── 章节循环节点 C{n}.N09–N16 ────────────────────────────────────────────────

def chapter_nodes(st: dict, book: Path, n: int) -> dict[str, dict]:
    """第 n 章的 8 个节点。n 从 1 开始。

    prompt 里的 {QIMAO} / {STORY_LONG_WRITE} / {BOOK} 是占位符，
    由 autopilot.render_prompt() 在 next 输出时替换成真实绝对路径
    （f-string 里写 {{QIMAO}} 才是字面量）。
    """
    b = str(book)
    prev = n - 1
    base = {
        f"C{n}.N09": {
            "id": f"C{n}.N09", "kind": "llm", "role": "writer", "name": "单章场景卡",
            "evidence": f"大纲/细纲_第{n:03d}章.md", "min_size": 500,
            "prompt": (
                f"读取 {b}/大纲/目录_前12章.md 第 {n} 行、{b}/大纲/卷纲_第一卷.md、"
                f"{b}/设定/角色.md，并读权威细纲模板 "
                f"{{STORY_LONG_WRITE}}/references/workflow-outline.md，"
                f"产出 {b}/大纲/细纲_第{n:03d}章.md。\n"
                "硬约束（check-outline-contract.js 逐项机械验收，缺一项即打回）：\n"
                "1. 九个必填字段各占一行，格式「- 字段名：值」：核心事件 / 字数目标（如 2200字）/ "
                "字数口径（visible_chars_v1）/ 单元ID/位置 / 目标情绪 / 主角目标/关键选择 / "
                "章首钩子 / 爽点 / 本章禁止提前释放；\n"
                "2. 「目标情绪」「主角目标/关键选择」必须写实际内容，不接受 [待补充]；\n"
                "3. 四个小节标题（### 开头）：内容概括 / 情节安排 / 人物关系和出场顺序 / 情节细化；\n"
                "4. 内容概括写全五段：起因 / 发展 / 转折 / 高潮 / 结尾；\n"
                "5. 情节细化用表格，表头为：# / 情节点 / 功能标签 / 执行边界（禁＋放）；\n"
                "6. 反引号引用的项目内 .md 路径必须真实存在，不许杜撰。\n"
                "内容硬规则：3–6 拍；第 1 拍的前 200 字内必须有冲突；全章只推进一条主线；"
                "章末出口必须是钩子。回执 ≤100 字。"
            ),
        },
        f"C{n}.N10": {
            "id": f"C{n}.N10", "kind": "script", "name": "细纲闸门",
            "evidence": f"大纲/细纲_第{n:03d}章.md", "min_size": 1,
        },
        f"C{n}.N11": {
            "id": f"C{n}.N11", "kind": "script", "name": "章节上下文组装",
            "evidence": "追踪/上下文.md", "min_size": 1,
        },
        f"C{n}.N12": {
            "id": f"C{n}.N12", "kind": "llm", "role": "writer", "name": "写正文+填追踪事务",
            "evidence": f"正文/第{n:03d}章_待命名.md", "min_size": 1800,
            "prompt": (
                f"读取：\n"
                f"  {b}/大纲/细纲_第{n:03d}章.md          （场景卡）\n"
                f"  {b}/追踪/上下文.md                    （7 栏上下文 + shapes + 上一章末尾 600 字锚点）\n"
                f"  {b}/.story/work/第{n:03d}章/tracking.json   （预填事务，按 shapes 填）\n"
                f"  {{QIMAO}}/harness/style-anchor.json    （文风锚，取 2 段正例切片）\n"
                f"写出 {b}/正文/第{n:03d}章_<标题>.md，单章 **≥2000 字**。\n"
                "硬约束：\n"
                "  · 因果显式——多用「因为/于是/这才」，七猫年代文因果连接词密度须 ≥1.0/千字\n"
                "  · 叙述动作词密度 ≤56/千字——不要把动作堆成流水账，留静态描写与心理\n"
                "  · 无缝衔接上一章末尾 600 字的语气与场景\n"
                "  · 按 shapes **完整填好** tracking.json 事务（delta / 角色快照 / 伏笔 / 时间线 / context.position）\n"
                "回执 ≤100 字，格式 {\"written\":\"...\",\"words\":N}"
            ),
        },
        f"C{n}.N13": {
            "id": f"C{n}.N13", "kind": "script", "name": "合并检查+平台闸门",
            "evidence": f"正文/第{n:03d}章_待命名.md", "min_size": 1,
        },
        f"C{n}.N14": {
            "id": f"C{n}.N14", "kind": "llm", "role": "writer", "name": "定点改写",
            "evidence": f"正文/第{n:03d}章_待命名.md", "min_size": 1800,
            "prompt": (
                f"读取 {b}/_pipeline/findings_C{n}.json（N13 脚本报错与 N15 审查意见已统一汇入）。\n"
                f"只改 findings 指向的段落，**不重写整章**；改完必须同步更新 "
                f"{b}/.story/work/第{n:03d}章/tracking.json。\n"
                "硬规则：同一处失败 2 次就停下报 blocked，不要第 3 次自己猜；不许注水凑字数。\n"
                "回执 ≤100 字。"
            ),
        },
        f"C{n}.N15": {
            "id": f"C{n}.N15", "kind": "verify", "role": "judge", "name": "阶段质量闸门",
            "evidence": f"_pipeline/judgments_C{n}.N15.json", "min_size": 200,
            "skippable": True,
            "prompt": _n15_prompt(n, prev, b),
        },
        f"C{n}.N16": {
            "id": f"C{n}.N16", "kind": "script", "name": "章末原子封板",
            "evidence": f"正文/第{n:03d}章_待命名.md", "min_size": 1,
        },
    }
    return base


def _n15_prompt(n: int, prev: int, b: str) -> str:
    """N15 清单按章分级：修复指令只能作用于当前章（前序章已封板）。"""
    if n == 1:
        scope = ("**首章铁律**：前 200 字有冲突（主角陷入困境：退婚/背叛/陷害/侮辱/破产/误会/灾难/生死危机/利益冲突）；"
                 "主角核心困境已交代；章末留下钩子。")
        src = f"{b}/正文/第001章_*.md"
    elif n == 2:
        return ("本条自动跳过——第 2 章靠 C02.N13 的确定性闸门把关即可。"
                "请直接调 skip。")
    elif n == 3:
        scope = ("**黄金三章闭环**：读第 1–3 章。查三章内是否有至少一次反转、"
                 "前 600 字冲突是否升级、前 6000 字主线是否可用一句话说清。")
        src = f"{b}/正文/ 下第 1–3 章"
    elif n in (6, 10):
        scope = ("**Milestone Gate**：节奏是否拖沓、伏笔回收率、主线推进是否过半、"
                 "是否存在悬念空窗（大悬念解决后 3 章内无新钩子）。")
        src = f"{b}/正文/ 下全部已写章节"
    else:
        return ("本条自动跳过——非里程碑章，靠确定性闸门把关。请直接调 skip。")

    return (
        f"你是七猫签约评审（红队模式）。只读 {src}。\n"
        f"审查范围：{scope}\n\n"
        "**铁律：你的修复指令只能作用于第 {n} 章**——第 1–{prev} 章已在各自章末封板，"
        "严禁要求推翻它们。若问题只能靠改前序章解决，请判 block 并说明。\n\n"
        "先输出「最可能被七猫编辑拒稿的 3 个理由」，再做裁决。\n"
        f"写入 {b}/_pipeline/judgments_C{n}.N15.json：\n"
        '  {"verdict":"pass|block","summary":"...","items":[{"criterion":"...","quote":"正文连续12-30字原文","source":"正文/第NNN章_xxx.md","verdict":"pass|fail","fix":"修复指令（只能落在当前章）"}]}\n'
        "quote 必须是正文连续原文，严禁省略号跳跃。回执 ≤100 字。"
    )


# ─── 游标推进 ─────────────────────────────────────────────────────────────────

CHAPTER_SEQ = [f"C{{n}}.N{k:02d}" for k in (9, 10, 11, 12, 13, 14, 15, 16)]
GLOBAL_SEQ = ["N01", "N02", "N03", "N04", "N05", "N06", "N07", "N08", "N09d",
              "__chapters__", "N17", "N18", "N19", "N20", "N20f", "N21"]


def first_cursor() -> str:
    return "N01"


def next_cursor(st: dict) -> str:
    """按当前 cursor 算下一个。章节循环由 current_chapter 驱动。"""
    cur = st["cursor"]
    if cur == "__done__":
        return "__done__"

    if cur.startswith("C"):
        n = st["current_chapter"] + 1
        key = cur.split(".", 1)[1]              # N09..N16
        idx = [f"N{k:02d}" for k in (9, 10, 11, 12, 13, 14, 15, 16)].index(key)
        if idx + 1 < 8:
            return f"C{n}.N{[9,10,11,12,13,14,15,16][idx + 1]:02d}"
        # 一章走完：若由 N16 封板成功，章号 +1
        st["current_chapter"] = n
        st["chapters_committed"] = n
        if n >= st["max_chapters"] or st["words_total"] >= st["target_words"]:
            return "N17"
        return f"C{n + 1}.N09"

    i = GLOBAL_SEQ.index(cur)
    if i + 1 >= len(GLOBAL_SEQ):
        return "__done__"
    nxt = GLOBAL_SEQ[i + 1]
    if nxt == "__chapters__":
        return "C1.N09"
    return nxt
