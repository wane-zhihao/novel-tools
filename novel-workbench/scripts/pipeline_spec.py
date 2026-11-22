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

import json
import os
import re
import subprocess
from pathlib import Path

CHAPTER_BODY_MIN_WORDS = 2000
CHAPTER_TARGET_WORDS = 2200
# 上限闸门：storyctl 按 visible_chars_v1（汉字＋标点）判定，超出即 over → N12 预检打回。
# 此前 N12 提示词只写了「≥2000」不说上限，writer 自然写到 3200+，每章都要被预检拦一次。
CHAPTER_BODY_MAX_CHARS = 2600

# ─── 裁决契约（2026-11-19） ──────────────────────────────────────────────────
# 闸门校验器（commands._validate_judgment）执行的就是这份契约：
#   fail 项必须给逐字引文（阻断与修复指令要有据）；
#   pass 项可省引文（结构性检查本来没有可引的句子，硬要引文只会逼 judge 编）；
#   整份裁决至少要有一条命中引文，以证明 judge 真读了源文件。
# 提示词与校验器必须说同一件事——否则执行方照提示词写、被校验器按另一套
# 规矩拒，好裁决白费（冷启动书 N06 实测：内容全对，只因键名不同被判废）。
VERIFY_CONTRACT = (
    "\n【裁决契约】\n"
    "- `items` 不可为空（空会被 done 拒收，quote_check_failed）。\n"
    "- `verdict`：顶层 pass|block；每项 pass|fail。顶层写 block 时至少要有一项 fail。\n"
    "- `source`：源文件相对本书根目录的路径。\n"
    "- `quote`：**fail 项必填**——源文件里连续 12-30 字的逐字原文，"
    "严禁省略号跳跃、严禁改写（修复指令要落到具体字句上，必须有据）。\n"
    "  pass 项可省略引文，但**整份裁决至少要有一条命中原文的 quote**，"
    "用来证明你真读了源文件。\n"
    "- `level`：A=提示（只记录不阻断），B=硬伤（打回重写），缺省按 B。\n"
    "- `fix`：verdict=fail 时写清具体修复指令。\n"
)


def _apply_verify_contract(specs: dict[str, dict]) -> dict[str, dict]:
    """给所有 verify 节点补上裁决契约（幂等）。"""
    for spec in specs.values():
        if spec.get("kind") != "verify":
            continue
        prompt = spec.get("prompt")
        if isinstance(prompt, str) and "【裁决契约】" not in prompt:
            spec["prompt"] = prompt.rstrip() + "\n" + VERIFY_CONTRACT
    return specs


