import json
import sys
from pathlib import Path

SCRIPTS = Path(r"F:/WorkBuddy AI/Novel Platform/novel-workbench/scripts")
sys.path.insert(0, str(SCRIPTS))
import autopilot  # noqa: E402
from pipeline_spec import GLOBAL_NODES  # noqa: E402

BOOK = Path(r"F:/小说/works/顶职")
st = autopilot.load_state(BOOK)
spec = GLOBAL_NODES[st["cursor"]]
prompt = autopilot.render_prompt(spec["prompt"], BOOK)
instruction = {
    "node": spec["id"],
    "kind": spec["kind"],
    "role": spec["role"],
    "evidence": spec["evidence"],
    "prompt": prompt,
}
out = Path(r"F:/WorkBuddy AI/Novel Platform/novel-workbench/.tmp-instruction.json")
out.write_text(json.dumps(instruction, ensure_ascii=False, indent=1), encoding="utf-8")
print("node:", spec["id"], "| role:", spec["role"], "| evidence:", spec["evidence"])
print("prompt chars:", len(prompt))
