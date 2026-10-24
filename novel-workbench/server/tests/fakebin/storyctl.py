import json
import os
import sys

status = os.environ.get("FAKE_STORY_STATUS", "ready")
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
print(json.dumps(doc, ensure_ascii=False))
sys.exit(0 if status in ("ready", "blocked") else 2)
