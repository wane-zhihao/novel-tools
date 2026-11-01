# Gate Runner 补跑验证 —— 全量完成（2026-11-01）

补跑对象：`20261028-gate_verification-results.md` 中标记为「⏸️ Model timeout，待补跑」的
命令 2（stub_900.md）与命令 4（不存在文件）。本次一并重跑控制组（gbk_sample.md）作交叉确认。

**结论：Novel tools 承诺的全部字段均已兑现，无一失守（4/4 + 控制组 5/5）。**
三条命令实际执行时间均在数秒内 —— 历史上的「超时」是模型侧中断，不是工具侧慢或挂起。

---

## 运行环境

| 项 | 值 |
|---|---|
| 工作目录 | `F:/小说/platforms/_core/tools` |
| 解释器 | Python 3.12.10（`C:/Users/Wane-/AppData/Local/Programs/Python/Python312/python.exe`） |
| 旗标 | `python -B`（无 `__pycache__` 落盘）+ `PYTHONIOENCODING=utf-8` |
| 采集方式 | stdout / stderr 分别重定向至 `%TEMP%\gate{A,B,C,D}.json|.err`，再以 `encoding='utf-8'` 读回解析 |
| stderr | 三次正式运行 + 一次补测**全部 0 字节**，无警告无回退 |
| 边界 | 本次运行**只读** `F:/小说/`，写入目标仅 `.task/` 本报告。写后复查全部 **8 个被读文件** ——`_core/tools/` 下 `gate_runner.py`(10-28 15:57)、`style_probe.py`(10-28 15:57)、`clarity_probe.py`(10-28 15:59)、`clarity_org_probe.py`(10-28 15:59)，`qimao/harness/gates.json`(10-28 16:05)、`genre-baselines.json`(10-26 18:24)，`_audit_fixtures/stub_900.md`、`gbk_sample.md`(均 10-28 03:28)—— mtime 无一变动，本次执行未触发任何写入。`python -B` 已阻止新 `.pyc` 落盘（`__pycache__` 中三者仍为 10-28 16:14 旧档）。 |

> **并发方提示（非本次改动）**：复查时发现同目录下 `check_payoff.py`（15:55）、`mood_probe.py`（15:57）
> 及其 `__pycache__/*.pyc`（15:55）mtime 为今日，属另一维护方在本次验证期间同时编辑该仓库。
> 这两个文件**不在 gate_runner 的读取路径内**（gate_runner 只加载 style/clarity/clarity_org 三个探针），
> 不影响本次三项结论；但说明 `F:/小说/platforms/_core/tools` 当前**有并发写入者**，
> 主控方若要把本报告当作该仓库的稳定基线快照，引用时应带上抓取时刻。
> （更正说明：本节初稿曾写「`find -maxdepth 2` 返回空」作为无改动证据，该证据无效 ——
> maxdepth 2 够不到深度 4 的 `tools/*.py`，不能证明未改动，已按实际写后复查结论替换。）

`F:/小说/platforms/qimao/harness/gates.json` 上下文（用于核对 coverage 计数是否自洽）：
`execution_mode=null`、`min_chars_for_density_metrics=1000`、`gates_total=9`，
九条闸门全为 A 级（无 B 级 → 本轮不可能出现 `blocked`），仅 `G8 因果显式度` 为 `genre_specific`
（不带 `--genre` 时 skipped，构成 coverage.skipped=1）。

---

## 命令 A — 短稿 stub_900.md

### 确切命令
```bash
cd "F:/小说/platforms/_core/tools" && PYTHONIOENCODING=utf-8 python -B gate_runner.py \
  --platform "F:/小说/platforms/qimao" --json "F:/小说/_audit_fixtures/stub_900.md"
```

### 退出码
`rc=0`　（stderr 空）

