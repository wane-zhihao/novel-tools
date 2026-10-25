import hashlib
import json
import os
import sys
from pathlib import Path

status = os.environ.get("FAKE_STORY_STATUS", "ready")
args = sys.argv[1:]


def opt(name):
    return args[args.index(name) + 1] if name in args else None


if status == "ready":
    doc = {
        "status": "ready",
        "quality": {"status": "pass", "blocking_findings": []},
        "length": {"status": "internal_pass", "actual": 2200, "target": 2200},
    }
elif status == "blocked":
    doc = {
        "status": "blocked",
        "quality": {"status": "fail", "blocking_findings": [
            {"type": "QUALITY", "message": "动作链流水账", "line": 3, "excerpt": "他走过去"}]},
        "length": {"status": "internal_pass", "actual": 2200, "target": 2200},
    }
elif status == "invalid":
    doc = {"status": "invalid", "error_code": "INVALID", "message": "章节结构无效"}
elif status == "tool_unavailable":
    doc = {"status": "tool_unavailable", "message": "质量工具缺失"}
else:
    doc = {"status": "error", "error_code": "CHECK_FAILED", "message": "boom"}

# commit 分支：模拟上游「写账本」的真实行为，否则 API 层的幂等/前进无从验证
if "commit" in args and status == "ready":
    project = Path(opt("--project"))
    chapter = int(opt("--chapter"))
    files = sorted((project / "正文").glob(f"第{chapter:03d}章_*.md"))
    if not files:
        print(json.dumps({"status": "error", "error_code": "CHAPTER_NOT_FOUND",
                          "message": "找不到正文"}, ensure_ascii=False))
        sys.exit(2)
    body = files[0].read_bytes()
    state_path = project / "追踪" / "_tracking-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    records = state.setdefault("wordcount_records", {})
    records[str(chapter)] = {"actual": len(body.decode("utf-8")), "target": 2200,
                             "body_sha256": hashlib.sha256(body).hexdigest()}
    state["last_committed_chapter"] = max(int(state.get("last_committed_chapter") or 0), chapter)
    state["state_revision"] = int(state.get("state_revision") or 0) + 1
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"status": "ok",
                      "last_committed_chapter": state["last_committed_chapter"],
                      "state_revision": state["state_revision"]}, ensure_ascii=False))
    sys.exit(0)

print(json.dumps(doc, ensure_ascii=False))
sys.exit(0 if status in ("ready", "blocked") else 2)
