#!/usr/bin/env python3
"""Import scholarship searches into the dashboard as selectable trackers.

Trackers live in trackers.json; each has its own store file stores/<id>.json, so marks, notes and
the action log stay separate per search. Three importers, all reading real files on disk:

  --pdf PATH            a generated report PDF (parsed text sections)
  --run DIR             a scholarship-research run workspace; reads its evaluated.json (the
                        skill's structured output: verified flag, page-check notes, leads)
  --evaluated FILE      an evaluated.json file directly
  --subset KEYWORDS     scope an existing tracker down by keywords into a new tracker
                        (subset = a scoped view of the same search, never invented rows)

Examples
  python3 import.py --run ~/scholarship-runs/run-01                     # a finished run workspace
  python3 import.py --evaluated path/to/evaluated.json --id fall-2026
  python3 import.py --pdf scholarship-report.pdf --label "Report · all matches"
  python3 import.py --subset "state university, community college" --from fall-2026 \
      --id in-state --label "In-state colleges"

Data goes to BOARD_DATA_DIR (default ~/.hermes/scholarship-board), the folder server.py reads.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path

DATA_DIR = Path(os.environ.get("BOARD_DATA_DIR") or Path.home() / ".hermes" / "scholarship-board").expanduser()
REGISTRY = DATA_DIR / "trackers.json"
STORES = DATA_DIR / "stores"
LOCK_FILE = STORES / ".write.lock"  # same lock as server.py and watch.py
TODAY = date.today()

KEYS = ["Sponsor", "Amount", "Deadline", "Application status", "Eligibility", "Why it matches",
        "Requirements", "Application tasks", "Uncertainties", "Official source", "Source authority",
        "Application", "Verified on", "Evidence", "Why unverified", "Lead/source"]
# the PDF repeats this header on every page; left in, it leaks into whichever field crosses a page break
PAGE_FURNITURE = re.compile(r"\n\s*Page \d+\s*\n\s*Scholarship Report - generated [^\n]*")

# Profile tags for a PDF import, read from the report's own "why it matches" text. Generic on
# purpose: nothing here names a place, school or person.
SIGNALS = [
    ("high school senior", r"\bsenior\b|high school senior|final year of secondary"),
    ("graduating class", r"class of 20\d\d|graduating (?:senior|class)"),
    ("state resident", r"\bresidents? of\b|\bin-state\b|state resident"),
    ("U.S. citizen", r"u\.?s\.? citizen|united states citizen"),
    ("first-generation", r"first[- ]generation"),
    ("financial need", r"financial need|need[- ]based|unmet need|family gross income"),
    ("STEM field", r"engineering|science, math|\bstem\b|computer science|manufacturing"),
    ("research project", r"independent research|research report|research project"),
    ("community service / leadership", r"community service|leadership|volunteer"),
    ("athletic / arts talent", r"athletic|artistic|music|arts talent"),
    ("military / service family", r"military|veteran|guard|reservist"),
    ("dual enrollment / college credit", r"dual[- ]enroll|college credit|credit hours"),
]


def clean(s: str) -> str:
    s = re.sub(r"[\x00-\x20\x7f]", " ", (s or "").replace("\\", "").replace("*", ""))
    return re.sub(r"\s+", " ", s).strip().strip(" -•")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:64]


def money(text: str) -> int | None:
    vals = [int(n.replace(",", "")) for n in re.findall(r"\$\s?([\d,]+)", text)]
    return max(vals) if vals else None


def links(text: str) -> list[str]:
    """URLs from a field. The report wraps them in <...>; a long one can break across lines."""
    urls = [re.sub(r"\s+", "", u) for u in re.findall(r"<\s*(https?://[^>]+?)\s*>", text or "")]
    return urls or [u.rstrip(").,") for u in re.findall(r"https?://[^\s>\)\]]+", text or "")]


def split_values(block: str) -> dict[str, str]:
    pat = "|".join(re.escape(k) for k in KEYS)
    hits = [(m.start(), m.group(1)) for m in re.finditer(rf"({pat})\s*:", block)]
    out: dict[str, str] = {}
    for i, (pos, key) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(block)
        out[key] = clean(block[pos + len(key) + 1:end])
    return out


# Facts compared between imports; a difference is recorded in the row's "changes".
TRACKED = ("deadline", "amount", "url", "appStatus", "eligibility", "lead", "verified")


def parse_evaluated(path: Path) -> list[dict]:
    """Rows from the skill's evaluated.json: verified, needs-confirmation, and lead records."""
    data = json.loads(path.read_text("utf-8"))
    rows, seen = [], set()
    for bucket in ("verified", "needs_confirmation", "omitted_due_to_limit", "unverified"):
        for it in data.get(bucket, []):
            if not isinstance(it, dict) or not it.get("name"):
                continue
            name = clean(str(it["name"]))
            rid = slug(name)
            if rid in seen:
                continue
            seen.add(rid)
            dl_obj = it.get("deadline") or {}
            iso = dl_obj.get("date")
            dl = date.fromisoformat(iso) if isinstance(iso, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso) else None
            kind = dl_obj.get("type") or ""
            amount = it.get("amount") or {}
            lead = bucket == "unverified"
            unsure = [clean(u) for u in it.get("uncertainties") or [] if clean(u)]
            rows.append(mk_row(
                id=rid, name=name, sponsor=clean(str(it.get("sponsor") or "")),
                amount=clean(str(amount.get("display") or "")), amountMax=amount.get("maximum") or 0,
                deadline=dl.isoformat() if dl else None,
                deadlineLabel=dl.isoformat() if dl else ("Rolling" if kind == "rolling" else "Not announced"),
                daysLeft=(dl - TODAY).days if dl else None, month=dl.strftime("%Y-%m") if dl else "none",
                appStatus=clean(str(it.get("status") or "open")),
                eligibility="eligible" if bucket == "verified" else clean(str(it.get("eligibility_state") or bucket)),
                why="; ".join(clean(str(x)) for x in it.get("match_reasons") or []),
                requirements="; ".join(clean(str(r.get("description", ""))) for r in it.get("requirements") or [] if isinstance(r, dict)),
                tasks=[clean(str(t)) for t in it.get("application_tasks") or [] if clean(str(t))],
                uncertainties=unsure, openItems=len(unsure),
                url="" if lead else str(it.get("official_url") or ""), appUrl=str(it.get("application_url") or ""),
                verifiedOn=str(it.get("verified_at") or ""), lead=lead,
                leadUrl=str(it.get("official_url") or "") if lead else "",
                whyUnverified="; ".join(clean(str(x)) for x in it.get("validation_notes") or []) if lead else "",
                verified=bucket == "verified",
                notes=[clean(str(n)) for n in it.get("notes") or [] if clean(str(n))],
            ))
    return rows


