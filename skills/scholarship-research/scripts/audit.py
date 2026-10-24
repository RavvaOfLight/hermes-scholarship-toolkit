#!/usr/bin/env python3
"""Deterministic page checks for stored scholarship records.

``audit_candidates`` re-reads each record's own pages (saved by ``search_batch``
in the run workspace's ``pages/`` folder, or fetched once through Hermes's
``web_extract``) and compares the record with the page text:

- the page names the scholarship (catches a link to a different program or to a
  foundation's general page),
- the deadline date appears, with its year,
- the maximum award appears and does not read as a program total,
- explicit no-fee, no-repayment, and merit-selection statements and a stated
  deadline time zone, which fill fields the record left unknown.

Findings go to ``audit.json`` next to the candidates. The evaluator turns them
into uncertainties (which block "verified") or fills unknown fields, quoting the
page. No model is involved and nothing is inferred beyond the page text.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import web_batch

AUDIT_FILE = "audit.json"
AUDIT_SCHEMA_VERSION = 1
FETCH_CHUNK = 5
DEFAULT_AUDIT_BUDGET_SECONDS = 240
QUOTE_CHARS = 220
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december")
NAME_STOPWORDS = frozenset({
    "scholarship", "scholarships", "program", "programs", "award", "awards", "the", "of", "for",
    "and", "a", "an", "in", "at", "to", "on", "incl", "including", "also", "fund", "funds", "grant",
    "grants", "foundation", "national", "annual", "student", "students", "college", "university",
    "high", "school", "senior", "seniors", "new", "first", "year",
})
TZ_NAMES = {
    "et": "America/New_York", "est": "America/New_York", "edt": "America/New_York",
    "eastern": "America/New_York", "ct": "America/Chicago", "cst": "America/Chicago",
    "cdt": "America/Chicago", "central": "America/Chicago", "mt": "America/Denver",
    "mst": "America/Denver", "mdt": "America/Denver", "mountain": "America/Denver",
    "pt": "America/Los_Angeles", "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles",
    "pacific": "America/Los_Angeles",
}
_ZONE = r"(ET|EST|EDT|Eastern|CT|CST|CDT|Central|MT|MST|MDT|Mountain|PT|PST|PDT|Pacific)\b"
TZ_RE = re.compile(
    r"\b(?:(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)|midnight|noon)\s*\(?\s*" + _ZONE,
    re.IGNORECASE,
)
_I = re.IGNORECASE
NO_FEE = [re.compile(p, _I) for p in (
    r"\bno\s+(?:application|entry|processing|submission)\s+fees?\b",
    r"\bfree\s+to\s+(?:apply|enter)\b",
    # "Free Application for Federal Student Aid" is the FAFSA's name, not a fee statement
    r"\b(?:a|is|the)\s+free\s+application\b(?!\s+for\s+federal)",
    r"\b(?:the\s+)?(?:application|applying)\s+is\s+(?:free|at\s+no\s+cost)\b",
    r"\bno\s+(?:fee|cost|charge)\s+to\s+(?:apply|enter|submit)\b",
    r"\b(?:does|do|will)\s+not\s+charge\s+(?:a\s+|an\s+|any\s+)?(?:application\s+)?fees?\b",
    r"\bno\s+fees?\s+(?:are\s+)?(?:required|charged)\b",
)]
AUTO_CONSIDER = [re.compile(p, _I) for p in (
    r"\bautomatic(?:ally)?\s+(?:be\s+)?consider",
    r"\bno\s+separate\s+(?:scholarship\s+)?application\b",
    r"\ball\s+(?:incoming|admitted|new|first-year)\b[^\n]{0,60}?\b(?:students|applicants)\b[^\n]{0,100}?\bwill\s+(?:automatically\s+)?be\s+considered\b",
)]
FEE_MENTION = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d\d)?\s+(?:non-?refundable\s+)?(?:application|processing|entry|submission)\s+fee"
    r"|\b(?:application|processing|entry|submission)\s+fee\s+(?:of\s+)?\$\s?\d",
    _I,
)
NOT_LOAN = [re.compile(p, _I) for p in (
    r"\b(?:does|do|will)\s+not\s+(?:need\s+to\s+|have\s+to\s+)?be\s+repaid\b",
    r"\bno\s+repayment\b",
    r"\bnot\s+a\s+loan\b",
    r"\bnever\s+(?:has|have|needs?)\s+to\s+be\s+(?:re)?paid\b",
    r"\bgift\s+aid\b",
)]
LOAN_MENTION = re.compile(r"\bmust\s+be\s+repaid\b|\bforgivable\s+loan|\bservice\s+(?:obligation|commitment)\b", _I)
MERIT = [re.compile(p, _I) for p in (
    r"\bselection\s+(?:criteria|is\s+based|committee|process)\b",
    r"\b(?:recipients|winners|scholars|finalists)\s+(?:are|will\s+be|were)\s+(?:chosen|selected|evaluated|judged)\b",
    r"\bselected\s+(?:based\s+on|on\s+the\s+basis\s+of|by\s+(?:a|the)\s+(?:committee|panel|judges))\b",
    r"\b(?:judged|evaluated|reviewed|scored)\s+(?:on|by|based\s+on)\b",
    r"\bbased\s+on\s+(?:academic\s+)?(?:merit|financial\s+need|achievement|leadership)\b",
    r"\b(?:merit|need)[- ]based\b",
    r"\bautomatic(?:ally)?\s+(?:be\s+)?consider",
)]
SWEEP = re.compile(r"\bsweepstakes\b|\brandom(?:ly)?\s+(?:drawing|draw|selected|chosen)\b|\benter\s+to\s+win\b", _I)
AMOUNT_AGGREGATE_AFTER = re.compile(
    r"^\W{0,3}(?:in\s+(?:total\s+)?scholarships|(?:has\s+been\s+|have\s+been\s+|were\s+|was\s+)?awarded"
    r"|total\b|to\s+\d[\d,]*\s+(?:students|recipients|scholars)|annually\s+to|since\s+\d{4}|program[- ]wide)",
    _I,
)
AMOUNT_AGGREGATE_BEFORE = re.compile(r"(?:total(?:ing)?|families\s+(?:making|earning)[^$]{0,20}|less\s+than|under)\s*~?\s*$", _I)


def _plain(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip() + " "


def _quote(text: str, start: int, end: int) -> str:
    left = max(text.rfind("\n", 0, start), text.rfind(". ", 0, start) + 1, start - QUOTE_CHARS // 2, 0)
    right_candidates = [i for i in (text.find("\n", end), text.find(". ", end)) if i != -1]
    right = min(right_candidates + [end + QUOTE_CHARS // 2, len(text)])
    return " ".join(text[left:right + 1].split())[:QUOTE_CHARS]


def name_tokens(name: str) -> tuple[str, list[str]]:
    """(core phrase, distinctive tokens) of a scholarship name, without parentheses or years."""
    core = re.sub(r"\([^)]*\)", " ", name)
    core = re.sub(r"\b(?:19|20)\d{2}(?:\s*[-–/]\s*(?:19|20)?\d{2})?\b", " ", core)
    words = _plain(core).split()
    distinctive = [word for word in words if len(word) >= 3 and word not in NAME_STOPWORDS]
    return " ".join(words), list(dict.fromkeys(distinctive))


def name_on_page(name: str, text: str) -> bool | None:
    phrase, tokens = name_tokens(name)
    page = _plain(text)
    if phrase and f" {phrase} " in page:
        return True
    if not tokens:
        return None
    hits = sum(1 for token in tokens if f" {token} " in page)
    return hits >= 1 and hits / len(tokens) >= 0.6


def _date_patterns(day: date) -> list[re.Pattern[str]]:
    full = MONTHS[day.month - 1]
    month = f"(?:{full}|{full[:3]}{'|sept' if day.month == 9 else ''})\\.?"
    return [re.compile(p, _I) for p in (
        rf"\b{month}\s+0?{day.day}(?:st|nd|rd|th)?(?!\d)",
        rf"(?<!\d)0?{day.day}(?:st|nd|rd|th)?\s+(?:of\s+)?{month}\b",
        rf"(?<![\d/.-])0?{day.month}[/.-]0?{day.day}(?:[/.-](?:\d{{4}}|\d{{2}}))?(?![\d/])",
        rf"\b{day.isoformat()}\b",
    )]


def deadline_on_page(iso: str, text: str) -> dict[str, bool]:
    day = date.fromisoformat(iso)
    year = str(day.year)
    found = near = False
    for pattern in _date_patterns(day):
        for match in pattern.finditer(text):
            found = True
            window = text[max(0, match.start() - 160):match.end() + 160]
            if year in window or re.search(rf"[/.-]{year[2:]}(?!\d)", match.group(0)):
                near = True
                break
        if near:
            break
    return {"found": found, "year_near": near, "page_has_year": year in text}


def _amount_patterns(value: float) -> list[re.Pattern[str]]:
    if not float(value).is_integer():
        return [re.compile(rf"\$?\s?{re.escape(f'{value:,.2f}')}(?![\d])")]
    number = int(value)
    patterns = [rf"(?<![\d,.]){re.escape(f'{number:,}')}(?![\d,])", rf"(?<![\d,.]){number}(?![\d,])"]
    if number >= 1000 and number % 1000 == 0:
        patterns.append(rf"\${number // 1000}\s?[kK]\b")
    if number >= 1_000_000 and number % 100_000 == 0:
        patterns.append(rf"\${number / 1_000_000:g}\s+million\b")
    return [re.compile(p, _I) for p in patterns]


def amount_on_page(maximum: float, text: str) -> dict[str, bool]:
    found = standalone = False
    for pattern in _amount_patterns(maximum):
        for match in pattern.finditer(text):
            found = True
            after = text[match.end():match.end() + 70]
            before = text[max(0, match.start() - 50):match.start()]
            if not (AMOUNT_AGGREGATE_AFTER.search(after) or AMOUNT_AGGREGATE_BEFORE.search(before)):
                standalone = True
    return {"found": found, "aggregate_only": found and not standalone}


def _first(patterns: list[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return _quote(text, match.start(), match.end())
    return None


def _timezone(text: str) -> dict[str, str] | None:
    zones: dict[str, str] = {}
    for match in TZ_RE.finditer(text):
        zone = TZ_NAMES[match.group(1).casefold()]
        zones.setdefault(zone, _quote(text, match.start(), match.end()))
    if len(zones) != 1:
        return None  # none stated, or several (for example "PT / ET"): leave it to the record
    zone, quote = next(iter(zones.items()))
    return {"value": zone, "quote": quote}


def check_record(record: dict[str, Any], texts: dict[str, str]) -> dict[str, Any]:
    """Compare one record with the text of its pages. Pure: no I/O."""
    official = str(record.get("official_url") or "")
    application = str(record.get("application_url") or "")
    authority = str((record.get("source_verification") or {}).get("authority_url") or "")
    own_pages = "\n\n".join(texts[url] for url in (official, application) if url in texts)
    all_pages = "\n\n".join(texts[url] for url in dict.fromkeys((official, application, authority)) if url in texts)
    findings: dict[str, Any] = {
        "official_url": web_batch.normalize_page_url(official) if official else "",
        "checked_urls": [url for url in dict.fromkeys((official, application, authority)) if url in texts],
        "flags": [],
    }
    if not own_pages:
        findings["readable"] = False
        return findings
    findings["readable"] = True
    named = name_on_page(str(record.get("name") or ""), own_pages)
    findings["name_on_page"] = named
    if named is False:
        findings["flags"].append(
            "Page check: the official page does not name this scholarship; confirm the link is for this program."
        )
    deadline = (record.get("deadline") or {}).get("date")
    if isinstance(deadline, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
        seen = deadline_on_page(deadline, all_pages)
        findings["deadline_on_page"] = seen
        day = date.fromisoformat(deadline)
        pretty = f"{MONTHS[day.month - 1].title()} {day.day}, {day.year}"
        if not seen["found"]:
            findings["flags"].append(f"Page check: the deadline {pretty} does not appear on the pages checked.")
        elif not seen["year_near"] and not seen["page_has_year"]:
            findings["flags"].append(
                f"Page check: the page gives the deadline without a year; confirm {pretty} is this cycle's date."
            )
    maximum = (record.get("amount") or {}).get("maximum")
    if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and maximum > 0:
        seen_amount = amount_on_page(float(maximum), all_pages)
        findings["amount_on_page"] = seen_amount
        if not seen_amount["found"]:
            findings["flags"].append(f"Page check: the maximum award (${maximum:,.0f}) does not appear on the pages checked.")
        elif seen_amount["aggregate_only"]:
            findings["flags"].append(
                f"Page check: ${maximum:,.0f} reads as a program total on the page, not one student's award."
            )
    no_fee = _first(NO_FEE, all_pages) or _first(AUTO_CONSIDER, all_pages)
    fee = FEE_MENTION.search(all_pages)
    if no_fee and not fee:
        findings["application_fee"] = {"value": False, "quote": no_fee}
    elif fee:
        findings["flags"].append(
            "Page check: the page mentions a fee ('" + _quote(all_pages, fee.start(), fee.end())
            + "'); confirm the scholarship itself is free."
        )
    not_loan = _first(NOT_LOAN, all_pages)
    loan = LOAN_MENTION.search(all_pages)
    if not_loan and not loan:
        findings["is_loan"] = {"value": False, "quote": not_loan}
    elif loan:
        findings["flags"].append(
            "Page check: the page mentions repayment or a service obligation ('"
            + _quote(all_pages, loan.start(), loan.end()) + "'); confirm it is not a loan."
        )
    sweep = SWEEP.search(all_pages)
    merit = _first(MERIT, all_pages)
    if sweep:
        findings["flags"].append(
            "Page check: the page describes a drawing ('" + _quote(all_pages, sweep.start(), sweep.end())
            + "'); confirm it is not a sweepstakes."
        )
    elif merit:
        findings["is_sweepstakes"] = {"value": False, "quote": merit}
    zone = _timezone(all_pages)
    if zone:
        findings["timezone"] = zone
    return findings


def apply_findings(record: dict[str, Any], findings: dict[str, Any] | None) -> dict[str, Any]:
    """Merge audit findings into a validated record: fill unknowns, add page-check doubts.

    Findings made for a different official URL (the record was rewritten since the
    audit) are ignored. Explicit record values are never overridden.
    """
    if not findings:
        return record
    official = str(record.get("official_url") or "")
    if findings.get("official_url") != (web_batch.normalize_page_url(official) if official else ""):
        return record
    notes = record.setdefault("notes", [])
    for field, label in (("application_fee", "no fee"), ("is_loan", "not a loan"), ("is_sweepstakes", "selection criteria")):
        fill = findings.get(field)
        if record.get(field) is None and isinstance(fill, dict) and fill.get("value") is False:
            record[field] = False
            notes.append(f"Page check ({label}): \"{fill.get('quote', '')}\"")
    zone = findings.get("timezone")
    deadline = record.get("deadline") or {}
    if deadline.get("date") and not deadline.get("timezone") and isinstance(zone, dict) and zone.get("value"):
        deadline["timezone"] = zone["value"]
        notes.append(f"Page check (time zone): \"{zone.get('quote', '')}\"")
    for flag in findings.get("flags", []):
        if flag not in record["uncertainties"]:
            record["uncertainties"].append(flag)
    return record


def _record_urls(record: dict[str, Any]) -> list[str]:
    urls = [record.get("official_url"), record.get("application_url"),
            (record.get("source_verification") or {}).get("authority_url")]
    return [url for url in dict.fromkeys(urls) if isinstance(url, str) and url.startswith("https://")]


def audit_candidates(
    candidates_path: str | Path,
    *,
    fetch: Callable[..., Any] | None = None,
    time_budget: float = DEFAULT_AUDIT_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Check every stored record against its pages and write audit.json beside the candidates."""
    import pipeline  # local import: pipeline imports this module

    path = Path(candidates_path).expanduser()
    workdir = path.parent
    stored = pipeline.load_json(path) if path.exists() else {"scholarships": []}
    records = [record for record in pipeline._candidate_list(stored) if isinstance(record, dict)]
    texts: dict[str, str] = {}
    missing: list[str] = []
    for record in records:
        for url in _record_urls(record):
            if url in texts or url in missing:
                continue
            saved = web_batch.load_page(workdir, url)
            if saved is not None:
                texts[url] = saved
            else:
                missing.append(url)
    fetched: list[str] = []
    unreadable: list[str] = []
    not_reached: list[str] = []
    started = clock()
    if fetch is not None:
        for start in range(0, len(missing), FETCH_CHUNK):
            chunk = missing[start:start + FETCH_CHUNK]
            if start and clock() - started > time_budget:
                not_reached = missing[start:]
                break
            contents: dict[str, str] = {}
            try:
                web_batch.normalize_extract_results(web_batch._call_extract(fetch, chunk), chunk, contents)
            except Exception:  # noqa: BLE001 - provider errors make the page unreadable, not fatal
                contents = {}
            for url in chunk:
                if contents.get(url):
                    web_batch.save_page(workdir, url, "", contents[url])
                    texts[url] = contents[url]
                    fetched.append(url)
                else:
                    unreadable.append(url)
    else:
        unreadable = list(missing)
    results: dict[str, Any] = {}
    compact: list[dict[str, Any]] = []
    for record in records:
        record_id = pipeline.scholarship_id(str(record.get("name", "")), str(record.get("sponsor", "")))
        findings = check_record(record, texts)
        findings["name"] = str(record.get("name", ""))[:300]
        results[record_id] = findings
        fills = [field for field in ("application_fee", "is_loan", "is_sweepstakes", "timezone") if field in findings]
        if findings["flags"] or fills or not findings.get("readable"):
            compact.append({"name": findings["name"], "readable": findings.get("readable", False),
                            "flags": findings["flags"], "fills": fills})
    audit = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "audited_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "records": results,
    }
    out = workdir / AUDIT_FILE
    web_batch._private_write(out, json.dumps(audit, indent=2, ensure_ascii=False))
    flagged = sum(1 for item in results.values() if item["flags"])
    return {
        "ok": True,
        "path": str(out),
        "audited_records": len(results),
        "pages_read": len(texts),
        "fetched_pages": len(fetched),
        "unreadable_urls": unreadable[:20],
        "unreadable_count": len(unreadable),
        "not_reached_count": len(not_reached),
        "records_flagged": flagged,
        "fills": {field: sum(1 for item in results.values() if field in item)
                  for field in ("application_fee", "is_loan", "is_sweepstakes", "timezone")},
        "records": compact[:40],
        "next_action": "audit_again_to_read_remaining_pages" if not_reached else "evaluate",
    }


def load_audit(candidates_path: str | Path) -> dict[str, Any] | None:
    path = Path(candidates_path).expanduser().parent / AUDIT_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("records"), dict) else None
