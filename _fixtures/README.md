# 回归夹具

这三本书都是《联试之书》的副本，**只用于验证流水线状态机**，不是投稿稿，
不要拿去投给编辑。

| 夹具 | 停在 | 验证目标 |
|---|---|---|
| `chapter_e2e` | `C3.N09`（第 2 章已封板，第 3 章待写） | N10 细纲闸门 → N11 上下文组装 → N12 → N13 平台闸门 → N16 原子封板；N16 事务缺失回退到 N14；`words_total` 回写 |
| `chapter_loop` | `C1.N15`，`blocked_reason` 已置位 | N15 block → N14 定点改写两轮 → 第三次停机；`fix_rounds` 计数 |
| `workbench_e2e` | `__done__`，`投稿包/` 四文件齐全 | N17→N18→N19→N20→N21 通过路径；N20 block→N20f 两轮回跳→第三次停机 |

## 复跑方式

```bash
PY="C:\Users\Wane-\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe"
AUT="F:\WorkBuddy AI\Novel Platform\novel-workbench\scripts\autopilot.py"
"$PY" "$AUT" status --book "F:\WorkBuddy AI\Novel Platform\_fixtures\chapter_e2e"
"$PY" "$AUT" next   --book "F:\WorkBuddy AI\Novel Platform\_fixtures\chapter_e2e"
```

要回到章节循环起点：把 `_pipeline/state.json` 的 `cursor` 改为 `C2.N10`、
`current_chapter` 改为 `1`、清空 `nodes` / `fix_rounds` / `blocked_reason`，
并删掉 `_pipeline/.step_token`。第 2 章正文与追踪事务仍保留，可反复封板。

## 注意

- `chapter_e2e` 的 `findings_C2.json` 带 `resolved_at`，是封板后的历史记录；
  Dashboard 会显示「已封板」。
- 夹具里的 `正文/` 是测试文本，其中第 2 章为了跑通闸门复制了第 1 章正文，
  不代表真实写作质量。
- `_pipeline/workbench.json` 由 `autopilot.py status/next/init` 自动生成，
  换机器后会重写成当前机器的绝对路径。
- `relay.md` 的触发（第 3 章封板时 `chapters_committed % 3 == 0`）已用 storyctl 桩验证：
  状态机走真实代码，仅封板命令被替换；确认 `relay.md` 写入当前 cursor、字数与续跑命令。
- `autopilot.py` 启动时把 stdout 强制为 UTF-8；宿主按 UTF-8 读取，不要二次转码。
- N21 现在对“字数 < 20000 / N20 非 pass / 机械 blocking > 0”返回 `NOT_SUBMITTABLE` 并阻断；
  `workbench_e2e` 是修复前的历史完成态，重新跑 N21 会变为 blocked，不再是可投样例。
