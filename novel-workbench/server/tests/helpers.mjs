import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { detectPython } from "../settings.mjs";

export const FAKEBIN = join(dirname(fileURLToPath(import.meta.url)), "fakebin");
export const FAKE_PLATFORM = join(FAKEBIN, "platform");

export function fakeEnv(python = detectPython()) {
  return {
    WORKBENCH_PYTHON: python,
    WORKBENCH_NODE: process.execPath,
    WORKBENCH_STORY_LONG_WRITE: FAKEBIN,
    WORKBENCH_STORYCTL: join(FAKEBIN, "storyctl.py"),
    WORKBENCH_TRACKING_COMMIT: join(FAKEBIN, "tracking_commit.py"),
    WORKBENCH_CHECK_OUTLINE: join(FAKEBIN, "check-outline.js"),
    WORKBENCH_AI_PATTERNS: join(FAKEBIN, "ai-patterns.js"),
    WORKBENCH_DEGENERATION: join(FAKEBIN, "degeneration.js"),
    WORKBENCH_PLATFORM_DIR: FAKE_PLATFORM,
    WORKBENCH_CORE_TOOLS: FAKEBIN,
    WORKBENCH_GATE_RUNNER: join(FAKEBIN, "gate_runner.py"),
    WORKBENCH_STYLE_PROBE: join(FAKEBIN, "style_probe.py"),
    WORKBENCH_QIMAO_HARNESS: join(FAKE_PLATFORM, "harness"),
    WORKBENCH_QIMAO_GATES: join(FAKE_PLATFORM, "harness", "gates.json"),
    WORKBENCH_QIMAO_THRESHOLDS: join(FAKE_PLATFORM, "config", "thresholds.json"),
    WORKBENCH_QIMAO_GENRE_BASE: join(FAKE_PLATFORM, "harness", "genre-baselines.json"),
    WORKBENCH_QIMAO_STYLE_ANCHOR: join(FAKE_PLATFORM, "harness", "style-anchor.json"),
    WORKBENCH_QIMAO_CHAPTER_CHECK: join(FAKE_PLATFORM, "tools", "qimao-chapter-check.py"),
    WORKBENCH_READ_ROOTS: FAKE_PLATFORM,
    FAKE_STORY_STATUS: "ready",
    FAKE_GATE_STATUS: "pass",
  };
}

export function applyFakeEnv() {
  Object.assign(process.env, fakeEnv());
}
