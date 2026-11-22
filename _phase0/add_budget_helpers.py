from pathlib import Path
p=Path(r'F:\WorkBuddy AI\Novel Platform\novel-workbench\scripts\commands.py')
t=p.read_text(encoding='utf-8')
anchor='def _handle_verify_block(book: Path, st: dict, node: str, j: dict, jf: Path) -> int:'
helper='''def _budget_scope(node: str) -> str:
    """跨 run 保留的失败预算 scope。章级按章，卷级 gate 按节点。"""
    if node.startswith("C") and node.endswith(".N15"):
        return node.split(".", 1)[0]
    if node.startswith("V") and node.endswith(".N08"):
        return node
    if node in ("N20", "N20g"):
        return node
    return node


def _record_block_budget(st: dict, node: str, items: list) -> tuple[int, int, str]:
    """按固定 finding id/criterion 指纹累计预算；总次数兜底防措辞换皮刷轮。"""
    ids = sorted({str(x.get("id") or x.get("criterion") or "unknown")
                  for x in items if isinstance(x, dict) and x.get("blocking", True)})
    if not ids:
        ids = sorted({str(x.get("criterion") or x.get("id") or "unknown")
                      for x in items if isinstance(x, dict)})
    if not ids:
        ids = ["<empty-block>"]
    fp = hashlib.sha1("\\n".join(ids).encode("utf-8")).hexdigest()[:12]
    scope = _budget_scope(node)
    budgets = st.setdefault("budget", {})
    old = budgets.get(scope) or {}
    same_rounds = int(old.get("rounds", 0)) + 1 if old.get("fp") == fp else 1
    total = int(old.get("total", 0)) + 1
    budgets[scope] = {"rounds": same_rounds, "total": total, "fp": fp,
                      "ids": ids, "updated_at": now()}
    return same_rounds, total, fp


'''
assert anchor in t and 'def _record_block_budget' not in t
t=t.replace(anchor,helper+anchor,1)
p.write_text(t,encoding='utf-8')
print('added budget helpers')
