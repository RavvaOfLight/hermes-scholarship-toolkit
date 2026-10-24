#!/usr/bin/env python3
"""Weekly page watch for awards that are not open yet (no model in the loop).

For every award whose application has not opened, has no deadline, is an unverified
lead, or was missed by the latest search, re-read its official page through Hermes's
web_extract and pull out its facts: future opening and deadline dates and "applications
are now open" wording. Every fact ever seen on a page is remembered; a fact never seen
before marks the award "page updated" on the board and in the activity log. Comparing
facts rather than page text matters: extract backends return different amounts of text
for the same page from one call to the next.

Run (Hermes's venv provides the web tools; the systemd user timer does this weekly):
    ~/.hermes/hermes-agent/venv/bin/python watch.py [--dry-run]
Data: BOARD_DATA_DIR (default ~/.hermes/scholarship-board), the folder server.py reads.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

DATA_DIR = Path(os.environ.get("BOARD_DATA_DIR") or Path.home() / ".hermes" / "scholarship-board").expanduser()
REGISTRY = DATA_DIR / "trackers.json"
STORES = DATA_DIR / "stores"
LOCK_FILE = STORES / ".write.lock"   # same lock file as server.py
STATE = DATA_DIR / "watch-state.json"    # every fact seen per URL
LATEST = DATA_DIR / "watch-latest.json"  # summary the board shows
HERMES_AGENT = Path(os.environ.get("HERMES_AGENT_DIR", Path.home() / ".hermes" / "hermes-agent"))
CHUNK = 5
PASSES = 2          # each run reads every page twice and pools the facts (backends vary per call)
LEARNING_CHECKS = 2  # a page's first checks only learn its facts; after that a new fact is a change
WAITING_STATUSES = {"not_open_yet", "cycle_unknown"}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
DATE_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
    r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b"
    r"|\b(\d{1,2})/(\d{1,2})/(20\d{2})\b|\b(20\d{2})-(\d{2})-(\d{2})\b",
    re.IGNORECASE,
)
OPENS_RE = re.compile(r"\bopen(?:s|ing)?\b|available|\bbegin|\bstart|launch", re.IGNORECASE)
DEADLINE_RE = re.compile(r"deadline|\bdue\b|clos(?:e|es|ing)\b|submit|postmark|received by|no later than|apply by|\buntil\b", re.IGNORECASE)
OPEN_NOW_RE = re.compile(
    r"applications?\s+(?:are|is)\s+now\s+(?:open|available|being\s+accepted)|now\s+accepting\s+applications|"
    r"application\s+(?:portal\s+|period\s+)?is\s+(?:now\s+)?open\b|\bapply\s+now\b",
    re.IGNORECASE,
)


@contextmanager
def store_lock():
    STORES.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _write(path: Path, data: object) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def normalize_url(url: str) -> str:
    return re.sub(r"#.*$", "", url.strip()).rstrip("/").casefold()


def _quote(text: str, start: int, end: int) -> str:
    left = max(text.rfind("\n", 0, start) + 1, start - 110, 0)
    right = text.find("\n", end)
    right = min(right if right != -1 else len(text), end + 110)
    return " ".join(LINK_RE.sub(r"\1", text[left:right]).split())[:220]


def _date_kind(text: str, start: int, end: int) -> str | None:
    """'opens' or 'deadline' from the nearest keyword before the date (else just after it)."""
    best: tuple[str, int] | None = None
    before = text[max(0, start - 90):start]
    for kind, pattern in (("deadline", DEADLINE_RE), ("opens", OPENS_RE)):
        hits = list(pattern.finditer(before))
        if hits and (best is None or hits[-1].end() > best[1]):
            best = (kind, hits[-1].end())
    if best:
        return best[0]
    after = text[end:end + 50]
    for kind, pattern in (("deadline", DEADLINE_RE), ("opens", OPENS_RE)):
        hit = pattern.search(after)
        if hit and (best is None or hit.start() < best[1]):
            best = (kind, hit.start())
    return best[0] if best else None


def find_dates(text: str, today: date) -> list[dict]:
    """Future opening or deadline dates stated on the page (a date without a year is kept as such)."""
    found: dict[tuple[str, str], dict] = {}
    for match in DATE_RE.finditer(text):
        g = match.groups()
        year_stated = True
        try:
            if g[0]:
                month, day = MONTHS[g[0][:3].casefold()], int(g[1])
                year_stated = bool(g[2])
                when = date(int(g[2]) if g[2] else today.year, month, day)
            elif g[3]:
                when = date(int(g[5]), int(g[3]), int(g[4]))
            else:
                when = date(int(g[6]), int(g[7]), int(g[8]))
        except ValueError:
            continue
        if year_stated and not today <= when <= today + timedelta(days=450):
            continue
        kind = _date_kind(text, match.start(), match.end())
        if not kind:
            continue
        label = when.isoformat() if year_stated else f"{when:%B} {when.day} (no year on the page)"
        found.setdefault((kind, label), {"kind": kind, "date": label, "quote": _quote(text, match.start(), match.end()),
                                         "md": (when.month, when.day), "year": year_stated})
    dated = {item["md"] for item in found.values() if item["year"]}
    # a date the page also gives with its year elsewhere adds nothing without it
    kept = [item for item in found.values() if item["year"] or item["md"] not in dated]
    return [{k: item[k] for k in ("kind", "date", "quote")} for item in kept][:4]


def watchable(row: dict) -> bool:
    if row.get("status") in ("rejected", "accepted"):
        return False
    return bool(row.get("appStatus") in WAITING_STATUSES or not row.get("deadline")
                or row.get("lead") or row.get("missingSince"))


def watch_url(row: dict) -> str:
    for key in ("url", "leadUrl", "appUrl"):
        value = str(row.get(key) or "")
        if value.startswith("https://"):
            return value
    return ""


def hermes_fetch(urls: list[str]) -> dict[str, str]:
    """Page text by URL through Hermes's configured extract backend (no model)."""
    sys.path.insert(0, str(HERMES_AGENT))
    from tools.web_tools import web_extract_tool  # noqa: E402 - needs Hermes's venv

    pages: dict[str, str] = {}
    for start in range(0, len(urls), CHUNK):
        chunk = urls[start:start + CHUNK]
        try:
            payload = json.loads(asyncio.run(web_extract_tool(chunk, char_limit=40000)))
        except Exception as exc:  # noqa: BLE001 - one bad chunk must not stop the watch
            print(f"fetch failed for {len(chunk)} pages: {exc}", file=sys.stderr)
            continue
        wanted = {normalize_url(u): u for u in chunk}
        for row in payload.get("results", []):
            original = wanted.get(normalize_url(str(row.get("url", ""))))
            if original and row.get("content") and not row.get("error"):
                pages[original] = str(row["content"])
    return pages