def mk_row(**kw) -> dict:
    row = {"status": "verify", "note": "", "tasks": [], "openItems": 0, "url": "", "appUrl": "",
           "eligibility": "", "why": "", "requirements": "", "verifiedOn": "", "appStatus": "open",
           "amountMax": 0, "amount": "", "daysLeft": None, "uncertainties": [],
           "lead": False, "leadUrl": "", "whyUnverified": "", "verified": False, "notes": []}
    row.update(kw)
    return row


def build_row(name: str, f: dict[str, str], lead: bool = False) -> dict | None:
    iso = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", f.get("Deadline", ""))
    dl = date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))) if iso else None
    label = dl.isoformat() if dl else ("Rolling" if "rolling" in f.get("Deadline", "").lower()
                                       else "Not announced")
    urls = links(f.get("Official source", ""))
    app_urls = links(f.get("Application", ""))
    # items end with "." and are joined by ";" — a bare ";" also occurs inside an item
    unsure = [clean(u) for u in re.split(r"(?<=\.)\s*;\s*", f.get("Uncertainties", "")) if clean(u)]
    return mk_row(
        lead=lead, leadUrl=(links(f.get("Lead/source", "")) or [""])[0],
        whyUnverified=f.get("Why unverified", ""), uncertainties=unsure,
        id=slug(name), name=name,
        sponsor=f.get("Sponsor", ""), amount=f.get("Amount", ""), amountMax=money(f.get("Amount", "")) or 0,
        deadline=dl.isoformat() if dl else None, deadlineLabel=label,
        daysLeft=(dl - TODAY).days if dl else None,
        month=dl.strftime("%Y-%m") if dl else "none",
        appStatus=f.get("Application status", "open"), eligibility=f.get("Eligibility", ""),
        why=f.get("Why it matches", ""), requirements=f.get("Requirements", ""),
        tasks=[clean(t) for t in re.split(r";\s+", f.get("Application tasks", "")) if clean(t)],
        openItems=len(unsure),
        url=urls[0] if urls else "", appUrl=app_urls[0] if app_urls else "",
        verifiedOn=f.get("Verified on", ""),
    )


