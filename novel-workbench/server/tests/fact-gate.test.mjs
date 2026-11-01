import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { detectPython } from "../settings.mjs";

// 事实层闸门（platforms/_core/tools/fact_gate.py）的契约测试。
//
// 为什么单独测它：gate_runner 只测文风，事实错误靠这个闸门拦。
// 闸门失效时不能有任何提示——所以必须有一条「喂坏样本必须被抓」的用例，
// 否则它退化成永远返回 PASS 的摆设也没人发现。

const FACT_GATE = process.env.WORKBENCH_FACT_GATE
  || "F:/小说/platforms/_core/tools/fact_gate.py";

// 这条测试依赖**外部**工具链（platforms/_core/tools/fact_gate.py），不在本仓库内。
//   · 工具不存在 → 整组跳过（skip，不是 fail）：开源用户没有 F:\小说 是常态，
//     不能让他们的 npm test 恒红；
//   · 工具存在但行为不符 → 必须失败，这正是它存在的意义。
// 跳过与否会打印在测试输出里，不允许静默。
const TOOL_PRESENT = existsSync(FACT_GATE);
const SKIP_REASON = `事实闸门不在本仓库内（${FACT_GATE}）；设置 WORKBENCH_FACT_GATE 可启用契约测试`;

function runGate(args) {
  try {
    const out = execFileSync(detectPython(), [FACT_GATE, ...args], {
      encoding: "utf8",
      timeout: 60_000,
      env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
    });
    return { code: 0, out };
  } catch (e) {
    return { code: e.status ?? -1, out: `${e.stdout ?? ""}${e.stderr ?? ""}` };
  }
}

test("事实闸门：自检必须抓到坏样本", (t) => {
  if (!detectPython()) return t.skip("本机没有可执行的 Python");
  if (!TOOL_PRESENT) return t.skip(SKIP_REASON);
  const r = runGate(["--selftest"]);
  assert.equal(r.code, 0, r.out);
  assert.match(r.out, /SELFTEST PASS/);
});

test("事实闸门：字数断言不符必须 BLOCK", (t) => {
  if (!detectPython()) return t.skip("本机没有可执行的 Python");
  if (!TOOL_PRESENT) return t.skip(SKIP_REASON);
  const dir = mkdtempSync(join(tmpdir(), "fg-"));
  try {
    const body = join(dir, "正文");
    mkdirSync(body);
    // 不合格 = 3 字，这里故意写成 4 字
    writeFileSync(join(body, "第001章_x.md"), "评定结论四个字：不合格。\n", "utf8");
    const r = runGate([body, "--allow-no-config"]);
    assert.equal(r.code, 1, r.out);
    assert.match(r.out, /F1/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("事实闸门：日期断言被改写必须 BLOCK", (t) => {
  if (!detectPython()) return t.skip("本机没有可执行的 Python");
  if (!TOOL_PRESENT) return t.skip(SKIP_REASON);
  const dir = mkdtempSync(join(tmpdir(), "fg-"));
  try {
    const body = join(dir, "正文");
    mkdirSync(body);
    writeFileSync(join(body, "第001章_x.md"), "昨晚他打来电话。\n", "utf8");
    const cfg = join(dir, "cfg.json");
    writeFileSync(cfg, JSON.stringify({
      base_date: "2026-10-16",
      chapter_offsets: { "001": 0 },
      // 断言要求 昨晚 = 10/15，但正文已被改成「昨晚」以外的说法就会触发缺失阻断
      date_assertions: [
        { chapter: "001", token: "今晚", expect: "2026-10-16", why: "测试：正文没有这个词" },
      ],
    }), "utf8");
    const r = runGate([body, "--config", cfg]);
    assert.equal(r.code, 1, r.out);
    assert.match(r.out, /F3/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("事实闸门：无台账时必须报 unverified 而不是 pass", (t) => {
  if (!detectPython()) return t.skip("本机没有可执行的 Python");
  if (!TOOL_PRESENT) return t.skip(SKIP_REASON);
  const dir = mkdtempSync(join(tmpdir(), "fg-"));
  try {
    const body = join(dir, "正文");
    mkdirSync(body);
    writeFileSync(join(body, "第001章_x.md"), "他说了一句话。\n", "utf8");
    const r = runGate([body, "--json"]);
    assert.equal(r.code, 0, r.out);
    const payload = JSON.parse(r.out);
    // 核心断言：没检查 ≠ 检查通过
    assert.equal(payload.status, "unverified", "无台账时状态必须是 unverified");
    assert.notEqual(payload.status, "pass");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