def _spec_cfg() -> dict:
    """读 config.json。**不 import inline_runner**——它反向 import 本模块，会成环。"""
    override = os.environ.get("WORKBENCH_CONFIG")
    path = Path(override) if override else (Path(__file__).resolve().parent.parent / "config.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _action_chain_block(b: str, n: int) -> str:
    """把 N15 删除测试真正需要的探针数据注入提示词。

    **为什么要有**（2026-11-15 实测）：原提示词写「探针会给出本章最长的 3 条纯动作链」，
    却从不注入任何链，也不给 action_chain_max。judge 拿不到数据，又要遵守
    「action_chain_max < 3 本项直接 pass」，于是**编造该条件**来免检——
    历史 `_dingzhi_bumped/judgments_C1.N15.json` 就写着「action_chain_max<3，直接pass」，
    而同目录 findings_C1.json 的真实值是 6（C2=4、C3=6、C4=7）。
    删除测试因此被系统性假通过。这里把真值摊开，堵掉编造空间。
    """
    cfg = _spec_cfg()
    py, probe = cfg.get("PYTHON"), cfg.get("CLARITY_ORG_PROBE")
    hits = sorted(Path(b).glob(f"正文/第{n:03d}章_*.md"))
    if not (py and probe and hits):
        return ("【探针数据缺失】未能运行 clarity_org_probe（配置或正文缺失）。\n"
                "**不得假设 action_chain_max < 3**：请自己在正文里定位连续纯动作句，"
                "按下面规则逐句做删除测试，并在 summary 里说明你是人工定位的。\n")
    try:
        r = subprocess.run([py, probe, str(hits[0])], cwd=b,
                           encoding="utf-8", errors="replace", capture_output=True,
                           timeout=120,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        payload = (r.stdout or "").split("--- JSON ---", 1)[-1]
        rows = json.loads(payload)
        row = rows[0] if isinstance(rows, list) and rows else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return ("【探针数据缺失】clarity_org_probe 运行失败。\n"
                "**不得假设 action_chain_max < 3**：请自己定位连续纯动作句做删除测试。\n")
    acm = row.get("action_chain_max")
    chains = row.get("action_chains") or []
    if not isinstance(acm, int):
        return ("【探针数据缺失】探针未返回 action_chain_max。\n"
                "**不得假设 action_chain_max < 3**，请自己定位后判定。\n")
    lines = [f"【探针实测数据（clarity_org_probe，已跑，不要自行假设）】",
             f"action_chain_max = {acm}"]
    if acm < 3:
        lines.append("→ 按规则本项**直接 pass**，无需做删除测试。")
    elif not chains:
        lines.append("→ action_chain_max ≥ 3 但探针未给出链摘录，请自己在正文定位后做删除测试。")
    else:
        lines.append(f"→ 必须对下面 {len(chains)} 条链逐句做删除测试：")
        for i, c in enumerate(chains, 1):
            lines.append(f"  链{i}（{c.get('length')} 句，起始句序 {c.get('start_sent')}）："
                         f"{c.get('excerpt')}")
    return "\n".join(lines) + "\n"

# ─── 正文文件判定（单一事实源，JS 侧镜像在 server/book-files.mjs）────────────
# T-2：改稿流程会产出 `第NNN章_标题_原稿_<日期>.md` 备份稿，它不是本章现役正文。
#       上游 wordcount_core.py:279 一直在排除（`if "_原稿_" in name: return None`），
#       本仓库一处都没排除 → 备份稿进 chapters 表、进全文合成、进字数统计。
# T-2b：章号解析四处各写一遍 `\d{3}`，只认三位补零。个人作者导入的
#       `第1章_开局.md` / `第10章：供奉堂` 被静默忽略 → 该章在 DB / 书架 / 字数 /
#       全文里彻底隐形（与 DL-01 叠加即完整的静默缺章链）。
#       现按 TDD §T-2b：章号认 1–4 位，分隔符认 `_` / 空格 / `：` / `:`。
_CHAPTER_FILE = re.compile(r"^第(\d{1,4})章(?:[_ 　:：](.*))?\.md$")


def is_backup_body_file(name: str) -> bool:
    """备份稿判定：`_原稿_` 中缀、`.bak`/`~` 后缀、`_backup`。"""
    return (
        re.search(r"_原稿(?:_|\.)", name)
        or re.search(r"\.(?:bak|backup)(?:\.md)?$", name, re.IGNORECASE)
        or re.search(r"_backup(?:_|\b)", name, re.IGNORECASE)
        or name.endswith("~")
    )


def parse_body_file_name(name: str) -> tuple[int, str] | None:
    """从正文文件名解析 (章号, 标题)；不是章节文件返回 None。"""
    m = _CHAPTER_FILE.match(name)
    if not m:
        return None
    n = int(m.group(1))
    if n < 1:
        return None
    return n, (m.group(2) or "")


def chapter_number_in_body(text: str) -> int | None:
    """取正文开头声明的章号（若声明了）。

    TDD §T-2b 规则 2：章号以正文标题内的「第N章」为准，文件名前缀只作辅助。

    只看**首个标题行**（`# …`）而不是正文任意位置：正文里出现「第3章的伏笔」
    这类回叙引用不代表本章是第 3 章。首个标题行没有章号时返回 None——
    真实夹具首行常是「# 榜上换了名」这种纯标题。
    """
    head = text[:400]
    heading = re.search(r"^\s*#{1,6}\s*(.+)$", head, re.MULTILINE)
    scope = heading.group(1) if heading else ""
    m = re.search(r"第\s*(\d{1,4})\s*章", scope)
    if not m:
        return None
    n = int(m.group(1))
    return n if n >= 1 else None


def body_files(book: Path) -> list[Path]:
    """列出该书全部现役正文文件，按章号升序。

    备份稿排除在外。同一章有多个现役稿时全部返回——由调用方判歧义，
    不在这里替作者挑一个。
    """
    body = Path(book) / "正文"
    if not body.is_dir():
        return []
    out: list[tuple[int, str, Path]] = []
    for p in body.iterdir():
        if not p.is_file() or p.suffix != ".md":
            continue
        parsed = parse_body_file_name(p.name)
        if parsed is None or is_backup_body_file(p.name):
            continue
        out.append((parsed[0], p.name, p))
    return [p for _, _, p in sorted(out, key=lambda x: (x[0], x[1]))]


def body_files_in(book: Path, start: int, end: int) -> list[Path]:
    """第 start–end 章的现役正文文件（按章号）。"""
    return [p for p in body_files(book)
            if start <= parse_body_file_name(p.name)[0] <= end]


def body_file_for_chapter(book: Path, chapter: int) -> list[Path]:
    """某章的现役正文文件（0 个 = 缺章，多个 = 歧义，调用方自己处理）。"""
    return [p for p in body_files(book)
            if parse_body_file_name(p.name)[0] == chapter]

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
            "你是{PLATFORM}系选题参谋。任务：写一份符合{PLATFORM}商业签约标准的选题报告。\n"
            "第一步，只读这三份资料（用 read_file，路径照抄即可）：\n"
            "  {QIMAO}/01_投稿方案.md   （站点门槛 / 收稿范围 / 签约方式与钱）\n"
            "  {QIMAO}/02_写作方案.md   （平台调性 / 开篇四关 / 编辑桑葚审稿指南）\n"
            "  {QIMAO}/harness/genre-baselines.json  （爽文/年代两组的实测基准）\n"
            "若 {BOOK}/大纲/_商业策划方案.md 存在，必须首要读取并提取其中的成熟商业模版设定。\n"
            "第二步，读完立即用 write_file 把报告写入 {BOOK}/大纲/选题报告.md。"
            "不要浏览其他目录，不要读 _pipeline/ 下的任何文件。\n"
            "报告要求：≥600 字；含 5 个题材候选，按客观标准打分：平台收稿匹配度 / 榜单热度 / "
            "创新配比(90%套路+10%创新) / 是否避开已写烂的梗 / 与年代题材基准兼容性；"
            "必须含「结论」一节，明确给出选定题材与理由。\n"
            "【商业过签硬指标（结论必须包含以下三项，否则 N04 必判 B 级硬伤）】\n"
            "1. **商业吸量书名（3 个候选）**：按七猫下沉市场最火爆的【动词+身份+反差+金手指】结构（如《重回七九：抢我顶职名额，反手掀翻全厂假账》），严禁散文式抽象书名。\n"
            "2. **黄金三章生死节拍设计**：明确规划前 3 章的剧情递进：\n"
            "   · 第 1 章：前 200 字窒息压迫（受辱点/生存绝境），第 1200 字金手指觉醒，章末卡点断章；\n"
            "   · 第 2 章：小试牛刀，制造对手/配角的严重误解与轻视；\n"
            "   · 第 3 章：大庭广众公开反杀打脸，拿足实质收益，全场配角目瞪口呆倒吸凉气（围观震惊流），章末引出新危机。\n"
            "3. **核心受害者痛感与首战反派**：写明谁在压迫主角、抢夺了什么、主角第一次反杀打掉谁。\n"
            "【打分的硬规矩（2026-11-19 冷启动实测：红队在这两条上判了 B 级硬伤）】\n"
            "· 每个维度必须写明**分数的出处**：本地语料实证 / 平台资料 / 纯推断。"
            "**纯推断的维度不得参与排序淘汰**，只能作定性参考——一个自认是推断的分数"
            "不能用来给候选定生死。\n"
            "· 同一维度的所有候选必须**用该维度自己的定义**评分，候选之间必须可比。"
            "若某候选在该维度的分数理由与该维度的定义不符（例：热度列定义是供给密度/"
            "稀缺度，却用「流量好做」当理由），改分或改理由。\n"
            "· 所有数字必须**算得出来**。不要用「≈」掩盖算错（例：2 万字 / 12 章 = 1667，"
            "不是 2000）；同一本书的体量只留一套数。\n"
            "· 若正文声明「无法实证的判断一律登记 A 级不阻断」，那就不能让这些判断进加权表。\n"
            "若 _pipeline/judgments_N04.json 存在且 verdict=block，先读它，按其中 fail 项的 fix 意见针对性改写后再写入。\n"
            "写完只用 ≤100 字回执，不要把报告正文复述在回执里。"
        ),
    },
    "N04": {
        "id": "N04", "kind": "verify", "model": "reasoning", "role": "judge", "name": "选题闸门",
        "evidence": "_pipeline/judgments_N04.json", "min_size": 200,
        "prompt": (
            "你是{PLATFORM}签约评审（红队模式）。只读 {BOOK}/大纲/选题报告.md，"
            "对照 {QIMAO}/01_投稿方案.md 的收稿范围与「一稿一编辑、没过签可一直投」的约束。\n"
            "先输出「这个选题最可能被{PLATFORM}编辑拒掉的 3 个理由」，再做裁决。\n"
            "写入 {BOOK}/_pipeline/judgments_N04.json：\n"
            '  {"verdict":"pass|block","summary":"...","items":[{"criterion":"...","quote":"报告原文连续12-30字","source":"大纲/选题报告.md","verdict":"pass|fail","level":"A|B"}]}\n\n**level 决定是否阻断（重要）**：\n  \u00b7 level \"B\"（默认）= 硬伤，有它就打回重写\n  \u00b7 level \"A\" = 风险提示/待观察，只记录不阻断\n  你列的风险类发现（如热度无实证、留存未知）**必须标 A**，\n  否则会被当成硬伤强制打回——只有真正的硬伤才标 B。\n**`items` 不可为空**——裁决项为空会被 done 拒收（quote_check_failed）。\n'
            "**B 级硬伤的判断准则**：\n"
            "  · 商业吸量度硬伤：书名平淡散文化、缺少动词与金手指标签，或未规划黄金三章生死节拍 → 标 B（七猫编辑一眼拒）。\n"
            "  · 该发现**进了加权表并影响了候选的胜负** → 标 B（硬伤）。"
            "一个自认是推断的分数被用来定生死，就不能只算风险。\n"
            "  · 报告自称「无法实证的一律登记 A 级不阻断」，但这些判断其实进了加权表 → "
            "标 B（报告两头占）。\n"
            "  · 同一维度里不同候选的分数理由与该维度自己的定义不符 → 标 B（不可比）。\n"
            "  · 报告里的数字算不出来（例：2 万字 / 12 章 = 1667 却写成 2000）→ 标 B。\n"
            "  · 其余定性参考、热度留存未实证但未进加权表的发现 → 标 A。\n"
            "fail 项的 quote 必须是报告中连续原文，严禁省略号跳跃。回执 ≤100 字。"
        ),
    },
    "N05": {
        "id": "N05", "kind": "llm", "model": "default", "role": "writer", "name": "设定",
        "evidence": "设定/题材定位.md", "min_size": 300,
        "prompt": (
            "读取 {BOOK}/大纲/选题报告.md 的结论一节，产出 5 个设定文件：\n"
            "  设定/题材定位.md  设定/文风.md  设定/角色.md  设定/世界观.md  设定/势力.md\n"
            "{ONE_LINE}\n"
            "文风.md 必须引用 {QIMAO}/harness/genre-baselines.json 中**本书 genre 对应组**的"
            "实测区间（句长中位 / 段每千字 / 对话占比 / 因果密度），把数值抄进去，不要用别处的数字。\n"
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
            "世界观是否过度庞杂（{PLATFORM}清单：设定太庞杂是硬伤）、文风是否与本书 genre 的基准兼容。\n"
            "先输出 3 个最可能被拒的理由再裁决。写入 {BOOK}/_pipeline/judgments_N06.json"
            "（**必须用下面这个结构，不要自创键名**）：\n"
            '  {"verdict":"pass|block","summary":"...","items":[{"criterion":"...",'
            '"quote":"设定文件里连续12-30字原文","source":"设定/文风.md",'
            '"verdict":"pass|fail","level":"A|B","fix":"修复指令"}]}\n'
            "level \"B\"（默认）= 硬伤，有它就打回重写；level \"A\" = 提示，只记录不阻断。\n"
            "fail 项的 quote 必须是某个设定文件里**连续 12-30 字原文**，严禁省略号跳跃；"
            "source 填该文件名（如 `设定/文风.md`）。\n"
            "**`items` 不可为空**——裁决项为空会被 done 拒收（quote_check_failed）。\n"
            "回执 ≤100 字。"
        ),
    },
    "N07": {
        "id": "N07", "kind": "llm", "model": "default", "role": "writer", "name": "卷纲",
        "evidence": "大纲/卷纲_第一卷.md", "min_size": 800,
        "prompt": (
            "读取 {BOOK}/设定/ 全部文件，产出 {BOOK}/大纲/卷纲_第一卷.md：\n"
            "  主线一句话（必须能一句话说清「主角要做什么、谁在挡」）\n"
            "  卷末高潮 / 13 万字配比 / 每 3 章一个爽点峰值\n"
            "  黄金三章商业打脸闭环：前 3 章必须形成完整的反转与收益闭环（第 1 章压迫觉醒、第 2 章试手反差、第 3 章当众打脸翻盘）\n"
            "  悬念接力表：每个大悬念的埋设章与揭晓章，确保任一时刻并行钩子 ≥1.75\n"
            "若 _pipeline/judgments_N08.json 存在且 verdict=block，先读它，按其中 fail 项的 fix 意见改写后再写入。\n"
            "回执 ≤100 字。"
        ),
    },
    "N08": {
        "id": "N08", "kind": "verify", "model": "reasoning", "role": "judge", "name": "主线闸门",
        "evidence": "_pipeline/judgments_N08.json", "min_size": 200,
        "prompt": (
            "只读 {BOOK}/大纲/卷纲_第一卷.md。核心判据有两条：\n"
            "  1. **主线能否用一句话说清「主角要做什么、谁在挡」**（说不清 = 前 6000 字主线不清晰，约 80% 评估不通过，标 B）\n"
            "  2. **黄金三章打脸闭环**：前 3 章是否具备完整的受辱-反转-打脸兑现规划。若前三章纯介绍背景、无直接爽点打脸，标 B！\n"
            "顺带查：悬念接力表是否存在空窗（大悬念解决后 3 章内无新钩子接入）。\n"
            "先输出 3 个拒稿理由再裁决。写入 {BOOK}/_pipeline/judgments_N08.json，"
            "{\"verdict\":\"pass|block\",\"summary\":\"...\",\"items\":[{\"criterion\":\"...\","
            "\"quote\":\"卷纲连续12-30字原文\",\"source\":\"大纲/卷纲_第一卷.md\",\"verdict\":\"pass|fail\","
            "\"level\":\"A|B\",\"fix\":\"修复指令\"}]}。\n"
            "level \"B\"（默认）= 硬伤，有它就打回重写；level \"A\" = 提示，只记录不阻断。\n"
            "**`items` 不可为空**——裁决项为空会被 done 拒收（quote_check_failed）。\nfail 项的 quote 必须是卷纲里连续原文，严禁省略号跳跃。回执 ≤100 字。"
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
            "你是{PLATFORM}签约评审（红队模式）。这是**终审**，不写稿、不改稿。\n"
            "读取：\n"
            "  {QIMAO}/03_评分卡.md                 （判定项与 B/A 级别，唯一标准）\n"
            "  {QIMAO}/01_投稿方案.md               （收稿范围 / 签约方式与钱）\n"
            "  {BOOK}/投稿包/全文.md                 （整合后的交付文本）\n"
            "  {BOOK}/_pipeline/endgame_findings.json（N18/N19 的机械扫描结果）\n"
            "  {BOOK}/设定/角色.md                   （人物登记表，判「未知人物」）\n"
            "  {BOOK}/追踪/_tracking-state.json      （伏笔账本）\n\n"
            "【分工】机械项以 endgame_findings.json 为准，不要自己重算：\n"
            "  5–7 文风、12–13 AI/退化、17–19 字数与整合。\n"
            "你只判需要读文本的项：\n"
            "  B 级（任一 fail → verdict=block）：1–4 开篇四关、8–9 场景切换/过渡、"
            "14 段落重复、16 未知人物、20 商业张力与爽点兑现（第 1 章前 500 字无极端矛盾、前 3 章无配角震惊/对手破防、章末泄气无断章悬念，必判 B）；\n"
            "  A 级（只记录，不阻断）：10–11 主线收束/利用率、15 题材漂移。\n\n"
            "先输出「最可能被{PLATFORM}编辑拒稿的 3 个理由」，再逐项裁决。\n"
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
            "**`items` 不可为空**——裁决项为空会被 done 拒收（quote_check_failed）。\nfail 项的 quote 必须是正文连续原文，严禁省略号跳跃。回执 ≤100 字。"
        ),
    },
    "N20f": {
        "id": "N20f", "kind": "llm", "model": "default", "role": "writer", "name": "外科手术补丁",
        "evidence": "_pipeline/n20f_patch_log.json", "min_size": 20,
        "prompt": (
            "读取 {BOOK}/_pipeline/endgame_findings.json 与 {BOOK}/_pipeline/judgments_N20.json。\n"
            "**只改 正文/ 下被点名的章节**，做段落级外科手术：按 findings 的 fix 逐条修，"
            "不重写整章、不改剧情、不改人物关系、不新增/删除章节文件。\n"
            "【商业平淡与张力修复配方】\n"
            "  · 若被指出「缺少配角震惊/爽点不落地」：在主角反击或打脸得胜处，补入 2 段围观群众目瞪口呆、全场倒吸凉气、对手面如死灰的失态描写。\n"
            "  · 若被指出「开篇前200字平淡」：删掉开头的天气风景铺垫，把后面的剧烈矛盾冲突直接提到第一段第一句。\n"
            "  · 若被指出「章末泄气无悬念」：删掉结尾的洗漱睡觉琐事，改写为危机逼近、对峙戛然而止或重大悬念问句收尾。\n"
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
        "写入 {BOOK}/_pipeline/judgments_N20g.json"
        "（**必须用下面这个结构，不要自创键名**）：\n"
        '  {"verdict":"pass|block","summary":"...","items":[{"criterion":"...",'
        '"quote":"概要里连续12-30字原文","source":"投稿包/全书概要.md",'
        '"verdict":"pass|fail","level":"A|B","fix":"修复指令"}]}\n'
        "level \"B\" = 硬伤（跨卷主线断裂、伏笔结构性未回收），\"A\" = 提示。\n"
        "fail 项的 quote 必须是概要里的连续原文；**items 不可为空**（空会被 done 拒收）。\n"
        "回执 ≤100 字。"
    ),
}

