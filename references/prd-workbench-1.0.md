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
| **事实准确性** | 有事实断言时执行 F1–F6；缺工具或无可执行断言时明确标记未验证并纳入缺席台账 | ❌ 我方人工定稿与无人值守 N16 路径均待按 T-3/T-3b 完成验证；三平台配置已声明 F1–F4 阻断层 | ✅ 两条路径共用门禁；允许能力缺席，但不允许把缺席显示为通过 |
| **字数口径透明** | 报告同时给「可见字符」与「纯汉字」两个数并注明门槛来源 | ❌ DL-03：单一 visible 口径，与汉字数天然差约 17%，且门槛 20000 未标 provisional | ✅ 双口径并列 + 口径可选 + provisional 标注 |

> **关于第三行的目标值**：差异不可能"降到 <1%"——中文正文里标点/数字本就被计入 visible_chars_v1，17% 是口径定义的结果，不是缺陷。
> 真正要达成的是**透明**：让作者和编辑看见同一件事的两种算法，并让门槛按所选口径判定。
> 把"差异<1%"当指标会导向错误修复（改计数函数），而正确的修复是改展示与判定。

> **关于第二行的路径缺口**：截至本轮核对，N16 自动封板仍未接入事实检查；PRD 旧版曾将人工定稿路径的事实检查接入描述为已确认，但当前实现仍需按 T-3 的三态规则修复并验证。T-3b 将 N16 接入平台中性 wrapper，确保无人值守路径具备同一事实检查并保留未验证状态。

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

**验收规则（实现以当前 JavaScript 服务层为准）**
1. 以已同步章节清单为基准逐章确认磁盘文件存在、正文哈希与 DB 镜像一致。
2. 对比磁盘现役正文、DB 章节行数和可验证的流水线已定稿章节数；若当前状态源不支持可靠取数，须先补齐该状态源再验收三方一致，不能把不可读的状态缓存描述为已核验。差异列出章号并阻止预检通过。
3. 投稿包生成再次验证必须产出的 `全文.md` 与 MANIFEST 条目；任何缺章或文件写入失败都返回明确错误，不可静默跳过。

#### M2: _原稿_ 备份文件排除

| 检查项 | 实现位置 | 实现方式 | 失败后果 |
|---|---|---|---|
| glob("*.md") 必须排除 _原稿_ 后缀 | scripts/inline_runner.py + server/book-files.mjs + server/sync.mjs | 统一在一个共享函数中判定 | 同一 chapter number 抢行、旧稿混入全文 |

**验收规则（实现以当前 JavaScript / Python 服务层为准）**
1. 章节发现、同步、全文合成与统计使用一致的现役正文判定规则；排除 `_原稿_`、`.bak`、`_backup` 等备份稿。
2. 同章现役稿与备份稿并存时，只将现役稿写入章节记录、全文和字数统计；不得由文件遍历顺序决定正文版本。
3. 共享判定需覆盖 Node 与 Python 调用路径，并由 T-2 回归用例验证；章号解析与文件名兼容规则按 T-2b 验收。

#### M3: words_total 双口径展示

| 检查项 | 实现方式 | 对外承诺 |
|---|---|---|
| 体检报告/投稿包同时给 visible_chars_v1 + 汉字数 | inline_runner.py:1163-1166 + submissions.mjs:39-45 | "可见字符 21,112 字（含标点）；纯汉字 17,533 字" |
| thresholds.json 中标注 provisional | F:/小说/platforms/qimao/config/thresholds.json | "2 万字为本地历史观察，非官方条款" |

**验收规则（实现以当前服务层及平台配置为准）**
1. 体检报告和投稿包同时显示 `visible_chars_v1` 与 CJK 汉字数，并标明各自口径。
2. 平台投稿门槛展示配置来源、计数字段和 `provisional` 状态；未确认的本地观察值不得写成平台硬门槛。
3. 同一字段不得跨口径比较；T-4b / T-5 负责字段选择、提示级别与门槛来源验收。

### 3.2 事实层闸门门禁（P0）

**目标：杜绝"事实错误进账本"的情况。**

#### M4: fact_gate 调用链接入

