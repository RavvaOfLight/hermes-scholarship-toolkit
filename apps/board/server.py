#!/usr/bin/env python3
"""Scholarship board: a phone-friendly tracker for the awards a scholarship-research run found.

Run:  python3 server.py [port]          (default 8765, listens on 127.0.0.1 only)
Then: http://127.0.0.1:8765

Each tracker is one search with its own store (`stores/<id>.json`), listed in `trackers.json`;
add trackers with import.py. The page (app.html) reads one tracker with GET /api/board and
changes it with POST /api/<tool>: status marks, notes and checklist steps. No model is involved.

Data lives in BOARD_DATA_DIR (default ~/.hermes/scholarship-board), never next to the code.
Login: if BOARD_DATA_DIR/.auth.json exists (see set_password.py), every request needs HTTP Basic
auth. BOARD_AUTH=/path/to/file points at another file; BOARD_AUTH=none turns it off (local only).
To reach the board from a phone, put Tailscale in front of it (see docs/remote-access.md).
"""
from __future__ import annotations

import base64
import csv
import fcntl
import hashlib
import hmac
import io
import json
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("BOARD_DATA_DIR") or Path.home() / ".hermes" / "scholarship-board").expanduser()
REGISTRY = DATA_DIR / "trackers.json"
STORES = DATA_DIR / "stores"
_ENV_AUTH = os.environ.get("BOARD_AUTH")
AUTH = None if (_ENV_AUTH or "").lower() == "none" else Path(_ENV_AUTH) if _ENV_AUTH else DATA_DIR / ".auth.json"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else int(os.environ.get("BOARD_PORT") or 8765)
STATUSES = ["verify", "applied", "in progress", "accepted", "rejected"]
WRITE_LOCK = threading.Lock()  # one read-modify-write of a store at a time (two quick taps)
LOCK_FILE = STORES / ".write.lock"  # shared with watch.py, which runs as its own process
WATCH_LATEST = DATA_DIR / "watch-latest.json"
MAX_BODY = 64 * 1024
CHECK_CATEGORIES = (  # application steps grouped across awards (keyword match on the step text)
    ("rec", "Recommendation letters", r"recommend|reference|letters? of support"),
    ("nomination", "Nominations", r"nominat"),
    ("essay", "Essays and statements", r"essay|personal statement|short[- ]answer|written response|\bstatement\b|writing sample"),
    ("transcript", "Transcripts", r"transcript"),
    ("fafsa", "FAFSA", r"\bfafsa\b"),
    ("css", "CSS Profile", r"css profile"),
    ("scores", "Test scores", r"\bsat\b|\bact\b|test scores?"),
    ("resume", "Resume or activities list", r"r[eé]sum[eé]|activit(?:y|ies) (?:list|sheet|résumé)|extracurricular"),
    ("interview", "Interviews", r"interview"),
    ("financial", "Financial documents", r"tax return|\bincome\b|financial (?:info|information|documents?|statement)|\bw-?2\b"),
    ("project", "Projects and portfolios", r"portfolio|research (?:paper|report|project)|\bproject\b"),
    ("admission", "College applications", r"apply (?:to|for admission)|admissions? application|common app|application for admission"),
)