# 全局 verify 节点统一补裁决契约（N04/N06/N08/N20g 等）。
_apply_verify_contract(GLOBAL_NODES)


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


def _prev_chapter_digest(st: dict, book: Path, n: int, limit: int = 3) -> str:
    """前序章节的压缩摘要，注入 N09 提示词（2026-11-17 加）。

    **为什么要有**：N09 提示词此前只让 writer 去读目录/卷纲/角色表，
    **没有给前文摘要**。于是 writer 只能自己翻正文文件对齐前情——
    实测第 8 章细纲的 subagent 连死两次（agent-37/38），
    成功的第 4、5、7 章都是我**在提示词里手写前序章节摘要**时写的。
    也就是说：这份上下文本该由工具提供，此前一直靠编排者手工补——
    这正是「工具跑得慢/跑不通」的一个隐藏来源。

    这里从 `追踪/_tracking-state.json` 的 wordcount_records 与伏笔账本里取，
    不读正文（避免撑爆上下文），生成「每章一句 + 伏笔状态」的紧凑摘要。
    """
    if n <= 1:
        return ""
    try:
        state_path = book / "追踪" / "_tracking-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    recs = state.get("wordcount_records") or {}
    ledger = state.get("foreshadow") or {}
    chars = state.get("characters") or {}
    lines: list[str] = []
    for k in sorted(recs, key=lambda x: int(x) if str(x).isdigit() else 0):
        if not str(k).isdigit():
            continue
        ci = int(k)
        if ci >= n or ci < n - limit:
            continue
        v = recs.get(k) or {}
        lines.append(f"第{ci}章｜已封板 {v.get('actual')} 字")
    if not lines:
        return ""
    # 伏笔状态（只列未回收的，已回收的不必占地方）
    due = []
    for fid, row in sorted(ledger.items()):
        if not isinstance(row, dict):
            continue
        if row.get("status") in ("已回收", "放弃", "已过期"):
            continue
        planned = row.get("planned_resolution_chapter")
        mark = f"计划第{planned}章回收" if isinstance(planned, int) else "回收章未定"
        due.append(f"{fid}｜{str(row.get('summary') or '')[:40]}｜{mark}")
    out = "【前序章节与伏笔状态（不必自己翻正文，以下即当前状态）】\n"
    out += "\n".join(lines)
    if due:
        out += "\n未回收伏笔：" + "；".join(due)
    # 角色当前状态：writer 不知道「陆明现在是什么身份、陆建国停职了没有」就会写偏
    if chars:
        out += "\n角色当前状态："
        for name, s in list(chars.items())[:6]:
            if isinstance(s, dict):
                out += (f"\n  {name}｜{str(s.get('identity') or '')[:24]}"
                        f"｜{str(s.get('location') or '')[:18]}"
                        f"｜{str(s.get('state') or '')[:36]}")
    return out + "\n"