| 检查项 | 当前状态 | 要求 |
|---|---|---|
| 人工定稿 `commit_chapter.py` | 有 `_fact_check` 调用，但缺少稳定的 `FACT_GATE` 设置项，解析与错误分支仍需按 T-3 重做 | 区分 absent / tool_error / ran；按用户裁决，缺席可放行但需登记，已配置后故障必须 fail-closed |
| **无人值守 N16 封板** | ❌ 尚未接入事实检查；目前仅调用 `storyctl chapter commit` | T-3b：改用平台中性 `chapter_check.py`，并让两条路径对阻断、工具故障和 unverified 采取一致处理 |
| fact-gate.test.mjs | 已被 npm 自动发现，包含于 `npm test` | 保持跨环境可运行，外部工具缺席时按契约 skip |
| `unverified` 是否能区分 pass | fact_gate rc=0 可同时是 pass/advisory/unverified | 必须读取 `status`，不得因 rc=0 标记已验证；组合 wrapper 的结果为 partial，能力缺席按 T-3 记账后可继续 |

#### M5: fact_gate 返回值处理（三态与不静默）

现行 `_fact_check` 仍解析合并输出并把无法解析的内容降成 `{"raw": ...}`；上层随后只检查 `blocking`，存在阻断被吞的 fail-open 路径。T-3 负责替换该路径，现行代码片段不作为期望实现。

| 检查项 | 正确处置 |
|---|---|
| 未配置工具且默认路径不存在 | 识别为 `absent`；按用户裁决允许继续，但写缺席台账、在结果中标明未验证 |
| 已配置工具不存在、运行失败、rc=2 或 JSON 无效 | 识别为 `tool_error`；fail-closed，不得套用“缺席放行” |
| rc=1 且 JSON `status=blocked` / `blocking` 非空 | `fact_blocked` + findings |
| rc=0 且 `status=unverified` | 可以继续，但记为未验证，不得记为 pass |
| rc=2 | 按工具/配置错误处理；fact_gate 此时 stdout 无 JSON，错误从 stderr 取得 |

实现与验收以 `references/tdd-workbench-1.0.md` T-3 / T-3c 为准，不能继续使用旧版只按 `frc == 2` 的二态示例。

### 3.3 外部工具链接口契约（P1）

**已与 Novel tools 双方确认（2026-11-01 按对方接口审查清单修订）：**

| 维度 | gate_runner | fact_gate |
|---|---|---|
| **rc 取值** | **0**（非阻断）/ **1**（blocked）/ **2**（配置或用法错误） | 同上，**不加 rc=3** |
| **rc=2 时** | stdout **无 JSON**，错误只走 stderr → 调用方不得解析 stdout | 同上 |
| **status 枚举** | pass / advisory / blocked / metrics_only / **null**（仅 has_error=true 时） | pass / advisory / blocked / unverified |
| **error 标记** | `"has_error": true` + `"error": "..."` | **无**（故障通道是 rc=2 + stderr） |
| **undetermined 标记** | `"undetermined": true` + `"coverage": {...}` | 无（用 status=unverified 表达） |
| **判定逻辑** | 先处理 `has_error`，再看 `undetermined`，最后解释 `status`；rc=2 时 stdout 无 JSON，按工具/配置错误处理 | rc=2 时按配置/用法错误处理；rc=0/1 有 JSON 时必须读 `status`，`unverified` 不得折叠成 pass |
| **编码输出** | stdout 必须 UTF-8（调用方注入 PYTHONIOENCODING） | 同上 |

> **rc=1 的测试边界**：三平台统计层目前都没有 B 级 gate，因此正常内容不会触发统计层 B 级阻断；`gate_runner` 的退出码由 B 级阻断项数量决定。文件读取错误在 JSON 行中标为 `has_error=true`，不等同于内容阻断。`fact_gate` 的 rc=1 可由 F1–F4 事实断言触发。测试报告必须标清验证工具和隔离夹具，不得把统计配置能力与 wrapper 的组合阻断能力混为一谈。
>
> **per-gate level 值域**：qimao/fanqie 用 `"A"`/`"B"`，**zongheng 用 `"advisory"`**（5 道全是）→ 统计必须留 `other_level` 兜底，
> 否则纵横会被读成"一道闸门都没配"（少报式失真，比多报更难发现）。
>
> **两个探针的 rc 语义与 gate_runner/fact_gate 不同**：探针 rc=1 =「没有有效章节目录」、rc=2 =「未给目录」；
> gate_runner/fact_gate 的 rc=1 = blocked。**同名不同义，接入时不得混用。**

