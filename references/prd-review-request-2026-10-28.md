# PRD 审查申请记录（2026-10-28）

## 已发送对象
- Novel tools 会话（uds:...\cc-msg-87b29bf574ec2e9b0bfebeb08c40b3a4）
- 发送时间：2026-10-28 03:50+
- 状态：SendMessage 调用失败（model timeout），待重试

## PRD 落盘位置
`F:\WorkBuddy AI\Novel Platform\references\prd-workbench-1.0.md`

## 关键接口契约回顾（需确认）

### gate_runner/fact_gate 语义
| 字段 | 取值范围 | 解读规则 |
|---|---|---|
| `rc` | 0 / 1 | 0=非阻断, 1=blocked → **不看 rc 做判定** |
| `status` | pass/advisory/blocked/metrics_only (gate_runner)<br>pass/advisory/blocked/unverified (fact_gate) | **唯一判定依据** |
| `has_error` | true/false | true → 工具失败 → 阻断 |
| `undetermined` | true/false | true → 判定不可靠 → 提示但不阻断 |
| `encoding` | utf-8/gb18030/etc | !=utf-8 → undetermined=true |

### A-J 十条接口清单
A: storyctl.py (chapter check/draft/commit)  
B: tracking_commit.py (draft/init)  
C: gate_runner.py (--platform --genre --json)  
D: check-outline-contract.js (--json)  
E: check-ai-patterns.js  
F: check-degeneration.js  
G: style_probe.py (dynamic import, probe(path))  
H: clarity_org_probe.py (registered but unused)  
I: build_writer_prompt.py (registered but unused)  
J: fact_gate.py (<body_dir> --config --json)  

### 不承诺改动范围
- CLI 参数顺序 ✓ 不改
- 退出码语义 ✓ 保持 {0,1}
- JSON 顶层键 ✓ status/executed/not_executed_reason/assertion_stats 固定
- 函数签名 ✓ style_probe.probe(path) 不变
- 存储格式 ✓ fact_gate 台账 JSON schema 兼容

## 需要答复的时间窗口
- **简单确认（yes/no）**：2 小时内
- **详细意见（diff/修改建议）**：今天结束前（10-28 23:59+）

## 安全事件备案
**fact_gate 集成写入者身份不明**
- 写入时间：2026-10-28 02:19+
- 涉及文件：novel-workbench/scripts/commit_chapter.py、package.json、server/tests/fact-gate.test.mjs
- 影响：代码本身正确但溯源缺失
- 状态：留档待查（不影响功能）

## 后续行动
1. 收到 Review 反馈后，合并接口改动
2. 启动 TDD 执行（TDD-001 ~ TDD-012）
3. 修复完成后 tag v1.0-RC1

---

> 此文件由 Novel Platform 主控方生成，供 Novel tools 团队参考。
> 如需讨论请直接回复该会话或发起新对话。