def parse_pdf(path: Path) -> tuple[list[dict], dict]:
    import pypdf  # only PDF imports need it: python3 -m pip install pypdf

    reader = pypdf.PdfReader(str(path))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    text = PAGE_FURNITURE.sub("", text).split("\nExclusion summary")[0]
    leads_at = text.find("\nUnverified leads\n")
    items = list(re.finditer(r"\n\s*(\d{1,2})\.\s+", text))
    rows, seen = [], set()
    for i, m in enumerate(items):
        body = text[m.end():items[i + 1].start() if i + 1 < len(items) else len(text)]
        name = clean(body.splitlines()[0]) if body.strip() else ""
        if "Sponsor:" not in body.replace(" ", "") or len(name) < 4:
            continue
        f = split_values(body)
        r = build_row(name, f, lead=0 <= leads_at < m.start())
        if r and r["id"] not in seen:
            seen.add(r["id"])
            rows.append(r)
    haystack = " ".join(" ".join([r["why"], r["eligibility"], r["requirements"]]) for r in rows)
    signals = []
    for label, pat in SIGNALS:
        n = len(re.findall(pat, haystack, flags=re.I))
        if n:
            signals.append({"left": label, "right": f"{n} match reason(s)"})
    profile = {
        "source": f"profile signals read out of the report's match reasoning ({path.name})",
        "fields": [{"left": "Report", "right": path.name},
                   {"left": "Awards in this search", "right": str(len(rows))}] + signals,
        "tags": [s["left"] for s in signals][:8],
        "notes": ["Profile facts come from the report's own 'why it matches' / eligibility reasoning.",
                  "Import a run workspace (--run) to attach that run's real profile.json snapshot."],
    }
    return rows, profile


def parse_run(d: Path) -> tuple[list[dict], dict]:
    """A scholarship-research run workspace: its evaluated.json, else its results.csv export."""
    rows: list[dict] = []
    if (d / "evaluated.json").exists():
        rows = parse_evaluated(d / "evaluated.json")
    elif (d / "results.csv").exists():
        with (d / "results.csv").open(newline="", encoding="utf-8") as handle:
            for rec in csv.DictReader(handle):
                name = clean(rec.get("name") or rec.get("Name") or rec.get("scholarship") or "")
                if not name:
                    continue
                f = {k: clean(v) for k, v in rec.items() if k and v}
                rows.append(build_row(name, {"Amount": f.get("amount", ""), "Sponsor": f.get("sponsor", ""),
                                             "Deadline": f.get("deadline", ""), "Eligibility": f.get("eligibility", ""),
                                             "Why it matches": f.get("why", ""), "Official source": f.get("url", "")}))
    prof = {}
    try:
        prof = json.loads((d / "profile.json").read_text("utf-8"))
    except (OSError, ValueError):
        pass
    st = (prof.get("student") or {}) if isinstance(prof, dict) else {}
    # The board can be opened from a phone, so it keeps no profile details beyond two tags.
    tags = [clean(str(t)) for t in ((st.get("location") or {}).get("state"), st.get("intended_major")) if t]
    profile = {"source": f"run workspace ({d.name})", "fields": [], "tags": tags[:8],
               "notes": ["Imported from the run's evaluated results (verified flags and page checks included)."]}
    return rows, profile


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


def load_registry() -> list[dict]:
    return json.loads(REGISTRY.read_text("utf-8")) if REGISTRY.exists() else []