@contextmanager
def store_lock():
    """Serialize store writes across threads and processes (the server and watch.py)."""
    with WRITE_LOCK:
        STORES.mkdir(parents=True, exist_ok=True)
        with open(LOCK_FILE, "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def task_key(text: str) -> str:
    return hashlib.sha1(re.sub(r"\s+", " ", str(text).strip().casefold()).encode("utf-8")).hexdigest()[:10]


def task_categories(text: str) -> list[str]:
    return [key for key, _label, pattern in CHECK_CATEGORIES if re.search(pattern, text, re.IGNORECASE)]


def checklist(row: dict) -> list[dict]:
    """The award's application steps (from the report plus your own), with done state."""
    done = row.get("taskDone") or {}
    items, seen = [], set()
    steps = [(task_key(t), str(t).strip(), False) for t in row.get("tasks") or [] if str(t).strip()]
    steps += [(c.get("key", ""), str(c.get("text", "")).strip(), True) for c in row.get("customTasks") or []]
    for key, text, custom in steps:
        if not key or not text or key in seen:
            continue
        seen.add(key)
        items.append({"key": key, "text": text, "custom": custom, "done": key in done,
                      "doneOn": done.get(key, ""), "cats": task_categories(text)})
    return items

MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(million|k\b)?", re.I)
# a figure is a program total or an income cap, not one student's award, when its context says so
AGGREGATE_AFTER = re.compile(r"^\W{0,3}(total(?:ing)? to|awarded|in (?:\w+\s+){0,2}(?:scholarships|awards|aid)|to \d[\d,]* students|since|"
                             r"program-wide|/year program)", re.I)
AGGREGATE_BEFORE = re.compile(r"(famil(?:y|ies)[^$;]{0,30}|less than|under|income[^$;]{0,20}|totaling|~|"
                              r"program-wide)\s*$", re.I)


def award_max(text: str) -> int:
    """Largest single-student figure in an amount text (0 when only totals or nothing is stated).
    A million dollars or more is always a program total, never one student's award."""
    text = text or ""
    best = 0
    for m in MONEY.finditer(text):
        v = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").lower()
        v *= 1_000_000 if unit == "million" else 1000 if unit == "k" else 1
        if v >= 1_000_000 or AGGREGATE_AFTER.search(text[m.end():m.end() + 40]) or \
                AGGREGATE_BEFORE.search(text[max(0, m.start() - 40):m.start()]):
            continue
        best = max(best, int(v))
    return best


def days_left(r: dict) -> int | None:
    """Days until the deadline, counted today (the stored daysLeft is frozen at import time)."""
    try:
        return (date.fromisoformat(r.get("deadline") or "") - date.today()).days
    except ValueError:  # missing or not an ISO date
        return None


def csv_safe(v) -> str:
    """Flatten newlines/tabs and defuse Excel formula injection (=, +, -, @)."""
    s = str(v if v is not None else "")
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return ("'" + s) if s[:1] in ("=", "+", "-", "@") else s


def short(s, n: int = 96) -> str:
    s = str(s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def registry() -> list[dict]:
    items = json.loads(REGISTRY.read_text("utf-8")) if REGISTRY.exists() else []
    return sorted(items, key=lambda t: str(t.get("label", "")))


def _arg(args: dict, key: str) -> str:
    value = args.get(key) or ""
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    return value.strip()


def resolve(args: dict) -> tuple[str, dict, dict]:
    items = registry()
    if not items:
        raise ValueError("no trackers yet: add a search with import.py --run DIR (or --evaluated FILE, --pdf FILE)")
    tid = _arg(args, "tracker")
    meta = next((t for t in items if t["id"] == tid), None)
    if meta is None:
        # unknown/stale tracker id: fall back to the first, but say so in the header label
        meta = dict(items[0])
        if tid:
            meta["label"] = f'{items[0]["label"]} · (unknown tracker "{tid}" — showing first)'
    path = STORES / f"{meta['id']}.json"
    if not path.exists():
        raise ValueError(f"store missing for tracker {meta['id']}")
    return meta["id"], json.loads(path.read_text("utf-8")), meta


def persist(tid: str, store: dict) -> None:
    path = STORES / f"{tid}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, indent=1), "utf-8")
    os.replace(tmp, path)  # never leave a half-written store behind


def tracker_rows() -> list[dict]:
    out = []
    for t in registry():
        try:
            store = json.loads((STORES / f"{t['id']}.json").read_text("utf-8"))
            rows = store["scholarships"]
            marked = sum(1 for r in rows if r.get("status") != "verify")
            notes = sum(1 for r in rows if r.get("note"))
        except (FileNotFoundError, KeyError):
            rows, marked, notes = [], 0, 0
        out.append({"id": t["id"], "label": t["label"],
                    "meta": f"{len(rows)} awards · {marked} marked · {notes} notes",
                    "opt": {"value": t["id"], "label": t["label"]}})
    return out


def set_status(args: dict) -> dict:
    with store_lock():
        return _set_status(args)


def _set_status(args: dict) -> dict:
    tid, store, meta = resolve(args)
    rid = _arg(args, "id")
    status = _arg(args, "status")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    row = next((x for x in store["scholarships"] if x["id"] == rid), None)
    if not row:
        raise ValueError(f"unknown scholarship id for tracker {tid}: {rid}")
    prev = row.get("status")
    row["status"] = status
    store.setdefault("log", []).append({"at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                        "text": f"{short(row['name'], 44)}: {prev} → {status}"})
    persist(tid, store)
    return {"ok": True, "id": rid, "status": status, "previous": prev, "tracker": tid}


def add_note(args: dict) -> dict:
    with store_lock():
        return _add_note(args)


def _add_note(args: dict) -> dict:
    tid, store, meta = resolve(args)
    rid = _arg(args, "id")
    note = _arg(args, "note")
    row = next((x for x in store["scholarships"] if x["id"] == rid), None)
    if not row:
        raise ValueError(f"unknown scholarship id for tracker {tid}: {rid}")
    row["note"] = note
    store.setdefault("log", []).append({"at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                        "text": f"note on {short(row['name'], 40)}: {short(note, 60) or '(cleared)'}"})
    persist(tid, store)
    return {"ok": True, "id": rid, "note": note, "tracker": tid}


def _row(store: dict, tid: str, rid: str) -> dict:
    row = next((x for x in store["scholarships"] if x["id"] == rid), None)
    if not row:
        raise ValueError(f"unknown scholarship id for tracker {tid}: {rid}")
    return row


def _log(store: dict, text: str) -> None:
    store.setdefault("log", []).append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "text": text})


def toggle_task(args: dict) -> dict:
    with store_lock():
        tid, store, meta = resolve(args)
        row = _row(store, tid, _arg(args, "id"))
        key = _arg(args, "key")
        item = next((i for i in checklist(row) if i["key"] == key), None)
        if not item:
            raise ValueError("unknown checklist step")
        done = row.setdefault("taskDone", {})
        if args.get("done") in (True, "true", "1", 1):
            done[key] = date.today().isoformat()
            _log(store, f"✓ {short(row['name'], 36)}: {short(item['text'], 60)}")
        else:
            done.pop(key, None)
            _log(store, f"unchecked {short(row['name'], 32)}: {short(item['text'], 60)}")
        persist(tid, store)
        return {"ok": True, "id": row["id"], "key": key, "done": key in done}


def add_task(args: dict) -> dict:
    text = re.sub(r"\s+", " ", str(args.get("text") or "")).strip()
    if not 2 <= len(text) <= 200:
        raise ValueError("a step needs 2 to 200 characters")
    with store_lock():
        tid, store, meta = resolve(args)
        row = _row(store, tid, _arg(args, "id"))
        custom = row.setdefault("customTasks", [])
        if len(custom) >= 20:
            raise ValueError("at most 20 added steps per award")
        key = "c" + task_key(f"{text}|{time.time()}")[:9]
        custom.append({"key": key, "text": text})
        _log(store, f"step added to {short(row['name'], 36)}: {short(text, 60)}")
        persist(tid, store)
        return {"ok": True, "id": row["id"], "key": key}


def remove_task(args: dict) -> dict:
    with store_lock():
        tid, store, meta = resolve(args)
        row = _row(store, tid, _arg(args, "id"))
        key = _arg(args, "key")
        custom = row.get("customTasks") or []
        kept = [c for c in custom if c.get("key") != key]
        if len(kept) == len(custom):
            raise ValueError("only steps you added can be removed")
        row["customTasks"] = kept
        (row.get("taskDone") or {}).pop(key, None)
        _log(store, f"step removed from {short(row['name'], 36)}")
        persist(tid, store)
        return {"ok": True, "id": row["id"], "key": key}


def watch_summary() -> dict:
    try:
        data = json.loads(WATCH_LATEST.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


AWARD_FIELDS = {"id": "", "name": "", "sponsor": "", "status": "verify", "deadline": None,
                "deadlineLabel": "Not announced", "amount": "", "appStatus": "", "eligibility": "",
                "why": "", "requirements": "", "tasks": [], "uncertainties": [], "url": "", "appUrl": "",
                "verifiedOn": "", "note": "", "lead": False, "leadUrl": "", "whyUnverified": "",
                "verified": False, "notes": [], "changes": [], "missingSince": "", "newSince": "",
                "watch": None}


def board(args: dict) -> dict:
    """Everything the phone board (app.html) shows for one tracker, in one call."""
    if not _arg(args, "tracker"):  # first visit: the full search, not a slice of it
        full = [t for t in registry() if not str(t.get("source", "")).startswith("subset of")]
        args = {**args, "tracker": full[0]["id"] if full else ""}
    tid, store, meta = resolve(args)
    awards = []
    for r in store["scholarships"]:
        a = {k: d if r.get(k) is None else r[k] for k, d in AWARD_FIELDS.items()}
        a["deadline"] = a["deadline"] if days_left(r) is not None else None  # the page trusts it as ISO
        a["amountMax"] = award_max(str(a["amount"]))
        a["checklist"] = checklist(r)
        a["watch"] = a["watch"] or {}
        awards.append(a)
    prof = store.get("profile") or {}
    return {"today": date.today().isoformat(),
            "tracker": {"id": tid, "label": meta["label"], "source": meta.get("source", ""),
                        "imported": store.get("imported", "")},
            "trackers": tracker_rows(), "awards": awards, "statuses": STATUSES,
            "categories": [{"key": key, "label": label} for key, label, _pattern in CHECK_CATEGORIES],
            "watch": watch_summary(),
            "log": store.get("log", [])[-50:][::-1],
            "profile": {"source": prof.get("source", ""),  # fields (GPA, scores) stay off the wire
                        "tags": prof.get("tags") or [], "notes": prof.get("notes") or []}}


def ics_text(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_fold(line: str) -> str:
    """RFC 5545: lines over 75 octets continue on the next line after a space."""
    b, out, limit = line.encode("utf-8"), [], 75
    while len(b) > limit:
        cut = limit
        while (b[cut] & 0xC0) == 0x80:  # never split a UTF-8 character
            cut -= 1
        out.append(b[:cut].decode("utf-8"))
        b, limit = b[cut:], 74
    out.append(b.decode("utf-8"))
    return "\r\n ".join(out)


def deadlines_ics(args: dict) -> str:
    """Dated deadlines as all-day events, reminders 7 days and 1 day before at 9:00."""
    tid, store, meta = resolve(args)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//scholarship-board//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:Scholarship deadlines"]
    for r in store["scholarships"]:
        dl = days_left(r)
        if dl is None or dl < 0 or r.get("status") == "rejected":  # only what is still ahead
            continue
        d = date.fromisoformat(r["deadline"])
        desc = "\n".join(x for x in [r.get("amount", ""), f"Status: {r.get('status', 'verify')}",
                                      r.get("url", "")] if x)
        lines += ["BEGIN:VEVENT", f"UID:{r['id']}@{tid}.scholarships", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
                  f"SUMMARY:{ics_text('Due: ' + r['name'])}", f"DESCRIPTION:{ics_text(desc)}"]
        if r.get("url"):
            lines.append(f"URL:{r['url']}")
        for trig, when in (("-P6DT15H", "in 7 days"), ("-PT15H", "tomorrow")):
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{ics_text(r['name'] + ' is due ' + when)}",
                      f"TRIGGER:{trig}", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(ics_fold(x) for x in lines) + "\r\n"


def export_csv(args: dict) -> bytes:
    """One tracker as a spreadsheet, soonest deadline first (UTF-8 with BOM so Excel reads it)."""
    tid, store, meta = resolve(args)
    rows = sorted(store["scholarships"], key=lambda r: (days_left(r) is None, days_left(r) or 0, str(r.get("name", ""))))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Award", "Status", "Deadline", "Days left", "Max $", "Sponsor", "Official page", "Note"])
    for r in rows:
        days = days_left(r)
        w.writerow([csv_safe(x) for x in (r.get("name"), r.get("status", "verify"), r.get("deadlineLabel", "Not announced"),
                                          "" if days is None else days, award_max(r.get("amount", "")),
                                          r.get("sponsor"), r.get("url"), r.get("note"))])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


READ_TOOLS = {"board": board}
WRITE_TOOLS = {"set_status": set_status, "add_note": add_note,
               "toggle_task": toggle_task, "add_task": add_task, "remove_task": remove_task}


_AUTH_OK: dict[str, float] = {}  # sha256 of an accepted header -> login file mtime it matched


# Headers a proxy adds. Tailscale Serve and Funnel set X-Forwarded-For; Funnel also marks its requests.
PROXY_HEADERS = ("X-Forwarded-For", "Forwarded", "Tailscale-Funnel-Request", "Tailscale-User-Login")
NO_PASSWORD = ("This board has no password, so it only opens on this computer.\n"
               "Set one first (python3 set_password.py YOUR_NAME), then reload this page.\n")


def check_auth(header: str | None) -> bool:
    if not AUTH or not AUTH.exists():
        return True
    if not header or not header.startswith("Basic "):
        return False
    mtime = AUTH.stat().st_mtime
    seen = hashlib.sha256(header.encode("utf-8", "replace")).hexdigest()
    if _AUTH_OK.get(seen) == mtime:  # the slow hash runs once per login, not on every request
        return True
    try:
        user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except Exception:
        return False
    a = json.loads(AUTH.read_text("utf-8"))
    got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(a["salt"]), a["iterations"])
    ok = hmac.compare_digest(user, a["user"]) & hmac.compare_digest(got.hex(), a["hash"])
    if ok:
        if len(_AUTH_OK) > 32:
            _AUTH_OK.clear()
        _AUTH_OK[seen] = mtime
    return ok


class Handler(BaseHTTPRequestHandler):
    def _authorized(self) -> bool:
        if (not AUTH or not AUTH.exists()) and any(self.headers.get(h) for h in PROXY_HEADERS):
            # reached through Tailscale Serve/Funnel (or another proxy) with no password set: refuse
            self._send(403, NO_PASSWORD.encode(), "text/plain; charset=utf-8")
            return False
        if check_auth(self.headers.get("Authorization")):
            return True
        time.sleep(1)  # slow down password guessing
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="scholarships", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, message: str) -> None:
        self._send(code, json.dumps({"error": message}).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        u = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/":
                self._send(200, (HERE / "app.html").read_bytes(), "text/html; charset=utf-8")
            elif u.path == "/api/deadlines.ics":
                self._send(200, deadlines_ics(qs).encode("utf-8"), "text/calendar; charset=utf-8",
                           {"Content-Disposition": 'attachment; filename="scholarship-deadlines.ics"'})
            elif u.path == "/api/export.csv":
                self._send(200, export_csv(qs), "text/csv; charset=utf-8",
                           {"Content-Disposition": 'attachment; filename="scholarships.csv"'})
            elif u.path.startswith("/api/"):
                name = unquote(u.path[len("/api/"):])
                if name in WRITE_TOOLS:  # changes only by POST, so a link or an image can't make one
                    self._error(405, "use POST")
                elif name in READ_TOOLS:
                    self._send(200, json.dumps(READ_TOOLS[name](qs)).encode(), "application/json")
                else:
                    self._error(404, "tool-not-found")
            else:
                self._send(404, b"not found", "text/plain")
        except ValueError as exc:  # bad tracker, award or argument: tell the page why
            self._error(400, str(exc))
        except Exception as exc:  # noqa: BLE001 - a broken store file must not kill the request thread
            self.log_message("error on %s: %r", u.path, exc)
            self._error(500, "server error (see the server log)")

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        name = unquote(urlparse(self.path).path[len("/api/"):]) if self.path.startswith("/api/") else ""
        if name not in WRITE_TOOLS:
            self._error(404, "tool-not-found")
            return
        # Another site can't send JSON here: a cross-origin JSON POST needs a CORS preflight,
        # which this server never grants. Browsers also label cross-site requests.
        if self.headers.get_content_type() != "application/json" or \
                self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            self._error(403, "requests must come from the board page")
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if not 0 <= n <= MAX_BODY:
            self._error(413, "request too large")
            return
        try:
            args = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(args, dict):
                raise ValueError("expected a JSON object")
            out = WRITE_TOOLS[name](args)
        except (ValueError, TypeError) as exc:  # json.JSONDecodeError is a ValueError
            self._error(400, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.log_message("error on %s: %r", name, exc)
            self._error(500, "server error (see the server log)")
            return
        self._send(200, json.dumps(out).encode(), "application/json")

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    auth_on = bool(AUTH and AUTH.exists())
    print(f"scholarship board -> http://127.0.0.1:{PORT}  ({len(registry())} trackers, data in {DATA_DIR}, "
          f"login {'ON' if auth_on else 'OFF: run set_password.py before sharing it beyond this computer'})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
