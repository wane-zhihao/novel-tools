# 书房工作台 v1.0 PRD（可宣发版）

> 版本：1.0  
> 日期：2026-10-28  
> 目标形态：GitHub 开源 + zip 解压单机分发；面向个人网文作者（非技术用户为主）  
> 验收标准：能可靠地无人值守连跑一卷 → 统稿审阅 → 投稿包（真正有正文、无事实错误）

---

## 一、产品定位与目标用户场景

### 1.1 核心价值主张

**让用户从"AI 写人审"变成"AI 跑完卷后人投"：**
- AI 负责从开书到封板的完整流水线（选题→设定→卷纲→细纲→正文→质检）
- 人只在两个关键时刻介入：开书配置 + 最后统稿审阅
- 最终交付物是**可信的投稿包**（编辑收到的就是真实完成的正文，不是账本幻觉）

### 1.2 目标用户画像

| 特征 | 描述 | 对产品的期望 |
|---|---|---|
| **身份** | 个人网文作者 | 不懂命令行，不会装 Node/Python |
| **设备** | 个人 PC/笔记本 | Windows 系统为主，机器配置参差不齐 |
| **技术能力** | 非技术用户 | "解压即用"是唯一预期，不能有任何前置条件 |
| **使用节奏** | 边写边投或写完再投 | 希望无人值守跑完一卷，然后统稿审阅 |
| **付费意愿** | 愿意为签约成功付费 | 接受一次性买断价格，但对"误投""失败"零容忍 |

### 1.3 竞品与替代方案

| 方案 | 优点 | 缺点 | 我们的机会 |
|---|---|---|---|
| 纯手工写作 | 完全可控 | 效率低、质量不稳定 | AI 提效但不放弃质量控制 |
| Chatbot+ 手调脚本 | 灵活度高 | 需要技术知识、无法闭环 | 开箱即用的自动化 |
| SaaS 写作平台 | 云端协同 | 数据隐私、订阅费、平台规则限制 | 本地化、一次付费、数据自持 |

---

## 二、北极星指标

**每本书的「有效投稿成功率」** = 投稿后获得编辑正面反馈 / 总投稿次数

这不是一个直接可量化的后台指标，而是由三个前置指标支撑：

| 前置指标 | 定义 | 当前基线 | 目标（v1.0） |
|---|---|---|---|
| **正文完整性** | 投稿包 `全文.md` 实际字数 == 预检报告字数（三方一致：DB 行 / 磁盘文件 / state 计数） | ❌ DL-01：正文删除后预检仍报达标，可打包出缺正文的包 | ✅ 不一致即拒绝打包 |
| **事实准确性** | 封板前事实层 F1–F6 真的跑过且非 unverified | ❌ **仅人工定稿路径接入**（`commit_chapter.py:191`）；无人值守 N16 封板（`inline_runner.py:872`）只调 storyctl，**不跑 fact_gate** | ✅ 两条路径同一门禁 |
| **字数口径透明** | 报告同时给「可见字符」与「纯汉字」两个数并注明门槛来源 | ❌ DL-03：单一 visible 口径，与汉字数天然差约 17%，且门槛 20000 未标 provisional | ✅ 双口径并列 + 口径可选 + provisional 标注 |

> **关于第三行的目标值**：差异不可能"降到 <1%"——中文正文里标点/数字本就被计入 visible_chars_v1，17% 是口径定义的结果，不是缺陷。
> 真正要达成的是**透明**：让作者和编辑看见同一件事的两种算法，并让门槛按所选口径判定。
> 把"差异<1%"当指标会导向错误修复（改计数函数），而正确的修复是改展示与判定。

> **关于第二行的路径不对称（本轮新发现，v1.0 关键缺口）**：
> 产品主张"AI 无人值守连跑一卷"，但事实闸门目前只挂在**人工点击定稿**那条路上；
> 流水线自动封板 N16 走 `storyctl chapter commit`，绕开 fact_gate。
> 也就是说：**越自动化，越少一道防线** —— 这与产品定位正好相反。
> 对应 TDD T-3b（见 `references/tdd-workbench-1.0.md`）。

---

## 三、核心需求与门禁（不可妥协项）

### 3.1 投稿包生成门禁（P0）

**目标：确保编辑收到的就是一套完整真实的书。**

#### M1: 正文文件存在性校验

| 检查项 | 实现位置 | 实现方式 | 失败后果 |
|---|---|---|---|
| chapters 表行数 == 正文目录实际 .md 文件数 | server/sync.mjs + submissions.mjs | 在 preflightSubmission 增加独立检查（不是只读 state.words_total） | fail-open 会被绕过 |
| 每章文件 sha256 == DB 中 body_sha256 | server/sync.mjs | 遍历正文书目逐章比对，不一致列清单 | silent-pass 已证实存在 |
| MANIFEST.json 必含 全文.md | server/submissions.mjs:127-162 | files 循环后立即校验 manifest.length，缺失抛错 | 静默 continue 已证实 |

**修复实施（fix_effort: 中）**
```python
# 新增于 submissions.mjs:preflight_check
def check_body_integrity(book):
    # 1) chapters 表行数 vs 正文目录 .md 文件数
    db_count = db.execute("SELECT COUNT(*) FROM chapters WHERE book_id=?", (book.id,)).fetchone()[0]
    disk_files = list((book.path / "正文").glob("第*章_*.md"))
    if db_count != len(disk_files):
        return False, f"章节数量不匹配：DB={db_count}, 磁盘={len(disk_files)}"
    
    # 2) 每章 sha256 比对
    mismatch = []
    for row in db.query_chapters_by_book(book.id):
        disk_file = (book.path / "正文") / f"{row.chapter}_{row.source}.md"
        if not disk_file.exists():
            mismatch.append(f"第{row.chapter:03d}章文件不存在")
        elif sha256_file(disk_file) != row.body_sha256:
            mismatch.append(f"第{row.chapter:03d}章 sha256 不一致")
    
    if mismatch:
        return False, "以下章节异常：" + str(mismatch)
    
    return True, "OK"
```

