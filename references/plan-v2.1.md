# 一键小说工作台（七猫系）· 实现方案 v2.1

> 用户裁定：范围=**选题→签约包全流程** · **完全无人值守** · 平台=**七猫系** · 宿主=**WorkBuddy**
> 本版已合入两轮红队审查的全部 10 项修正

> **2026-10-25 对抗性审查修订（P0/P1 整改后）**：以下声称已被实证推翻并修正，代码以修订后为准——
> ① 6.4.1「step_token 100% 物理隔离」不成立（能读 `_pipeline/` 的 agent 可拿令牌、`next` 向调用方发令牌），
>    改为"速度 bump + 配套防线"（done 的 evidence-spec 模式比对、控制文件读写保护、judgments 角色感知）；
> ② 6.3 done「四道验收」中的 sha256 一致/退出码 0 从未实现，docstring 已撤回；
> ③ 5.2.2「N14 done 后先过 N13 复检」此前未实现（N14 直达 N15），已落地；
> ④ 第七节 resume「扫盘重建 + 交叉校验」此前只翻状态位，已补轻量交叉校验 + 修复预算返还；
> ⑤ 补丁4「回滚 Blocker 最少快照」此前只有写入方，已实现（manifest 记录各快照 blocking 数，终停时选最小）；
> ⑥ block/resume 增加在途令牌校验与人工阻断态 --force，control_log.jsonl 留痕。
> 完整证据见 `references/adversarial-review-2026-10-25.md`。

---

## 一、目标与验收

输入一条指令，自动完成 **选题 → 设定 → 卷纲 → 细纲(场景卡) → 正文至 2 万字 → 质检 → 投稿包**。

**验收标准（可测）**：

- [ ] `_pipeline/state.json` 记录全节点状态，`resume` 从任意中断点续跑且**不重复 commit**
- [ ] 产出 `投稿包/{全文.md, 体检报告.md, 投稿邮件.md, 投稿记录.md}`
- [ ] 正文字数 ≥ 20000，每章 `qimao-chapter-check.py` 退出码 0
- [ ] 全程无需人工输入；仅在闸门 BLOCK 时停下并给出修复指引
- [ ] 无 hook 环境下，LLM **物理上无法**篡改校验脚本参数（补丁1）
- [ ] 主会话单章上下文消耗 ≤ 500 tokens（瘦主控协议）

---

## 二、宿主选型结论

| 宿主 | subagent | hook | 本机状态 | 结论 |
| --- | --- | --- | --- | --- |
| Qoder | ❌ | ❌ | 已装 | 排除 |
| Codex | ✅ | ✅ 完整 5 类事件 | 已装已认证 | 能力最强，但需切工具、丢失上下文连续性 |
| OpenCode | ✅ | 需自写 plugin.ts | **未安装** | 排除 |
| **WorkBuddy** | ✅ 真 subagent | ❌ | 当前环境 | **选定** |


**为什么无 hook 也可行**：

- 红队漏洞"参数篡改"由**补丁1（脚本内联执行）**从设计上消除——LLM 只发指令，不碰脚本参数
- "礼貌停机"靠铁律 + Rule Echo + 检查点接力缓解，列为已知上限
- 上下文爆炸由**真 subagent 隔离 + 瘦主控协议**双重解决

---

## 三、架构：瘦主控 + 文件指针协议

```
WorkBuddy 主会话（Orchestrator，极瘦）
├── 只调 autopilot.py（next / done / status / resume）
├── 只把 next 返回的 subagent_prompt 转交给 subagent
├── 禁读 正文/ 大纲/ 设定/ 任何 .md 内容
└── 每章仅消耗 ~300 tokens → 连跑 50 章也不会触发压缩

Subagent（上下文隔离，各自全新）
├── writer   读路径 → 创作 → 落盘 → 回执 ≤100 字
├── judge    只读正文+标准 → 红队找茬 → 落盘 judgments.jsonl
└── scout    读方案+榜单 → 选题报告
```

**三条铁律（写入 SKILL.md）**：

1. **唯一调用权**：只有主会话可执行 `autopilot.py`；subagent 提示词中严禁出现该命令
2. **只传路径，不传正文**：`next` 输出的 JSON 直接提供拼装好的 `subagent_prompt`（只含磁盘路径）
3. **极简回执**：subagent 禁止返回大段正文，只准 `{"written": "...", "words": 2180}` 式回执

