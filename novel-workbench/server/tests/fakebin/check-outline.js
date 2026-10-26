// 假检查器：输出与真实 check-outline 同形的 JSON，至少覆盖 _outline_gate 依赖的
// wordcount 块（target / target_status / range / range_status）。
//
// 为什么必须解析细纲：不解析就等于「检查器永远不报告字数」，_outline_gate 会把
// 缺字段判成 target=None 而阻断——那是假件的缺陷被当成产品的缺陷，会让整条
// mock 流水线在 N10 永久卡住。真实工具是解析的，假件也得解析。
import { readFileSync } from "node:fs";

const args = process.argv.slice(2);
const flagIndex = args.indexOf("--json");
const file = flagIndex >= 0 ? args[flagIndex + 1] : args.at(-1);

let text = "";
try {
  text = readFileSync(file, "utf8");
} catch {
  // 读不到就当没声明，让下游按缺字段处理（与真实工具行为一致）
}

// 兼容「2200 字」「约 2200」「2,200」等写法
const targetMatch = /字数目标[：:]\s*(?:约)?\s*([\d,]+)/.exec(text);
const target = targetMatch ? Number(targetMatch[1].replaceAll(",", "")) : null;

// 「字数范围：2000-2600」，容忍全角/波浪号/「到」
const rangeMatch = /字数范围[：:]\s*(\d+)\s*(?:-|–|~|～|到)\s*(\d+)/.exec(text);
const range = rangeMatch
  ? { min: Number(rangeMatch[1]), max: Number(rangeMatch[2]) }
  : null;

console.log(JSON.stringify({
  schema_version: 1,
  verifier: "fakebin.check-outline",
  status: "ok",
  ok: true,
  file,
  wordcount: {
    target,
    target_status: target === null ? "missing" : "ok",
    range,
    range_status: range === null ? "missing" : "ok",
    contract_status: "ok",
  },
  checks: [],
  failures: [],
  advisories: [],
  blocking: [],
}));