#### M2: _原稿_ 备份文件排除

| 检查项 | 实现位置 | 实现方式 | 失败后果 |
|---|---|---|---|
| glob("*.md") 必须排除 _原稿_ 后缀 | scripts/inline_runner.py + server/book-files.mjs + server/sync.mjs | 统一在一个共享函数中判定 | 同一 chapter number 抢行、旧稿混入全文 |

**修复实施（fix_effort: 中）**
```python
# 新增于 scripts/pipeline_spec.py:1-5
def is_valid_chapter_file(filename: str) -> bool:
    """真正的章节文件判定：排除 _原稿_/.bak/备份 等后缀"""
    basename = filename.split(".")[-1].split("_")[0] if "_" in filename else filename
    excluded_markers = ["_原稿_", ".bak", "_backup", "原稿"]
    return all(marker not in filename for marker in excluded_markers) and basename.isdigit()
```
然后在三处改：
- `scripts/inline_runner.py:941,965,1120,1155,1528,1538,1569` → `if is_valid_chapter_file(path.name)`
- `server/book-files.mjs:67-75` → same check
- `server/sync.mjs:283-305` → same check + UNIQUE(book_id, file_path) 防冲突

#### M3: words_total 双口径展示

| 检查项 | 实现方式 | 对外承诺 |
|---|---|---|
| 体检报告/投稿包同时给 visible_chars_v1 + 汉字数 | inline_runner.py:1163-1166 + submissions.mjs:39-45 | "可见字符 21,112 字（含标点）；纯汉字 17,533 字" |
| thresholds.json 中标注 provisional | F:/小说/platforms/qimao/config/thresholds.json | "2 万字为本地历史观察，非官方条款" |

**修复实施（fix_effort: 中）**
```markdown
# 体检报告新增字段
- 字数统计：
  - 可见字符数（visible_chars_v1）: 21,112 字
  - 纯汉字数（CJK only）: 17,533 字
  - 标点/拉丁/数字占比：17.0%
- 投稿门槛说明：
  - 七猫历史观察值：≥20,000 字（current_min_words=null, status=provisional）
  - 建议以编辑反馈为准，此值为参考区间
```

### 3.2 事实层闸门门禁（P0）

**目标：杜绝"事实错误进账本"的情况。**

#### M4: fact_gate 调用链接入

| 检查项 | 当前状态 | 要求 |
|---|---|---|
| commit_chapter 中是否有 _fact_check 调用 | ✅ **仅人工定稿路径**（`scripts/commit_chapter.py:191`） | 保持现状，加超时/缺工具/stderr 污染三类用例 |
| **无人值守 N16 封板是否跑 fact_gate** | ❌ **不跑** —— `inline_runner.py:872 _commit_chapter` 只调 `storyctl chapter commit` | **v1.0 必须补**：两条路径共用同一道事实门禁（见 TDD T-3b） |
| fact-gate.test.mjs 是否被 npm test 包含 | ⚠️ 已写入 `package.json` 但文件未跟踪 → 状态不一致 | `git add` 入库（见 TDD 方案 §1.1，开工前先做） |
| unverified 是否能区分 pass | ✅ gate_runner/fact_gate 契约已定 | `commit_chapter.py:191` 需保证 `fpayload.get("status", "unverified")` 不为 pass |

**修复实施（fix_effort: 低，已有人写了）**
确认 fact-gate.test.mjs 已加入 npm test：
```json
// package.json:14
"test": "node --test server/tests/sync.test.mjs ... server/tests/run-events.test.mjs server/tests/fact-gate.test.mjs"
```

#### M5: fact_gate 返回值处理（fail-closed）

| 检查项 | 风险点 | 修复 |
|---|---|---|
| `_fact_check` 返回 rc=2 时 | 工具不存在/Python 不可用 → 抛出 fact_tool_unavailable | commit_chapter.py:195-197 |
| `blocking` 数组非空时 | 任何 F1-F6 阻断 → 抛出 fact_blocked + findings[:10] | commit_chapter.py:205-207 |
| stderr 混入 stdout 时 | JSON.parse 失败 → 吞掉阻断 | 改用 inline_runner.extract_json(out) |

**修复实施（fix_effort: 低）**
```python
# commit_chapter.py:191-200
def commit_chapter(...):
    ...
    frc, fpayload = _fact_check(book)
    if frc == 2:
        raise CommitError("fact_tool_unavailable",
                          f"事实闸门不可用：{str(fpayload)[-200:]}", 400)
    fblock = fpayload.get("blocking") or []
    gate["fact"] = {
        "status": fpayload.get("status", "unverified"),
        "blocking_count": len(fblock),
        "advisory_count": len(fpayload.get("advisory") or [])
    }
    if fblock:
        raise CommitError(
            "fact_blocked",
            f"事实层阻断 {len(fblock)} 条（日期/数值/字数断言）", 422,
            {"fact_gate": gate["fact"], "findings": fblock[:10]}
        )
```

### 3.3 外部工具链接口契约（P1）

**已与 Novel tools 双方确认（2026-11-01 按对方接口审查清单修订）：**

| 维度 | gate_runner | fact_gate |
|---|---|---|
| **rc 取值** | **0**（非阻断）/ **1**（blocked）/ **2**（配置或用法错误） | 同上，**不加 rc=3** |
| **rc=2 时** | stdout **无 JSON**，错误只走 stderr → 调用方不得解析 stdout | 同上 |
| **status 枚举** | pass / advisory / blocked / metrics_only / **null**（仅 has_error=true 时） | pass / advisory / blocked / unverified |
| **error 标记** | `"has_error": true` + `"error": "..."` | **无**（故障通道是 rc=2 + stderr） |
| **undetermined 标记** | `"undetermined": true` + `"coverage": {...}` | 无（用 status=unverified 表达） |
| **判定逻辑** | 短路顺序 `has_error → undetermined → status`；**不看 rc 做内容结论** | 同上（status=unverified 不得折叠成 pass） |
| **编码输出** | stdout 必须 UTF-8（调用方注入 PYTHONIOENCODING） | 同上 |