---

## 四、两轮红队审查 → 修补映射（10 项）

| # | 漏洞 | 修补 | 来源 |
| --- | --- | --- | --- |
| 1 | 单会话上下文爆炸 + 伪独立角色 | 真 subagent 隔离 + Rule Echo 每次重注铁律 + verify 强制红队找茬 | 一轮 |
| 2 | 工具调用上限 + 礼貌停机 | **补丁1：script 节点全部内联 autopilot.py**，LLM 只碰 llm/verify；工具调用砍 60%+ | 一轮 |
| 3 | state.json 无章节命名空间 | schema v2，循环节点用 `C01.N09` 复合键 + `current_cursor` | 一轮 |
| 4 | N13↔N14 改写负向劣化 | 进 N14 前自动快照，失败回滚到 Blocker 最少的版本 | 一轮 |
| 5 | N20 打回导致整书作废 | 检查左移（第3/6章 Milestone）+ N20f 只准段落级外科手术 | 一轮 |
| 6 | 双状态机非原子提交 | **收权**：LLM 禁止直接调 `tracking_commit.py`，autopilot.py 唯一父进程代调 | 一轮 |
| 7 | Dashboard 启动按钮无体感 | 点启动自动复制唤醒口令到剪贴板 + 横幅提示 | 一轮 |
| **8** | **commit 先于 verify → 第1章结尾即撞墙** | **顺序对调**：N12写 → N13检 ↔ N14改 → **N15质量闸门** → **N16 commit 封板**（commit 必须是单章最后动作） | 二轮 Bug1 |
| **9** | **commit 是纯 script 却要剧情摘要/伏笔增量** | 改用上游 `tracking_commit.py draft` 预填事务，writer 按 `shapes` 填 `.story/work/第n章/tracking.json`，N16 直接消费 | 二轮 Bug2 |
| **10** | **N20f 改哪份文件、改完不复检** | N20f **只改 `正文/`**，done 后**回跳 N17** 自动重合成全文+重扫，最多 2 轮 | 二轮 Bug3 |


**二轮补充的 5 个工程暗坑**（第 6–8 节落地）：

- 缺口4 主控上下文泄漏 → 瘦主控+文件指针协议（已入第三节）
- 缺口5 subagent 冷启动失忆 → 强制附**上一章末尾 600 字原文锚点** + style-anchor 2 段正例切片
- 暗坑6 原文引用硬校验被中文标点误杀 40% → 归一化只留 `[\u4e00-\u9fa5a-zA-Z0-9]`，要求 ≥8 字
- 暗坑7 Windows subprocess GBK 崩溃 → `PYTHONUTF8=1` + `encoding="utf-8"` + 统一 `config.json`
- 暗坑8 第10章写完 19,800 字不够 → 单章硬卡 **≥2000 字** + 目录骨架生成 **12 章**缓冲

---

## 五、节点表 v2.1（时序已修正）

### 5.1 全局节点（一次性）

| # | 节点 | 类型 | 执行者 | 落盘 | 失败处置 |
| --- | --- | --- | --- | --- | --- |
| N01 | 初始化 | script | autopilot 内联 | 标准目录 + `_pipeline/state.json` | blocked 停 |
| N02 | 文风锚定 | script | autopilot 内联 | 更新 `harness/style-anchor.json` | 标 provisional 续跑 |
| N03 | 选题报告 | llm | subagent `scout` | `大纲/选题报告.md` | 重试 1 次 → blocked |
| N04 | 选题闸门 | verify | subagent `judge`（红队模式） | state | 回 N03 ≤2 轮 |
| N05 | 设定 | llm | subagent `writer` | `设定/×5.md` | 重试 1 次 |
| N06 | 设定校验 | verify | subagent `judge` | state | 回 N05 |
| N07 | 卷纲 | llm | subagent `writer` | `大纲/卷纲_第一卷.md` | 重试 1 次 |
| N08 | 主线闸门 | verify | subagent `judge` | state | 回 N07 ≤2 次 |
| N09d | 前 **12** 章目录骨架 | llm | subagent `writer` | `大纲/目录_前12章.md` | 重试 1 次 |