### 完整 JSON stdout（原样，未截断）
```json
[
 {
  "file": "stub_900.md",
  "platform": "qimao",
  "status": "advisory",
  "has_error": false,
  "undetermined": true,
  "coverage": {
   "judged": 1,
   "skipped": 1,
   "missing_metric": 0,
   "insufficient_sample": 7,
   "observed": 0,
   "gates_total": 9
  },
  "blocked_count": 0,
  "advisory_count": 0,
  "metrics": {
   "encoding": "utf-8",
   "visible_chars": 900,
   "paragraphs_per_1000": 33.3,
   "sent_len_median": 3.0,
   "dialog_ratio": 0.0,
   "causal_per_1000": 0.0,
   "narration_action_density": 333.33,
   "action_density": 333.33,
   "action_chain_max": 300,
   "number_per_1000": 0.0,
   "question_per_1000": 0.0,
   "multi_actor_sent_ratio": 0.0
  },
  "findings": [],
  "insufficient_findings": [
   {
    "id": "G3-a",
    "name": "句长中位数",
    "metric": "sent_len_median",
    "range": [
     11,
     27
    ],
    "level": "A",
    "note": "文本 900 字 < 1000，密度类指标不可靠",
    "status": "insufficient_sample",
    "actual": 3.0
   },
   {
    "id": "G3-b",
    "name": "对话占比",
    "metric": "dialog_ratio",
    "range": [
     0.05,
     0.46
    ],
    "level": "A",
    "note": "文本 900 字 < 1000，密度类指标不可靠",
    "status": "insufficient_sample",
    "actual": 0.0
   },
   {
    "id": "G3-c",
    "name": "连续短句链",
    "metric": "c_short_chain_max",
    "max": 7,
    "level": "A",
    "note": "文本 900 字 < 1000，密度类指标不可靠",
    "status": "insufficient_sample",
    "actual": 300
   },
   {
    "id": "G6-a",
    "name": "单章字数下限",
    "metric": "visible_chars",
    "min": 1500,
    "level": "A",
    "direction": "min_only",
    "note": "文本 900 字 < 1000，密度类指标不可靠",
    "status": "insufficient_sample",
    "actual": 900
   },
   {
    "id": "G9",
    "name": "叙述动作词密度",
    "metric": "narration_action_density",
    "max": 56,
    "level": "A",
    "note": "文本 900 字 < 1000，密度类指标不可靠",
    "source": "clarity_org_probe.py / internal calibration 2026-10-23",
    "genre_specific": false,
    "min": 5,
    "status": "insufficient_sample",
    "actual": 333.33
   },
   {
    "id": "G11",
    "name": "心理外化提示",
    "metric": "question_per_1000",
    "min": 1.5,
    "level": "A",
    "note": "文本 900 字 < 1000，密度类指标不可靠",
    "status": "insufficient_sample",
    "actual": 0.0
   },
   {
    "id": "G9-b",
    "name": "动作链长",
    "metric": "action_chain_max",
    "max": 7,
    "level": "A",
    "note": "文本 900 字 < 1000，密度类指标不可靠",
    "status": "insufficient_sample",
    "actual": 300
   }
  ]
 }
]
```

### 逐项判定

| 承诺字段 | 预期 | 实测 | 判定 |
|---|---|---|---|
| exit code | `rc=0` | `0` | **MATCH** |
| `status` | `"advisory"`（改前为 `pass`） | `"advisory"` | **MATCH** |
| `undetermined` | `true` | `true` | **MATCH** |
| `coverage.insufficient_sample` | `> 0` | `7` | **MATCH** |
| `insufficient_findings[]` | 被吞的 fail 原值保留 | 存在，7 条：`G3-a, G3-b, G3-c, G6-a, G9, G11, G9-b` | **MATCH** |
| `has_error`（隐含：非故障） | 不得为 true | `false` | **MATCH** |

coverage 自洽性：`judged 1 + skipped 1 + missing_metric 0 + insufficient_sample 7 = 9 = gates_total` ✅
（`judged=1` 是 G2-a 段落密度真判为 pass；`skipped=1` 是 G8 缺 `--genre`。）

`insufficient_findings` 每条都带 `id / name / metric / level / actual` 与阈值
（`range` 或 `min` / `max`）以及降级原因 `note: "文本 900 字 < 1000，密度类指标不可靠"` ——
原值（如 `sent_len_median=3.0`、`action_chain_max=300`、`narration_action_density=333.33`）
完整可查，**HAR-01「不再吞 fail」的修复成立**。

### 需要主控知道的语义细节（非失守，属契约本意）
- `advisory_count=0` 且 `findings=[]`：本行的 `advisory` **不是**由活着的 A 级 fail 触发，
  而是 `gate_runner.py:220-221` 的降权路径 —— `undet and status=='pass'` → 改判 `advisory`
  （注释：「没判全的行不可能是全绿」）。
  ⇒ 消费方（novel-workbench）**不能把 `status=advisory` 读成「有 N 条待改」**；
  要区分「有 A 级问题」与「没判全」，须同时看 `advisory_count` 与 `undetermined` 两个字段。
- `metrics.encoding="utf-8"`：编码正常，本行 `undetermined` 完全由短稿样本不足 + G8 skipped 驱动。

