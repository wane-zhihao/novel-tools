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
| R4 内容结论读 status，工具故障读 rc | 内容结论依据 JSON 的 `status` / `has_error` / `undetermined`；`rc=2` 表示配置或用法错误且 stdout 无 JSON，必须按工具错误处理，不得把它当内容结论 |
| R5 提交粒度 | 每条 finding 一个 commit，消息格式 `TDD-<id>: <行为> 修复（<finding>）` |

**开工前先做一次干净基线**（见 §1），否则后面无法区分"我改坏了"和"本来就坏"。

---

## 1. 基线（M0，已完成）

M0 的环境与测试基线记录在 `.task/20261101-baseline.md`。T-6 后 `npm test` 改为自动发现，当前闸门结果以最新执行记录为准；10-28 的 114/114 全量数和 100/100 手工清单数仅作为历史基线，不再作为当前验收值。

| 动作 | 记录位置 / 结果 |
|---|---|
| 环境版本 | `.task/20261101-baseline.md` |
| 全量测试与 `npm test` 差异 | `.task/20261101-baseline.md`；T-6 将默认测试改为 glob 自动发现 |
| fact-gate 契约测试 | `server/tests/fact-gate.test.mjs`；外部工具可用时运行，缺席时按测试约定 skip |
| Python 契约测试 | T-6 已注册此前遗漏的两项；直接运行应输出 `PY_CONTRACTS_OK` |

**M0 通过状态**：基线记录已落盘，M0 集成改动已提交。不要重复执行旧的暂存/提交指令；新增改动仍按本方案 R5 分粒度处理。

### 1.1 T-6 测试闸门加固（已完成）

`package.json` 的 `test` 使用 `node --test server/tests/*.test.mjs` 自动发现；`test:red` 独立运行 `.red.mjs`；`test:gate` 提供外部事实闸门契约测试入口。`test-gate.test.mjs` 检查 glob、加载期错误特征和静态用例数下限。Python 两项孤儿契约已注册进 `main()`。

2026-11-01 提交 `694d462` 时记录：`npm test` 121/121 通过；`npm run test:red` 17 项均以预期断言失败（红灯状态）；Python 契约输出 `PY_CONTRACTS_OK`。后续改动须重新运行相关验证，不能沿用该快照作为当前通过证据。

---

## 2. 修复队列

优先级依据：PRD §3「不可妥协项」。每条给 finding、测试、实现点、验收。

### T-1 · DL-01（critical）投稿预检读缓存，可产出缺正文的投稿包

**现象**：`preflightSubmission` 若用 `books.words_total`（从 state.json 同步的缓存值）判字数，缓存虚高时会越过字数门槛；删章后的数据库刷新也可能删除证据行。投稿包文件复制若跳过缺失项，仍会产出缺文件的 MANIFEST。

**Red（tdd-001）**
- 造出 state 缓存字数高于章节行合计的书，断言投稿预检按 chapters 求和并不过字数线
- 对比 `state.chapters_committed`、tracking 定稿章数、DB 行和磁盘正文文件；删除正文或增加未定稿草稿时阻断并列出章号
- 修改正文或移除定稿摘要后，断言完整性预检失败
- 缺少 `全文.md` 或其他必需投稿文件时，断言 `packageSubmission()` **抛错**，不留下半成品目录或确认审计
- （终审追加）同章号两份现役正文、章节行指向书内旧稿、`全文.md` 为陈旧内容、tracking `actual` 虚高、章数非有限/超范围/缺账、正文或大纲目录逃逸书根、确认审计写入失败、包目录时间戳碰撞、API refresh 同步失败沿用旧库、BOM/CRLF 打包产物误拦、`_原稿_` 备份稿误拦、skip 同步后 DB 字数残留旧缓存、manuscript state 不可读/缺字段冒充零值——逐项断言失败或放行，证明各防线独立生效