> **rc=1 的现状注记**：当前三平台统计层 B 级数 = 0，**rc=1 只可能来自崩溃**（探针异常 / 未捕获异常）。
> 即"rc=1 = 内容阻断"这条语义在现行配置下不可达，必须靠 mock 或夹具验证，不能声称端到端已测。
>
> **per-gate level 值域**：qimao/fanqie 用 `"A"`/`"B"`，**zongheng 用 `"advisory"`**（5 道全是）→ 统计必须留 `other_level` 兜底，
> 否则纵横会被读成"一道闸门都没配"（少报式失真，比多报更难发现）。
>
> **两个探针的 rc 语义与 gate_runner/fact_gate 不同**：探针 rc=1 =「没有有效章节目录」、rc=2 =「未给目录」；
> gate_runner/fact_gate 的 rc=1 = blocked。**同名不同义，接入时不得混用。**

**关键约束（写进 PRD 的"不可变契约"）：**
1. **rc=0 不代表 pass** → 调用方必须检查 status 字段
2. **rc=1 不等于 content-blocked** → 可能是崩溃，要先看 stdout 是否为 JSON
3. **has_error=true 优先阻断** → undetermined=true 次之，最后是 status
4. **encoding != utf-8 → undetermined=true** → 指标来源不可信但不阻断

### 3.4 N20g/N21 终审门禁（P1）

| 检查项 | 问题 | 修复方向 |
|---|---|---|
| N20g judge 只读全书概要 | 标题 + 首段≤120 字 → 跨卷主线问题看不见 | 要求至少读全书 markdown 索引结构（卷末 advisories/人物弧光） |
| V.N20 block 降为 advisory | "永不 terminal block" → 单卷硬伤不影响整卷 | 可选：加一个全局阈值（例如 3 卷内有 1 卷 advisory → N21 fail） |
| target_words 不参与 can_submit | 2 万硬门槛 → 60 万字目标书写到 2 万就放行 | 改为 max(target_words * 0.5, 20000) 或完全按 target_words |

---

### 3.6 决策：事实层缺席时**放行 + 显式标注 + 持续跟踪**（用户裁决 2026-11-01）

背景：`commit_chapter.py:55` 硬编码 `F:\小说\...`，且 `FACT_GATE` 键在 `config.json`/`settings.mjs` 中**根本不存在**
（全仓 grep 只有那行硬编码与测试里的常量）→ 开源用户机器上没有该路径 → 现实现返回 rc=2 并抛
`fact_tool_unavailable`(400) → **人工定稿对每个开源用户直接锁死**。

**裁决 = B**：事实层不可用时**不阻断定稿**，但必须**显式标注"本包未做事实校验"**，
并纳入下述持续跟踪机制。明确**不采用 A（一律阻断）**：那会让 zip 发出去第一天就无法使用。

**代价必须写清（不许美化）**：B 意味着**在工具链到位之前，产品的核心承诺之一（事实准确性）是缺席的**。
因此 B 成立的唯一前提是"缺席永远可见、可追、可补"。若缺席变得不可见，B 就退化成 we've-seen-before 的假绿灯。

#### 持续跟踪机制（防线缺席登记 · Defense-Absence Ledger）

| 维度 | 设计 |
|---|---|
| **写入点** | 每次判定产生"未判定/未安装/降级"时追加一条：定稿、`preflight`、`packageSubmission`、N16 封板、sync 章数校验 |
| **存储** | 书目录 `_pipeline/gate_absences.json`（**append-only**，与 `confirmations.json` 同风格）+ DB 镜像新表 `gate_absences` |
| **字段** | `at` / `scope`(book\|chapter\|package) / `layer`(fact\|stats\|importer\|wrapper) / `reason`(tool_absent\|unverified\|no_config\|undetermined\|skipped) / `detail` / `tool_version` / `blocking_possible` / `pack_hash` |
| **呈现（三处，缺一即视为未实现）** | ① 投稿包内附 `防线缺席说明.md`，**编辑可见**；② 投页与体检报告显示"本包未做事实校验（原因：事实闸门未安装）"；③ 书房书卡黄标 + 高级抽屉新增「缺席台账」tab |
| **补检回路** | 工具链到位或平台能力变化后，对该书历史 pack 比对「当时缺席 vs 现在能力」，差集非空 → 生成「建议复检」待办。**不自动重打包**（会破坏投稿包不可变性），只提示并允许显式重跑 |
| **指标** | `unverified_pack_rate`（未做事实校验的包 / 总包数）、`absence_backlog`（可补而未补的缺席条数）、`absence_hidden_count`（应为 **恒 0**：任何缺席都必须在三个呈现面至少出现一次） |
| **纪律** | 前两个指标**初期必然非零**，目标不是清零而是**单调下降且从不静默**；`absence_hidden_count` 一旦 >0 视为 critical 回归 |

**验收（不可妥协）**
1. 空环境（无 `F:\小说`）：定稿成功，且投稿包内**有**`防线缺席说明.md`，DB `gate_absences` **有**记录 → 证明"放行但不静默"。
2. 同一本书装好工具链后复检：缺席记录不消失（历史留痕），新增一条 `rechecked` 记录，`absence_backlog` 下降。
3. 故意让某个缺席**不**出现在三个呈现面之一 → 测试必须失败（防"悄悄漏报"退化）。
4. `gate_absences.json` 被截断/覆盖 → 校验失败并报警（append-only 是审计前提）。

对应实现见 `references/tdd-workbench-1.0.md` T-3 / T-3c。

## 四、与 Novel tools 的接口边界

### 4.1 权威副本划分

