# Novel Platform 对抗性审查报告（2026-10-25）

> 结论先行 · 范围：master @ dd2dc1a 全仓库（worktree 清点见下）· 方法：四路并行对抗审查（Python 核心 / Node 服务端与运行时 / Web 界面 / 实证与测试）+ 磁盘实证 + 测试复跑 + 全网基准检索 + 关键证据亲自复核 · 立场：找"错了什么 / 高估了什么"，不复述成绩

## 一、总体判断（结论先行）

**扎实的部分**（不复述）：节点全拓扑可执行；闸门有判别力（阳性对照实测）；追踪事务 schema 严苛且已测绘；数据层 / 运行时 / UI 四层都在盘，测试 52/54 过；worktree 清点干净（全机仅 1 个）。

**核心问题**：上一轮 A1/A2/A3 三个信任假设，一个都没有真正修复；系统变得更复杂后敞口反而更大：

1. **step_token 是搬家不是拆锁**——令牌从 state.json 移到 `.step_token`，但同目录、同权限，subagent 提示词本就指向 `_pipeline/`；且 `next` 自己向调用方发令牌。"100% 机械隔离"应从所有文档撤回。
2. **done 验收的第二、三道是装饰**——sha256 只记录不比对、evidence 用调用方传入值、不与 spec 期望模式比对。持令牌者可自建占位文件完成越权推进，伪造链完整。
3. **两条零成本新攻击路径**（本轮新发现，均已实证）：Windows 8.3 短名写穿保护名单（真把 state.json 改坏了）；`_pipeline/agents.json` 可被 subagent 改写（持久化提示注入 + 手稿外泄通道）。
4. **UI 核心循环断路**：v2 书房与制作台是互相没有出口的两个应用；API 失败被误报成"书房还空着"。
5. **words_total 在真实书目上为 0**——"保底 2 万字"的停止条件是死的，只能靠 max_chapters 兜底。
6. **judge 未校准（上轮遗留）且 judgments 文件可被 writer 覆写**——L1 九维评分层若现在上线，是把未校准的裁判制度化，还把裁决记录交给被裁判者保管。

**一句话**：管道确实修通了，但"石油"（无人值守）仍然不能自动流；这一轮的新事实是——连"油桶"（控制文件）都能被伪造，而且日常拧阀门的人（作者）每天要在两个断路的界面之间横跳。

## 二、声称 vs 实证核对表

| # | 声称（出处） | 实证 |
|---|---|---|
| 1 | step_token 越权 100% 机械拦截（README:258 / plan 6.4.1） | ❌ 令牌与 state.json 同目录；next 向调用方明文发令牌；subagent 全工具权限 |
| 2 | done 四道验收含 sha256 一致（commands.py docstring） | ❌ 只记录不比对；evidence 不校验 spec 模式 |
| 3 | 禁止改写 state.json/.step_token/workbench.json/run.json（README:169） | ⚠️ 正常路径拦；`_PIPEL~1/` 8.3 别名写穿（实证改坏 state.json）；agents.json 不在名单（实证可写） |
| 4 | 只接受回环 Host、写请求 Origin 必须本机（README:202） | ⚠️ Host 有效；Origin 只校验主机名不校验端口，任意回环端口放行 |
| 5 | token 不出进程、不进结果、不进日志（README:170） | ⚠️ 结果/日志成立；经子进程 argv 可被本机进程管理器读取 |
| 6 | N14 done 后自动重跑 N13（plan:120,142） | ❌ next_cursor 对 N14 直达 N15（pipeline_spec.py:397-399，已亲自证实） |
| 7 | 失败回滚到 Blocker 最少快照（plan:328） | ❌ 快照只有写入方，全仓无恢复代码 |
| 8 | C02 夹具回归 words_total 回写正确（README:290） | ❌ 仅夹具内成立；真实 book2 words_total=0（第 1 章已封板 2380 字） |
| 9 | 测试全绿（README:296 等） | ⚠️ 本机 52 过 / 1 崩（Node 22 fs.cpSync 段错误，引擎要求 ≥24）/ 1 取消 |
| 10 | package.json 版本 0.6.0（README:249） | ❌ 磁盘实为 0.7.0（version.mjs 单一来源机制本身正确） |
| 11 | 每封板 3 章写 relay.md（SKILL.md:104） | ⚠️ 代码在盘（%3==0 触发）；book2 仅封 1 章未触发，不可当作已验证事实 |
| 12 | 全项目含所有 worktree 已审查 | ✅ 全机清点：仅 1 个 worktree；story skill 与 F:\小说 均非 git 仓库 |

