// TDD-003：事实闸门（_fact_check）必须 fail-closed，且它的两个错误码必须在 UI 有落点。
//
// 现状（红）：scripts/commit_chapter.py:62-66
//     rc, out = run_script(args, book, timeout=120)
//     try:  return rc, json.loads(out)
//     except ValueError:  return rc, {"raw": str(out)[-400:]}
//   而 inline_runner.run_script:90 返回的是 **stdout + stderr 拼接**：闸门只要往 stderr 打一行
//   warning（或往 stdout 打一行进度日志），json.loads 就整段失败，payload 退成 {"raw": ...}，
//   `fpayload.get("blocking")` 于是是空 → 190-205 行的阻断判定当作「没查出问题」放行，
//   定稿继续。这是 fail-open：闸门坏掉 == 闸门通过。
//   另一半：调用点只认 `frc == 2`，可 run_script 实际会返回 124（超时）/127（找不到解释器）
//   或工具自己的任意退出码；这些都被当成「非 2 → 继续」，同样静默放行。
//   仓库里本来就有 extract_json（inline_runner.py:129，9 处调用），_fact_check 是唯一没用的。
//
// 另一半（前端）：commit_chapter 已经会抛 fact_blocked / fact_tool_unavailable，
//   但 web/lib/errors.js 的映射表里没有这两个码 → errorState 走兜底分支，
//   把后端 message（可能带 stderr 尾巴）直接甩给用户，且没有任何出口。
//
// 期望（绿）：解析失败 / rc ∉ (0,1) → 一律归一为「工具不可用」（rc=2 语义），
//   阻断项解析不出来时宁可拒绝定稿，绝不静默放行；两个码进 errors.js 映射表。
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { errorState, knownErrorCodes } from "../../web/lib/errors.js";
import { detectPython } from "../settings.mjs";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(TEST_DIR, "..", "..");
const SCRIPTS_DIR = join(REPO_ROOT, "scripts");
const CONFIG_PATH = join(REPO_ROOT, "config.json");
const baseConfig = existsSync(CONFIG_PATH) ? JSON.parse(readFileSync(CONFIG_PATH, "utf8")) : {};

const FACT_BLOCKING = [{ id: "F1", chapter: "001", why: "字数断言不符：写「四个字」实为 3 字" }];

// 直调被测函数：用仓库自己的 commit_chapter._fact_check，不重写一份逻辑
const FACT_HARNESS = [
  "import json, sys",
  "from pathlib import Path",
  "scripts, book = sys.argv[1], Path(sys.argv[2])",
  "sys.path.insert(0, scripts)",
  "import commit_chapter as cc",
  "rc, payload = cc._fact_check(book)",
  "print(json.dumps({'rc': rc, 'payload': payload}, ensure_ascii=False, default=str))",
].join("\n");

function makeBook(root) {
  const book = join(root, "book");
  mkdirSync(join(book, "正文"), { recursive: true });
  mkdirSync(join(book, "追踪"), { recursive: true });   // 不放 _fact-assertions.json，避免多带 --config
  writeFileSync(join(book, "正文", "第001章_换名.md"), "评定结论四个字：不合格。\n", "utf8");
  return book;
}

/**
 * 用一个假 fact_gate 跑一次 _fact_check。
 * WORKBENCH_CONFIG 指向临时配置：config.json 里没有 FACT_GATE 键，_fact_check 会硬编回 F:\小说，
 * 那样测的就是上游工具而不是这条错误处理路径。pipeline_spec 在 import 时就校验必备键，
 * 所以临时配置必须在仓库 config.json 上覆盖，不能另起一份。
 */
