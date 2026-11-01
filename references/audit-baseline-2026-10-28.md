# novel-workbench 审计底稿（主控手记，2026-10-28）

> 本文是主控在取证期间**亲自实测**得到的事实，不是 agent 转述。
> agent 结论另见各工作流产出。本文只保留"我自己跑过命令确认"的。

## A. GitHub 开源暴露面（已实测）

| 项 | 实测结果 | 命令 |
|---|---|---|
| 提交身份 | 65 次提交全部 `Wane.Zhihao <wane-zhihao@outlook.com>` | `git log --all --format='%an <%ae>' \| sort -u` |
| 历史内真实密钥 | **无**。唯一 `sk-` 命中是测试夹具 `secret.api_key = "sk-should-not-be-saved"`（api.test.mjs:191） | `git log --all -S "sk-"` + `git grep` |
| `.workbuddy-ai` 记忆 | **从未进入 git 历史** | `git log --all -- .workbuddy-ai` → 空 |
| 含个人路径的 tracked 文件 | **15 个**（`C:\Users\Wane-` / `F:\小说`）；其中 7 个在产品代码与文档，8 个在 `_phase0`/`_fixtures` 产物 | `git grep -l -I -E "C:\\\\Users\\\\Wane-\|F:\\\\小说"` |
| 产品侧含个人路径的文件 | `README.md`、`ci.cmd`、`ci.sh`、`novel-workbench/SKILL.md`、`novel-workbench/config.json`、`novel-workbench/scripts/autopilot.py`、`references/plan-v2.1.md` | 同上 |
| 已入库的越界语料 | `_phase0/probe/zc.html`（纵横错误页）+ `zc2.html`（纵横小说第 1 章正文快照，28KB）**在 git 跟踪中** | `git ls-files _phase0/probe/` |
| 仓库内最大文件 | inline_runner.py 76,899B、api.mjs 60,280B —— **无大体积语料副本** | 遍历 tracked 文件 stat |

### 外部资产体量（决定发行物边界）

| 资产 | 体积 | 性质 |
|---|---|---|
| `~/.qoder/skills/story-long-write`（实为 `~/.config/magpie/library/skills/...` 的 junction） | 1.6MB | 工具脚本，**必须随发行** |
| `F:/小说/platforms/_core` | 161KB | 门禁工具，**必须随发行** |
| `F:/小说/platforms/qimao/harness` | 52KB | 平台知识，**随发行** |
| `F:/小说/platforms` 合计 | 8.6MB | 含语料 |
| `F:/小说/platforms/qimao/corpus` | 4.1MB / 332 篇 | 七猫作家交流区**公开教学帖**，性质可辩护 |
| `F:/小说/platforms/qimao/corpus/positive` | 248KB / 8 本 | **他人签约作品正文 → 绝不发行** |
| `F:/小说/platforms/qimao/corpus/positive_niandai` | 204KB | **年代签约作品正文 → 绝不发行** |
| `F:/小说/_调研/raw` | 772MB | 抓取原文 → 绝不发行 |

## B. 错误码体系双向断裂（已实测）

后端抛 **58** 个 error code，前端 `web/lib/errors.js` 只映射 **14** 个，
且映射的 14 个里有 **10** 个后端根本不抛（来自 `server/agent/tools.mjs` 的 `fail(code, message)`）。

**后端有、前端无映射（用户看到裸技术文案）** —— 58 − 14 = 44 条，含全部致命项：

- `ai_roles_not_configured` ← **新书第一堵墙**（agents 全 mock → 400）
- `needs_resume` ← 陈旧 `.step_token` 卡死（仅在**已废弃**的 `web/studio.js:546` 有处理逻辑，v2 无入口）
- `provider_key_missing`、`invalid_platform`、`preflight_failed`、`books_root_missing`、`init_failed`、`sync_failed` …

**前端有、后端不抛**：`chapter_gap`、`commit_timeout`、`committed_edit_required`、`gate_blocked`、
`length_out_of_band`、`quality_blocked`、`quality_tool_unavailable`、`revision_conflict`、
`tracking_invalid`、`tracking_missing`

结论：错误映射层需要一次**全量对齐**，且必须新增"配置缺失类"错误的引导出口
（当前 `ai_roles_not_configured` 没有"去设置里配模型"这个动作，作者会卡死在 400 上）。

## C. 外部工具链调用契约（我方调用面，权威副本在我仓）

