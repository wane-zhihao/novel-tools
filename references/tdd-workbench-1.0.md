# TDD 执行方案 · novel-workbench 可宣发版 v1.0

> 依据：`references/prd-workbench-1.0.md`（目标与门禁） + `references/audit-baseline-2026-10-28.md`（实测事实）
> 方法：每个改动先写**失败测试（red）**→ 最小实现（green）→ 回归 → 提交。禁止先改实现再补测试。
> 边界：`F:\小说\**` 属另一个项目（Novel tools），全程只读，不修改。

---

## 0. 执行纪律

| 规则 | 说明 |
|---|---|
| R1 先红后绿 | 每条修复必须先在 `server/tests/` 或 `scripts/` 契约里有失败用例，且失败原因必须是"行为未实现"，不是 `ReferenceError`/语法错误 |
| R2 最小改动 | 只改到测试变绿为止，不顺带重构 |
| R3 不碰 peer | 需要 fact_gate/gate_runner 的异常行为时，用**临时目录副本**模拟，不改 `F:\小说\` |
| R4 判定只看 status | 凡调用外部工具，判定依据是 JSON 的 `status` / `has_error` / `undetermined`，**不靠退出码做内容结论** |
| R5 提交粒度 | 每条 finding 一个 commit，消息格式 `TDD-<id>: <行为> 修复（<finding>）` |

**开工前先做一次干净基线**（见 §1），否则后面无法区分"我改坏了"和"本来就坏"。

---

## 1. 基线（M0，开工门禁）

| 动作 | 命令 | 期望 |
|---|---|---|
| 记录环境 | `node --version` / `python --version` | Node ≥24，Python 3.12+ |
| 全量测试基线 | `node --test server/tests/*.test.mjs` | 记录 tests/pass/fail 数字（10-28 为 114/114） |
| 当前闸门基线 | `npm test` | 记录差异（10-28 为 100/100，漏 14 例） |
| 事实闸门基线 | `node --test server/tests/fact-gate.test.mjs` | 单独确认能否跑、缺 Python 时是否 skip |

**M0 通过标准**：三个数字写进 `.task/20261101-baseline.md`；任何 fail 必须在 §2 有对应条目，否则停止并先归因。

### 1.1 未提交改动的处置（阻塞项，先做）

工作区现状：`package.json` 与 `scripts/commit_chapter.py` 已修改、`server/tests/fact-gate.test.mjs` 未跟踪。
风险：`package.json` 已引用 `fact-gate.test.mjs`，但该文件未入库 → 一次 `git stash`/`git checkout -- package.json` 后 `npm test` 会引用不存在的文件。

处置：
```
git add novel-workbench/server/tests/fact-gate.test.mjs
git commit -m "TDD-M0: fact_gate 契约测试入库（补 10-28 未跟踪状态）"
```
并在提交信息里注明该集成改动的来源仍待溯源（见 PRD 风险表）。

---

## 2. 修复队列

优先级依据：PRD §3「不可妥协项」。每条给 finding、测试、实现点、验收。

### T-1 · DL-01（critical）投稿预检读缓存，可产出缺正文的投稿包

**现象**：`server/submissions.mjs:39` 用 `book.words_total`（源自 state.json 缓存）判字数达标；`server/sync.mjs:129-131` 同样从 state 读而非对 chapters 求和。删掉正文文件后，chapters 表行数下降但 words_total 不变 → 预检全绿 → 打包"成功"，MANIFEST 里没有 `全文.md`。

**Red（tdd-001）**
- 造一本书：正文 9 个 `.md`，state/tracking 记 9 章
- sync → 断言 chapters=9
- 删除其中 4 个正文文件 → 再 sync
- 断言 `preflightSubmission().ready === false`，且失败的 check 明确列出缺失章号
- 断言 `packageSubmission()` **抛错**，而不是返回成功

**Green（最小实现）**
1. `server/sync.mjs`：`words_total` 改为以 chapters 求和为准，与 state 值不一致时记 drift（state 只作参考）
2. `server/submissions.mjs` `preflight`：新增两项检查
   - 章节数三方一致：chapters 表行数 == 磁盘有效正文数 == `state.chapters_committed`
   - 逐章 sha 一致：磁盘文件 sha256 == DB `body_sha256`
3. `server/submissions.mjs` 打包循环：把 `continue` 改成计数；循环后断言 `全文.md` ∈ manifest，否则抛 `package_incomplete`

**验收**：删正文 → 预检 fail 且报出章号；缺 `全文.md` → 打包抛错。
**工作量**：M（60–100 行 + 测试）

---

### T-2 · DL-02（critical）`_原稿_` 备份文件被当成正文

**现象**：`server/sync.mjs:283-305` 的正则 `/^第(\d{3})章_(.*)\.md$/` 会匹配上游改稿流程产生的 `第005章_x_原稿_20261028.md`；两个文件抢同一 `number` → DB 行的 sha 指向旧稿。`scripts/inline_runner.py` 7 处 `glob("*.md")`（941/965/1120/1155/1528/1538/1569）与 `server/book-files.mjs:67-75` 同样无过滤 → 旧稿被合进 `投稿包/全文.md`。上游 `wordcount_core.py:279` 是排除 `_原稿_` 的，我方未对齐。

**Red（tdd-002）**
- `正文/` 内同时放 `第005章_x.md` 与 `第005章_x_原稿_1.md`
- sync → 断言 chapters 表 `number=5` 行的 sha 仍等于**真实正文**的 sha
- 合成全文 → 断言输出不含"原稿"标记文件内容

**Green**
1. 新增**单一**判定函数（三处共用，避免再次漂移）：
   - Node 侧：`server/book-files.mjs` 导出 `isChapterBodyFile(name)`，排除 `_原稿_` / `.bak` / `_backup`
   - Python 侧：`scripts/pipeline_spec.py` 定义 `is_valid_chapter_file()`，`inline_runner.py` 7 处调用
2. `server/sync.mjs` 收集章节时先过滤，再决定是否 upsert
3. 备份落位改为 `_pipeline/backups/正文/`（`book-files.mjs:87-104` 已有该机制，对齐即可）

**验收**：`_原稿_` 文件不进 chapters 表、不进全文.md、不进字数统计。
**工作量**：M（80–120 行 + 迁移脏数据）
**依赖**：T-1（两者都动 sync 的章节收集，同批改减少冲突）

---

### T-2b · 章号解析强制三位补零 → 非补零命名被静默忽略（P0，本轮新发现）

**现象**：四处正则都要求**恰好 3 位数字**，且把"文件名前缀"当章号唯一来源。

| 位置 | 正则 |
|---|---|
| `server/sync.mjs:284` | `/^第(\d{3})章_(.*)\.md$/` |
| `server/sync.mjs:195` | `/^第(\d{3})章$/` |
| `server/run-manager.mjs:197` | `/^正文\/第(\d{3})章_/` |
| `scripts/inline_runner.py:958 / :1539` | `re.match(r"第(\d{3})章", name)` |

流水线自产名字是 `第001章_*.md`（`:03d`），所以自产书没事；
但**个人作者导入自己已写章节**时命名通常是 `第1章_开局.md` / `第01章.md` / `第10章：供奉堂`
（纵横语料实测同库混用 `_`、空格、`：` 三种分隔符）。
`第1章` 不匹配 `\d{3}` → sync **不加行、不报错** → 该章在 DB / 书架 / 字数 / 全文合成里彻底隐形。

**与 DL-01 叠加 = 完整的静默缺章链**：导入未补零章节 → sync 认不到（0 行）
→ preflight 读 state 缓存仍报"字数达标 / 全部已定稿" → 打包"成功"但缺章 → 唯一一次签约评估作废。

**Red**
- 三本书各测：`第1章…第12章`（未补零）/ `第001章…`（补零）/ 分隔符混用（`_`、空格、`：`、`:`）
- 断言 `syncBook` 后 `chapters` 行数 == 目录内正文件数（未补零那本现在是 **0 行**）
- 断言"有 `.md` 但零匹配"时 syncBook **必须报警**，不得静默返回
- 断言未补零那本走 preflight 时，缺章必须让 `ready=false`

**Green**
1. 放宽为 `^第(\d{1,4})章` 并兼容四种分隔符（四处共用一个判定，勿各写一遍——T-2 已定此纪律）
2. **章号以标题内的「第N章」为准**，文件名前缀只作辅助；前缀与标题冲突 → 视为歧义，列候选报错不猜
   （与对方 `chapter_check.py` 同规则，避免两侧算出不同正文）
3. fail-closed：目录有文件但一章都没匹配上 = 错误，不是"0 章且成功"

**验收**：三本书行数都等于文件数；未补零书的缺章能被 preflight 拦住；两侧规则对齐。
**工作量**：M
**依赖**：T-2（同一套文件判定，必须一起改，否则两拨修复互相覆盖）

---

### T-3 · fact_gate fail-open（critical，安全类）

**现象**：`scripts/commit_chapter.py` 的 `_fact_check` 用 `json.loads(out)` 解析**拼接后的输出**。外部工具往 stderr 写一行 warning，或超时（rc=124, out 空）时，解析失败 → `blocking` 取不到 → 当作无阻断 → **定稿照常通过**。仓库里已有专门解决该问题的 `extract_json`，此处没用。

**Red（tdd-003）**
用临时目录副本模拟异常（不改 `F:\小说\`）：
1. 工具输出 = 一行 warning + 合法 JSON（含 blocking）→ 断言 commit **被阻断**（`fact_blocked`），不是静默通过
2. 工具超时（rc=124，stdout 空）→ 断言抛 `fact_tool_unavailable`
3. 工具文件不存在 → 断言抛 `fact_tool_unavailable`
4. `status="unverified"`（无台账）→ 断言 commit 结果里显式带 `unverified`，**不得记为 pass**

**Green（2026-11-01 按决策 B 重写：区分"缺席"与"故障"）**

**核心区分（B 只豁免"缺席"，不豁免"故障"）**

| 情形 | 判定 | 处置 |
|---|---|---|
| `FACT_GATE` 未配置 **且** 探测不到默认路径 | 能力**缺席** | ✅ **放行** + 登记 `reason=tool_absent` + 三处呈现（T-3c） |
| 已配置但文件不存在 / Python 不可用 / rc∉(0,1) / 输出不可解析 | 工具**故障** | ❌ **fail-closed**，抛 `fact_tool_unavailable` |
| rc=1 且 `blocking` 非空 | 内容阻断 | ❌ 抛 `fact_blocked` + findings |
| `status="unverified"`（无台账/无断言） | 未判定 | ⚠️ 放行 + 登记 `reason=unverified`，**不得记成 pass** |

即：**"没装"可以放行并标注，"装了却坏了"绝不能放行。**
否则 B 会被滥用成"任何异常都静默通过"——那正是要消灭的假绿灯。

实现要点：
1. **`FACT_GATE` 必须成为真配置**。当前是假的：`config.json`/`settings.mjs` 里**没有这个键**，
   `commit_chapter.py:55` 的 `cfg.get("FACT_GATE")` 永远 None → 永远走硬编码 `F:\小说\...` 死路径
   → 开源用户机器上必然"缺席"→ 按 B 放行。补进 `settings.mjs` 路径登记
   （与 GATE_RUNNER / STYLE_PROBE 同构，带 sources 与 exists），首次向导可选填。
2. `_fact_check` 返回**三态**而非 `(rc, payload)`：`(verdict, payload)`，
   `verdict ∈ {absent, tool_error, ran}`；把"缺席"从"错误"里拆出来，上层才能区别对待。
3. 解析改用 `inline_runner.extract_json`，**stdout/stderr 分开取**。当前 `json.loads(out)` 遇污染即失败
   → 返回 `{"raw":...}` → `fpayload.get("blocking")` 为空 → **静默放行**，这是当前最危险的 fail-open。
4. `web/lib/errors.js` 补 `fact_blocked`；`fact_tool_unavailable` 保留但语义收窄为"故障"。
   两个码各给**一个真能做的出口**（去设置配工具链 / 去审阅看阻断），不给假出口。

**外部契约（对方 2026-11-01 定稿，已采纳）**
```
exit 0 = 非阻断：pass / advisory / unverified
exit 1 = blocked        ← 稿子错了，改正文
exit 2 = 配置或用法错误  ← 闸门输入坏了，改台账
```
- 断言 token 不在可换算词表 → **exit 2**（该断言从未被执行，报 1 会误导作者去改正文，而正文没错）
- 缺配置要分两种，**不得混**：
  - 自动探测不到 `<书>/追踪/_fact-assertions.json`（新书首跑）→ `status=unverified` + **exit 0**
  - 显式传了 `--config` 而路径不存在 → **exit 2**（路径写错属用法错误，不得静默变 unverified）
- 我方 `fact-gate.test.mjs` 的 1 条红测试要改两处：**退出码 2**（非 1）**且换输入 token**
  （对方已把「今晚」纳入 REL 词表 → 今晚现在合法；须改用「当晚」这类仍非法且稳定的词）

**验收**：缺席→放行且登记；故障→fail-closed；阻断→拦截；unverified→显式标注。四种都不再"静默放行"。
**工作量**：M（B 分支 + 三态返回 + 配置真键 + 台账登记，比原估 S–M 大）
**依赖**：无（T-3c 依赖本条）

---

### T-3c · 防线缺席台账（critical，决策 B 的落地载体）

**为什么它是独立一条**：决策 B（缺席放行）成立的前提是"缺席永远可见、可追、可补"。
没有台账，B 就退化成静默降级——和 `writing-contract.md` 把 `calibrated:false` 写成"生死线"是同一类错误。

**Red**
1. 空环境（`FACT_GATE` 未配置且探测不到）→ 定稿**成功**，且：
   - `_pipeline/gate_absences.json` 出现一条 `{layer:"fact", reason:"tool_absent"}`
   - DB `gate_absences` 表有对应行
   - 生成的投稿包内**有** `防线缺席说明.md`
   - 投页/体检报告显示"本包未做事实校验（原因：事实闸门未安装）"
2. 工具链到位后对同一本书复检 → 历史缺席记录**仍在**（append-only），另加一条 `rechecked`，
   `absence_backlog` 计数下降
3. **防退化**：故意让某条缺席不出现在三个呈现面之一 → 测试必须失败
4. `gate_absences.json` 被截断 → 校验失败并报警（append-only 是审计前提）

**Green**
1. 书目录 `_pipeline/gate_absences.json`（append-only，风格对齐 `confirmations.json`）+ DB 新表 `gate_absences`
2. 写入点：定稿 / `preflight` / `packageSubmission` / N16 封板 / sync 章数校验
3. 三处呈现（缺一即视为未实现）：投稿包内 `防线缺席说明.md`（**编辑可见**）/ 投页与体检报告 / 书房书卡黄标 + 高级抽屉「缺席台账」tab
4. 补检回路：工具链到位或平台能力变化后，对历史 pack 比对"当时缺席 vs 现在能力" → 差集非空则生成「建议复检」待办；
   **不自动重打包**（破坏投稿包不可变性），只提示并允许显式重跑
5. 指标：`unverified_pack_rate`、`absence_backlog`、**`absence_hidden_count`（恒 0，>0 即 critical 回归）**

**验收**：见上 4 条；`absence_hidden_count` 恒 0。
**工作量**：M
**依赖**：T-3（先有三态判定，才知道什么算"缺席"）

---

### T-3b · 无人值守路径绕过事实闸门（critical，本轮新发现）

**现象**：事实闸门只挂在**人工定稿**那条路上。

| 路径 | 实现 | 是否跑 fact_gate |
|---|---|---|
| 作者在写页点「定稿」 | `scripts/commit_chapter.py:191` → `_fact_check(book)` | ✅ 跑 |
| 流水线 N16 自动封板 | `scripts/inline_runner.py:872 _commit_chapter()` → 只调 `cfg["STORYCTL"] chapter commit` | ❌ **不跑** |

产品主张是"AI 无人值守连跑一卷、作者最后统稿"。也就是说**作者不点定稿**——恰恰是不跑门禁的那条路。
结论：**自动化程度越高，防线越少**，与产品定位正好相反。这比 T-3（fail-open）更根本：T-3 是"接了但可能失效"，T-3b 是"主路径根本没接"。

**流程文档与代码不一致（已由第三方会话日志证实）**
另一个执行会话（WorkBuddy AI）在 `F:\WorkBuddy AI\2026-10-22-19-33-42\.workbuddy-ai\memory\2026-10-28.md:209-215` 申报了四层改动：
- **③ 流程接线**：把 `references/workflow-chapter.md` 步骤 8 的判据从"合并检查退出码是唯一判据"改成"**合并检查 0 且 事实闸门 0**"
- **④ 代码接线**：只改在 `scripts/commit_chapter.py`（人工定稿那条路）

也就是说：**书面流程已经要求跑事实闸门，自动化代码没有实现这条要求**。
按文档办事的编排者会跑，按流水线跑的 N16 不跑——同一个"封板"有两种含义。这类"文档前进、代码落后"的裂缝比单纯的缺功能更危险，因为它会让人相信防线存在。
→ T-3b 的验收必须包含"文档判据 == 代码行为"这一条，不能只测代码。

**Red**
- 造一本含事实错误的书（如日期断言与正文冲突，或"不合格是 3 个字"写成 4 个字）
- 走**流水线 N16**（`autopilot next/done` 或 `run_node_script("C1.N16")`）自动封板
- 断言：封板被拒绝，`blocked_reason` 含 `fact_blocked`，且 `last_committed_chapter` 不前进
- 对照组：同一本书走**人工定稿** `commit_chapter.py` → 断言同样被拦（证明两条路一致）

**Green（2026-11-01 复核后收敛为方案 A′，成本低于原估）**

对方四条主张我已逐条磁盘复核，**全部成立**：

| 主张 | 复核证据 | 结论 |
|---|---|---|
| storyctl **不含**事实层 | `story-long-write/scripts/storyctl.py` mtime **10-10 17:00**（早于 fact_gate 诞生），`grep fact_gate` = **0 命中** | ✅ 成立 → 我方**必须**自己接，不能等对方 |
| wrapper 已把 fact_gate 作为第 3 段 | `qimao/tools/qimao-chapter-check.py:8/61/190-207`，`FACT_GATE` 常量 + 全书检查 + `results['fact']` 汇总 | ✅ 成立 |
| wrapper 对 `unverified` 处理正确 | `:223-226` `unverified → verdict=partial`（"未验证不是阻断，但也不是干净通过"）；`:235-238` 输出不可解析 → `error` | ✅ 成立，比裸调 gate_runner 更稳 |
| 文档 :40「退出码 0 才算过」确为危险表述 | 原文实测存在，而 fact_gate 的 `unverified` rc 也是 0 | ✅ 成立，对方认领修其权威文件 |

**方案 A′（采纳）**：N16 不再自己调 `storyctl chapter commit`，改为调对方 wrapper 作为单一入口。
理由：wrapper 已把三段顺序、verdict 优先级、`unverified→partial`、解析失败→`error` 都处理好了，
我方重复实现一遍只会制造第二份会漂移的判定（本仓已有 `_genre_bounds_text` 双定义的前车之鉴）。

**但复核暴露一条对方方案没覆盖的坑（必须在实现前定调）**：

```
=== 各平台是否有 chapter-check 包装器 ===
--- qimao     → qimao-chapter-check.py   ✅
--- zongheng  → （无）                   ❌
--- fanqie    → （无）                   ❌
```

`qimao-chapter-check.py` 是**平台专属命名**，纵横/番茄没有对应包装器。若 N16 直接调它：
1. 代码里硬写死七猫路径 → 纵横书要么报错、要么悄悄不走门禁；
2. 纵横 `gates.json` 是 `execution_mode=metrics_only`（统计层结构上给不出阻断），
   又缺 wrapper 兜 fact_gate → **纵横书的封板可能一道阻断都没有**，比现状更糟。

**因此 T-3b 的前置条件**（新增 P0 依赖，未确认前不动 N16）：
- 向对方提出：把包装器**平台中性化**（`chapter-check.py --platform <dir>`，或为每平台提供同形入口），
  并确认 fact_gate 的 `--config <书>/追踪/_fact-assertions.json` 对三平台一致适用；
- 或我方在 N16 侧做**薄适配**：按 `st["platform"]` 解析包装器路径，找不到包装器时
  **退化为直接依次调 gate_runner + fact_gate**（按 §4.2c 消费规则判定），并在体检报告标注"走退化路径"。

退化路径必须 fail-closed：适配层缺失 ≠ 放行。否则又造出一个新的静默绿灯。

**验收**：同一本坏稿，人工定稿与 N16 自动封板**都被拦**，报错同源（同 code、同 findings）；
且**纵横/番茄书各测一次**，确认不是只有七猫有门禁。
**工作量**：A′=M（含平台适配与退化路径）
**依赖**：T-3（先修 fail-open）＋ 对方平台中性化答复

---

### T-4 · N21 投稿包路径无 error 检查（high）

**现象**：`scripts/inline_runner.py:1221` 处 `if rc not in (0,1): out = ""` 把工具崩溃吞成"指标未取到"；`:1232` 解析行不检查 `has_error` → 工具失败后**照常打包投稿**。

**Red**
- 让 gate_runner 对某章返回 `has_error=true`（临时副本）→ 断言 N21 不得把该章记为"已判定"，体检报告须显式列"未判定章"

**Green**
1. 去掉 `out = ""` 的静默降级，改为记录 `tool_error` 并计入未判定
2. 逐章解析处按 R4 判定：`has_error` → 未判定；`undetermined` → 提示；其余才看 status
3. 体检报告「五、文风与节奏（逐章）」表增加"未判定原因"列

**工作量**：S
**依赖**：T-3（同一套判定语义）

---

### T-5 · DL-03（high）字数口径与 20000 门槛

**现象**：`words_total` 用 `visible_chars_v1`（含标点/拉丁/数字）。真书实测 21,112（可见）vs 17,533（汉字），差 17%。门槛 `STATION_MIN_WORDS=20000` 硬编码，且 `F:\小说\platforms\qimao\config\thresholds.json` 里 `current_min_words: null`、`status: "provisional"` —— 20000 只是本地历史观察，不是官方条款。

**Red**
- 造 visible≈20500 / CJK≈17000 的样本 → 断言体检报告**同时**给两个数字
- 断言报告标注门槛为 provisional（"以编辑反馈为准"）
- 断言 `can_submit` 不再只看硬编码 20000：按书的 `target_words` 与所选口径判定

**Green**
1. `pipeline_spec.py` / `submissions.mjs` 的 20000 收敛为**单一命名常量**并带口径标注
2. 体检报告与 manuscript 展示双口径（可见 / 汉字）+ 差值百分比
3. `can_submit` 纳入 `target_words`（PRD §3.4），阈值来源与 provisional 状态透传
4. 门槛口径需作者确认目标平台算法 → 先做成**可配置**，默认展示双数字不做单一断言

**工作量**：M
**依赖**：T-1（都动 words_total 取值）

---

### T-6 · 测试闸门失真（high，流程类）

**现象**：`package.json` 手工列测试文件 → `npm test` 100 例、全量 114 例，漏跑 `commit` / `errors` / `run-guard`（其中 `commit.test.mjs` 覆盖单章定稿这条最危险写路径）。`ci.sh`/`ci.cmd` 调 `npm test`，因此 CI 同样漏。仓库无 `.github`，不会被自动发现。

**Red → Green**
- 改 `package.json` 的 `test` 为 `node --test server/tests/*.test.mjs`（自动发现）
- 加一条守护用例：比对 `ls server/tests/*.test.mjs` 与 `npm test` 实际执行文件数，不等则失败
- `python_contracts.py`：把 `test_status_exposes_volume_degradation`、`test_terminal_block_writes_relay_reason` 注册进 `main()`（34 → 36）

**验收**：`npm test` == 全量；两个孤儿契约执行。
**工作量**：S（最高性价比，建议最先做，因为它保护后续所有修复）

---

### T-7 · 服务退出不终止运行中的 run（high，可靠性）

**现象**：`server/index.mjs:80-83` 的 `close()` 只 `server.close()+db.close()`；`runManager.dispose()` 生产代码从未调用 → Ctrl+C 后 autopilot 子进程可能继续写书目录，而 DB 已关。

**Red**
- 起一个临时服务 + 一次 run，触发 `close()`，断言子进程被终止、`stop()` 被调用

**Green**
- `close()` 内先 `runManager.dispose()`（对每个 active run 走已有 `taskkill /T /F` 路径），再关 server/db
- Python 侧补齐：`scripts/inline_runner.py:84-108` 的 `TimeoutExpired` 只返回 124 不杀进程树 → 对齐 `runtime.mjs:62-69` 已有的 Windows 补杀做法

**工作量**：S–M

---

### T-8 · 幽灵书 + 无删除入口（medium，但影响首次体验）

**现象**：`books.id=1《联试之书》` path 指向不存在的 TEMP 目录，仍被 `GET /api/books` 列出；`shelf.js` 无 `lastBook` 时回落 `books[0]`，按 title 排序它第一 → 新浏览器默认打开一本坏书。路由表**没有** `DELETE /api/books`，`syncAll` 也不清理。

**Green**
1. 数据修复：删除或归档幽灵书（人工确认后）
2. 补 `DELETE /api/books/:id`（只删 DB 镜像行，不动磁盘；带确认）
3. 书架按 `updated_at` 排序，且默认书跳过 path 不存在的书
4. 缺失书在 UI 标注"目录不存在"而非静默可点

**工作量**：M

---

### T-9 · 写页保存可靠性（medium，防丢稿）

**现象**：`web/views/write.js:131-134` 保存请求在途时新一轮 `save()` 直接 return；`:155-157` 旧请求成功后把 `dirty=false` 并删本地草稿 → 保存耗时 > 1.2s 时尾段输入可能无提示丢失。`write.js:69-72` 定稿不检查 `save()` 返回值 → 冲突/取消后仍提交磁盘旧稿。

**Red**：模拟保存中继续输入 → 断言尾段不丢；save 返回 false → 断言**不**发起定稿。
**Green**：保存串行化 + 请求序号比对（过期响应不得覆盖新状态）；定稿前必须 `await save()` 且校验返回值。
**工作量**：M

---

## 3. 发布门禁（Definition of Done for v1.0）

| 门禁 | 判据 | 未达后果 |
|---|---|---|
| G1 测试闸门真实 | `npm test` 执行数 == 全量文件数，全绿 | 后续修复无保护 |
| G2 投稿包可信 | T-1/T-2/T-4 全绿：缺正文/混旧稿/工具崩 都能拦住 | 错误投稿（每本书只有一次评估机会） |
| G2b 无人值守同样受门禁 | **T-3b** 全绿：N16 自动封板与人工定稿拦住同一批坏稿 | 自动化程度越高防线越少，与产品定位相反 |
| G3 判定不靠 rc | T-3/T-4 全绿，外部工具异常一律 fail-closed | 坏稿静默放行（且 T-3b 接进去也是假门禁） |
| G4 数字可解释 | T-5：双口径 + provisional 标注 | 对外承诺与编辑口径不符 |
| G5 中断不写脏 | T-7：退出杀进程树 | 书目录被孤儿进程继续改写 |
| G6 首书能跑 | `agents.json` 配好真模型，real 模式起跑不被 400 拒 | 陌生客户第一次就撞墙 |
| G7 开源脱敏 | 无个人路径 / 无 `zc*.html` 越界语料 / 提交身份已处理 | 隐私与版权暴露 |
| G8 可分发 | 目标机器空环境跑通（运行时捆绑策略确定） | zip 发出去用不了 |

**G6–G8 属宣发形态，非代码 bug，放在 §4。**

---

## 4. 执行顺序与里程碑

```
M0 基线 + 未提交改动入库            （不达标不进 M1）
 └─ M1 保护网：T-6                  ← 最先做，它保护后面所有改动
     └─ M2 投稿可信：T-1 → T-2 → T-3 → T-3b → T-4
         └─ M3 数字与可靠性：T-5 → T-7
             └─ M4 首次体验：T-8 → T-9
                 └─ M5 宣发形态：G6 / G7 / G8（开源脱敏 + 运行时捆绑）
                     └─ tag v1.0-RC1
```

**顺序说明**：T-3 必须在 T-3b 之前——先把 fail-open 修好，否则把它接进 N16 只是多了一条"会静默放行"的路径。

**里程碑出口判据**：每个 M 结束时跑一次全量测试 + 更新 `.task/` 基线记录；M2 结束时必须做一次"删正文 / 塞原稿 / 工具崩"三场景实测。

工作量合计 ≈ 5–7 人日（单人、熟悉本仓库）。M1 半天即可完成，收益最高。

---

## 5. 外部依赖（Novel tools，我方只能等或适配，不能改）

| 依赖 | 状态 | 我方动作 |
|---|---|---|
| gate_runner `has_error` / `undetermined` 分离 | 已落地并实测 | 按 R4 消费 |
| fact_gate `executed` / `not_executed_reason` / `assertion_stats` | 已落地 | 体检报告用这三个字段呈现"N 条未判定 + 原因" |
| fact_gate `rc` 保持 {0,1}，`unverified` 走 status | 已按我方裁决落地 | 不加 rc=3 分支 |
| style_probe 编码 fallback，`probe(path)` 签名不变 | 已落地 | 依赖其不变；动了要兼容层 |
| qimao-chapter-check 三段式入口 | 待确认 CLI/退出码 | 已发问；确认后决定是否扩 A–K |

**待你方答复未落地前，T-3/T-4 的实现写成兼容两种形状**（有 `has_error` 走新逻辑，无则退回 `error` 键判定），避免被上游节奏卡住。

---

## 6. 明确不做（本版本）

| 不做 | 理由 |
|---|---|
| 逐章审批流 | 使用者工作流是"连跑一卷 + 统稿"；`approvals` 表已有但无 API/UI，做全套成本高、非当前阻塞 |
| 审阅档位三选一（直通/统稿/逐章） | 宣发版需要，但属 v1.1；v1.0 先保证"跑完的东西是真的" |
| 移动端运行面板 | 已知 `<=960px` 隐藏运行入口，属体验债 |
| 番茄平台接入 | `fanqie` 有 272 篇语料但未进 `PLATFORMS`，需先补 genre 基线 |
| L1 九维数值评分 | judge 校准集未做（人工盲评 ≥10 章），校准前不得上线 |
| 大文件拆分重构 | `inline_runner.py` 1674 行 / `api.mjs` 1393 行的职责切分留到 v1.1，避免与修复同批引入回归 |

---

## 7. 风险

| 风险 | 处置 |
|---|---|
| 修复期间外部工具契约变化 | T-3/T-4 写兼容层（§5）；上游改动要求先给 diff |
| M2 改动触及 sync 核心，可能影响已同步的书 | 每步跑 `sync.test.mjs`；DB 改动前留 `.bak` |
| 未提交的 fact_gate 集成被覆盖丢失 | §1.1 立即入库 |
| 基线数字与 10-28 不一致导致误判 | 以 `.task/20261101-baseline.md` 为准，不用旧数字 |
| **`blocked`/`rc=1` 分支无法用真工具验证**（qimao 9 道闸门全 A） | T-3/T-4 的阻断用例走 mock 或构造夹具，并在测试名与报告中**明示"mock 验证"**，不声称端到端已测 |
| **故障行字段形状不同**（缺文件行无 `coverage`/`undetermined` 键） | 消费侧一律 `.get()` + `has_error` 早退短路；写一条"故障行不得抛 KeyError"的用例 |
| **`advisory` 有两种成因**（真 A 级问题 vs 未完全判定） | 报告分流必须同时读 `advisory_count` 与 `undetermined`，否则要么造出不存在的问题清单，要么漏掉真提示 |
| 对方仓此刻有并发写者（`check_payoff.py` 15:55 / `mood_probe.py` 15:57 在验证期间被改） | 我方基线是**带时间戳的快照**，非稳定基线；引用外部行为时记录验证时刻 |

---

> 本方案落盘：`references/tdd-workbench-1.0.md`
> 待补：基线三项数字（`.task/20261101-baseline.md`）、stub_900 与不存在文件复跑结果