**关键约束（写进 PRD 的"不可变契约"）：**
1. **rc=0 不代表 pass** → 调用方必须检查 status 字段
2. **rc=1 不等于 content-blocked** → 可能是崩溃，要先看 stdout 是否为 JSON
3. **先处理工具/输入错误，再处理未判定，最后解释内容状态**：gate_runner 行先看 `has_error`，再看 `undetermined` 与 coverage，最后读 `status`；fact_gate 不提供 `has_error` / `undetermined`，rc=2 + stderr 是配置或用法错误，必须单独处理。组合 wrapper 的 verdict 优先级见 §4.2d。
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
| G | style_probe.py | `probe(path)`，提供 `visible_chars`（历史 CJK 口径）、`cjk_chars`、`visible_chars_v1` | gate_runner 动态导入；兼容时保留旧键语义 |
| H | clarity_probe.py / clarity_org_probe.py | gate_runner 动态导入；提供 `c_short_chain_max` 及 `causal_per_1000` / `narration_action_density` / `question_per_1000` / `action_chain_max` | 不是闲置依赖：七猫多道 gate 使用其输出；改函数或键名须核对所有引用 |
| I | build_writer_prompt.py | 注册于 settings | 当前调用状态以代码检索为准；不是 gate_runner 的动态探针 |
| J | fact_gate.py | `fact_gate <body_dir> [--config <json>] [--json] [--selftest]` | 读 `status`；rc=2 是配置/用法错误；`unverified` 必须显式呈现，不得当 pass |
| **K** | chapter_check.py（平台中性；qimao shim 转发） | `--platform <平台目录绝对路径> --project <书> --chapter N [--genre] [--file] [--extra]`；另有 `--describe` / `--selftest` | **N16 计划使用的唯一入口**（实施前须满足 T-3 三态策略）；三段键名 `storyctl`/`gate`/`fact`；`fact.status`、`fact.executed`、`fact.not_executed_reason`、`fact.assertion_stats` |
| L | 平台配置字段名 | `gates.json` / `genre-baselines.json` / `thresholds.json` / `style-anchor.json` 的键名 | 我方优先读取 `--describe`；不可用时回退 `gates.json` 现有字段。检查 `blocking_gates_remaining`、各 level 计数、`execution_mode` 和 `fact_layer`；不把 `--describe` 设为硬依赖 |

**K 条契约要点**（2026-11-01 对方以《顶职》ch1 实跑验证，exit 0）

| 项 | 取值 |
|---|---|
| verdict | `ready \| needs_decision \| blocked \| partial \| error` |
| 优先级 | 当前可达 verdict 顺序为 `error / partial > blocked > needs_decision > ready`；gate 的 `advisory` 保留在分段结果，不升级综合 verdict。共享源码 rank 字典仍有 `advisory: 2`，但没有 `degrade('advisory')` 调用，属于未清理的潜在排序项。`--selftest` 已覆盖 `error` 与 `blocked` 并存应为 error、`blocked` 与 `undetermined` 并存应为 partial；N16 接入前仍需核对跨平台运行场景 |
| 退出码 | `0` = ready / needs_decision（gate advisory 仍是提示）；`1` = blocked（storyctl 质量阻断、gate B 级、fact F1–F4 任一）；`2` = error / partial。因 fact `unverified` 会产生 partial，wrapper 返回 2；调用方须区分能力缺席与工具故障 |
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

**口径不同需要明确标注，但当前证据不支持“同章结论相反”**：

- 七猫 G6-a 的 `min=1500` 是 level-A 的残章提示，不是统计层阻断；1992 个 CJK 字也高于该值。
- 七猫 `min_chars_for_density_metrics=1000` 是密度指标的样本下限；同章 1992 个 CJK 字高于下限，不会仅因该条件被标为 `insufficient_sample`。
- 番茄 G6-a 的 `min=2000` 是 level-A 提示值，1992 个 CJK 字低于它时可能出现文案提示，但这不等于密度指标不可靠，也不构成阻断。