| 工具 | 调用形态 | 调用点 |
|---|---|---|
| storyctl.py | `storyctl.py chapter check --project <book> --chapter <n>` | `scripts/inline_runner.py:551` |
| storyctl.py | `storyctl.py chapter commit ...` | `scripts/inline_runner.py:901`、`scripts/commit_chapter.py:229` |
| tracking_commit.py | `tracking_commit.py draft` / `init` | `scripts/inline_runner.py:274` / `:265`、`scripts/commit_chapter.py:215` |
| gate_runner.py | `gate_runner.py --platform <dir> --genre <g> --json <files...>` | `scripts/inline_runner.py:564`、`:1021`、`:1221` |
| check-outline-contract.js | `node check-outline-contract.js --json <file>` | `scripts/inline_runner.py:423` |
| check-ai-patterns.js / check-degeneration.js | 成对调用 | `scripts/inline_runner.py:970-971` |
| style_probe.py | **importlib 动态 import**，函数名即契约 | `scripts/inline_runner.py:1009` |
| clarity_org_probe.py / build_writer_prompt.py | 仅在 settings.mjs 登记，**脚本内无调用点** | `server/settings.mjs:317` / `:258` |

**契约要点（已与 Novel tools 会话对齐）**：
1. gate_runner 退出码 **0 与 1 都是有效结果**，rc ∉ (0,1) 才算工具失败（`inline_runner.py:571`）。
2. `fact_gate.py` 的 `unverified` 退出码是 **0**（与 pass 同码）→ **调用方必须读 JSON 的 `status` 字段，
   rc 只能区分 blocked**。这条要写进 PRD 的对外契约章节，并核查我方 3 处 gate_runner 调用点是否只看 rc。
3. style_probe 是动态 import，**函数名/签名是对外契约**，不能改。

### D2. gate_runner 退出码语义 —— 2026-10-28 上机实证（与 Novel tools 联合核实）

实测命令（`PYTHONIOENCODING=utf-8 PYTHONUTF8=1`）：

| 场景 | rc | JSON status | 结论 |
|---|---|---|---|
| 退化文本（60 段重复句）过 qimao | **0** | `advisory`（advisory=6, blocked=0） | ✅ 确认 |
| 文件不存在 | **0** | `[{"error":"not found"}]` | ✅ 确认 |
| 探针崩溃（GBK 文件触发 `UnicodeDecodeError`） | **1** | 无 JSON（traceback 逃逸） | ✅ 确认，**且最危险** |

**三平台 B 级门禁的真实分布**（此前文档口径有误）：
- qimao `gates[]` 9 条**全为 A**；那 4 条 B 级在**另一个数组** `fact_layer.gates[]`（F1–F6，走 `fact_gate.py`）
- fanqie 6 条全 A；zongheng `execution_mode=metrics_only`
- **结论：经 gate_runner 通道，三平台 rc 恒为 0，B 级判定能力只存在于 fact_gate 通道**
- **而我的流水线根本没有调用 fact_gate**（只调 gate_runner 3 处）→ qimao 事实上无任何 B 级门禁在跑

**我方三处调用点对 `error` 键 / rc 的处理不一致**：

| 位置 | 功能 | error 键检查 | 问题 |
|---|---|---|---|
| `inline_runner.py:576` | N13 单章闸门 | ✅ 有 | 但 rc=1 仍被当成「内容阻断」 |
| `inline_runner.py:1048` | V.N19 文风漂移 | ✅ 有 | 同上 |
| `inline_runner.py:1232` | N21 投稿包逐章指标 | ❌ **无** | 且 `if rc not in (0,1): out=""` —— 工具崩了清空输出后**照常打包投稿** |

**这是"静默放行"的高危实例**：探针崩溃（rc=1）被读成内容不合格；N21 路径工具失败被读成"指标取不到，不阻断打包"。
两者都会让无人值守下的坏稿进入投稿包。

**修复方向（已与 Novel tools 对齐，采用兼容方案）**：
对方在 gate_runner 增加 `has_error: true` 布尔字段（**不新增 status 枚举、不改 rc 值**）；
我方统一约定：判定只看 JSON status；stdout 非 JSON 或含 traceback → 工具失败；rc=1 且 stdout 非 JSON → 工具崩溃（非 blocked）；
任一 row `has_error` → 该文件「未判定」，不得计入通过。

### D3. 契约终稿（2026-10-28 与 Novel tools 双方确认）

