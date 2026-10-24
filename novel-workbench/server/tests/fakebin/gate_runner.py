import json
import os
import sys

status = os.environ.get("FAKE_GATE_STATUS", "pass")
file = sys.argv[-1].split("\\")[-1].split("/")[-1]
print(json.dumps([{
    "file": file,
    "status": status,
    "metrics": {"visible_chars": 2200, "dialog_ratio": 0.2},
    "findings": [],
}], ensure_ascii=False))
