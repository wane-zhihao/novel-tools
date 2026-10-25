#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline_spec.py — 节点表（流程唯一真相）

节点类型：
  script  → autopilot.py 内联 subprocess 执行，对 LLM 透明
  llm     → 需 subagent 创作，落盘后调 done
  verify  → 需 subagent 红队审查，落盘 judgments JSON 后调 done

模型分工（复刻 autonovel 的「审写分离」，防自评自夸）：
  writer 用 model="default" 或 "lite"  —— 写正文靠长上下文与速度，量大
  judge  用 model="reasoning"           —— 审稿要挑刺，需更强推理
主会话 spawn subagent 时必须按 spec["model"] 传参，不得统一用默认值。

章节循环用复合键 C{n}.Nxx，避免扁平字典写第 2 章时覆盖第 1 章状态。
"""
from __future__ import annotations

from pathlib import Path

# ─── 全局节点 N01–N09d ────────────────────────────────────────────────────────

# ─── 卷与里程碑（纯函数推导，不建第二真相源）────────────────────────────
# 只存 st["plan"] = {"vol_size": N}；卷边界每次由 max_chapters 推导，
# 所以 resume 不需要重建卷数据，改 max_chapters 也不会与卷计划打架。

def vol_size(st: dict) -> int:
    return (st.get("plan") or {}).get("vol_size", 22)


def span(st: dict, v: int) -> tuple[int, int]:
    """第 v 卷的 (起始章, 结束章)，均含端点，章号从 1 开始。"""
    size, total = vol_size(st), st["max_chapters"]
    return (v - 1) * size + 1, min(v * size, total)


def volumes(st: dict) -> list[tuple[int, int, int]]:
    """[(卷号, 起始章, 结束章)]。无 plan 时视为单卷全书。"""
    total, size = st["max_chapters"], vol_size(st)
    n = (total + size - 1) // size
    return [(v, *span(st, v)) for v in range(1, n + 1)]


def vol_of(st: dict, n: int) -> int:
    return (n - 1) // vol_size(st) + 1


def milestones(st: dict) -> tuple[int, ...]:
    """N15 审查点。

    无 plan（老书）：(1, 3, 6, 10) —— 与改造前逐字一致，保证七猫不回退。
    有 plan：黄金三章固定 (1,3,6,10) + 每 20 章一个 + 末章。
    """
    if not st.get("plan"):
        return (1, 3, 6, 10)
    m = st["max_chapters"]
    pts = {1, 3, 6, 10, m} | set(range(20, m, 20))
    return tuple(sorted(x for x in pts if 1 <= x <= m))


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
        "id": "N03", "kind": "llm", "model": "default", "role": "scout", "name": "选题报告",
        "evidence": "大纲/选题报告.md", "min_size": 600,
        "prompt": (
            "你是七猫系选题参谋。任务：写一份选题报告。\n"
            "第一步，只读这三份资料（用 read_file，路径照抄即可）：\n"
            "  {QIMAO}/01_投稿方案.md   （站点门槛 / 收稿范围 / 唯一一次评估机会）\n"
            "  {QIMAO}/02_写作方案.md   （平台调性 / 开篇四关）\n"
            "  {QIMAO}/harness/genre-baselines.json  （爽文/年代两组的实测基准）\n"
            "第二步，读完立即用 write_file 把报告写入 {BOOK}/大纲/选题报告.md。"
            "不要浏览其他目录，不要读 _pipeline/ 下的任何文件。\n"
            "报告要求：≥600 字；含 5 个题材候选，按客观标准打分：平台收稿匹配度 / 榜单热度 / "
            "创新配比(90%套路+10%创新) / 是否避开已写烂的梗 / 与年代题材基准兼容性；"
            "必须含「结论」一节，明确给出选定题材与理由。\n"
            "若 _pipeline/judgments_N04.json 存在且 verdict=block，先读它，按其中 fail 项的 fix 意见针对性改写后再写入。\n"
            "写完只用 ≤100 字回执，不要把报告正文复述在回执里。"
        ),
    },
    "N04": {
        "id": "N04", "kind": "verify", "model": "reasoning", "role": "judge", "name": "选题闸门",
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
        "id": "N05", "kind": "llm", "model": "default", "role": "writer", "name": "设定",
        "evidence": "设定/题材定位.md", "min_size": 300,
        "prompt": (
            "读取 {BOOK}/大纲/选题报告.md 的结论一节，产出 5 个设定文件：\n"
            "  设定/题材定位.md  设定/文风.md  设定/角色.md  设定/世界观.md  设定/势力.md\n"
            "文风.md 必须引用 {QIMAO}/harness/genre-baselines.json 的年代组基准"
            "（句长中位 15-27、段/千字 33.1-54.1）。\n"
            "角色.md 每个角色需有：名字 / 一句话动机 / 核心困境 / 与主角的关系。\n"
            "若 _pipeline/judgments_N06.json 存在且 verdict=block，先读它，按其中 fail 项的 fix 意见改写后再写入。\n"
            "回执 ≤100 字。"
        ),
    },
    "N06": {
        "id": "N06", "kind": "verify", "model": "reasoning", "role": "judge", "name": "设定校验",
        "evidence": "_pipeline/judgments_N06.json", "min_size": 200,
        "prompt": (
            "只读 {BOOK}/设定/ 下 5 个文件。核查：五个文件是否齐全、角色是否有明确动机、"
            "世界观是否过度庞杂（七猫清单：设定太庞杂是硬伤）、文风是否与年代基准兼容。\n"
            "先输出 3 个最可能被拒的理由再裁决。写入 {BOOK}/_pipeline/judgments_N06.json，"
            "格式同 N04，source 填具体文件名。回执 ≤100 字。"
        ),
    },
    "N07": {
        "id": "N07", "kind": "llm", "model": "default", "role": "writer", "name": "卷纲",
        "evidence": "大纲/卷纲_第一卷.md", "min_size": 800,
        "prompt": (
            "读取 {BOOK}/设定/ 全部文件，产出 {BOOK}/大纲/卷纲_第一卷.md：\n"
            "  主线一句话（必须能一句话说清「主角要做什么、谁在挡」）\n"
            "  卷末高潮 / 13 万字配比 / 每 3 章一个爽点峰值\n"
            "  悬念接力表：每个大悬念的埋设章与揭晓章，确保任一时刻并行钩子 ≥1.75\n"
            "若 _pipeline/judgments_N08.json 存在且 verdict=block，先读它，按其中 fail 项的 fix 意见改写后再写入。\n"
            "回执 ≤100 字。"
        ),
    },
    "N08": {
        "id": "N08", "kind": "verify", "model": "reasoning", "role": "judge", "name": "主线闸门",
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
        "id": "N09d", "kind": "llm", "model": "default", "role": "writer", "name": "前12章目录骨架",
        "evidence": "大纲/目录_前12章.md", "min_size": 500,
        "prompt": (
            "读取 {BOOK}/大纲/卷纲_第一卷.md，产出 {BOOK}/大纲/目录_前12章.md：\n"
            "  每章一行：章号 / 一句话事件 / 本章爽点 / 章末钩子\n"
            "生成 **12 章**而非 10 章——预留 2 章冗余，防第 10 章写完差几百字不够 2 万。\n"
            "每章标注预估字数（保底 ≥2000）。回执 ≤100 字。"
        ),
    },

    # ─── P5 收尾：只读探针 + 单点修补（N20f 由 N20 block 路由进入，不在线性序列里） ───
    "N17": {
        "id": "N17", "kind": "script", "name": "全文整合",
        "evidence": "投稿包/全文.md", "min_size": 2000,
    },
    "N18": {
        "id": "N18", "kind": "script", "name": "AI味/退化扫描",
        "evidence": "_pipeline/endgame_findings.json", "min_size": 10,
    },
    "N19": {
        "id": "N19", "kind": "script", "name": "文风漂移+伏笔回收",
        "evidence": "_pipeline/endgame_findings.json", "min_size": 10,
    },
    "N20": {
        "id": "N20", "kind": "verify", "model": "reasoning", "role": "judge", "name": "评分卡终审",
        "evidence": "_pipeline/judgments_N20.json", "min_size": 200,
        "prompt": (
            "你是七猫签约评审（红队模式）。这是**终审**，不写稿、不改稿。\n"
            "读取：\n"
            "  {QIMAO}/03_评分卡.md                 （判定项与 B/A 级别，唯一标准）\n"
            "  {QIMAO}/01_投稿方案.md               （收稿范围 / 唯一一次评估机会）\n"
            "  {BOOK}/投稿包/全文.md                 （整合后的交付文本）\n"
            "  {BOOK}/_pipeline/endgame_findings.json（N18/N19 的机械扫描结果）\n"
            "  {BOOK}/设定/角色.md                   （人物登记表，判「未知人物」）\n"
            "  {BOOK}/追踪/_tracking-state.json      （伏笔账本）\n\n"
            "【分工】机械项以 endgame_findings.json 为准，不要自己重算：\n"
            "  5–7 文风、12–13 AI/退化、17–19 字数与整合。\n"
            "你只判需要读文本的项：\n"
            "  B 级（任一 fail → verdict=block）：1–4 开篇四关、8–9 场景切换/过渡、"
            "14 段落重复、16 未知人物；\n"
            "  A 级（只记录，不阻断）：10–11 主线收束/利用率、15 题材漂移。\n\n"
            "先输出「最可能被七猫编辑拒稿的 3 个理由」，再逐项裁决。\n"
            "每条 item 必须给 quote（正文连续 12–30 字原文），source 填具体章节文件路径；"
            "纯机械项可 quote 留空，但必须写 metric（{\"actual\":...,\"expected\":...}）。\n"
            "裁决：任一 B 项 fail → verdict=block。summary 必须列出**只能靠改 正文/ 解决的章节清单**；"
            "若问题无法通过改正文解决（例如字数总量不足、伏笔回收率结构性过低），明确写「需人工决策」。\n"
            "N20f 只准改正文，不许改剧情、大纲、设定、追踪。\n\n"
            "写入 {BOOK}/_pipeline/judgments_N20.json：\n"
            '  {"verdict":"pass|block","summary":"...","items":[{"id":"1","level":"B|A",'
            '"criterion":"...","quote":"正文连续12-30字原文","source":"正文/第NNN章_xxx.md",'
            '"verdict":"pass|fail","fix":"只改正文的修复指令","metric":{"actual":...,"expected":...}}],'
            '"chapter_fix_list":[{"chapter":1,"items":["..."]}]}\n'
            "quote 必须是正文连续原文，严禁省略号跳跃。回执 ≤100 字。"
        ),
    },
    "N20f": {
        "id": "N20f", "kind": "llm", "model": "default", "role": "writer", "name": "外科手术补丁",
        "evidence": "_pipeline/n20f_patch_log.json", "min_size": 20,
        "prompt": (
            "读取 {BOOK}/_pipeline/endgame_findings.json 与 {BOOK}/_pipeline/judgments_N20.json。\n"
            "**只改 正文/ 下被点名的章节**，做段落级外科手术：按 findings 的 fix 逐条修，"
            "不重写整章、不改剧情、不改人物关系、不新增/删除章节文件。\n"
            "严禁修改 大纲/、设定/、追踪/、投稿包/——投稿包会在下一轮 N17 重新合成。\n"
            "同一处修 2 次仍不过就停下，把原因写进补丁日志，不要第 3 次硬猜。\n"
            "改完写 {BOOK}/_pipeline/n20f_patch_log.json：\n"
            '  {"chapters":[{"chapter":1,"file":"正文/第001章_xxx.md","fixed":["..."]}],'
            '"unresolved":[{"item":"...","reason":"..."}]}\n'
            "回执 ≤100 字。"
        ),
    },
    "N21": {
        "id": "N21", "kind": "script", "name": "投稿包打包",
        "evidence": ["投稿包/全文.md", "投稿包/体检报告.md",
                     "投稿包/投稿邮件.md", "投稿包/投稿记录.md"],
        "min_size": 80,
    },
}


# 全书汇总节点：只合成 + 只读概要，**不给 judge 读正文**
# 25 万字正文约 750KB / 20 万 token，进 judge 上下文必爆。
GLOBAL_NODES["N17g"] = {
    "id": "N17g", "kind": "script", "name": "全书合成+概要",
    "evidence": "投稿包/全书概要.md", "min_size": 200,
    "note": "合出 全文.md 供人读，另出 全书概要.md 供 N20g 判。N20g 不读正文。",
}

GLOBAL_NODES["N20g"] = {
    "id": "N20g", "kind": "verify", "model": "reasoning", "role": "judge",
    "name": "全书汇总终审", "evidence": "_pipeline/judgments_N20g.json", "min_size": 200,
    "prompt": (
        "只读 {BOOK}/投稿包/全书概要.md（**不要读正文，25 万字会超出上下文**）。\n"
        "这是逐卷终审之后的跨卷检查。审四件事：\n"
        "  1. 主线是否从第 1 卷贯穿到最后一卷（有没有中途换目标）\n"
        "  2. 伏笔回收率：埋设但未回收的还有几条\n"
        "  3. 人物弧光：主角在第 1 卷与最后一卷是否是同一个人的成长\n"
        "  4. 卷与卷之间有没有重复套路（同一招用了三次以上）\n"
        "写入 {BOOK}/_pipeline/judgments_N20g.json，格式同 N20，"
        "source 填 投稿包/全书概要.md。quote 必须是概要里的连续原文。\n"
        "回执 ≤100 字。"
    ),
}


# ─── 章节循环节点 C{n}.N09–N16 ────────────────────────────────────────────────

def _outline_ref(st: dict, n: int) -> str:
    """第 n 章该读哪份目录、第几行。

    有卷计划：读本卷目录，行号 = n - 本卷起始章 + 1（卷内偏移）。
    无卷计划（老书）：沿用 目录_前12章.md 第 n 行——但 n>12 时该文件根本没有第 n 行，
    所以老书本来就只能写 12 章，这是既有上限，不在本次改动范围内。
    """
    b = str(st.get("_book") or "{BOOK}")
    if st.get("plan"):
        v = vol_of(st, n)
        start, _ = span(st, v)
        return f"{{BOOK}}/大纲/目录_第{v}卷.md 第 {n - start + 1} 行（本卷第 {n - start + 1} 行）"
    return f"{{BOOK}}/大纲/目录_前12章.md 第 {n} 行"


def _volume_outline_ref(st: dict, n: int) -> str:
    if st.get("plan"):
        return f"{{BOOK}}/大纲/卷纲_第{vol_of(st, n)}卷.md"
    return "{BOOK}/大纲/卷纲_第一卷.md"


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
            "id": f"C{n}.N09", "kind": "llm", "model": "default", "role": "writer", "name": "单章场景卡",
            "evidence": f"大纲/细纲_第{n:03d}章.md", "min_size": 500,
            "prompt": (
                f"读取 {_outline_ref(st, n)}、{_volume_outline_ref(st, n)}、"
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
            "evidence": "追踪/上下文.md", "min_size": 200,
        },
        f"C{n}.N12": {
            "id": f"C{n}.N12", "kind": "llm", "model": "default", "role": "writer", "name": "写正文+填追踪事务",
            "evidence": f"正文/第{n:03d}章_*.md", "min_size": 1800,
            "prompt": (
                f"读取：\n"
                f"  {b}/大纲/细纲_第{n:03d}章.md          （场景卡，按拍写）\n"
                f"  {b}/追踪/上下文.md                    （7 栏上下文 + shapes + 上一章末尾 600 字锚点）\n"
                f"  {b}/.story/work/第{n:03d}章/tracking.json   （预填事务，按 shapes 填）\n"
                f"  {{QIMAO}}/harness/style-anchor.json    （文风锚，取 2 段正例切片）\n"
                f"  {{SKILL}}/references/writing-contract.md    （**手法手册，先读**）\n\n"
                f"写出 {b}/正文/第{n:03d}章_<标题>.md，单章 **≥2000 字**。\n\n"
                "【核心差异】被拒稿写「然后」，签约作品写「所以」。\n"
                "  反例：「陈志海绕到后头去抬，王婶喊了一声慢，三个人这才把车顺下去。」——只有事件\n"
                "  正例：「正因为空得太干净，他才确信这一栏不是没人填，而是被人抹掉过。」——有因果\n\n"
                "【四个必用手法】\n"
                "  1. 段落收尾法：每段最后一句必须回答「所以呢」，用 这才/原来/果然/难怪/怪不得/正因为 收。\n"
                "     目标：带因果词收尾的段落 ≥60%\n"
                "  2. 心理问句法：每 2–3 段插一个角色内心问句，要具体（「第七行为什么是三？」\n"
                "     而不是「他该怎么办？」）。目标：全章 ≥5 处\n"
                "  3. 动作-因果改写：连续 3 句纯动作必须拆——一半改因果句，一半改「动作+心理」复合句。\n"
                "     但**不要取消动作**（签约作品动作词密度中位是 29，不是 0）\n"
                "  4. 数字落地法：数字后下一句必须写它造成的后果或反应，不许罗列\n\n"
                "  5. 段落切分：每段 18–30 字（1–2 句），对应 33–54 段/千字。\n"
                "     场景切换/动作转换/心理另起都要断段。AI 天然爱写长段，必须主动切。\n\n"
                "【生死线（N13 会卡，**上下限都要**）】\n"
                "  · 因果连接/千字 **1.0–6.0**（超过 6.0 说明每段都硬塞因果，像说教）\n"
                "  · 叙述动作词/千字 **≤41**（年代题材）\n"
                "  · 问句/千字 2.0–11.7\n"
                "  · 段/千字 **33–54**（低于 33 = 段落太长）\n"
                "  · 字数 ≥2000\n\n"
                "【开篇铁律】第 1 章前 200 字必须是当下冲突，不许环境描写、不许回忆倒叙。\n"
                "【衔接】无缝接上上一章末尾 600 字的语气与场景。\n"
                "【追踪】按 shapes **完整填好** tracking.json 事务（delta / 角色快照 / 伏笔 / 时间线 / context.position）；"
                "字段条数上限：continuity_risks ≤5、active_character_names ≤6、long_term_constraints ≤6、"
                "next_chapter_commitments ≤5、recent_chapters ≤3——超出会被 tracking_commit 拒收、N16 封板失败\n\n"
                "交付前按 writing-contract.md 第三节自检清单逐项过一遍，任一项不过先自己改。\n"
                "回执 ≤100 字，格式 {\"written\":\"...\",\"words\":N}"
            ),
        },
        f"C{n}.N13": {
            "id": f"C{n}.N13", "kind": "script", "name": "合并检查+平台闸门",
            "evidence": f"正文/第{n:03d}章_*.md", "min_size": 1,
        },
        f"C{n}.N14": {
            "id": f"C{n}.N14", "kind": "llm", "model": "default", "role": "writer", "name": "定点改写",
            "evidence": f"正文/第{n:03d}章_*.md", "min_size": 1800,
            "skippable": True,  # N13 通过（无 findings）时按方案跳过，直达 N15
            "prompt": (
                f"读取 {b}/_pipeline/findings_C{n}.json（N13 脚本报错与 N15 审查意见已统一汇入）。\n"
                f"读取 {{SKILL}}/references/writing-contract.md 的「四个可操作手法」。\n"
                f"只改 findings 指向的段落，**不重写整章**；改完必须同步更新 "
                f"{b}/.story/work/第{n:03d}章/tracking.json。\n\n"
                "【按 findings 类型选手法（**注意方向：偏高用反向手法**）】\n"
                "  · 因果连接 偏低 → 手法1 段落收尾法（补「正因为/这才/原来」）\n"
                "  · 因果连接 偏高(>6.0) → 反向：删掉部分段落的因果收尾，留 20%–30% 用纯动作或纯对话收\n"
                "  · 问句 偏低 → 手法2 心理问句法（插具体内心问句）\n"
                "  · 问句 偏高(>11.7) → 反向：把部分内心问句改成陈述\n"
                "  · 动作词 偏高(>41) → 手法3 动作-因果改写（拆连续动作句，但不要取消动作）\n"
                "  · 动作词 偏低(<10) → 反向：加回具体动作，别全是心理与对话\n"
                "  · 段/千字 偏低(<33) → 手法5 段落切分（切到 18–30 字/段）\n"
                "  · 段/千字 偏高(>54) → **反向：合段**（同场景相邻短段合并，别跨场景）\n"
                "  · 句长中位 偏低(<15) → 手法6 句长控制（叙述句写长到 25–40 字，对话保持短）\n"
                "  · 字数不足 → 加「一个回合的博弈」，不是加形容词\n\n"
                "硬规则：同一处失败 2 次就停下报 blocked，不要第 3 次自己猜；不许注水凑字数；不许删剧情。\n"
                "回执 ≤100 字。"
            ),
        },
        f"C{n}.N15": {
            "id": f"C{n}.N15", "kind": "verify", "model": "reasoning", "role": "judge", "name": "阶段质量闸门",
            "evidence": f"_pipeline/judgments_C{n}.N15.json", "min_size": 200,
            "skippable": True,
            "prompt": _n15_prompt(n, prev, b, st),
        },
        f"C{n}.N16": {
            "id": f"C{n}.N16", "kind": "script", "name": "章末原子封板",
            "evidence": f"正文/第{n:03d}章_*.md", "min_size": 1,
        },
    }
    return base


def _chapter_path(b: str, n: int) -> str:
    hits = sorted(Path(b).glob(f"正文/第{n:03d}章_*.md"))
    return str(hits[0]) if hits else f"{b}/正文/第{n:03d}章_*.md"


def volume_nodes(st: dict, book: Path, v: int) -> dict[str, dict]:
    """第 v 卷的 7 个节点。镜像 chapter_nodes() 的做法，按卷动态生成。

    卷前：N07 卷纲 → N08 主线闸门 → N09d 本卷目录
    卷末：N17 合成 → N18 AI味 → N19 漂移 → N20 本卷终审
    """
    b = str(book)
    start, end = span(st, v)
    size = end - start + 1
    words = size * 1925            # 纵横单章中位，用于给 writer 一个量感
    return {
        f"V{v}.N07": {
            "id": f"V{v}.N07", "kind": "llm", "model": "default", "role": "writer",
            "name": f"第{v}卷卷纲", "evidence": f"大纲/卷纲_第{v}卷.md", "min_size": 600,
            "prompt": (
                f"读取 {b}/设定/ 全部文件"
                + (f"、{b}/大纲/卷纲_第{v-1}卷.md 的卷末状态" if v > 1 else "")
                + f"，产出 {b}/大纲/卷纲_第{v}卷.md。\n"
                f"本卷范围：第 {start}–{end} 章（{size} 章，约 {words} 字）。\n"
                "必含：\n"
                "  主线一句话（主角要做什么、谁在挡）\n"
                "  本卷高潮在第几章、是什么\n"
                "  每 3 章一个爽点峰值\n"
                "  悬念接力表：本卷埋的伏笔、何时回收（跨卷的要写明目标卷）\n"
                + ("  接续上卷：上一卷遗留的未回收伏笔必须在本卷或后续卷给出位置\n" if v > 1 else "")
                + "回执 ≤100 字。"
            ),
        },
        f"V{v}.N08": {
            "id": f"V{v}.N08", "kind": "verify", "model": "reasoning", "role": "judge",
            "name": f"第{v}卷主线闸门", "evidence": f"_pipeline/judgments_V{v}.N08.json",
            "min_size": 200,
            "prompt": (
                f"只读 {b}/大纲/卷纲_第{v}卷.md。核心判据只有一条：\n"
                "  **本卷主线能否用一句话说清「主角要做什么、谁在挡」**\n"
                "说不清 = 这一卷会写成流水账。\n"
                "顺带查：悬念接力表有没有空窗（大悬念解决后 3 章内无新钩子）。\n"
                f"先输出「本卷最可能被编辑毙掉的 3 个理由」再裁决，写入 "
                f"{b}/_pipeline/judgments_V{v}.N08.json，格式同 N08。\n"
                "quote 必须是卷纲里的连续原文。回执 ≤100 字。"
            ),
        },
        f"V{v}.N09d": {
            "id": f"V{v}.N09d", "kind": "llm", "model": "default", "role": "writer",
            "name": f"第{v}卷目录", "evidence": f"大纲/目录_第{v}卷.md", "min_size": 400,
            "prompt": (
                f"读取 {b}/大纲/卷纲_第{v}卷.md，产出 {b}/大纲/目录_第{v}卷.md。\n"
                f"**必须正好 {size} 行**（第 {start}–{end} 章），每行一章：\n"
                "  章号 / 一句话事件 / 本章爽点 / 章末钩子 / 预估字数（1800–2400）\n"
                "第 1 行对应第 {start} 章，第 {size} 行对应第 {end} 章——**行号即章号偏移，不能错位**。\n"
                + (f"另外把本卷第 1–12 章另存一份 {b}/大纲/目录_前12章.md（兼容旧工具）。\n" if v == 1 else "")
                + "回执 ≤100 字。"
            ),
        },
        f"V{v}.N17": {
            "id": f"V{v}.N17", "kind": "script", "name": f"第{v}卷合成",
            "evidence": f"投稿包/卷{v}_全文.md", "min_size": 1000,
            "note": f"只合本卷第 {start}–{end} 章，单卷 ≤6 万字。",
        },
        f"V{v}.N18": {
            "id": f"V{v}.N18", "kind": "script", "name": f"第{v}卷AI味扫描",
            "evidence": f"_pipeline/scan_V{v}.json", "min_size": 20,
        },
        f"V{v}.N19": {
            "id": f"V{v}.N19", "kind": "script", "name": f"第{v}卷文风漂移+伏笔",
            "evidence": f"_pipeline/drift_V{v}.json", "min_size": 20,
        },
        f"V{v}.N20": {
            "id": f"V{v}.N20", "kind": "verify", "model": "reasoning", "role": "judge",
            "name": f"第{v}卷终审", "evidence": f"_pipeline/judgments_V{v}.N20.json",
            "min_size": 200,
            "prompt": (
                f"只读 {b}/投稿包/卷{v}_全文.md（本卷第 {start}–{end} 章，≤6 万字）。\n"
                f"对照 {b}/大纲/卷纲_第{v}卷.md 与 {{QIMAO}}/03_评分卡.md 做本卷终审。\n"
                "先输出「本卷最可能让读者弃书的 3 个位置」再裁决。\n"
                f"写入 {b}/_pipeline/judgments_V{v}.N20.json，格式同 N20，"
                "source 填 投稿包/卷{v}_全文.md。\n"
                "quote 必须是正文连续原文。回执 ≤100 字。"
            ),
        },
    }


def _n15_prompt(n: int, prev: int, b: str, st: dict | None = None) -> str:
    """N15 清单按章分级：修复指令只能作用于当前章（前序章已封板）。"""
    if n == 1:
        scope = ("**首章铁律**：前 200 字有冲突（主角陷入困境：退婚/背叛/陷害/侮辱/破产/误会/灾难/生死危机/利益冲突）；"
                 "主角核心困境已交代；章末留下钩子。")
        src = _chapter_path(b, 1)
    elif n == 2:
        return ("本条自动跳过——第 2 章靠 C02.N13 的确定性闸门把关即可。"
                "请直接调 skip。")
    elif n == 3:
        scope = ("**黄金三章闭环**：读第 1–3 章。查三章内是否有至少一次反转、"
                 "前 600 字冲突是否升级、前 6000 字主线是否可用一句话说清。")
        src = "、".join(_chapter_path(b, i) for i in (1, 2, 3))
    elif n in milestones(st or {}):
        scope = ("**Milestone Gate**：节奏是否拖沓、伏笔回收率、主线推进是否过半、"
                 "是否存在悬念空窗（大悬念解决后 3 章内无新钩子）。")
        src = f"{b}/正文/ 下全部已写章节"
    else:
        return ("本条自动跳过——非里程碑章，靠确定性闸门把关。请直接调 skip。")

    prev_range = f"第 1–{prev} 章" if prev >= 1 else "无前序章（本章是第 1 章）"
    return (
        f"你是七猫签约评审（红队模式）。只读 {src}。\n"
        f"审查范围：{scope}\n\n"
        f"**铁律：你的修复指令只能作用于第 {n} 章**——{prev_range}已在各自章末封板，"
        "严禁要求推翻它们。若问题只能靠改前序章解决，请判 block 并说明。\n\n"
        "先输出「最可能被七猫编辑拒稿的 3 个理由」，再做裁决。\n\n"
        "【删除测试（判「白描 vs 流水账」，机械指标做不到这一层）】\n"
        "探针会给出本章最长的 3 条「纯动作链」（连续不含因果/心理的动作句）。\n"
        "对每条链逐句问：**删掉这一句，后面 3 句内是否出现依赖它的指代、后果或状态变化？**\n"
        "  · 有依赖 → 这句是白描，保留\n"
        "  · 无依赖 → 这句是素写，标记 removable\n"
        "一条链里 removable ≥60% → 该链判 fail。修复指令：改写成「动作+因果」或\n"
        "「动作+细节判断」，**不许删剧情**。\n"
        "若本章无动作链（action_chain_max < 3），本项直接 pass。\n\n"
        f"写入 {b}/_pipeline/judgments_C{n}.N15.json：\n"
        '  {"verdict":"pass|block","summary":"...","items":[{"criterion":"...","quote":"正文连续12-30字原文","source":"正文/第NNN章_xxx.md","verdict":"pass|fail","fix":"修复指令（只能落在当前章）"}]}\n'
        "quote 必须是正文连续原文，严禁省略号跳跃。回执 ≤100 字。"
    )


# ─── 游标推进 ─────────────────────────────────────────────────────────────────

STATION_MIN_WORDS = 20000
MECHANICAL_N20_IDS = {"5", "6", "7", "12", "13", "17", "18", "19"}


def judgment_policy(node_id: str) -> dict:
    """verify 节点的引用策略：不同节点允许的取证范围不同。"""
    if node_id == "N04":
        return {"sources": ["大纲/选题报告.md"], "mechanical_ids": set()}
    if node_id == "N06":
        return {"sources": ["设定/"], "mechanical_ids": set()}
    if node_id == "N08":
        return {"sources": ["大纲/卷纲_第一卷.md"], "mechanical_ids": set()}
    if node_id.startswith("C") and node_id.endswith(".N15"):
        chapter = int(node_id.split(".")[0][1:])
        return {"sources": [f"正文/第{chapter:03d}章_"], "mechanical_ids": set()}
    if node_id == "N20":
        return {"sources": ["正文/"], "mechanical_ids": MECHANICAL_N20_IDS}
    return {"sources": [], "mechanical_ids": set()}


# 单章节点顺序，只在这里定义一次（此前 :397 又硬编码了一遍 [9..16]，两处易不同步）
CHAPTER_STAGES = (9, 10, 11, 12, 13, 14, 15, 16)
# 卷级节点顺序（每卷开头跑一次，替代原来一次性的 N07/N08/N09d）
VOLUME_STAGES = ("N07", "N08", "N09d")          # 卷前：卷纲 → 主线闸门 → 本卷目录
VOLUME_END_STAGES = ("N17", "N18", "N19", "N20")  # 卷末：合成 → AI味 → 漂移 → 本卷终审

GLOBAL_SEQ = ["N01", "N02", "N03", "N04", "N05", "N06", "N07", "N08", "N09d",
              "__chapters__", "N17", "N18", "N19", "N20", "N21"]
# N20f 不在线性序列里：只有 N20 判 block 时才由 commands.cmd_done 把 cursor 指过去，
# 修完由 next_cursor 回跳 N17 重合成。这样 N20 pass 直接进 N21，不会空跑修补节点。


def first_cursor() -> str:
    return "N01"


def next_cursor(st: dict) -> str:
    """按当前 cursor 算下一个。章节循环由 current_chapter 驱动。"""
    cur = st["cursor"]
    if cur == "__done__":
        return "__done__"
    if cur == "N17g":                 # 全书合成完 → 全书汇总终审
        return "N20g"
    if cur == "N20g":                 # 全书终审完 → 投稿包
        return "N21"
    if cur == "N20f":                 # 补丁修完 → 回 N17 重合成/重扫，再进 N20 复核
        return "N17"
    if cur == "N20":                  # 终审 pass 才走到这里；block 由 cmd_done 直接指到 N20f
        return "N21"

    # ── 卷级节点 ──────────────────────────────────────────────────
    # 卷前：N07 → N08 → N09d → 本卷第 1 章
    # 卷末：N17 → N18 → N19 → N20 → 下一卷 N07（或全书 N17g）
    if cur.startswith("V"):
        head, key = cur.split(".", 1)
        v = int(head[1:])
        if key in VOLUME_STAGES:
            i = VOLUME_STAGES.index(key)
            if i + 1 < len(VOLUME_STAGES):
                return f"{head}.{VOLUME_STAGES[i + 1]}"
            start, _ = span(st, v)
            st["current_chapter"] = start - 1      # 下一章的 n = start
            return f"C{start}.N09"
        if key in VOLUME_END_STAGES:
            i = VOLUME_END_STAGES.index(key)
            if i + 1 < len(VOLUME_END_STAGES):
                return f"{head}.{VOLUME_END_STAGES[i + 1]}"
            _, end = span(st, v)                   # 本卷终审通过
            if end >= st["max_chapters"]:
                return "N17g"
            return f"V{v + 1}.N07"
        return "__done__"

    if cur.startswith("C"):
        n = st["current_chapter"] + 1
        key = cur.split(".", 1)[1]              # N09..N16
        idx = CHAPTER_STAGES.index(int(key[1:]))
        if idx + 1 < len(CHAPTER_STAGES):
            return f"C{n}.N{CHAPTER_STAGES[idx + 1]:02d}"
        # 一章走完：若由 N16 封板成功，章号 +1
        st["current_chapter"] = n
        st["chapters_committed"] = n
        if n >= st["max_chapters"]:
            # 新书（有 plan）走全书汇总；老书维持 N17
            return "N17" if not st.get("plan") else "N17g"
        if st.get("plan"):
            size = vol_size(st)
            if n % size == 0 or n == st["max_chapters"]:
                # 卷末 → 先做本卷合成/扫描/终审，再由 V{v}.N20 决定进下一卷还是 N17g
                return f"V{vol_of(st, n)}.N17"
        elif st["words_total"] >= st["target_words"]:
            # 老书的字数早停保留原样；新书不早停（否则 25 万字在第 125 章就跳走）
            return "N17"
        return f"C{n + 1}.N09"

    i = GLOBAL_SEQ.index(cur)
    if i + 1 >= len(GLOBAL_SEQ):
        return "__done__"
    nxt = GLOBAL_SEQ[i + 1]
    if nxt == "__chapters__":
        return "C1.N09"
    # 新书：N06 之后不进一次性 N07，而是进第 1 卷的卷纲
    if nxt == "N07" and st.get("plan"):
        return "V1.N07"
    return nxt
