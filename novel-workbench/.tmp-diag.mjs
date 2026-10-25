import { readFileSync, existsSync } from "node:fs";
import { loadAgentConfig } from "./server/agent-config.mjs";
import { defaultDataDir } from "./server/paths.mjs";
import { createProvider } from "./server/agent/providers.mjs";
import { createToolExecutor } from "./server/agent/tools.mjs";
import { secretResolver } from "./server/secrets.mjs";
import { resolveRuntimePaths } from "./server/agent/runtime.mjs";

const instruction = JSON.parse(readFileSync(".tmp-instruction.json", "utf8"));
const BOOK = "F:/小说/works/顶职";
const dataDir = defaultDataDir();
const paths = resolveRuntimePaths({ dataDir });
const loaded = loadAgentConfig({ bookDir: BOOK, dataDir });
const role = instruction.role;
const roleConfig = loaded.config.roles[role];
const provider = createProvider(roleConfig.provider, loaded.config.providers[roleConfig.provider], {
  env: {},
  secrets: secretResolver({ dataDir, env: {} }),
});
const tools = createToolExecutor({ bookDir: BOOK, readRoots: paths.readRoots ?? [] });
console.log("readRoots:", JSON.stringify(paths.readRoots ?? []));
const messages = [
  { role: "system", content: "你是「签约工作台」的执行智能体，只能通过工具读写当前书目录内的文件。规则：1. 严格按用户消息里的节点指令执行。2. 所有产出必须用 write_file 写到指令指定路径。3. 完成后只回一句 ≤100 字回执。" },
  { role: "user", content: instruction.prompt },
];
console.log("=== model:", roleConfig.model, "===");
for (let step = 0; step < 8; step += 1) {
  const reply = await provider.chat({
    model: roleConfig.model,
    messages,
    tools: tools.definitions,
    temperature: roleConfig.temperature,
    maxTokens: roleConfig.max_tokens,
  });
  console.log(`\n--- chat ${step + 1} ---`);
  console.log("content:", (reply.content || "(空)").slice(0, 300));
  console.log("toolCalls:", reply.toolCalls.length);
  for (const call of reply.toolCalls) {
    let args = {};
    try { args = JSON.parse(call.arguments || "{}"); } catch { console.log("  ARGS PARSE FAIL:", call.arguments?.slice(0, 120)); }
    console.log("  ->", call.name, JSON.stringify(args).slice(0, 160));
  }
  if (!reply.toolCalls.length) break;
  messages.push({
    role: "assistant",
    content: reply.content || null,
    tool_calls: reply.toolCalls.map((c) => ({ id: c.id, type: "function", function: { name: c.name, arguments: c.arguments } })),
  });
  for (const call of reply.toolCalls) {
    let args = {};
    try { args = JSON.parse(call.arguments || "{}"); } catch {}
    const result = tools.execute(call.name, args);
    console.log("  result:", result.ok, String(result.content).slice(0, 120));
    messages.push({ role: "tool", tool_call_id: call.id, content: result.content });
  }
}
console.log("\n=== 大纲/ after ===");
console.log(existsSync(BOOK + "/大纲/选题报告.md") ? "选题报告.md EXISTS" : "选题报告.md MISSING");