**Green（实现，含 2026-11-01/05 两轮对抗审查补丁）**
1. `server/sync.mjs`：`books.words_total` 改为章节行 `SUM(words)`；不改写 state 文件，保留账本原值用于漂移披露。指纹跳过路径同样归正 `words_total`（旧缓存不再永久驻留），state 文件字节不动。
2. `server/submissions.mjs` `preflight`：字数从**已验证的磁盘正文**按 `countVisibleChars` 复算求和（tracking `actual`、DB `words` 必须与复算一致，否则报该章并拒批）；检查 DB、磁盘、`state.chapters_committed` 与 tracking 定稿记录一致性；逐章绑定「DB 路径 == 正文目录唯一现役文件」并比较磁盘摘要、DB `sha256`、tracking `body_sha256`；定稿章数限定 0–500 安全整数（超出立即报错，不展开循环）；`_原稿_`/`.bak`/`_backup` 备份稿不参与现役/重复/全文判定（谓词 `isBackupChapterFile` 在 `book-files.mjs`，sync/Python 侧统一排除属 T-2）；`全文.md` 存在时须与现役章节正文逐字节一致（CR 归一 + 双侧剥 UTF-8 BOM，容忍流水线「冠书名」与「裸正文」两种产物），陈旧全文拒批。
3. `server/submissions.mjs` 打包：打包前列出缺失必需文件并抛 `manifest_incomplete`；必需件复制前逐一经 `resolveInsideBook` 校验（目录链接逃逸书根即拒）；包目录名带 UUID 且以非递归 mkdir 独占创建（碰撞不再覆盖/删除历史包）；复制或 MANIFEST 写入失败时删除**本次**临时包；确认审计写入失败同样删除本次包并保持不追加审计。API 将缺件映射为 HTTP 400 并列出缺失路径；投稿 preflight/package 端点在 refresh 同步失败时返回 400 `submission_sync_failed`，不得基于旧库放行。
4. `server/api.mjs` `manuscript`：漂移直接读 state 文件原值；state 不可读或账本字段缺失/非数值时报 `state_unreadable`/`*_missing` 漂移项并把 `*_state` 置 null，不得用 0 冒充「账本记了 0」。

**验收**：缓存字数不影响字数门槛；章节、定稿摘要、字数记录或必需投稿文件缺失/不一致时预检或打包失败；陈旧/缺章的 `全文.md` 不能过预检；备份稿不误拦、非备份的同章重复必拦；章数越界快速失败不成环；同步失败不沿用旧库；打包失败不留半成品且不吞历史包；API 继续显示 state 与 DB 实际章节合计之间的漂移（state 不可读时如实说不可读）。tdd-001 19/19；全量 `npm test` 146/146（2026-11-05）。
**工作量**：M（60–100 行 + 测试）——实际含审查轮共约 300 行 + 19 用例

---

### T-2 · DL-02（critical）`_原稿_` 备份文件被当成正文

**现象**：`server/sync.mjs:283-305` 的正则 `/^第(\d{3})章_(.*)\.md$/` 会匹配上游改稿流程产生的 `第005章_x_原稿_20261028.md`；两个文件抢同一 `number` → DB 行的 sha 指向旧稿。`scripts/inline_runner.py` 7 处 `glob("*.md")`（941/965/1120/1155/1528/1538/1569）与 `server/book-files.mjs:67-75` 同样无过滤 → 旧稿被合进 `投稿包/全文.md`。上游 `wordcount_core.py:279` 是排除 `_原稿_` 的，我方未对齐。

**Red（tdd-002）**
- `正文/` 内同时放 `第005章_x.md` 与 `第005章_x_原稿_1.md`
- sync → 断言 chapters 表 `number=5` 行的 sha 仍等于**真实正文**的 sha
- 合成全文 → 断言输出不含"原稿"标记文件内容

