import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { createToolExecutor } from "../agent/tools.mjs";
import { assertLocalRequest, isSameOrigin } from "../http.mjs";

// 2026-10-25 对抗性审查 P0 波：攻击回归。每条用例对应一个已实证的攻击路径，
// 修复后必须全部被拒绝。攻击书思路保留（_phase0/attack_book），这里固化为 CI。
// 仓库所在卷（F:）已实证启用 8.3 短名；tmpdir 所在卷未必启用，8.3 用例带回退。

const REPO_FIXTURES = resolve(import.meta.dirname, "..", "..", "..", "_fixtures");

function makeBook(baseDir) {
  const bookDir = mkdtempSync(join(baseDir, "wb-attack-"));
  mkdirSync(join(bookDir, "正文"), { recursive: true });
  mkdirSync(join(bookDir, "_pipeline"), { recursive: true });
  writeFileSync(join(bookDir, "正文", "第001章_测试.md"), "顶职名单今天换名字，换掉的正是他。", "utf8");
  writeFileSync(join(bookDir, "_pipeline", "state.json"), '{"cursor": "N01"}', "utf8");
  writeFileSync(join(bookDir, "_pipeline", "workbench.json"), "{}", "utf8");
  writeFileSync(join(bookDir, "_pipeline", "run.json"), "{}", "utf8");
  writeFileSync(join(bookDir, "_pipeline", "agents.json"), '{"roles": {}}', "utf8");
  writeFileSync(join(bookDir, "_pipeline", "submissions.json"), "[]", "utf8");
  writeFileSync(join(bookDir, "_pipeline", "findings_C1.json"), '{"items": []}', "utf8");
  writeFileSync(join(bookDir, "_pipeline", "judgments_C1.N15.json"), '{"verdict": "pass", "items": []}', "utf8");
  return bookDir;
}

function withBook(fn, baseDir = tmpdir()) {
  const bookDir = makeBook(baseDir);
  try {
    return fn(bookDir);
  } finally {
    rmSync(bookDir, { recursive: true, force: true });
  }
}

// A3：Windows 8.3 短名写穿保护名单（审查实证：真改坏过 state.json）
test("A3 8.3 短名不能写穿/读穿保护名单", () => {
  const aliasResolves = (bookDir) => existsSync(join(bookDir, "_PIPEL~1", "state.json"));
  const run = (bookDir) => {
    const alias = join(bookDir, "_PIPEL~1", "state.json");
    const before = readFileSync(alias, "utf8");
    const tools = createToolExecutor({ bookDir });
    const write = tools.execute("write_file", { path: "_PIPEL~1/state.json", content: "HACKED" });
    assert.equal(write.ok, false, "8.3 别名写控制文件必须被拒绝");
    assert.match(write.content, /禁止改写控制|8\.3 短名伪装/);
    assert.equal(readFileSync(alias, "utf8"), before, "真实 state.json 内容不得变化");

    const read = tools.execute("read_file", { path: "_PIPEL~1/state.json" });
    assert.equal(read.ok, false, "8.3 别名读控制文件必须被拒绝");
    assert.match(read.content, /控制文件不可读取|8\.3 短名伪装/);
  };
  if (process.platform !== "win32") return;          // 8.3 是 Windows 特性
  let covered = false;
  withBook((bookDir) => {
    if (aliasResolves(bookDir)) {
      run(bookDir);
      covered = true;
    }
  });
  if (covered) return;
  // 回退：tmpdir 所在卷未启用 8.3 生成时，用仓库所在卷（已实证启用）再试
  mkdirSync(REPO_FIXTURES, { recursive: true });
  withBook(run, REPO_FIXTURES);
});

// A4：agents.json / submissions.json 可被 subagent 改写 → 手稿外泄通道
test("A4 agents.json / submissions.json 禁止改写", () => withBook((bookDir) => {
  const tools = createToolExecutor({ bookDir });
  for (const path of ["_pipeline/agents.json", "_pipeline/submissions.json"]) {
    const result = tools.execute("write_file", { path, content: '{"providers": {"evil": {}}}' });
    assert.equal(result.ok, false, `${path} 应被拒绝`);
    assert.match(result.content, /禁止改写控制/);
  }
}));