def _partial_body_digest(st: dict, book: Path, n: int, limit: int = 1200) -> str:
    """本章已有正文（若 writer 中途死掉留下半成品）的尾部注入。

    **为什么要有**：实测第 8 章的 writer 写到 1511 字就卡死在**高潮对峙戏**
    （双方台词互顶，最难写的段落），磁盘上留下半截正文就再也动不了。
    此前没有任何机制能接手：新的 subagent 若从零重写会丢掉已有内容，
    编排者只能手工续写——这正是「工具跑不动」的一个隐藏来源。

    这里把已有正文的**尾部**（含最后一段）交给下一个 writer，
    让它接着往下写，而不是从头再来。
    """
    try:
        files = body_file_for_chapter(book, n)
    except Exception:
        return ""
    if not files:
        return ""
    try:
        text = files[0].read_text(encoding="utf-8")
    except OSError:
        return ""
    # 自己算可见字数，不依赖外部 wordcount_core（那个模块在 story-long-write
    # 目录下，pipeline_spec 不该在渲染提示词时去 import 它——实测直接 ImportError）。
    cur = sum(1 for ch in text if ch not in " \t\r\n　")
    if cur <= 0:
        return ""
    tail = text[-limit:]
    return (f"【本章已有正文 {cur} 字——上一轮 writer 中途停下了。**接着往下写，不要从头重写。**】\n"
            f"以下是目前正文的末尾（从「……{tail[-limit:].strip()[:200]}」）：\n"
            f"---\n{tail}\n---\n"
            f"续写要求：① 从上面最后一句的**下一句**接着写，不要复述已有内容；"
            f"② 按细纲剩余的拍继续推进；③ 写完后本章总字数落在 2000-{CHAPTER_BODY_MAX_CHARS} 可见字。\n")