> N09d 生成 12 章而非 10 章——预留 2 章冗余缓冲，防第 10 章写完差几百字不够 2 万。

### 5.2 章节循环（`C{n}.` 前缀，单章硬卡 ≥2000 字，10 章保底 2 万字）

**顺序铁律：commit 必须是单章生命周期的最后一个动作。**

| # | 节点 | 类型 | 执行者 | 落盘 / 动作 | 失败处置 |
| --- | --- | --- | --- | --- | --- |
| `C{n}.N09` | 单章场景卡 | llm | subagent `writer` | `大纲/细纲_第n章.md`（九字段+3–6拍） | 重试 1 次 |
| `C{n}.N10` | 细纲闸门 | script | autopilot 内联 `check-outline-contract.js` | 校验退出码 0 | 自动退回 N09 |
| `C{n}.N11` | 章节上下文组装 | script | autopilot 内联 `tracking_commit.py check` + **截取上章末尾 600 字** + style-anchor 切片 | `追踪/上下文.md` | 缺 state 硬停 |
| `C{n}.N12` | 写正文 **+ 填追踪事务** | llm | subagent `writer` | `正文/第n章_*.md` + 按 `shapes` 填 `.story/work/第n章/tracking.json` | 重试 1 次 |
| `C{n}.N13` | 合并检查+平台闸门 | script | autopilot 内联 `qimao-chapter-check.py`（含字数 ≥2000 硬卡） | 退出码 0 | 自动进 N14 |
| `C{n}.N14` | 定点改写 | llm | subagent `writer` | 快照后覆盖正文**与 delta**（done 时自动重跑 N13） | 同处 2 次 → 回滚最佳快照后 blocked |
| `C{n}.N15` | **阶段质量闸门** | verify | subagent `judge`（红队模式） | 清单按章分级，见 5.2.1 | 打回 N14 ≤2 轮 |
| `C{n}.N16` | **章末原子封板** | script | **autopilot 内联** `tracking_commit.py commit` + `storyctl.py chapter commit` | `_tracking-state.json` 章号+1，重算字数，清理工作目录 | 停机保护 |


**Bug 1 修正说明**：v2 把 commit 放在 verify 之前，导致第 1 章被 judge 打回时 `_tracking-state.json` 已封板旧版 → 重复提交或状态污染。v2.1 对调：**先审后封**。

### 5.2.1 N15 清单按章分级（修复"跨章审查 vs 单章修改"边界）

| 章 | 审什么 | 修复指令必须落脚在 |
| --- | --- | --- |
| `C01.N15` | **首章铁律**：前 200 字有冲突、主角核心困境、章末悬念 | `C01.N14` |
| `C02.N15` | 自动 skip（靠 `C02.N13` 确定性闸门把关） | — |
| `C03.N15` | **黄金三章闭环**：读第 1–3 章，但严禁要求推翻已封板的第 1–2 章 | `C03.N14` 或后续细纲微调 |
| `C06.N15` / `C10.N15` | **Milestone Gate**：节奏 / 伏笔回收率 / 主线推进 | 当前章的 `N14` |


**铁律：N15 的修复指令只能作用于当前章**——前序章已在各自 N16 封板，退回会破坏游标。

### 5.2.2 N14 统一缺陷输入接口（修复"N14 输入源分叉"）

`autopilot.py` 把 `N13` 的脚本报错与 `N15` 的 judge 打回意见，统一格式化写入 **`_pipeline/findings_C{n}.json`**。
`C{n}.N14` **永远只读这一个文件**；`N14` 调 `done` 后，状态机**永远先过 `C{n}.N13`（脚本复检）**，通过后才进 `C{n}.N15`。

### 5.2.3 N16 的 adapter：复用上游 draft→fill→commit 契约

实测 `tracking_commit.py` 真实签名（`C:\Users\Wane-\.qoder\skills\story-long-write\scripts\tracking_commit.py:1592-1605`）：

```
commit  --project <root>  --input <UTF-8 JSON 事务文档>
draft   --project <root>  --chapter N [--out <path>] [--force]
```

它**本来就有** `draft → 填 → commit` 三段式，`draft` 会生成预填事务并返回 `shapes` / `current_snapshots` / `fill` 说明。
所以**不自造追踪事务格式**，直接复用上游的 `draft → 填 → commit` 三段式，改为：