## 三、发现清单（按 影响面 × 成本 × 风险 排序）

### A 级——信任假设与安全（不修则上层建筑虚）

**A1. step_token：搬家不是拆锁。** 证据：autopilot.py:164-205（令牌存 `_pipeline/.step_token`，与 state.json 同目录同权限）；pipeline_spec.py:345（N15 提示词引导 subagent 读写 `_pipeline/`）；autopilot.py:478-489（next 向调用方明文输出 token+done_cmd）。两条复现路径成本都低于原攻击：① 读同目录令牌文件（Node 沙箱的扩展名白名单能挡 read_file，但对话 subagent 是全工具权限，一条 Bash 即可）；② 任何进程调一次 `next`，机器当场发新令牌。影响：被注入的 subagent 可推进任意 llm/verify 节点。成本：低（改文档声称零成本，改机制需工具面受限）。风险：高。修复：短期——从 README/SKILL/plan 撤回"100% 机械隔离"，改称"提高越权成本的速度 bump"；中期——done 增加 evidence 与 spec 期望模式比对 + 内容抽样断言（节点特有关键词）；根治——subagent 收敛到受限工具面（Node 运行时已有沙箱，可把对话 subagent 也迁过去）。

**A2. done 验收的第二、三道是装饰。** 证据：commands.py:5-9 docstring 声称"sha256 与 state 一致"，248-254 行只计算存储、无任何比对旧值处；commands.py:207 用调用方传入的 `a.evidence`，不比对 `spec["evidence"]` 期望模式；verify 节点的"退出码 0"无对应物。与 A1 叠加 = 完整伪造链：持令牌 → 指认任意大文件 → 节点 done。影响：状态机推进的全部验收价值只剩"文件存在且够大"。成本：低。风险：高。修复：done 断言 evidence 匹配 spec 模式；sha256 要么实现变更比对（变了标 stale）要么从 docstring 删除。

**A3. Windows 8.3 短名写穿保护名单（本轮新发现，已实证）。** 证据：tools.mjs:8-15 保护名单按字面 rel 比对；tools.mjs:134-136 rel 由 relative() 纯字符串计算，无 realpath 归一化。实证：`write_file({path:"_PIPEL~1/state.json"})` 返回 ok，真实 state.json 被改为占位内容；findings/run.json/workbench.json 同理。影响：子代理（或经污染证据文件的提示注入）可伪造状态机——cursor、封板标记、findings 全可改，等于关掉闸门。成本：极低（一次工具调用）。风险：高。修复：rel 比对前 realpathSync.native 归一化；或拒绝含 `~` 的路径段。

**A4. `_pipeline/agents.json` 可被 subagent 改写（本轮新发现，已实证）。** 证据：tools.mjs:15 PROTECTED_PREFIXES 不含 agents.json；agent-config.mjs:198 书级覆盖即读此文件。实证：write_file 可写。攻击形态：写入 evil provider（base_url 指向任意服务器、api_key_optional 免密钥通过结构校验）→ 后续每次 run 的全部 prompt 与手稿正文外发。影响：持久化提示注入 + 数据外泄，一次写入长期生效。成本：低。风险：高。修复：agents.json、submissions.json、judgments_*.json 加入保护名单。

