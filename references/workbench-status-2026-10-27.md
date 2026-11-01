# 工作台现状全面汇报（2026-10-27 19:20）

> 全部数字取自磁盘：`git`、文件行数、`workbench.db`（复制后只读查询）、测试实跑结果。
> 不引记忆、不引上一轮结论。凡"没验证过"的地方都在第 8 节明确标出。

---

## 1. 一句话定位

`novel-workbench` 是一个**本机单机的写作工作台**：Node 服务托管一个五页 Web 界面，
调度 `scripts/autopilot.py` 驱动的 17 + 8 + 7 个流水线节点，把一本书从"书名+题材"
推到"2 万字 + 质检通过 + 可投稿包"。人工只做三件事：开书、审阅、投稿。

当前版本 `0.7.0`，schema v5，Node ≥24，默认 `127.0.0.1:43120`（除非显式 `--allow-network`）。

---

## 2. 现在真的能用的功能

界面五页（`web/app.js` 路由）+ 一个覆盖层：

| 页面 | 路由 | 能力 |
|---|---|---|
| 书房 | `#/` | 书籍列表/空态二分、继续写第 N 章、目标字数、三件待办、开新书 |
| 写 | `#/books/:id/write/:n` | 17px/1.8 稿纸、1.2 秒自动保存、冲突保护、专注/夜间/预览、章节抽屉、字数与保存态、单章定稿（append/revision 自动分流） |
| 故事 | `#/books/:id/story[?doc=]` | 人物/世界/大纲/伏笔/时间线直接编辑书内 Markdown；大纲组按卷动态；`?doc=` 深链；未保存离开三层拦截 |
| 改 | `#/books/:id/revise` | 版本列表、diff、回滚 |
| 投 | `#/books/:id/submit` | 投前检查（字数/大纲/待定稿/阻塞项/重复投稿）、不可变投稿包、投稿记录与跟进、reveal 打开产物 |
| 高级（抽屉） | `#/books/:id/advanced/{nodes,log,config,submissions}` | 节点表、运行日志、按书覆盖 Agent 配置、投稿记录；覆盖层不重建底页 DOM |
| 设置 | `#/settings`、`#/setup` | 路径体检（每项标来源 cli/env/user/default/derived）、首次开机向导、provider 密钥写入不回显 |

运行面板（`web/views/run-panel.js`，挂在写页）：
- 启动 / 停止（杀进程树）/ 解除中断（resume）
- 显示当前节点、第几章、心跳新鲜度；60 秒无心跳判"可能卡死"
- 区分「现在在跑」与「上次跑成什么样」（服务重启后从 runs 表还原历史态）
- 写页按钮可直接「让 AI 写第 N 章」（`until_chapter`，越界判断在执行前，不碰下一章）

开书表单：书名 / 题材 / 目标平台 / 目标字数 / 每卷章数 / 一句话卖点。
平台与题材**两级联动**，名单全部来自 `/api/health`（源自 `config.json` 的 `PLATFORMS`），
配置里加平台就自动出现；缺 `GENRE_BASE` 标「未接入」并禁选。

---

## 3. 不能做的 / 尚未验证的（诚实清单）

1. **逐章暂停等审**没做。现在的节奏是"自动跑完 + 版本/diff/回滚"。
2. **真实新书端到端没跑过**。所有 Phase 1/2 的界面改动只经过自动化测试和我的无头浏览器验证；
   `workbench.db` 最后写入停在 2026-10-25 21:31，之后工作台**没有被真正使用过**。
3. **`runs` / `run_events` 两张表 0 行** —— v2 运行面板建成后，没有任何一次真实启动的落库记录。
4. **judge 校准集没做**（人工盲评 ≥10 章）→ L1 九维评分层**校准前不得上线**。
5. **relay.md 没触发过**（封板 <3 章）。
6. **手写章「补录」的 LLM 环节没验证**：draft 只给骨架，delta 需要读正文生成。
7. 只有 qimao / zongheng 两个平台接入；番茄未接（上一轮刚从下拉里删掉）。

---

## 4. 架构

三层，边界清晰：

```
Web (原生 JS ESM，无框架)  ──HTTP──▶  Node 服务 (server/, 20 模块)
                                        │  ├─ SQLite 镜像 (schema v5, 12 表)
                                        │  ├─ RunManager（子进程 + 心跳 + 预算 + 停止）
                                        │  └─ subprocess ─▶ scripts/autopilot.py
                                                              ├─ pipeline_spec.py（节点规格）
                                                              ├─ commands.py（done/block/commit）
                                                              └─ inline_runner.py（script 节点内联）
外部夹具（只读引用，不复制）：F:\小说\platforms\*、~/.qoder/skills/story-long-write
```

