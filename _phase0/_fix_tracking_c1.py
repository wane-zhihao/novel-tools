# -*- coding: utf-8 -*-
"""C1 追踪事务 schema 修复 v2：压缩到渲染预算内。

在 v1（schema 合规）基础上，按 render_delta/render_snapshot 的真实字节数压缩：
DELTA_MAX_BYTES=3072（逐章记录硬上限），SNAPSHOT_MAX_BYTES=8192。
"""
import json
import sys

sys.path.insert(0, r"C:\Users\Wane-\.qoder\skills\story-long-write\scripts")
import tracking_commit as tc  # noqa: E402

P = r"F:\WorkBuddy AI\Novel Platform\_phase0\book2\.story\work\第001章\tracking.json"
t = json.load(open(P, encoding="utf-8"))

# ── result（≤360 字节：会被复制进 recent_chapters.summary）────────────────────
t["delta"]["result"] = (
    "陈砚重生回顶职公示当天，名字被换成弟弟陈磊；赵德海当众定性他账目对不上并殃及父亲清誉，"
    "定三天期限。陈砚问出经手人是谁并记下罪名顺序，把羞辱现场变取证现场。"
)

# ── character_changes：只收 {name, change}（≤360 字节/条）────────────────────
t["delta"]["character_changes"] = [
    {"name": "陈砚", "change": "从嫌疑人变成当众固定罪状顺序的取证者，拿到期限与罪名顺序；代价是被孤立"},
    {"name": "陈磊", "change": "名额暂挂榜；当众倒戈指认哥哥被问住——加害者亦是赵德海的枪"},
    {"name": "赵德海", "change": "当众定性成功并定下三天期限，但被复述罪状顺序戳中软肋"},
    {"name": "王秀兰", "change": "当众劝认被掰开手并反驳，愣在原地；偏心账加重"},
    {"name": "陈广田", "change": "清誉被当众殃及；人群边缘露面未语先离去"},
]

# ── commitments / constraints（列表项从紧）────────────────────────────────────
t["delta"]["next_chapter_commitments"] = [
    "ch2 陈砚找当年经手人对顺序（「经手人是谁」落地）",
    "ch2 许晚晴「不附和污名」关键亮相（情感线启动）",
    "ch2 埋 S3 账目窟窿（ch2 埋 ch52 收，全卷主轴）",
    "三天倒计时 ch2 递减，ch3 批斗会落期限内兑现 S1",
    "ch2 只启动找顺序、认人证；翻盘办法当晚仍想不出",
]
t["delta"]["constraints"] = [
    "前世记忆不当众展开、不当众认罪掀榜动武——反派死于顺序而非拳头",
    "污名与期限结构已点燃：羞辱须殃及父亲清誉；认与不认都通向除名或移交，唯一缝隙三天",
    "本章未释放：孙有田人证（ch3）、陈磊反咬（S5 留 ch3）、名额结果（S2 埋而不收）",
    "数字纪律：冲突只用定性词，不演算不晒数字；年月相对表达",
]

# ── foreshadow（summary 从紧）────────────────────────────────────────────────
t["delta"]["foreshadow_changes"] = [
    {"action": "upsert", "id": "F001", "summary": "限期认账：不认就除名；倒计时逐章递减，ch3 落期限内", "planted_chapter": 1, "planned_resolution_chapter": 3, "status": "已埋", "importance": "高"},
    {"action": "upsert", "id": "F002", "summary": "顶职名额归属：公示撤不撤、陈磊退不退；换名当众发生，埋而不收", "planted_chapter": 1, "planned_resolution_chapter": 6, "status": "已埋", "importance": "高"},
    {"action": "upsert", "id": "F003", "summary": "母亲偏心：凭什么是大的让；当众掰破默契，偏心账加重", "planted_chapter": 1, "planned_resolution_chapter": 9, "status": "已埋", "importance": "中"},
]