**Green**
1. 新增**单一**判定函数（三处共用，避免再次漂移）：
   - Node 侧：T-1 已在 `server/book-files.mjs` 落地 `isBackupChapterFile(name)`（submissions 校验侧已用）；T-2 把它收敛为 `chapterFiles`/`findChapterFile`/`syncChapters` 的统一现役稿判定（`isChapterBodyFile = 匹配章号模式 && !isBackupChapterFile`），不再另起名字
   - Python 侧：`scripts/pipeline_spec.py` 定义 `is_valid_chapter_file()`，`inline_runner.py` 7 处调用（941/965/1120/1155/1528/1538/1569）——在 Python 侧对齐前，T-1 校验器对「全文混入备份稿」按不一致拒批（fail-closed，属预期）
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

### T-3 · fact_gate fail-open 与能力缺席处理（critical，安全类）

**现象**：`scripts/commit_chapter.py` 的 `_fact_check` 用 `json.loads(out)` 解析输出。stderr 污染、超时或输出不可解析时，阻断结果可能被吞掉并静默放行；需要把 stdout/stderr 与退出码分开处理。仓库已有 `extract_json`，但须确认其输入边界后再复用。

**Red（tdd-003）**
用临时目录副本模拟异常（不改 `F:\小说\`）：
1. 工具输出 = 一行 warning + 合法 JSON（含 blocking）→ 断言 commit **被阻断**（`fact_blocked`），不是静默通过
2. 已配置工具超时（rc=124，stdout 空）→ 断言抛 `fact_tool_unavailable`
3. 已显式配置的工具路径不存在 → 断言抛 `fact_tool_unavailable`；未配置且默认路径不存在则走 absent 分支，继续操作并登记 `tool_absent`
4. `status="unverified"`（无台账/无断言）→ 断言 commit 结果里显式带 `unverified`，**不得记为 pass**，并登记缺席原因

**Green（2026-11-01 按决策 B 重写：区分"缺席"与"故障"）**

**核心区分（B 只豁免"缺席"，不豁免"故障"）**

| 情形 | 判定 | 处置 |
|---|---|---|
| `FACT_GATE` 未配置 **且** 探测不到默认路径 | 能力**缺席** | ✅ **放行** + 登记 `reason=tool_absent` + 三处呈现（T-3c） |
| 已配置但文件不存在 / Python 不可用 / rc=2 / 输出不可解析 | 工具**故障** | ❌ **fail-closed**，抛 `fact_tool_unavailable` |
| rc=1 且 `status="blocked"` / `blocking` 非空 | 内容阻断 | ❌ 抛 `fact_blocked` + findings |
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

**验收**：缺席→操作可继续且登记、显式披露；故障→fail-closed；阻断→拦截；unverified→显式标注并使综合检查保持 partial。四种都不再"静默放行"。
**工作量**：M（B 分支 + 三态返回 + 配置真键 + 台账登记，比原估 S–M 大）
**依赖**：无（T-3c 依赖本条）

---

### T-3d · 字数双区间：判定口径已确认，但 borderline 被静默

**口径已确认（2026-11-01 对方给口径，我方逐行复核 `wordcount_core.py` 属实）**

```python
# wordcount_core.py:167-168
"internal": {"min": (target*88+99)//100, "max": target*112//100},   # 目标 ±12%
"user":     {"min": (target*85+99)//100, "max": target*115//100},   # 目标 ±15%
# :216-220  status = "internal_pass" if internal_pass else ("borderline" if user_pass else under/over)
# :163-165  作者给定区间时两带取同一区间，band_source="author"，不再有 borderline
```
- **两条带都是本地派生带，不是平台下限**（`band_source="default"` 即默认公式，非平台条款）
- `borderline` = 落在 ±12% 与 ±15% 之间那一圈
- 实测吻合：target=2100 / actual=2366 → +12.67% → internal fail、user pass、borderline ✓
- **我方该按 `user_band` 判** —— 与 `storyctl.py:255` 一致：
  `return "ready" if length["status"] in {"internal_pass","borderline"} else "needs_decision"`
  即 storyctl 自己在 borderline 时判 ready，**已把 internal fail 当作可提交**
- 我方 `inline_runner.py:507` 只在 `status in ("under","over","invalid")` 时产出 item → **borderline 不阻断，与 storyctl 对齐，正确**
  → **我方原行为不是遗漏，判定逻辑不改**

**但缺口真实存在**：对方要求「borderline 必须显式显示『内部带 fail、用户带 pass、偏离 +X%』，
不能只报一个 pass」——而我方对 borderline **完全静默**（连 note 都没有，作者看不到任何提示）。
这是"提示性信息被删掉"，与统计层 advisory 被吞是同一类问题。

**Red**
1. 造一章 `target=2100, actual=2366`（borderline）→ 断言体检报告**出现**该章的 borderline 提示，
   且同时显示：内部带区间+fail、用户带区间+pass、偏离 +12.7%
2. 断言该提示**不阻断**（不产生 B 级 item，不影响封板）
3. 造一章 `status="internal_pass"` → 断言**不**出现 borderline 提示（别制造噪音）
4. 造一章 `status="over"` → 断言仍产生 B 级阻断 item（现有行为不回退）

**Green**
- `inline_runner` 收集 length 结果时，若 `status == "borderline"`，产出一条 **level=A（非阻断）** 提示 item，
  note 含三要素：内部带 fail / 用户带 pass / signed_error_pct
- 体检报告章节表增加「偏离」列

**验收**：borderline 可见、不阻断、internal_pass 不噪音、over 仍阻断。
**工作量**：S
**依赖**：无（口径已明确，不再是外部阻塞）

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

### T-3b · 无人值守路径绕过事实闸门（critical，本轮新发现；以 `partial` 保留未验证状态）

**现象**：N16 自动封板路径未接入 fact_gate。人工定稿代码存在 `_fact_check` 调用，但其默认路径、rc 处理和 JSON 解析仍有 T-3 已确认的 fail-open / 缺席处理缺口，因此不能记作已可靠接入。

| 路径 | 实现 | 当前状态 |
|---|---|---|
| 作者在写页点「定稿」 | `scripts/commit_chapter.py:191` → `_fact_check(book)` | 有调用，但需先完成 T-3 三态、配置与错误处理 |
| 流水线 N16 自动封板 | `scripts/inline_runner.py:872 _commit_chapter()` → 只调 `cfg["STORYCTL"] chapter commit` | ❌ **不跑** |

产品主张是"AI 无人值守连跑一卷、作者最后统稿"。也就是说**作者不点定稿**——恰恰是不跑门禁的那条路。
结论：**自动化程度越高，防线越少**，与产品定位正好相反。这比 T-3（fail-open）更根本：T-3 是"接了但可能失效"，T-3b 是"主路径根本没接"。

**流程文档与代码接线需分别验收**
Novel tools 会话此前报告：`workflow-chapter.md` 步骤 8 已要求合并检查与事实闸门同时通过；我方只能把该报告作为接口背景，不能据此声称共享文档已在当前工作树独立复核。当前代码侧已确认人工定稿与 N16 的实现状态不同：人工路径的事实检查需经 T-3 修复，N16 尚未接入 wrapper。
→ T-3b 验收需同时核对经授权读取的流程判据与代码行为，不能仅测代码；不得由 Novel Platform 会话修改 Novel tools 的流程文件。

**Red**
- 在结构完整的隔离书目录中准备可执行的日期或数值事实断言，并确保 storyctl 和正文定位都能正常通过
- 走**流水线 N16**（`autopilot next/done` 或 `run_node_script("C1.N16")`）自动封板
- 对事实冲突样本断言：封板被拒绝，`blocked_reason` 含 `fact_blocked`，且 `last_committed_chapter` 不前进
- 对无 fact_gate 工具的环境断言：按用户裁决 B 放行，但产生日志/台账与显式提示，结果标为事实未验证
- 对已配置但损坏的工具断言：fail-closed；不得按“工具缺席”放行
- 对照组：同一组样本通过人工定稿路径运行，确认两条路径对阻断、缺席与工具故障语义一致

**Green（2026-11-01 复核后收敛为平台中性入口）**

对方已提供 `platforms/_core/tools/chapter_check.py`，qimao shim 仅负责转发。其实现串联 storyctl、gate_runner、fact_gate，并把正文歧义与工具错误保留为 error；`--selftest` 覆盖章节定位与“工具错 + 内容阻断”优先级。三平台 `gates.json` 均已声明 F1–F4 事实阻断层，`--describe` 均报告 `fact_blocking_possible=true`。

**T-3b 的实现方案**：N16 调用平台中性的 `chapter_check.py --platform <绝对平台目录> --project <书目录> --chapter N`，不直接调用平台 shim，也不在 workbench 重写 verdict 聚合逻辑。进入实现前先完成 T-3 的 absent / tool_error / ran 三态与缺席登记策略；wrapper 的 `partial` / `unverified` 必须被转成可见的缺席记录，不能因其 rc=2 一概当作工具故障，也不能当 pass。

**已确认的接口约束**：
- 当前可达 verdict 顺序为 `error / partial > blocked > needs_decision > ready`；gate `advisory` 只保留在分段结果。共享源码 rank 字典仍有未调用的 `advisory: 2` 项；`--selftest` 覆盖 error 优先及 blocked + undetermined → partial。对方报告的三平台 9/9 矩阵未由我方独立重跑，N16 接入前须在实际调用链验证 ready、事实阻断、正文歧义和工具错误。
- exit 0 仍须读各段 status；fact `unverified` 不代表通过。
- exit 1 表示组合检查有内容阻断；exit 2 表示错误/部分检查或用法错误，需结合 JSON verdict 和 detail 区分。
- `--describe` 可用于能力呈现，但我方须支持回退读取 `gates.json`，不把它设为硬依赖。

**验收**：N16 与人工定稿对同一事实错误都阻断；故意移除事实工具时依据 T-3 用户裁决允许工作流继续，但 wrapper 结果必须标记 `partial`、记录缺席并显式披露；配置好的工具损坏时 fail-closed；三平台分别覆盖 ready、blocked、正文歧义和工具错误场景。隔离书目录须具备大纲/追踪状态等 storyctl 前置条件，且正文定位必须命中唯一文件。若夹具先被 storyctl 或正文定位拦截，不能称为“纯事实阻断”证据；报告需记录夹具结构及实际触发阶段。
**工作量**：M
**依赖**：T-3（先落地三态策略及缺席台账写入接口）

---

### T-4b · 跨工具字数口径标注与字段选择（P1，2026-11-01 修正结论）

**现象**：同一正文在 gate_runner 以 CJK 字数和 visible 字符数两个字段报告（本例 1992 vs 2366）。这证明口径不同，需要报告标注；并未证明样例触发了相反门禁结论。七猫 G6-a=1500、番茄 G6-a=2000 都是 A 级提示值；七猫密度样本线为 CJK 1000，本例高于该线。

| 来源 | 字段 | 口径 | 《顶职》第001章 |
|---|---|---|---|
| storyctl | `length.actual` | 去空白可见字符 | 2366 |
| 我方 `books.words_total` | — | 去空白可见字符 | 2366 ✓ 一致 |
| gate_runner | `metrics.visible_chars` | **纯汉字数** | **1992** |

七猫 G6-a 的 1500 是 A 级残章提示，密度样本线 1000 是 CJK 字数条件；本例 1992 高于两者。
番茄 G6-a 的 2000 是另一平台的 A 级提示值，本例可能触发提示，但这不意味着密度指标不可靠，也不是内容阻断。

因此，本 TDD 处理的是报告精确呈现两个口径及按字段语义取数，不改 workbench 的 visible 统计，也不把提示性阈值升级为阻断门槛。

**Red**
1. 造一章使 `visible_chars_v1` 与 `cjk_chars` 差 >10%（如含大量标点/数字的章）
2. 断言报告同时显示两个数，并分别标记“可见字符”和“CJK 汉字数”
3. 对配置阈值读取对应字段：`metric=visible_chars` 使用对应平台现有语义（当前历史 CJK）；不得仅凭字段名把 `words_total` 当同口径数
4. 断言 A 级提示只能显示为提示，不能被报告或封板逻辑升成 B 级阻断

**Green**
1. 我方统计口径不改（`visible_chars_v1` 与 storyctl 对齐）
2. 报告统一从 gate_runner 的 metrics 中按 metric/config 口径取数
3. 体检报告章节表为每个数字显示口径标签

**验收**：报告能辨别两个口径；提示级别与源配置一致；不声称样例产生了未实际发生的 `insufficient_sample`。
**工作量**：S–M
**依赖**：与 T-5 同一批改（都动字数展示）

---

### T-4 · N21 投稿包路径无 error 检查（high）

**现象**：`scripts/inline_runner.py:1221` 处 `if rc not in (0,1): out = ""` 把工具崩溃吞成"指标未取到"；`:1232` 解析行不检查 `has_error` → 工具失败后**照常打包投稿**。

**Red**
- 让 gate_runner 对某章返回 `has_error=true`（临时副本）→ 断言 N21 不得把该章记为"已判定"，体检报告须显式列"未判定章"
- 让 fact_gate 返回 rc=2 → 断言该配置/用法错误单独显示，不解析空 stdout，也不能伪装成内容 pass

**Green**
1. 去掉 `out = ""` 的静默降级，改为记录 `tool_error` 并计入未判定
2. gate_runner 逐章解析按 `has_error → undetermined / coverage → status`；fact_gate 按 `rc=2` 工具错误、rc=0/1 时读 JSON `status`
3. 体检报告「五、文风与节奏（逐章）」表增加"未判定原因"列，并区分统计层未判定和事实层配置错误

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
1. `pipeline_spec.py` / `submissions.mjs` 的 20000 收敛为**单一命名配置项**并带口径标注
2. 体检报告与 manuscript 展示双口径（可见 / 汉字）+ 差值百分比
3. `can_submit` 对照书的 `target_words` 与预检完整性结果；平台提交门槛与本地写作目标分开展示，阈值来源及 provisional 状态透传
4. 门槛口径按配置提供；默认展示双数字与门槛来源，不把未确认的本地观察值包装成平台硬门槛

**工作量**：M
**依赖**：T-1（都动 words_total 取值）

---

### T-6 · 测试闸门失真（high，流程类）—— ✅ 已完成 2026-11-01

**现象**：`package.json` 手工列测试文件 → `npm test` 100 例、全量 114 例，漏跑 `commit` / `errors` / `run-guard`（其中 `commit.test.mjs` 覆盖单章定稿这条最危险写路径）。`ci.sh`/`ci.cmd` 调 `npm test`，因此 CI 同样漏。仓库无 `.github`，不会被自动发现。

**已完成**
1. `package.json` 的 `test` 改为 `node --test server/tests/*.test.mjs`（自动发现），另加 `test:red` 与 `test:gate`
2. 新增 `server/tests/test-gate.test.mjs` 三条守护：
   - 断言 `npm test` 必须是 glob、**不得手工列举多个文件**（100/114 漏跑的根因）
   - 断言每个 `*.test.mjs` 都能被加载（加载崩溃 = 零保护且用例静默变 0）；
     **注意：不能用 per-file ✔/✖ 判定**——没有任何用例匹配时 Node 会把文件标 ✖，那是假阳性；
     可靠信号是加载期异常特征串
   - 断言静态可数用例数不低于下限（防止统计口径或文件状态异常）
3. `python_contracts.py`：把 `test_status_exposes_volume_degradation`、`test_terminal_block_writes_relay_reason`
   注册进 `main()`（34 → 36）—— 实测 `PY_CONTRACTS_OK`，两条**真的通过**（此前从未执行过）
4. 四个 TDD 红灯用例改名 `*.red.mjs`（不被 `*.test.mjs` glob 吞进闸门），由 `npm run test:red` 单独跑

**实测结果**
```
npm test        → 121 tests / 121 pass / 0 fail   （改前 104；多跑的 17 = 漏掉的 14 + 新守卫 3）
npm run test:red → 17 tests / 0 pass / 17 fail   （全是 ERR_ASSERTION 的有效红灯，非加载崩溃）
```

**验收**：`npm test` == 全量 ✅；两个孤儿契约执行 ✅；红灯不进闸门但可单独跑 ✅
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
| G1 测试闸门真实 | `npm test` 自动发现全部 `.test.mjs` 且全绿；Python 契约测试完整执行 | 后续修复无保护 |
| G2 投稿包可信 | T-1/T-2/T-2b/T-4 全绿：缺正文、混旧稿、章号歧义、工具崩溃都可见且不能伪装完整 | 错误投稿 |
| G2b 无人值守同样受门禁 | T-3b/T-3c 全绿：N16 与人工定稿一致处理阻断、缺席与故障 | 自动化主路径防线缺失 |
| G3 判定不靠 rc | T-3/T-4 全绿；工具错误、unverified、内容阻断分别处理 | 坏稿静默放行或误报通过 |
| G4 数字可解释 | T-3d/T-4b/T-5：borderline 提示、双口径及 provisional 来源均正确 | 对外承诺与编辑口径不符 |
| G5 中断不写脏 | T-7：退出时清理子进程树 | 书目录被孤儿进程继续改写 |
| G6 首书能跑 | `agents.json` 配好真模型，real 模式起跑不被 400 拒 | 陌生客户第一次就撞墙 |
| G7 开源脱敏 | 无个人路径 / 无 `zc*.html` 越界语料 / 提交身份已处理 | 隐私与版权暴露 |
| G8 可分发 | 目标机器空环境跑通（运行时捆绑策略确定） | zip 发出去用不了 |

**G6–G8 属宣发形态，非代码 bug，放在 §4。**

---

## 4. 执行顺序与里程碑

```
M0 基线 + T-6 测试闸门加固            ✅ 已完成并提交
 └─ M2 投稿可信：T-1 → T-2/T-2b → T-3 → T-3c → T-3b → T-4
     └─ M3 数字与可靠性：T-3d → T-4b → T-5 → T-7
         └─ M4 首次体验：T-8 → T-9
             └─ M5 宣发形态：G6 / G7 / G8（开源脱敏 + 运行时捆绑）
                 └─ tag v1.0-RC1（需另行完成发布验收）
```

**顺序说明**：T-3 必须在 T-3b 之前——先把 fail-open 修好，否则把它接进 N16 只是多了一条"会静默放行"的路径。

**里程碑出口判据**：每个 M 结束时跑一次全量测试 + 更新 `.task/` 基线记录；M2 结束时必须做一次"删正文 / 塞原稿 / 工具崩 / 事实断言冲突 / 事实工具缺席"场景实测。历史工时估算仅为粗略规划，不视为验收事实。

---

## 5. 外部依赖（Novel tools，我方只能等或适配，不能改）

| 依赖 | 状态 | 我方动作 |
|---|---|---|
| gate_runner `has_error` / `undetermined` 分离 | 已落地并实测 | 按 R4 消费 |
| fact_gate `executed` / `not_executed_reason` / `assertion_stats` | 已落地 | 体检报告用这三个字段呈现"N 条未判定 + 原因" |
| fact_gate `rc` 为 {0,1,2}，`unverified` 走 status | 已按接口复核 | rc=2 不解析 stdout；0/1 的内容状态仍读取 JSON |
| `chapter_check.py` 平台中性入口 | 已实现；三平台 `--describe` + selftest 已复核。Novel tools 本轮另报告独立/等号形式 `--platform` 解析、缺参用法错误和三平台交叉 `--describe` 回归通过，我方未独立重跑 | T-3 后接入 N16；消费 verdict 时保留 error/partial 优先级 |
| `mood_probe.py` / `check_payoff.py` 输出语义 | Novel tools 本轮报告多目录 totals、弱信号字段与 `field_semantics` 回归通过；仅视为对方验证，工作台尚无消费点 | 不把正则候选匹配升级为语义兑现或质量结论 |
| 三平台 `fact_layer` F1–F4 声明 | 已在 gates.json 复核 | 配置声明不等于我方路径已接入；按 T-3/T-3b 验收真实执行 |
| `--describe` 能力摘要 | 已落地 | 首选读取；旧版本回退 `gates.json` |
| style_probe 编码 fallback，`probe(path)` 签名不变 | 已落地 | 依赖其不变；动了要兼容层 |


---

## 6. 明确不做（本版本）

| 不做 | 理由 |
|---|---|
| 逐章审批流 | 使用者工作流是"连跑一卷 + 统稿"；`approvals` 表已有但无 API/UI，做全套成本高、非当前阻塞 |
| 审阅档位三选一（直通/统稿/逐章） | 宣发版需要，但属 v1.1；v1.0 先保证"跑完的东西是真的" |
| 移动端运行面板 | 已知 `<=960px` 隐藏运行入口，属体验债 |
| 番茄平台的签约判断能力 | `fanqie` 缺少小说正文校准语料；接入平台目录不等于具备可对外宣称的签约判别力，题材/产品支持状态按实现和配置单独验收 |
| L1 九维数值评分 | judge 校准集未做（人工盲评 ≥10 章），校准前不得上线 |
| 大文件拆分重构 | `inline_runner.py` 1674 行 / `api.mjs` 1393 行的职责切分留到 v1.1，避免与修复同批引入回归 |

---

## 7. 风险

| 风险 | 处置 |
|---|---|
| 修复期间外部工具契约变化 | T-3/T-4 写兼容层（§5）；上游改动要求先给 diff |
| M2 改动触及 sync 核心，可能影响已同步的书 | 每步跑 `sync.test.mjs`；DB 改动前留 `.bak` |
| 已提交 fact_gate 集成仍存在 fail-open 与缺席误分类 | T-3 重做三态与配置路径；M0 提交只代表代码入库，不代表行为已验收 |
| 基线数字与 10-28 不一致导致误判 | 以 `.task/20261101-baseline.md` 为基线，T-6 提交的当前测试结果作为之后回归起点 |
| **统计层 B 级 gate 当前不可由真配置触发** | 统计 gate_runner 阻断测试应标明使用构造夹具/mock；fact_gate F1–F4 事实阻断另按隔离书目录测试，确认 storyctl 与正文定位没有抢先失败；报告注明实际验证阶段 |
| **故障行字段形状不同**（缺文件行无 `coverage`/`undetermined` 键） | 消费侧一律 `.get()` + `has_error` 早退短路；写一条"故障行不得抛 KeyError"的用例 |
| **`advisory` 有两种成因**（真 A 级问题 vs 未完全判定） | 报告分流必须同时读 `advisory_count` 与 `undetermined`，否则要么造出不存在的问题清单，要么漏掉真提示 |
| 对方仓此刻有并发写者（`check_payoff.py` 15:55 / `mood_probe.py` 15:57 在验证期间被改） | 我方基线是**带时间戳的快照**，非稳定基线；引用外部行为时记录验证时刻 |

---

> 本方案落盘：`references/tdd-workbench-1.0.md`