| 模块 | 维护方 | 变更流程 |
|---|---|---|
| novel-workbench/scripts/*.py | Novel Platform | 改前先通知 Novel tools（仅 A–L 十二种接口） |
| novel-workbench/server/** | Novel Platform | 同上 |
| novel-workbench/web/** | Novel Platform | 同上 |
| platforms/_core/tools/* | Novel tools | 改前先通知 Novel Platform |
| platforms/<p>/harness/* | Novel tools | 同上 |
| platforms/<p>/corpus/* | Novel tools | 所有权归原著平台，Platform owner 许可后方可重新分发 |

### 4.2 调用接口清单（A–L）

| 编号 | 接口名 | 调用形态 | 我方处理 |
|---|---|---|---|
| A | storyctl.py | `chapter check` / `chapter commit` / `chapter accept-current-length`；`wordcount measure/check/checkpoint`（**无 draft**，draft 属 tracking_commit） | parse JSON, validate status |
| B | tracking_commit.py | `draft` / `commit` / `check` / `init`（**commit 才是主用法**） | parse JSON, normalize tracking |
| C | gate_runner.py | `gate_runner --platform --genre --json <files...>` | **read status**, ignore rc except for error detection |
| D | check-outline-contract.js | node check-outline --json | parse JSON, block on failure |
| E | check-ai-patterns.js | AI patterns check | parse JSON, aggregate findings |
| F | check-degeneration.js | degeneration check | parse JSON, aggregate findings |
| **G** | **gate_runner 探针集** | style_probe.py / clarity_probe.py / clarity_org_probe.py —— **三者都被 gate_runner 内部动态 import**（`gate_runner.py:46-47` 实测加载） | **函数名/键名即对外契约**：style_probe 提供 `probe()` 与 `visible_chars*`；clarity_probe 提供 `c_short_chain_max`（→ 七猫 G3-c、番茄 G3-c）；clarity_org_probe 提供 `causal_per_1000` / `narration_action_density` / `question_per_1000` / `action_chain_max`（→ **七猫 9 道闸门里有 4 道判它的输出**）。**改任一函数名/键名即断多道闸门** |
| H | build_writer_prompt.py | registered but unused | 暂无调用 |
| I | fact_gate.py | `fact_gate <body_dir> [--config <json>] [--json] [--selftest]` | **read status**；rc=2 → 配置错误；unverified 显式呈现 |
| **K** | chapter_check.py（平台中性） | `--platform <平台目录绝对路径> --project <书> --chapter N [--genre] [--file] [--extra]`；另有 `--describe` / `--selftest` | **N16 的唯一入口**（不接平台 shim）；三段键名 `storyctl`/`gate`/`fact`；`fact.status` 四态 + `fact.executed` 六门 + `fact.not_executed_reason` + `fact.assertion_stats` |
| L | 平台配置字段名 | `gates.json` / `genre-baselines.json` / `thresholds.json` / `style-anchor.json` 的键名 | 对方承诺不重命名；我方读 `blocking_gates_remaining` / `level=='B'` 计数 / `execution_mode` / `fact_layer`；`--describe` 为首选（带自洽断言），读不到时回退这些字段 —— **不把 `--describe` 作为硬依赖** |

**K 条契约要点**（2026-11-01 对方以《顶职》ch1 实跑验证，exit 0）

| 项 | 取值 |
|---|---|
| verdict | `ready \| needs_decision \| blocked \| partial \| error` |
| 优先级 | `error/partial > blocked > needs_decision > ready`（needs_decision 不再吞掉工具失败） |
| 退出码 | `0` = ready / needs_decision / advisory；`1` = blocked（storyctl 质量阻断、gate B 级、fact F1–F4 任一）；`2` = error / partial |
| 末行 | `_detail_json: {storyctl.status, gate.exit, fact.status, fact.executed, fact.assertion_stats}` |
| 依赖 | `STORYCTL_PATH` 环境变量优先；未设置则候选路径探测，**找不到 → verdict=partial（不是通过）** |

> 采用 K 后，我方 N16 封板可复用这个包装器作为单一入口 → TDD 方案 T-3b 的「方案 A 收口」成本显著下降。

### 3.5d 跨工具字数口径不一致（2026-11-01 对方接口审查发现，我方已独立复核）

**同一个"字数"，三个工具两种口径**：

| 来源 | 字段 | 口径 | 《顶职》第001章实测 |
|---|---|---|---|
| storyctl | `length.actual` | **去空白可见字符**（`visible_chars_v1`） | **2366** |
| 我方工作台 | `books.words_total` / `chapters.words` | **去空白可见字符**（`book-files.mjs:48` 同口径） | 2366 ✓ 与 storyctl 一致 |
| gate_runner | `metrics.visible_chars` | **纯汉字数**（`style_probe.py:85`，注释明写"历史口径，勿改"） | **1992** |

对方已在 `style_probe.py:85-90` 显式拆出三个键（**旧键语义不变**）：
`visible_chars`（纯汉字，历史）/ `cjk_chars`（纯汉字别名）/ `visible_chars_v1`（去空白可见，与 storyctl 同口径）。

**为什么这是缺陷而不只是"口径不同"**：
gate_runner 的两个阈值是**按纯汉字口径校准**的 —— `gates.json` 的 G6-a（单章字数下限 1500）
与 `min_chars_for_density_metrics`（1000）。而我方记录的是去空白可见字符，两者差 15.8%：

```
一章 1992 汉字 / 2366 可见字符
  → gate_runner：1992 < 2000 → 密度类指标 insufficient_sample（判不可靠）
  → 我方账本：  2366 → 「字数达标」
  → 同一章，两套口径给出相反结论
```
这与 §3.5b 那个"17% 差异"是**同一个坑的第二个入口**：上次的差在"对外承诺的字数"，
这次差在"内部判定的阈值"。

**处置**
1. **我方不改现有统计口径**（`visible_chars_v1` 与 storyctl 对齐，是对的）
2. 凡引用 gate_runner 的阈值/密度判断，**必须先换算到同一口径**：
   跨工具比较用 `metrics.visible_chars_v1`，对阈值用 `metrics.cjk_chars`，
   **不得**拿我方 `words_total` 直接比 gate_runner 的 1500/1000
3. 体检报告凡同时引用两边的字数，**必须标注各自口径**
4. 记入 TDD T-4b

### 3.5 平台门禁能力现状（2026-11-01 磁盘实测，P0 事实）

**三个平台今天的「可阻断闸门」都是 0。** 这不是 wrapper 缺失的下游症状，是平台配置本身不对称：

| 平台 | 统计层 gates | level=B | execution_mode | fact_layer 声明 | blocking_gates_remaining |
|---|---|---|---|---|---|
| qimao | 9 | **0** | （无） | ✅ **有**（F1–F4 = B） | **0** |
| fanqie | 6 | **0** | （无） | ❌ 无 | **0** |
| zongheng | 5 | **0**（level 值域是 `"advisory"` 而非 A/B） | `metrics_only` | ❌ 无 | **0** |

证据：`grep "fact_layer|execution_mode|blocking_gates_remaining" platforms/*/harness/gates.json`
- `qimao/harness/gates.json:105 fact_layer` / `:166 blocking_gates_remaining: 0`
- `fanqie/harness/gates.json:75 blocking_gates_remaining: 0`（无 fact_layer）
- `zongheng/harness/gates.json:7 execution_mode: metrics_only` / `:9 blocking_gates_remaining: 0`（无 fact_layer）

**三派生结论（全部影响对外承诺）**：

1. **统计层在任何平台都产不出 `rc=1`** —— `gate_runner.py:162` 只统计 `level=='B'`，而三平台 B 级数均为 0。
   因此「闸门通过」在文风维度上**不是质量信号，只是没崩**。UI 若显示绿色勾即为误导。
2. **唯一真实的阻断来源是 storyctl 质量检查 + fact_gate**，而 fact_layer 只在 qimao 声明、
   且 fact_gate **未接入 N16 自动封板**（见 §4.2c 与 TDD T-3b）。
   → 当前无人值守流水线的**实际阻断路径 = 仅 storyctl 质量层**。
3. **番茄/纵横连 fact_layer 都没有声明** → 即使接入 wrapper，这两平台仍是 0 阻断。
   依赖：对方补 `fanqie/gates.json`、`zongheng/gates.json` 的 `fact_layer` 声明（已承诺，走其接口变更流程）。

**我方必须做的两件如实呈现（不依赖对方）**：
- 界面与体检报告按 `--describe` 的 `stats_blocking_possible` / `fact_blocking_possible` 分流显示，
  文风层标「提示性，不阻断」，**不再对"闸门 9/9 PASS"给绿灯语义**；
- 体检报告显式列出"本版本实际会拦住什么"，把「不拦什么」写出来（对齐对方第 5 条假绿灯的教训）。

### 3.5b 校准数据的真实覆盖面（决定阈值能否对外承诺）

`platforms/qimao/corpus/` 根目录 **329 篇是「七猫作家学院」教学文章**，不是小说正文
（`_index.json count=329`，文件名带「写作技巧/创作规范」）。
小说正文只有：`positive/` **8 本 × 3 章 = 24** + `positive_niandai/` **6 本 × 3 章 = 18** = **14 本 / 42 章，且每本只有前 3 章**。
（注：对方 11-01 表述为"每本只有前 2 章 ch01/ch02"，与磁盘不符 —— 实测命名到 `ch03_第3章_*.md`，
 章数 24/18 正确，"前 2 章"应为"前 3 章"；不影响结论方向，但引用时以磁盘为准。）

| 后果 | 说明 |
|---|---|
| 基线只代表**开篇** | 42 章全部落在前 3 章（约 6–7 千字），**不能代表中盘节奏**。写作方案 `02_写作方案.md:53` 自己也写明「对照/拒稿文本来自被纵横驳回的作品，并非七猫拒稿」 |
| 番茄无校准数据 | `fanqie/corpus/` 271 篇**全是写作技巧，0 篇小说正文** → 番茄任何基线都无正文依据 |
| 无拒稿样本 → 无判别力 | `gates.json critical_finding`：「has Qimao signed-work text and a small cross-platform comparison, but **no Qimao rejected-work sample**. It cannot test Qimao signing discrimination.」 |

→ 与 §4.2b 的探针阈值冻结是**同一条纪律**：没有拒稿对照，任何"达标/能过签"的阈值主张都不可证伪也不可信。
所有对外文案禁用「总体规律」「平台标准」，只可写「n=14 本 / 前 3 章 / 教学文章 329 篇 / 无拒稿对照」。

### 3.5c 章号取自文件名前缀 + 强制三位补零 → 非补零命名被**静默忽略**（本轮新发现，与 DL-01 叠加）

触发点：对方指出纵横语料的 `chNN_` 前缀与标题章号不一致（我实测复核成立）。
顺着这条查我方代码，发现我方对"文件名前缀即章号"的依赖比想象的更深、更脆：

| 位置 | 现用正则 | 问题 |
|---|---|---|
| `server/sync.mjs:284` | `/^第(\d{3})章_(.*)\.md$/` | **要求恰好 3 位数字** |
| `server/sync.mjs:195` | `/^第(\d{3})章$/`（目录名） | 同上 |
| `server/run-manager.mjs:197` | `/^正文\/第(\d{3})章_/` | 同上 |
| `scripts/inline_runner.py:958 / :1539` | `re.match(r"第(\d{3})章", name)` | 同上 |

**后果（对开源用户是主要路径，不是边角）**：
流水线自己产出的名字是 `第001章_*.md`（Python `:03d` 格式化），所以自产书没事。
但**个人作者导入自己已写的章节**时，命名几乎必然是 `第1章_开局.md` / `第01章.md` / `第10章：供奉堂`
（对方纵横语料里就同时存在 `_`、空格、`：` 三种分隔符，见 `ch03_第10章：供奉堂.md`）——

> `第1章` / `第01章` **不匹配** `\d{3}` → sync **不加行、不报错、不警告** →
> 该章在 DB、书架、字数汇总、全文合成里**彻底隐形**。

与 DL-01 叠加后成为完整的静默缺章链：
```
作者导入 第1章…第12章（未补零）
  → sync 一个都不认（0 行）
  → preflight 读 state.words_total 缓存，仍报「字数达标 / 全部已定稿」  ← DL-01
  → packageSubmission 用 glob 合全文，或 continue 跳过缺失      ← DL-01/T-4
  → 产出「成功」的投稿包，里面缺章
  → 作者投出去，编辑看到跳号或断线 → 唯一一次签约评估机会作废