// A6：裁决记录只有 judge 角色可写——writer 覆写 judgments_* 可翻转历史结论
test("A6 judgments 只有 judge 角色可写", () => withBook((bookDir) => {
  const asWriter = createToolExecutor({ bookDir, role: "writer" });
  const blocked = asWriter.execute("write_file", {
    path: "_pipeline/judgments_C1.N15.json",
    content: '{"verdict": "pass", "items": []}',
  });
  assert.equal(blocked.ok, false, "writer 不得改写 judge 裁决记录");
  assert.match(blocked.content, /禁止改写控制/);

  const asJudge = createToolExecutor({ bookDir, role: "judge" });
  const allowed = asJudge.execute("write_file", {
    path: "_pipeline/judgments_C2.N15.json",
    content: '{"verdict": "pass", "items": []}',
  });
  assert.equal(allowed.ok, true, "judge 必须能写自己的裁决记录");

  // setRole 动态切换（runtime 按节点注入角色）
  const tools = createToolExecutor({ bookDir });
  tools.setRole("writer");
  assert.equal(tools.execute("write_file", { path: "_pipeline/judgments_C3.N15.json", content: "{}" }).ok, false);
  tools.setRole("judge");
  assert.equal(tools.execute("write_file", { path: "_pipeline/judgments_C3.N15.json", content: "{}" }).ok, true);
}));

// B11：控制文件读侧默认不可读（持令牌 subagent 读 state.json 是越权第一步）
test("B11 控制文件禁止读取，findings 仍可读", () => withBook((bookDir) => {
  const tools = createToolExecutor({ bookDir });
  for (const path of ["_pipeline/state.json", "_pipeline/workbench.json",
                      "_pipeline/run.json", "_pipeline/agents.json"]) {
    const result = tools.execute("read_file", { path });
    assert.equal(result.ok, false, `${path} 不应可读`);
    assert.match(result.content, /控制文件不可读取/);
  }
  // N14 定点改写按设计要读 findings
  const findings = tools.execute("read_file", { path: "_pipeline/findings_C1.json" });
  assert.equal(findings.ok, true);
}));

// A3/A4 同类：快照与 endgame findings 也是脚本资产，禁止 subagent 改写
test("快照与 endgame findings 禁止改写", () => withBook((bookDir) => {
  const tools = createToolExecutor({ bookDir });
  for (const path of ["_pipeline/snapshots/C1_before_N14_r1/第001章_测试.md",
                      "_pipeline/endgame_findings.json"]) {
    const result = tools.execute("write_file", { path, content: "x" });
    assert.equal(result.ok, false, `${path} 应被拒绝`);
  }
}));

function throwsCode(fn, code) {
  assert.throws(fn, (error) => {
    assert.equal(error?.code, code);
    return true;
  });
}

// A5：本地 CSRF——Origin 只校验主机名不校验端口时，任意 localhost 页面可写
test("A5 Origin 必须与服务端同源", () => {
  const base = { method: "POST", headers: { host: "127.0.0.1:43120" } };
  // 同源放行（含回环别名等价：同端口只有一个进程能绑定，等价不增加攻击面）
  assert.doesNotThrow(() => assertLocalRequest({
    ...base, headers: { ...base.headers, origin: "http://127.0.0.1:43120" },
  }));
  assert.doesNotThrow(() => assertLocalRequest({
    ...base, headers: { ...base.headers, origin: "http://localhost:43120" },
  }));
  // 跨端口本地页（恶意 localhost:9999）拒绝
  throwsCode(() => assertLocalRequest({
    ...base, headers: { ...base.headers, origin: "http://localhost:9999" },
  }), "invalid_origin");
  // 跨站拒绝
  throwsCode(() => assertLocalRequest({
    ...base, headers: { ...base.headers, origin: "https://evil.example" },
  }), "invalid_origin");
  // Sec-Fetch-Site 跨站拒绝
  throwsCode(() => assertLocalRequest({
    ...base, headers: { ...base.headers, "sec-fetch-site": "cross-site" },
  }), "invalid_fetch_site");
  // 无 Origin（curl 等非浏览器客户端）仍放行——回环 Host 是那道防线
  assert.doesNotThrow(() => assertLocalRequest(base));
  // 非回环 Host 一律拒绝
  throwsCode(() => assertLocalRequest({
    method: "GET", headers: { host: "evil.example:43120" },
  }), "invalid_host");
  // isSameOrigin 端口敏感；回环别名等价
  assert.equal(isSameOrigin("http://127.0.0.1:43120", "127.0.0.1:43120"), true);
  assert.equal(isSameOrigin("http://localhost:43120", "127.0.0.1:43120"), true);
  assert.equal(isSameOrigin("http://127.0.0.1:9999", "127.0.0.1:43120"), false);
  assert.equal(isSameOrigin("https://evil.example", "127.0.0.1:43120"), false);
});
