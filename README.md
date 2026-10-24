# 书房 · 签约工作台（Novel Platform）

> 建立：2026-10-23 ｜ 分支 `master` ｜ 提交身份 Wane.Zhihao

## v2：正文中心（当前）

界面围绕作者的一天重做：**书房 → 写 → 故事 → 改 → 投**。节点、运行日志、闸门与路径全部收进“制作台”（`/studio.html`）。

- **书房**：继续写第 N 章、目标字数、三件待办；没有统计卡堆叠。
- **写**：17px/1.8 的稿纸编辑器，1.2 秒自动保存、冲突保护、专注/夜间/预览、章节抽屉、字数与保存态。
- **故事**：人物、世界、大纲、伏笔、时间线；直接编辑书内 Markdown，AI 用的是同一份资料。
- **改**：每次 AI 写正文都会留版本；看 diff、回滚到较早版本。默认自动跑，事后可回滚。
- **投**：2 万字/大纲/无待定稿/无阻塞问题/未投他处的投前检查；生成不可变投稿包；记录编辑、预计回复与跟进。
- **首次开书**：书名/题材/一句话卖点三字段；路径、Python、Agent 配置默认隐藏。
- **移动端**：390px 起可用，底部导航、44px 触控、无横向滚动。

> 逐章“暂停等审”尚未启用；本轮按“自动跑完 + 版本/diff/回滚”落地。旧界面保留在 `/studio.html`，CLI 不变。

## 这个仓库是什么

**小说自动生产的工程仓库**——承载 Skill / Agent / Workflow / Harness / 工具代码。
输入一条指令，自动完成 选题 → 设定 → 卷纲 → 细纲 → 正文至 2 万字 → 质检 → 投稿包。

## 与旧数据的分工（重要）

**本仓库只放"代码与流程"，不放"资料与数据"**：

