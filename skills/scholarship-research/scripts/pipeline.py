#!/usr/bin/env python3
"""Deterministic query, verification, matching, rendering, and export pipeline."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import ipaddress
import json
import math
import re
import string
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from scholarship_profile import (  # noqa: E402
    FIELD_RULES,
    SEARCH_MODES,
    atomic_write_text,
    file_lock,
    get_path,
    load_profile,
    missing_required_fields,
    optional_questionnaire_completed,
    strip_control_characters,
    validate_profile,
)
import audit as page_audit  # noqa: E402


PIPELINE_SCHEMA_VERSION = 1
TRUSTED_SOURCES_PATH = SKILL_DIR / "references" / "trusted-sources.json"
ALLOWED_SOURCE_KINDS = {"official", "authorized_administrator", "discovery"}
ALLOWED_STATUSES = {"open", "not_open_yet", "closed", "cycle_unknown"}
DEADLINE_TYPES = {"received_by", "postmarked_by", "rolling", "not_announced"}
ALLOWED_OPERATORS = {
    "eq",
    "neq",
    "gte",
    "lte",
    "in",
    "not_in",
    "contains",
    "contains_any",
    "overlaps",
    "truthy",
}
REQUIRED_EVIDENCE = {"amount", "deadline", "eligibility", "application"}
CANDIDATE_FIELDS = {
    "name", "sponsor", "source_kind", "cycle", "applicant_entry_year", "status",
    "amount", "deadline", "requirements", "application_tasks", "official_url",
    "application_url", "verified_at", "evidence", "uncertainties",
    "application_fee", "is_loan", "is_sweepstakes", "source_verification",
    "eligibility_logic", "notes",
}
REQUIRED_CANDIDATE_FIELDS = CANDIDATE_FIELDS - {"applicant_entry_year", "notes"}
AMOUNT_DISPLAY_MAX = 200
# A "note" that hedges is a doubt: it moves to uncertainties, where it blocks "verified".
DOUBT_RE = re.compile(
    r"\b(?:confirm|unclear|unknown|uncertain|unsure|may|might|maybe|verify|check|ambiguous|tbd|"
    r"to\s+be\s+(?:announced|determined|confirmed)|appears?|seems?|likely|probably|assum\w*|"
    r"not\s+(?:stated|listed|published|clear|found|confirmed|specified))\b",
    re.IGNORECASE,
)
BUCKETS = ("verified", "needs_confirmation", "ineligible", "expired", "unverified", "rejected")
CLOSING_SOON_DAYS = 14
MAX_QUERY_LIMIT = 100
STANDARD_GPA_SCALE = 4.0

# Registry roles and the source_kind a verified record from that role must use.
# None marks discovery-only roles that can never verify a record.
REGISTRY_ROLES: dict[str, str | None] = {
    "official_sponsor": "official",
    "official_program": "official",
    "official_sponsor_or_local_club": "official",
    "state_agency": "official",
    "authorized_administrator_and_discovery": "authorized_administrator",
    "government_discovery_database": None,
    "discovery_database": None,
}
TRIGGER_KEYWORD_GROUPS: dict[str, tuple[str, ...]] = {
    "major_keywords": ("student.academics.intended_major",),
    "affiliation_keywords": ("student.affiliations.clubs", "student.affiliations.professional"),
    "military_keywords": ("student.affiliations.military",),
    "identity_keywords": (
        "student.identity.ethnicity",
        "student.identity.religion",
        "student.identity.other",
    ),
}
TRIGGER_KEYS = set(TRIGGER_KEYWORD_GROUPS) | {"always", "match", "states", "search_modes", "true_fields"}
IDENTITY_FIELDS = {
    "student.identity.ethnicity",
    "student.identity.lgbtq",
    "student.identity.disability",
    "student.identity.religion",
    "student.identity.other",
}
TEMPLATE_FIELDS = {
    "state", "city", "county", "major", "graduation_year", "entry_year", "next_year",
    "entry_term", "applicant_stage", "state_aid_stage", "degree",
}
HOSTNAME_RE = re.compile(
    r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
)
# RFC 3986 characters. Anything else (spaces, quotes, angle brackets) must be
# percent-encoded, which keeps URLs inert when reports are rendered.
URL_CHARACTERS_RE = re.compile(r"[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
TIMEZONE_ALIASES = {
    "et": "America/New_York", "est": "America/New_York", "edt": "America/New_York",
    "eastern": "America/New_York", "eastern time": "America/New_York",
    "ct": "America/Chicago", "cst": "America/Chicago", "cdt": "America/Chicago",
    "central": "America/Chicago", "central time": "America/Chicago",
    "mt": "America/Denver", "mst": "America/Denver", "mdt": "America/Denver",
    "mountain": "America/Denver", "mountain time": "America/Denver",
    "pt": "America/Los_Angeles", "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles",
    "pacific": "America/Los_Angeles", "pacific time": "America/Los_Angeles",
    "akt": "America/Anchorage", "akst": "America/Anchorage", "akdt": "America/Anchorage",
    "hst": "Pacific/Honolulu", "hawaii": "Pacific/Honolulu",
    "utc": "UTC", "gmt": "UTC",
}


US_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas",
    "ca": "california", "co": "colorado", "ct": "connecticut", "de": "delaware",
    "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho",
    "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas",
    "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland",
    "ma": "massachusetts", "mi": "michigan", "mn": "minnesota", "ms": "mississippi",
    "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada",
    "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico", "ny": "new york",
    "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma",
    "or": "oregon", "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina",
    "sd": "south dakota", "tn": "tennessee", "tx": "texas", "ut": "utah",
    "vt": "vermont", "va": "virginia", "wa": "washington", "wv": "west virginia",
    "wi": "wisconsin", "wy": "wyoming", "dc": "district of columbia",
    "washington dc": "district of columbia", "washington d c": "district of columbia",
}


class CandidateError(ValueError):
    pass


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {token}")


def _loads(text: str) -> Any:
    return json.loads(text, parse_constant=_reject_json_constant)


def load_json(path: Path) -> Any:
    try:
        return _loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"file does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON {path}: {exc}") from exc


def atomic_write(path: Path, text: str, *, newline: str | None = None) -> None:
    atomic_write_text(path, text, newline=newline)


def emit(value: Any, output: str | None, *, as_json: bool = True) -> None:
    text = json.dumps(value, indent=2, ensure_ascii=False) if as_json else str(value)
    if output:
        atomic_write(Path(output).expanduser(), text)
    else:
        print(text)


def clean_text(value: Any, field: str, *, required: bool = False, maximum: int = 1000) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise CandidateError(f"{field} must be a string")
    text = strip_control_characters(value).strip()
    if required and not text:
        raise CandidateError(f"{field} is required")
    if len(text) > maximum:
        raise CandidateError(f"{field} exceeds {maximum} characters")
    return text


def normalize_token(value: Any, *, state: bool = False) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).strip()
    if state:
        return US_STATES.get(text, text)
    return text


def normalize_dedup(value: str) -> str:
    without_year = re.sub(r"\b(?:19|20)\d{2}(?:\s*[-/]\s*(?:19|20)?\d{2})?\b", " ", value)
    return normalize_token(without_year)


def scholarship_id(name: str, sponsor: str) -> str:
    material = f"{normalize_dedup(sponsor)}|{normalize_dedup(name)}".encode("utf-8")
    return "sch-" + hashlib.sha256(material).hexdigest()[:12]


def infer_entry_year(profile: dict[str, Any]) -> int:
    term = str(get_path(profile, "student.entry_term") or "")
    years = [int(year) for year in re.findall(r"\b(?:19|20)\d{2}\b", term)]
    if years:
        return years[0]
    graduation = get_path(profile, "student.graduation_year")
    if isinstance(graduation, int):
        return graduation
    raise ValueError("cannot infer college-entry year from entry_term or graduation_year")


# ---------------------------------------------------------------------------
# Trusted-source registry


def _host_list(entry: dict[str, Any], label: str) -> list[str]:
    raw = entry.get("official_domains", entry.get("official_domain"))
    domains = [raw] if isinstance(raw, str) else raw
    if not isinstance(domains, list) or not domains:
        raise ValueError(f"{label}: official_domains must be a non-empty list")
    cleaned: list[str] = []
    for domain in domains:
        if not isinstance(domain, str):
            raise ValueError(f"{label}: domains must be strings")
        host = domain.strip().strip(".").casefold()
        if not HOSTNAME_RE.fullmatch(host):
            raise ValueError(f"{label}: invalid domain {domain!r}")
        cleaned.append(host)
    return cleaned


def _string_list_field(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{label} must be a non-empty list of strings")
    return [item.strip() for item in value]


def _validate_template(template: Any, label: str) -> str:
    if not isinstance(template, str) or not template.strip() or len(template) > 300:
        raise ValueError(f"{label}: query_template must be a string of at most 300 characters")
    for _literal, field_name, format_spec, conversion in string.Formatter().parse(template):
        if field_name is None:
            continue
        if field_name not in TEMPLATE_FIELDS or format_spec or conversion:
            raise ValueError(f"{label}: unsupported template field {{{field_name}}}")
    return template


def _validate_trigger(trigger: Any, label: str) -> dict[str, Any]:
    if not isinstance(trigger, dict) or not trigger:
        raise ValueError(f"{label}: trigger must be a non-empty object")
    unknown = set(trigger) - TRIGGER_KEYS
    if unknown:
        raise ValueError(f"{label}: unsupported trigger keys {sorted(unknown)}")
    result: dict[str, Any] = {}
    if "always" in trigger:
        if trigger["always"] is not True:
            raise ValueError(f"{label}: trigger.always must be true when present")
        result["always"] = True
    match = trigger.get("match", "any")
    if match not in {"any", "all"}:
        raise ValueError(f"{label}: trigger.match must be any or all")
    result["match"] = match
    for group in TRIGGER_KEYWORD_GROUPS:
        if group in trigger:
            result[group] = _string_list_field(trigger[group], f"{label}: trigger.{group}")
    if "states" in trigger:
        states = _string_list_field(trigger["states"], f"{label}: trigger.states")
        normalized = [normalize_token(state, state=True) for state in states]
        unknown_states = [state for state in normalized if state not in US_STATES.values()]
        if unknown_states:
            raise ValueError(f"{label}: unknown states {unknown_states}")
        result["states"] = normalized
    if "search_modes" in trigger:
        modes = _string_list_field(trigger["search_modes"], f"{label}: trigger.search_modes")
        if set(modes) - SEARCH_MODES:
            raise ValueError(f"{label}: unknown search modes {sorted(set(modes) - SEARCH_MODES)}")
        result["search_modes"] = modes
    if "true_fields" in trigger:
        fields = _string_list_field(trigger["true_fields"], f"{label}: trigger.true_fields")
        for field in fields:
            if field not in FIELD_RULES or FIELD_RULES[field][0] != "bool":
                raise ValueError(f"{label}: true_fields must name boolean profile fields")
        result["true_fields"] = fields
    has_condition = any(key in result for key in ("always", "states", "search_modes", "true_fields")) or any(
        group in result for group in TRIGGER_KEYWORD_GROUPS
    )
    if not has_condition:
        raise ValueError(f"{label}: trigger needs at least one condition")
    return result


def validate_registry(raw: Any) -> dict[str, Any]:
    """Validate trusted-sources.json and return a normalized registry."""
    if not isinstance(raw, dict) or not isinstance(raw.get("sources"), list):
        raise ValueError("trusted-sources.json must contain a sources array")
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw["sources"]):
        label = f"trusted-sources.json sources[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{label} must be an object")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 150:
            raise ValueError(f"{label}: name must be a string of at most 150 characters")
        label = f"trusted source {name!r}"
        if name in seen:
            raise ValueError(f"{label}: duplicate name")
        seen.add(name)
        role = entry.get("role")
        if role not in REGISTRY_ROLES:
            raise ValueError(f"{label}: unsupported role {role!r}")
        tier = entry.get("tier", 5)
        if isinstance(tier, bool) or not isinstance(tier, int) or not 1 <= tier <= 5:
            raise ValueError(f"{label}: tier must be an integer from 1 to 5")
        purpose = entry.get("purpose")
        if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 120:
            raise ValueError(f"{label}: purpose must be a string of at most 120 characters")
        domains = _host_list(entry, label)
        homepage = entry.get("homepage", "")
        if homepage:
            if not isinstance(homepage, str):
                raise ValueError(f"{label}: homepage must be a string")
            host = (urlparse(homepage).hostname or "").casefold()
            if not homepage.startswith("https://") or not any(_host_matches_domain(host, d) for d in domains):
                raise ValueError(f"{label}: homepage must be an https URL on an official domain")
        sources.append({
            "name": name,
            "role": role,
            "tier": tier,
            "purpose": purpose.strip(),
            "official_domains": domains,
            "homepage": homepage,
            "trigger": _validate_trigger(entry.get("trigger"), label),
            "query_template": _validate_template(entry.get("query_template"), label),
        })
    discovery = [
        domain.casefold() for domain in raw.get("discovery_domains", [])
        if isinstance(domain, str) and HOSTNAME_RE.fullmatch(domain.casefold())
    ]
    social = [
        domain.casefold() for domain in raw.get("social_domains", [])
        if isinstance(domain, str) and HOSTNAME_RE.fullmatch(domain.casefold())
    ]
    return {"sources": sources, "discovery_domains": discovery, "social_domains": social}


_REGISTRY_CACHE: dict[str, Any] = {}


def load_registry(path: Path = TRUSTED_SOURCES_PATH) -> dict[str, Any]:
    key = str(path)
    if key not in _REGISTRY_CACHE:
        _REGISTRY_CACHE[key] = validate_registry(load_json(path))
    return _REGISTRY_CACHE[key]


def _load_trusted_sources() -> list[dict[str, Any]]:
    return load_registry()["sources"]


def _profile_texts(profile: dict[str, Any], fields: Iterable[str]) -> list[str]:
    texts: list[str] = []
    for field in fields:
        value = get_path(profile, field)
        values = value if isinstance(value, list) else [value]
        texts.extend(normalize_token(item) for item in values if isinstance(item, str) and item.strip())
    return texts


def _keyword_hit(keywords: Iterable[str], haystacks: list[str]) -> bool:
    """Match whole normalized words, so 'deca' does not match 'decathlon'."""
    padded = [f" {haystack} " for haystack in haystacks]
    for keyword in keywords:
        needle = normalize_token(keyword)
        if needle and any(f" {needle} " in haystack for haystack in padded):
            return True
    return False


def _source_triggered(source: dict[str, Any], profile: dict[str, Any]) -> bool:
    trigger = source.get("trigger", {})
    consent = get_path(profile, "preferences.include_identity_searches") is True
    states = trigger.get("states")
    if states:
        profile_state = normalize_token(get_path(profile, "student.location.state") or "", state=True)
        if profile_state not in {normalize_token(state, state=True) for state in states}:
            return False
    modes = trigger.get("search_modes")
    if modes and get_path(profile, "student.search_mode") not in modes:
        return False
    for field in trigger.get("true_fields", []):
        if field in IDENTITY_FIELDS and not consent:
            return False
        if get_path(profile, field) is not True:
            return False
    groups = {group: trigger[group] for group in TRIGGER_KEYWORD_GROUPS if trigger.get(group)}
    if "identity_keywords" in groups and not consent:
        return False
    if not groups:
        return True
    hits = [
        _keyword_hit(keywords, _profile_texts(profile, TRIGGER_KEYWORD_GROUPS[group]))
        for group, keywords in groups.items()
    ]
    return all(hits) if trigger.get("match") == "all" else any(hits)


# ---------------------------------------------------------------------------
# Query generation


def _query_record(
    query: str,
    purpose: str,
    tier: int,
    source: str = "",
    domains: list[str] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "query": re.sub(r"\s+", " ", query).strip(),
        "purpose": purpose,
        "tier": tier,
        "suggested_source": source,
        "must_verify_officially": True,
    }
    if domains:
        record["official_domains"] = list(domains)
    return record


def _require_ready_profile(profile: dict[str, Any]) -> None:
    errors, _warnings = validate_profile(profile)
    if errors:
        raise ValueError("invalid profile:\n- " + "\n- ".join(errors))
    missing = missing_required_fields(profile)
    if missing:
        raise ValueError("profile is missing required search fields: " + ", ".join(missing))
    if not optional_questionnaire_completed(profile):
        raise ValueError(
            "profile questionnaire is incomplete; ask every questionnaire batch and "
            "submit the responses with complete_onboarding (profile.py finish-questionnaire)"
        )


STEM_MAJOR_RE = re.compile(
    r"engineer|robot|science|scien|comput|math|physic|chemi|biolog|aerospace|electr|mechani|data|"
    r"statistic|technolog|cyber|software|neuro|geolog|astron",
    re.IGNORECASE,
)
SPORTS_RE = re.compile(
    r"football|basketball|baseball|softball|soccer|track|cross[- ]country|swim|tennis|golf|wrestl|"
    r"volleyball|lacrosse|hockey|cheer|athlet|varsity",
    re.IGNORECASE,
)


def _stage_terms(search_mode: str, degree: str) -> dict[str, str | bool]:
    degree_text = re.sub(r"[_-]+", " ", degree).strip()
    degree_label = {
        "bachelor": "bachelor's",
        "bachelors": "bachelor's",
        "bachelor s": "bachelor's",
        "master": "master's",
        "masters": "master's",
        "master s": "master's",
        "doctorate": "doctoral",
        "doctoral": "doctoral",
        "phd": "doctoral",
        "associate": "associate-degree",
        "certificate": "certificate",
    }.get(normalize_token(degree_text), degree_text)
    degree_student = f"{degree_label} student" if degree_label else "student"
    stages: dict[str, dict[str, str | bool]] = {
        "high_school": {
            "applicant": "high school senior",
            "institutional": "incoming first-year student",
            "state_aid": "undergraduate",
            "school_sources": True,
        },
        "undergraduate": {
            "applicant": "current undergraduate student",
            "institutional": "continuing undergraduate student",
            "state_aid": "undergraduate",
            "school_sources": False,
        },
        "transfer": {
            "applicant": "transfer student",
            "institutional": "incoming transfer student",
            "state_aid": "undergraduate transfer",
            "school_sources": False,
        },
        "graduate": {
            "applicant": degree_student,
            "institutional": f"incoming {degree_student}",
            "state_aid": "graduate",
            "school_sources": False,
        },
        "trade_certificate": {
            "applicant": "trade or certificate student",
            "institutional": "incoming trade or certificate student",
            "state_aid": "workforce training trade certificate",
            "school_sources": False,
        },
    }
    if search_mode not in stages:
        raise ValueError(f"unsupported student.search_mode: {search_mode}")
    return stages[search_mode]


def _organization_only(value: str) -> str:
    """Remove common relationship prefixes before putting an affiliation in a query."""
    text = value.strip()
    return re.sub(
        r"^(?:self|student|parent|guardian|mother|father|mom|dad|spouse|partner|family)\s*[:\-]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()


def generate_queries(profile: dict[str, Any], max_queries: int = 60) -> dict[str, Any]:
    _require_ready_profile(profile)
    registry = load_registry()
    entry_year = infer_entry_year(profile)
    graduation_year = get_path(profile, "student.graduation_year")
    state = clean_text(get_path(profile, "student.location.state"), "state", required=True, maximum=100)
    county = clean_text(get_path(profile, "student.location.county"), "county", maximum=150)
    city = clean_text(get_path(profile, "student.location.city"), "city", maximum=150)
    high_school = clean_text(get_path(profile, "student.location.high_school"), "high_school", maximum=250)
    district = clean_text(get_path(profile, "student.location.school_district"), "school_district", maximum=250)
    major = clean_text(get_path(profile, "student.academics.intended_major"), "major", required=True, maximum=200)
    degree = clean_text(get_path(profile, "student.degree_level"), "degree_level", required=True, maximum=80)
    search_mode = clean_text(get_path(profile, "student.search_mode"), "search_mode", required=True, maximum=40)
    entry_term = clean_text(get_path(profile, "student.entry_term"), "entry_term", required=True, maximum=80)
    stage = _stage_terms(search_mode, degree)
    applicant = str(stage["applicant"])
    institutional = str(stage["institutional"])
    state_aid = str(stage["state_aid"])
    school_sources = bool(stage["school_sources"])
    colleges = get_path(profile, "student.target_colleges") or []
    affiliations = (
        (get_path(profile, "student.affiliations.clubs") or [])
        + (get_path(profile, "student.affiliations.professional") or [])
    )
    terms = [entry_term, f"{entry_year}-{entry_year + 1} academic year", "current scholarship application"]
    if search_mode == "high_school":
        terms.insert(0, f"Class of {graduation_year}")
    queries: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(
        query: str,
        purpose: str,
        tier: int,
        source: str = "",
        domains: list[str] | None = None,
    ) -> None:
        normalized = normalize_token(query)
        if normalized and normalized not in seen:
            seen.add(normalized)
            queries.append(_query_record(query, purpose, tier, source, domains))

    for college in colleges[:5]:
        add(f"{college} {institutional} scholarships {entry_term}", "institutional scholarships", 1, str(college))
        add(f"{college} {major} department scholarships {applicant} {entry_year}", "department scholarships", 1, str(college))
        add(f"{college} scholarship portal general application {entry_year}", "college scholarship portal", 1, str(college))

    locality = county or city
    if locality:
        add(f"{locality} {state} {applicant} scholarship application {entry_year}", "local scholarships", 2)
        add(f"{locality} {state} community foundation scholarships {applicant} {entry_year}", "community foundation scholarships", 2)
        # one community-foundation application often covers dozens of named funds
        add(f"{locality} {state} community foundation scholarship fund list eligibility", "community foundation fund directory", 2)
        add(f"{locality} {state} Rotary Kiwanis Lions Ruritan club scholarship {applicant} {entry_year}", "civic club scholarships", 3)
        add(f"{locality} {state} local business bank credit union scholarship {applicant} {entry_year}", "local business scholarships", 3)
    if school_sources and high_school:
        add(f"{high_school} scholarship bulletin Class of {graduation_year}", "high-school scholarships", 2, high_school)
        add(f"{high_school} counseling office local scholarship list {graduation_year}", "high-school counselor scholarship list", 2, high_school)
    if school_sources and district:
        add(f"{district} scholarships graduating seniors {graduation_year}", "school-district scholarships", 2, district)
    if school_sources and (district or county):
        add(f"{district or county + ' public schools'} senior scholarship packet {graduation_year}", "school-district scholarship packet", 2, district or county)
    if STEM_MAJOR_RE.search(major):
        add(f"high school science research competition scholarship {entry_year} science fair", "STEM research competitions", 4)
    if any(SPORTS_RE.search(str(club)) for club in affiliations):
        add(f"{state} high school league scholar athlete scholarship {graduation_year or entry_year}", "scholar-athlete scholarships", 4)

    add(f"{state} official state grants scholarships {state_aid} {entry_year}-{entry_year + 1}", "state aid", 3)
    add(f"{state} {major} scholarship {applicant} {entry_year} application", "major scholarships", 4)
    add(f"{major} scholarship {applicant} {entry_term} application", "national major scholarships", 4)

    for affiliation in affiliations[:6]:
        location = f" {city} {state}" if city else f" {state}"
        add(f"{affiliation} member scholarship{location} {entry_year} application", "affiliation scholarship", 4, str(affiliation))

    employer = _organization_only(clean_text(get_path(profile, "student.affiliations.employer"), "employer", maximum=200))
    credit_union = _organization_only(clean_text(get_path(profile, "student.affiliations.credit_union"), "credit_union", maximum=200))
    military = clean_text(get_path(profile, "student.affiliations.military"), "military", maximum=200)
    citizenship = clean_text(get_path(profile, "student.citizenship"), "citizenship", maximum=120)
    if employer:
        add(f"{employer} employee family scholarship {entry_year} application", "employer-affiliation scholarship", 4, employer)
    if credit_union:
        add(f"{credit_union} member scholarship {entry_year} application", "credit-union scholarship", 4, credit_union)
    if military:
        add(f"{_organization_only(military)} family scholarship {applicant} {entry_year} application", "military-affiliation scholarship", 4)
    if get_path(profile, "student.first_generation") is True:
        add(f"first-generation student scholarship {applicant} {entry_year} application", "first-generation scholarship", 4)
    if get_path(profile, "student.financial_need") is True:
        add(f"need-based scholarship {applicant} {state} {entry_year} application", "need-based scholarship", 4)
    if citizenship:
        add(f"{citizenship} scholarship {applicant} {entry_year} application", "citizenship-based scholarship", 5)
    if get_path(profile, "preferences.include_no_essay") is True:
        add(f"no-essay scholarship {applicant} {entry_year} official application", "no-essay scholarship", 5)

    template_values = {
        "state": state,
        "city": city,
        "county": county,
        "major": major,
        "graduation_year": graduation_year if graduation_year is not None else "",
        "entry_year": entry_year,
        "next_year": entry_year + 1,
        "entry_term": entry_term,
        "applicant_stage": applicant,
        "state_aid_stage": state_aid,
        "degree": degree,
    }
    for source in registry["sources"]:
        if _source_triggered(source, profile):
            add(
                source["query_template"].format(**template_values),
                source["purpose"],
                source["tier"],
                source["name"],
                source["official_domains"],
            )

    if get_path(profile, "preferences.include_identity_searches"):
        identity_queries: list[tuple[str, str]] = []
        ethnicity = get_path(profile, "student.identity.ethnicity")
        if ethnicity:
            identity_queries.append((str(ethnicity), "ethnicity"))
        if get_path(profile, "student.identity.lgbtq") is True:
            identity_queries.append(("LGBTQ", "LGBTQ identity"))
        if get_path(profile, "student.identity.disability") is True:
            identity_queries.append(("students with disabilities", "disability"))
        religion = get_path(profile, "student.identity.religion")
        if religion:
            identity_queries.append((str(religion), "religious affiliation"))
        for other in get_path(profile, "student.identity.other") or []:
            identity_queries.append((str(other), "other opted-in identity"))
        for identity_value, purpose in identity_queries:
            add(f"{identity_value} scholarship {applicant} {entry_year} application", f"opt-in {purpose} scholarship", 4)

    # Most specific sources first; within a tier keep generation order.
    queries.sort(key=lambda record: record["tier"])
    warnings: list[str] = []
    if not locality:
        warnings.append("No county or city was supplied, so local coverage is reduced.")
    if school_sources and not high_school:
        warnings.append("No high school was supplied, so school-specific coverage is reduced.")
    if not colleges:
        warnings.append("No target institutions were supplied, so institutional searches were not generated.")
    omitted_queries = queries[max_queries:]
    queries = queries[:max_queries]
    if omitted_queries:
        warnings.append(
            f"{len(omitted_queries)} lower-priority queries were omitted by the max_queries limit; "
            "rerun with a higher value for broader coverage."
        )
    for index, record in enumerate(queries, 1):
        record["id"] = f"q{index:03d}"
    return {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entry_year": entry_year,
        "student_stage": search_mode,
        "privacy": (
            "Queries never include GPA, test scores, SAI, or disability details. Identity-related "
            "queries, including identity-focused organizations, run only with explicit consent."
        ),
        "cycle_terms": terms,
        "warnings": warnings,
        "omitted_query_count": len(omitted_queries),
        "omitted_query_purposes": list(dict.fromkeys(
            record["purpose"] for record in omitted_queries
        )),
        "queries": queries,
    }


# ---------------------------------------------------------------------------
# Candidate validation


def validate_https_url(value: Any, field: str, *, required: bool) -> str:
    if value is not None and not isinstance(value, str):
        raise CandidateError(f"{field} must be a string")
    if isinstance(value, str) and value and (
        re.search(r"\s", value) or "\\" in value or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
    ):
        raise CandidateError(f"{field} contains whitespace or invalid URL characters")
    text = clean_text(value, field, required=required, maximum=2048)
    if not text:
        return ""
    if not URL_CHARACTERS_RE.fullmatch(text):
        raise CandidateError(f"{field} contains characters that must be percent-encoded")
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise CandidateError(f"{field} must be a public HTTPS URL without embedded credentials")
    try:
        hostname = parsed.hostname.casefold()
        _ = parsed.port
    except ValueError as exc:
        raise CandidateError(f"{field} contains an invalid host or port") from exc
    if not re.fullmatch(r"[a-z0-9.-]+", hostname):
        raise CandidateError(f"{field} contains an invalid hostname")
    if hostname.startswith(".") or hostname.endswith(".") or ".." in hostname:
        raise CandidateError(f"{field} contains an invalid hostname")
    if hostname == "localhost" or hostname.endswith((".local", ".internal", ".localhost")):
        raise CandidateError(f"{field} may not point to a local host")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise CandidateError(f"{field} may not point to a private or local IP address")
    return text


def _url_host(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.hostname or "").casefold()


def _host_matches_domain(host: str, domain: str) -> bool:
    normalized = domain.casefold().strip().strip(".")
    return bool(normalized) and (host == normalized or host.endswith("." + normalized))


def _hosts_related(first: str, second: str) -> bool:
    return _host_matches_domain(first, second) or _host_matches_domain(second, first)


def _registry_entry(name: str) -> dict[str, Any] | None:
    sources = _load_trusted_sources()
    for source in sources:
        if source["name"] == name:
            return source
    wanted = normalize_token(name)
    for source in sources:
        if normalize_token(source["name"]) == wanted:
            return source
    return None


def _registry_matches_for_host(host: str) -> list[dict[str, Any]]:
    return [
        source for source in _load_trusted_sources()
        if any(_host_matches_domain(host, domain) for domain in source["official_domains"])
    ]


def discovery_domain_for(host: str) -> str | None:
    for domain in load_registry()["discovery_domains"]:
        if _host_matches_domain(host, domain):
            return domain
    return None


def _validate_source_verification(
    value: Any,
    source_kind: str,
    official_url: str,
) -> dict[str, Any] | None:
    if source_kind == "discovery":
        if value is not None:
            raise CandidateError("discovery records must use source_verification=null")
        return None
    if not isinstance(value, dict):
        raise CandidateError(
            "official and authorized-administrator records require source_verification"
        )
    expected = {"method", "authority_url", "evidence", "trusted_source"}
    if set(value) != expected:
        raise CandidateError(
            "source_verification must contain exactly method, authority_url, evidence, "
            "and trusted_source"
        )
    method = clean_text(value.get("method"), "source_verification.method", required=True, maximum=40)
    if method not in {"trusted_registry", "sponsor_domain", "authorized_by_sponsor"}:
        raise CandidateError("source_verification.method is unsupported")
    authority_url = validate_https_url(
        value.get("authority_url"), "source_verification.authority_url", required=True
    )
    evidence = clean_text(
        value.get("evidence"), "source_verification.evidence", required=True, maximum=1000
    )
    trusted_source = value.get("trusted_source")
    if trusted_source is not None:
        trusted_source = clean_text(
            trusted_source, "source_verification.trusted_source", required=True, maximum=150
        )

    official_host = _url_host(official_url)
    authority_host = _url_host(authority_url)
    if source_kind == "official":
        discovery_domain = discovery_domain_for(official_host)
        verifying = [
            source for source in _registry_matches_for_host(official_host)
            if REGISTRY_ROLES[source["role"]] is not None
        ]
        if discovery_domain and not verifying:
            raise CandidateError(
                f"official_url is on the discovery database {discovery_domain}; record it as "
                "source_kind=discovery or find the sponsor's own page"
            )
    if method == "trusted_registry":
        if not trusted_source:
            raise CandidateError("trusted_registry verification requires trusted_source")
        source = _registry_entry(trusted_source)
        if source is None:
            suggestions = [entry["name"] for entry in _registry_matches_for_host(official_host)]
            hint = f"; registry entries for this domain: {', '.join(suggestions)}" if suggestions else ""
            raise CandidateError(f"trusted source is not registered: {trusted_source}{hint}")
        trusted_source = source["name"]
        role = source["role"]
        required_kind = REGISTRY_ROLES[role]
        if required_kind is None:
            raise CandidateError(
                f"registered source {trusted_source} is discovery-only and cannot verify a record"
            )
        if source_kind != required_kind:
            raise CandidateError(
                f"registered source {trusted_source} requires source_kind={required_kind}"
            )
        domains = source["official_domains"]
        if not any(_host_matches_domain(official_host, domain) for domain in domains):
            raise CandidateError(
                f"official_url does not match the registered domains {', '.join(domains)} "
                f"for {trusted_source}"
            )
        if not any(_host_matches_domain(authority_host, domain) for domain in domains):
            raise CandidateError(
                "source_verification.authority_url does not match the registered domains "
                + ", ".join(domains)
            )
    elif method == "sponsor_domain":
        if source_kind != "official":
            raise CandidateError("sponsor_domain verification requires source_kind=official")
        if trusted_source is not None:
            raise CandidateError("sponsor_domain verification must use trusted_source=null")
        if not _hosts_related(official_host, authority_host):
            raise CandidateError(
                "sponsor_domain verification requires the authority and scholarship pages "
                "to use the same domain"
            )
    else:
        if source_kind != "authorized_administrator":
            raise CandidateError(
                "authorized_by_sponsor verification requires "
                "source_kind=authorized_administrator"
            )
        if trusted_source is not None:
            raise CandidateError("authorized_by_sponsor verification must use trusted_source=null")
        if _hosts_related(official_host, authority_host):
            raise CandidateError(
                "authorized_by_sponsor verification requires an independent sponsor authority page"
            )
    return {
        "method": method,
        "authority_url": authority_url,
        "evidence": evidence,
        "trusted_source": trusted_source,
    }


def parse_iso_date(value: Any, field: str, *, required: bool = False) -> date | None:
    if value in (None, ""):
        if required:
            raise CandidateError(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise CandidateError(f"{field} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CandidateError(f"{field} must be a real date in YYYY-MM-DD format") from exc


def parse_verified_date(value: Any) -> date:
    text = clean_text(value, "verified_at", required=True, maximum=80)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.date()
    except ValueError:
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise CandidateError("verified_at must be an ISO-8601 date or timestamp") from exc


def extract_cycle_years(cycle: str) -> set[int]:
    """Read both full (2026-2027) and abbreviated (2026-27) cycle labels."""
    years = {int(year) for year in re.findall(r"\b(?:19|20)\d{2}\b", cycle)}
    abbreviated = re.finditer(r"\b((?:19|20)\d{2})\s*[-/–—]\s*(\d{2})\b", cycle)
    for match in abbreviated:
        start = int(match.group(1))
        end = (start // 100) * 100 + int(match.group(2))
        if end < start:
            end += 100
        years.add(end)
    return years


def cycle_matches(record: dict[str, Any], entry_year: int) -> bool:
    cycle = clean_text(record.get("cycle"), "cycle", required=True, maximum=100)
    years = extract_cycle_years(cycle)
    explicit = record.get("applicant_entry_year")
    if explicit is not None:
        return isinstance(explicit, int) and explicit == entry_year and (not years or entry_year in years)
    return entry_year in years


def _shorten_display(text: str) -> str:
    """Cut a long award description at a clause boundary instead of rejecting the record."""
    if len(text) <= AMOUNT_DISPLAY_MAX:
        return text
    head = text[:AMOUNT_DISPLAY_MAX - 1]
    cut = max(head.rfind("; "), head.rfind(". "), head.rfind(", "))
    return (head[:cut] if cut >= 60 else head).rstrip(" ;,.") + "…"


def _amount(record: dict[str, Any]) -> dict[str, Any]:
    amount = record.get("amount")
    if not isinstance(amount, dict):
        raise CandidateError("amount must be an object")
    if set(amount) != {"display", "minimum", "maximum"}:
        raise CandidateError("amount must contain exactly display, minimum, and maximum")
    raw_display = clean_text(amount.get("display"), "amount.display", required=True, maximum=2000)
    display = _shorten_display(raw_display)
    minimum = amount.get("minimum")
    maximum = amount.get("maximum")
    for field, value in (("amount.minimum", minimum), ("amount.maximum", maximum)):
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise CandidateError(f"{field} must be a non-negative number or null")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise CandidateError("amount.minimum cannot exceed amount.maximum")
    return {"display": display, "minimum": minimum, "maximum": maximum}


def _evidence(record: dict[str, Any]) -> dict[str, str]:
    evidence = record.get("evidence")
    if not isinstance(evidence, dict):
        raise CandidateError("evidence must be an object")
    missing = sorted(REQUIRED_EVIDENCE - set(evidence))
    if missing:
        raise CandidateError("missing evidence fields: " + ", ".join(missing))
    extras = sorted(set(evidence) - REQUIRED_EVIDENCE)
    if extras:
        raise CandidateError("unsupported evidence fields: " + ", ".join(extras))
    return {key: clean_text(evidence.get(key), f"evidence.{key}", required=True, maximum=1000) for key in sorted(REQUIRED_EVIDENCE)}


def _string_list(value: Any, field: str, *, required: bool = False, maximum: int = 50) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CandidateError(f"{field} must be an array of strings")
    cleaned = [clean_text(item, field, required=True, maximum=500) for item in value]
    if required and not cleaned:
        raise CandidateError(f"{field} must not be empty")
    if len(cleaned) > maximum:
        raise CandidateError(f"{field} may contain at most {maximum} entries")
    return cleaned


def _requirement(requirement: Any, index: int) -> dict[str, Any]:
    if not isinstance(requirement, dict):
        raise CandidateError(f"requirements[{index}] must be an object")
    expected_fields = {"field", "operator", "value", "hard", "description", "evidence"}
    if set(requirement) != expected_fields:
        missing = sorted(expected_fields - set(requirement))
        extra = sorted(set(requirement) - expected_fields)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unsupported " + ", ".join(extra))
        raise CandidateError(f"requirements[{index}] has invalid fields: {'; '.join(details)}")
    field = clean_text(requirement.get("field"), f"requirements[{index}].field", required=True, maximum=150)
    if field not in FIELD_RULES:
        raise CandidateError(f"requirements[{index}].field is unsupported: {field}")
    operator = clean_text(requirement.get("operator"), f"requirements[{index}].operator", required=True, maximum=30)
    if operator not in ALLOWED_OPERATORS:
        raise CandidateError(f"requirements[{index}].operator is unsupported: {operator}")
    kind = FIELD_RULES[field][0]
    compatible_operators = {
        "str": {"eq", "neq", "in", "not_in", "contains", "contains_any", "truthy"},
        "bool": {"eq", "neq", "in", "not_in", "truthy"},
        "int": {"eq", "neq", "gte", "lte", "in", "not_in"},
        "float": {"eq", "neq", "gte", "lte", "in", "not_in"},
        "list_str": {"contains", "contains_any", "overlaps", "truthy"},
    }
    if operator not in compatible_operators[kind]:
        raise CandidateError(
            f"requirements[{index}].operator {operator} is incompatible with {field} ({kind})"
        )
    hard = requirement.get("hard", True)
    if not isinstance(hard, bool):
        raise CandidateError(f"requirements[{index}].hard must be true or false")
    value = requirement.get("value")
    if operator in {"in", "not_in", "contains_any", "overlaps"} and not isinstance(value, list):
        raise CandidateError(f"requirements[{index}].value must be an array for {operator}")
    if operator in {"gte", "lte"} and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise CandidateError(f"requirements[{index}].value must be a finite number for {operator}")
    if operator == "truthy" and value not in (None, True):
        raise CandidateError(f"requirements[{index}].value must be null or true for truthy")
    if operator in {"contains", "contains_any", "overlaps"}:
        values = value if isinstance(value, list) else [value]
        if not values or any(not isinstance(item, str) or not item.strip() for item in values):
            raise CandidateError(
                f"requirements[{index}].value must contain non-blank strings for {operator}"
            )
    if operator in {"eq", "neq", "in", "not_in"}:
        values = value if isinstance(value, list) else [value]
        if not values:
            raise CandidateError(f"requirements[{index}].value must not be empty")
        if kind == "str" and any(not isinstance(item, str) or not item.strip() for item in values):
            raise CandidateError(f"requirements[{index}].value must contain non-blank strings")
        if kind == "bool" and any(not isinstance(item, bool) for item in values):
            raise CandidateError(f"requirements[{index}].value must contain booleans")
        if kind == "int" and any(isinstance(item, bool) or not isinstance(item, int) for item in values):
            raise CandidateError(f"requirements[{index}].value must contain integers")
        if kind == "float" and any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(item)
            for item in values
        ):
            raise CandidateError(f"requirements[{index}].value must contain finite numbers")
    return {
        "field": field,
        "operator": operator,
        "value": value,
        "hard": hard,
        "description": clean_text(requirement.get("description"), f"requirements[{index}].description", required=True, maximum=500),
        "evidence": clean_text(requirement.get("evidence"), f"requirements[{index}].evidence", required=True, maximum=1000),
    }


def _eligibility_logic(
    value: Any,
    requirements: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Validate an optional all/any/not expression over hard requirement indexes."""
    if value is None:
        return None
    referenced: list[int] = []

    def visit(node: Any, depth: int = 0) -> dict[str, Any]:
        if depth > 12:
            raise CandidateError("eligibility_logic exceeds the maximum nesting depth")
        if not isinstance(node, dict) or len(node) != 1:
            raise CandidateError(
                "eligibility_logic nodes must contain exactly one of requirement, all, any, or not"
            )
        operator, operand = next(iter(node.items()))
        if operator == "requirement":
            if isinstance(operand, bool) or not isinstance(operand, int):
                raise CandidateError("eligibility_logic requirement references must be integers")
            if not 0 <= operand < len(requirements):
                raise CandidateError(f"eligibility_logic references missing requirement {operand}")
            if not requirements[operand]["hard"]:
                raise CandidateError("eligibility_logic may reference only hard requirements")
            referenced.append(operand)
            return {"requirement": operand}
        if operator in {"all", "any"}:
            if not isinstance(operand, list) or not operand:
                raise CandidateError(f"eligibility_logic {operator} must contain a non-empty array")
            return {operator: [visit(child, depth + 1) for child in operand]}
        if operator == "not":
            return {"not": visit(operand, depth + 1)}
        raise CandidateError(f"eligibility_logic operator is unsupported: {operator}")

    normalized = visit(value)
    if len(referenced) != len(set(referenced)):
        raise CandidateError("eligibility_logic may reference each hard requirement only once")
    hard_indexes = {index for index, item in enumerate(requirements) if item["hard"]}
    if set(referenced) != hard_indexes:
        missing = sorted(hard_indexes - set(referenced))
        extra = sorted(set(referenced) - hard_indexes)
        details: list[str] = []
        if missing:
            details.append("missing hard requirements " + ", ".join(map(str, missing)))
        if extra:
            details.append("invalid references " + ", ".join(map(str, extra)))
        raise CandidateError("eligibility_logic must cover every hard requirement: " + "; ".join(details))
    return normalized


