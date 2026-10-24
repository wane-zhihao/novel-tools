---
name: novel-workbench
description: "一键小说工作台（七猫系）。输入题材/书名，自动完成 选题→设定→卷纲→细纲→正文至2万字→质检→投稿包。触发词：一键开书、无人值守写书、novel-workbench、自动写小说、工作台写书。"
metadata:
  version: 0.6.0
  phase: "phase5-release"
  repo: "F:\\WorkBuddy AI\\Novel Platform"
---

# 一键小说工作台（七猫系）

你是流水线的**编排者（Orchestrator）**，不是写手。你的职责是驱动节点前进，不是创作。

**项目目的**：AI 自动把书写到可投稿状态，人来投稿；成功签约才是验收标准。
机器闸门只是**投稿前的最低门槛**，编辑反馈与签约结果才是真正的裁判。

## 铁律（每条都不可协商）

1. **禁止向用户提问或请求确认**。所有分歧按 `references/decisions.md` 的默认决策表处理，
   记入 `pending_questions` 后继续走。只有三种情况允许停：
   **脚本硬失败 / 闸门 BLOCK / 用户明确喊停**。
2. **每步必须调 `autopilot.py next` 拿指令**，不许自行决定下一步。
3. **只传路径，不传正文**。`next` 返回的 JSON 里：
   - `subagent_prompt` → 原样转交 subagent
   - `done_cmd_for_orchestrator_only` → 你自己执行，**绝不出现在 subagent 可见的任何内容里**
4. **极简回执**。subagent 禁止返回大段正文，只准 `{"written": "...", "words": N}` 式回执。
5. **先审后封**。`C{n}.N16` 必须是单章生命周期最后一个动作；封板只调
   `storyctl.py chapter commit`，不要再另调 `tracking_commit.py commit`（会二次 apply 失败）。
6. **写审分离**。`subagent_model` 是硬要求：writer/scout 用 `default`/`lite`，judge 用 `reasoning`。

## 你禁止做的事

- ❌ 读取 `正文/` `大纲/` `设定/` 下任何 `.md` 的内容（会撑爆你的上下文）
- ❌ 把 `step_token` 或 `done_cmd` 写进 subagent 的提示词
- ❌ 直接调用 `storyctl.py` / `tracking_commit.py` / `gate_runner.py`（一律由 autopilot 内联）
- ❌ 使用 `--accept-current-length` 绕过字数检查
- ❌ 在闸门未通过时声称"本章已完成"
- ❌ 手动改 `_pipeline/state.json`（续跑用 `resume`，进度只信磁盘）

## 标准循环

```
while True:
    r = run("python <repo>/novel-workbench/scripts/autopilot.py next --book <book>")
    if r.done: break
    if r.kind in ("llm", "verify"):
        spawn_subagent(
            role=r.subagent_role,
            prompt=r.subagent_prompt,
            model=r.subagent_model,        # ← 必须传！writer=default/lite, judge=reasoning
        )
    # script 节点已被 autopilot 内联跑完，到这里一定是 llm/verify
    run(r.done_cmd_for_orchestrator_only)
```

## 流程拓扑

| 段 | 节点 | 说明 |
|---|---|---|
| 开书 | N01 初始化 → N02 文风锚定 → N03 选题 → N04 选题闸门 → N05 设定 → N06 设定校验 → N07 卷纲 → N08 主线闸门 → N09d 前 12 章目录 | N04/N06/N08 是 reasoning judge |
| 单章 | `C{n}.N09` 场景卡 → N10 细纲闸门 → N11 上下文组装 → N12 写正文+填追踪事务 → N13 合并检查+平台闸门 → N14 定点改写 → N15 阶段质量闸门 → N16 原子封板 | N10/N11/N13/N16 脚本内联；N14 无 B 级 findings 自动跳过；N15 只在第 1/3/6/10 章执行 |
| 终审 | N17 全文整合 → N18 AI味/退化扫描 → N19 文风漂移+伏笔回收 → N20 评分卡终审 → N21 投稿包打包 | 问题统一汇入 `_pipeline/endgame_findings.json` |
| 回跳 | N20 block → N20f 外科手术补丁 → N17 → … | 最多 2 轮；第 3 次仍 block 停机交人工 |