def write_store(tid: str, label: str, rows: list[dict], profile: dict, source: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", tid):
        raise SystemExit(f"tracker id must be lowercase letters, digits and dashes: {tid!r}")
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with store_lock():
        return _write_store(tid, label, rows, profile, source)


def _write_store(tid: str, label: str, rows: list[dict], profile: dict, source: str) -> Path:
    p = STORES / f"{tid}.json"
    log: list = []
    if p.exists():  # re-import: refresh facts, keep marks/notes/log, record changes, keep missed rows
        old = json.loads(p.read_text("utf-8"))
        marks = {r["id"]: r for r in old.get("scholarships", [])}
        today = TODAY.isoformat()
        for r in rows:
            prev = marks.get(r["id"])
            if prev is None:
                r["newSince"] = today
                continue
            r["status"] = prev.get("status", "verify")
            r["note"] = prev.get("note", "")
            for kept in ("taskDone", "customTasks", "watch"):  # your checklist and the page watch
                if prev.get(kept):
                    r[kept] = prev[kept]
            changes = [{"field": f, "old": prev.get(f), "new": r.get(f), "on": today}
                       for f in TRACKED if f in prev and prev.get(f) != r.get(f)]
            r["changes"] = (prev.get("changes") or [])[-8:] + changes
            if prev.get("newSince") and not changes:
                r["newSince"] = prev["newSince"]
        new_ids = {r["id"] for r in rows}
        for prev in old.get("scholarships", []):
            if prev["id"] not in new_ids:  # a run can miss an award; keep it, marked
                prev["missingSince"] = prev.get("missingSince") or today
                rows.append(prev)
        log = old.get("log", [])
    _write(p, {"scholarships": rows, "log": log, "profile": profile,
               "source": source, "imported": TODAY.isoformat()})
    reg = [t for t in load_registry() if t["id"] != tid]
    dated = sum(1 for r in rows if r.get("deadline"))
    reg.append({"id": tid, "label": label, "awards": len(rows), "dated": dated,
                "profile": (profile.get("source") or "")[:80], "source": source})
    _write(REGISTRY, sorted(reg, key=lambda t: t["label"]))
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf")
    ap.add_argument("--run")
    ap.add_argument("--evaluated")
    ap.add_argument("--subset")
    ap.add_argument("--from", dest="from_id", default=None)
    ap.add_argument("--id", default=None)
    ap.add_argument("--label", default=None)
    a = ap.parse_args()

    if a.pdf:
        path = Path(a.pdf).expanduser()
        rows, profile = parse_pdf(path)
        tid = a.id or f"report-{re.sub(r'[^0-9]', '', path.stem) or TODAY.strftime('%Y%m%d')}"
        label = a.label or f"Report · {path.name}"
        p = write_store(tid, label, rows, profile, path.name)
    elif a.evaluated:
        path = Path(a.evaluated).expanduser()
        rows = parse_evaluated(path)
        profile = {"source": f"evaluated results ({path.parent.name})", "fields": [], "tags": [],
                   "notes": ["Imported from the skill's evaluated.json (verified flags and page checks included)."]}
        tid = a.id or slug(path.parent.name)
        p = write_store(tid, a.label or f"Run · {path.parent.name}", rows, profile, path.name)
    elif a.run:
        d = Path(a.run).expanduser()
        rows, profile = parse_run(d)
        if not rows:
            print(f"no evaluated.json or results.csv with awards in {d}")
            return 2
        tid = a.id or slug(d.name)
        p = write_store(tid, a.label or f"Run · {d.name}", rows, profile, d.name)
    elif a.subset:
        reg = {t["id"]: t for t in load_registry()}
        src = a.from_id or (list(reg)[0] if reg else None)
        if src not in reg:
            print(f"unknown --from tracker: {src}")
            return 2
        store = json.loads((STORES / f"{src}.json").read_text("utf-8"))
        words = [w.strip().lower() for w in a.subset.split(",") if w.strip()]
        rows = [dict(r) for r in store["scholarships"]
                if any(w in (r["name"] + " " + r["sponsor"] + " " + r["why"]).lower() for w in words)]
        prof = dict(store.get("profile") or {})
        prof["source"] = f"{prof.get('source', 'profile')} · scoped to: {a.subset}"
        tid = a.id or slug(a.subset)
        p = write_store(tid, a.label or f"{reg[src]['label']} · scoped", rows, prof, f"subset of {src}")
    else:
        print("need --pdf PATH, --run DIR, --evaluated FILE, or --subset \"keywords\" --from TRACKER_ID")
        return 2

    print(f"tracker: {tid}")
    print("rows:", json.loads(p.read_text('utf-8'))["scholarships"].__len__(), "| store:", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