STATUS_TEXT = {"changed": "page updated", "baseline": "now watched", "same": "no change",
               "unreadable": "page could not be read"}


def summarize(status: str, found: list[dict], open_signal: str) -> str:
    parts = [STATUS_TEXT[status]]
    for item in found:
        parts.append(f"{'deadline' if item['kind'] == 'deadline' else 'opens'} {item['date']}")
    if open_signal:
        parts.append("says applications are open")
    return "; ".join(parts)


def run_watch(fetch: Callable[[list[str]], dict[str, str]] = hermes_fetch, *, today: date | None = None,
              dry_run: bool = False) -> dict:
    today = today or date.today()
    registry = json.loads(REGISTRY.read_text("utf-8")) if REGISTRY.exists() else []
    tracker_ids = [t["id"] for t in registry if (STORES / f"{t['id']}.json").exists()]
    targets: dict[str, dict] = {}
    for tid in tracker_ids:
        for row in json.loads((STORES / f"{tid}.json").read_text("utf-8")).get("scholarships", []):
            url = watch_url(row)
            if url and watchable(row):
                target = targets.setdefault(url, {"names": [], "ids": set()})
                target["ids"].add(row["id"])
                if row["name"] not in target["names"]:
                    target["names"].append(row["name"])
    for target in targets.values():  # several awards can share one page (a foundation's portal)
        extra = len(target["names"]) - 1
        target["name"] = target["names"][0] + (f" (+{extra} more on the same page)" if extra else "")
    pages: dict[str, str] = {}
    for _ in range(PASSES if targets else 0):
        for url, text in fetch(sorted(targets)).items():
            pages[url] = pages.get(url, "") + "\n\n" + text
    state = json.loads(STATE.read_text("utf-8")) if STATE.exists() else {}
    results: dict[str, dict] = {}
    for url, info in targets.items():
        prev = state.get(url) or {}
        known: dict[str, dict] = prev.get("facts") or {}
        text = pages.get(url)
        new_keys: list[str] = []
        open_signal = ""
        if text:
            for item in find_dates(text, today):
                key = f"{item['kind']}:{item['date']}"
                if key not in known:
                    known[key] = {**item, "first_seen": today.isoformat()}
                    new_keys.append(key)
            hit = OPEN_NOW_RE.search(text)
            if hit:
                open_signal = _quote(text, hit.start(), hit.end())
                if "open-now" not in known:
                    known["open-now"] = {"kind": "open-now", "quote": open_signal, "first_seen": today.isoformat()}
                    new_keys.append("open-now")
        # dated facts that have passed are dropped; a date without a year stays until replaced
        known = {k: v for k, v in known.items()
                 if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v.get("date", ""))) or v["date"] >= today.isoformat()}
        checks = int(prev.get("checks", 0))
        if not text:
            status = "unreadable"
        elif checks < LEARNING_CHECKS:
            status = "baseline"
        else:
            status = "changed" if new_keys else "same"
        changed_on = today.isoformat() if status == "changed" else prev.get("changedOn", "")
        found = sorted((v for v in known.values() if v.get("kind") in ("opens", "deadline")),
                       key=lambda v: str(v.get("date")))[:4]
        results[url] = {"checked": today.isoformat(), "status": status, "found": found, "openSignal": open_signal,
                        "changedOn": changed_on, "url": url, "new": new_keys if status == "changed" else [],
                        "summary": summarize(status, [known[k] for k in new_keys if k != "open-now"] if status == "changed" else found,
                                             open_signal if (status != "changed" or "open-now" in new_keys) else "")}
        if text or prev:
            state[url] = {"facts": known, "checked": today.isoformat() if text else prev.get("checked", ""),
                          "changedOn": changed_on, "checks": checks + (1 if text else 0)}
    summary = {
        "ran_at": datetime.now().isoformat(timespec="minutes"),
        "awards_watched": sum(len(t["ids"]) for t in targets.values()),
        "pages_watched": len(targets),
        "readable": sum(1 for r in results.values() if r["status"] != "unreadable"),
        "unreadable": sorted(targets[u]["name"] for u, r in results.items() if r["status"] == "unreadable"),
        "changed": [{"name": targets[u]["name"], "summary": r["summary"]} for u, r in results.items() if r["status"] == "changed"],
        "found": [{"name": targets[u]["name"], "summary": r["summary"]} for u, r in results.items()
                  if r["found"] or r["openSignal"]],
    }
    if dry_run:
        return summary
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with store_lock():
        for tid in tracker_ids:
            path = STORES / f"{tid}.json"
            store = json.loads(path.read_text("utf-8"))
            touched = False
            for row in store.get("scholarships", []):
                url = watch_url(row)
                if url in results and row["id"] in targets[url]["ids"]:
                    row["watch"] = results[url]
                    touched = True
                    if results[url]["status"] == "changed" or (results[url]["status"] == "baseline"
                                                                and (results[url]["found"] or results[url]["openSignal"])):
                        store.setdefault("log", []).append(
                            {"at": stamp, "text": f"page watch · {row['name'][:44]}: {results[url]['summary']}"})
            if touched:
                _write(path, store)
        _write(STATE, state)
        _write(LATEST, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="fetch and compare, but write nothing")
    args = parser.parse_args()
    summary = run_watch(dry_run=args.dry_run)
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
