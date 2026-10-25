import { readFileSync } from "node:fs";
import { getSecret } from "./server/secrets.mjs";
import { loadAgentConfig } from "./server/agent-config.mjs";
import { defaultDataDir } from "./server/paths.mjs";
import { createToolExecutor } from "./server/agent/tools.mjs";
import { resolveRuntimePaths } from "./server/agent/runtime.mjs";

const instruction = JSON.parse(readFileSync(".tmp-instruction.json", "utf8"));
const BOOK = "F:/小说/works/顶职";
const dataDir = defaultDataDir();
const paths = resolveRuntimePaths({ dataDir });
const loaded = loadAgentConfig({ bookDir: BOOK, dataDir });
const roleConfig = loaded.config.roles[instruction.role];
const { value: key } = getSecret("DEEPSEEK_API_KEY");
const tools = createToolExecutor({ bookDir: BOOK, readRoots: paths.readRoots ?? [] });

const messages = [
  { role: "system", content: "你是「签约工作台」的执行智能体，只能通过工具读写当前书目录内的文件。规则：1. 严格按用户消息里的节点指令执行。2. 所有产出必须用 write_file 写到指令指定路径。3. 完成后只回一句 ≤100 字回执。" },
  { role: "user", content: instruction.prompt },
];
// 先读三个源文件（复刻 chat1 的成功路径），再发一轮不带工具的直接提问，看原始响应
const docPaths = [
  "F:\\小说\\platforms\\qimao/01_投稿方案.md",
  "F:\\小说\\platforms\\qimao/02_写作方案.md",
  "F:\\小说\\platforms\\qimao/harness/genre-baselines.json",
];
for (const p of docPaths) {
  const r = tools.execute("read_file", { path: p, limit: 3000 });
  messages.push({ role: "user", content: `【文件 ${p} 开头】\n${String(r.content).slice(0, 1500)}` });
}
messages.push({ role: "user", content: "资料已读完。现在把选题报告写入 F:\\小说\\works\\顶职/大纲/选题报告.md（用 write_file 工具）。" });

const response = await fetch("https://api.deepseek.com/v1/chat/completions", {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
  body: JSON.stringify({
    model: roleConfig.model,
    messages,
    tools: tools.definitions,
    tool_choice: "auto",
    temperature: roleConfig.temperature,
    max_tokens: roleConfig.max_tokens,
    stream: false,
  }),
});
const data = await response.json();
console.log("HTTP:", response.status);
const msg = data?.choices?.[0] ?? {};
console.log("finish_reason:", msg.finish_reason);
console.log("message keys:", Object.keys(msg.message ?? {}));
console.log("content:", JSON.stringify(msg.message?.content ?? null)?.slice(0, 300));
console.log("reasoning_content:", JSON.stringify(msg.message?.reasoning_content ?? null)?.slice(0, 400));
console.log("tool_calls:", JSON.stringify(msg.message?.tool_calls ?? null)?.slice(0, 400));
console.log("usage:", JSON.stringify(data?.usage ?? null));