| 留在原处 | 位置 | 内容 |
|---|---|---|
| 平台方案与语料 | `F:\小说\platforms\` | 七猫/番茄两仓库：投稿方案、写作方案、评分卡、gates、thresholds、genre-baselines、校准报告 |
| 平台语料原文 | `F:\小说\platforms\qimao\corpus\` | 329 篇学院文 + 42 章签约正例（爽文 24 + 年代 18） |
| 探针与工具 | `F:\小说\platforms\_core\tools\` | style_probe / clarity_probe / clarity_org_probe / calibrate / gate_runner / selftest |
| 抓取原始数据 | `F:\小说\_调研\raw\` | 七猫 329 + 番茄 271 篇原文 |
| 技能链备份 | `F:\小说\_backup\` | skills-20261022 |

**本仓库通过 `novel-workbench/config.json` 的绝对路径引用上述数据，不复制、不迁移。**

理由：语料与校准结论是**证据**，必须留在原处可回溯；代码是**实现**，需要版本管理与迭代。两者混在一个仓库会让语料的每次抓取都产生无意义的 diff。

## 目录

```
Novel Platform/
├── README.md                    本文件
├── start-workbench.cmd          Windows 一键启动（检查 Node 24+，失败暂停）
├── start-workbench.sh           macOS / Linux 一键启动
├── ci.cmd / ci.sh               语法检查 + 全量回归（发版前跑）
├── references/
│   ├── plan-v2.1.md             **实现方案 v2.1**（含两轮红队审查的 10 项修补，代码的设计依据）
│   └── phase0-report.md         Phase 0 纪律阈值实测报告
├── novel-workbench/             Skill（~/.workbuddy-ai/skills/novel-workbench 是指向这里的 junction）
│   ├── SKILL.md                 入口 + 铁律
│   ├── config.json              绝对路径根目录（指向旧数据与上游技能）
│   ├── package.json             版本 / Node>=24 / start·test 脚本
│   ├── agents.default.json      Agent 角色 / provider / 预算默认配置（仓库只读模板）
│   ├── references/
│   │   └── decisions.md         无人值守默认决策表
│   ├── server/                  Node 后端（零依赖）
│   │   ├── paths.mjs            数据目录与书库根目录解析
│   │   ├── db.mjs               SQLite 打开与迁移
│   │   ├── schema.mjs           books / chapters / nodes / findings / judgments / runs
│   │   ├── sync.mjs             文件→DB 增量同步与全量重建
│   │   ├── settings.mjs         用户路径设置 → dataDir/pipeline.json
│   │   ├── secrets.mjs          Windows DPAPI 密钥存储（只进不出）
│   │   ├── agent-config.mjs     配置合并、校验、密钥状态
│   │   ├── agent/
│   │   │   ├── runtime.mjs      autopilot 桥 + 节点执行器 + 预算/停止
│   │   │   ├── providers.mjs    OpenAI 兼容客户端 + mock provider
│   │   │   └── tools.mjs        read_file / write_file / list_dir 沙箱
│   │   ├── http.mjs             路由辅助、本机校验、静态文件
│   │   ├── api.mjs              REST 接口（书库/运行/报告/投稿/配置）
│   │   ├── run-manager.mjs      单书单运行、事件流、停止、入库
│   │   ├── gate-report.mjs      gate_runner 实时报告
│   │   ├── version.mjs          版本单一来源（package.json）
│   │   ├── index.mjs            HTTP 服务入口
│   │   ├── cli.mjs              sync / rebuild / status / config / run
│   │   └── tests/               node --test 回归
│   ├── scripts/
│   │   ├── mock_autopilot.py    Phase 0 纪律阈值实测（仅历史用途）
│   │   ├── autopilot.py          状态机入口（init/next/done/skip/block/status/resume）
│   │   ├── commands.py           done / skip / block / resume 实现
│   │   ├── inline_runner.py      script 节点内联执行 + 原文引用硬校验
│   │   └── pipeline_spec.py      节点表（流程唯一真相）
│   └── web/
│       ├── index.html           书房（v2 入口）
│       ├── app.js               v2 路由与启动
│       ├── styles.css           案头/稿纸设计系统
│       ├── lib/                 api / dom / format
│       ├── views/               书房 / 写 / 故事 / 改 / 投 / 设置
│       ├── studio.html          旧控制台（制作台）
│       ├── studio.js            旧控制台脚本
│       └── studio.css           旧控制台样式
├── _fixtures/                   回归夹具（chapter_e2e / chapter_loop / workbench_e2e）
└── _phase0/                     Phase 0 测试目录（联试书 book2，已暂停）
```

### Dashboard 扩展（仓库外）

`F:\WorkBuddy AI\Novel Platform` 只放流水线代码；写作台界面在
`C:\Users\Wane-\.qoder\skills\story\`（`scripts/dashboard-server.mjs` + `assets/`）。
> 这是早期在 story skill 里做的临时看板；仓库内自有后端与界面见下方 P3 / P4。
> 它继续保留作通用文稿编辑器，工作台功能以 `novel-workbench/server/` 为准。

流水线页签通过四个端点和 `_pipeline/state.json` 通信。确定性节点由 Dashboard
**直接执行**；LLM 节点返回提示词，交给对话里的编排者：

| 端点 | 作用 |
|---|---|
| `GET /api/pipeline/status` | 节点进度、blocked 原因、evidence 链接、最近 findings |
| `POST /api/pipeline/start` | 执行 `autopilot.py next`，写 `run.json`，返回当前节点 instruction |
| `POST /api/pipeline/resume` | 先 `resume` 清阻断/中断节点，再执行 `next`；返回 instruction |
| `GET /api/pipeline/report?chapter=N` | 实时调 `gate_runner.py`，失败时回退到 N13 findings |

`instruction` 在 llm/verify 节点会带 `subagent_prompt`（服务端已剥离 `step_token` 与
`done_cmd_for_orchestrator_only`），界面自动复制提示词；写作/审查本身仍由对话 agent 完成。

书目录里的 `_pipeline/workbench.json` 由 `autopilot.py` 自动写入（python / autopilot /
gate_runner / platform 的绝对路径），Dashboard 只认这份清单，不读仓库 `config.json`。

> **Plan 溯源**：`references/plan-v2.1.md` 从 `C:\Users\Wane-\.workbuddy-ai\plans\cosmic-nebula-babbage-MnzvwCoC.md`
> 复制而来（sha256 已校验一致）。原文件保留在原地不动——按"旧资料留原处"的约定。
> 本仓库的代码以这份方案为唯一设计依据；方案若改，改这里，不要改原处。

## 数据层（P1，已完成）

**文件是唯一真相，SQLite 只做索引与历史**——DB 可随时从磁盘重建，脚本离线仍能跑。

```bash
# 数据目录默认 %LOCALAPPDATA%\NovelWorkbench，可用 WORKBENCH_DATA_DIR 覆盖
node novel-workbench/server/cli.mjs sync   --root _fixtures
node novel-workbench/server/cli.mjs status
node novel-workbench/server/cli.mjs rebuild --root _fixtures   # 清空业务表后全量重建
node novel-workbench/server/cli.mjs config --book <书目录>      # 合并后的 Agent 配置