**A5. Origin 校验不校验端口 → 本地 CSRF/RCE 链。** 证据：http.mjs:100-105 只比对 Origin 主机名是否在 {127.0.0.1,::1,localhost}，不校验端口。链路：本机任意 localhost 恶意页 → PUT /api/settings 把 python 改为任意 exe（settings.mjs:42-44 会 spawn 探测）→ POST /api/books 触发执行。影响：浏览器里任何一个本地应用即 RCE。成本：低。风险：高（本地威胁模型内）。修复：Origin 精确匹配服务端自身 origin（两种字面量）+ 校验 Sec-Fetch-Site + 写操作要求自定义头。

**A6. judge 未校准 + 裁决记录可被被裁判者覆写（上轮遗留 + 新面）。** 证据：judgments_*.json 无写保护（tools.mjs:15），writer 角色可覆写 judge 历史裁决；上轮 A3（self-preference 偏见、无人类基准）未动。影响：L1 九维权重（beat .25 等）是未经校准的先验，直接上线 = 把偏见固化进结构，且校准数据可被污染。成本：中（需要人工盲评基准集）。风险：高。修复：judgments_ 加入写保护；L1 搁置至校准集存在（见第四波 P3）。

**A7. "完全无人值守"实证仍为零（上轮遗留，本轮确认）。** 证据：真实新书端到端未跑（book2 暂停在 C2.N12，blocked_reason=用户指示）；联试期三次 blocked 全部依靠人工诊断恢复，无一次自动恢复。外部基准：OrchestraBench 把 recovery 列为专门评测对象——我们的 recovery 外包给运气。影响：项目第一卖家不可验收。成本：中。风险：中。修复：见第四波 P3；在有人工在环的真书跑出来之前，"无人值守"四个字不进任何汇报。

### B 级——工程正确性（按组）

**状态机组**
- **B1 N14 改写后 N13 不复跑**（已亲自证实：pipeline_spec.py:397-399 对 N14 直达 N15，违反 plan:120,142）。影响：N14 改崩的平台指标（因果 1.0–6.0、动作词 ≤41、段/千字 33–54）再无机械闸门。修：next_cursor 对 N14 返回 N13，注意 findings 累积与 fix_rounds 交互。
- **B2 resume 两处错**：不清 fix_rounds/n20_rounds（commands.py:294-314，终停后人工修复再 resume，第一次再 block 即再次终停）；docstring 声称的"扫盘重建 + 与 _tracking-state.json 交叉校验"未实现，只翻状态位。修：终停节点轮次清零；补 cursor↔last_committed_chapter 交叉校验。
- **B3 快照回滚只写不读**（inline_runner.py:954-971 只有写入方，全仓无恢复代码）——plan:328 的"回滚到 Blocker 最少快照"是空头支票。修：终停路径前实现按 blocker 数选快照恢复，或删除声称。
- **B4 并发锁只锁 next**：done/skip/block/resume 全无锁；next 内联可跑数分钟，期间另一驱动 done 推进会基于陈旧 state 覆盖游标。修：done/skip 获取同一 .next.lock。
- **B5 block/resume 无令牌 = 免费停机键**（commands.py:283-291），任何进程一句话停摆整链。修：要求令牌或记录调用方。
- **B6 gate exit code 与 findings 脱钩**（inline_runner.py:449-452 只进错误文案）；stdout+stderr 拼接后 rfind 提取 JSON，stderr 一个 warning 即可误硬停。修：断言 rc 与 blocking 一致；stdout/stderr 分开收集。
- **B7 words_total 真实书 = 0**（book2 实证：第 1 章封板 2380 字，words_total=0）。影响：target_words 停止条件死（pipeline_spec.py:403），"保底 2 万字"只能靠 max_chapters。修：查 N16 封板回写路径为何未在真实书生效（夹具里成立 → 条件分歧）。