因此，已证实的问题是**两个工具报告不同计数口径**（1992 CJK vs 2366 visible chars），而不是这组样本触发了相反的门禁结论。跨工具展示仍需标注口径；阈值判断应使用对应配置和对应指标字段，不能把某个平台的提示阈值泛化成密度样本线。

**处置**
1. **我方不改现有统计口径**（`visible_chars_v1` 与 storyctl 对齐，是对的）
2. 凡引用 gate_runner 的阈值/密度判断，**必须先换算到同一口径**：
   跨工具比较用 `metrics.visible_chars_v1`，对阈值用 `metrics.cjk_chars`，
   **不得**拿我方 `words_total` 直接比 gate_runner 的 1500/1000
3. 体检报告凡同时引用两边的字数，**必须标注各自口径**
4. 记入 TDD T-4b

### 3.5 平台门禁能力现状（2026-11-01 磁盘复核，P0 事实）

**三平台统计层都没有 B 级 gate；三平台配置现在都声明了 F1–F4 事实阻断层。** `blocking_gates_remaining=0` 仅描述统计层，不代表合并的事实层也不能阻断。

| 平台 | 统计层 gates | 统计层 B | execution_mode | fact_layer | fact B gates |
|---|---:|---:|---|---|---|
| qimao | 9 | 0 | advisory | 已声明 | F1–F4 |
| fanqie | 6 | 0 | advisory | 已声明 | F1–F4 |
| zongheng | 5 | 0（level=`advisory`） | metrics_only | 已声明 | F1–F4 |

证据：三平台 `harness/gates.json` 中均有 `fact_layer` 和 F1–F4（2026-11-01 本地复核）；`chapter_check.py --describe` 对三平台均返回 `stats_blocking_possible=false`、`fact_blocking_possible=true`、`blocking_possible=true`。因此必须区分“统计层是否能阻断”与“组合门禁是否能阻断”。对方最新报告称 qimao/fanqie/zongheng 的 ready、事实阻断、正文歧义矩阵 9/9 通过；我方已复核源码和 `--selftest`，但未独立重跑这 9 个跨平台场景。当前 rank 字典残留未调用的 advisory 项，详见 §4.2d。

1. **统计层不构成质量通过信号**：三平台都没有统计层 B 级 gate；纵横使用 `metrics_only`，只输出指标。UI 不应把“闸门跑完”显示成签约质量绿灯。
2. **事实层具备阻断能力，但不能仅凭配置声明证明端到端接线有效**：当前 N16 是否调用平台中性 wrapper 由 T-3b 验收；在 T-3b 完成前，无人值守主路径不能宣传事实层已执行。
3. **calibrated=false 必须保留**：事实层阻断的是显式断言冲突，不代表文风指标已校准，更不代表预测签约结果。

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
| 番茄无校准数据 | `fanqie/corpus/` 教学材料不构成小说正文校准集 → 番茄任何签约判别或正文质量阈值都无正文样本依据 |
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

### 4.2d 契约变更（2026-11-01 复核；证据按来源区分）

平台中性入口 `platforms/_core/tools/chapter_check.py` 已在对方仓库实现；qimao shim 转发到该实现。三平台 `--describe` 实跑均返回 `stats_blocking_possible=false`、`fact_blocking_possible=true`、`blocking_possible=true`，并保留 `calibrated=false`。本轮本地复核 `--selftest` 通过。此前三平台 ready 样例输出也已验收。这些证据验证 wrapper 和配置描述，不等于 N16 已接线，也不等于所有阻断路径均完成端到端验收。

**已采纳的接口约定**
1. `--describe` 优先读取；若版本不支持，则回退 `gates.json` 现有字段，不能把新子命令当硬依赖。
2. 正文复核可用 `--json`；文本输出需继续保留兼容调用方可解析的形式。
3. `fact_gate` 退出码为 0 非阻断（含 `unverified`）、1 内容阻断、2 配置或用法错误；调用方必须结合 JSON `status`。rc=2 时不要解析 stdout。
4. 当前可达 verdict 顺序为 `error / partial > blocked > needs_decision > ready`；gate `advisory` 只留在分段结果，不改变综合 verdict。源码 rank 字典仍保留 `advisory: 2`，但无 `degrade('advisory')` 调用；`--selftest` 覆盖 error 优先和 blocked + undetermined → partial。对方报告三平台 ready / 事实阻断 / 正文歧义矩阵 9/9 通过；我方已运行 `--selftest`，跨平台 9 个场景仍标作对方报告证据，N16 接入前需在我方实际调用链复核。