# 回归（临时库，不写真实数据目录）
node --test novel-workbench/server/tests/sync.test.mjs \
            novel-workbench/server/tests/agent-config.test.mjs
```

- 书库根目录：`WORKBENCH_BOOKS_ROOTS`（`;`/`,` 分隔）优先，缺省读 `config.json` 的 `BOOKS_ROOT`。
- 同步器解析 `state.json`、`正文/`、`追踪/_tracking-state.json`、`findings_C*.json`、
  `endgame_findings.json`、`judgments_*.json`、`run.json`；按 mtime+size 指纹跳过未变化的书。
- findings 保留 `first_seen_at`，文件里消失的条目只置 `active=0`，不删历史。
- Agent 配置：`agents.default.json` + 每书 `_pipeline/agents.json` 覆盖；
  密钥可来自环境变量（如 `DEEPSEEK_API_KEY`）或 UI（Windows DPAPI 加密存数据目录）；
  接口与日志只暴露"已配置/未配置/来源"，永不回显密钥。
- 默认 provider 是 `mock`，未接真实模型前不会产生调用费用；`writer` 与 `judge` 同模型会告警。

## Agent 运行时（P2，已完成）

运行时把 `autopilot.py` 当成状态机，自己完成"取指令 → 调模型 → 写文件 → 交令牌"的闭环：

```bash
# 安全演练：mock provider，只推一个节点
node novel-workbench/server/cli.mjs run --book <书目录> --mock --max-steps 1

# 接真实模型：在 UI「Agent 配置」里选 provider 并填密钥，
# 或改 agents.default.json / 书内 _pipeline/agents.json 后设环境变量
set DEEPSEEK_API_KEY=...
node novel-workbench/server/cli.mjs run --book <书目录>

