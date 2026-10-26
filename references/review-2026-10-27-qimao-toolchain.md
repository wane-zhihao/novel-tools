# 七猫系工具链（"铲子"）批判性审查

- 审查日期：2026-10-27
- 审查对象：`F:\小说\platforms\`（番茄 / 七猫 / 纵横三平台配置与技能）、`~/.workbuddy-ai/skills/` 三平台 write 技能、`F:\小说\works\顶职` 项目数据
- 参照记录：`F:\WorkBuddy AI\2026-10-22-19-33-42\.workbuddy-ai\memory\2026-10-26.md`
- 审查方式：**不采信自述，全部从磁盘取证**（读配置 JSON、统计目录、比对 SHA-256、数正文汉字）

---

## 一句话结论

**工具链本身是可用的，而且比同类项目诚实**（阈值标注了来源与未核实状态、`metrics_only` 没假装成闸门）。但它存在一个会直接影响投稿判断的硬问题：**书的 state 与正文/账本三方不一致，导致字数指标错了 54%**。同时有两处自述与磁盘不符，需要纠正记录。

---

## 一、三处与自述不符（附证据）

### ① 严重：《顶职》三方数据互相对不上

| 来源 | 内容 |
|---|---|
| `正文/` 实际文件 | 9 个章节文件，去空白汉字合计 **20,066** |
| `追踪/_tracking-state.json` | `last_committed_chapter = 8`，`wordcount_records` 有 1–8 |
| `_pipeline/state.json` | `chapters_committed = 4`、`words_total = 9288`、`cursor = C5.N14` |

记忆里写的「5 章 / 11,798 字 / 对 2 万字线 59% / 差约 8,202 字」——**写下时就已过期**。

**为什么这是硬问题**：`words_total` 是 `GET /api/books/:id/preflight` 的字数来源（`server/sync.mjs:129` 直接取 `state.words_total`）。所以：

- 正文明明已过 2 万字，投稿预检会报「还差字数」
- 界面进度长期显示 46%，而不是实际 100%
- 更危险的是**续写位置**：cursor 停在 `C5.N14`，而账本说 8 章已定稿。直接续跑会重做第 5 章，与已存在的正文冲突

这类漂移此前**没有任何出口**——错了没人知道。

### ② 「仓库副本与适配层逐字同文」不成立

三对 `SKILL.md` 的 SHA-256 全部不同：

| 技能 | adapter | repo |
|---|---|---|
| fanqie-write | `5317c7a619bf` | `c06ddc5b4f1d` |
| qimao-write | `2acea2ed5fad` | `df2f4c7e789d` |
| zongheng-write | `b006d247babf` | `cc422e668f47` |

原因是 adapter 是**注册外壳**（带 frontmatter + 绝对路径 + 路由 description），repo 是源文档——它们本来就不该同文。同一段记忆里既写「绝对路径、不复制规则」又写「逐字同文」，两句自相矛盾。

**真正的风险不是不同文，而是没有守护**：没有任何测试检查 adapter 里那 17/19/17 条绝对路径是否仍然存在。仓库一搬家，技能就静默失效。

### ③ 技能数口径未交代

磁盘上 `~/.workbuddy-ai/skills/` 有 **64 个目录**，记忆称缓存 **111 项**。两者口径不同（缓存含插件提供的技能），不能直接判错——但记忆没说明口径，读者无法验证。

---

## 二、值得肯定的部分

1. **七猫阈值是诚实的**：`current_min_words: null`，2 万字只作为 `historical_observations` 存在，且 source 明写「local research corpus; current eligibility not independently verified」；`policy_verification` 指向作者后台/当前签约页。**没有把未核实的东西伪装成规则。**

2. **纵横做了真正的核实**：联网查到官方帮助页（满 3 万自动进审、满 10 万可投邮箱、内投 ≥3 万正文+大纲、买断 ≥6 万），并把本地旧「1 万字」在 thresholds / 01_投稿方案 / platform-scout / submission-auditor / README / WORKFLOW 六处全部作废，还记录了官方页之间的冲突（zh100 说 3 万，zh97 说 2 万）。这是整份记录里质量最高的一段。

3. **`metrics_only` 如实标注**：纵横 5 项无阈值，配置里标 `execution_mode: metrics_only`、状态打 `METRICS`，没假装成闸门。

---

## 三、结构性批评（不是 bug，是取向）

1. **「完成」的定义偏了**。验收几乎全是自洽性：JSON 能解析、脚本能编译、runner exit=0、文件存在。这些证明**管道不漏**，不证明**产出的东西能签约**。全书投稿次数：0。

2. **验证层级缺一环**。judge 判定「这章合格」的可信度没有校准集背书（项目计划里也记着「L1 九维评分层校准前不得上线」）。管道说 pass，与编辑说 pass，是两件事。

3. **对称性靠约定维持，而约定已被证伪**。三平台各一套 skill/agent，靠「逐字同文」保持一致——见问题②。这是**迟早会漂移**的结构，应改为「适配层只存指针 + 一条路径存在性测试」。

---

## 四、本次已落地的改动

`/api/books/:id/manuscript` 新增：

```json
{
  "words_total_state": 9288,
  "chapters_committed_state": 4,
  "drift": {
    "ok": false,
    "items": [{ "id": "words_total", "state": 9288, "actual": 20066,
                "detail": "state 记 9288 字，章节表合计 20066 字" }]
  }
}
```

设计取舍：**只报两个数字，不擅自判定谁对**。判断哪个对需要人看正文；工具擅自「修正」反而会掩盖问题。附 Prove-It 测试（把 `words_total` 改成 9288/4 章后 `drift` 必须变红）。

---

## 五、建议处置顺序

| 顺序 | 事项 | 理由 |
|---|---|---|
| 1 | 实现 `tracking:fill`（从账本+正文重建 state，先 dry-run） | 修《顶职》前的必要工具；直接手改 `state.json` 是拿真书赌博 |
| 2 | 用 `tracking:fill` 修《顶职》，先备份 | 解除 cursor 与账本冲突，恢复正确的续写位置与字数 |
| 3 | 加一条测试守 adapter→repo 路径存在性 | 让「逐字同文」这条失效约定退场，换成可执行的检查 |
| 4 | 开书字段统一（platform/vol_size/one_line 落盘） | 多平台已接好，但字段此前空转；已随本次一并完成 |

---

## 六、证据复现方式

```bash
# ① 三方数据
ls "F:/小说/works/顶职/正文/"            # 9 个文件
python -c "import json;print(json.load(open(r'F:/小说/works/顶职/_pipeline/state.json',encoding='utf-8'))['words_total'])"
python -c "import json;print(json.load(open(r'F:/小说/works/顶职/追踪/_tracking-state.json',encoding='utf-8'))['last_committed_chapter'])"

# ② 同文声称
sha256sum ~/.workbuddy-ai/skills/qimao-write/SKILL.md "F:/小说/platforms/qimao/skills/qimao-write/SKILL.md"

# ③ 技能数
ls -1 ~/.workbuddy-ai/skills/ | wc -l
```