**瘦主控 + 文件指针协议**：上层只调 `autopilot next`，拿回"下一个节点 + prompt 路径 + 模型档位"，
不读正文。`next` 输出含 `subagent_prompt`（只给路径）与 `done_cmd_for_orchestrator_only`。

**节点表**（`scripts/pipeline_spec.py`，实测导出）：

| 层 | 数量 | 节点 |
|---|---|---|
| 全局 | 17 | N01 初始化、N02 文风锚定、N03 选题、N04 选题闸门、N05 设定、N06 设定校验、N07 卷纲、N08 主线闸门、N09d 前12章目录骨架、N17 全文整合、N18 AI味/退化扫描、N19 文风漂移+伏笔、N20 评分卡终审、N20f 外科手术补丁、N21 投稿包、N17g 全书合成、N20g 全书汇总终审 |
| 章级 | 8 | N09 场景卡、N10 细纲闸门、N11 上下文组装、N12 写正文+追踪、N13 合并检查+平台闸门、N14 定点改写、N15 阶段质量闸门、N16 章末原子封板 |
| 卷级 | 7 | V.N07 卷纲、V.N08 主线闸门、V.N09d 目录、V.N17 合成、V.N18 AI味扫描、V.N19 漂移+伏笔、V.N20 终审 |

**CLI**（`autopilot.py`）：`init` / `next` / `status` / `doctor` / `relay` / `done` / `skip` / `block` / `resume` / `commit` / `tracking-fill`。

**落盘协议**（书目录下）：`_pipeline/state.json`（唯一游标真相）、`tracking/`、`judgments_*.json`、
`workbench.json`（工具清单）、`.step_token`、`.next.lock`、`snapshots/`、`relay.json/md`、`logs/`。

**可靠性设计**：step_token 防误调（不是隔离，已撤回"100% 机械拦截"的说法）、
done 的 evidence-spec 模式比对、预算按节点/章/卷三级、N14 后复检 N13、
回滚取"Blocker 最少"快照、控制文件读写保护、`stop` 杀进程树。

---

## 5. 代码规模（实测行数）

| 目录 | 文件 | 行数 | 说明 |
|---|---:|---:|---|
| `server/` | 20 | 5,297 | HTTP、同步、运行器、数据库、安全 |
| `server/tests/` | 27 | 4,510 | 25 个 `*.test.mjs` + 契约脚本 |
| `scripts/` | 6 | 4,698 | autopilot / pipeline_spec / commands / inline_runner 等 |
| `web/`（根） | 6 | 3,232 | app.js / studio.js / styles / 两个 html |
| `web/views/` | 9 | 1,960 | shelf / write / story / revise / submit / settings / setup / advanced / run-panel |
| `web/lib/` | 3 | 305 | api / dom / errors |
| **合计（90 文件，含 json/css）** | | **21,603** | |

最大的六个文件：`inline_runner.py` 1,674 ／ `api.mjs` 1,393 ／ `autopilot.py` 1,170 ／
`commands.py` 779 ／ `pipeline_spec.py` 666 ／ `sync.mjs` 660 ／ `settings.mjs` 591。

---

## 6. 接口与数据

**API 27 条**（4 条顶层 + 23 条正则）：
`/api/health`、`/api/settings`(GET/PUT)、`/api/books`(GET/POST)、`/api/agents`；
书级：`/:id`、`/sync`、`/report`、`/runs`、`/run`(GET/POST)、`/resume`、`/stop`、`/manuscript`、
`/chapters/:n/content`、`/chapters/:n/commit`、`/versions`、`/versions/:v`、`/versions/:v/diff/:w`、
`/versions/:v/restore`、`/story`、`/story/doc`、`/submissions`、`/submissions/preflight`、
`/submissions/package`、`/submissions/:id`、`/submissions/:id/events`、`/submissions/:id/reveal`；
另有 `/api/providers/:name/secret`。

**数据库 12 表 / schema v5**：`books`、`chapters`、`nodes`、`findings`、`judgments`、`runs`、
`run_events`、`submissions`、`versions`、`story_docs`、`approvals`、`analytics_events`
（+ `schema_migrations`）。DB 是**镜像**，真相在书目录的文件里；`/sync` 与启动时对账。

**当前库里的真实内容**（`%LOCALAPPDATA%\NovelWorkbench\workbench.db`，10-25 21:31 最后写入）：

| 表 | 行数 |
|---|---:|
| books | 2（其中 1 本是测试残留，见 F2） |
| chapters | 9 |
| nodes | 86 |
| findings | 63 |
| judgments | 10 |
| story_docs | 25 |
| runs / run_events | 0 / 0 |
| submissions / versions / approvals | 0 / 0 / 0 |

