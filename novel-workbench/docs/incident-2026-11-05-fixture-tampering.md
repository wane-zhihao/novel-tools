# 仓库文件被就地篡改事件（2026-11-05 立案）

## 结论

26 个 git 跟踪文件的工作区内容被外部进程**就地覆盖**为无关内容（第三方库源码片段、NUL 补尾、一个原始 x86-64 PE 机器码文件），同时把 mtime/ctime 回拨到 2026-10-23～25 的夹具生成时间。
因此 `git status` / `git diff` / `git update-index --really-refresh` 全部报 **clean**——git 的 stat 缓存命中 `ce_match_stat()`，根本不会去读文件字节。

**这不是 git 的缺陷，也不是本次改动引入的**：HEAD 与 index 的 blob 全程完好，`git fsck` 干净。
**取证时点**：2026-11-05（HEAD = 694d462，分支 `t1-body-integrity`）。
**损害窗口**：内容可证实在 2026-11-01 18:28 仍然完好（当时测试临时目录里有 30 份副本可对照），2026-11-04/05 发现。

## 判别依据

| 观察 | 结论 |
|---|---|
| 26/26 文件的磁盘长度 == 原始长度（含两个历史 CRLF 文件：blob + LF 数 == 磁盘长度） | 长度被刻意保留——这正是让 git stat 缓存失明的原因 |
| 10 个文件尾部是 NUL 填充（36～3298 字节） | 外部把别的文件窗口拷进同尺寸零填充缓冲区 |
| 外来内容都从句子/行中间开始 | 读取时带偏移，不是从文件头拷贝 |
| 至少 7 个不同来源池：npm `diff`、`marked`、`three.js`、yoga-layout/react-native、Meta/jest 版权头、`bun:ffi`、裸 PE 字节 | 外部字节池，非随机损坏 |
| 两个同尺寸文件拿到**逐字节相同**的垃圾 | 脚本化填充，非位翻转 |
| mtime/ctime/birthtime 全是夹具生成期时间，与 git 缓存 stat 精确到 100ns 一致；nlink=1 | 原地写 + 回拨时间戳，未删除重建 |
| `ls-files -v` 全为大写 H；无 `core.fsmonitor`/`untrackedCache`/`trustmtime`；无 `.gitattributes`，`check-attr -a` 无输出；`cat-file -p HEAD:…` 输出的是真 JSON 不是 LFS pointer | 排除 skip-worktree / assume-unchanged / fsmonitor / lfs / smudge 伪造 |
| `git fsck` 仅 7 个 dangling stash（各自带着完好的 state.json blob） | 对象库完好 |
| 仓库内所有写入都是 temp + renameSync；全仓 grep 无 `writeSync`/`r+`/`os.utime`/`SetFileTime`/`allocUnsafe` | 排除仓库自身工具链 |

**写入者未定位。** 两个主要假设（缺证据，需要 11-02～11-04 的 shell history / agent transcript 才能定案）：

- H1：某个「绕过 stat 的篡改/损坏 harness」直接跑在**线上仓库**而非临时副本上——读目标尺寸 → 用无关字节池填同尺寸零缓冲 → 原地写 → 回拨 mtime。这正是本仓库自己的指纹测试声明必须能识别的攻击类别（`sync.test.mjs` 「同 size 同 mtime、内容不同」用例）。
- H2：外部备份/同步/去重/编辑器工具的缓冲区复用缺陷 + 时间戳继承。

## 处置

1. 26 份垃圾内容已留证在本目录 `corruption-evidence-2026-11-05/`（原样保存，含 PE 字节）。**故意不入库**：攻击者内容不该进版本库。
2. 已用 `git cat-file blob :<path>` 取出完好 index blob，逐字节写回工作区并校验。`git restore` 与 `git checkout-index -f` 都被 stat 缓存**无声跳过**（不报错、也不写文件）——这本身就是本事件最危险的一面。
3. 恢复后：JSON 全部可解析、正文/大纲首行恢复为中文散文、`shelf.js` 首行恢复为 `import { api } …`；`npm test` → 146/146 通过。
4. `_fixtures/chapter_e2e/_pipeline/state.json` 与 `正文/第002章…md` 等两个历史 CRLF 文件随后用 `git checkout-index -f` 走 smudge 写回 CRLF，与 `core.autocrlf=true` 下的 blob 保持一致。

## 受影响文件（26）与内容指纹

`sha256` 前 16 位；`garbage` = 篡改后磁盘内容，`head` = 恢复源（index blob）。