**字段语义（永久分离，不得合并）**：

| 字段 | 含义 | 我方处置 |
|---|---|---|
| `has_error: true` | 工具/读取失败（文件不存在、解码失败、探针抛异常） | **工具故障 → 阻断** |
| `undetermined: true` | 判定不可靠（短稿 insufficient_sample / 缺 --genre skipped / 指标缺失） | **提示但不阻断** |
| `status` | 四态 `pass`/`advisory`/`blocked`/`metrics_only` | 唯一判定依据 |

**判定顺序（我方实现照此短路）**：`has_error` → `undetermined` → `status`

**编码降级语义**：`utf-8`/`utf-8-sig` → `metrics.encoding="utf-8"`，不置 undetermined；
`gb18030` → `metrics.encoding="gb18030"` **且置 `undetermined=true`**；全失败 → `has_error=true`，不产出指标。

**fact_gate 契约**：
- CLI `fact_gate.py <正文目录> [--config <json>] [--json] [--selftest]`
- **rc 保持二值（0=非阻断 / 1=blocked），不新增 rc=3** —— 理由：多一个 rc 只会多一个可能被"只快照 rc"的调用方吞掉的值；
  "unverified 不能被折叠成 pass" 由**我方的呈现义务**保证（体检报告单列一节显式呈现），而非依赖对方退出码。
- 顶层 JSON `status` ∈ `pass|advisory|unverified|blocked`，我方**必须**把 `unverified` 单列呈现，不折叠进 pass。
- 形态与 gate_runner 不同（位置参数=正文目录），我方需**新增第 4 个调用点**，不复用 gate_runner 参数形态。

**夹具基线（2026-10-28 实测，双方一致）**：

| 夹具 | 真实编码 | rc | 输出 |
|---|---|---|---|
| `gbk_sample.md` | **GB18030**（utf-8 FAIL @0xc9） | 1 + traceback | `style_probe.py:27` UnicodeDecodeError |
| `stub_900.md` | UTF-8 | 0 | `status=pass, findings=[]`, visible_chars=900 ← **900 字稿报 PASS** |
| `degenerate_1500.md` | UTF-8 | 0 | `status=advisory, advisory=6`（G3-a/b/c、G9、G11、G9-b） |

> **踩坑记录（我自己犯的错，值得留档）**：第一次测 `degenerate_1500.md` 时我的读取脚本报
> `UnicodeDecodeError 0xbe in position 545`，我据此误判"该夹具是 GBK 编码"。
> 真因是**我的读取脚本用了平台默认编码 cp936**，不是文件编码问题。
> 正确核验方式：`open(f,'rb').read()` 后逐编码试 decode（utf-8 / gb18030 / cp936）。
> 同类坑：`subprocess` 重定向到文件时 stdout 也按 locale 编码写盘，只有带
> `PYTHONIOENCODING=utf-8 PYTHONUTF8=1` 才是 UTF-8 —— 而流水线正是这样调的
> （`inline_runner.py` 的 `utf8_env()`），所以**生产路径无此问题**。
>
> 对方夹具原为 UTF-8，无需另存 `_utf8` 版本；6 条 fail 的回归基线不会失守。

> ⚠️ 附带发现（值得进 PRD）：**gate_runner 的 stdout 编码依赖调用方环境**。
> 直接在 cmd 里跑（无 PYTHONIOENCODING）时，Windows 下 stdout 按 cp936 落盘，
> 任何按 UTF-8 读它的下游都会炸。我方流水线有 `utf8_env()` 兜底，但外部调用者没有。
> → 建议对方在 gate_runner 内部显式 `sys.stdout.reconfigure(encoding='utf-8')`（或约定输出必为 UTF-8）。

### D4. 我方待改（本次核实暴露，已入 TDD 范围）

1. `inline_runner.py:1232`（N21 投稿包逐章指标）：**无 error 键检查**，且 `if rc not in (0,1): out=""` → 工具崩了清空输出后**照常打包投稿**（high）
2. `inline_runner.py:571`：rc=1 仍被当作"内容阻断" → 探针崩溃被读作文风不合格（high）
3. `inline_runner.py:576` / `:1048` 已有 error 检查，但同样只看 rc（medium）
4. **fact_gate 尚未接入**：qimao 事实上无任何 B 级门禁在跑 —— 这是**平台能力缺口**，不只是实现细节（high，PRD 必列）