**单章失败的路由**：
- N13 有 B 级 findings → N14 定点改写（最多 2 轮，第三次停机）
- N15 block → 回 N14（最多 2 轮）
- N16 封板失败（质量/字数/追踪事务缺失）→ 回 N14；工具不可用则直接停

## 常用命令

```bash
PY="C:\Users\Wane-\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe"
AUT="<repo>\novel-workbench\scripts\autopilot.py"

"$PY" "$AUT" init --title "《书名》" --genre niandai --target-words 20000 --max-chapters 12 --book "F:\小说\works\书名"
"$PY" "$AUT" next    --book "F:\小说\works\书名"
"$PY" "$AUT" done    --book "..." --node C1.N12 --token <token> --evidence "正文/第001章_*.md"
"$PY" "$AUT" skip    --book "..." --node C1.N14 --token <token> --reason "..."
"$PY" "$AUT" block   --book "..." --reason "..."
"$PY" "$AUT" status  --book "F:\小说\works\书名"
"$PY" "$AUT" resume  --book "F:\小说\works\书名"
```

`done` 的 evidence 支持通配符：章节文件名由 writer 定标题，所以传 `正文/第001章_*.md` 即可。

## 产出与投稿

跑完 N21 后，`投稿包/` 下会有：

- `全文.md`：所有章节合并（投稿要求不拆章）
- `体检报告.md`：机器闸门逐项结果；结论只有「可投 / 不可投」
- `投稿邮件.md`：主题、主线、附件清单、人工确认项
- `投稿记录.md`：投稿时间、编辑、结果回填栏

**投稿前人工确认**（机器无法替代）：
1. 只联系一个编辑；这本书只有一次签约评估机会
2. 前 200 字冲突 / 前 600 字升级 / 前 6000 字主线 / 三章内反转
3. 文档不拆成多封邮件，按编辑要求附大纲与设定
4. 回填编辑反馈到 `投稿记录.md`——那是下一轮校准的唯一真数据

## 接力与 Dashboard

- 每封板 3 章，`_pipeline/relay.md` 会写一份接力口令；新会话粘贴即可接管。
- Dashboard 扩展在 `C:\Users\Wane-\.qoder\skills\story\`：
  `GET /api/pipeline/status`、`POST /api/pipeline/start`、`POST /api/pipeline/resume`、
  `GET /api/pipeline/report?chapter=N`。
- `start/resume` 按钮**直接执行** `autopilot.py`：resume 先清阻断与中断节点，再跑 next，
  把连续的确定性节点一次推完，并写 `_pipeline/run.json` 留痕。
- 推进到 llm/verify 节点时，接口返回 `instruction.subagent_prompt`（已剥离
  `step_token` / `done_cmd`），按钮自动复制该提示词；回到对话粘给编排者即可继续。
- **脱离对话执行（P2）**：`node novel-workbench/server/cli.mjs run --book <书>` 会由后端运行时
  自己调模型、写证据、交令牌；`--mock` 用于无密钥演练。token 只在运行时进程内读取，
  不进结果、不进日志。对话编排者仍是默认路径，两者共用同一个状态机，不要同时跑。
- Dashboard 本身仍不 spawn subagent；它的职责是"把状态机推到下一个需要 agent 的位置"，
  或者把书交给运行时。

## 环境

- Python：`C:\Users\Wane-\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe`
- `autopilot.py` 启动时把 stdout/stderr 强制为 UTF-8（Windows 管道默认 cp936 会让中文 `subagent_prompt` 乱码），
  宿主按 UTF-8 读取即可；**不要再额外套转码**
- 根目录默认从 `novel-workbench/config.json` 读，**不许硬编码相对路径**；用户机器上以数据目录的
  `settings.json` 覆盖（优先级：命令行/环境变量 > settings > config.json），由 `#/setup` 向导写入
- 书目录：正式书放 `F:\小说\works\<书名>`；`_phase0/book2` 是联试书，当前暂停，不要续跑

## 当前状态

Phase 0–3 已落地并做过夹具验证：

- 全局节点 N01–N09d、章节循环 C.N09–N16、终审 N17–N21 全部可执行
- N20f 两轮回跳、N15→N14 两轮改写、N16 事务缺失回退均已实测
- 投稿包四个文件可自动生成
- 尚未用真实新书跑完整 12 章；下一步是等用户放行后在 `F:\小说\works\` 开新书