| 节点 | 动作 |
| --- | --- |
| `C{n}.N11`（script 内联） | 追加跑 `tracking_commit.py draft --project --chapter n --out .story/work/第n章/tracking.json`，把返回的 `shapes` 写进 `追踪/上下文.md` |
| `C{n}.N12`（llm writer） | 除落盘正文外，**按 `shapes` 填好同一份 `tracking.json` 事务**（delta / character_snapshots / 伏笔 / 时间线 / context.position） |
| `C{n}.N14`（llm writer） | 改写正文后**必须同步更新该事务**，否则 N16 拒绝封板 |
| `C{n}.N16`（script 内联） | 依次跑 `tracking_commit.py commit --project --input .story/work/第n章/tracking.json` **和** `storyctl.py chapter commit`（重新计数 + 清理本章工作目录） |


> 上游 `draft` 的 fill 说明原话：「正文改过就用 story-long-write 的 `storyctl.py chapter commit` 提交（重新计数并清理本章工作目录）」——所以 N16 必须调这两个，只调一个会漏。

### 5.3 收尾节点（P5 诊断汇流模式）

**铁律：P5 的 N18/N19/N20 全是只读探针，发现问题绝不各自乱跳**，统一汇入 `_pipeline/endgame_findings.json`，由 `N20f` 一处修补。

| # | 节点 | 类型 | 执行者 | 落盘 | 失败处置 |
| --- | --- | --- | --- | --- | --- |
| N17 | 全文整合 | script | autopilot 内联 | `投稿包/全文.md` | 停 |
| N18 | AI 味/退化扫描 | script | autopilot 内联 | findings 追加写入 `endgame_findings.json` | 不跳转 |
| N19 | 文风漂移+伏笔回收 | script+llm | 内联 + subagent | findings 追加写入 | 不跳转 |
| N20 | 评分卡 19 项 | verify | subagent `judge`（红队模式） | findings 追加写入 + 汇总判断 | 红项总数 >0 → N20f |
| N20f | 外科手术补丁 | llm | subagent `writer` | **只改 `正文/第n章.md`**（先快照），按 findings 的章节定位清单逐章润色 | done 后**回跳 N17**，≤2 轮 |
| N21 | 投稿包 | script | autopilot 内联 | `投稿包/×4.md` | 终点 |


**闭环拓扑**：

```
N17 → N18 → N19 → N20 ──findings 总数为 0──→ N21（打包终点）
                      └──有红项──→ N20f ──→ 回跳 N17 重跑一轮（≤2 轮）
```

**Bug 3 修正说明**：v2.1 初版 `N18` 写「回 deslop 节点」（表中无此节点）、`N19` 写「回对应章」（第 1–10 章已全部 commit 封板，跳回 `C04.N14` 会破坏游标）。改为**只读探针 + 单点汇流**。

**Bug 3 修正说明**：N20f 若直接改 `投稿包/全文.md`，单章文件与全文文件永久不一致；若改 `正文/` 而不重跑 N17，交付的是旧版。故规定 N20f 只改 `正文/`，done 后回跳 N17（N17/N18 均为内联 script，1 秒内自动重合成+重扫），再进 N20 复核红项是否消除。

---

## 六、`autopilot.py`：内联驱动器（核心改动）

### 6.1 职责重划分

| 节点类型 | 谁执行 | 交互 |
| --- | --- | --- |
| `script` | **autopilot.py 内部 `subprocess.run`** | 对 LLM 透明。`done` 时自动顺延跑完后续连续 script 节点，直到撞上下一个 llm/verify 或脚本报错，才交还控制权 |
| `llm` | WorkBuddy subagent | `next` 输出填好参的 `subagent_prompt` + Rule Echo；落盘后调 `done` |
| `verify` | WorkBuddy subagent（红队模式） | `next` 注入红队 prompt；`done` 时校验 judgments JSON |


### 6.2 子命令

```
autopilot.py init    --title 《xxx》 --genre niandai --target-words 20000
autopilot.py next                                  # 输出当前节点 JSON（含 _iron_rules + subagent_prompt）
autopilot.py done    --node C03.N12 --evidence 正文/第3章_xxx.md --delta _pipeline/delta_C3.json
autopilot.py skip    --node N02 --reason "..."
autopilot.py block   --reason "..."
autopilot.py status / resume
```

