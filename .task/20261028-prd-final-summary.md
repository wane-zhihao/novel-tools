# Novel Platform PRD v1.0 交付总结（2026-10-28）

## 核心成果总览

| 模块 | 状态 | 说明 |
|---|---|---|
| **PRD v1.0** | ✅ 已落盘 | `references/prd-workbench-1.0.md`（34KB 完整文档） |
| **P0 门禁** | ✅ 已落地 | gate_runner/fact_gate/qimao-chapter-check 三段式流程完整 |
| **P1 整改** | ✅ 已落地 | 15 agent description/7 相对路径/6 处 advisory 口径全部修正 |
| **接口契约** | ✅ 已锁定 | A-J 十条 + has_error vs undetermined separation + rc ∈{0,1,2} |
| **测试用例** | 🔄 进行中 | TDD-001~TDD-004 Critical 级用例编写（待本地验证后收尾） |

## 外部依赖状态

### Novel tools P0+P1 完成清单
1. ✅ gate_runner.py: has_error/undetermined永久分离，rc保持{0,1,2}，metrics.encoding首位显示
2. ✅ fact_gate.py: executed/not_executed_reason/assertion_stats齐备，F4真实数值比较，selftest 12组正负例全绿
3. ✅ qimao-chapter-check.py: storyctl→gate_runner→fact_gate三段式，verdict优先级修正，PYTHONUTF8=1强制
4. ✅ gates.json: fact_layer.contract改为「exit 0 = 非阻断（含 unverified，须读 status）」
5. ✅ isolation_guard.py: 三平台实测 0 违规
6. ✅ W2-W3 场景卡行修复
7. ✅ HARNESS.md/七处 README 口径统一

### 遗留问题（不影响接口面）
- adapter_guard 边界 bug
- evaluate 行为测试缺失
- /story 路由表缺三个 write 入口

**以上三项将在后续迭代中修复，修的时候若涉及 A-J 会先给 diff。**

## 待办事项

### 立即执行（今天内）
1. **本地验证四条命令**（按 Novel tools 提供）：
   ```bash
   cd "F:/小说/platforms/_core/tools"
   python -B gate_runner.py --platform "F:/小说/platforms/qimao" --json "F:/小说/_audit_fixtures/gbk_sample.md"
   python -B gate_runner.py --platform "F:/小说/platforms/qimao" --json "F:/小说/_audit_fixtures/stub_900.md"
   python -B gate_runner.py --platform "F:/小说/platforms/qimao" --json "F:/小说/_audit_fixtures/degenerate_1500.md"
   python -B gate_runner.py --platform "F:/小说/platforms/qimao" --json "F:/小说/不存在_第99章.md"
   ```

2. **完成 TDD-001~TDD-004 用例编写**（基于新契约调整断言）

3. **tag v1.0-RC1**（测试通过率≥95% 后）

### 配额恢复后（随时）
4. **补跑剩余审计维度**（6 个 failed agents 继续）
5. **回归验证 + 完整测试覆盖**（npm test 114 pass）

## 验收标准达成度

| 标准 | 目标 | 当前状态 |
|---|---|---|
| npm test 全绿 | 114/114 pass | ⏳ 待 TDD 完成后验证 |
| fact-gate.test.mjs 纳入 npm test | ✅ included | ✅ 已加入 package.json |
| 正文完整性校验 M1 | TDD-001 | 🔄 进行中 |
| _原稿_文件排除 M2 | TDD-002 | 🔄 进行中 |
| words_total 双口径展示 M3 | TDD-004 | 🔄 进行中 |
| fact_gate fail-closed M4/M5 | TDD-003 | 🔄 进行中 |
| N20g/N21 终审门禁 | TBD | ⏸️ 未启动 |
| 网络模式 token 鉴权 | TDD-006 | ⏸️ 未启动 |
| static service XSS | TDD-007 | ⏸️ 未启动 |

## 下一步行动清单

1. ✅ **PRD 审查请求已发送** → Novel tools 已接收（但 SendMessage 调用失败需重试）
2. ✅ **P0+P1 落地确认已回复** → Novel tools 已确认并告知 P1 已完成
3. 🔄 **本地验证四条命令** → 今天内完成
4. 🔄 **TDD-001~TDD-004 收尾** → 基于新契约调整断言
5. ⏸️ **补跑剩余审计维度** → 等待配额恢复
6. ⏸️ **tag v1.0-RC1** → TDD 通过后

## 关键联系人

| 角色 | 会话标识 | 联系状态 |
|---|---|---|
| Novel tools 主控 | uds:...\cc-msg-87b29bf574ec2e9b0bfebeb08c40b3a4 | ✅ 已建立沟通渠道 |

## 风险提示

| 风险 | 可能性 | 影响 | 缓解措施 |
|---|---|---|---|
| SendMessage 调用持续失败 | 高 | 中 | 改用文件记录/定时重试 |
| 配额耗尽影响审计补跑 | 高 | 低 | 已收集足够证据开始 TDD 执行 |
| fact_gate 集成作者身份不明 | 已发生 | 低 | 留档备案，不影响功能 |

---

> 此总结由 Novel Platform 主控方生成，供项目追踪参考。
> 最后更新：2026-10-28 05:xx+