# ── timeline（story_time 同场景合并、reader_knowledge 短写）───────────────────
t["delta"]["timeline_events"] = [
    {"action": "upsert", "id": "E001", "story_time": "1983年初夏·晌午", "objective_fact": "名单换名：陈砚变陈磊，公示于厂前宣传栏，榜前挤满人", "reader_knowledge": "读者已知", "reveal_status": "已揭示", "reveal_chapter": 1, "characters": ["陈砚", "陈磊", "赵德海"]},
    {"action": "upsert", "id": "E002", "story_time": "同日晌午", "objective_fact": "赵德海当众宣布陈砚账目「对不上」，定性侵占公款嫌疑，殃及父亲清誉", "reader_knowledge": "读者已知", "reveal_status": "已揭示", "reveal_chapter": 1, "characters": ["赵德海", "陈砚", "陈广田"]},
    {"action": "upsert", "id": "E003", "story_time": "同日晌午", "objective_fact": "陈磊当众倒戈，指认哥哥「那笔账有问题」", "reader_knowledge": "读者已知", "reveal_status": "已揭示", "reveal_chapter": 1, "characters": ["陈磊", "陈砚"]},
    {"action": "upsert", "id": "E004", "story_time": "同日晌午", "objective_fact": "王秀兰当众劝认，被陈砚掰开手：「我认了，爸的清誉就真没了。」", "reader_knowledge": "读者已知", "reveal_status": "已揭示", "reveal_chapter": 1, "characters": ["王秀兰", "陈砚"]},
    {"action": "upsert", "id": "E005", "story_time": "同日晌午", "objective_fact": "陈砚当众发问「换名之前经手人是谁」并逐条复述罪名顺序记入笔记本；赵德海顺势定三天期限，羞辱现场变取证现场", "reader_knowledge": "读者已知", "reveal_status": "已揭示", "reveal_chapter": 1, "characters": ["陈砚", "赵德海"]},
    {"action": "upsert", "id": "E006", "story_time": "同日傍晚", "objective_fact": "人群散去；陈砚在公示栏前按顺序记下今天的一切，距认账期限三天", "reader_knowledge": "读者已知", "reveal_status": "已揭示", "reveal_chapter": 1, "characters": ["陈砚"]},
]

# ── retired_* 置空（第 1 章无人退役）────────────────────────────────────────
t["delta"]["retired_characters"] = []
t["delta"]["retired_context_items"] = []

# ── oracle ────────────────────────────────────────────────────────────────────
errors = []
allowed_root = {"schema_version", "mode", "chapter", "chapter_title",
                "expected_state_revision", "delta", "context",
                "character_snapshots", "wordcount"}
extra = set(t) - allowed_root
if extra:
    errors.append(f"root 未知键: {extra}")


def check(label, fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        errors.append(f"{label}: {e}")
        return None


snapshots = check("normalize_snapshots", lambda: tc.normalize_snapshots(t["character_snapshots"]))
ctx = check("validate_context_input",
            lambda: tc.validate_context_input(t["context"], include_initial_fields=False))
delta = check("normalize_delta", lambda: tc.normalize_delta(
    t["delta"], through_chapter=1, snapshots=snapshots or {}, existing_core_names={}))

# 渲染字节预算：逐章记录 ≤3072，单角色快照 ≤8192
if delta is not None:
    rendered = tc.render_delta(1, t["chapter_title"], delta, set(snapshots or {}))
    size = len(rendered.encode("utf-8"))
    print(f"render_delta: {size} bytes (cap {tc.DELTA_MAX_BYTES})")
    if size > tc.DELTA_MAX_BYTES:
        errors.append(f"逐章记录超限: {size} > {tc.DELTA_MAX_BYTES}")
if snapshots:
    for name, snap in snapshots.items():
        r = tc.render_snapshot(name, snap, 1, 1)
        size = len(r.encode("utf-8"))
        print(f"render_snapshot {name}: {size} bytes (cap {tc.SNAPSHOT_MAX_BYTES})")
        if size > tc.SNAPSHOT_MAX_BYTES:
            errors.append(f"快照超限 {name}: {size}")

if errors:
    print("VALIDATION FAILED:")
    for e in errors:
        print(" -", e)
    sys.exit(1)

open(P, "w", encoding="utf-8").write(json.dumps(t, ensure_ascii=False, indent=1) + "\n")
print("ALL CHECKS PASSED — 事务已修复并落盘")