我方 N16 计划接平台中性 `chapter_check.py`，但须在 T-3 缺席/故障三态策略完成后实施；qimao 平台 shim 不作为 N16 路径。

#### 4.2e 字数双区间语义（2026-11-01 已确认）

storyctl 的 internal band 为目标 ±12%，user band 为目标 ±15%；两者是本地派生区间，不是平台签约下限。`borderline` 表示 internal band 未通过但 user band 通过；storyctl 将其判为 `ready`。我方沿用 user band 的非阻断语义是正确的，但原报告没有展示 borderline 信号。

**处置**：不改变阻断行为；TDD T-3d 要在报告中显示 internal fail、user pass 和偏差百分比，作为非阻断提示。

### 4.2c 契约消费规则（2026-11-01 实测确立，实现必须遵守）

对方承诺字段全部实测吻合（stub_900 / 不存在文件 / gbk 控制样本 / 混合批次四条命令，逐字段 MATCH，无失信）。
实测同时暴露三个**不写在契约里就会踩**的语义陷阱：

| # | 陷阱 | 证据 | 我方实现规则 |
|---|---|---|---|
| 1 | `status="advisory"` **有两种成因** | stub_900 的 advisory 来自 `gate_runner.py:220-221`（`undet and status=='pass' → advisory`，"未完全判定不得全绿"），此时 `advisory_count=0`、`findings=[]`；另一种才是真有 A 级问题 | **禁止**把 advisory 读成"有 N 个问题要改"。必须 `advisory_count` + `undetermined` 合起来判：`(count>0)→有提示` / `(count==0 且 undetermined)→没测准` |
| 2 | 故障行的**字段形状不同**：缺文件时 `undetermined` 与 `coverage` 整个不出现 | 混合批次实测：good 行 `advisory`+`undetermined`，missing 行 `has_error=true` 且**无 coverage 键** | `row["coverage"]` 在故障行会 `KeyError`。一律 `.get()`，且按 `has_error → undetermined → status` 顺序**短路**：见 has_error 就早退，不碰后续字段 |
| 3 | 统计 gate_runner 的 B 级阻断当前不能由平台配置触发 | 三平台统计层都没有 B 级 gate；fact_gate 则有 F1–F4 B 级事实断言 | 统计层阻断使用构造样本/mock 并明确标注；事实层阻断可用满足 storyctl 前置条件的隔离书目录验证。记录夹具结构和实际触发阶段，不把工具故障或预检失败称为 fact_gate 内容阻断 |
| 4 | **`undetermined=false` ≠ "判过"** | 对方实测：zongheng `metrics_only` + 短稿 → `undetermined=false` 但 `coverage={judged:0, observed:5}` | 我方判定必须同时看 `coverage.judged`；只看 `undetermined` 会把"一闸未判"读成"全部判过且没问题" |

> 陷阱 1 尤其关键：我方 `submissions.mjs`/`inline_runner.py` 若把 advisory 一律当作"有待修问题"计入报告，
> 会给作者制造**不存在的问题清单**；反过来若一律忽略，又会漏掉真提示。两者都必须靠 `advisory_count` 分流。

> 陷阱 3 是证据边界：统计 gate_runner 的 B 级阻断只能用构造样本/mock 验证；fact_gate 的 F1–F4 有真实 B 级配置，可在完整隔离书目录中验证。夹具若先被 storyctl 或正文定位拦截，不能记为事实层内容阻断证据。

### 4.2b 暂不纳入质量门禁的外部探针（2026-11-01 复核）

`mood_probe.py` / `check_payoff.py` 提供描述性线索，不是闸门。对方最新接口复核补充：