## D0. 未申报改动溯源（2026-11-01 已结案）

**事件**：10-28 02:19，我方仓内 `scripts/commit_chapter.py`、`package.json`、`server/tests/fact-gate.test.mjs`
被写入 fact_gate 集成，当时无法归因（我方 agent 转录无命中，Novel tools 会话目录亦无写入记录）。

**结论（已独立复核，非采信对方转述）**：
- 改动由**第三个执行会话**（WorkBuddy AI）做出，并在其自身日志中申报过：
  `F:\WorkBuddy AI\2026-10-22-19-33-42\.workbuddy-ai\memory\2026-10-28.md:214-218`（文件 mtime 02:21:18，改动后 2 分钟）
- 该日志同时申报了对 `platforms/**`（对方地盘）与 `works/顶职/追踪/_fact-assertions.json` 的改动 —— 即该会话**跨两个项目**作业
- 不是幽灵写入，不是 Novel tools 本会话，也不是我方 agent

**复核过的两条附带事实**：
- junction 两级指向确认无误：`~/.workbuddy-ai/skills/novel-workbench` → `~/.config/magpie/library/skills/novel-workbench` → `F:/WorkBuddy AI/Novel Platform/novel-workbench`，同一目录，非两份副本
- 该日志记有一条重要环境坑：**managed node 22 跑 `commit.test.mjs` 会硬崩 `0xC0000005`**，必须用 node 24
  → 这解释了 `ci.sh` 为何设 >=24 守卫，也说明 `config.json` 的 `NODE=22.22.2` 是真实风险而非文档瑕疵

**遗留问题（比溯源更重要）**：该日志第 ③ 项把「事实闸门」写进了**流程文档**
（`workflow-chapter.md` 步骤 8 判据改为"合并检查 0 且 事实闸门 0"），但第 ④ 项代码只接在
`commit_chapter.py`（人工定稿路）。**书面流程前进了，自动化代码没跟上** → 见 TDD T-3b。

**处置**：改动本身正确、保留；但 `package.json` 引用未跟踪的 `fact-gate.test.mjs` 属状态不一致，
须按 TDD §1.1 先入库再继续。跨项目写作的约定需与用户重新明确（三个会话共用一台机器、两个仓库）。

## D. 运行时与进程（已实测）

- `server/agent/runtime.mjs:40-72`：`execFile` + `timeout` + `signal`；**Windows 下补杀整棵树**
  （`taskkill /pid <pid> /T /F`，`:67`）。这是代码里做对了的地方。
- 但 `server/index.mjs:80-83` 的 `close()` 只 `server.close() + db.close()`，
  `runManager.dispose()` 生产代码从未调用 → Ctrl+C 后 autopilot 子进程可能继续写书目录（DB 已关）。
- `scripts/inline_runner.py:84-108` 的 `subprocess.run(timeout=)` 捕获 TimeoutExpired 后**只返回 124**，
  不杀孙进程 —— 与 runtime.mjs 的做法不一致。

## E. 状态真值边界（已实测）

三方并存且口径不同：

| 来源 | 顶职的值 | 说明 |
|---|---|---|
| `_pipeline/state.json` | 9 章 / 20,128 字 / cursor C10.N09 | 游标与节点状态真值 |
| `追踪/_tracking-state.json` | 9 章 | 字数与章节数的账本真值 |
| 磁盘正文实际汉字数 | **16,693** | 与 state 的 20,128 不同口径 |
| SQLite `chapters` 表 | 4 章 / 9,288 字 | 镜像，停在 10-25 21:31 |

→ `state.words_total` 与磁盘汉字数**口径不同**，用它判"是否达到 2 万字"会误判；
→ DB 数字不可用于任何"当前状态"判断，只能靠 `/sync` 刷新。

## F. 已确认干净的部分（审计不必重复查）

- 无 TODO/FIXME/HACK 真实遗留、无裸 `except:`、无 `.skip`/`.only`、无成块注释代码
- `web/lib/errors.js` 的设计意图正确（"一句人话 + 一个出口"，且不映射时保留后端 message）——只是**覆盖面**不足
- `server/agent/runtime.mjs` 的 Windows 进程树补杀是对的
- 密钥 DPAPI 加密、写入不回显、`api_key_env` 走环境变量
- 版本号 0.7.0 在 package.json / SKILL.md / README / `--help` 四处一致，且有测试守护
- 路径越界与控制文件写保护有专门测试覆盖