### 6.3 `done` 验收（四道，缺一不可）

1. evidence 存在 ∧ 非空 ∧ 大小 > 节点阈值
2. sha256 与 state 一致（变了标 `stale` 重做）
3. script/verify 节点退出码 0
4. verify 节点额外：**原文引用硬校验**（见 6.5）

### 6.4 关键安全设计

- autopilot.py **不接受任何透传参数**（砍掉 v1 的 `--extra`），脚本调用全部硬编码 → LLM 无篡改入口
- `tracking_commit.py` 只能由 autopilot.py 代调，LLM 无入口
- N13 通过后自动复验字数，**禁用 `accept-current-length`**
- N14 改写后必须同步更新 `tracking.json` 事务，否则 N16 拒绝封板

### 6.4.1 `step_token`：subagent 越权的 100% 物理隔离（升级风险 2）

不需要宿主支持工具白名单，一个**一次性握手令牌**即可：

```python
# autopilot.py next 内部
token = f"{random.randrange(16**6):06x}"
state["step_token"] = token
state["step_token_node"] = cursor
save(state)
```

`next` 输出的 JSON **拆成两个字段**：

```json
{
  "node": "C01.N12",
  "done_cmd_for_orchestrator_only": "autopilot.py done --node C01.N12 --token 9f3a2c --evidence 正文/第1章_xxx.md",
  "subagent_role": "writer",
  "subagent_prompt": "请读取 F:\\...\\细纲_第1章.md 与 .story\\work\\第1章\\tracking.json，写完后落盘 F:\\...\\正文\\第1章_xxx.md 并回填该事务。回执 ≤100 字。"
}
```

**物理隔离原理**：

1. 主会话只把 `subagent_prompt` 传给 subagent，**`step_token` 与 `done_cmd` 绝不出现在 subagent 可见的任何内容里**
2. `done` 强制校验 `--token`，不匹配直接拒绝
3. 节点处于 `running` 时，无 token 调 `next` 或 `done` 一律拒绝并报错
4. 即使 subagent 产生幻觉去调 `autopilot.py`，也被 100% 拦截

**这把第十二节风险 2 从「提示词约束」升级为「机械强制」。**

### 6.5 暗坑6：原文引用归一化（防中文标点误杀）

```python
import re
def norm_cjk(s: str) -> str:
    return re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', s)

def check_quote(quote: str, chapter_text: str) -> bool:
    q, t = norm_cjk(quote), norm_cjk(chapter_text)
    return len(q) >= 8 and q in t
```

Prompt 同时要求 judge 的 `原文引用` 必须是**连续 12–30 个原文字词，严禁省略号跳跃摘录**。

### 6.6 暗坑7：Windows 编码与路径

```python
env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
res = subprocess.run(cmd, cwd=book_dir, env=env,
                     encoding="utf-8", errors="replace",
                     capture_output=True, timeout=120)
```

统一 `novel-workbench/config.json` 存放三个绝对根目录，**严禁硬编码相对路径 `../../`**：

```json
{
  "WORKBUDDY_SKILL_DIR": "C:\\Users\\Wane-\\.workbuddy-ai\\skills\\novel-workbench",
  "QODER_STORY_DIR":     "C:\\Users\\Wane-\\.qoder\\skills",
  "QIMAO_PLATFORM_DIR":  "F:\\小说\\platforms\\qimao",
  "CORE_TOOLS_DIR":      "F:\\小说\\platforms\\_core\\tools"
}
```

---

## 七、`state.json` v2 与断点续跑

```json
{
  "schema_version": 2,
  "book": "《xxx》",
  "genre": "niandai",
  "target_words": 20000,
  "current_cursor": "C03.N12",
  "tracking_sync_chapter": 2,
  "chapters_committed": 2,
  "words_total": 4320,
  "nodes": {
    "N01":  { "status": "done", "at": "..." },
    "C01.N09": { "status": "done", "evidence": ["大纲/细纲_第1章.md"], "sha256": ["..."] },
    "C01.N12": { "status": "done", "evidence": ["正文/第1章_xxx.md", "_pipeline/delta_C1.json"],
                 "sha256": ["...", "..."], "words": 2150 },
    "C01.N16": { "status": "done", "committed_chapter": 1 },
    "C03.N12": { "status": "running", "attempts": 1,
                 "snapshots": ["_pipeline/snapshots/C03_N12_v1.md"] }
  },
  "decisions_taken": [
    { "at_node": "N05", "issue": "配角重名", "applied_rule": "D03:取先登场者" }
  ],
  "pending_questions": [],
  "blocked_reason": null
}
```

