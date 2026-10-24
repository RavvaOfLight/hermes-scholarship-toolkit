#!/usr/bin/env python3
"""Clear status marks, notes, checklist ticks and added steps, and the log — per tracker (default: every tracker).

  reset.py                all trackers
  reset.py fall-2026      only that tracker's store
"""
import json
import sys

from importlib import import_module

board_import = import_module("import")  # same data folder, lock and atomic write as import.py


def main(want: list[str]) -> int:
    ids = want or [t["id"] for t in board_import.load_registry()]
    with board_import.store_lock():
        for tid in ids:
            p = board_import.STORES / f"{tid}.json"
            if not p.exists():
                print("skip (no store):", tid)
                continue
            doc = json.loads(p.read_text("utf-8"))
            for r in doc["scholarships"]:
                r["status"] = "verify"
                r["note"] = ""
                r.pop("taskDone", None)     # checklist ticks
                r.pop("customTasks", None)  # steps you added
            doc["log"] = []
            board_import._write(p, doc)
            print(f"reset {tid}: {len(doc['scholarships'])} awards → verify, notes, checklists and log cleared")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