```

**我方处置（纳入 TDD）**：
1. **T-2b（新，P0）**：章号解析放宽为 `^第(\d{1,4})章` 并兼容 `_`／空格／`：`／`:` 分隔；
   同时**不再信任前缀作为唯一来源**——按对方同类修法，解析**标题内的「第N章」**为准，
   前缀与标题冲突即视为歧义（列出候选报错，不猜，沿用已定稿的第 4 条规则）。
2. **导入即体检**：`syncBook` 对「目录里有 `.md` 但一章都没匹配上」必须**显式报警**，
   绝不能 0 行还报成功。这条是 R4 fail-closed 的具体化。
3. **验收新增**：造一本 `第1章/第2章/第10章`（未补零）+ 一本 `第001章`（补零）+ 一本混用分隔符，
   三本 sync 后行数都必须等于文件数，且 preflight 不得在缺章时报 ready。

**同时给对方的对齐点**（已在回复中提出）：我方 `正文/` 命名规则一旦放宽，
对方 `chapter_check.py` 的正文定位须用同一规则，否则「你算的正文和我算的不是同一份」。

### 4.2d 契约变更（2026-11-01 对方落地后已验收通过）

对方 A 方案已落地并经我方实跑验收（命令见下），三平台**逐字一致**：

```
qimao    rc=0  verdict=ready   storyctl:exit=0 status=ready   gate:exit=0 status=pass
fanqie   rc=0  verdict=ready   storyctl:exit=0 status=ready   gate:exit=0 status=advisory
zongheng rc=0  verdict=ready   storyctl:exit=0 status=ready   gate:exit=0 status=metrics_only
```
且 `_detail_json` 三份顶层键集、verdict 集合、退出码语义全一致。

**我方界面直接印对方的这句**（已实测出现在输出里）：
> 本平台可阻断性：统计层=False 事实层=True 合计=True（calibrated=False）
> ⚠ 文风闸门为**提示性**：跑过 ≠ 质量过关，只是没崩。

**验收命令**（留档，供复现）：
```bash
cd "F:/小说/platforms"
for p in qimao fanqie zongheng; do
  PYTHONIOENCODING=utf-8 python -B "$p/tools/$p-chapter-check.py" \
    --project "F:/小说/works/顶职" --chapter 1 --genre shuangwen
  echo "rc=$?"
