import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createToolExecutor } from "../agent/tools.mjs";

test("书外只读根可读，写入仍被拒绝", () => {
  const root = mkdtempSync(join(tmpdir(), "wb-read-roots-"));
  const bookDir = join(root, "book");
  const refs = join(root, "refs");
  mkdirSync(bookDir, { recursive: true });
  mkdirSync(refs, { recursive: true });
  writeFileSync(join(refs, "guide.md"), "手法手册内容", "utf8");
  try {
    const tools = createToolExecutor({ bookDir, readRoots: [refs] });
    const absolute = tools.execute("read_file", { path: join(refs, "guide.md") });
    assert.equal(absolute.ok, true);
    assert.match(absolute.content, /手法手册/);

    const relative = tools.execute("read_file", { path: "guide.md" });
    assert.equal(relative.ok, true);

    const writeOutside = tools.execute("write_file", { path: join(refs, "guide.md"), content: "x" });
    assert.equal(writeOutside.ok, false);

    const noRoot = createToolExecutor({ bookDir });
    const denied = noRoot.execute("read_file", { path: join(refs, "guide.md") });
    assert.equal(denied.ok, false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