---

## 7. 测试与质量

- **Node 侧：25 个测试文件、114 个用例**，用 `--test server/tests/*.test.mjs` 实测 114/114 通过（Node 24.16.0）。
- **Python 契约**：`python_contracts.py` 定义 36 个 `test_*`，`main()` 实际执行 34 个。
- **覆盖到的硬骨头**：CSRF/Origin、路径越界、控制文件写保护、provider 密钥不回显、
  step_token、预算与轮次返还、N14 复检、快照回滚、run 事件增量落库、manuscript 漂移检测、
  平台名单与题材联动、投稿包 gate 与 preflight、正文版本 diff/回滚。
- **两个入口和实跑不一致**（见 F1/F3）：`npm test` 只列了 22 个文件；`ci.sh` 在本机直接失败。

---

## 8. 仓库与流程

- 单仓 `F:\WorkBuddy AI\Novel Platform`，master 单分支，**65 次提交**（2026-10-23 → 10-27），
  提交身份已配置：`Wane.Zhihao <wane-zhihao@outlook.com>`（README 里那句"提交身份"是真的）。
- `novel-workbench/` 是唯一代码主体；`_fixtures/` 测试夹具；`_phase0/` 早期验证产物；`references/` 方案与审查。
- `ci.sh` / `ci.cmd`：`node --check` 全量 .mjs → `compileall` 全量 .py → `npm test`。
- `start-workbench.cmd` / `.sh`：版本守卫（≥24）+ 启动 + 失败暂停。
- 技能入口：`~/.workbuddy-ai/skills/novel-workbench` 是指向本仓库的 junction（改仓库即改技能）。
- 工作区未跟踪：`_phase0/acceptance/`、`_phase0/add_budget_helpers.py`、`_phase0/e2e/_pipeline/relay.{json,md}`（旧 e2e 产物）。

---

## 9. 发现的问题（按严重度）

**F1（流程漏洞，高）`npm test` / `ci.sh` 只跑 100 个用例，不是 114。**
`package.json` 的 `test` 脚本手工列了 22 个文件，漏掉后来新增的
`commit.test.mjs`(6)、`errors.test.mjs`(5)、`run-guard.test.mjs`(3) = 14 个用例。
其中 `commit.test.mjs` 覆盖的是**单章定稿**这条最危险的写路径。

**F2（数据卫生，中）生产库里有一本幽灵书。**
`books.id=1 《联试之书》`，path 指向 `%TEMP%\wb-dryrun-book`（目录已不存在），
仍带 4 个章节、3 章已定稿。它还会出现在书架里。

**F3（流程，中）`ci.sh` 在本机跑不起来。** PATH 上的 `node` 是 `v22.22.2`，
而脚本守卫要求 ≥24 → 直接 `exit 1`。我所有 114/114 都是显式指定
`C:\...\node-v24.16.0-win-x64\node.exe` 才跑出来的。
另：`config.json` 的 `NODE` 也指向 22.22.2（这个只用于派生子进程，不用于跑测试）。

**F4（测试假通过，中）2 个契约测试从未执行。**
`test_status_exposes_volume_degradation`、`test_terminal_block_writes_relay_reason`
既不在 `main()` 里，也不被任何套件调用。后者恰好覆盖"预算耗尽写 relay 原因"，
而 relay 正是我们列为"从未触发"的那条路径。

**F5（已知，低）`autopilot.py` 里 `_genre_bounds_text` 定义了两次**（86 行与 163 行），
后者遮蔽前者，两份实现当前内容相同，属定时炸弹。

**F6（文档漂移，低）`SKILL.md` / README 与代码有几处口径差**：
README 说"七猫/番茄两仓库"的数据分工（那是 `F:\小说` 的现状，不是本仓库能力）；
SKILL.md 仍写 0.7.0 与 v2-author，和实际一致，但没提"平台只剩两家"。

---

## 10. 结论

**能拿去用的**：开书 → 写 → 改 → 投的完整界面、真流水线调度、单章定稿、
版本回滚、投前检查与投稿包、运行可观测与停止/恢复。代码 2.1 万行，114 个 Node 用例全绿。

**还不能拿去用的**：真书端到端一次都没跑过；judge 校准与 L1 评分层不能上线；
`npm test` 口径少 14 个用例；生产库里有一本幽灵书碍眼。

**最短的下一步**：先补 F1（让 `npm test` 真的跑 114）+ F3（ci 用绝对 Node 24），
再在 `F:\小说\works\` 开第一本真书人工在环跑 C1–C3——那一步同时收 judge 校准数据。
