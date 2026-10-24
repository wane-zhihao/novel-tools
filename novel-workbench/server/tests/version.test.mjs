import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const SKILL_DIR = resolve(TEST_DIR, "..", "..");

test("package.json 与 SKILL.md 版本一致", () => {
  const pkg = JSON.parse(readFileSync(join(SKILL_DIR, "package.json"), "utf8"));
  const skill = readFileSync(join(SKILL_DIR, "SKILL.md"), "utf8");
  const match = /^\s*version:\s*"?([0-9]+\.[0-9]+\.[0-9]+)"?/m.exec(skill);
  assert.ok(match, "SKILL.md 缺少 metadata.version");
  assert.equal(match[1], pkg.version);
});