- `mood_probe` 的章节级 `results[].results[].emotion_intensity_percent` 是该章自身占比；全书加权率位于 `totals.avg_emotion_pct`，多目录顶层 `totals` 含 `dialog_count`、`dialog_punct_count`。尾窗实际行数由 `tail_window_lines` 提供。对方报告《顶职》9 章共 527 条对话、71 条含情绪标点；旧逐章等权均值为 13.7%，按对话加权为 13.5%。
- `check_payoff` 的 `field_semantics` 位于 JSON 根对象；单目录章节字段路径为 `results[].results[]`，书级聚合位于根 `totals.*`。多目录时每个 `results[]` 是目录对象，目录的章节结果位于其 `results[]`、目录聚合在其 `totals.*`。新增清晰弱信号字段 `has_weak_signal_match`、`weak_signal_chapter_coverage_rate`、`avg_weak_signals_per_chap`；旧字段名称和值保持兼容。以上强弱模式字段都只代表模式命中/覆盖，不证明语义兑现；`tail_window_lines` 表示实际扫描窗口。
- 三个探针的所有阈值仍为 `calibrated=false`。正则/模式命中只提示人工复核，不构成质量结论、改稿要求或签约预测。
- 对方报告三个探针在 Windows 默认 GBK 下的编码回退用例已覆盖实际警告符；我方只读源码确认相关探测字符包含 `⚠`，并分别运行 `chapter_check.py --selftest`、`mood_probe.py --selftest`、`check_payoff.py --selftest`，三项均通过。对方本轮另外报告三平台独立/等号形式 `--platform` 解析、`chapter_check.py --describe` 缺参、`mood_probe --json` 多目录汇总及 `check_payoff` 字段语义回归通过；我方本轮未独立重跑这些新增接口用例。跨平台 9/9 入口矩阵是对方报告结果，本轮未独立重跑。

我方仅按显式契约字段展示指标与采样窗口，不消费兼容旧键作为语义结论。阈值在取得带样本量、语料来源和校准方法的证据前不得纳入门禁或对外承诺。

### 4.3 不承诺的改动范围

| 类型 | 承诺 | 不承诺 |
|---|---|---|
| **CLI 参数与退出码** | ✓ 调用形态及退出码按已复核契约消费；综合 verdict 以当前可达状态和自检覆盖为准，未调用的 `advisory` rank 项列为清理项，跨平台 9/9 结果注明为对方报告 | ✗ 把未独立验证的跨平台结果说成我方实测，或只按 rc=0 判通过 |
| **JSON 顶层键** | ✓ 消费已复核的 fact_gate `status` / `executed` / `not_executed_reason` / `assertion_stats` 字段 | ✗ 把可选字段视为必有，或擅自重命名既有键 |
| **函数签名** | ✓ style_probe.probe(path) 保持兼容 | ✗ |
| **存储格式** | ✓ fact_gate 台账 JSON schema 保持兼容 | ✗ |

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

### 6.1 当前修复队列（以 TDD §2 的 T- 编号为准）

| 顺序 | TDD 项 | 目标 | 验收重点 |
|---|---|---|---|
| 已完成 | M0 / T-6 | 测试闸门真实化 | 自动发现全部 `.test.mjs`；历史执行结果见 TDD §1.1 |
| 已完成 | T-1 | 投稿内容完整性 | 缺正文、DB/磁盘/定稿数不一致或 MANIFEST 缺全文时拒绝（2026-11-05 提交 18c1cf2） |
| 已完成 | T-2 / T-2b | 原稿排除与章号导入 | 备份稿不进入正文；未补零命名可导入，歧义明确报错（2026-11-05，tdd-002 11 条用例） |
| 3 | T-3 | fact_gate 三态与错误处理 | 缺席可继续并登记；配置故障 fail-closed；阻断与 unverified 正确呈现 |
| 4 | T-3c | 防线缺席台账 | 追加留痕、三处呈现及补检回路 |
| 5 | T-3b | N16 事实门禁接入 | 无人值守与人工定稿对阻断、缺席和故障处理一致 |
| 6 | T-4 | N21 未判定与工具错误处理 | gate_runner 故障行和 fact_gate rc=2 不得被当作通过 |
| 7 | T-3d / T-4b / T-5 | 字数信息与门槛 | borderline 提示、双口径、provisional 来源及平台门槛分离 |
| 8 | T-7 / T-8 / T-9 | 可靠性与首次体验 | 服务退出、幽灵书处理、写页保存可靠性 |

### 6.2 后续宣发验收

