import { appendFileSync, readFileSync, writeFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const pipelinePath = resolve(process.argv[2] ?? "");
const bookDir = resolve(process.argv[3] ?? "");
if (!process.argv[2] || !process.argv[3]) {
  console.error("Usage: node acceptance-runner.mjs <pipeline.json> <book-dir>");
  process.exit(2);
}
const pipeline = JSON.parse(readFileSync(pipelinePath, "utf8"));
const serverDir = String.raw`F:\WorkBuddy AI\Novel Platform\novel-workbench\server`;
const { AgentRuntime } = await import(pathToFileURL(join(serverDir, "agent", "runtime.mjs")));
const { defaultDataDir } = await import(pathToFileURL(join(serverDir, "paths.mjs")));
const { loadAgentConfig } = await import(pathToFileURL(join(serverDir, "agent-config.mjs")));
const { secretInfo } = await import(pathToFileURL(join(serverDir, "secrets.mjs")));
const dataDir = defaultDataDir();
const loaded = loadAgentConfig({
  bookDir,
  dataDir,
  secretNames: new Set(secretInfo({ dataDir }).names.map((item) => item.name)),
});
if (loaded.errors.length) throw new Error(loaded.errors.join("; "));
const provider = loaded.providers.find((item) => item.name === "deepseek");
if (!provider || provider.type !== "openai" || !provider.configured) {
  throw new Error("DeepSeek provider 未配置，拒绝开始模型验收");
}
for (const role of ["writer", "judge", "scout"]) {
  if (loaded.config.roles?.[role]?.provider !== "deepseek") {
    throw new Error(`${role} 未指向 DeepSeek，拒绝开始模型验收`);
  }
}
const eventPath = join(bookDir, "_pipeline", "runtime-events.jsonl");
const resultPath = join(bookDir, "_pipeline", "runtime-result.json");
writeFileSync(eventPath, "", "utf8");
const paths = {
  python: pipeline.PYTHON,
  autopilot: join(serverDir, "..", "scripts", "autopilot.py"),
  configPath: pipelinePath,
  readRoots: pipeline.WORKBENCH_READ_ROOTS ?? [],
};
const runtime = new AgentRuntime({
  bookDir,
  config: loaded.config,
  dataDir,
  paths,
  readRoots: paths.readRoots,
  runId: `acceptance-${basename(bookDir)}`,
  onEvent(event) {
    const safe = {
      type: event.type,
      node: event.node,
      role: event.role,
      provider: event.provider,
      model: event.model,
      attempt: event.attempt,
      routed_to: event.routed_to,
      reason: event.reason,
      error: event.error,
      detail: event.detail,
      status: event.status,
      book: event.book,
      steps: event.steps,
      llm_rounds: event.llm_rounds,
      usage: event.usage,
      error: event.error,
    };
    appendFileSync(eventPath, `${JSON.stringify(safe)}\n`, "utf8");
    if (["node_started", "node_done", "node_retry", "routed", "run_finished"].includes(event.type)) {
      console.log(JSON.stringify(safe));
    }
  },
});
const result = await runtime.run();
writeFileSync(resultPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
console.log(JSON.stringify({ resultPath, status: result.status, book: result.book, llm_rounds: result.llm_rounds, usage: result.usage }));
if (result.status !== "done") process.exitCode = 1;