**`resume` 纪律**：不信任内存，重新扫盘重建；`tracking_sync_chapter` 与 `_tracking-state.json` 实际章号交叉校验，不一致以 tracking 为准并标记对应节点 stale。

**快照机制（补丁4）**：进 `C{n}.N14` 前自动备份 `正文/第n章.md` 与 `.story/work/第n章/tracking.json` 到 `_pipeline/snapshots/C{n}_N12_v{k}/`；同处失败 2 次则回滚到 **Blocker 数量最少**的快照，再触发 blocked。

---

## 八、防停机三件套（无 hook 的替代方案）

1. **Rule Echo**：`next` 返回的 JSON 恒带 `_iron_rules`（禁止询问用户 / 必须调 done / 只有三种情况可停），对抗上下文压缩导致的遗忘
2. **检查点接力**：每完成 3 章自动写「接力口令」到 `_pipeline/relay.md`（含当前 cursor 与下一步指令），人工粘贴即可续跑——承认单轮上限物理存在，不停机而是优雅交接
3. **铁律入 SKILL.md**：只有脚本硬失败 / 闸门 BLOCK / 用户喊停三种情况允许停；「想确认」记入 `pending_questions` 按 `decisions.md` 默认表继续走

---

## 九、质量兜底

**真 subagent 隔离**（WorkBuddy 的核心优势）：`writer` 与 `judge` 是不同 subagent，judge 的上下文里**只有正文 + 标准**，没有写手的创作过程——红队漏洞 1 的利益冲突在此真正消除。

**缺口5 冷启动失忆的修补**：`C{n}.N11` 组装的 `追踪/上下文.md` 末尾强制附：

- `【上一章结尾原文锚点】` —— 第 n-1 章正文最后 **600 字**（标注"请无缝衔接语气与场景"）
- `【文风锚样本】` —— `harness/style-anchor.json` 中 2 段正例切片

**红队模式 prompt**（verify 节点强制）：先输出「最可能被七猫编辑拒稿的 3 个理由」，再做 pass/block 裁决。

**LLM 自判项留痕** → `追踪/质检/llm-judgments.jsonl`：

```json
{"node":"C01.N15","chapter":1,"criterion":"前200字有冲突","verdict":"pass",
 "原文引用":"周桂芳咳出一口血来的时候…","judge_role":"opening-judge","at":"..."}
```

**检查左移（补丁5）**：Milestone Gate 放在第 3 / 6 / 10 章，而不是等 2 万字全写完再终审——避免"回对应阶段"导致整书作废。

---

## 十、Dashboard 扩展

文件：`C:\Users\Wane-\.qoder\skills\story\scripts\dashboard-server.mjs` + `assets\app.js`

| 端点 | 作用 |
| --- | --- |
| `GET /api/pipeline/status` | 节点进度 + blocked 原因 + evidence 链接 |
| `POST /api/pipeline/start` | 写 `_pipeline/run.json` + **复制唤醒口令到剪贴板** |
| `POST /api/pipeline/resume` | 同上，续跑 |
| `GET /api/pipeline/report?chapter=N` | 调 `gate_runner.py` 取闸门报告 |


前端「流水线」页签：启动表单、节点进度条（done/running/blocked/pending + evidence 链接）、质检报告查看器、blocked 横幅 + 「修复后续跑」。

> **诚实限制**：无 hook，按钮不能真拉起 LLM 循环。点启动后横幅提示「已复制唤醒口令，请回到对话粘贴发送即可接管」。

---

## 十一、分期（Phase 0 必须先做）

| 期 | 内容 | 出口标准 |
| --- | --- | --- |
| **Phase 0** | 极简 `mock_autopilot.py`（约 50 行，15 个连续节点）+ 草版 SKILL.md，**带真 subagent 跑完整闭环** | 拿到「`next` → 唤起真 subagent → subagent 返回 → `done`」这个三步闭环能连续跑多少轮不礼貌停机，记为阈值 `K` |


