import argparse
import json
from pathlib import Path


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init")
    init.add_argument("--project", required=True)
    init.add_argument("--input", required=False)
    draft = sub.add_parser("draft")
    draft.add_argument("--project", required=True)
    draft.add_argument("--chapter", type=int, required=True)
    draft.add_argument("--out", required=True)
    commit = sub.add_parser("commit")
    commit.add_argument("--project", required=True)
    commit.add_argument("--input", required=True)
    args = parser.parse_args()
    project = Path(args.project)
    if args.cmd == "init":
        write(project / "追踪" / "_tracking-state.json", {
            "schema_version": 1,
            "state_revision": 0,
            "last_committed_chapter": 0,
            "wordcount_records": {},
            "characters": {},
            "context": {},
            "foreshadow": {},
            "timeline": [],
        })
        print(json.dumps({"status": "ok"}))
        return 0
    if args.cmd == "draft":
        chapter = args.chapter
        out = Path(args.out)
        txn = {
            "schema_version": 1,
            "mode": "append",
            "chapter": chapter,
            "chapter_title": f"第{chapter}章 mock",
            "expected_state_revision": 0,
            "delta": {
                "result": "",
                "character_changes": [],
                "foreshadow_changes": [],
                "timeline_events": [],
                "constraints": [],
                "next_chapter_commitments": [],
                "retired_context_items": [],
                "retired_characters": [],
            },
            "context": {
                "position": {
                    "volume": "第一卷",
                    "volume_start_chapter": 1,
                    "story_time": "1990-01-01",
                    "scene": "厂办",
                },
                "long_term_constraints": [],
                "active_character_names": [],
                "continuity_risks": [],
            },
            "character_snapshots": {},
        }
        write(out, txn)
        print(json.dumps({
            "status": "ok",
            "draft": str(out.relative_to(project)).replace("\\", "/"),
            "expected_state_revision": 0,
            "fill": "按 shapes 填写",
            "limits": {},
            "shapes": {"delta": "array", "context": "object"},
            "current_snapshots": {},
        }, ensure_ascii=False))
        return 0
    if args.cmd == "commit":
        # 预检/封板都会调 commit；夹具只需回报成功（真实校验在真工具里）
        print(json.dumps({"status": "ok", "last_committed_chapter": 1, "state_revision": 1}))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