done
```
我方 N16 应接**平台中性**的 `platforms/_core/tools/chapter_check.py`，不接平台 shim（shim 是给既有路径用的）。

**已采纳的三条变更**
1. `--describe` 独立子命令 → 我方读 `blocking_gates` / `stats_block` / `fact_block`；
   界面把统计层标「提示性」、事实层标「硬闸」，**`calibrated=False` 一并显示不隐藏**
2. 正文复核内部改传 `--json` → 我方只调 wrapper，无影响；请对方保持 gate_runner 文本输出双形态可解析
3. fact_gate 退出码三分（0 非阻断 / 1 blocked / 2 配置或用法错误）→ 完全采纳，我方按此实现

#### 4.2e ⚠️ 新发现：同一章有**两个长度带、结论相反**（待对方澄清）

三份 `_detail_json` 的 storyctl 段落实测：
```
"target": 2100, "actual": 2366,
"internal_band": {min:1848, max:2352, status:"fail"},   ← 判 fail
"user_band":    {min:1785, max:2415, status:"pass"},   ← 判 pass
"signed_error_pct": 0.1267, "status": "borderline"
```
《顶职》第 1 章即 border：按内部带不合格、按用户带合格。

**我方现状**：`scripts/inline_runner.py:514` 只引用 `user_band`（note 文本就是 `user_band=...`），
**从不知道存在一个判 fail 的 `internal_band`** → 我方流水线对"这章字数合不合格"的认知
可能与对方内部标准不一致。若 `internal_band` 才是平台真实下限，则我方现状是**放宽了不该放宽的**。

**处置**：已去信追问三个问题（两带各自语义 / borderline 判定规则 / 我方该按哪条带判）。
未澄清前我方**不改** `inline_runner` 的字数判定，并把该条记入 TDD 待澄清项。

### 4.2c 契约消费规则（2026-11-01 实测确立，实现必须遵守）

对方承诺字段全部实测吻合（stub_900 / 不存在文件 / gbk 控制样本 / 混合批次四条命令，逐字段 MATCH，无失信）。
实测同时暴露三个**不写在契约里就会踩**的语义陷阱：

| # | 陷阱 | 证据 | 我方实现规则 |
|---|---|---|---|
| 1 | `status="advisory"` **有两种成因** | stub_900 的 advisory 来自 `gate_runner.py:220-221`（`undet and status=='pass' → advisory`，"未完全判定不得全绿"），此时 `advisory_count=0`、`findings=[]`；另一种才是真有 A 级问题 | **禁止**把 advisory 读成"有 N 个问题要改"。必须 `advisory_count` + `undetermined` 合起来判：`(count>0)→有提示` / `(count==0 且 undetermined)→没测准` |
| 2 | 故障行的**字段形状不同**：缺文件时 `undetermined` 与 `coverage` 整个不出现 | 混合批次实测：good 行 `advisory`+`undetermined`，missing 行 `has_error=true` 且**无 coverage 键** | `row["coverage"]` 在故障行会 `KeyError`。一律 `.get()`，且按 `has_error → undetermined → status` 顺序**短路**：见 has_error 就早退，不碰后续字段 |
| 3 | `blocked` / `rc=1` 分支**至今无法用真工具验证** | qimao 9 道闸门全为 level A，经 gate_runner 产不出 B 级阻断（三平台同构，此前已确认） | 该分支只能用**构造样本或 mock** 测试；不能声称"阻断路径已实测"。列为可测试性缺口（见 §7 风险） |
| 4 | **`undetermined=false` ≠ "判过"** | 对方实测：zongheng `metrics_only` + 短稿 → `undetermined=false` 但 `coverage={judged:0, observed:5}` | 我方判定必须同时看 `coverage.judged`；只看 `undetermined` 会把"一闸未判"读成"全部判过且没问题" |

> 陷阱 1 尤其关键：我方 `submissions.mjs`/`inline_runner.py` 若把 advisory 一律当作"有待修问题"计入报告，
> 会给作者制造**不存在的问题清单**；反过来若一律忽略，又会漏掉真提示。两者都必须靠 `advisory_count` 分流。

> 陷阱 3 是诚实性要求：TDD T-3 的"blocking 非空必须抛 fact_blocked"用例只能用夹具/mock 达成，
> 报告中须写明"经 mock 验证，真平台暂无 B 级闸门可触发"，不得伪装成端到端已验证。

### 4.2b 暂不写入 PRD 的外部能力（对方主动拦截，我方接受）

对方本轮新增两个探针 `mood_probe.py` / `check_payoff.py`，其 target_baselines
（≥20% / ≥8 / ≥3 / ≤14；≥80% / ≥1.5）**是从单本书手工对照推出的，尚未用三平台语料校准**，
且对方自查出两处自相矛盾：

1. 「章尾短句爆发」在《顶职》实测 **0.0**，与其复盘报告写的 **2.6** 不符 —— 探针按行统计且要求行末是句标点，对话行以右引号结尾即漏计
2. `check_payoff` 实测第三拍覆盖率 **77.8%**（9 章中 7 章有信号），与报告写的"9 章全部停在第二拍"不符 —— 疑似"没说话/把X推到桌上"这类克制文风高频词造成假阳性

**处置**：v1.0 **不引用这两个探针的任何阈值**。等对方给出带样本量的校准分布
（各指标 min/中位/max + 语料到底是签约正文还是教学文章）后再评估是否纳入。

理由：把未校准阈值写进 PRD，等于对外主张"能阻止平淡"——而这恰是本产品最容易被证伪的承诺，
也正是审计里 `writing-contract.md` 已经犯过的错（把 `calibrated:false` 的描述性统计写成"生死线"）。

### 4.3 不承诺的改动范围

| 类型 | 承诺 | 不承诺 |
|---|---|---|
| **CLI 参数顺序** | ✓ 不改 | ✗ |
| **退出码语义** | ✓ 保持 {0,1} | ✗ |
| **JSON 顶层键** | ✓ status/executed/not_executed_reason/assertion_stats 固定 | ✗ 新增 enum |
| **函数签名** | ✓ style_probe.probe(path) 不变 | ✗ |
| **存储格式** | ✓ fact_gate台账 JSON schema 兼容 | ✗ |

---

## 五、发布形态与迁移策略

### 5.1 GitHub 开源仓库结构

```
Novel Platform/
├── README.md                  ← 公开文档（删个人路径）
├── start-workbench.cmd/.sh    ← 启动脚本（引导安装 Node/Python）
├── novel-workbench/           ← 核心仓
│   ├── SKILL.md               ← 工具体验手册
│   ├── config.json            ← 绝对路径占位符（启动向导填充）
│   └── ...
├── references/
│   ├── prd-workbench-1.0.md   ← 本文档
│   ├── audit-baseline-2026-10-28.md
│   └── decisions.md           ← 决策记录
├── LICENSE                    ← 新增
└── CHANGELOG.md               ← 新增
```

**开源前脱敏动作清单：**
1. [ ] 删除 README.md:3 的提交身份声明（Wane.Zhihao）
2. [ ] 替换 config.json 所有 `C:\\Users\\Wane-` / `F:\\小说` 为占位符
3. [ ] 删除 `_phase0/probe/zc.html` / `zc2.html`（版权内容）
4. [ ] 将 personal path 写入新向导脚本（start-workbench.sh 第一步）
5. [ ] 确认所有测试 fixtures 不含他人签约作品（positive corpus 不在仓内）

### 5.2 zip 分发物结构

```
novel-workbench-win-x64.zip
├── run-novel-workbench.exe    ← 可选：Electron 打包的桌面壳（未来）
├── docs/
│   └── quick-start.md         ← 10 分钟安装指南
└── novel-workbench/
    ├── start.cmd              ← 启动器（检测 Node>=24）
    ├── configs/
    │   ├── sample-config.json ← 示例配置
    │   └── sample-agents.json ← 示例 Agent 配置（mock）
    └── platform-pack/         ← 可选：自制平台知识包（harness 无语料）