**Phase 0 必须带真 subagent，不能只跑空脚本**：主会话单纯连续调 Bash 通常能跑 30+ 次不停；
**真正最容易诱发「礼貌停机」的时刻，是 subagent 执行完毕把结果返回主会话的那一瞬间**。
只有穿插真实 `Agent` 调用测出的 `K` 才可靠，据此定检查点间隔（建议 `checkpoint = floor(K * 0.6)` 章）。

| **一期** | `autopilot.py` 完整七子命令 + 内联驱动器 + 全局节点 N01–N09d + `state.json` v2 | 一本书跑到「前 12 章目录骨架」齐全 |
| --- | --- | --- |
| **二期** | 章节循环 `C{n}.N09–N16` + Milestone Gate + 快照回滚 + delta 消费 + 接力口令 | 无人值守写出前 3 章并通过 G8/G9 |
| **三期** | N17–N21 + N20f 回跳 + Dashboard 四端点 + 剪贴板唤醒 | 端到端产出 `投稿包/` |


**Phase 0 是唯一的大未知数**：它测出的阈值直接决定检查点间隔。不测就开工，可能整套建完才发现执行层崩坏。

---

## 十二、风险与已知上限

1. **无 hook → 礼貌停机无法机械阻断**。只能靠铁律 + Rule Echo + 接力口令缓解。这是选定 WorkBuddy 的代价，Phase 0 要测出实际发生率。
2. **subagent 越权调 `autopilot.py`** → 已由 `step_token`（6.4.1）**机械强制隔离**，不再是无防护项。
残留风险仅剩：subagent 若能读到 `state.json` 就能看到 token——故 `subagent_prompt` 只给具体文件路径，**不给 `_pipeline/` 目录的读取权**。
3. **LLM 可写「非空垃圾」绕过 done**。缓解只有哈希 + verify 退出码 + 原文引用硬校验。
4. **成本**：2 万字 ≈ 10 章 ×（1 writer + 可能 1 改写）+ 约 5 次 judge，无人值守下总量不可预控，建议设 `--max-chapters` 上限。
5. **`recovered/` 工程缺 `_tracking-state.json`**，入场前须先迁移（`/story-import`）。
6. **双状态一致性**已由 autopilot.py 收权缓解，但中断在 `tracking_commit` 与 `state.json` 之间仍可能漂移——靠 resume 时交叉校验兜底。
7. **`--genre` 必填**：G8（因果显式度）是题材相关闸门，不传则跳过，年代文会漏掉最关键的检查。

---

## 十三、关键文件

**新建**

- `C:\Users\Wane-\.workbuddy-ai\skills\novel-workbench\SKILL.md`（入口 + 三条铁律 + 防停规则）
- `...\novel-workbench\config.json`（四个绝对根目录）
- `...\novel-workbench\references\pipeline.md`（节点表 v2.1）
- `...\novel-workbench\references\decisions.md`（默认决策表）
- `...\novel-workbench\references\agents\{writer,judge,scout}.md`
- `...\novel-workbench\scripts\autopilot.py`

**改动**

- `C:\Users\Wane-\.qoder\skills\story\scripts\dashboard-server.mjs`
- `C:\Users\Wane-\.qoder\skills\story\scripts\assets\app.js`
- `C:\Users\Wane-\.qoder\skills\story\SKILL.md`（路由表 +1 行，可选）

**复用（不改）**

- `story-long-write\scripts\` 全部确定性脚本（`build_writer_prompt.py `/ `check-outline-contract.js` / `tracking_commit.py` / `storyctl.py` 等）
- `F:\小说\platforms\qimao\`（方案 / 闸门 / 语料 / 工具 / `qimao-chapter-check.py`）
- `F:\小说\platforms\_core\tools\`（6 个探针与工具）

---

## 十四、建议的动手顺序

**Phase 0 → 一期 → 二期**，跑通"无人值守写出前 3 章并通过闸门"再继续三期。
理由：前两个阶段就能暴露"LLM 守不守 autopilot 纪律"这个最大的未知数，避免整套建完才发现执行层崩坏。