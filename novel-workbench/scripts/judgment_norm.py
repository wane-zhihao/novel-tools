#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""judgment_norm.py — verify 裁决的归一化层 + 自解释报错 + 裁决契约

根因（2026-11-19，冷启动书 N06 实测）：
闸门校验器把「接口键名」和「裁决内容」混为一谈。judge 只要没把裁决写成
canonical 的 items[] 形状——哪怕内容完全正确、连逐字引文都真的从源文件里
抄对了——整份文件仍被判废（quote_check_failed: items 为空）。
被废掉的是「语义有效的工作」，丢掉的只是「接口约定」。于是一轮好裁决白费，
而对话路径又没有 P2 那样的自愈回路，代价是整轮甚至连续几轮空转。

本模块提供三件东西：

1. normalize_judgment() —— 无损归一：
   - 别名容器/别名键 → canonical 键（items / criterion / quote / source /
     verdict / level / fix / id / metric）；
   - 散文（detail/note/basis…）里回捞真正命中源文件的连续逐字引文；
   - 引文命中但 source 写错 → 改指到真正命中的那个文件。
   底线：引文必须**真的**逐字出现在源文件里才写回。捞不到就如实报错，
   不造事实、不替 judge 圆场。

2. judgment_schema_hint() —— 自解释报错：把目标结构、字段规则、允许引用的
   文件、可直接抄的示例一起塞进错误载荷。P2 的 node_retry 与对话路径的
   重派都靠它一次改对。

3. 裁决契约 —— 引文的作用是给**阻断性结论**提供依据（修复指令要落到具体
   字句上），不是给每条观察发一张证书：
   - fail 项必须给逐字引文（阻断必须有据）；
   - pass 项可省引文，但**整份裁决至少要有一条命中引文**，以证明 judge
     真的读了源文件（否则整份判空）。
   这一条把「必须逐条引文」从形式要求降为实质要求：结构性检查（文件是否
   齐全、数值是否自洽）本来就没有可引的句子，硬要引文只会逼 judge 编。