**运行时组**
- **B8 llm_rounds 记账口径错**：runtime.mjs 按 attempt 计数，一次 attempt 内最多 48 次 chat，预算形同虚设。修：按 chat 次数计。
- **B9 stop 不杀孙进程**：execFile timeout/signal 只杀直接子进程，Windows 无进程组 kill，autopilot 再 spawn 的 storyctl/gate_runner 继续写书目录。修：taskkill /T /F 或 job object。
- **B10 sync 指纹漏同步**（mtime:size:count，rename 互换 / 保留 mtime 的拷贝即漏）；**rebuild 非无损**（versions/story_docs/analytics/approvals 不重建，版本历史断链）。修：per-file sha 或 name+size+mtime 三元组；rebuild 从 _pipeline/versions/** 重建。
- **B11 读侧无保护名单**（state/run/workbench/agents/judgments/findings 全可读）+ judgments 可覆写（见 A6）。修：读侧对 _pipeline/ 控制文件拒读。
- **B12 600 事件上限丢早期事件**（长跑中 UI 永久丢失 run_started 等）。修：按 seq 增量 INSERT。
- **B13 provider 错误体进事件流并回喂模型**（端点/账号信息外泄给模型）。修：脱敏后回喂。

**产品组（UI）**
- **B14 v2 与制作台是断路的两个应用**：write.js:256"检查与完成"整页跳 studio.html，studio 无返回链接，v2 六视图未调用 /run /stop /resume /report 任何一个。作者每天的核心循环（写→看闸门→定稿）被迫横跳。修：跳转明示 + 双向出口；中期把闸门结论与"重新定稿"以只读面板嵌入 v2「改」视图。
- **B15 API 失败误报"书房还空着"**（app.js:85-95 抛错后走空态分支）。首次启动失败即误判数据丢失。修：空态二分（加载失败含重试 / 真空）。
- **B16 移动端三项承诺两项打折**：36px 触控（README 承诺 44px）、保存态 <560px 隐藏、toast 被底导航遮挡。修：工具栏 44px、保存态移至 write-foot 常驻、toast 加底导航偏移。
- **B17 两套设计系统 drift**：按钮/主色/字号/导航范式全不同，helper 两份。修：抽共享 tokens.css + lib，先统一四类令牌。
- **B18「改」视图答不上"AI 改了什么"**：manual 版本淹没 AI 版本、diff 对象不可选、死代码佐证筛选未做完。修：按 source 分组（默认折叠 manual）、双下拉选版本。
- **B19 投稿最后一公里断**：投稿包只是路径字符串，无文件出口；SKILL.md 的人工确认 4 条在 UI 零曝光。修：列 MANIFEST 文件 + 复制路径/资源管理器打开 + 人工确认 checklist 固定展示。

### C 级——卫生（一批，顺手修）
README 版本 0.6.0 过时（实 0.7.0）；测试环境 Node 22 vs 引擎要求 ≥24（本机已装 24.16.0，换它复跑再定位 api.test.mjs 崩溃）；工作区脏（state.json 已修改 + 未跟踪细纲_第002章.md，修复波开始前先提交或 stash 保持归因干净）；N02 恒真 no-op 却声称"文风锚定"；令牌用 random 非 secrets 且仅 24bit；quote 要求 12–30 字代码只卡 ≥8；token 走子进程 argv；错误回包泄露绝对路径；静态响应无 CSP；workbench.json/relay.md 非原子写。

## 四、外部基准映射（本轮检索）

| 来源 | 结论 | 打在哪 |
|---|---|---|
| Anthropic《How we built our multi-agent research system》 | 主 agent 只做编排、重负载丢 subagent、主上下文防污染 | 瘦主控方向正确；但其编排是确定性代码，我们的编排在 LLM 里——A1/A7 的根 |
| LLM-as-judge 工程实践（2026 年多篇综述） | rubric 决定上限；judge 不能弱于被评者；同族模型互评有系统性 bias | A6：writer=default/judge=reasoning 方向对，但未校准 + 可覆写 |
| OWASP LLM Top 10 2025 | prompt injection 头号风险 + Excessive Agency | A1–A4：本流水线核心输入（小说正文、平台文档、检索结果）全是不可信文本 |
| OrchestraBench | recovery 是编排能力专门评测对象 | A7 |
| 上轮 novel-writer-cli 横向比对 | 确定性编排 CLI + instruction packet + 写入锁 | A7/B4：迁移评估仍值得单开一轮，但不是当前最短路径 |

## 五、整改 Plan

### 问题定义
项目要验收的不是"管道能跑"，而是"一条指令产出可投稿且编辑愿意签约的书"。当前卡点：① 控制面可被伪造（A1–A5），自动跑书不可信；② 状态机有三处与方案不符的正确性缺口（B1/B2/B3）+ 停止条件死（B7）；③ 日常使用回路断路（B14/B15）；④ 质量裁判未校准（A6）。

### 目标用户与场景
唯一用户黄志豪，单人本地工具，三角色一体（作者/操作者/审阅者）。场景一（当前主线）：每天 1–2 小时副线时间，从书房继续写 → 看闸门 → 改 → 投。场景二（暂缓）：完全无人值守过夜跑书。所有成功指标按这两个场景设。

### 方案描述（四波，按序执行；每波附对应发现编号）

**第一波 P0 安全止损（约 0.5–1 个会话，多为几十行改动）**
A3 realpath 归一化（或拒含 ~ 路径段）；A4 agents.json / submissions.json / judgments_* 入保护名单；A5 Origin 精确匹配 + Sec-Fetch-Site + 写操作自定义头；A1/A2 文档撤回"100% 机械隔离"与"四道验收"表述，done 补 evidence-spec 模式比对；B11 读侧拒读 _pipeline/ 控制文件；B5 block/resume 要令牌。附带：攻击书 _phase0/attack_book 扩充为 4 条攻击用例（8.3 写穿 / agents.json 改写 / CSRF / next 盗令牌）做回归。

**第二波 P1 状态机正确性（约 1 个会话）**
B1 N14→N13 复检（含 findings 累积与 fix_rounds 交互测试）；B2 resume 轮次清零 + cursor↔tracking 交叉校验；B3 快照回滚实现或删声称；B4 done/skip 加锁；B6 gate rc 断言 + stderr 分离；B7 words_total 真实书回写修复（夹具回归 + book2 状态修正）；B8 记账口径；B9 taskkill /T。

**第三波 P2 产品回路（约 1 个会话）**
B14（跳转明示 + 双向出口，闸门结论只读面板进「改」视图）；B15 空态二分；B19 投稿包文件出口 + 人工确认 4 条 checklist；B16 移动端 44px + 保存态常驻；B17 设计令牌统一（先四类）；B18 版本按 source 分组。

**第四波 P3 验证与校准（约 1–2 个会话，与第一本真书并行）**
A7：在 F:\小说\works\ 开第一本真书，人工在环跑 C1–C3（不等无人值守）；每章人工盲评 vs judge 裁决，积累校准集（≥10 章）；A6：校准集上测 judge 一致率（block 召回 100% 优先于误判率）；B10/B12/B13 随波修；L1 九维评分层在一致率达标后才上线。

### 交互流程（修好后的日常循环）
书房 hero"继续写第 N 章"→ 稿纸（保存态常驻可见）→「检查与完成」在原地展开闸门结论面板（0 跳转，通过/findings 清单/去制作台的显式入口）→「改」视图看 AI 版本 diff（manual 默认折叠）→ 回滚在 v2 内闭环 → 书房"投稿检查"→ preflight → 生成投稿包 → 面板内直接打开全文.md / 复制路径 / 人工确认 4 条逐条勾 → 投稿记录回填编辑反馈。无人值守路径（cli.mjs run）的安全姿态：subagent 仅 Node 沙箱工具面 + 控制文件读写全拒 + 攻击用例回归。

### 边界与异常
- 攻击面清单：不可信文本（正文/平台文档/检索结果）→ subagent；本机任意 localhost 页面 → API；被注入 subagent → 控制文件。防线：沙箱 realpath、保护名单含 agents/judgments、Origin 精确匹配、done 模式比对。
- book2 暂停态不受整改影响（修复波开始前先提交/stash 现有脏状态）。
- N13 遇同章多版本文件硬停是设计约束，保持。
- gate_runner 缺失时 N13/N19 硬停与 README"只告警"不符——要么改代码要么改文档（C 级顺手）。

### 成功指标（每波可衡量）
- P0：4 条攻击用例全部拒绝（exit≠0 且状态机未推进）；README/SKILL/plan 中"100% 机械隔离"字样 = 0。
- P1：真书或 book2 修正后 words_total == Σ 封板章 visible_chars（误差 0）；N14 后 gate 复跑有记录；fixtures 54/54 绿（Node 24 下）；resume 后轮次清零可观测。
- P2：书房→闸门结论 ≤2 点击 0 跳转；390px 触控目标 ≥44px 占比 100%；生成投稿包→打开全文.md ≤1 操作；误空态曝光 0。
- P3：真书 C1–C3 人工恢复次数 = 0；judge block 召回 100%、与人工盲评一致率 ≥80% 后 L1 方可上线。

### 风险
- 安全修复引入回归 → 攻击书 + _fixtures 双回归门禁。
- resume/锁语义变更影响在办书目 → book2 已暂停，影响可控；改前先提交脏状态。
- UI 改动面大 → 每视图改完跑 v2-content / v2-story-submit 回归。
- 时间成本 vs "尽快开真书"：P0 不修则真书跑在可伪造的控制面上；P0 约几十行改动，是最便宜的安全险。完全无人值守继续挂起，不作为当前目标——第一本真书人工在环跑，既是产品进度也是 judge 校准数据来源。

## 六、优先级矩阵（影响面 × 成本 × 风险）

| 发现 | 影响面 | 修复成本 | 风险 | 波次 |
|---|---|---|---|---|
| A3 8.3 写穿 | 控制面全可伪造 | 极低 | 高 | P0 |
| A4 agents.json 劫持 | 手稿外泄 + 持久注入 | 低 | 高 | P0 |
| A5 CSRF/RCE | 本机任意应用 | 低 | 高 | P0 |
| A1 token 隔离 | 越权推进 | 低（文档）/中（机制） | 高 | P0（文档）/P3（机制） |
| A2 done 装饰 | 伪造链完整 | 低 | 高 | P0 |
| B11/B5 读写保护 | 与 A3/A4 叠加 | 低 | 高 | P0 |
| B7 words_total | 停止条件死 | 中 | 中 | P1 |
| B1 N14→N13 | 闸门漏检 | 中 | 高 | P1 |
| B2/B3/B4/B6 | 恢复路径不可用 | 中 | 中 | P1 |
| B14/B15 UI 断路 | 每日核心循环 | 中 | 中 | P2 |
| A6/A7 校准 + 无人值守 | 第一卖家验收 | 高 | 中 | P3 |
| B16–B19 UI 体验 | 日常效率 | 中 | 低 | P2 |
| C 级卫生 | 归因 / 可维护 | 低 | 低 | 随波 |

## 七、选项（需用户裁定）

- **A（推荐）：按四波顺序整改，P0 立即开始。** 理由：A 级全是低成本高风险的"控制面可伪造"问题，不修则真书、评分层、无人值守全部建在沙上；P0 约几十行改动 + 4 条攻击回归，是最便宜的安全险。P1/P2 决定"自动跑"和"每天用"的验收，P3 用第一本真书同时换产品进度和校准数据。
- **B：先产品（P2）后安全（P0）。** 理由可能是日常体验更痛。权衡：UI 修得再顺，控制面仍可被一次提示注入关掉闸门；且 P0 成本更低，先做不亏。
- **C：只修 P0 + 直接开真书人工在环，B/C 级记录在案延后。** 适合"这周就要开始写字"的节奏；代价：N14 漏复检、resume 不可用、UI 横跳带病运行，靠人工兜底。
- **D：认真评估 novel-writer-cli 式"确定性编排 CLI + instruction packet"迁移（上轮 C 选项复判）。** 这是把编排移出 LLM 的大赌注，能根治 A1/A7，但迁移成本以周计；建议 P3 之后、有真书数据时再单开调研，现在不动。

**我的推荐是 A**：P0 这波可以直接开始（改代码 + 攻击书扩充回归），做完给出攻击测试结果再继续 P1。如果用户说"先不"，则先修 P2 的 B14/B15 两个断点让日常回路能用，安全波并行排后。