# ---------------------------------------------------------------------------
# Matching


def _is_unknown(value: Any) -> bool:
    return value is None or value == "" or value == []


def _as_values(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _nonstandard_gpa_scale(requirement: dict[str, Any], profile: dict[str, Any]) -> float | None:
    """Return the profile's GPA scale when a numeric GPA rule cannot be compared directly."""
    if requirement["field"] != "student.academics.unweighted_gpa" or requirement["operator"] not in {"gte", "lte"}:
        return None
    scale = get_path(profile, "student.academics.gpa_scale")
    if isinstance(scale, (int, float)) and not isinstance(scale, bool) and abs(scale - STANDARD_GPA_SCALE) > 1e-9:
        return float(scale)
    return None


def evaluate_requirement(requirement: dict[str, Any], profile: dict[str, Any]) -> str:
    actual = get_path(profile, requirement["field"])
    if _is_unknown(actual):
        return "unknown"
    if _nonstandard_gpa_scale(requirement, profile) is not None:
        return "unknown"
    operator = requirement["operator"]
    expected = requirement.get("value")
    state_field = requirement["field"].endswith(".state")

    def norm(value: Any) -> str:
        return normalize_token(value, state=state_field)

    if operator == "truthy":
        return "matched" if bool(actual) else "failed"
    if operator in {"gte", "lte"}:
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            return "unknown"
        matched = actual >= expected if operator == "gte" else actual <= expected
        return "matched" if matched else "failed"
    if operator == "eq":
        return "matched" if norm(actual) == norm(expected) else "failed"
    if operator == "neq":
        return "matched" if norm(actual) != norm(expected) else "failed"
    if operator in {"in", "not_in"}:
        choices = {norm(item) for item in _as_values(expected)}
        matched = norm(actual) in choices
        if operator == "not_in":
            matched = not matched
        return "matched" if matched else "failed"
    if operator == "contains":
        needle = norm(expected)
        matched = any(needle in norm(item) for item in _as_values(actual))
        return "matched" if matched else "failed"
    if operator == "contains_any":
        needles = [norm(item) for item in _as_values(expected)]
        matched = any(needle and needle in norm(item) for needle in needles for item in _as_values(actual))
        return "matched" if matched else "failed"
    if operator == "overlaps":
        actuals = {norm(item) for item in _as_values(actual)}
        expected_values = {norm(item) for item in _as_values(expected)}
        return "matched" if actuals & expected_values else "failed"
    return "unknown"


class Clock:
    """Current time for deadline checks, with an optional fixed date for tests."""

    def __init__(self, today: date | None = None, now: datetime | None = None):
        self.override = today
        self.now = now or datetime.now(timezone.utc)

    @property
    def utc_today(self) -> date:
        return self.override or self.now.date()

    def today_in(self, timezone_name: str) -> date:
        if self.override is not None:
            return self.override
        zone = _zone(timezone_name)
        if zone is None:
            # Unknown zone: use the last place on Earth where the date is still
            # current, so a deadline is never treated as passed too early.
            return (self.now - timedelta(hours=12)).date()
        return self.now.astimezone(zone).date()


def _zone(name: str) -> Any:
    text = (name or "").strip()
    if not text:
        return None
    text = TIMEZONE_ALIASES.get(text.casefold(), text)
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(text)
    except Exception:  # noqa: BLE001 - missing tzdata or an unknown zone name
        return None


def _coerce_clock(value: date | Clock | None) -> Clock:
    if isinstance(value, Clock):
        return value
    return Clock(value)


def _validate_candidate(record: Any, profile: dict[str, Any], today: date | Clock | None, max_age: int) -> dict[str, Any]:
    clock = _coerce_clock(today)
    if not isinstance(record, dict):
        raise CandidateError("candidate must be an object")
    missing_fields = sorted(REQUIRED_CANDIDATE_FIELDS - set(record))
    extra_fields = sorted(set(record) - CANDIDATE_FIELDS)
    if missing_fields:
        raise CandidateError("missing candidate fields: " + ", ".join(missing_fields))
    if extra_fields:
        raise CandidateError("unsupported candidate fields: " + ", ".join(extra_fields))
    name = clean_text(record.get("name"), "name", required=True, maximum=300)
    sponsor = clean_text(record.get("sponsor"), "sponsor", required=True, maximum=300)
    source_kind = clean_text(record.get("source_kind"), "source_kind", required=True, maximum=40)
    if source_kind not in ALLOWED_SOURCE_KINDS:
        raise CandidateError(f"source_kind must be one of {sorted(ALLOWED_SOURCE_KINDS)}")
    status = clean_text(record.get("status"), "status", required=True, maximum=40)
    if status not in ALLOWED_STATUSES:
        raise CandidateError(f"status must be one of {sorted(ALLOWED_STATUSES)}")
    official_url = validate_https_url(record.get("official_url"), "official_url", required=True)
    application_url = validate_https_url(record.get("application_url"), "application_url", required=False)
    source_verification = _validate_source_verification(
        record.get("source_verification"), source_kind, official_url
    )
    verified_date = parse_verified_date(record.get("verified_at"))
    utc_today = clock.utc_today
    if verified_date > utc_today + timedelta(days=1):
        raise CandidateError("verified_at cannot be in the future")
    if (utc_today - verified_date).days > max_age:
        raise CandidateError(f"verification is older than {max_age} days")
    entry_year = infer_entry_year(profile)
    applicant_entry_year = record.get("applicant_entry_year")
    if applicant_entry_year is not None and (
        isinstance(applicant_entry_year, bool)
        or not isinstance(applicant_entry_year, int)
        or not 2000 <= applicant_entry_year <= 2100
    ):
        raise CandidateError("applicant_entry_year must be an integer from 2000 to 2100 or null")
    cycle = clean_text(record.get("cycle"), "cycle", required=True, maximum=100)
    if status != "cycle_unknown" and not cycle_matches(record, entry_year):
        raise CandidateError(f"cycle does not support the student's {entry_year} entry year")
    deadline_obj = record.get("deadline")
    if not isinstance(deadline_obj, dict):
        raise CandidateError("deadline must be an object")
    if set(deadline_obj) != {"date", "timezone", "type"}:
        raise CandidateError("deadline must contain exactly date, timezone, and type")
    deadline_type = clean_text(deadline_obj.get("type"), "deadline.type", required=True, maximum=40)
    if deadline_type not in DEADLINE_TYPES:
        raise CandidateError("deadline.type is unsupported")
    deadline_date = parse_iso_date(
        deadline_obj.get("date"),
        "deadline.date",
        required=status == "open" and deadline_type != "rolling",
    )
    deadline = {
        "date": deadline_date.isoformat() if deadline_date else None,
        "timezone": clean_text(deadline_obj.get("timezone"), "deadline.timezone", maximum=100),
        "type": deadline_type,
    }
    if status == "open" and deadline_type == "not_announced":
        raise CandidateError("open scholarships cannot use deadline.type=not_announced")
    if status == "open" and source_kind != "discovery" and not application_url:
        raise CandidateError("open scholarships require application_url")
    evidence = _evidence(record)
    application_tasks = _string_list(record.get("application_tasks"), "application_tasks", required=True, maximum=30)
    requirements_raw = record.get("requirements", [])
    if not isinstance(requirements_raw, list):
        raise CandidateError("requirements must be an array")
    requirements = [_requirement(item, index) for index, item in enumerate(requirements_raw)]
    eligibility_logic = _eligibility_logic(record.get("eligibility_logic"), requirements)
    uncertainties = _string_list(record.get("uncertainties", []), "uncertainties", maximum=30)
    notes: list[str] = []
    for note in _string_list(record.get("notes", []), "notes", maximum=10):
        (uncertainties if DOUBT_RE.search(note) else notes).append(note)
    amount_raw = record.get("amount")
    full_display = clean_text(amount_raw.get("display"), "amount.display", maximum=2000) if isinstance(amount_raw, dict) else ""
    if len(full_display) > AMOUNT_DISPLAY_MAX and full_display not in evidence.get("amount", ""):
        evidence["amount"] = clean_text(f"{evidence.get('amount', '')} Full award text: {full_display}", "evidence.amount", maximum=3000)
    if record.get("application_fee") not in (True, False, None):
        raise CandidateError("application_fee must be true, false, or null")
    if record.get("is_loan") not in (True, False, None):
        raise CandidateError("is_loan must be true, false, or null")
    if record.get("is_sweepstakes") not in (True, False, None):
        raise CandidateError("is_sweepstakes must be true, false, or null")
    return {
        "id": scholarship_id(name, sponsor),
        "name": name,
        "sponsor": sponsor,
        "source_kind": source_kind,
        "source_verification": source_verification,
        "cycle": cycle,
        "applicant_entry_year": applicant_entry_year,
        "status": status,
        "amount": _amount(record),
        "deadline": deadline,
        "requirements": requirements,
        "eligibility_logic": eligibility_logic,
        "application_tasks": application_tasks,
        "official_url": official_url,
        "application_url": application_url,
        "verified_at": record.get("verified_at"),
        "evidence": evidence,
        "uncertainties": uncertainties,
        "notes": notes,
        "application_fee": record.get("application_fee"),
        "is_loan": record.get("is_loan"),
        "is_sweepstakes": record.get("is_sweepstakes"),
    }


def _evaluate_eligibility_logic(
    logic: dict[str, Any] | None,
    requirements: list[dict[str, Any]],
    outcomes: list[str],
) -> str:
    if logic is None:
        hard_outcomes = [
            outcomes[index]
            for index, requirement in enumerate(requirements)
            if requirement["hard"]
        ]
        if any(outcome == "failed" for outcome in hard_outcomes):
            return "failed"
        if any(outcome == "unknown" for outcome in hard_outcomes):
            return "unknown"
        return "matched"

    if "requirement" in logic:
        return outcomes[logic["requirement"]]
    if "not" in logic:
        outcome = _evaluate_eligibility_logic(logic["not"], requirements, outcomes)
        return "failed" if outcome == "matched" else "matched" if outcome == "failed" else "unknown"
    if "all" in logic:
        child_outcomes = [
            _evaluate_eligibility_logic(child, requirements, outcomes)
            for child in logic["all"]
        ]
        if any(outcome == "failed" for outcome in child_outcomes):
            return "failed"
        if any(outcome == "unknown" for outcome in child_outcomes):
            return "unknown"
        return "matched"
    child_outcomes = [
        _evaluate_eligibility_logic(child, requirements, outcomes)
        for child in logic["any"]
    ]
    if any(outcome == "matched" for outcome in child_outcomes):
        return "matched"
    if any(outcome == "unknown" for outcome in child_outcomes):
        return "unknown"
    return "failed"


def _classify(record: dict[str, Any], profile: dict[str, Any], today: date | Clock | None) -> tuple[str, dict[str, Any]]:
    clock = _coerce_clock(today)
    result = copy.deepcopy(record)
    reasons: list[str] = []
    if result["application_fee"] is True:
        reasons.append("An application fee is required.")
        result["validation_notes"] = reasons
        return "rejected", result
    if result["is_loan"] is True:
        reasons.append("This opportunity requires repayment.")
        result["validation_notes"] = reasons
        return "rejected", result
    if result["is_sweepstakes"] is True:
        reasons.append("This is a sweepstakes or giveaway rather than a verified scholarship application.")
        result["validation_notes"] = reasons
        return "rejected", result
    if result["source_kind"] == "discovery":
        reasons.append("Only a discovery source was supplied; an official or authorized source is still required.")
        result["validation_notes"] = reasons
        return "unverified", result
    if result["status"] == "cycle_unknown":
        reasons.append("The official page does not identify a current applicable cycle.")
        result["validation_notes"] = reasons
        return "unverified", result
    deadline_text = result["deadline"]["date"]
    deadline_date = date.fromisoformat(deadline_text) if deadline_text else None
    local_today = clock.today_in(result["deadline"]["timezone"])
    if result["status"] == "closed" or (deadline_date and deadline_date < local_today):
        reasons.append("The application is closed or its deadline has passed.")
        result["validation_notes"] = reasons
        return "expired", result
    if deadline_date is not None:
        result["days_left"] = (deadline_date - local_today).days
        result["closing_soon"] = result["days_left"] <= CLOSING_SOON_DAYS

    matched: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    requirement_outcomes: list[str] = []
    for requirement in result["requirements"]:
        outcome = evaluate_requirement(requirement, profile)
        requirement_outcomes.append(outcome)
        target = matched if outcome == "matched" else failed if outcome == "failed" else unknown
        target.append(requirement)
    result["matched_requirements"] = matched
    result["failed_requirements"] = failed
    result["unknown_requirements"] = unknown
    hard_unknown = [item for item in unknown if item["hard"]]
    logic_outcome = _evaluate_eligibility_logic(
        result["eligibility_logic"], result["requirements"], requirement_outcomes
    )
    result["eligibility_logic_outcome"] = logic_outcome
    result["match_reasons"] = [item["description"] for item in matched]
    if not result["match_reasons"]:
        result["match_reasons"] = ["No known profile value conflicts with the documented requirements."]
    if logic_outcome == "failed":
        result["eligibility_state"] = "ineligible"
        result["validation_notes"] = [
            "The documented hard-requirement eligibility logic did not match the profile."
        ]
        return "ineligible", result

    needs_confirmation = bool(
        logic_outcome == "unknown"
        or result["uncertainties"]
        or result["status"] == "not_open_yet"
    )
    dangerous_unknowns = {
        "application_fee": "Confirm that the application is free before submitting.",
        "is_loan": "Confirm that the opportunity is an award and never requires repayment.",
        "is_sweepstakes": "Confirm that the opportunity is merit- or eligibility-based rather than a sweepstakes.",
    }
    for field, message in dangerous_unknowns.items():
        if result[field] is None:
            result["uncertainties"].append(message)
            needs_confirmation = True
    if deadline_date is not None and not result["deadline"]["timezone"]:
        result["uncertainties"].append("Confirm the deadline timezone with the sponsor.")
        needs_confirmation = True
    if deadline_date is None and result["deadline"]["type"] == "rolling":
        result["uncertainties"].append(
            "Rolling deadline: confirm that funds remain available and apply early."
        )
        needs_confirmation = True
    minimum_award = get_path(profile, "preferences.minimum_award")
    maximum_amount = result["amount"]["maximum"]
    minimum_amount = result["amount"]["minimum"]
    if minimum_award is not None:
        if maximum_amount is not None and maximum_amount < minimum_award:
            result["validation_notes"] = [f"Maximum documented award is below the preferred minimum of ${minimum_award:,.0f}."]
            return "rejected", result
        if maximum_amount is None and (minimum_amount is None or minimum_amount < minimum_award):
            result["uncertainties"].append("The award amount must be confirmed against the preferred minimum.")
            needs_confirmation = True
    for requirement in result["requirements"]:
        scale = _nonstandard_gpa_scale(requirement, profile)
        if scale is not None and requirement["hard"]:
            result["uncertainties"].append(
                f"GPA rule '{requirement['description']}' assumes a 4.0 scale; your GPA uses a "
                f"{scale:g}-point scale, so confirm it with the sponsor."
            )
    if logic_outcome == "unknown" and hard_unknown:
        result["uncertainties"].extend(
            "Confirm requirement: " + item["description"]
            for item in hard_unknown
            if _nonstandard_gpa_scale(item, profile) is None
        )
    result["uncertainties"] = list(dict.fromkeys(result["uncertainties"]))
    result["eligibility_state"] = "needs_confirmation" if needs_confirmation else "eligible"
    result["validation_notes"] = reasons
    return ("needs_confirmation" if needs_confirmation else "verified"), result


def _record_score(bucket: str, record: dict[str, Any]) -> tuple[int, int, int, int]:
    bucket_score = {
        "verified": 6,
        "needs_confirmation": 5,
        "ineligible": 4,
        "expired": 3,
        "unverified": 2,
        "rejected": 1,
    }[bucket]
    return (
        bucket_score,
        2 if record.get("source_kind") == "official" else 1,
        1 if record.get("status") == "open" else 0,
        len(record.get("evidence", {})),
    )


def _deduplicate(classified: Iterable[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    selected: dict[str, tuple[str, dict[str, Any]]] = {}
    for bucket, record in classified:
        key = f"{normalize_dedup(record.get('sponsor', ''))}|{normalize_dedup(record.get('name', ''))}"
        current = selected.get(key)
        if current is None or _record_score(bucket, record) > _record_score(current[0], current[1]):
            selected[key] = (bucket, record)
    return list(selected.values())


def _sort_key(record: dict[str, Any]) -> tuple[str, float, str]:
    deadline = (record.get("deadline") or {}).get("date") or "9999-12-31"
    maximum = (record.get("amount") or {}).get("maximum")
    amount = float(maximum) if isinstance(maximum, (int, float)) else -1.0
    return deadline, -amount, normalize_token(record.get("name", ""))


def _candidate_list(candidates_data: Any) -> list[Any]:
    if isinstance(candidates_data, list):
        return candidates_data
    if isinstance(candidates_data, dict) and isinstance(candidates_data.get("scholarships"), list):
        return candidates_data["scholarships"]
    raise ValueError("candidate file must be a JSON array or an object with a scholarships array")


def evaluate_candidates(
    profile: dict[str, Any],
    candidates_data: Any,
    today: date | Clock | None,
    max_age: int,
    audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _require_ready_profile(profile)
    clock = _coerce_clock(today)
    candidates = _candidate_list(candidates_data)
    findings = (audit or {}).get("records") or {}
    classified: list[tuple[str, dict[str, Any]]] = []
    invalid: list[dict[str, Any]] = []
    for index, raw in enumerate(candidates):
        try:
            validated = _validate_candidate(raw, profile, clock, max_age)
            validated = page_audit.apply_findings(validated, findings.get(validated["id"]))
            classified.append(_classify(validated, profile, clock))
        except CandidateError as exc:
            name = raw.get("name", "unnamed") if isinstance(raw, dict) else "unnamed"
            sponsor = raw.get("sponsor", "") if isinstance(raw, dict) else ""
            invalid.append({
                "id": scholarship_id(str(name), str(sponsor)),
                "index": index,
                "name": str(name)[:300],
                "sponsor": str(sponsor)[:300],
                "validation_notes": [str(exc)],
            })
    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in BUCKETS}
    for bucket, record in _deduplicate(classified):
        buckets[bucket].append(record)
    for records in buckets.values():
        records.sort(key=_sort_key)
    maximum_results = get_path(profile, "preferences.maximum_results") or 20
    matching_before_limit = len(buckets["verified"]) + len(buckets["needs_confirmation"])
    omitted_due_to_limit: list[dict[str, Any]] = []
    if matching_before_limit > maximum_results:
        combined = [("verified", record) for record in buckets["verified"]] + [("needs_confirmation", record) for record in buckets["needs_confirmation"]]
        combined.sort(key=lambda item: (0 if item[0] == "verified" else 1, _sort_key(item[1])))
        kept = combined[:maximum_results]
        omitted_due_to_limit = [
            {**record, "original_bucket": bucket}
            for bucket, record in combined[maximum_results:]
        ]
        buckets["verified"] = [record for bucket, record in kept if bucket == "verified"]
        buckets["needs_confirmation"] = [record for bucket, record in kept if bucket == "needs_confirmation"]
    summary: dict[str, int] = {name: len(records) for name, records in buckets.items()}
    summary["invalid"] = len(invalid)
    summary["input_candidates"] = len(candidates)
    summary["matching_before_limit"] = matching_before_limit
    summary["returned_matching_results"] = (
        len(buckets["verified"]) + len(buckets["needs_confirmation"])
    )
    summary["omitted_by_result_limit"] = len(omitted_due_to_limit)
    summary["closing_soon"] = sum(
        1 for name in ("verified", "needs_confirmation") for record in buckets[name] if record.get("closing_soon")
    )
    summary["page_checked"] = sum(1 for record in candidates if isinstance(record, dict)
                                  and scholarship_id(str(record.get("name", "")), str(record.get("sponsor", ""))) in findings)
    return {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "evaluated_at": clock.utc_today.isoformat(),
        "student_entry_year": infer_entry_year(profile),
        "summary": summary,
        **buckets,
        "omitted_due_to_limit": omitted_due_to_limit,
        "invalid": invalid,
    }


def add_candidate(
    profile_path: str | Path,
    candidates_path: str | Path,
    record: dict[str, Any] | str,
    *,
    today: date | None = None,
    max_age: int = 7,
) -> dict[str, Any]:
    """Validate one candidate record, then add it to (or replace it in) the candidate file.

    Invalid records are rejected with the reason and nothing is written, so
    every stored record already passes structural validation.
    """
    profile = load_profile(Path(profile_path).expanduser())
    _require_ready_profile(profile)
    clock = Clock(today)
    path = Path(candidates_path).expanduser()
    try:
        if isinstance(record, str):
            record = _loads(record)
        validated = _validate_candidate(record, profile, clock, max_age)
        bucket, classified = _classify(validated, profile, clock)
    except (CandidateError, ValueError) as exc:
        return {
            "ok": False,
            "status": "rejected",
            "error": str(exc),
            "next_action": "fix_the_record_and_call_add_candidate_again",
        }
    with file_lock(path):
        stored = load_json(path) if path.exists() else {"scholarships": []}
        records = _candidate_list(stored)
        replaced = False
        for index, existing in enumerate(records):
            if isinstance(existing, dict) and scholarship_id(
                str(existing.get("name", "")), str(existing.get("sponsor", ""))
            ) == validated["id"]:
                records[index] = record
                replaced = True
                break
        if not replaced:
            records.append(record)
        atomic_write(path, json.dumps({"scholarships": records}, indent=2, ensure_ascii=False))
    counts: dict[str, int] = {name: 0 for name in BUCKETS}
    for existing in records:
        try:
            existing_bucket, _ = _classify(_validate_candidate(existing, profile, clock, max_age), profile, clock)
        except CandidateError:
            continue
        counts[existing_bucket] += 1
    target = get_path(profile, "preferences.maximum_results") or 20
    matches = counts["verified"] + counts["needs_confirmation"]
    return {
        "ok": True,
        "status": "replaced" if replaced else "added",
        "id": validated["id"],
        "bucket": bucket,
        "notes": (classified.get("validation_notes") or []) + (classified.get("uncertainties") or [])[:5],
        "counts": counts,
        "stored_records": len(records),
        "target_results": target,
        "enough_matches": matches >= target,
    }


GENERIC_DOUBTS = {
    "Confirm that the application is free before submitting.": "application_fee",
    "Confirm that the opportunity is an award and never requires repayment.": "is_loan",
    "Confirm that the opportunity is merit- or eligibility-based rather than a sweepstakes.": "is_sweepstakes",
    "Confirm the deadline timezone with the sponsor.": "timezone",
    "The award amount must be confirmed against the preferred minimum.": "amount_vs_minimum",
    "Rolling deadline: confirm that funds remain available and apply early.": "rolling_deadline",
}
FIXABLE_NOW = {"application_fee", "is_loan", "is_sweepstakes", "timezone", "page_check", "record_doubt"}


def _blockers(record: dict[str, Any]) -> list[str]:
    """Why a needs-confirmation record is not verified, as short reason codes."""
    reasons: list[str] = []
    if record.get("status") == "not_open_yet":
        reasons.append("cycle_not_open")
    for item in record.get("unknown_requirements") or []:
        if item.get("hard"):
            reasons.append("ask_user:" + str(item.get("field", "")))
    for doubt in record.get("uncertainties") or []:
        if doubt in GENERIC_DOUBTS:
            reasons.append(GENERIC_DOUBTS[doubt])
        elif doubt.startswith("Page check:"):
            reasons.append("page_check")
        elif doubt.startswith(("Confirm requirement:", "GPA rule")):
            continue
        else:
            reasons.append("record_doubt")
    return list(dict.fromkeys(reasons))


def worklist(evaluated: dict[str, Any], limit: int = 25) -> dict[str, Any]:
    """What still blocks "verified": reason counts, records fixable by one more page check,
    profile fields worth asking the user about, and records only waiting for a cycle to open."""
    counts: dict[str, int] = {}
    fixable: list[dict[str, Any]] = []
    ask: dict[str, int] = {}
    waiting = 0
    for record in evaluated.get("needs_confirmation", []):
        reasons = _blockers(record)
        for reason in reasons:
            key = "ask_user" if reason.startswith("ask_user:") else reason
            counts[key] = counts.get(key, 0) + 1
            if reason.startswith("ask_user:"):
                field = reason.split(":", 1)[1]
                ask[field] = ask.get(field, 0) + 1
        fix = [reason for reason in reasons if reason in FIXABLE_NOW]
        if fix:
            fixable.append({"name": record.get("name"), "official_url": record.get("official_url"), "fix": fix})
        elif reasons and set(reasons) <= {"cycle_not_open", "amount_vs_minimum", "rolling_deadline"}:
            waiting += 1
    return {
        "blocking_reasons": dict(sorted(counts.items(), key=lambda item: -item[1])),
        "fixable_by_page_check": fixable[:limit],
        "fixable_count": len(fixable),
        "ask_user_about": dict(sorted(ask.items(), key=lambda item: -item[1])),
        "waiting_for_cycle_or_amount": waiting,
        "how_to_fix": (
            "For each fixable record read the sponsor's FAQ, eligibility, or application page "
            "(web_extract the official site, or search site:<sponsor domain> fee OR repay OR deadline), "
            "then update the record with page-backed values and evidence, add_candidates again, audit, "
            "and evaluate. Ask the user once about ask_user_about fields."
        ),
    }


def add_candidates(
    profile_path: str | Path,
    candidates_path: str | Path,
    records: list[dict[str, Any]] | str,
    *,
    today: date | None = None,
    max_age: int = 7,
) -> dict[str, Any]:
    """add_candidate for a list of records, with one verdict line per record and a final count."""
    if isinstance(records, str):
        records = _loads(records)
    if not isinstance(records, list):
        raise ValueError("records must be a JSON array of record objects")
    verdicts: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for record in records:
        name = str(record.get("name", "unnamed"))[:120] if isinstance(record, dict) else "unnamed"
        result = add_candidate(profile_path, candidates_path, record, today=today, max_age=max_age)
        verdicts.append({"name": name, "ok": result["ok"], "bucket": result.get("bucket"),
                         "error": result.get("error", "")[:300]})
        if result["ok"]:
            last = result
    path = Path(candidates_path).expanduser()
    stored = len(_candidate_list(load_json(path))) if path.exists() else 0
    failed = [verdict for verdict in verdicts if not verdict["ok"]]
    return {
        "ok": not failed,
        "added_or_replaced": len(verdicts) - len(failed),
        "rejected": failed,
        "verdicts": verdicts,
        "stored_records": stored,
        "counts": last.get("counts", {}),
        "next_action": "fix_rejected_records_and_add_them_again" if failed else "continue",
    }


def remove_candidate(candidates_path: str | Path, name: str, sponsor: str | None = None) -> dict[str, Any]:
    """Delete stored records by name (and sponsor), e.g. a lead superseded by a renamed record."""
    path = Path(candidates_path).expanduser()
    target = normalize_dedup(name)
    if not target:
        raise ValueError("name is required")
    with file_lock(path):
        stored = load_json(path) if path.exists() else {"scholarships": []}
        records = _candidate_list(stored)
        kept = [
            record for record in records
            if not (isinstance(record, dict) and normalize_dedup(str(record.get("name", ""))) == target
                    and (sponsor is None or normalize_dedup(str(record.get("sponsor", ""))) == normalize_dedup(sponsor)))
        ]
        removed = len(records) - len(kept)
        if removed:
            atomic_write(path, json.dumps({"scholarships": kept}, indent=2, ensure_ascii=False))
    return {"ok": removed > 0, "removed": removed, "stored_records": len(kept),
            **({} if removed else {"error": f"no stored record named {name!r}"})}


def _gold_match(gold: dict[str, Any], records: list[tuple[str, dict[str, Any]]]) -> tuple[str, dict[str, Any]] | None:
    _phrase, gold_tokens = page_audit.name_tokens(str(gold.get("name", "")))
    gold_host = _url_host(str(gold.get("official_url") or "")) if gold.get("official_url") else ""
    best: tuple[float, tuple[str, dict[str, Any]]] | None = None
    for bucket, record in records:
        _p, tokens = page_audit.name_tokens(str(record.get("name", "")))
        overlap = len(set(gold_tokens) & set(tokens)) / max(len(set(gold_tokens) | set(tokens)), 1)
        same_page = bool(gold_host) and page_audit.web_batch.normalize_page_url(str(gold.get("official_url"))) == \
            page_audit.web_batch.normalize_page_url(str(record.get("official_url") or ""))
        score = 1.0 if same_page else overlap
        if score >= 0.5 and (best is None or score > best[0]):
            best = (score, (bucket, record))
    return best[1] if best else None


def score_against_gold(evaluated: dict[str, Any], gold: dict[str, Any] | list[Any]) -> dict[str, Any]:
    """Score a run against a hand-checked list: found, deadline, amount, and source correctness."""
    entries = gold.get("scholarships") if isinstance(gold, dict) else gold
    if not isinstance(entries, list) or not entries:
        raise ValueError("gold file must hold a non-empty scholarships list")
    records = [(bucket, record) for bucket in ("verified", "needs_confirmation", "unverified", "ineligible",
                                               "expired", "rejected", "omitted_due_to_limit")
               for record in evaluated.get(bucket, []) if isinstance(record, dict)]
    rows: list[dict[str, Any]] = []
    totals = {"gold": len(entries), "found": 0, "deadline_checked": 0, "deadline_correct": 0,
              "amount_checked": 0, "amount_correct": 0, "source_checked": 0, "source_correct": 0,
              "eligibility_checked": 0, "eligibility_correct": 0}
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("name"):
            raise ValueError("every gold entry needs a name")
        match = _gold_match(entry, records)
        row: dict[str, Any] = {"name": entry["name"], "found": bool(match)}
        if match:
            bucket, record = match
            totals["found"] += 1
            row.update({"matched": record.get("name"), "bucket": bucket})
            if entry.get("deadline"):
                totals["deadline_checked"] += 1
                ok = (record.get("deadline") or {}).get("date") == entry["deadline"]
                totals["deadline_correct"] += ok
                row["deadline_ok"] = ok
            if entry.get("amount_max") is not None:
                totals["amount_checked"] += 1
                ok = (record.get("amount") or {}).get("maximum") == entry["amount_max"]
                totals["amount_correct"] += ok
                row["amount_ok"] = ok
            if entry.get("official_url"):
                totals["source_checked"] += 1
                ok = _hosts_related(_url_host(str(entry["official_url"])), _url_host(str(record.get("official_url") or "")))
                totals["source_correct"] += ok
                row["source_ok"] = ok
            if entry.get("eligible") is not None:
                totals["eligibility_checked"] += 1
                ok = (bucket != "ineligible") == bool(entry["eligible"])
                totals["eligibility_correct"] += ok
                row["eligibility_ok"] = ok
        rows.append(row)
    rate = lambda good, checked: round(good / checked, 3) if checked else None  # noqa: E731
    return {
        "ok": True,
        "recall": rate(totals["found"], totals["gold"]),
        "deadline_accuracy": rate(totals["deadline_correct"], totals["deadline_checked"]),
        "amount_accuracy": rate(totals["amount_correct"], totals["amount_checked"]),
        "source_accuracy": rate(totals["source_correct"], totals["source_checked"]),
        "eligibility_accuracy": rate(totals["eligibility_correct"], totals["eligibility_checked"]),
        "totals": totals,
        "rows": rows,
        "missed": [row["name"] for row in rows if not row["found"]],
    }


# ---------------------------------------------------------------------------
# Rendering


def _md(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    text = text.replace("\\", "\\\\")
    for character in ("`", "*", "_", "[", "]", "<", ">", "#", "|"):
        text = text.replace(character, "\\" + character)
    return text


def _link(url: Any) -> str:
    """Render a validated URL as a Markdown autolink, or nothing when it is unsafe."""
    text = str(url or "")
    if not text.startswith("https://") or not URL_CHARACTERS_RE.fullmatch(text):
        return ""
    return f"<{text}>"


def _count(number: int, singular: str, plural: str) -> str:
    return f"{number} {singular if number == 1 else plural}"


def _days_left_text(record: dict[str, Any]) -> str:
    days = record.get("days_left")
    if days is None:
        return "—"
    if days == 0:
        return "today"
    return f"{days}"


def _render_record(record: dict[str, Any], number: int, *, unverified: bool = False) -> list[str]:
    lines = [f"### {number}. {_md(record.get('name', 'Unnamed scholarship'))}", ""]
    lines.append(f"- **Sponsor:** {_md(record.get('sponsor', 'Not stated'))}")
    if unverified:
        notes = record.get("validation_notes") or ["Official verification is incomplete."]
        lines.append(f"- **Why unverified:** {_md('; '.join(notes))}")
        if _link(record.get("official_url")):
            lines.append(f"- **Lead/source:** {_link(record.get('official_url'))}")
        lines.append("")
        return lines
    amount = (record.get("amount") or {}).get("display", "Not stated")
    deadline = (record.get("deadline") or {}).get("date") or "Not announced"
    deadline_type = (record.get("deadline") or {}).get("type", "")
    deadline_zone = (record.get("deadline") or {}).get("timezone", "")
    deadline_detail = ", ".join(part for part in (deadline_type, deadline_zone) if part)
    closing = " — closing soon" if record.get("closing_soon") else ""
    lines.extend([
        f"- **Amount:** {_md(amount)}",
        f"- **Deadline:** {_md(deadline)} ({_md(deadline_detail)}){closing}",
        f"- **Application status:** {_md(record.get('status', 'unknown'))}",
        f"- **Eligibility:** {_md(record.get('eligibility_state', 'needs_confirmation'))}",
        f"- **Why it matches:** {_md('; '.join(record.get('match_reasons') or []))}",
    ])
    requirements = [item.get("description", "") for item in record.get("requirements", [])]
    lines.append(f"- **Requirements:** {_md('; '.join(requirements) if requirements else 'See official eligibility evidence.')}")
    lines.append(f"- **Application tasks:** {_md('; '.join(record.get('application_tasks') or []))}")
    uncertainties = record.get("uncertainties") or []
    lines.append(f"- **Uncertainties:** {_md('; '.join(dict.fromkeys(uncertainties)) if uncertainties else 'None identified from the documented requirements.')}")
    if record.get("notes"):
        lines.append(f"- **Notes:** {_md('; '.join(record['notes']))}")
    lines.append(f"- **Official source:** {_link(record.get('official_url'))}")
    source_verification = record.get("source_verification") or {}
    if source_verification:
        lines.append(
            f"- **Source authority:** {_md(source_verification.get('evidence', ''))} "
            f"({_link(source_verification.get('authority_url'))})"
        )
    if _link(record.get("application_url")):
        lines.append(f"- **Application:** {_link(record.get('application_url'))}")
    lines.append(f"- **Verified on:** {_md(str(record.get('verified_at', '')))}")
    evidence = record.get("evidence") or {}
    lines.append(f"- **Evidence:** Amount—{_md(evidence.get('amount', ''))} Deadline—{_md(evidence.get('deadline', ''))} Eligibility—{_md(evidence.get('eligibility', ''))} Application—{_md(evidence.get('application', ''))}")
    lines.append("")
    return lines


def _overview_table(evaluated: dict[str, Any]) -> list[str]:
    rows = [("verified", record) for record in evaluated.get("verified", [])] + [
        ("needs_confirmation", record) for record in evaluated.get("needs_confirmation", [])
    ]
    if not rows:
        return []
    rows.sort(key=lambda item: _sort_key(item[1]))
    lines = [
        "## At a glance",
        "",
        "| Scholarship | Amount | Deadline | Days left | Result |",
        "| --- | --- | --- | --- | --- |",
    ]
    for bucket, record in rows:
        deadline = (record.get("deadline") or {}).get("date") or (record.get("deadline") or {}).get("type", "")
        result = "verified" if bucket == "verified" else "confirm first"
        days = _days_left_text(record)
        if record.get("closing_soon"):
            days += " (soon)"
        lines.append(
            f"| {_md(record.get('name', ''))} | {_md((record.get('amount') or {}).get('display', ''))} "
            f"| {_md(deadline)} | {_md(days)} | {result} |"
        )
    lines.append("")
    return lines


def render_report(evaluated: dict[str, Any], include_excluded: bool = False) -> str:
    required = {"summary", "verified", "needs_confirmation", "unverified", "evaluated_at"}
    if not isinstance(evaluated, dict) or not required.issubset(evaluated):
        raise ValueError("evaluated file does not match the pipeline output schema")
    summary = evaluated["summary"]
    lines = [
        "# Verified Scholarship Report",
        "",
        f"Evaluated on {evaluated['evaluated_at']} for college entry in {evaluated.get('student_entry_year', 'the stated year')}.",
        "",
        f"Found {_count(summary.get('verified', 0), 'verified match', 'verified matches')} and "
        f"{_count(summary.get('needs_confirmation', 0), 'match', 'matches')} needing confirmation.",
        "",
    ]
    if summary.get("closing_soon"):
        lines.extend([
            f"{summary['closing_soon']} of them close within {CLOSING_SOON_DAYS} days.",
            "",
        ])
    lines.extend(_overview_table(evaluated))
    lines.extend(["## Verified matches", ""])
    if not evaluated["verified"]:
        lines.extend(["No opportunities passed every verification and eligibility check.", ""])
    for index, record in enumerate(evaluated["verified"], 1):
        lines.extend(_render_record(record, index))
    lines.extend(["## Needs confirmation", ""])
    if not evaluated["needs_confirmation"]:
        lines.extend(["None.", ""])
    for index, record in enumerate(evaluated["needs_confirmation"], 1):
        lines.extend(_render_record(record, index))
    lines.extend(["## Unverified leads", ""])
    if not evaluated["unverified"]:
        lines.extend(["None.", ""])
    for index, record in enumerate(evaluated["unverified"], 1):
        lines.extend(_render_record(record, index, unverified=True))
    lines.extend([
        "## Exclusion summary",
        "",
        f"- Ineligible: {summary.get('ineligible', 0)}",
        f"- Expired or closed: {summary.get('expired', 0)}",
        f"- Rejected (fee, loan, sweepstakes, or preference): {summary.get('rejected', 0)}",
        f"- Invalid records: {summary.get('invalid', 0)}",
        f"- Valid matches omitted by your result limit: {summary.get('omitted_by_result_limit', 0)}",
        "",
        "Confirm every requirement and deadline on the linked official page before applying. "
        "This report is informational and does not guarantee eligibility or an award.",
        "",
    ])
    if include_excluded:
        for bucket, title in (("ineligible", "Ineligible"), ("expired", "Expired or closed"), ("rejected", "Rejected"), ("invalid", "Invalid records"), ("omitted_due_to_limit", "Valid matches omitted by result limit")):
            lines.extend([f"## {title}", ""])
            records = evaluated.get(bucket, [])
            if not records:
                lines.extend(["None.", ""])
            for index, record in enumerate(records, 1):
                lines.extend(_render_record(record, index, unverified=True))
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Export


def _ics_escape(value: Any) -> str:
    text = strip_control_characters(str(value)).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return text.replace("\n", "\\n")


def _ics_fold(line: str) -> list[str]:
    """Fold a content line to 75 octets as RFC 5545 requires."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return [line]
    parts: list[str] = []
    current = ""
    limit = 75
    for character in line:
        if len((current + character).encode("utf-8")) > limit:
            parts.append(current)
            current = character
            limit = 74  # continuation lines start with one space
        else:
            current += character
    parts.append(current)
    return [parts[0]] + [" " + part for part in parts[1:]]


def export_ics(evaluated: dict[str, Any], *, reminder_days: int = 7) -> str:
    """Calendar file with an all-day event for every matching deadline."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//hermes-scholarship-research//scholarship deadlines//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for bucket in ("verified", "needs_confirmation"):
        for record in evaluated.get(bucket, []):
            deadline_text = (record.get("deadline") or {}).get("date")
            if not deadline_text:
                continue
            deadline_date = date.fromisoformat(deadline_text)
            name = record.get("name", "Scholarship")
            description = "\n".join(part for part in (
                f"Amount: {(record.get('amount') or {}).get('display', '')}",
                f"Deadline type: {(record.get('deadline') or {}).get('type', '')} {(record.get('deadline') or {}).get('timezone', '')}".rstrip(),
                "Status: verified" if bucket == "verified" else "Status: confirm eligibility first",
                f"Official page: {record.get('official_url', '')}",
                "Re-check the official page before applying.",
            ) if part)
            event = [
                "BEGIN:VEVENT",
                f"UID:{record.get('id', scholarship_id(str(name), str(record.get('sponsor', ''))))}@scholarship-research",
                f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{deadline_date.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{(deadline_date + timedelta(days=1)).strftime('%Y%m%d')}",
                f"SUMMARY:{_ics_escape('Scholarship deadline: ' + str(name))}",
                f"DESCRIPTION:{_ics_escape(description)}",
            ]
            if _link(record.get("official_url")):
                event.append(f"URL:{record['official_url']}")
            if reminder_days > 0:
                event.extend([
                    "BEGIN:VALARM",
                    f"TRIGGER:-P{reminder_days}D",
                    "ACTION:DISPLAY",
                    f"DESCRIPTION:{_ics_escape(str(name) + ' is due in ' + str(reminder_days) + ' days')}",
                    "END:VALARM",
                ])
            event.append("END:VEVENT")
            lines.extend(event)
    lines.append("END:VCALENDAR")
    folded: list[str] = []
    for line in lines:
        folded.extend(_ics_fold(line))
    return "\r\n".join(folded) + "\r\n"


CSV_COLUMNS = (
    "result", "name", "sponsor", "amount", "deadline", "deadline_type", "days_left",
    "status", "eligibility", "official_url", "application_url", "application_tasks",
    "uncertainties", "verified_at",
)


def _csv_cell(value: Any) -> str:
    text = re.sub(r"\s+", " ", strip_control_characters(str(value if value is not None else ""))).strip()
    # Web-sourced text must never become a spreadsheet formula.
    if text and text[0] in "=+-@":
        text = "'" + text
    return text


def export_csv(evaluated: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for bucket in ("verified", "needs_confirmation", "unverified"):
        for record in evaluated.get(bucket, []):
            deadline = record.get("deadline") or {}
            writer.writerow([_csv_cell(value) for value in (
                bucket,
                record.get("name", ""),
                record.get("sponsor", ""),
                (record.get("amount") or {}).get("display", ""),
                deadline.get("date") or "",
                deadline.get("type", ""),
                record.get("days_left", ""),
                record.get("status", ""),
                record.get("eligibility_state", ""),
                record.get("official_url", ""),
                record.get("application_url", ""),
                "; ".join(record.get("application_tasks") or []),
                "; ".join(record.get("uncertainties") or record.get("validation_notes") or []),
                record.get("verified_at", ""),
            )])
    return buffer.getvalue()


def write_export(evaluated: dict[str, Any], fmt: str, output: Path) -> dict[str, Any]:
    if fmt == "ics":
        text = export_ics(evaluated)
    elif fmt == "csv":
        text = export_csv(evaluated)
    else:
        raise ValueError("export format must be ics or csv")
    atomic_write(output, text, newline="")
    return {"ok": True, "format": fmt, "path": str(output)}


# ---------------------------------------------------------------------------
# CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scholarship research validation pipeline.")
    sub = parser.add_subparsers(dest="command", required=True)
    queries = sub.add_parser("queries", help="generate privacy-safe search queries")
    queries.add_argument("profile_path", metavar="profile")
    queries.add_argument("--output")
    queries.add_argument("--max-queries", type=int, default=60)
    add = sub.add_parser("add-candidate", help="validate one candidate record and store it")
    add.add_argument("profile_path", metavar="profile")
    add.add_argument("candidates")
    add.add_argument("record", help="JSON file holding one record, or - for standard input")
    add.add_argument("--today", help="override current UTC date for testing (YYYY-MM-DD)")
    add.add_argument("--max-verification-age", type=int, default=7)
    evaluate = sub.add_parser("evaluate", help="validate, match, and deduplicate candidates")
    evaluate.add_argument("profile_path", metavar="profile")
    evaluate.add_argument("candidates")
    evaluate.add_argument("--output")
    evaluate.add_argument("--today", help="override current UTC date for testing (YYYY-MM-DD)")
    evaluate.add_argument("--max-verification-age", type=int, default=7)
    render = sub.add_parser("render", help="render evaluated JSON as Markdown")
    render.add_argument("evaluated")
    render.add_argument("--output")
    render.add_argument("--include-excluded", action="store_true")
    export = sub.add_parser("export", help="export evaluated results as a calendar or spreadsheet")
    export.add_argument("evaluated")
    export.add_argument("--format", choices=("ics", "csv"), required=True)
    export.add_argument("--output", required=True)
    sub.add_parser("check-registry", help="validate references/trusted-sources.json")
    score = sub.add_parser("score", help="score evaluated results against a hand-checked gold list")
    score.add_argument("evaluated")
    score.add_argument("gold")
    return parser


def _parse_today(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "queries":
            if not 1 <= args.max_queries <= MAX_QUERY_LIMIT:
                raise ValueError(f"--max-queries must be between 1 and {MAX_QUERY_LIMIT}")
            profile = load_profile(Path(args.profile_path).expanduser())
            emit(generate_queries(profile, args.max_queries), args.output)
            return 0
        if args.command == "add-candidate":
            record_text = sys.stdin.read() if args.record == "-" else Path(args.record).expanduser().read_text(encoding="utf-8")
            result = add_candidate(
                args.profile_path, args.candidates, record_text,
                today=_parse_today(args.today), max_age=args.max_verification_age,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result["ok"] else 2
        if args.command == "evaluate":
            if not 0 <= args.max_verification_age <= 365:
                raise ValueError("--max-verification-age must be between 0 and 365")
            profile = load_profile(Path(args.profile_path).expanduser())
            candidates = load_json(Path(args.candidates).expanduser())
            emit(evaluate_candidates(profile, candidates, Clock(_parse_today(args.today)), args.max_verification_age), args.output)
            return 0
        if args.command == "render":
            evaluated = load_json(Path(args.evaluated).expanduser())
            emit(render_report(evaluated, args.include_excluded), args.output, as_json=False)
            return 0
        if args.command == "export":
            evaluated = load_json(Path(args.evaluated).expanduser())
            print(json.dumps(write_export(evaluated, args.format, Path(args.output).expanduser())))
            return 0
        if args.command == "score":
            result = score_against_gold(load_json(Path(args.evaluated).expanduser()), load_json(Path(args.gold).expanduser()))
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.command == "check-registry":
            registry = load_registry()
            print(json.dumps({
                "ok": True,
                "sources": len(registry["sources"]),
                "discovery_domains": len(registry["discovery_domains"]),
            }))
            return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