```

**runtime 策略选择：**
| 选项 | 优点 | 缺点 | 推荐度 |
|---|---|---|---|
| 捆绑 Node + Python 便携版 | 开箱即用 | zip 体积增大 ~120MB | ⭐⭐⭐⭐⭐ |
| 依赖用户自备 Node + Python | 体积小 | 首次体验差 | ⭐⭐ |
| 纯 Web 托管（Docker） | 部署简单 | 失去"单机"优势 | ⭐⭐⭐ |

**推荐方案：zip 内含 Node portable（约 70MB）+ Python embedded（约 50MB），启动器自动探测可用 runtime。**

### 5.3 升级迁移路径

| 升级场景 | 方案 | 风险 |
|---|---|---|
| schema v5 → v6 | db.mjs 迁移 + .bak-v* 备份 | 备份失败被静默吞掉（`server/db.mjs:28-34`，测试审计 T-09 同型问题） |
| tool chain 接口变化 | 先加兼容层，后移除旧代码 | 兼容层过长影响维护 |
| platform pack 扩容 | 下载额外语料 → 校验哈希 → 解压替换 | 下载失败导致不完整 |

---

## 六、TDD 执行规划

### 6.1 第一轮测试（修复 Critical）

| 测试 ID | 目标 | 失败时场景 | 预估工作量 |
|---|---|---|---|
| TDD-001 | M1: 正文文件存在性校验 | 删除第 006-009 章 → syncBook → preflight 应 fail | 4h |
| TDD-002 | M1: MANIFEST 必含全文.md | 删除全文.md → packageSubmission 应抛错 | 2h |
| TDD-003 | M2: _原稿_ 文件排除 | 放入 第005 章_原稿_.md → syncBook 不应更新章节 sha | 4h |
| TDD-004 | M5: fact_gate fail-closed | 模拟 fact_gate timeout → commit_chapter 应抛 fact_tool_unavailable | 3h |
| **小计** | | | **13h** |

### 6.2 第二轮测试（修复 High）

| 测试 ID | 目标 | 失败时场景 | 预估工作量 |
|---|---|---|---|
| TDD-005 | M3: words_total 双口径 | 对比 visible_chars_v1 vs 汉字数 → 体检报告应有双数字 | 3h |
| TDD-006 | security: network token | POST 带 wrong_token → 应返回 401 而非 200 | 2h |
| TDD-007 | static service XSS | upload `><script>alert()</script>.md` → render 应转义 | 3h |
| TDD-008 | concurrency: edit conflict | 两个浏览器窗口同时 save 同一章 → 应回滚冲突 | 6h |
| **小计** | | | **14h** |

### 6.3 第三轮测试（加固）

| 测试 ID | 目标 | 失败时场景 | 预估工作量 |
|---|---|---|---|
| TDD-009 | test-truth: fact-gate.test.mjs | npm test 漏跑 → CI 应红 | 1h |
| TDD-010 | docs-drift: SKILL.md N15 里程碑 | 文档说"1/3/6/10" vs 实际"1/3/6/10/20/22" | 1h |
| TDD-011 | maintainability: npm test auto-discover | 添加新测试文件 → npm test 应自动包含 | 2h |
| TDD-012 | reliability: autosave race condition | 保存中快速输入 10 次 → 不应丢失尾段 | 4h |
| **小计** | | | **8h** |

**总计：35h**（单人，熟悉代码情况下）

### 6.4 TDD 执行顺序

1. **Day 1**: TDD-001 ~ TDD-004（Critical → 确保投稿包真）
2. **Day 2**: TDD-005 ~ TDD-008（High → 确保安全性与一致性）
3. **Day 3**: TDD-009 ~ TDD-012（加固 → 确保测试与文档对齐）
4. **Day 4**: 回归测试 + PRD Review → Novel tools 接口审查
5. **Day 5**: 合并 + tag v1.0-RC1 → beta 分发

---

## 七、验收标准

### 7.1 Release Criteria（v1.0）

| 标准 | 达标证明 |
|---|---|
| npm test 114 用例全绿 | package.json 已改为 `node --test server/tests/*.test.mjs` |
| fact-gate.test.mjs 已纳入 npm test | `npm test server/tests/fact-gate.test.mjs` 通过 |
| 投稿包 全文.md 校验 | TDD-001 通过 |
| _原稿_ 文件排除 | TDD-003 通过 |
| words_total 双口径 | 体检报告有 visibly+CJK 两栏 |
| network token 鉴权 | TDD-007 通过 |
| static service XSS | TDD-006 通过 |
| autosave 竞态修复 | TDD-012 通过 |

### 7.2 Not In Scope（v1.0）

| 功能 | 原因 |
|---|---|
| 逐章暂停等审 | 用户核心需求是"无人值守 + 统稿"，逐章审批可作为后续迭代 |
| 多人协作 | 单机单用户的 MVP 优先级更高 |
| 云同步/Dropbox | 数据自持原则下，先做本地化 |
| 移动端适配 | 写稿/审稿主要在大屏终端完成 |
| 主题切换/自定义 CSS | UI 工程化后作为锦上添花项 |

---

## 八、已知风险与缓解

| 风险 | 可能性 | 影响 | 缓解措施 |
|---|---|---|---|
| Novel tools 接口突变 | 中 | 高 | 固化 A-J 十条契约，改前先通知 |
| Fact_gate 未接入门禁 | 已发生 | critical | 已有 PR（commit_chapter.py 191 行）待回归测试 |
| 测试覆盖率不足 | 高 | 中 | TDD 轮次重点补 |
| 开源版权纠纷 | 低 | 高 | 删除 zc.html，语料不进仓 |
| Node/Python 兼容性 | 中 | 中 | bundle portable 版本 |

---

## 九、附录：术语表

| 术语 | 定义 |
|---|---|
| **visible_chars_v1** | wordcount_core.py 的 count_visible_chars（去空白的所有可见字符） |
| **CJK 汉字数** | 纯汉字统计，不计标点/拉丁/数字 |
| **gate_runner** | F:/小说/platforms/_core/tools/gate_runner.py，文风闸门工具 |
| **fact_gate** | F:/小说/platforms/_core/tools/fact_gate.py，事实层闸门工具 |
| **sync.mjs** | server/sync.mjs，书架同步/指纹比对模块 |
| **state.json** | _pipeline/state.json，游标/节点状态真相源 |
| **tracking 账本** | 追踪/_tracking-state.json，字数/章节数真相源 |
| **has_error** | gate_runner/fact_gate JSON 中标记工具失败的布尔字段 |
| **undetermined** | gate_runner JSON 中标记判定不可靠的布尔字段 |
| **M1/M2/M3** | 门禁（Gate）编号，对应本节 3.1-3.3 |

---

## 十、修订记录

| 版本 | 日期 | 修订者 | 说明 |
|---|---|---|---|
| 1.0 | 2026-10-28 | Wane.Zhihao | 初始版本（基于审计证据 + 外部契约） |
| ? | ? | ? | ? |

---

> **本文档发送对象**：Novel tools 团队（接口面审查）
> **下一步行动**：请 Novel tools 确认 A-J 十条契约 + fact_gate status 字段语义，无异议则进入 TDD 执行阶段。
