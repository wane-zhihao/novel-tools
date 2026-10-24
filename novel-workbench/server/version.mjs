import { readFileSync } from "node:fs";
import { join } from "node:path";
import { SKILL_DIR } from "./paths.mjs";

let cached = null;

export function workbenchVersion() {
  if (cached) return cached;
  try {
    cached = JSON.parse(readFileSync(join(SKILL_DIR, "package.json"), "utf8")).version ?? "0.0.0";
  } catch {
    cached = "0.0.0";
  }
  return cached;
}
