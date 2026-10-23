# Novel Platform · 一键小说工作台

> 建立：2026-10-23 ｜ 分支 `master` ｜ 提交身份 Wane.Zhihao

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
├── references/
│   ├── plan-v2.1.md             **实现方案 v2.1**（含两轮红队审查的 10 项修补，代码的设计依据）
│   └── phase0-report.md         Phase 0 纪律阈值实测报告
├── novel-workbench/             Skill（~/.workbuddy-ai/skills/novel-workbench 是指向这里的 junction）
│   ├── SKILL.md                 入口 + 铁律
│   ├── config.json              绝对路径根目录（指向旧数据与上游技能）
│   ├── references/
│   │   └── decisions.md         无人值守默认决策表
│   └── scripts/
│       ├── mock_autopilot.py    Phase 0 纪律阈值实测
│       ├── autopilot.py          状态机入口（init/next/done/skip/block/status/resume）
│       ├── commands.py           done / skip / block / resume 实现
│       ├── inline_runner.py      script 节点内联执行 + 原文引用硬校验
│       └── pipeline_spec.py      节点表（流程唯一真相）
└── _phase0/                     Phase 0 测试目录（mock 状态机实测产物）
```

> **Plan 溯源**：`references/plan-v2.1.md` 从 `C:\Users\Wane-\.workbuddy-ai\plans\cosmic-nebula-babbage-MnzvwCoC.md`
> 复制而来（sha256 已校验一致）。原文件保留在原地不动——按"旧资料留原处"的约定。
> 本仓库的代码以这份方案为唯一设计依据；方案若改，改这里，不要改原处。

## 关键设计（详见方案）

- **瘦主控 + 文件指针协议**：主会话只调 `autopilot.py`，禁读正文；`next` 输出含路径的 `subagent_prompt`
- **补丁1 脚本内联**：所有 `script` 节点由 `autopilot.py` 内部 `subprocess.run` 执行，LLM 物理上无法篡改脚本参数
- **`step_token` 一次性令牌**：subagent 越权调用被 100% 机械拦截
- **先审后封**：`commit` 必须是单章生命周期的最后一个动作
- **P5 诊断汇流**：N18/N19/N20 全是只读探针，问题统一汇入 `endgame_findings.json`，由 N20f 单点修补

## 当前进度

- [x] 两轮红队审查 + 宿主选型 + 方案 v2.1 定稿
- [ ] **Phase 0**：带真 subagent 测单轮纪律阈值 K（进行中）
- [ ] 一期：autopilot.py 完整实现 + 全局节点 N01–N09d
- [ ] 二期：章节循环 C{n}.N09–N16
- [ ] 三期：N17–N21 + Dashboard
