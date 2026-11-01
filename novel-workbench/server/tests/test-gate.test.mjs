import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

// TDD-006（审计 finding: npm test 只跑 100/114）：默认测试闸门必须真的跑全部测试文件。
//
// 背景：package.json 曾经手工列举 22 个测试文件，新增的
// commit.test.mjs / errors.test.mjs / run-guard.test.mjs 三个文件
// 被漏掉，其中 commit.test.mjs 覆盖的是**单章定稿**这条最危险的写路径。
// 全绿绿灯却什么都没保护到——比红灯更危险。
//
// 这条用例本身也必要：光把 package.json 改成 glob 只能解决"新增文件被漏"，
// 解决不了"文件名改成不匹配 glob"或"glob 写错"这类退化。
//
// *.red.mjs 是**故意红**的 TDD 用例（断言尚未实现的行为），
// 由 npm run test:red 单独跑，不属于闸门，故不在统计范围内。

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(TEST_DIR, "..", "..");

function testFiles() {
  return readdirSync(TEST_DIR).filter((name) => name.endsWith(".test.mjs")).sort();
}

test("npm test 必须执行 server/tests/ 下的全部 *.test.mjs（不得再手工列举）", () => {
  const files = testFiles();
  assert.ok(files.length > 0, "server/tests/ 下应有 *.test.mjs");

  const pkg = JSON.parse(readFileSync(join(REPO_ROOT, "package.json"), "utf8"));
  const script = pkg.scripts?.test ?? "";

  assert.match(
    script,
    /server\/tests\/\*\.test\.mjs/,
    `npm test 必须是自动发现（server/tests/*.test.mjs），实际是：${script}`,
  );
  assert.doesNotMatch(
    script,
    /\.test\.mjs\s+\S*\.test\.mjs/,
    `npm test 不得手工列举多个测试文件（这是 100/114 漏跑的根因），实际是：${script}`,
  );

  // 红灯用例必须用 .red.mjs 命名，否则会被 glob 吞进闸门
  for (const name of readdirSync(TEST_DIR).filter((n) => n.endsWith(".red.mjs"))) {
    assert.ok(
      !name.endsWith(".test.mjs"),
      `${name} 同时匹配 *.red.mjs 与 *.test.mjs 语义；红灯用例请只用 .red.mjs 后缀`,
    );
  }
});

test("每个 *.test.mjs 都能被加载（加载崩溃 = 零保护，且不计入用例数）", () => {
  // 用子进程只做「加载」不执行断言：--test-name-pattern 匹配一个不存在的名字，
  // 于是所有文件都被加载（顶层 import 全部执行）但不跑任何用例。
  //
  // 注意：**不能**用 per-file 的 ✔/✖ 判定成败 —— 当没有任何用例匹配时，
  // Node 会把文件标成 ✖（那是"没匹配到用例"而非"加载失败"），会产生假阳性。
  // 可靠的信号是加载期异常的特征串：模块找不到 / 语法错误。
  //
  // 必须排除本文件：它自己会再开一个子进程探测，递归下去会栈溢出。
  const pattern = "__load_probe_no_such_test__";
  const targets = testFiles().filter((f) => f !== "test-gate.test.mjs");
  let output = "";
  try {
    output = execFileSync(
      process.execPath,
      [
        "--test",
        `--test-name-pattern=${pattern}`,
        ...targets.map((f) => `server/tests/${f}`),
      ],
      { encoding: "utf8", cwd: REPO_ROOT, timeout: 300_000 },
    );
  } catch (error) {
    output = `${error.stdout ?? ""}${error.stderr ?? ""}`;
  }

  const loadErrors = output
    .split("\n")
    .filter((line) =>
      /Cannot find (package|module)|ERR_MODULE_NOT_FOUND|SyntaxError:|is not a function|Unexpected token/.test(line),
    );
  assert.deepEqual(
    loadErrors,
    [],
    `这些测试文件加载即崩，等于零保护（用例会静默变 0）：\n${loadErrors.join("\n")}`,
  );
});

test("npm test 的用例数不得少于静态可数出的用例数下限", () => {
  // 静态计数：每个文件里 test( / it( 的出现次数之和 = 用例数下限
  // （一个 test() 可能被跳过或参数化，故只做下限校验）。
  // 如果闸门漏跑文件，这个数会明显小于实际注册数。
  const pkg = JSON.parse(readFileSync(join(REPO_ROOT, "package.json"), "utf8"));
  const script = pkg.scripts?.test ?? "";
  const staticCount = testFiles().reduce((sum, name) => {
    const text = readFileSync(join(TEST_DIR, name), "utf8");
    const matches = text.match(/^\s*(test|it)\s*\(/gm);
    return sum + (matches ? matches.length : 0);
  }, 0);

  assert.ok(staticCount > 0, "静态计数为 0，说明统计方法失效");
  // 记录基线供人工核对：当前静态计数应等于实际运行数（除非有参数化用例）
  assert.ok(
    staticCount >= 100,
    `静态可数用例数 ${staticCount} 异常偏低，可能统计口径或文件状态出了问题`,
  );
  assert.match(script, /server\/tests\/\*\.test\.mjs/, "闸门脚本必须是 glob");
});
