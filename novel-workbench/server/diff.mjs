const MAX_LINES = 1500;

function splitLines(text) {
  return String(text ?? "").replace(/\r\n?/g, "\n").split("\n");
}

/** 零依赖行级 diff：LCS 回溯，输出 same/add/del；超大文件退化为整段替换。 */
export function lineDiff(oldText, newText) {
  const a = splitLines(oldText);
  const b = splitLines(newText);
  if (a.length > MAX_LINES || b.length > MAX_LINES || a.length * b.length > 2_250_000) {
    return [
      ...a.map((line) => ({ type: "del", text: line })),
      ...b.map((line) => ({ type: "add", text: line })),
    ];
  }
  const rows = a.length + 1;
  const cols = b.length + 1;
  const table = Array.from({ length: rows }, () => new Uint32Array(cols));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j]
        ? table[i + 1][j + 1] + 1
        : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const out = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ type: "same", text: a[i] });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      out.push({ type: "del", text: a[i] });
      i += 1;
    } else {
      out.push({ type: "add", text: b[j] });
      j += 1;
    }
  }
  while (i < a.length) {
    out.push({ type: "del", text: a[i] });
    i += 1;
  }
  while (j < b.length) {
    out.push({ type: "add", text: b[j] });
    j += 1;
  }
  return out;
}

export function diffStats(lines) {
  let added = 0;
  let removed = 0;
  let same = 0;
  for (const line of lines) {
    if (line.type === "add") added += 1;
    else if (line.type === "del") removed += 1;
    else same += 1;
  }
  return { added, removed, same, changed: added + removed };
}

/** 把 diff 切成带上下文的 hunk，便于 UI 折叠。 */
export function diffHunks(lines, context = 3) {
  const changed = lines
    .map((line, index) => (line.type === "same" ? -1 : index))
    .filter((index) => index >= 0);
  if (!changed.length) return [];
  const ranges = [];
  let start = Math.max(0, changed[0] - context);
  let end = Math.min(lines.length - 1, changed[0] + context);
  for (const index of changed.slice(1)) {
    if (index - end <= context * 2 + 1) {
      end = Math.min(lines.length - 1, index + context);
    } else {
      ranges.push([start, end]);
      start = Math.max(0, index - context);
      end = Math.min(lines.length - 1, index + context);
    }
  }
  ranges.push([start, end]);
  return ranges.map(([from, to]) => lines.slice(from, to + 1));
}