---

## 命令 B — 不存在文件

### 确切命令
```bash
cd "F:/小说/platforms/_core/tools" && PYTHONIOENCODING=utf-8 python -B gate_runner.py \
  --platform "F:/小说/platforms/qimao" --json "F:/小说/does_not_exist_ch99.md"
```

### 退出码
`rc=0`　（stderr 空 —— 契约要求：含 `has_error` 行仍返回 0，非阻断）

### 完整 JSON stdout（原样，未截断）
```json
[
 {
  "file": "does_not_exist_ch99.md",
  "platform": "qimao",
  "status": null,
  "has_error": true,
  "error": "not found",
  "blocked_count": 0,
  "advisory_count": 0,
  "metrics": {},
  "findings": []
 }
]
```

### 逐项判定

| 承诺字段 | 预期 | 实测 | 判定 |
|---|---|---|---|
| exit code | `rc=0` | `0` | **MATCH** |
| `status` | `null` | `null` | **MATCH** |
| `has_error` | `true` | `true` | **MATCH** |
| `error` | `"not found"` | `"not found"` | **MATCH** |

### 需要主控知道的形状细节（消费方风险点）
故障行**没有** `undetermined`、**没有** `coverage`、**没有** `insufficient_findings` 三个键，
只到 `file / platform / status / has_error / error / blocked_count / advisory_count / metrics / findings`。
这承诺本身没违反（承诺只涉及三字段），但意味着：
- 行 schema 不统一，读 `row["undetermined"]` / `row["coverage"]` 会 `KeyError`；
  必须用 `row.get(...)`，或先判 `row.get("has_error")` 短路。
- 判定顺序须按 `gate_runner.py` 文件头契约走：**`has_error`（故障）→ `undetermined`（不可靠）→ `status`**，
  否则故障行会被 `row.get('undetermined')` 的 `None` 误判为「可靠地未通过/通过」。
- 若 novel-workbench 侧有 JSON Schema / dataclass 严格校验，建议把 `undetermined` 与 `coverage`
  在故障行也补成显式 `false` / 零值 —— 属**新增建议**，不是本次未兑现项。

---

## 控制组 — gbk_sample.md（验证 `has_error` 与 `undetermined` 未被合并）

### 确切命令
```bash
cd "F:/小说/platforms/_core/tools" && PYTHONIOENCODING=utf-8 python -B gate_runner.py \
  --platform "F:/小说/platforms/qimao" --json "F:/小说/_audit_fixtures/gbk_sample.md"
```

### 退出码
`rc=0`　（stderr 空）

### 完整 JSON stdout（原样，未截断）
```json
[
 {
  "file": "gbk_sample.md",
  "platform": "qimao",
  "status": "advisory",
  "has_error": false,
  "undetermined": true,
  "coverage": {
   "judged": 3,
   "skipped": 1,
   "missing_metric": 0,
   "insufficient_sample": 5,
   "observed": 0,
   "gates_total": 9
  },
  "blocked_count": 0,
  "advisory_count": 0,
  "metrics": {
   "encoding": "gb18030",
   "visible_chars": 680,
   "paragraphs_per_1000": 58.8,
   "sent_len_median": 17.0,
   "dialog_ratio": 0.0,
   "causal_per_1000": 0.0,
   "narration_action_density": 58.82,
   "action_density": 58.82,
   "action_chain_max": 40,
   "number_per_1000": 0.0,
   "question_per_1000": 0.0,
   "multi_actor_sent_ratio": 0.0
  },
  "findings": [],
  "insufficient_findings": [
   {
    "id": "G3-b",
    "name": "对话占比",
    "metric": "dialog_ratio",
    "range": [
     0.05,
     0.46
    ],
    "level": "A",
    "note": "文本 680 字 < 1000，密度类指标不可靠",
    "status": "insufficient_sample",
    "actual": 0.0
   },
   {
    "id": "G6-a",
    "name": "单章字数下限",
    "metric": "visible_chars",
    "min": 1500,
    "level": "A",
    "direction": "min_only",
    "note": "文本 680 字 < 1000，密度类指标不可靠",
    "status": "insufficient_sample",
    "actual": 680
   },
   {
    "id": "G9",
    "name": "叙述动作词密度",
    "metric": "narration_action_density",
    "max": 56,
    "level": "A",
    "note": "文本 680 字 < 1000，密度类指标不可靠",
    "source": "clarity_org_probe.py / internal calibration 2026-10-23",
    "genre_specific": false,
    "min": 5,
    "status": "insufficient_sample",
    "actual": 58.82
   },
   {
    "id": "G11",
    "name": "心理外化提示",
    "metric": "question_per_1000",
    "min": 1.5,
    "level": "A",
    "note": "文本 680 字 < 1000，密度类指标不可靠",
    "status": "insufficient_sample",
    "actual": 0.0
   },
   {
    "id": "G9-b",
    "name": "动作链长",
    "metric": "action_chain_max",
    "max": 7,
    "level": "A",
    "note": "文本 680 字 < 1000，密度类指标不可靠",
    "status": "insufficient_sample",
    "actual": 40
   }
  ]
 }
]
```

