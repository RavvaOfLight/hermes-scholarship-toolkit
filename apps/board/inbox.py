"""Add finished research runs to the board.

The research skill's send_to_board() drops a request ({"run", "tracker", "label"}) in the inbox
folder; systemd (scholarship-board-inbox.path, from install.sh) starts this script, which imports
each request with import.py and leaves <request>.done with the result for the skill to read.

    python3 inbox.py          (BOARD_DATA_DIR, BOARD_INBOX, BOARD_IMPORT, BOARD_PYTHON are honored)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("BOARD_DATA_DIR") or "~/.hermes/scholarship-board").expanduser()
INBOX = Path(os.environ.get("BOARD_INBOX") or DATA / "inbox").expanduser()
IMPORT = Path(os.environ.get("BOARD_IMPORT") or HERE / "import.py").expanduser()
PYTHON = os.environ.get("BOARD_PYTHON") or sys.executable
TRACKER_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


def handle(request: Path) -> dict:
    try:
        req = json.loads(request.read_text("utf-8"))
        run, tid, label = Path(str(req["run"])), str(req["tracker"]), str(req.get("label") or req["tracker"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "error": f"unreadable request: {exc}"}
    if not TRACKER_ID.fullmatch(tid):
        return {"ok": False, "error": f"tracker id must be lowercase letters, digits and dashes: {tid!r}"}
    if not (run / "evaluated.json").is_file():
        return {"ok": False, "error": f"no evaluated.json in {run}"}
    done = subprocess.run([PYTHON, str(IMPORT), "--run", str(run), "--id", tid, "--label", label[:80]],
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        return {"ok": False, "error": "import failed: " + (done.stderr or done.stdout).strip()[-500:]}
    return {"ok": True, "tracker": tid, "label": label[:80], "output": done.stdout.strip()[-300:]}


def main() -> int:
    if not INBOX.is_dir():
        return 0
    for request in sorted(INBOX.glob("*.json")):
        result = handle(request)
        tmp = request.with_suffix(".done.tmp")
        tmp.write_text(json.dumps(result), "utf-8")
        os.replace(tmp, request.with_suffix(".done"))
        request.unlink(missing_ok=True)
        print(request.name, "added" if result["ok"] else result["error"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