| 阶段 | 验收范围 | 放行条件 |
|---|---|---|
| 全量回归 | 相关测试、`npm test`、Python 契约测试 | 以当次命令输出为准；T-6 历史数字不替代本次结果 |
| 发布形态 | G6 首书运行、G7 开源脱敏、G8 空环境分发 | 目标机器验证完成，个人路径及越界语料清理完成 |
| 版本发布 | v1.0-RC1 | PRD Release Criteria 全部通过并完成单独发布验收 |

工时仅供排期，不作为验收或完成状态依据。
---

## 七、验收标准

### 7.1 Release Criteria（v1.0）

| 标准 | 达标证明 |
|---|---|
| 测试闸门完整 | `npm test` 自动发现 `server/tests/*.test.mjs` 并在发布验收当次全绿；Python 契约测试当次返回 `PY_CONTRACTS_OK` |
| 投稿包完整性 | T-1 通过：正文缺失、章节清单不一致、哈希不一致与 MANIFEST 缺项均明确拒绝 |
| 正文导入与备份隔离 | T-2/T-2b 通过：原稿不污染章节；非补零文件正常导入；歧义明确报错 |
| 事实门禁三态和无人值守路径 | T-3/T-3c/T-3b 通过：阻断、工具故障、能力缺席分别处理，缺席持续可追踪 |
| N21 判定完整性 | T-4 通过：未判定和工具错误不能伪装成内容通过 |
| 字数口径和投稿门槛 | T-3d/T-4b/T-5 通过：borderline 提示、双口径与 provisional 来源一致 |
| 运行与保存可靠性 | T-7/T-9 通过：退出时清理子进程，并发输入不丢失尾段 |
| 安全性与发布形态 | 对应安全验收、G6 首书运行、G7 脱敏、G8 空环境分发均完成 |

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
| Novel tools 接口突变 | 中 | 高 | 固化 A–L 十二项契约，改前先通知 |
| fact_gate 目前未通过我方两条封板路径完成验证 | 已发生 | critical | 依 T-3/T-3b/T-3c 落地三态、N16 接线和缺席台账；平台配置已有 F1–F4，不代表 workbench 已调用 |
| 测试覆盖率不足 | 高 | 中 | TDD 轮次重点补 |
| 开源版权纠纷 | 低 | 高 | 删除 zc.html，语料不进仓 |
| Node/Python 兼容性 | 中 | 中 | bundle portable 版本 |

---

## 九、附录：术语表

| 术语 | 定义 |
|---|---|
| **visible_chars_v1** | wordcount_core.py 的 count_visible_chars（去空白的所有可见字符） |
| **CJK 汉字数** | 纯汉字统计，不计标点/拉丁/数字 |
| **gate_runner** | F:/小说/platforms/_core/tools/gate_runner.py，文风统计工具；三平台统计 gate 当前不具备 B 级阻断能力 |
| **fact_gate** | F:/小说/platforms/_core/tools/fact_gate.py，事实层闸门工具；rc=0/1 时须读 JSON status，rc=2 为配置/用法错误且不保证有 JSON stdout |
| **sync.mjs** | server/sync.mjs，书架同步/指纹比对模块 |
| **state.json** | _pipeline/state.json，游标/节点状态真相源 |
| **tracking 账本** | 追踪/_tracking-state.json，字数/章节数真相源 |
| **has_error** | gate_runner 行中标记工具/读取失败的布尔字段；fact_gate 不提供此字段，故障用 rc=2 + stderr 表达 |
| **undetermined** | gate_runner JSON 中标记判定不可靠的布尔字段 |
| **M1/M2/M3** | 门禁（Gate）编号，对应本节 3.1-3.3 |

---

## 十、修订记录

| 版本 | 日期 | 修订者 | 说明 |
|---|---|---|---|
| 1.0 | 2026-10-28 | Wane.Zhihao | 初始版本（基于审计证据 + 外部契约） |
| 1.0 | 2026-11-01 | Novel Platform | 按接口复核更新事实层、统计口径、导入及测试计划；记录工具侧探针字段和验证边界 |

---

> **本文档发送对象**：Novel tools 团队（接口面审查）
> **修订状态（2026-11-01）**：已纳入 Novel tools 接口复核与最新探针字段说明。我方运行三个工具自检并核对共享源码；三平台 9/9 矩阵依据对方报告记录，未由我方独立重跑。Novel Platform 按 TDD 顺序继续实施，N16 接入需等待 T-3 三态策略完成。