"""
from __future__ import annotations

import re
from pathlib import Path

from inline_runner import _norm as _match_norm  # 与 verify_quote 同一套匹配规则

# ─── 别名字典 ────────────────────────────────────────────────────────────────

ITEMS_KEYS = ("items", "checks", "findings", "results", "verdicts", "details",
              "judgements", "judgment_items")
CRITERION_KEYS = ("criterion", "name", "title", "check", "dimension", "aspect",
                   "item", "point", "clause")
QUOTE_KEYS = ("quote", "evidence", "excerpt", "snippet", "original", "citation",
              "quoted", "ref")
SOURCE_KEYS = ("source", "file", "path", "src", "from", "location", "source_file")
VERDICT_KEYS = ("verdict", "result", "status", "judgement", "judgment",
                "conclusion", "outcome")
LEVEL_KEYS = ("level", "severity", "grade", "risk_level", "weight", "weight_level")
FIX_KEYS = ("fix", "fix_instruction", "fix_hint", "suggestion", "advice",
            "how_to_fix", "action", "repair", "remediation")
ID_KEYS = ("id", "check_id", "code", "no", "num", "number", "seq")
PROSE_KEYS = ("detail", "details", "note", "notes", "basis", "reason", "comment",
              "remark", "explanation", "desc", "description", "detail_text",
              "analysis", "分析", "依据", "说明", "结论", "理由")

_PASS_WORDS = {"pass", "passed", "passing", "ok", "okay", "good", "true", "yes",
               "y", "1", "通过", "合格", "符合", "满足", "无问题", "没问题",
               "一致", "兼容"}
_FAIL_WORDS = {"fail", "failed", "failing", "bad", "false", "no", "n", "0",
               "reject", "rejected", "blocked", "不通过", "不合格", "不符合",
               "缺失", "违规", "有问题", "不合格项", "驳回"}
_B_LEVEL_WORDS = {"b", "block", "blocking", "hard", "critical", "high", "severe",
                  "致命", "硬伤", "严重", "高", "必修"}
_A_LEVEL_WORDS = {"a", "info", "advisory", "minor", "low", "suggestion",
                  "提示", "轻微", "低", "建议", "可不改"}

# 契约：fail 项必须给引文；pass 项可省；整份裁决至少一条命中引文。
QUOTE_REQUIRED_FOR = "fail"
MIN_GROUNDED_QUOTES = 1


def _first_present(data: dict, keys) -> object | None:
    for key in keys:
        if key in data:
            value = data[key]
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, (list, dict)) and not value:
                continue
            return value
    return None


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(_as_text(v) for v in value)
    if isinstance(value, dict):
        return " ".join(_as_text(v) for v in value.values())
    return str(value)


def _map_verdict(value) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    if not token:
        return None
    if token in _PASS_WORDS:
        return "pass"
    if token in _FAIL_WORDS:
        return "fail"
    if token.startswith("pass") or token.startswith("通过"):
        return "pass"
    if token.startswith("fail") or token.startswith("不通过"):
        return "fail"
    return None


def _map_level(value) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    if not token:
        return None
    if token in _B_LEVEL_WORDS:
        return "B"
    if token in _A_LEVEL_WORDS:
        return "A"
    return None


def _candidate_source_files(book: Path, patterns) -> list[Path]:
    """把「允许的取证范围」展开成书目录下的真实文件列表。"""
    out: list[Path] = []
    seen: set[str] = set()
    for pattern in patterns or []:
        text = str(pattern).replace("\\", "/")
        if text.endswith("/"):
            base = book / text.rstrip("/")
            files = [p for p in sorted(base.rglob("*")) if p.is_file()] if base.is_dir() else []
        elif text.endswith("_"):
            folder, _, prefix = text[:-1].rpartition("/")
            base = book / folder if folder else book
            files = [p for p in sorted(base.glob(f"{prefix}*")) if p.is_file()] if base.is_dir() else []
        else:
            exact = book / text
            files = [exact] if exact.is_file() else []
        for path in files:
            try:
                rel = str(path.resolve().relative_to(book.resolve())).replace("\\", "/")
            except (ValueError, OSError):
                continue
            if rel not in seen:
                seen.add(rel)
                out.append(path)
    return out


def _rel_path(book: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(book.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(path)


def _cjk_only(text: str) -> str:
    return re.sub(r"[^\u4e00-\u9fa5]", "", text or "")


def _longest_hit_window(text: str, norm_source: str, min_len: int,
                        max_len: int = 30) -> str:
    """在 text 里找最长的一段（≤max_len），其归一形式出现在 norm_source 中。

    与 verify_quote 用同一套归一规则（去标点），因此捞出来的片段一定是
    verify_quote 认可的逐字引文。

    另加一条**质量门槛**：片段至少要含 12 个汉字。verify_quote 的下限是
    「原始长度 ≥12 且归一后 ≥4 个字符」，按这个下限回捞会捞到
    「`、`设定/势力.md`），」这种文件路径碎片——它是逐字的，却不是引文，
    拿它充数等于把「证明读过源文件」这条实质要求掏空。
    """
    upper = min(max_len, len(text))
    for length in range(upper, min_len - 1, -1):
        for start in range(0, len(text) - length + 1):
            frag = text[start:start + length]
            if len(_cjk_only(frag)) < min_len:
                continue
            norm = _match_norm(frag)
            if len(norm) >= 4 and norm in norm_source:
                return frag
    return ""


def _extract_quote_from_prose(prose: str, source_file: Path,
                               min_len: int = 12) -> str:
    """从散文里回捞真正命中源文件的逐字引文；捞不到就返回空串。

    这是**搬运**，不是编造：返回的片段必然逐字出现在源文件里，
    且至少含 12 个汉字（避免捞到路径碎片充数）。
    """
    if not prose or source_file is None or not source_file.is_file():
        return ""
    try:
        body = source_file.read_text(encoding="utf-8")
    except OSError:
        return ""
    norm_source = _match_norm(body)
    if not norm_source:
        return ""
    for segment in re.split(r"…|\.\.\.", prose):
        segment = segment.strip()
        if len(segment) < min_len:
            continue
        norm_segment = _match_norm(segment)
        if not norm_segment:
            continue
        if len(_cjk_only(segment)) >= min_len and norm_segment in norm_source:
            # 整段就是逐字原文（judge 常常整句抄了过来）
            return segment[:300]
        hit = _longest_hit_window(segment, norm_source, min_len)
        if hit:
            return hit
    return ""


def _pick(item: dict, keys, canonical: str):
    """取 canonical 键；没有就找别名。返回 (值, 是否用了别名)。"""
    value = item.get(canonical)
    if value is not None and not (isinstance(value, str) and not value.strip()):
        return value, False
    alt = _first_present(item, keys)
    if alt is None:
        return None, False
    return alt, True


def _normalize_item(item: dict, index: int) -> tuple[dict, bool]:
    """把一个裁决项归一到 canonical 形状。返回 (归一项, 是否用了别名)。"""
    out = dict(item)
    alias_used = False

    criterion, aliased = _pick(item, CRITERION_KEYS, "criterion")
    out["criterion"] = str(criterion).strip() if criterion is not None else f"item-{index}"
    alias_used = alias_used or aliased

    raw_verdict, aliased = _pick(item, VERDICT_KEYS, "verdict")
    mapped = _map_verdict(raw_verdict)
    out["verdict"] = mapped if mapped is not None else str(raw_verdict or "").strip().lower()
    alias_used = alias_used or aliased

    raw_level, aliased = _pick(item, LEVEL_KEYS, "level")
    mapped = _map_level(raw_level)
    out["level"] = mapped if mapped is not None else "B"
    alias_used = alias_used or aliased

    quote, aliased = _pick(item, QUOTE_KEYS, "quote")
    out["quote"] = _as_text(quote).strip() if quote is not None else ""
    alias_used = alias_used or aliased

    source, aliased = _pick(item, SOURCE_KEYS, "source")
    out["source"] = _as_text(source).strip().replace("\\", "/") if source is not None else ""
    alias_used = alias_used or aliased

    fix, aliased = _pick(item, FIX_KEYS, "fix")
    out["fix"] = _as_text(fix).strip() if fix is not None else ""
    alias_used = alias_used or aliased

    ident, aliased = _pick(item, ID_KEYS, "id")
    if ident is not None:
        out["id"] = str(ident).strip()
        alias_used = alias_used or aliased

    metric = item.get("metric")
    if isinstance(metric, dict):
        out["metric"] = metric
    return out, alias_used


def normalize_judgment(raw: dict, book: Path, node_id: str) -> tuple[dict, list[str]]:
    """把 judge 的裁决归一到 canonical 形状。

    返回 (归一后的裁决, 修复说明)。**修复说明非空 = 文件被真正改写过**，
    只有这时才该把归一结果落盘——避免给已完成的书 gratuitously 改写历史裁决。
    """
    if not isinstance(raw, dict):
        return raw, []
    from pipeline_spec import judgment_policy

    policy = judgment_policy(node_id)
    repairs: list[str] = []
    out = dict(raw)

    # ── 容器：找出裁决项列表 ──────────────────────────────────────────────
    container_key = None
    for key in ITEMS_KEYS:
        value = raw.get(key)
        if isinstance(value, list) and value:
            container_key = key
            break
    if container_key is None:
        for key in ITEMS_KEYS:
            if key in raw:
                container_key = key
                break
    raw_items = raw.get(container_key) if container_key else None
    if not isinstance(raw_items, list):
        raw_items = []

    items: list = []
    alias_used = False
    for i, x in enumerate(raw_items):
        if isinstance(x, dict):
            norm_item, aliased = _normalize_item(x, i)
            alias_used = alias_used or aliased
            items.append(norm_item)
        else:
            items.append(x)
    if container_key and container_key != "items":
        repairs.append(f"容器键 {container_key} → items")
    if alias_used:
        repairs.append("裁决项键名归一（criterion/verdict/level/quote/source/fix）")

    # ── 顶层 verdict ───────────────────────────────────────────────────────
    top_verdict = _map_verdict(_first_present(raw, VERDICT_KEYS))
    if top_verdict is None:
        top_verdict = "block" if any(
            isinstance(x, dict) and x.get("verdict") == "fail" for x in items) else "pass"
        repairs.append(f"顶层 verdict 缺失 → 由明细推导为 {top_verdict}")
    out["verdict"] = top_verdict

    summary = _first_present(raw, ("summary", "ruling", "conclusion", "overall",
                                   "reason", "abstract"))
    out["summary"] = _as_text(summary).strip() if summary is not None else ""

    # ── 引文补捞：逐项尝试从散文里找回逐字原文 ────────────────────────────
    allowed = policy.get("sources") or []
    candidates = _candidate_source_files(book, allowed)
    top_source = _as_text(_first_present(raw, SOURCE_KEYS)).strip().replace("\\", "/")
    top_quote = _as_text(_first_present(raw, QUOTE_KEYS)).strip()

    def _norm_hit(quote: str, path: Path) -> bool:
        if not quote or path is None or not path.is_file():
            return False
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            return False
        norm = _match_norm(quote)
        return bool(norm) and len(norm) >= 4 and norm in _match_norm(body)

    for item in items:
        if not isinstance(item, dict):
            continue
        source = item.get("source") or ""
        source_path = book / source if source else None
        if source_path is None or not source_path.is_file():
            source_path = None

        # (a) 引文有、source 写错或缺失 → 改指到真正命中的文件
        if item.get("quote"):
            if source_path is not None and _norm_hit(item["quote"], source_path):
                continue
            for path in candidates:
                if _norm_hit(item["quote"], path):
                    item["source"] = _rel_path(book, path)
                    repairs.append(f"引文命中改指 source → {item['source']}")
                    source_path = path
                    break
            continue

        # (b) 没有引文 → 从散文里回捞
        prose = " ".join(_as_text(item.get(key)) for key in PROSE_KEYS
                         if item.get(key)).strip()
        if not prose:
            continue
        pool: list[Path] = []
        if source_path is not None:
            pool.append(source_path)
        if top_source:
            top_path = book / top_source
            if top_path.is_file() and top_path not in pool:
                pool.append(top_path)
        for path in candidates:
            if path not in pool:
                pool.append(path)
        for path in pool:
            hit = _extract_quote_from_prose(prose, path)
            if hit:
                item["quote"] = hit
                if item.get("source") != _rel_path(book, path):
                    item["source"] = _rel_path(book, path)
                repairs.append(f"从散文回捞引文 → {item['source']}")
                break

    # (c) 顶层引文没被任何项认领 → 认领给 source 相同的项
    if top_quote and top_source:
        top_path = book / top_source
        if top_path.is_file():
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("quote"):
                    continue
                if (item.get("source") or "") == top_source:
                    item["quote"] = top_quote
                    repairs.append(f"顶层引文归入 source 相同的项 → {top_source}")
                    break

    out["items"] = items
    return out, repairs


# ─── 自解释报错 ──────────────────────────────────────────────────────────────

CANONICAL_JUDGMENT_SCHEMA = {
    "verdict": "pass|block",
    "summary": "一句话结论",
    "items": [{
        "criterion": "被检查的维度（短句）",
        "quote": "源文件里连续 12-30 字的逐字原文，不得用省略号跳跃",
        "source": "源文件相对本书根目录的路径，如 设定/文风.md",
        "verdict": "pass|fail",
        "level": "A|B",
        "fix": "修复指令（verdict=fail 时必填）",
    }],
}

_CONTRACT_TEXT = (
    "字段规则：\n"
    "- items：**不可为空**——必须是数组，至少 1 项（空数组会被拒）。"
    "每项 = 一个被检查的维度。\n"
    "- criterion：这个维度叫什么，短句即可。\n"
    "- verdict：顶层 pass|block；每项 pass|fail。顶层写 block 时至少要有一项 fail。\n"
    "- source：源文件相对本书根目录的路径，只能填本节点允许清单里的文件。\n"
    "- quote：**fail 项必填**——必须是 source 文件里连续 12-30 字的逐字原文，"
    "不得用省略号跳跃、不得改写（修复指令要落到具体字句上，必须有据）。\n"
    "  pass 项可省略；但**整份裁决至少要有一条命中原文的 quote**，"
    "用来证明你真的读了源文件——全部 pass 又一条引文都没有会被拒。\n"
    "- level：A=提示（只记录不阻断）；B=硬伤（打回重写）。缺省按 B。\n"
    "- fix：verdict=fail 时写清具体修复指令。\n"
)


def judgment_schema_hint(node_id: str, book: Path) -> dict:
    """构造自解释的错误载荷：目标结构 + 字段规则 + 允许引用清单 + 可抄示例。"""
    from pipeline_spec import judgment_policy

    policy = judgment_policy(node_id)
    patterns = policy.get("sources") or []
    files = [_rel_path(book, p) for p in _candidate_source_files(book, patterns)]

    # 示例必须**自洽**：quote 真的来自它标的那个 source。
    # 随手拼一个示例（引文取 A 文件、source 写 B 文件）会让执行方照抄一个
    # 必然被拒的范例——比不给示例更糟。
    #
    # 挑行时还要避开两类行：markdown 引用行（以 > 开头）和「依据：xxx.md」
    # 这类**交叉引用**——它们虽然确实在自己文件里，但作为示例看着像引错文件，
    # 反而教坏了执行方。优先挑汉字最多、长度适中的正经陈述句。
    example_source = files[0] if files else "设定/文风.md"
    example_quote = ""
    best_score = 0
    for rel in files:
        try:
            body = (book / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        for line in body.splitlines():
            candidate = line.strip().lstrip("#").lstrip("-").strip()
            if not candidate or candidate.startswith(">") or candidate.startswith("|"):
                continue
            if re.search(r"依据[:：]|参见|见\s*《|本文档", candidate):
                continue
            cjk = len(_cjk_only(candidate))
            if cjk < 12:
                continue
            score = min(cjk, 40)
            if score > best_score:
                best_score = score
                example_quote = candidate[:60]
                example_source = rel
        if best_score >= 30:
            break
    if not example_quote:
        example_quote = "（本节点暂无真实文件，请先写好源文件再裁决）"

    example = {
        "verdict": "pass",
        "summary": "设定五件齐全，角色动机明确，文风与 niandai 基准兼容，无硬伤。",
        "items": [{
            "criterion": "角色有明确动机",
            "quote": example_quote,
            "source": example_source,
            "verdict": "pass",
            "level": "A",
            "fix": "",
        }],
    }
    allowed_text = ("\n".join(f"  {rel}" for rel in files) if files
                    else "  （本节点未开放任何取证文件——见下方 allowed_sources）")
    # 顺序有讲究：P2 无人值守路径把这条反馈**截到 900 字**再喂给模型
    # （server/agent/runtime.mjs L482）。所以最该被看到的三样必须排在前面：
    # ① 目标结构 ② 可直接抄的示例 ③ 字段规则。示例排最后就会被截掉。
    feedback = (
        "裁决文件结构不对，done 被拒（quote_check_failed）。"
        "请按下面的结构**重写整个文件**（不要在旧文件上打补丁，也不要只改键名）：\n\n"
        '{"verdict":"pass|block","summary":"...",'
        '"items":[{"criterion":"...","quote":"...","source":"...",'
        '"verdict":"pass|fail","level":"A|B","fix":"..."}]}\n\n'
        + "可直接抄的示例（结构与字段都合法）：\n"
        + json_dumps_pretty(example)
        + "\n\n" + _CONTRACT_TEXT
        + "\n本节点允许的 source（只能填这些）：\n" + allowed_text
    )
    return {
        "schema": CANONICAL_JUDGMENT_SCHEMA,
        "example": example,
        "allowed_sources": files,
        "allowed_source_patterns": list(patterns),
        "feedback": feedback,
    }


def json_dumps_pretty(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, indent=2)