def _partial_outline_digest(st: dict, book: Path, n: int, limit: int = 1500) -> str:
    """本章已有细纲（若 writer 中途死掉留下半成品）的断点注入。

    **为什么要有**：N09 细纲包含 9 个字段、4 个小节与情节细化表格，文本通常在 10KB-18KB。
    若 subagent 中途崩溃或超时，磁盘上可能已写出部分字段（如前九项或情节安排）。
    如果没有续写注入，新的 subagent 从零重写不仅极易再次超时，还容易遗失前序构思。
    这里检测已有细纲，若已有半成品（> 200 字节且缺少关键结尾小节），提供断点摘要并要求继续补完。
    """
    path = book / "大纲" / f"细纲_第{n:03d}章.md"
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    cur = sum(1 for ch in text if ch not in " \t\r\n　")
    if cur < 50:
        return ""
    if "### 情节细化" in text and cur > 1500:
        return ""
    tail = text[-limit:].strip()
    return (f"【本章细纲已有草稿 {cur} 字——上一轮 writer 中途停下。**请基于已有结构补完，避免从头重写耗尽轮次。**】\n"
            f"已写出内容的尾部如下：\n"
            f"---\n{tail}\n---\n"
            "续写要求：① 检查并保留已有合法字段；② 补齐缺失的小节与情节细化表格；③ 控制总字节在 17600 字节以内。\n\n")


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
            "max_attempts": 3,
            "evidence": f"大纲/细纲_第{n:03d}章.md", "min_size": 500,
            "prompt": (
                f"{_partial_outline_digest(st, book, n)}"
                f"读取 {_outline_ref(st, n)}、{_volume_outline_ref(st, n)}、"
                f"{b}/设定/角色.md，并读权威细纲模板 "
                f"{{STORY_LONG_WRITE}}/references/workflow-outline.md，"
                f"产出 {b}/大纲/细纲_第{n:03d}章.md。\n"
                "硬约束（check-outline-contract.js 逐项机械验收，缺一项即打回）：\n"
                f"1. 九个必填字段各占一行，格式「- 字段名：值」：核心事件 / 字数目标（固定 {CHAPTER_TARGET_WORDS}字）/ "
                "字数口径（visible_chars_v1）/ 单元ID/位置 / 目标情绪 / 主角目标/关键选择 / "
                "章首钩子 / 爽点 / 本章禁止提前释放；\n"
                "2. 「目标情绪」「主角目标/关键选择」必须写实际内容，不接受 [待补充]；\n"
                "3. 四个小节标题（### 开头）：内容概括 / 情节安排 / 人物关系和出场顺序 / 情节细化；\n"
                "4. 内容概括写全五段：起因 / 发展 / 转折 / 高潮 / 结尾；\n"
                "5. 情节细化用表格，表头为：# / 情节点 / 功能标签 / 执行边界（禁＋放）；\n"
                "6. 反引号引用的项目内 .md 路径必须真实存在，不许杜撰。\n"
                "内容硬规则：3–6 拍；第 1 拍的前 200 字内必须有冲突；全章只推进一条主线；"
                f"目标正文至少 {CHAPTER_BODY_MIN_WORDS} 字；字数目标和明确范围下限均不得低于该门槛。\n"
                "章末出口必须是钩子。\n"
                "7. **商业情绪反差与围观震惊（消灭平淡）**：\n"
                "   - 细化表格的情节点必须标注【情绪走势】（压抑/蓄力/反转/舒爽）；\n"
                "   - 反派言行必须有具体的【刺痛侮辱细节】；\n"
                "   - 反击后必须设计【配角反馈/围观震惊槽位】（指明谁在看、谁目瞪口呆或全场死寂）；\n"
                "   - 最后一拍必须是【断章悬念】（危机未解或反击前夕，严禁平淡收工睡觉）。\n"
                "**「本章禁止提前释放」只写本章特有的 3–5 条**：卷级常任禁忌写在卷纲里，"
                "不要逐章复读（超过 5 条会被判 outline.release-brevity）。\n\n"
                "【体量硬规矩：细纲是场景卡，不是散文稿】\n"
                "  全文字节数**不得超过「字数目标 × 8」**（2200 字目标 → 17600 字节上限）。\n"
                "  实测 5 章数据：细纲体量与「正文照搬细纲」的比例强正相关（r=0.814）——\n"
                "  细纲 2378 / 13691 / 15286 字节时照搬率 0.0% / 1.5% / 0.9%，\n"
                "  而 20540 / 22235 字节时照搬率跳到 8.6% / 6.9%。**细纲写得越细，正文越是照抄。**\n"
                "  所以：每个情节点只写「谁做了什么 / 功能标签 / 禁+放」各一句话，不要展开成叙述；\n"
                "  不要复述卷纲；不要写环境描写与人物心理；复沓锚句只列原话本身，不加解释。\n"
                f"{_prev_chapter_digest(st, book, n)}\n"
                "  回执 ≤100 字。"
            ),
        },
        f"C{n}.N10": {
            "id": f"C{n}.N10", "kind": "script", "name": "细纲闸门",
            "evidence": f"大纲/细纲_第{n:03d}章.md", "min_size": 1,
        },
        f"C{n}.N11": {
            "id": f"C{n}.N11", "kind": "script", "name": "章节上下文组装",
            "evidence": "追踪/写作上下文.md", "min_size": 200,
        },
        f"C{n}.N12": {
            "id": f"C{n}.N12", "kind": "llm", "model": "default", "role": "writer", "name": "写正文+填追踪事务",
            "evidence": f"正文/第{n:03d}章_*.md", "min_size": 1800,
            "max_attempts": 4,
            "prompt": (
                f"读取：\n"
                f"  {b}/大纲/细纲_第{n:03d}章.md          （场景卡，按拍写）\n"
                f"  {b}/追踪/写作上下文.md                    （S1-S7 七栏：场景卡/上一章锚点/主线/角色状态/文风锚/禁写约束/事务说明；**只读**）\n"
                f"  {b}/.story/work/第{n:03d}章/tracking.json   （预填事务，按下面的形状填）\n"
                f"  {b}/.story/work/第{n:03d}章/tracking-draft.json   （**draft 工具自己的输出**："
                f"shapes=该章每类条目的权威形状、current_snapshots=上一章的角色快照"
                f"（**整份复制再改成本章结束时的状态**）、fill=逐字填法说明、limits_chars=字节上限）\n"
                f"  {{QIMAO}}/harness/style-anchor.json    （文风锚，取 2 段正例切片）\n"
                f"  {{SKILL}}/references/writing-contract.md    （**手法手册，先读**）\n"
                f"  {{SKILL}}/references/tracking-transaction.md   （事务形状全集，**形状问题查这里**）\n\n"
                f"{_partial_body_digest(st, book, n)}"
                f"写出 {b}/正文/第{n:03d}章_<标题>.md，单章 **{CHAPTER_BODY_MIN_WORDS}–{CHAPTER_BODY_MAX_CHARS} 可见字**；"
                f"细纲字数目标至少 {CHAPTER_BODY_MIN_WORDS} 字，验收目标 {CHAPTER_TARGET_WORDS} 字；明确范围的下限不得低于 {CHAPTER_BODY_MIN_WORDS} 字。\n"
                "**字数上限以本章细纲的「字数范围」为准**：细纲写了 2000-2400 就按 2400 交，"
                "写了 2000-2600 就按 2600 交。上面这个 2600 只是缺省值——细纲范围更严时按细纲来，"
                "因为 N13/N16 是按细纲范围判的。\n\n"
                "【字数是硬闸门，**上限同样会卡**】N12 交付前的预检按 `visible_chars_v1` 口径"
                f"（汉字＋标点）计字数，**超出 {CHAPTER_BODY_MAX_CHARS} 直接判 over 并打回**。"
                "写完必须回读文件核对：字数落在带内再交付。**不要写超**——宁可少写一句环境，"
                "也不要多写一段铺陈。\n\n"
                "【这些句式会被判 blocking 逐条打回，写之前就避开】\n"
                "  1. **「不是 X，是 Y」/「是 X，不是 Y」对比句式** —— 直接写 Y，或改成动作/细节呈现\n"
                "  2. **「声音不大/不高……却/但……」音量反差腔** —— 直接写声音造成的现场效果（谁停了手、哪排安静了）\n"
                "  3. **破折号「——」** —— 一律不用。打断用动作 beat 或短句，拖长音改用短停顿，"
                "插入说明用逗号/冒号\n"
                "  4. **省略号「……」** —— 一律不用，改逗号或句号\n"
                "以上四类是机械闸门的 blocking 项，写进去就要被打回重写，白烧轮次。\n\n"
                "【不要照抄细纲原句】细纲锁的是功能与结果，不是句子。连续 15 字以上的照搬会被"
                "「细纲照搬检测」记为 advisory。系统面板、誓词、案卷原话、固定专名等功能性重合可以保留，"
                "其余句子都要在正文现场重写。\n\n"
                "【写入方式（**必须分段写**）】单次 write_file 的 content 控制在 1000–1200 字以内。\n"
                "  第一次 write_file（不带 append）写开头，之后每次 write_file 设 append=true 追加，\n"
                "  分 2–3 次写完整章。一次性输出整章会触到模型长度上限、整段作废、白烧轮次。\n"
                "  **不要写别的文件**：`追踪/写作上下文.md` 是脚本生成的只读输入，改写它会让下一章\n"
                "  读到缺栏的坏文件（已有守卫会拦下判失败）。只写 正文/第NNN章_*.md 与\n"
                "  .story/work/第NNN章/tracking.json 这两个文件。\n\n"
                "【核心差异】被拒稿写「然后」，签约作品写「所以」。\n"
                "  反例：「陈志海绕到后头去抬，王婶喊了一声慢，三个人这才把车顺下去。」——只有事件\n"
                "  正例：「正因为空得太干净，他才确信这一栏不是没人填，而是被人抹掉过。」——有因果\n\n"
                "【四个必用手法】\n"
                "  1. 段落收尾法：每段最后一句必须回答「所以呢」，用 这才/原来/果然/难怪/怪不得/正因为 收。\n"
                "     目标：带因果词收尾的段落 **约 1/4–1/3**（不是 60%——见下）。\n"
                "     **密度是区间不是上限**：因果密度要落在 **1.0–6.0/千字**（下限也要够）。\n"
                "     闸门只数 16 个词（因为/所以/于是/因此/结果/这才/可见/原来/怪不得/难怪/既然/\n"
                "     要是/如果/否则/不然/为此），2200 字的稿子**全章最多 13 个**被计分的因果词；\n"
                "     而 73–119 段的稿子若要 60% 的段落带因果词，需要 44 个词——**算不通**。\n"
                "     所以：其余段落用**不计分**的后果句收尾（反倒/到底/毕竟/幸好/说到底），\n"
                "     照样回答「所以呢」，又不占密度额度。**密度超 6.0 是硬闸门，会被打回。**\n"
                "     **但必须换着用，不许刷同一个词**：18 个七猫年代签约样本实测，单个因果词\n"
                "     占全部因果词的比重中位 0.43、最大 0.60。**同一个词写满全章是明确的 AI 指纹**\n"
                "     （实测有章节把「果然」刷了 44 次，占比 0.98，因果密度反而达标——指标看不出\n"
                "     来，编辑一眼就看出来）。硬规矩：因果收尾词至少轮换 4 种以上；同一个词在\n"
                "     全章出现超过 3 次就换写法（改成动作后果、改具体细节、改成陈述句）。\n"
                "  2. 心理问句法：每 2–3 段插一个角色内心问句，要具体（「第七行为什么是三？」\n"
                "     而不是「他该怎么办？」）。目标：全章 ≥5 处\n"
                "  3. 动作-因果改写：连续 3 句纯动作必须拆——一半改因果句，一半改「动作+心理」复合句。\n"
                "     但**不要取消动作**（签约作品动作词密度中位是 29，不是 0）\n"
                "  4. 数字落地法：数字后下一句必须写它造成的后果或反应，不许罗列\n\n"
                "  5. 段落切分：每段 18–30 字（1–2 句），对应 33–54 段/千字。\n"
                "     场景切换/动作转换/心理另起都要断段。AI 天然爱写长段，必须主动切。\n\n"
                "【动作节拍密度：本书当前偏高（2026-11-17 实测）】\n"
                "  两本真实七猫拒稿样本（《顶职》《边镇小吏》）的动作节拍密度是 166–250，\n"
                "  14 本签约样本是 32.5–136.3；**本书 8 章是 179–225，落在拒稿区间里**。\n"
                "  真实拒稿样本的共同点：动作很多，但每个动作都没留下痕迹——编辑的说法是\n"
                "  「细节刻画上力度稍弱」。\n"
                "  所以写本章时：**动作要少而重**。一个动作必须改变现场状态、暴露人物意图，\n"
                "  或者让读者记住它；只交代「他做了什么」而不改变任何东西的动作，删掉。\n"
                "  宁可少写两个动作，也不要写满两个不落地的动作。\n"
                "  （该指标判别力只差「拒稿书≥3」这一条门槛，仍只作定位线索，不作硬性指标。）\n\n"
                "【生死线（N13 会卡，**上下限都要**）——下面是本书所属平台的实测区间，不要用别处的数字】\n"
                "{GENRE_BOUNDS}\n\n"
                "【七猫商业签约必达六法（消灭平淡，编辑签约一手红线）】\n"
                "  1. **开篇前 200 字直入危机冲突（第 1 章铁律）**：\n"
                "     第 1 章前 200 字必须直接炸开极端矛盾（逼迫按手印、撕毁证明、当众羞辱、绝境压迫）。\n"
                "     严禁描写天气、风景、晨曦阳光、历史漫长铺陈（平铺描写会被商业探针秒判 block）。\n"
                "  2. **痛感前置与具象化**：\n"
                "     主角受辱或被算计的处境不能是一笔带过的抽象大话，必须写出具体的刺痛细节：\n"
                "     （冰冷的泥水、泛白的指节、撕碎的红头信笺、恶棍肆无忌惮的唾沫星子）。\n"
                "  3. **动作必须带情绪与后果**：\n"
                "     禁止连续 3 句纯动作流水账（“陈砚推开门，走到桌前，拿起茶缸”）。\n"
                "     动作必须改变现场物理状态或暴露杀气心理（“陈砚反手合上门栓，生锈的铁搭扣咔哒锁死，屋里瞬间暗了半截”）。\n"
                "  4. **围观震惊流（爽点落地的唯一证据）**：\n"
                "     主角解决问题、打脸反派或展现实力时，严禁主角自言自语就完了！\n"
                "     **必须紧跟 2–3 句周围配角/对手的生理失态与震撼反应**：\n"
                "     对手反应（脸色铁青、浑身发颤、面如死灰、瘫倒在地、哑口无言）；\n"
                "     围观反应（全场死寂、齐齐倒吸一口凉气、目瞪口呆、窃窃私语、满脸骇然）。\n"
                "     没有围观震撼反应，编辑就会给出「叙述平淡、爽感不足」的退稿结论！\n"
                "  5. **利益与收益具体化**：\n"
                "     反击收益必须是真金白银与具体实物（数字、元、块、转正文件、工牌、灵石、神兵），禁止虚无缥缈的口头获胜。\n"
                "  6. **章末致命断章术（留存率之魂）**：\n"
                "     单章最后一段必须卡在**矛盾高潮前夕、重大突发危机、或者意料之外的反转**处！\n"
                "     可以用悬念问句（“难道是他？！”）、突发变故（“门外猛然传来急促的砸门声……”）收尾。\n"
                "     **严禁泄气下班式结尾**：绝对禁止写“天黑了回宿舍洗漱睡觉”、“一切归于平静”、“今天先到这里”！\n\n"
                "【开篇铁律】第 1 章前 200 字必须是当下冲突，不许环境描写、不许回忆倒叙。\n"
                "【衔接】无缝接上上一章末尾 600 字的语气与场景。\n"
                "【追踪】按下面的**形状**完整填好 tracking.json 事务。"
                "**形状不许自创**——下面的结构就是 tracking_commit 认的形状，"
                "写成别的形状会被拒（且封板时会浪费一整轮）。\n"
                "字段条数上限：continuity_risks ≤5、active_character_names ≤6、long_term_constraints ≤6、"
                "next_chapter_commitments ≤5、recent_chapters ≤3——超出会被 tracking_commit 拒收、N16 封板失败。\n"
                "**控制字段原样保留，一个字都不要改**：schema_version / mode / chapter / "
                "expected_state_revision（改了会被 N16 以 tracking state changed 拒收）；"
                "只填 chapter_title / delta / context.position / character_snapshots。\n\n"
                "【事务形状（照抄字段名，不要改）】\n"
                '  "delta": {\n'
                '    "result": "一句话写清本章结果（≤120字）",\n'
                '    "character_changes": [\n'
                '      {"name": "角色名", "change": "本章他发生了什么变化（≤40字）"}\n'
                '    ],\n'
                '    "foreshadow_changes": [\n'
                '      {"action": "upsert", "id": "F001",\n'
                '       "summary": "伏笔一句话（≤60字）",\n'
                '       "planted_chapter": 1, "planned_resolution_chapter": 3,\n'
                '       "status": "已埋", "importance": "高"}\n'
                '    ],\n'
                '    "timeline_events": [\n'
                '      {"action": "upsert", "id": "E002",\n'
                '       "story_time": "1987年3月某日上午",\n'
                '       "objective_fact": "本章客观发生的事（≤60字）",\n'
                '       "reader_knowledge": "读者此刻知道什么（≤60字）",\n'
                '       "reveal_status": "部分揭示", "reveal_chapter": 2,\n'
                '       "characters": ["陈铁生", "王德贵"]}\n'
                '    ],\n'
                '    "constraints": ["本章确立的约束（字符串数组）"],\n'
                '    "next_chapter_commitments": ["下一章要兑现的承诺（字符串数组）"],\n'
                '    "retired_context_items": [], "retired_characters": []\n'
                '  },\n'
                '  "context": {"position": {"volume": "…", "volume_start_chapter": 1,\n'
                '                "story_time": "本章结束时的故事时间", "scene": "本章结束时的场景"},\n'
                '             "long_term_constraints": ["…"], "active_character_names": ["…"],\n'
                '             "continuity_risks": ["…"]},\n'
                '  "character_snapshots": {\n'
                '    "角色名": {\n'
                '      "identity": "身份（≤40字）", "location": "当前位置（≤40字）",\n'
                '      "goal": "当前目标（≤40字）", "state": "当前状态（≤40字）",\n'
                '      "abilities_resources": ["能力/资源"],\n'
                '      "relationships": ["关系"], "knowledge": ["已知"],\n'
                '      "open_threads": ["未决线索"]\n'
                '    }\n'
                '  }\n'
                "  · `context` 的 commit 事务**只收 4 项**：position / long_term_constraints /\n"
                "  active_character_names / continuity_risks。**recent_chapters 与 "
                "next_chapter_commitments 由工具派生，手填会在写入前被拒。**\n"
                "  · `character_snapshots` 与 `character_changes` 必须是**同一个集合**：\n"
                "  **本章状态有变化的核心角色**。每个进了 character_changes 的核心角色都要有快照，\n"
                "  反过来快照里出现的角色必须都在 character_changes 里。\n"
                "  **没变化的角色不要写进 character_snapshots**——工具会拒\n"
                "  （「character_snapshots must contain exactly the core characters changed」）。\n"
                "  · 快照要**整份**写（identity / location / goal / state / abilities_resources /\n"
                "  relationships / knowledge / open_threads 八项一个不少），不是只写变化的那几项——\n"
                "  提交时是整份覆盖。照 `tracking-draft.json` 的 `current_snapshots` 复制再改最快。\n"
                "**必填项一个都不能空着**：delta.result 要一句话写清本章结果；delta.character_changes 写本章"
                "起了变化的核心角色（至少 1 个）；delta.foreshadow_changes 写本章埋下或推进的伏笔；"
                "delta.timeline_events 写本章客观发生的事；"
                "context.position 的 story_time 与 scene 填本章结束时的故事时间与场景；"
                "character_snapshots 给**本章状态有变化的核心角色**各写一份。"
                "**留空等于没填**：空数组会让 N16 封板失败，届时要重开一轮补，等于白烧。\n"
                "  · **`foreshadow_changes` 只写本章埋下或推进的伏笔**，不是全量台账——没动的伏笔"
                "已经在账本里，不要重列（重列既超字节又没有意义）。\n"
                "  · **`character_snapshots` 必须整份覆盖 `character_changes` 里的每个核心角色**："
                "改了谁的状态，就得给谁写完整的一份快照，漏一个会被判"
                "「core character changed but has no current snapshot」。\n\n"
                "【事务的**形态**也会被机械校验，形态错和没填一样会被拒】\n"
                "  · `delta.foreshadow_changes[].status` 只能是 `已埋` / `已回收` / `已过期` / `放弃` 四个值，"
                "**不许自造**（如「已坐实」）；要表达「本章把伏笔坐实了」，把结论写进 `summary`，status 仍填 `已埋`。\n"
                "  · **`delta` 序列化后硬上限 3072 字节**——写得越详细越容易超。每条 `summary` 控制在 60 字内，"
                "`result` 控制在 120 字内，角色快照每项字段控制在 40 字内。"
                "**宁可短，不要全**；超了会被打回重写，正文一个字都不用改。\n\n"
                "【交付前自检（逐项过，不过先自己改）】\n"
                f"  □ 用 read 回读正文，确认 U+FFFD=0（不要经 PowerShell 转码写中文，会落成乱码）\n"
                f"  □ 回读正文核对字数落在 {CHAPTER_BODY_MIN_WORDS}–{CHAPTER_BODY_MAX_CHARS} 可见字内\n"
                "  □ 全文无破折号「——」、无省略号「……」、无「不是X是Y」对比句式、无「声音不大却」反差腔\n"
                "  □ 回读 tracking.json，确认上面每个必填项都填了、控制字段没动、status 用的是四个合法值之一\n"
                "  □ 回读 tracking.json，确认 delta 序列化后不超过 3072 字节\n"
                "  □ 按 writing-contract.md 第三节自检清单过一遍\n\n"
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
            "max_attempts": 4,
            "skippable": True,  # N13 通过（无 findings）时按方案跳过，直达 N15
            "prompt": (
                f"读取 {b}/_pipeline/findings_C{n}.json（N13 脚本报错与 N15 审查意见已统一汇入）。\n"
                f"读取 {{SKILL}}/references/writing-contract.md 的「四个可操作手法」。\n"
                f"只改 findings 指向的段落，**不重写整章**；改完必须同步更新 "
                f"{b}/.story/work/第{n:03d}章/tracking.json（schema_version / mode / chapter / "
                f"expected_state_revision 四个控制字段原样保留，改了 N16 会拒收）。\n\n"
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
                "  · 字数不足 → 加「一个回合的博弈」，不是加形容词\n"
                "  · COMMERCIAL_TENSION（商业张力不足/爽点未落地）→ 七猫商业定点强化配方：\n"
                "    - 若报「前500字无直接危机/开篇泄气」：将极端危机（逼签/抢名额/按手印/撕破脸/绝境）直接前移到前200字，彻底删掉开头所有风景天气说明。\n"
                "    - 若报「缺少配角震惊反馈或对手破防」：主角反击或亮底牌后，必须立即插入至少1段配角震惊反应（如「倒吸一口凉气」、「全场死寂」、「目瞪口呆」、「一片哗然」）或对手失态（如「脸色煞白」、「脑海嗡的一声」、「气急败坏」）。\n"
                "    - 若报「章末洗漱睡觉泄气收尾」：删除最后一段平淡日常收尾，改断在突发变故、急促敲门声、杀机锁定或危机悬念处（制造章末断章留存）。\n\n"
                "硬规则：同一处失败 2 次就停下报 blocked，不要第 3 次自己猜；不许注水凑字数；不许删剧情。\n"
                "写入方式：单次 write_file 的 content 控制在 1000–1200 字以内；改完的整章分 2–3 次写"
                "（首次不带 append，之后 append=true），避免触到长度上限整段作废。\n"
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
    return _apply_verify_contract(base)


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
    specs = {
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
                f"{b}/_pipeline/judgments_V{v}.N08.json（**必须用下面这个结构，不要自创键名**）：\n"
                '  {"verdict":"pass|block","summary":"...","items":[{"criterion":"...",'
                '"quote":"连续12-30字原文","source":"大纲/卷纲_第N卷.md",'
                '"verdict":"pass|fail","level":"A|B","fix":"修复指令"}]}\n'
                "**items 不可为空**——裁决项为空会被 done 拒收（quote_check_failed）。\n"
                "fail 项的 quote 必须是卷纲里的连续原文。回执 ≤100 字。"
            ),
        },        f"V{v}.N09d": {
            "id": f"V{v}.N09d", "kind": "llm", "model": "default", "role": "writer",
            "name": f"第{v}卷目录", "evidence": f"大纲/目录_第{v}卷.md", "min_size": 400,
            "prompt": (
                f"读取 {b}/大纲/卷纲_第{v}卷.md，产出 {b}/大纲/目录_第{v}卷.md。\n"
                f"**必须正好 {size} 行**（第 {start}–{end} 章），每行一章：\n"
                "  章号 / 一句话事件 / 本章爽点 / 章末钩子 / 预估字数（1800–2400）\n"
                f"第 1 行对应第 {start} 章，第 {size} 行对应第 {end} 章——**行号即章号偏移，不能错位**。\n"
                + (f"另外把本卷前 {min(12, size)} 章另存一份 {b}/大纲/目录_前12章.md（兼容旧工具）。\n" if v == 1 else "")
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
                f"写入 {b}/_pipeline/judgments_V{v}.N20.json（**必须用下面这个结构，不要自创键名**）：\n"
                '  {"verdict":"pass|block","summary":"...","items":[{"criterion":"...",'
                '"quote":"连续12-30字原文","source":"投稿包/卷N_全文.md",'
                '"verdict":"pass|fail","level":"A|B","fix":"修复指令"}]}\n'
                "**items 不可为空**——裁决项为空会被 done 拒收（quote_check_failed）。\n"
                f"source 填 投稿包/卷{v}_全文.md。\n"
                "fail 项的 quote 必须是正文连续原文。回执 ≤100 字。"
            ),
        },
    }
    return _apply_verify_contract(specs)


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
    chains = _action_chain_block(b, n)
    return (
        f"你是{{PLATFORM}}签约评审（红队模式）。只读 {src}。\n"
        f"审查范围：{scope}\n\n"
        f"**铁律：你的修复指令只能作用于第 {n} 章**——{prev_range}已在各自章末封板，"
        "严禁要求推翻它们。若问题只能靠改前序章解决，请判 block 并说明。\n\n"
        "先输出「最可能被{PLATFORM}编辑拒稿的 3 个理由」，再做裁决。\n\n"
        "【删除测试（判「白描 vs 流水账」，机械指标做不到这一层）】\n"
        f"{chains}\n"
        "对上面每条链逐句问：**删掉这一句，后面 3 句内是否出现依赖它的指代、后果或状态变化？**\n"
        "  · 有依赖 → 这句是白描，保留\n"
        "  · 无依赖 → 这句是素写，标记 removable\n"
        "一条链里 removable ≥60% → 该链判 fail。修复指令：改写成「动作+因果」或\n"
        "「动作+细节判断」，**不许删剧情**。\n"
        "**严禁在没有链数据时自行断言「无动作链」或「action_chain_max<3」**——"
        "那等于跳过本项检查；数据缺失时必须改为自己在正文里定位连续纯动作句，"
        "并在 summary 里写明是人工定位。\n\n"
        f"写入 {b}/_pipeline/judgments_C{n}.N15.json：\n"
        '  {"verdict":"pass|block","summary":"...","items":[{"criterion":"...","quote":"正文连续12-30字原文","source":"正文/第NNN章_xxx.md","verdict":"pass|fail","fix":"修复指令（只能落在当前章）"}]}\n'
        "fail 项的 quote 必须是正文连续原文，严禁省略号跳跃。回执 ≤100 字。"
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
    # 卷级 verify（2026-10-25 补）：此前没有 V* 分支，落到 sources=[] 全禁，
    # 导致 V{v}.N08 连它本该审的卷纲都引用不了。
    if node_id.startswith("V") and node_id.endswith(".N08"):
        v = node_id[1:].split(".", 1)[0]
        return {"sources": [f"大纲/卷纲_第{v}卷.md"], "mechanical_ids": set()}
    if node_id.startswith("V") and node_id.endswith(".N20"):
        # 卷末终审读的是合成后的 投稿包/卷{v}_全文.md，不是 正文/。
        # 2026-10-26 修：此前这里写的是 ["正文/"]，而 prompt 要求 source 填
        # 投稿包/卷{v}_全文.md → 每卷末必然 source 不在允许范围，节点永远过不了。
        v = node_id[1:].split(".", 1)[0]
        return {"sources": [f"投稿包/卷{v}_全文.md"], "mechanical_ids": MECHANICAL_N20_IDS}
    if node_id == "N20g":
        # 全书终审只读 投稿包/全书概要.md（不读正文，25 万字会爆上下文）。
        # 2026-10-26 修：此前落到默认分支 sources=[] → 任何文本项都过不了。
        return {"sources": ["投稿包/全书概要.md"], "mechanical_ids": set()}
    if node_id.startswith("C") and node_id.endswith(".N15"):
        # 取证范围 = 提示词要求的**审查窗口**，不是"当前章"。
        # 2026-11-15 修：此前这里只放行 `正文/第{n:03d}章_`，可 N15 提示词对
        # 第 3 章明写「读第 1–3 章，查三章内是否有至少一次反转」、对里程碑章
        # 明写「读 正文/ 下全部已写章节」——judge 按提示词跨章取证就被
        # `source 不在允许范围` 拒掉，节点**永远过不了**。实测 C3.N15 撞在此处。
        # 「能引用的范围」与「修复指令能改的范围」是两件事：前者是审查窗口
        # （第 1..n 章），后者由 prompt 的铁律锁在当前章，不在这里放宽。
        # 非里程碑章的 N15 由 next 自动跳过，实际只有 n=1 / n=3 / 里程碑会跑到。
        chapter = int(node_id.split(".")[0][1:])
        return {"sources": [f"正文/第{i:03d}章_" for i in range(1, chapter + 1)],
                "mechanical_ids": set()}
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
            # 有卷计划时先完成末卷审查，再汇总全书；老书维持 N17。
            if not st.get("plan"):
                return "N17"
            return f"V{vol_of(st, n)}.N17"
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