```
_fixtures/chapter_e2e/_pipeline/snapshots/C1_before_N14_r2/第001章_榜上换了名.md  garbage=66af3533bb1d15c9  head=54aba4ecaf9c151d
_fixtures/chapter_e2e/_pipeline/snapshots/C1_before_N14_r3/第001章_榜上换了名.md  garbage=f58af8e74e4f46b0  head=54aba4ecaf9c151d
_fixtures/chapter_e2e/_pipeline/snapshots/C2_before_N14_r2/第002章_老会计的茶缸.md  garbage=2226d9545d1bf6f2  head=fa55493c802f37fd
_fixtures/chapter_e2e/_pipeline/state.json                                          garbage=66cfff1955a29466  head=a11a5f421616fa8b
_fixtures/chapter_e2e/大纲/卷纲_第一卷.md                                             garbage=e3cfd4856dae3565  head=96631941dce103c6
_fixtures/chapter_e2e/大纲/细纲_第001章.md                                            garbage=49dfa301ce2d0628  head=231701fb87cf975d
_fixtures/chapter_e2e/大纲/细纲_第002章.md                                            garbage=2fb50dceddf178bc  head=ac73623827c0c5fd
_fixtures/chapter_e2e/正文/第001章_榜上换了名.md                                      garbage=26e0908c22c919b4  head=54aba4ecaf9c151d
_fixtures/chapter_e2e/正文/第002章_老会计的茶缸.md                                    garbage=436b662dd3678c28  head=fa55493c802f37fd
_fixtures/chapter_e2e/追踪/_tracking-state.json                                      garbage=36e4055e2cdff2d2  head=74e4e21b31914292
_fixtures/chapter_e2e/追踪/上下文.md                                                  garbage=c8911cc0e373806e  head=866e69efb9fba419
_fixtures/chapter_loop/大纲/卷纲_第一卷.md                                            garbage=ef114fd84e1656c2  head=96631941dce103c6
_fixtures/chapter_loop/大纲/目录_前12章.md                                            garbage=2d437a0fa78bfa98  head=9e45487e17f787df
_fixtures/chapter_loop/大纲/细纲_第001章.md                                           garbage=d98162626b16da9f  head=231701fb87cf975d
_fixtures/chapter_loop/追踪/_tracking-state.json                                     garbage=4077faa54f72794e  head=6599ca3d5df3f29d
_fixtures/chapter_loop/追踪/上下文.md                                                 garbage=bed4f4aea8ba6ae0  head=7172b9c39bf1cd7b
_fixtures/workbench_e2e/_pipeline/snapshots/N20f_round2/第001章_榜上换了名.md          garbage=fcd9eeffa70af6e2  head=54aba4ecaf9c151d
_fixtures/workbench_e2e/大纲/卷纲_第一卷.md                                           garbage=a964539b2af1fc38  head=96631941dce103c6
_fixtures/workbench_e2e/大纲/目录_前12章.md                                           garbage=974dc6e4cac2fc5f  head=9e45487e17f787df
_fixtures/workbench_e2e/大纲/细纲_第001章.md                                          garbage=49dfa301ce2d0628  head=231701fb87cf975d
_fixtures/workbench_e2e/追踪/_tracking-state.json                                    garbage=82d84bfb24af81e0  head=6599ca3d5df3f29d
_fixtures/workbench_e2e/追踪/上下文.md                                                garbage=7b91b5aed2bae73e  head=7172b9c39bf1cd7b
_phase0/book2/大纲/细纲_第002章.md                                                   garbage=fe4e87ce746ec636  head=ac73623827c0c5fd
_phase0/book2/追踪/_tracking-state.json                                              garbage=19e21f174d92eb69  head=6599ca3d5df3f29d
_phase0/e2e/大纲/细纲_第001章.md                                                     garbage=eb83eeaa31ff9fda  head=19983d6ea848720e
novel-workbench/web/views/shelf.js                                                   garbage=3d7f711b08922411  head=6115c984eb854dd6
```

## 长期结论（写给以后的人）

- **判断仓库完整性要靠内容哈希，不能靠 `git status` 或 stat。** 本事件是活教材：stat-only 检查 100% 漏掉了 26 个被改的文件。T-1 的正文完整性工作正是把「按内容哈希核对」变成产品行为。
- 怀疑被篡改时，`git restore` / `git checkout` 可能是**静默无操作**；用 `git cat-file blob :<path>` 取字节再自写，并做读回校验。
- 恢复后必须重新同步（DB 指纹按内容缓存，mtime 变了不会自动刷新）。