### 逐项判定

| 检查 | 预期 | 实测 | 判定 |
|---|---|---|---|
| exit code | `rc=0` | `0` | **MATCH** |
| `status` | `"advisory"` | `"advisory"` | **MATCH** |
| `has_error` | `false`（编码降级 ≠ 故障） | `false` | **MATCH** |
| `undetermined` | `true` | `true` | **MATCH** |
| `metrics.encoding` | `gb18030`，且在 metrics 首位 | `"gb18030"`（首键） | **MATCH** |
| `coverage.insufficient_sample` | `> 0` | `5`（`G3-b, G6-a, G9, G11, G9-b`） | **MATCH** |

coverage 自洽：`judged 3 + skipped 1 + missing_metric 0 + insufficient_sample 5 = 9` ✅

**分离性结论：两个字段是独立轴，未被合并。**
- 本行：`has_error=false` 而 `undetermined=true` —— 指标齐全、非故障，但判定不完整
  （短稿 + G8 skipped + 编码降级三条原因）。
- 命令 B：`has_error=true` 而 `undetermined` 键根本不出现 —— 故障没有顺带置 `undetermined=true`。
两向都验到了，`gate_runner.py:215-219` 的实现与契约一致。

---

## 补充验证（本次主动加测）：同批混合好文件与缺失文件

```bash
cd "F:/小说/platforms/_core/tools" && PYTHONIOENCODING=utf-8 python -B gate_runner.py \
  --platform "F:/小说/platforms/qimao" --json \
  "F:/小说/_audit_fixtures/stub_900.md" "F:/小说/does_not_exist_ch99.md"
```
`rc=0`，逐行结果：

| file | status | has_error | undetermined | error |
|---|---|---|---|---|
| `stub_900.md` | `"advisory"` | `false` | `true` | — |
| `does_not_exist_ch99.md` | `null` | `true` | *（键不存在）* | `"not found"` |

意义：故障行只污染自己那一行，不改变同批正常行的判定，也不要把整批退出码抬到 1。
`HAR-03`（按文件捕获、不整轮崩溃）在批量场景下同样成立。

---

## 兑现情况总表

| # | 承诺 | 结果 |
|---|---|---|
| 1 | 短稿不再静默 `pass` → `advisory` | ✅ 兑现 |
| 2 | 短稿 `undetermined=true` | ✅ 兑现 |
| 3 | `coverage.insufficient_sample > 0` | ✅ 兑现（=7） |
| 4 | 被吞的 fail 保留进 `insufficient_findings[]` | ✅ 兑现（7 条，含原值 + note） |
| 5 | 缺失文件 `rc=0` | ✅ 兑现 |
| 6 | 缺失文件 `status=null` | ✅ 兑现 |
| 7 | 缺失文件 `has_error=true` + `error="not found"` | ✅ 兑现 |
| 8 | `has_error` 与 `undetermined` 保持分离 | ✅ 兑现（双向验证） |
| 9 | 控制组 gbk 行为与 10-28 记录一致 | ✅ 一致（无回归） |

**没有任何一项承诺未被兑现。** 三项待留意（均非违约）：
(a) `status=advisory` 有两种来源，须靠 `advisory_count` + `undetermined` 区分；
(b) 故障行缺少 `undetermined` / `coverage` 键，严格 schema 消费方需 `.get()`；
(c) qimao 当前九条闸门全 A 级，本轮无法验证 `blocked` 与 `rc=1` 路径 —— 若要闭合 B 级阻断链，
需另配一条 B 级闸门样本或指向含 B 级闸门的平台再跑一次。

---

> 补跑由 Novel Platform 主控方执行；原始 stdout 逐字来自 `%TEMP%\gate{A,B,C,D}.json`，未手工转录。
> 前置记录：`.task/20261028-gate_verification-results.md`（命令 2、4 当时因模型超时未跑）