function runFactCheck({ root, python, book, source, name = "stub_fact_gate.py" }) {
  const stub = join(root, name);
  writeFileSync(stub, source, "utf8");
  const config = join(root, "workbench-config.json");
  writeFileSync(config, JSON.stringify({
    ...baseConfig, PYTHON: python, FACT_GATE: stub,
  }), "utf8");
  const out = execFileSync(python, ["-c", FACT_HARNESS, SCRIPTS_DIR, book], {
    encoding: "utf8",
    timeout: 120_000,
    windowsHide: true,
    env: { ...process.env, WORKBENCH_CONFIG: config, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
  });
  return JSON.parse(out.trim().split(/\r?\n/).at(-1));
}

// 闸门「有阻断」时真实返回的 JSON（rc=1）；噪音行可以走 stderr 或 stdout
function gateSource(noise, stream, exitCode) {
  return [
    "import json, sys",
    `print(${JSON.stringify(noise)}, file=sys.${stream}, flush=True)`,
    "print(json.dumps({'status': 'blocked', 'blocking': "
    + JSON.stringify(FACT_BLOCKING)
    + ", 'advisory': []}, ensure_ascii=False))",
    `sys.exit(${exitCode})`,
  ].join("\n");
}

// 闸门「干净通过」但退出码不对（崩溃 / 参数错 / 信号）
function badRcSource(exitCode) {
  return [
    "import json, sys",
    "print(json.dumps({'status': 'pass', 'blocking': [], 'advisory': []}))",
    `sys.exit(${exitCode})`,
  ].join("\n");
}

const blocked = (payload) => Array.isArray(payload?.blocking) && payload.blocking.length === 1
  && payload.blocking[0].id === "F1";

test("DL-003 闸门 stderr 上的告警行不得吞掉阻断项（fail-open）", (t) => {
  const python = detectPython();
  if (!python) return t.skip("本机没有可执行的 Python");
  if (!existsSync(CONFIG_PATH)) return t.skip(`缺少 ${CONFIG_PATH}，_fact_check 取不到工具路径`);
  const root = mkdtempSync(join(tmpdir(), "wb-tdd003-stderr-"));
  try {
    const res = runFactCheck({
      root, python, book: makeBook(root),
      source: gateSource("WARN: fact_gate 台账缺 base_date，按正文推断\n", "stderr", 1),
    });
    assert.equal(res.rc, 1, `夹具闸门应以 rc=1 报阻断，实际 rc=${res.rc}：${JSON.stringify(res.payload)}`);
    assert.ok(
      blocked(res.payload) || res.rc === 2,
      "stdout 的 JSON 被 stderr 的一行 warning 拼在一起后 json.loads 整段失败，"
      + "_fact_check 退成 {\"raw\": ...}（commit_chapter.py:63-66）→ 阻断项凭空消失，定稿照样通过。"
      + "必须用仓库自带的 extract_json 取 JSON，或干脆归一为「工具不可用」。实际返回："
      + JSON.stringify(res),
    );
    assert.equal(res.payload.raw, undefined, `payload 只剩 raw 就是吞掉了结果：${JSON.stringify(res.payload)}`);
  } finally {
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});

test("DL-003 闸门 stdout 前缀日志同样不得让阻断静默消失", (t) => {
  const python = detectPython();
  if (!python) return t.skip("本机没有可执行的 Python");
  if (!existsSync(CONFIG_PATH)) return t.skip(`缺少 ${CONFIG_PATH}，_fact_check 取不到工具路径`);
  const root = mkdtempSync(join(tmpdir(), "wb-tdd003-stdout-"));
  try {
    const res = runFactCheck({
      root, python, book: makeBook(root),
      source: gateSource("scanning 3 chapters...\n", "stdout", 1),
    });
    assert.ok(
      blocked(res.payload) || res.rc === 2,
      "闸门在 JSON 前多打一行进度日志，阻断项就被当成「没查出来」放行了。实际：" + JSON.stringify(res),
    );
  } finally {
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});

test("DL-003 rc 不在 (0,1) 内必须归一为工具不可用，不能当通过", (t) => {
  const python = detectPython();
  if (!python) return t.skip("本机没有可执行的 Python");
  if (!existsSync(CONFIG_PATH)) return t.skip(`缺少 ${CONFIG_PATH}，_fact_check 取不到工具路径`);
  // 124=run_script 超时哨兵，127=找不到解释器，3=工具自己崩了；调用点只认 frc == 2，
  // 这三个值现在全部落到「继续定稿」分支（commit_chapter.py:192-205）。
  const cases = [
    { rc: 124, why: "超时（run_script 返回 124，输出只有 TIMEOUT 文本）" },
    { rc: 127, why: "解释器/脚本找不到" },
    { rc: 3, why: "闸门自身异常退出，但 stdout 上恰好有一段 pass JSON" },
  ];
  const root = mkdtempSync(join(tmpdir(), "wb-tdd003-rc-"));
  try {
    const book = makeBook(root);
    for (const item of cases) {
      const res = runFactCheck({
        root, python, book, name: `stub_fact_gate_${item.rc}.py`,
        source: item.rc === 124
          ? "import sys\nprint('TIMEOUT after 120s')\nsys.exit(124)\n"
          : badRcSource(item.rc),
      });
      assert.equal(
        res.rc, 2,
        `${item.why}：_fact_check 原样返回 rc=${res.rc}，调用点只挡 frc == 2 → `
        + `事实闸门等于没跑，还继续定稿（payload=${JSON.stringify(res.payload)}）。`
        + "契约：0=过 / 1=有阻断 / 其他一律 2=工具不可用（fact_tool_unavailable）。",
      );
      assert.notEqual(res.payload.status, "pass", `rc=${item.rc}（${item.why}）绝不能报 pass`);
    }
  } finally {
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});

test("DL-003 fact_blocked / fact_tool_unavailable 必须在 web/lib/errors.js 有落点", () => {
  const codes = knownErrorCodes();
  for (const code of ["fact_blocked", "fact_tool_unavailable"]) {
    assert.ok(
      codes.includes(code),
      `commit_chapter.py:193/202 已经在抛 ${code}，但 web/lib/errors.js 的映射表里没有它 → `
      + `errorState 走兜底分支，把后端 message 原样甩给用户、且一个出口都不给。已知码：${codes.join(",")}`,
    );
  }

  const blockedState = errorState(
    { code: "fact_blocked", message: "事实层阻断 1 条（日期/数值/字数断言）" },
    { go: () => {}, bookId: 1, chapter: 3 },
  );
  assert.notEqual(blockedState.text, "fact_blocked", "事实层阻断必须给人话，不是 code");
  assert.equal(blockedState.tone, "danger", "事实层阻断是拦人的，不能是 ok/warn");
  assert.ok(
    blockedState.actions.length >= 1,
    "fact_blocked 必须给一个能做的下一步（对齐 quality_blocked 的「去审：看问题」）",
  );

  const unavailable = errorState({ code: "fact_tool_unavailable", message: "事实闸门不可用" }, { go: () => {} });
  assert.equal(unavailable.tone, "danger", "「闸门没跑起来」不能伪装成通过");
  assert.ok(
    unavailable.actions.some((action) => /设置/.test(action.label)),
    "工具不可用多半是路径配置问题，必须给「去设置」出口（对齐 quality_tool_unavailable）",
  );
});