# 机器可读结果（不含 token / done_cmd）
node novel-workbench/server/cli.mjs run --book <书目录> --json
```

- Provider：`mock`（默认，不联网）与 OpenAI 兼容（DeepSeek / Kimi / 智谱 / Ollama / OpenRouter）。
  每个角色独立配置 provider、model、temperature、timeout、max_attempts。
- 工具循环只暴露 `read_file / write_file / list_dir`，路径锁死在书目录；
  禁止改写 `state.json`、`.step_token`、`workbench.json`、`run.json`。
- 令牌由运行时自己从 `_pipeline/.step_token` 读取并调 `done`，不出进程、不进结果、不进日志。
- `done` 返回引用校验失败时，把失败原因回喂给 judge 重写（受 `max_attempts` 限制）；
  N15/N20 的 block 按状态机路由（N14 / N20f），第三次连续失败才停机。
- 停止：Ctrl+C（AbortSignal）；预算：`budgets.llm_rounds`、`budgets.node_timeout_s`；`--max-steps` 限制本轮节点数。
- 每轮结束自动同步 SQLite，`status` 立即能看到新进度。
- 回归：`node --test novel-workbench/server/tests/*.test.mjs`（含真实 autopilot 单节点集成测试，全部在临时副本上跑）。

## 后端 API（P3，已完成）

```bash
# 只监听 127.0.0.1；--open 用系统浏览器打开
node novel-workbench/server/index.mjs --port 43120 --root <书库根目录> --open
```

| 端点 | 作用 |
|---|---|
| `GET /api/health` | 数据目录、书库根、已入库书数 |
| `GET /api/books` | 同步书库并返回汇总（进度、章节数、现行阻断数） |
| `POST /api/books` | 在首个书库根下调用 `autopilot init` 新建书 |
| `GET /api/books/:id` | 书详情：章节、节点、findings、判词 |
| `POST /api/books/:id/sync` | 强制从文件重建该书索引 |
| `GET /api/books/:id/report?chapter=N` | 实时 `gate_runner`，失败回退已存 findings |
| `POST /api/books/:id/run` | 异步启动运行时（`mode: real\|mock`、`max_steps`），返回 202 |
| `GET /api/books/:id/run?since=N` | 运行快照 + 序号增量事件；结束后仍保留最后一次 |
| `POST /api/books/:id/stop` | AbortSignal 停止当前运行 |
| `POST /api/books/:id/resume` | 清掉残留 token / running 标记后继续（停止或崩溃后） |
| `PUT/DELETE /api/providers/:name/secret` | 写入/删除本地密钥（只返回是否已配置） |
| `GET /api/books/:id/runs` | 运行历史（含 `agent_run`） |
| `GET/POST /api/books/:id/submissions` | 投稿记录列表 / 追加 |
| `PUT /api/books/:id/submissions/:sid` | 更新编辑反馈、下一步动作 |
| `GET /api/agents`、`PUT /api/agents` | 全局或按书读取/写入 Agent 配置 |

- 安全：只接受回环 Host；写请求的 Origin 必须是本机；配置文件拒绝任何明文密钥字段；
  静态文件路径锁死在 `novel-workbench/web/`。
- 运行互斥：同一本书同时只允许一个运行；`next` 另有跨驱动锁；开始即写 `runs` 行，
  重启后残留运行标为 `interrupted`；事件最多保留 600 条并定期落库。
- 停止/崩溃后不会自动清 token：UI 显示「待恢复」，点「恢复」后再继续，避免两个驱动互抢。
- N21 若字数不足 2 万 / N20 未 pass / 机械 blocking 非 0，会返回 `NOT_SUBMITTABLE` 并阻断，不会进入完成态。
- 投稿记录以 `_pipeline/submissions.json` 为真相，DB 只是索引；编辑器反馈可直接回填。

## 工作台界面（P4，已完成）

```bash
node novel-workbench/server/index.mjs --port 43120 --root <书库根目录> --open
```

- **书库**：左侧列出所有书与进度/阻断状态；「新建书」直接调用 `autopilot init`。
- **书详情**：统计卡、执行面板、流程节点、章节闸门、问题清单、运行历史。
  - 「演练一步」用 mock provider 走通链路，不产生模型费用；「执行一步」按配置 provider 跑一个节点；
    「连续执行」直到收尾/阻断/预算/步数上限；「停止」发送 AbortSignal。
  - 运行事件每 800ms 增量拉取（`?since=seq`），结束后自动刷新书详情并落库。
  - 章节「闸门」实时调 `gate_runner`，并展示本章 findings；问题清单区分 B/A 与已解决。
- **投稿记录**：新增渠道/编辑/反馈/下一步；直接回填编辑反馈并随时更新状态。
- **Agent 配置**：全局或按书编辑完整 JSON（provider、角色、温度、超时、重试、预算）；
  provider 状态只显示"已配置/未配置"；保存前校验，失败自动回滚。
- 布局在窄屏自动堆叠；无横向滚动；所有数据来自本机 API，不上传。

## 启动与首次运行（P5，已完成）

**前置**：Node.js 24+；Python 3.10+；`gate_runner.py` 与七猫平台目录（可选，缺省时闸门报告只显示已存 findings）。

```bash
# Windows：双击 start-workbench.cmd
# macOS / Linux：
./start-workbench.sh

# 或者：
npm start --prefix novel-workbench
```

- 首次启动会自动检测路径；不可用时浏览器进入「首次运行设置」，填写：
  书库根目录（不存在可自动创建）、Python、autopilot（默认仓库自带）、可选的 gate_runner / 平台目录。
- 设置保存在数据目录的 `settings.json`（默认 `%LOCALAPPDATA%\NovelWorkbench`，可用 `WORKBENCH_DATA_DIR` 覆盖），
  **不写入仓库**；保存后立即同步书库。
- 路径优先级：`--root` / 环境变量 > `settings.json` > 仓库 `config.json` 默认值。
  生效路径会写成数据目录的 `pipeline.json`，autopilot 及其子脚本只读这一份，避免双配置漂移。
- 必填：Python、story-long-write 技能目录、autopilot；gate_runner / 平台目录缺失时只告警。
- 环境变量包括 `WORKBENCH_BOOKS_ROOTS`、`WORKBENCH_PYTHON`、`WORKBENCH_STORY_LONG_WRITE`、
  `WORKBENCH_GATE_RUNNER`、`WORKBENCH_PLATFORM_DIR` 等。
- 版本：`novel-workbench/package.json`（当前 0.6.0）；`GET /api/health` 返回版本、DB schema 版本与 `needs_setup`。
- 数据库迁移随启动自动执行；业务数据在数据目录，删掉 DB 可从文件重建，不影响书稿。
- 模型密钥：环境变量优先；也可在 UI 设置页填写，Windows 用 DPAPI（CurrentUser）加密存
  `<dataDir>/secrets.json`，接口只返回是否已配置与来源。首次运行默认 mock，不需要密钥。

## 关键设计（详见方案）

- **瘦主控 + 文件指针协议**：主会话只调 `autopilot.py`，禁读正文；`next` 输出含路径的 `subagent_prompt`
- **补丁1 脚本内联**：所有 `script` 节点由 `autopilot.py` 内部 `subprocess.run` 执行，LLM 物理上无法篡改脚本参数
- **`step_token` 一次性令牌**：subagent 越权调用被 100% 机械拦截
- **先审后封**：`commit` 必须是单章生命周期的最后一个动作
- **P5 诊断汇流**：N18/N19/N20 全是只读探针，问题统一汇入 `endgame_findings.json`，由 N20f 单点修补
- **失败可回退**：N15/N16 失败回 N14 定点改写（上限 2 轮），N20 block 回 N20f（上限 2 轮），第三次停机交人工

## 快速开始

```bash
PY="C:\Users\Wane-\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe"
AUT="F:\WorkBuddy AI\Novel Platform\novel-workbench\scripts\autopilot.py"

# 1. 初始化新书（正式书放 F:\小说\works\<书名>）
"$PY" "$AUT" init --title "《书名》" --genre niandai --target-words 20000 --max-chapters 12 \
  --book "F:\小说\works\书名"

# 2. 主循环：next → 按 subagent_prompt 派 subagent → done
"$PY" "$AUT" next --book "F:\小说\works\书名"

# 3. 查进度 / 续跑
"$PY" "$AUT" status --book "F:\小说\works\书名"
"$PY" "$AUT" resume --book "F:\小说\works\书名"

# 4. 写作台（可选）
node "C:\Users\Wane-\.qoder\skills\story\scripts\dashboard-server.mjs" \
  --root "F:\小说\works" --open
```

## 当前进度

- [x] 两轮红队审查 + 宿主选型 + 方案 v2.1 定稿
- [x] **Phase 0**：带真 subagent 测单轮纪律阈值 K（实测 6 轮零中断，报告见 `references/phase0-report.md`）
- [x] 一期：autopilot.py 完整实现 + 全局节点 N01–N09d（联试 `book2` 实跑通过）
- [x] 二期：章节循环 C{n}.N09–N16（C01 全节点闭环；C02 夹具回归通过：N10/N11 → N12 → N13 → N16 封板，words_total 回写正确）
- [x] 三期：N17–N21 + N20f 回跳 + 投稿包四文件 + Dashboard 四端点 + 执行桥（确定性节点直接跑，LLM 节点交提示词）
  - 夹具 `_fixtures/workbench_e2e`：N17→N21 通过路径与 N20 三次 block 停机路径
  - 夹具 `_fixtures/chapter_e2e`：N13 闸门 / N14 auto-skip / N15 跳过 / N16 封板 / 事务缺失回退
  - 夹具 `_fixtures/chapter_loop`：N15 block→N14 两轮→第三次停机
- [x] **P1 数据层**：SQLite schema + 迁移、文件→DB 增量同步/重建、Agent 配置合并与校验（`node --test` 回归）
- [x] **P2 Agent 运行时**：OpenAI 兼容 provider + mock、书内工具沙箱、autopilot 桥、引用回喂重试、预算/停止（11 项回归 + 真实 autopilot 单节点集成通过）
- [x] **P3 后端 API 与执行桥**：书库/详情/闸门报告、异步运行+增量事件+停止、运行历史、投稿反馈、Agent 配置读写（HTTP 集成测试）
- [x] **P4 仓库内工作台 UI**：书库/详情/执行事件流/章节闸门/问题清单/投稿反馈/Agent 配置（浏览器实测：演练运行、停止、投稿新增与更新、配置保存与非法回滚、新建书）
- [x] **P5 打包与首次运行**：一键启动脚本、Node 24 检查、路径设置层（env/本地设置/默认值）、首次运行向导、版本与迁移、浏览器实测首次设置
- [x] **修复轮（2026-10-25）**：全量对抗性审查后的 P0/P1/P2 修复
  - P0：N12 通配证据、judge 空引用、Python/Node 双路径、停止/崩溃恢复、书外参考只读、关键产物保护
  - P1：N13 状态矩阵、N21 不可投阻断、证据新鲜度、N16 幂等、全局配置迁出仓库、DPAPI 密钥、token 预算/节点超时、测试去环境化
  - P2：运行持久化与中断标记、唯一临时名 + next 锁、UI 竞态/输入边界/网络 token、版本与发版卫生
- [x] **v2 正文中心**：书房/写作台/自动保存/版本 diff 回滚/故事库/投稿 CRM/三字段开书/移动端底栏；旧控制台保留为制作台
- [ ] 逐章暂停审阅（AI 写完先等作者接受；当前默认自动跑 + 事后回滚）
- [ ] 真实新书端到端（等用户放行；`_phase0/book2` 暂停，不续跑）
