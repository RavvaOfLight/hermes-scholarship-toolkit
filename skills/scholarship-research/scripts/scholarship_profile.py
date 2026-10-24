#!/usr/bin/env python3
"""Private, validated profile storage for the scholarship-research skill."""

from __future__ import annotations

import argparse
import copy
import getpass
import json
import math
import os
import re
import secrets
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 5
QUESTIONNAIRE_VERSION = 1
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
DEFAULT_PROFILE_PATH = Path(
    os.environ.get("SCHOLARSHIP_PROFILE", HERMES_HOME / "scholarship-profile.json")
).expanduser()
# One file per student. "default" is the original single-profile path above.
PROFILES_DIR = HERMES_HOME / "scholarship-profiles"
TRASH_DIR_NAME = ".trash"
DEFAULT_STUDENT = "default"

# Files a run workspace may contain. Cleanup deletes only these names.
RUN_FILES = {
    "profile": "profile.json",
    "queries": "queries.json",
    "search_state": "queries.state.json",
    "candidates": "candidates.json",
    "evaluated": "evaluated.json",
    "report": "report.md",
    "calendar": "deadlines.ics",
    "csv": "results.csv",
    "evidence": "evidence.jsonl",
    "audit": "audit.json",
    "run": "run.json",
}
PAGES_DIR = "pages"
_PAGE_FILE_RE = re.compile(r"^[0-9a-f]{24}\.md$")
RUN_DIR_PREFIX = "run-"
RUN_ROOT_PREFIX = "hermes-scholarship-"


def default_profile() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "onboarding": {
            "questionnaire_version": QUESTIONNAIRE_VERSION,
            "question_dispositions": {
                field: "unanswered" for field in QUESTIONNAIRE_FIELDS
            },
        },
        "student": {
            "location": {
                "state": "",
                "county": "",
                "city": "",
                "high_school": "",
                "school_district": "",
            },
            "academic_level": "",
            "graduation_year": None,
            "entry_term": "",
            "citizenship": "",
            "degree_level": "",
            "search_mode": "",
            "academics": {
                "intended_major": "",
                "unweighted_gpa": None,
                "gpa_scale": None,
                "weighted_gpa": None,
                "sat": None,
                "act": None,
            },
            "target_colleges": [],
            "affiliations": {
                "clubs": [],
                "professional": [],
                "employer": "",
                "credit_union": "",
                "military": "",
            },
            "first_generation": None,
            "financial_need": None,
            "sai": None,
            "identity": {
                "ethnicity": None,
                "lgbtq": None,
                "disability": None,
                "religion": None,
                "other": [],
            },
        },
        "preferences": {
            "minimum_award": None,
            "maximum_results": 20,
            "include_identity_searches": False,
            "include_no_essay": False,
        },
    }


YES_NO_UNKNOWN = ("Yes", "No", "Unknown", "Prefer not to answer")
YES_NO_PRIVATE = ("Yes", "No", "Prefer not to answer")

# The single authoritative onboarding questionnaire, in asking order. Each entry
# is (field, prompt, choices). Choices are optional quick picks; Hermes's clarify
# tool adds a free-text "Other" row, so open answers are always possible.
QUESTION_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "group": "core_profile",
        "title": "Core search profile",
        "questions": (
            ("student.location.state", "Which U.S. state do you live in?", None),
            ("student.academic_level", "What is your current academic level?",
             ("High school junior", "High school senior", "Current college student",
              "Graduate student")),
            ("student.graduation_year",
             "What year will you graduate from your current school?", None),
            ("student.entry_term",
             "When do the studies this scholarship would pay for begin? For example: Fall 2027.",
             None),
            ("student.degree_level", "What degree or credential will you pursue next?",
             ("Bachelor's", "Associate", "Certificate or trade credential", "Master's")),
            ("student.academics.intended_major",
             "What is your intended major or field of study?", None),
            ("student.search_mode",
             "Which applicant path describes you? Choose Other for a trade, certificate, "
             "or apprenticeship program.",
             ("High school student applying to college", "Current undergraduate",
              "Transfer student", "Graduate student")),
        ),
    },
    {
        "group": "local_context",
        "title": "Local scholarship context",
        "questions": (
            ("student.location.county", "What county or independent city do you live in?", None),
            ("student.location.city", "What city or town do you live in?", None),
            ("student.location.high_school", "What high school do you attend or did you attend?", None),
            ("student.location.school_district", "What school district is that school in?", None),
        ),
    },
    {
        "group": "academics_and_college",
        "title": "Academics and college plans",
        "questions": (
            ("student.citizenship", "What is your citizenship or residency status?",
             ("U.S. citizen", "U.S. permanent resident", "Other eligible noncitizen",
              "Prefer not to answer")),
            ("student.academics.unweighted_gpa",
             "What is your unweighted GPA? Answer unknown if your school does not report one.",
             None),
            ("student.academics.gpa_scale", "What scale is that unweighted GPA on?",
             ("4.0", "5.0", "100", "Unknown")),
            ("student.academics.weighted_gpa", "What is your weighted GPA, if your school reports one?", None),
            ("student.academics.sat", "What is your official SAT score, if any? Do not estimate.", None),
            ("student.academics.act", "What is your official ACT score, if any? Do not estimate.", None),
            ("student.target_colleges",
             "Which colleges, universities, trade schools, or programs are you considering?", None),
        ),
    },
    {
        "group": "memberships_and_affiliations",
        "title": "Memberships and affiliations",
        "questions": (
            ("student.affiliations.clubs",
             "List your clubs, service groups, teams, robotics groups, scouting, athletics, "
             "and community organizations.", None),
            ("student.affiliations.professional",
             "List any professional, trade, academic, or honor-society memberships.", None),
            ("student.affiliations.employer",
             "Does an employer of yours, a parent or guardian, or a spouse offer scholarships? "
             "If so, name the employer and the relationship.", None),
            ("student.affiliations.credit_union",
             "Do you or your family belong to a credit union? If so, name it.", None),
            ("student.affiliations.military",
             "Describe any personal or family military affiliation, such as a parent who "
             "served in the Navy.", None),
        ),
    },
    {
        "group": "financial_and_family_background",
        "title": "Financial and family background (optional)",
        "sensitive": True,
        "questions": (
            ("student.first_generation",
             "Optional: would you be a first-generation college student?", YES_NO_UNKNOWN),
            ("student.financial_need",
             "Optional: do you have financial need for scholarship purposes?", YES_NO_UNKNOWN),
            ("student.sai",
             "Optional: what is your official FAFSA Student Aid Index (SAI)? Answer unknown "
             "if your FAFSA is not processed; do not estimate.", None),
        ),
    },
    {
        "group": "identity",
        "title": "Optional identity categories",
        "sensitive": True,
        "questions": (
            ("student.identity.ethnicity",
             "Optional: any ethnicity, race, heritage, or tribal affiliation to consider for "
             "matching?", None),
            ("student.identity.lgbtq", "Optional: do you identify as LGBTQ+?", YES_NO_PRIVATE),
            ("student.identity.disability", "Optional: do you have a disability to consider for matching?",
             YES_NO_PRIVATE),
            ("student.identity.religion", "Optional: any religion or faith affiliation to consider?", None),
            ("student.identity.other",
             "Optional: any other background to consider, such as gender, heritage language, "
             "or foster-care experience?", None),
            ("preferences.include_identity_searches",
             "May the identity details you shared be used in targeted web searches? Without "
             "consent they are used only to check eligibility.", ("Yes", "No")),
        ),
    },
    {
        "group": "search_preferences",
        "title": "Search preferences",
        "questions": (
            ("preferences.minimum_award", "What is the smallest award worth including?",
             ("No minimum", "$500", "$1,000", "$2,500")),
            ("preferences.maximum_results", "How many final results would you like (1-100, or 'as many as possible')?",
             ("10", "20", "30", "50")),
            ("preferences.include_no_essay",
             "Include no-essay scholarships when they still pass the legitimacy checks?",
             ("Yes", "No")),
        ),
    },
)

CORE_FIELDS: tuple[str, ...] = (
    "student.location.state",
    "student.academic_level",
    "student.graduation_year",
    "student.entry_term",
    "student.degree_level",
    "student.academics.intended_major",
)

# Questions whose answer or skip is recorded in onboarding.question_dispositions.
# This set is part of the stored profile format: changing it invalidates saved
# profiles, so core fields are tracked by missing_required_fields() instead.
QUESTIONNAIRE_FIELDS: tuple[str, ...] = (
    "student.location.county",
    "student.location.city",
    "student.location.high_school",
    "student.location.school_district",
    "student.citizenship",
    "student.search_mode",
    "student.academics.unweighted_gpa",
    "student.academics.gpa_scale",
    "student.academics.weighted_gpa",
    "student.academics.sat",
    "student.academics.act",
    "student.target_colleges",
    "student.affiliations.clubs",
    "student.affiliations.professional",
    "student.affiliations.employer",
    "student.affiliations.credit_union",
    "student.affiliations.military",
    "student.first_generation",
    "student.financial_need",
    "student.sai",
    "student.identity.ethnicity",
    "student.identity.lgbtq",
    "student.identity.disability",
    "student.identity.religion",
    "student.identity.other",
    "preferences.include_identity_searches",
    "preferences.minimum_award",
    "preferences.maximum_results",
    "preferences.include_no_essay",
)
ALL_QUESTION_FIELDS: tuple[str, ...] = tuple(
    field for group in QUESTION_GROUPS for field, _prompt, _choices in group["questions"]
)
QUESTION_DISPOSITIONS = {"unanswered", "answered", "skipped"}
MAX_CLARIFY_QUESTIONS = 5
MAX_CLARIFY_CHOICES = 4


# Paths from the old version are accepted so existing setup commands still work.
ALIASES = {
    "student.state": "student.location.state",
    "student.county": "student.location.county",
    "student.city": "student.location.city",
    "student.high_school": "student.location.high_school",
    "student.school_district": "student.location.school_district",
    "student.current_academic_level": "student.academic_level",
    "student.college_entry": "student.entry_term",
    "student.intended_degree": "student.degree_level",
    "student.intended_major": "student.academics.intended_major",
    "student.unweighted_gpa": "student.academics.unweighted_gpa",
    "student.weighted_gpa": "student.academics.weighted_gpa",
    "student.sat": "student.academics.sat",
    "student.act": "student.academics.act",
    "student.clubs": "student.affiliations.clubs",
    "student.professional_affiliations": "student.affiliations.professional",
    "student.employer": "student.affiliations.employer",
    "student.credit_union": "student.affiliations.credit_union",
    "student.identity.military_affiliation": "student.affiliations.military",
    "student.identity.religious_affiliation": "student.identity.religion",
    "student.minimum_acceptable_award": "preferences.minimum_award",
    "student.economic_disadvantage": "student.financial_need",
}


# kind, nullable, minimum, maximum
FIELD_RULES: dict[str, tuple[str, bool, float | None, float | None]] = {
    "student.location.state": ("str", False, None, 100),
    "student.location.county": ("str", False, None, 150),
    "student.location.city": ("str", False, None, 150),
    "student.location.high_school": ("str", False, None, 250),
    "student.location.school_district": ("str", False, None, 250),
    "student.academic_level": ("str", False, None, 80),
    "student.graduation_year": ("int", True, 2000, 2100),
    "student.entry_term": ("str", False, None, 80),
    "student.citizenship": ("str", False, None, 120),
    "student.degree_level": ("str", False, None, 80),
    "student.search_mode": ("str", False, None, 40),
    "student.academics.intended_major": ("str", False, None, 200),
    # Some schools grade on a 100-point scale, so GPA bounds allow it.
    "student.academics.unweighted_gpa": ("float", True, 0, 100),
    "student.academics.gpa_scale": ("float", True, 1, 100),
    "student.academics.weighted_gpa": ("float", True, 0, 110),
    "student.academics.sat": ("int", True, 400, 1600),
    "student.academics.act": ("int", True, 1, 36),
    "student.target_colleges": ("list_str", False, 0, 30),
    "student.affiliations.clubs": ("list_str", False, 0, 50),
    "student.affiliations.professional": ("list_str", False, 0, 30),
    "student.affiliations.employer": ("str", False, None, 200),
    "student.affiliations.credit_union": ("str", False, None, 200),
    "student.affiliations.military": ("str", False, None, 200),
    "student.first_generation": ("bool", True, None, None),
    "student.financial_need": ("bool", True, None, None),
    "student.sai": ("int", True, -1500, 999999),
    "student.identity.ethnicity": ("str", True, None, 200),
    "student.identity.lgbtq": ("bool", True, None, None),
    "student.identity.disability": ("bool", True, None, None),
    "student.identity.religion": ("str", True, None, 200),
    "student.identity.other": ("list_str", False, 0, 20),
    "preferences.minimum_award": ("int", True, 0, 10**9),
    "preferences.maximum_results": ("int", False, 1, 100),
    "preferences.include_identity_searches": ("bool", False, None, None),
    "preferences.include_no_essay": ("bool", False, None, None),
}


REQUIRED_SEARCH_FIELDS = (
    "student.location.state",
    "student.academic_level",
    "student.graduation_year",
    "student.entry_term",
    "student.degree_level",
    "student.search_mode",
    "student.academics.intended_major",
)

SEARCH_MODES = {
    "high_school",
    "undergraduate",
    "transfer",
    "graduate",
    "trade_certificate",
}

# Responses that mean "no information" for any question.
SKIP_RESPONSES = frozenset({
    "", "-", "--", "unknown", "not sure", "unsure", "don't know", "dont know", "idk",
    "none", "n/a", "na", "not applicable", "skip", "skipped", "pass",
    "prefer not to answer", "prefer not to say", "rather not say", "decline",
    "decline to answer", "no answer", "no comment", "none of the above",
})
# Extra responses that mean "nothing to record" for text and list questions.
# For yes/no questions these are real answers and are not skipped.
NEGATIVE_RESPONSES = frozenset({
    "no", "nope", "n", "no minimum", "not a member", "no affiliation", "nothing",
    "none yet", "not yet", "n/a yet",
})
YES_RESPONSES = frozenset({"yes", "y", "yeah", "yep", "true", "1", "i consent", "consent"})
NO_RESPONSES = frozenset({"no", "n", "nope", "false", "0", "i do not consent"})


class OnboardingError(ValueError):
    """Raised with every problem found in a questionnaire submission."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {token}")


def _json_loads(text: str) -> Any:
    return json.loads(text, parse_constant=_reject_json_constant)


def _blank(value: Any) -> bool:
    return value is None or value == [] or (isinstance(value, str) and not value.strip())


def has_control_characters(text: str) -> bool:
    """True when text contains control characters other than tab and newlines."""
    return any(unicodedata.category(ch) == "Cc" and ch not in "\t\n\r" for ch in text)


def strip_control_characters(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cc" or ch in "\t\n\r")


def resolve_profile_path(explicit: str | None = None) -> Path:
    return Path(explicit).expanduser() if explicit else DEFAULT_PROFILE_PATH


def normalize_path(path: str) -> str:
    clean = path.strip().strip(".")
    if not clean.startswith(("student.", "preferences.")):
        candidate = f"student.{clean}"
        clean = candidate if candidate in FIELD_RULES or candidate in ALIASES else clean
    return ALIASES.get(clean, clean)


def get_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in normalize_path(path).split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def set_path(data: dict[str, Any], path: str, value: Any) -> None:
    normalized = normalize_path(path)
    if normalized not in FIELD_RULES:
        raise ValueError(f"unsupported profile field: {path}")
    current: Any = data
    parts = normalized.split(".")
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"invalid profile path: {normalized}")
        current = current[part]
    current[parts[-1]] = value


def _validate_leaf(path: str, value: Any) -> list[str]:
    kind, nullable, lower, upper = FIELD_RULES[path]
    if value is None:
        return [] if nullable else [f"{path} cannot be null"]
    errors: list[str] = []
    if kind == "str":
        if not isinstance(value, str):
            return [f"{path} must be a string"]
        if has_control_characters(value):
            errors.append(f"{path} contains control characters")
        if value and not value.strip():
            errors.append(f"{path} cannot contain only whitespace")
        if upper is not None and len(value) > upper:
            errors.append(f"{path} must contain at most {int(upper)} characters")
    elif kind == "bool":
        if not isinstance(value, bool):
            errors.append(f"{path} must be true, false, or null")
    elif kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"{path} must be an integer"]
        if lower is not None and value < lower:
            errors.append(f"{path} must be at least {int(lower)}")
        if upper is not None and value > upper:
            errors.append(f"{path} must be at most {int(upper)}")
    elif kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"{path} must be numeric"]
        if not math.isfinite(value):
            return [f"{path} must be a finite number"]
        if lower is not None and value < lower:
            errors.append(f"{path} must be at least {lower:g}")
        if upper is not None and value > upper:
            errors.append(f"{path} must be at most {upper:g}")
    elif kind == "list_str":
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            return [f"{path} must be a JSON array of strings"]
        if upper is not None and len(value) > upper:
            errors.append(f"{path} may contain at most {int(upper)} entries")
        if any(len(item) > 250 for item in value):
            errors.append(f"{path} entries must contain at most 250 characters")
        if any(not item.strip() for item in value):
            errors.append(f"{path} entries cannot be blank")
        if any(has_control_characters(item) for item in value):
            errors.append(f"{path} entries contain control characters")
    return errors


def validate_profile(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return ["profile must be a JSON object"], warnings
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    for path in FIELD_RULES:
        errors.extend(_validate_leaf(path, get_path(data, path)))

    onboarding = data.get("onboarding")
    if not isinstance(onboarding, dict):
        errors.append("onboarding must be an object")
    elif set(onboarding) != {"questionnaire_version", "question_dispositions"}:
        errors.append(
            "onboarding must contain exactly questionnaire_version and "
            "question_dispositions"
        )
    else:
        if onboarding.get("questionnaire_version") != QUESTIONNAIRE_VERSION:
            errors.append(f"onboarding.questionnaire_version must be {QUESTIONNAIRE_VERSION}")
        dispositions = onboarding.get("question_dispositions")
        if not isinstance(dispositions, dict):
            errors.append("onboarding.question_dispositions must be an object")
        else:
            missing_dispositions = sorted(set(QUESTIONNAIRE_FIELDS) - set(dispositions))
            extra_dispositions = sorted(set(dispositions) - set(QUESTIONNAIRE_FIELDS))
            if missing_dispositions:
                errors.append(
                    "missing questionnaire dispositions: "
                    + ", ".join(missing_dispositions)
                )
            if extra_dispositions:
                errors.append(
                    "unsupported questionnaire dispositions: "
                    + ", ".join(extra_dispositions)
                )
            invalid_dispositions = sorted(
                path
                for path, disposition in dispositions.items()
                if disposition not in QUESTION_DISPOSITIONS
            )
            if invalid_dispositions:
                errors.append(
                    "invalid questionnaire dispositions: "
                    + ", ".join(invalid_dispositions)
                )
            defaults = default_profile()
            inconsistent_dispositions = sorted(
                path
                for path, disposition in dispositions.items()
                if path in FIELD_RULES and (
                    (disposition == "answered" and _blank(get_path(data, path)))
                    or (
                        disposition == "skipped"
                        and get_path(data, path) != get_path(defaults, path)
                    )
                )
            )
            if inconsistent_dispositions:
                errors.append(
                    "questionnaire dispositions do not match stored values: "
                    + ", ".join(inconsistent_dispositions)
                )

    search_mode = get_path(data, "student.search_mode")
    if search_mode and search_mode not in SEARCH_MODES:
        errors.append(
            "student.search_mode must be one of: "
            + ", ".join(sorted(SEARCH_MODES))
        )
    unweighted_gpa = get_path(data, "student.academics.unweighted_gpa")
    gpa_scale = get_path(data, "student.academics.gpa_scale")
    if (
        isinstance(unweighted_gpa, (int, float))
        and not isinstance(unweighted_gpa, bool)
        and isinstance(gpa_scale, (int, float))
        and not isinstance(gpa_scale, bool)
        and math.isfinite(unweighted_gpa)
        and math.isfinite(gpa_scale)
        and unweighted_gpa > gpa_scale
    ):
        errors.append("student.academics.unweighted_gpa cannot exceed its gpa_scale")

    allowed_top = {"schema_version", "onboarding", "student", "preferences"}
    extra_top = sorted(set(data) - allowed_top)
    if extra_top:
        errors.append(f"unsupported top-level fields: {', '.join(extra_top)}")

    def find_unknown(actual: Any, template: Any, prefix: str) -> list[str]:
        if not isinstance(actual, dict) or not isinstance(template, dict):
            return []
        unknown: list[str] = []
        for key in actual:
            path = f"{prefix}.{key}" if prefix else key
            if key not in template:
                unknown.append(path)
            elif isinstance(actual[key], dict):
                unknown.extend(find_unknown(actual[key], template[key], path))
        return unknown

    unknown_paths = find_unknown(data, default_profile(), "")
    if unknown_paths:
        errors.append("unsupported profile fields: " + ", ".join(sorted(unknown_paths)))

    if not get_path(data, "student.location.county") and not get_path(data, "student.location.city"):
        warnings.append("county or city is recommended for local scholarships")
    if not get_path(data, "student.location.high_school"):
        warnings.append("high school is recommended for school-specific scholarships")
    if not get_path(data, "student.target_colleges"):
        warnings.append("target colleges are recommended for institutional scholarships")
    if get_path(data, "preferences.include_identity_searches"):
        identity_values = [
            get_path(data, "student.identity.ethnicity"),
            get_path(data, "student.identity.lgbtq"),
            get_path(data, "student.identity.disability"),
            get_path(data, "student.identity.religion"),
            get_path(data, "student.identity.other"),
        ]
        if not any(value not in (None, "", [], False) for value in identity_values):
            warnings.append("identity searches are enabled but no identity category is selected")
    return errors, warnings


def missing_required_fields(data: dict[str, Any]) -> list[str]:
    return [path for path in REQUIRED_SEARCH_FIELDS if _blank(get_path(data, path))]


def optional_questionnaire_completed(data: dict[str, Any]) -> bool:
    onboarding = data.get("onboarding") if isinstance(data, dict) else None
    if not isinstance(onboarding, dict):
        return False
    dispositions = onboarding.get("question_dispositions")
    return (
        isinstance(dispositions, dict)
        and set(dispositions) == set(QUESTIONNAIRE_FIELDS)
        and all(value in {"answered", "skipped"} for value in dispositions.values())
    )


def onboarding_complete(data: dict[str, Any]) -> bool:
    return optional_questionnaire_completed(data) and not missing_required_fields(data)


# ---------------------------------------------------------------------------
# Questionnaire output


def _question_type(field: str) -> str:
    kind = FIELD_RULES[field][0]
    if field == "student.search_mode":
        return "choice"
    return {"str": "text", "bool": "yes_no", "int": "number", "float": "number", "list_str": "list"}[kind]


def questionnaire(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return every onboarding question as clarify-ready batches.

    When a profile is supplied, each batch also lists values already stored so
    the agent can confirm them instead of asking from scratch.
    """
    batches: list[dict[str, Any]] = []
    for group in QUESTION_GROUPS:
        questions = list(group["questions"])
        for start in range(0, len(questions), MAX_CLARIFY_QUESTIONS):
            chunk = questions[start:start + MAX_CLARIFY_QUESTIONS]
            clarify_questions: list[dict[str, Any]] = []
            for _field, prompt, choices in chunk:
                item: dict[str, Any] = {"question": prompt}
                if choices:
                    item["choices"] = list(choices[:MAX_CLARIFY_CHOICES])
                clarify_questions.append(item)
            batch: dict[str, Any] = {
                "batch": len(batches) + 1,
                "group": group["group"],
                "title": group["title"],
                "sensitive": bool(group.get("sensitive", False)),
                "fields": [field for field, _prompt, _choices in chunk],
                "types": [_question_type(field) for field, _prompt, _choices in chunk],
                "required_fields": [
                    field for field, _prompt, _choices in chunk if field in REQUIRED_SEARCH_FIELDS
                ],
                "clarify": {"questions": clarify_questions},
            }
            if data is not None:
                known = {
                    field: get_path(data, field)
                    for field, _prompt, _choices in chunk
                    if not _blank(get_path(data, field))
                }
                if known:
                    batch["current_values"] = known
                dispositions = data.get("onboarding", {}).get("question_dispositions", {})
                skipped = [
                    field for field, _prompt, _choices in chunk
                    if dispositions.get(field) == "skipped"
                ]
                if skipped:
                    batch["previously_skipped"] = skipped
            batches.append(batch)
    return {
        "questionnaire_version": QUESTIONNAIRE_VERSION,
        "instructions": (
            "Ask every batch in order, one clarify call per batch (or one chat message per "
            "batch when clarify is unavailable). Required fields need a real answer. Every "
            "other question may be answered 'unknown', 'none', or 'prefer not to answer'; "
            "record that reply verbatim and never infer or estimate a skipped answer. "
            "Identity-search consent is separate from sharing identity details. Then submit "
            "one responses object with an entry for every field, keyed by field path, using "
            "the user's words: complete_onboarding(profile, {\"responses\": {...}})."
        ),
        "skip_responses": sorted(SKIP_RESPONSES - {"", "-", "--"}),
        "batches": batches,
        "submission_template": {"responses": {field: None for field in ALL_QUESTION_FIELDS}},
    }


# ---------------------------------------------------------------------------
# Answer normalization


def _response_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = normalized.replace("’", "'")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.strip(" .!")


def is_skip_response(field: str, value: Any) -> bool:
    """True when a raw response means the user gave no information."""
    if value is None:
        return True
    kind = FIELD_RULES[field][0]
    if isinstance(value, str):
        text = _response_text(value)
        if text in SKIP_RESPONSES:
            return True
        return kind != "bool" and text in NEGATIVE_RESPONSES
    if isinstance(value, list):
        return all(isinstance(item, str) and is_skip_response(field, item) for item in value)
    return False


def _coerce_search_mode(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("student.search_mode must be text")
    raw = value.strip()
    if raw in SEARCH_MODES:
        return raw
    text = _response_text(raw).replace("_", " ").replace("-", " ")
    if "transfer" in text:
        return "transfer"
    if any(word in text for word in ("trade", "certificate", "vocational", "apprentice", "technical program", "career school")):
        return "trade_certificate"
    if "undergrad" in text or "current college" in text or "college student" in text:
        return "undergraduate"
    if (
        "graduate" in text or "grad school" in text or "master" in text
        or "doctor" in text or "phd" in text or "ph d" in text
    ):
        return "graduate"
    if "high school" in text or text in {"hs", "senior", "junior", "high schooler"}:
        return "high_school"
    raise ValueError(
        "student.search_mode must describe one of: high school student, current "
        "undergraduate, transfer student, graduate student, or trade/certificate student"
    )


_GPA_WITH_SCALE = re.compile(
    r"(?P<gpa>\d+(?:\.\d+)?)\s*(?:/|out of|on a)\s*(?P<scale>\d+(?:\.\d+)?)(?:\s*(?:scale|point scale))?",
    re.IGNORECASE,
)


def coerce_response(field: str, value: Any) -> tuple[Any, dict[str, Any]]:
    """Convert a raw answer into the stored type.

    Returns (value, implied_values). implied_values holds other fields the
    answer settles, such as a GPA scale given as "3.8/4.0".
    """
    kind = FIELD_RULES[field][0]
    if field == "student.search_mode":
        return _coerce_search_mode(value), {}
    if kind == "str":
        if not isinstance(value, str):
            raise ValueError(f"{field} must be text")
        text = value.strip()
        if _response_text(text) in YES_RESPONSES:
            raise ValueError(f"{field} needs the name or details, not just yes")
        return text, {}
    if kind == "bool":
        if isinstance(value, bool):
            return value, {}
        if isinstance(value, str):
            text = _response_text(value)
            if text in YES_RESPONSES or text.startswith("yes"):
                return True, {}
            if text in NO_RESPONSES or text.startswith("no "):
                return False, {}
        raise ValueError(f"{field} must be yes or no")
    if kind == "int":
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a whole number")
        if field == "preferences.maximum_results" and isinstance(value, str) and re.search(
            r"as many as|all of them|\ball\b|no limit|unlimited|\bmax", value, re.IGNORECASE
        ):
            return FIELD_RULES[field][3], {}
        if isinstance(value, int):
            return value, {}
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            return int(value), {}
        if isinstance(value, str):
            text = value.strip().replace(",", "").replace("$", "").replace(" ", "")
            if re.fullmatch(r"[+-]?\d+", text):
                return int(text), {}
        raise ValueError(f"{field} must be a whole number")
    if kind == "float":
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a number")
        if isinstance(value, (int, float)):
            return value, {}
        if isinstance(value, str):
            text = value.strip()
            match = _GPA_WITH_SCALE.fullmatch(text)
            if match and field == "student.academics.unweighted_gpa":
                return float(match.group("gpa")), {
                    "student.academics.gpa_scale": float(match.group("scale"))
                }
            if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
                return float(text), {}
        raise ValueError(f"{field} must be a number")
    if kind == "list_str":
        items: list[Any]
        if isinstance(value, list):
            items = value
        elif isinstance(value, str):
            text = value.strip()
            parsed: Any = None
            if text.startswith("["):
                try:
                    parsed = _json_loads(text)
                except ValueError:
                    parsed = None
            items = parsed if isinstance(parsed, list) else re.split(r"[;\n,]", text)
        else:
            raise ValueError(f"{field} must be a list of names")
        if any(not isinstance(item, str) for item in items):
            raise ValueError(f"{field} must be a list of names")
        cleaned = [item.strip() for item in items if item.strip() and not is_skip_response(field, item)]
        if any(_response_text(item) in YES_RESPONSES for item in cleaned):
            raise ValueError(f"{field} needs names, not just yes")
        return cleaned, {}
    raise ValueError(f"unsupported field type for {field}")


# ---------------------------------------------------------------------------
# Profile migration and merging


def _legacy_value(student: dict[str, Any], name: str) -> Any:
    value = student.get(name)
    return value if value not in (None, "", []) else None


def migrate_legacy(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert the original version-2 profile into the research-only schema."""
    if raw.get("schema_version") == SCHEMA_VERSION:
        return copy.deepcopy(raw)
    if raw.get("schema_version") in {3, 4}:
        upgraded = default_profile()
        legacy_profile = copy.deepcopy(raw)
        legacy_profile.pop("schema_version", None)
        legacy_profile.pop("onboarding", None)
        _merge_known(upgraded, legacy_profile)
        upgraded["schema_version"] = SCHEMA_VERSION
        # Earlier schemas silently defaulted these fields. They cannot prove
        # whether the user supplied them, so collect them again after upgrade.
        if raw.get("schema_version") == 4:
            upgraded["student"]["academic_level"] = ""
            upgraded["student"]["degree_level"] = ""
            upgraded["student"]["search_mode"] = ""
        if upgraded["student"]["academics"]["unweighted_gpa"] is None:
            upgraded["student"]["academics"]["gpa_scale"] = None
        return upgraded
    if raw.get("version") not in (1, 2, None) and raw.get("schema_version") not in (1, 2, None):
        raise ValueError("unsupported legacy profile version")
    old = raw.get("student", {})
    if not isinstance(old, dict):
        raise ValueError("legacy student field must be an object")
    new = default_profile()
    mappings = {
        "state": "student.location.state",
        "county": "student.location.county",
        "city": "student.location.city",
        "high_school": "student.location.high_school",
        "school_district": "student.location.school_district",
        "current_academic_level": "student.academic_level",
        "graduation_year": "student.graduation_year",
        "college_entry": "student.entry_term",
        "citizenship": "student.citizenship",
        "intended_degree": "student.degree_level",
        "search_mode": "student.search_mode",
        "intended_major": "student.academics.intended_major",
        "unweighted_gpa": "student.academics.unweighted_gpa",
        "weighted_gpa": "student.academics.weighted_gpa",
        "sat": "student.academics.sat",
        "act": "student.academics.act",
        "target_colleges": "student.target_colleges",
        "clubs": "student.affiliations.clubs",
        "professional_affiliations": "student.affiliations.professional",
        "employer": "student.affiliations.employer",
        "credit_union": "student.affiliations.credit_union",
        "first_generation": "student.first_generation",
        "economic_disadvantage": "student.financial_need",
        "sai": "student.sai",
        "minimum_acceptable_award": "preferences.minimum_award",
    }
    for old_name, new_path in mappings.items():
        value = _legacy_value(old, old_name)
        if value is not None:
            set_path(new, new_path, value)

    identity = old.get("identity", {}) if isinstance(old.get("identity", {}), dict) else {}
    identity_map = {
        "ethnicity": "student.identity.ethnicity",
        "lgbtq": "student.identity.lgbtq",
        "disability": "student.identity.disability",
        "religious_affiliation": "student.identity.religion",
        "military_affiliation": "student.affiliations.military",
    }
    for old_name, new_path in identity_map.items():
        value = identity.get(old_name)
        # The legacy schema defaulted these consent-sensitive values to false,
        # so false cannot be distinguished from "never answered." Preserve
        # only affirmative legacy selections and restore the rest to unknown.
        if old_name in {"lgbtq", "disability"} and value is not True:
            continue
        if value not in (None, ""):
            set_path(new, new_path, value)
    other = identity.get("other_identity")
    if other:
        set_path(new, "student.identity.other", [str(other)])
    return new


def _merge_known(target: dict[str, Any], update: dict[str, Any], prefix: str = "") -> None:
    for key, value in update.items():
        if key == "schema_version":
            continue
        path = f"{prefix}.{key}" if prefix else key
        if key not in target:
            raise ValueError(f"unsupported profile field: {path}")
        if isinstance(target[key], dict):
            if not isinstance(value, dict):
                raise ValueError(f"{path} must be an object")
            _merge_known(target[key], value, path)
        else:
            target[key] = value


def _present_supported_paths(update: dict[str, Any], prefix: str = "") -> set[str]:
    """Return supported leaf paths explicitly present in a nested update."""
    present: set[str] = set()
    for key, value in update.items():
        if key == "schema_version":
            continue
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            present.update(_present_supported_paths(value, path))
        elif normalize_path(path) in FIELD_RULES:
            present.add(normalize_path(path))
    return present


def _record_disposition(data: dict[str, Any], field: str) -> None:
    if field in QUESTIONNAIRE_FIELDS:
        data["onboarding"]["question_dispositions"][field] = (
            "skipped" if _blank(get_path(data, field)) else "answered"
        )


def _read_json_source(source: str) -> Any:
    if source == "-":
        return _json_loads(sys.stdin.read())
    return _json_loads(Path(source).expanduser().read_text(encoding="utf-8"))


def _normalize_answer_paths(update: dict[str, Any]) -> dict[str, Any]:
    """Normalize a flat answer object and reject nested or duplicate fields."""
    normalized: dict[str, Any] = {}
    for supplied_path, value in update.items():
        if not isinstance(supplied_path, str):
            raise ValueError("questionnaire answer keys must be strings")
        path = normalize_path(supplied_path)
        if path not in FIELD_RULES:
            raise ValueError(
                f"unsupported questionnaire field: {supplied_path}; "
                "use flat dot-path keys from the questionnaire"
            )
        if path in normalized:
            raise ValueError(f"duplicate questionnaire field after aliasing: {path}")
        normalized[path] = value
    return normalized


# ---------------------------------------------------------------------------
# Onboarding completion


def _normalize_submission(submission: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate the explicit answers/question_dispositions submission format."""
    if set(submission) != {"answers", "question_dispositions"}:
        raise ValueError(
            "questionnaire submission must contain exactly answers and "
            "question_dispositions, or exactly responses"
        )
    raw_answers = submission.get("answers")
    raw_dispositions = submission.get("question_dispositions")
    if not isinstance(raw_answers, dict):
        raise ValueError("questionnaire answers must be an object of flat dot-path keys")
    if not isinstance(raw_dispositions, dict):
        raise ValueError("question_dispositions must be an object")
    answers = _normalize_answer_paths(raw_answers)
    dispositions: dict[str, str] = {}
    for supplied_path, disposition in raw_dispositions.items():
        if not isinstance(supplied_path, str):
            raise ValueError("question_dispositions keys must be strings")
        path = normalize_path(supplied_path)
        if path not in QUESTIONNAIRE_FIELDS:
            raise ValueError(f"unsupported questionnaire disposition field: {supplied_path}")
        if path in dispositions:
            raise ValueError(f"duplicate questionnaire disposition after aliasing: {path}")
        if disposition not in {"answered", "skipped"}:
            raise ValueError(
                f"question disposition for {path} must be answered or skipped"
            )
        dispositions[path] = disposition
    missing_dispositions = sorted(set(QUESTIONNAIRE_FIELDS) - set(dispositions))
    if missing_dispositions:
        raise ValueError(
            "cannot complete onboarding; no disposition was supplied for: "
            + ", ".join(missing_dispositions)
        )
    for field, disposition in dispositions.items():
        if disposition == "answered":
            if field not in answers:
                raise ValueError(f"question {field} is marked answered but has no answer")
            if _blank(answers[field]):
                raise ValueError(f"question {field} is marked answered but its answer is blank")
        elif field in answers and not _blank(answers[field]):
            raise ValueError(f"question {field} is marked skipped but has a non-blank answer")
    return answers, dispositions


def finish_questionnaire(
    path: Path,
    update: dict[str, Any],
) -> dict[str, Any]:
    """Apply explicit per-question answers and complete onboarding atomically."""
    if isinstance(update, dict) and "responses" in update:
        data, _answered, _skipped = apply_responses(path, update)
        return data
    answers, dispositions = _normalize_submission(update)
    with profile_lock(path):
        data = load_profile(path)
        for field, disposition in dispositions.items():
            if disposition == "skipped":
                set_path(
                    data,
                    field,
                    copy.deepcopy(get_path(default_profile(), field)),
                )
        for field, value in answers.items():
            set_path(data, field, value)
        data["schema_version"] = SCHEMA_VERSION
        missing = missing_required_fields(data)
        if missing:
            raise ValueError(
                "cannot complete onboarding; missing required search fields: "
                + ", ".join(missing)
            )
        data["onboarding"]["questionnaire_version"] = QUESTIONNAIRE_VERSION
        data["onboarding"]["question_dispositions"] = dispositions
        _save_validated(path, data)
    return data


def apply_responses(
    path: Path,
    submission: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Apply raw questionnaire responses atomically.

    Every questionnaire field needs a response, so a submission proves each
    question was offered. Skip words become skips; other answers are converted
    to their stored types. Nothing is written unless every response is valid.
    """
    if not isinstance(submission, dict) or set(submission) != {"responses"}:
        raise OnboardingError(["submission must be an object containing only responses"])
    raw = submission["responses"]
    if not isinstance(raw, dict):
        raise OnboardingError(["responses must be an object keyed by field path"])
    try:
        responses = _normalize_answer_paths(raw)
    except ValueError as exc:
        raise OnboardingError([str(exc)]) from exc

    errors: list[str] = []
    missing = [field for field in QUESTIONNAIRE_FIELDS if field not in responses]
    if missing:
        errors.append(
            "no response recorded for: " + ", ".join(missing)
            + " (ask these questions; record skips as 'unknown' or 'prefer not to answer')"
        )
    with profile_lock(path):
        data = load_profile(path)
        for field in CORE_FIELDS:
            if field not in responses and _blank(get_path(data, field)):
                errors.append(f"{field} is required")
        updates: dict[str, Any] = {}
        implied: dict[str, Any] = {}
        skipped: list[str] = []
        for field, value in responses.items():
            if is_skip_response(field, value):
                if field in REQUIRED_SEARCH_FIELDS:
                    errors.append(f"{field} is required and cannot be skipped")
                else:
                    skipped.append(field)
                continue
            try:
                coerced, extra = coerce_response(field, value)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if _blank(coerced):
                skipped.append(field)
                continue
            updates[field] = coerced
            implied.update(extra)
        for field, value in implied.items():
            if field in updates and updates[field] != value:
                errors.append(f"{field} conflicts with the scale given in the GPA answer")
            elif field not in updates:
                updates[field] = value
                if field in skipped:
                    skipped.remove(field)
        if errors:
            raise OnboardingError(errors)

        defaults = default_profile()
        for field in skipped:
            set_path(data, field, copy.deepcopy(get_path(defaults, field)))
        for field, value in updates.items():
            set_path(data, field, value)
        data["schema_version"] = SCHEMA_VERSION
        data["onboarding"] = {
            "questionnaire_version": QUESTIONNAIRE_VERSION,
            "question_dispositions": {
                field: "answered" if field in updates else "skipped"
                for field in QUESTIONNAIRE_FIELDS
            },
        }
        missing_required = missing_required_fields(data)
        if missing_required:
            raise OnboardingError([
                "missing required search fields: " + ", ".join(missing_required)
            ])
        validation_errors, _warnings = validate_profile(data)
        if validation_errors:
            raise OnboardingError(validation_errors)
        _atomic_private_write(path, data)
    answered = [field for field in ALL_QUESTION_FIELDS if field in updates]
    return data, answered, sorted(skipped)


def _completion_result(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    missing = missing_required_fields(data)
    ready = optional_questionnaire_completed(data) and not missing
    return {
        "ok": ready,
        "status": "ready_for_search" if ready else "onboarding_incomplete",
        "profile": str(path),
        "questionnaire_complete": optional_questionnaire_completed(data),
        "missing_required_fields": missing,
        "next_action": "generate_queries" if ready else "continue_onboarding",
    }


def complete_onboarding(path: str | Path, submission: dict[str, Any] | str) -> dict[str, Any]:
    """Apply a questionnaire submission and report readiness without raising."""
    profile_path = Path(path).expanduser()
    try:
        if isinstance(submission, str):
            submission = _json_loads(submission)
        if not isinstance(submission, dict):
            raise OnboardingError(["submission must be a JSON object"])
        if "responses" in submission:
            data, answered, skipped = apply_responses(profile_path, submission)
            result = _completion_result(profile_path, data)
            result["answered_fields"] = answered
            result["skipped_fields"] = skipped
            return result
        data = finish_questionnaire(profile_path, submission)
        return _completion_result(profile_path, data)
    except OnboardingError as exc:
        errors = exc.errors
    except ValueError as exc:
        errors = [str(exc)]
    return {
        "ok": False,
        "status": "onboarding_incomplete",
        "profile": str(profile_path),
        "errors": errors,
        "next_action": "fix_and_resubmit",
    }


# ---------------------------------------------------------------------------
# Storage


def load_profile(path: Path) -> dict[str, Any]:
    try:
        raw = _json_loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"profile does not exist: {path}; run 'profile.py init' first") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read profile {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"cannot read profile {path}: profile must be a JSON object")
    data = migrate_legacy(raw)
    errors, _ = validate_profile(data)
    if errors:
        raise ValueError("invalid profile:\n- " + "\n- ".join(errors))
    return data


def atomic_write_text(path: Path, text: str, *, newline: str | None = None) -> None:
    """Write text privately and atomically (owner-only permissions where supported)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_private_write(path: Path, data: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Serialize read-modify-write operations on Linux, macOS, and Windows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+b") as handle:
        os.chmod(lock_path, 0o600)
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


profile_lock = file_lock


def _save_validated(path: Path, data: dict[str, Any]) -> None:
    errors, _ = validate_profile(data)
    if errors:
        raise ValueError("invalid profile:\n- " + "\n- ".join(errors))
    _atomic_private_write(path, data)


def init_profile(path: Path, *, force: bool = False) -> bool:
    """Create a blank private profile. Returns False when one already exists."""
    with profile_lock(path):
        if path.exists() and not force:
            return False
        _save_validated(path, default_profile())
    return True


# ---------------------------------------------------------------------------
# Named profiles (one per student) and the trash


def student_slug(name: str | None) -> str:
    """File-safe student key: lower-case letters, digits, and hyphens."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").casefold()).strip("-")[:40]
    if not slug:
        raise ValueError("a student name is required (letters or digits)")
    return slug


def student_profile_path(student: str | None) -> Path:
    slug = student_slug(student)
    return DEFAULT_PROFILE_PATH if slug == DEFAULT_STUDENT else PROFILES_DIR / f"{slug}.json"


def _trash_dir() -> Path:
    path = PROFILES_DIR / TRASH_DIR_NAME
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _identity_line(data: dict[str, Any]) -> str:
    """Enough to tell students apart in chat: school, class year, major."""
    parts = [get_path(data, "student.location.high_school"), get_path(data, "student.graduation_year"),
             get_path(data, "student.academics.intended_major")]
    return " · ".join(str(part) for part in parts if part not in (None, "", []))


def _profile_entry(slug: str, path: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "student": slug,
        "path": str(path),
        "modified": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        data = load_profile(path)
        entry.update({"identity": _identity_line(data), "onboarding_complete": onboarding_complete(data)})
    except ValueError as exc:
        entry["error"] = str(exc)[:200]
    return entry


def list_profiles() -> list[dict[str, Any]]:
    """Every saved student profile (the default one first)."""
    entries = []
    if DEFAULT_PROFILE_PATH.is_file():
        entries.append(_profile_entry(DEFAULT_STUDENT, DEFAULT_PROFILE_PATH))
    if PROFILES_DIR.is_dir():
        for path in sorted(PROFILES_DIR.glob("*.json")):
            if not path.name.startswith(".") and path.stem != DEFAULT_STUDENT:
                entries.append(_profile_entry(path.stem, path))
    return entries


def runs_for_student(slug: str) -> list[Path]:
    root = run_root()
    owned = []
    for run_dir in sorted(root.glob(f"{RUN_DIR_PREFIX}*")):
        meta = _read_run_meta(run_dir)
        if meta and meta.get("student") == slug and _is_run_dir(run_dir):
            owned.append(run_dir)
    return owned


def delete_profile(student: str, *, confirm: bool = False) -> dict[str, Any]:
    """Two-step delete: first returns the profile summary to show the user; with confirm=True
    moves the profile to the trash (restorable) and removes that student's run workspaces."""
    slug = student_slug(student)
    path = student_profile_path(slug)
    if not path.is_file():
        return {"ok": False, "error": f"no saved profile for student {slug!r}",
                "saved_students": [entry["student"] for entry in list_profiles()]}
    runs = runs_for_student(slug)
    if not confirm:
        try:
            summary = profile_summary(load_profile(path))
        except ValueError as exc:
            summary = f"(profile could not be read: {exc})"
        return {
            "ok": False,
            "status": "confirm_required",
            "student": slug,
            "summary": summary,
            "modified": _profile_entry(slug, path)["modified"],
            "run_workspaces_to_remove": [str(run) for run in runs],
            "next_action": (
                "Show this summary to the user and ask whether it is the student they mean. "
                "Only after a clear yes call delete_profile(student, confirm=True)."
            ),
        }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    trash_path = _trash_dir() / f"{slug}-{stamp}.json"
    with profile_lock(path):
        os.replace(path, trash_path)
        os.chmod(trash_path, 0o600)
    lock = path.with_name(path.name + ".lock")
    if lock.exists():
        lock.unlink()
    cleaned = [cleanup_run(run) for run in runs]
    return {
        "ok": True,
        "status": "moved_to_trash",
        "student": slug,
        "trash_file": trash_path.name,
        "removed_run_workspaces": [item["workdir"] for item in cleaned if item.get("ok")],
        "note": ("The profile is kept in the private trash and can be restored with "
                 "restore_profile(trash_file); empty_trash(trash_file) deletes it for good. "
                 "Answers typed in chat stay in Hermes session history."),
    }


def list_trash() -> list[dict[str, Any]]:
    trash = PROFILES_DIR / TRASH_DIR_NAME
    if not trash.is_dir():
        return []
    return [{"trash_file": path.name,
             "deleted": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")}
            for path in sorted(trash.glob("*.json"))]


def _trash_file(name: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]+-\d{8}T\d{6}Z\.json", str(name or "")):
        raise ValueError("trash_file must be a name returned by delete_profile or list_trash")
    path = PROFILES_DIR / TRASH_DIR_NAME / name
    if not path.is_file():
        raise ValueError(f"not in the trash: {name}")
    return path


def restore_profile(trash_file: str, student: str | None = None) -> dict[str, Any]:
    source = _trash_file(trash_file)
    slug = student_slug(student or trash_file.rsplit("-", 1)[0])
    target = student_profile_path(slug)
    if target.exists():
        return {"ok": False, "error": f"a profile for {slug!r} already exists; restore under another student name"}
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.replace(source, target)
    os.chmod(target, 0o600)
    load_profile(target)
    return {"ok": True, "student": slug, "profile": str(target)}


def empty_trash(trash_file: str) -> dict[str, Any]:
    path = _trash_file(trash_file)
    path.unlink()
    return {"ok": True, "deleted": trash_file}


# ---------------------------------------------------------------------------
# Run workspaces


def _safe_token(value: str | None) -> str:
    if not value or "${" in value:
        return ""
    return re.sub(r"[^A-Za-z0-9_-]", "", value)[:64]


def _user_token() -> str:
    try:
        name = getpass.getuser()
    except Exception:  # noqa: BLE001 - getuser raises several types on odd hosts
        name = ""
    return _safe_token(name)[:32] or "user"


def _private_dir_ok(path: Path) -> bool:
    """True when path is a real directory owned by this user (POSIX) and not a link."""
    if path.is_symlink() or not path.is_dir():
        return False
    if os.name == "nt" or not hasattr(os, "getuid"):
        return True
    stat = path.stat()
    if stat.st_uid != os.getuid():
        return False
    if stat.st_mode & 0o077:
        os.chmod(path, 0o700)
    return True


def run_root() -> Path:
    """Private per-user parent directory for run workspaces."""
    root = Path(tempfile.gettempdir()) / f"{RUN_ROOT_PREFIX}{_user_token()}"
    try:
        root.mkdir(mode=0o700, exist_ok=True)
    except OSError:
        pass
    if _private_dir_ok(root):
        return root
    # Someone else owns the predictable name: use a fresh unpredictable one.
    return Path(tempfile.mkdtemp(prefix=f"{RUN_ROOT_PREFIX}{_user_token()}-"))


def _run_token(session_id: str | None) -> str:
    """Stable token for a session so repeated start_run calls resume one workspace."""
    token = _safe_token(session_id) or _safe_token(os.environ.get("HERMES_SESSION_ID"))
    if token:
        return token
    # An unsubstituted ${HERMES_SESSION_ID} placeholder still means "this session".
    return "default" if session_id and "${" in session_id else secrets.token_hex(8)


def create_run_dir(session_id: str | None = None) -> Path:
    token = _run_token(session_id)
    run_dir = run_root() / f"{RUN_DIR_PREFIX}{token}"
    run_dir.mkdir(mode=0o700, exist_ok=True)
    if not _private_dir_ok(run_dir):
        raise ValueError(f"run directory is not private: {run_dir}")
    return run_dir


def run_paths(run_dir: Path) -> dict[str, str]:
    return {key: str(run_dir / name) for key, name in RUN_FILES.items()}


def _read_run_meta(run_dir: Path) -> dict[str, Any] | None:
    try:
        meta = json.loads((run_dir / RUN_FILES["run"]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def _resume_dir(resume: str) -> Path:
    candidate = Path(resume).expanduser()
    if not candidate.is_absolute():
        name = resume if resume.startswith(RUN_DIR_PREFIX) else f"{RUN_DIR_PREFIX}{resume}"
        candidate = run_root() / name
    if not _is_run_dir(candidate):
        raise ValueError(f"not an existing scholarship run workspace: {resume}; see list_runs()")
    return candidate


def start_run(
    session_id: str | None = None,
    *,
    persistent: bool = False,
    profile: str | None = None,
    student: str | None = None,
    resume: str | None = None,
) -> dict[str, Any]:
    """Create (or resume) a private run workspace and its profile.

    student="name" uses that student's own saved profile (created on first use);
    persistent=True alone uses the default saved profile; resume="run-..." reopens
    an earlier workspace listed by list_runs().
    """
    run_dir = _resume_dir(resume) if resume else create_run_dir(session_id)
    files = run_paths(run_dir)
    meta = _read_run_meta(run_dir) or {}
    if resume and meta.get("profile") and not (student or profile or persistent):
        profile_path = Path(meta["profile"])
        slug = meta.get("student")
    elif student:
        slug = student_slug(student)
        profile_path = student_profile_path(slug)
    elif persistent or profile:
        slug = DEFAULT_STUDENT if not profile else None
        profile_path = resolve_profile_path(profile)
    else:
        slug = None
        profile_path = Path(files["profile"])
    ephemeral = run_dir / RUN_FILES["profile"]
    if meta.get("profile") and Path(meta["profile"]) not in (profile_path, ephemeral):
        raise ValueError(
            f"this workspace belongs to {meta.get('student') or meta['profile']}; start a new session, "
            "pass another session_id, or resume a workspace from list_runs() for this student"
        )
    if profile_path != Path(files["profile"]):
        files["profile"] = str(profile_path)
    created = init_profile(profile_path)
    data = load_profile(profile_path)
    if meta.get("profile") != str(profile_path):
        meta = {"student": slug, "profile": str(profile_path),
                "started_at": meta.get("started_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")}
        atomic_write_text(run_dir / RUN_FILES["run"], json.dumps(meta, indent=2))
    return {
        "ok": True,
        "profile": str(profile_path),
        "student": slug,
        "persistent": profile_path != run_dir / RUN_FILES["profile"],
        "created": created,
        "onboarding_complete": onboarding_complete(data),
        "workdir": str(run_dir),
        "files": files,
    }


def list_runs() -> list[dict[str, Any]]:
    """Earlier run workspaces with their student and progress, newest first."""
    runs = []
    for run_dir in run_root().glob(f"{RUN_DIR_PREFIX}*"):
        if not _is_run_dir(run_dir):
            continue
        meta = _read_run_meta(run_dir) or {}
        entry: dict[str, Any] = {
            "resume": run_dir.name,
            "student": meta.get("student"),
            "started_at": meta.get("started_at"),
            "modified": datetime.fromtimestamp(run_dir.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "files": sorted(name for key, name in RUN_FILES.items() if (run_dir / name).exists()),
        }
        try:
            stored = json.loads((run_dir / RUN_FILES["candidates"]).read_text(encoding="utf-8"))
            entry["stored_records"] = len(stored.get("scholarships", [])) if isinstance(stored, dict) else len(stored)
        except (OSError, ValueError, AttributeError):
            entry["stored_records"] = 0
        try:
            evaluated = json.loads((run_dir / RUN_FILES["evaluated"]).read_text(encoding="utf-8"))
            entry["summary"] = {key: evaluated["summary"].get(key) for key in ("verified", "needs_confirmation", "unverified")}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        runs.append(entry)
    return sorted(runs, key=lambda item: item["modified"], reverse=True)


def _is_run_dir(path: Path) -> bool:
    if not path.name.startswith(RUN_DIR_PREFIX) or path.is_symlink() or not path.is_dir():
        return False
    parent = path.parent
    return (
        parent.name.startswith(RUN_ROOT_PREFIX)
        and parent.parent.resolve() == Path(tempfile.gettempdir()).resolve()
    )


def cleanup_run(workdir: str | Path) -> dict[str, Any]:
    """Delete the known files of one run workspace, then the empty directory.

    Only directories created by create_run_dir() qualify, and only file names
    listed in RUN_FILES (plus their lock and temporary siblings) are removed.
    """
    run_dir = Path(workdir).expanduser()
    if not _is_run_dir(run_dir):
        return {"ok": False, "error": f"not a scholarship run directory: {run_dir}"}
    removed: list[str] = []
    known = set(RUN_FILES.values())
    pages = run_dir / PAGES_DIR
    if pages.is_dir() and not pages.is_symlink():
        for entry in pages.iterdir():
            base = entry.name[1:].split(".md.")[0] + ".md" if entry.name.startswith(".") else entry.name
            if _PAGE_FILE_RE.match(base) and entry.is_file() and not entry.is_symlink():
                entry.unlink()
        if not any(pages.iterdir()):
            pages.rmdir()
            removed.append(PAGES_DIR + "/")
    for entry in run_dir.iterdir():
        name = entry.name
        base = name[:-5] if name.endswith(".lock") else name
        if base.startswith(".") and any(base.startswith(f".{known_name}.") for known_name in known):
            base = next(known_name for known_name in known if base.startswith(f".{known_name}."))
        if base in known and entry.is_file() and not entry.is_symlink():
            entry.unlink()
            removed.append(name)
    leftovers = sorted(entry.name for entry in run_dir.iterdir())
    if not leftovers:
        run_dir.rmdir()
    return {
        "ok": not leftovers,
        "workdir": str(run_dir),
        "removed": sorted(removed),
        "kept_unknown_files": leftovers,
    }


# ---------------------------------------------------------------------------
# CLI


def parse_value(path: str, text: str) -> Any:
    normalized = normalize_path(path)
    if normalized not in FIELD_RULES:
        raise ValueError(f"unsupported profile field: {path}")
    kind, nullable, _, _ = FIELD_RULES[normalized]
    if text.strip().lower() == "null":
        if not nullable:
            raise ValueError(f"{normalized} cannot be null")
        return None
    if kind == "str":
        return text.strip()
    if kind == "bool":
        lowered = text.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
        raise ValueError(f"{normalized} must be true, false, or null")
    if kind == "int":
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(f"{normalized} must be an integer") from exc
    if kind == "float":
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(f"{normalized} must be numeric") from exc
    if kind == "list_str":
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            value = [part.strip() for part in text.split(",") if part.strip()]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"{normalized} must be a JSON array of strings")
        return value
    raise ValueError(f"unsupported field type for {normalized}")


def profile_summary(data: dict[str, Any]) -> str:
    pairs = [
        ("State", get_path(data, "student.location.state")),
        ("County", get_path(data, "student.location.county")),
        ("City", get_path(data, "student.location.city")),
        ("High school", get_path(data, "student.location.high_school")),
        ("Academic level", get_path(data, "student.academic_level")),
        ("Applicant path", get_path(data, "student.search_mode")),
        ("Graduation year", get_path(data, "student.graduation_year")),
        ("College entry", get_path(data, "student.entry_term")),
        ("Degree", get_path(data, "student.degree_level")),
        ("Major", get_path(data, "student.academics.intended_major")),
        ("Target colleges", ", ".join(get_path(data, "student.target_colleges") or [])),
        ("Clubs", ", ".join(get_path(data, "student.affiliations.clubs") or [])),
        ("Minimum award", get_path(data, "preferences.minimum_award")),
        ("Maximum results", get_path(data, "preferences.maximum_results")),
    ]
    lines = ["Scholarship profile", "=" * 40]
    for label, value in pairs:
        if value not in (None, "", []):
            lines.append(f"{label}: {value}")
    if get_path(data, "student.sai") is not None:
        lines.append("SAI: [set]")
    if get_path(data, "preferences.include_identity_searches"):
        lines.append("Identity searches: enabled by user")
    lines.append(
        "Optional questionnaire: complete"
        if optional_questionnaire_completed(data)
        else "Optional questionnaire: incomplete"
    )
    missing = missing_required_fields(data)
    if missing:
        lines.append("Missing required fields: " + ", ".join(missing))
    return "\n".join(lines)


def _write_legacy_tracking_sidecar(source_raw: dict[str, Any], destination: Path) -> Path | None:
    tracking = {
        "applied_scholarships": source_raw.get("applied_scholarships", []),
        "deadlines": source_raw.get("deadlines", []),
        "goals": source_raw.get("goals", {}),
        "last_search": source_raw.get("last_search"),
    }
    if not any(tracking.values()):
        return None
    sidecar = destination.with_name(destination.stem + "-legacy-tracking.json")
    _atomic_private_write(sidecar, tracking)
    return sidecar


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage a private scholarship research profile.")
    parser.add_argument(
        "--profile",
        dest="profile_path",
        help="profile path; defaults to ~/.hermes/scholarship-profile.json",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("schema", help="print a blank profile and supported field paths")
    sub.add_parser("questionnaire", help="print every onboarding question as clarify-ready batches")
    finish = sub.add_parser(
        "finish-questionnaire",
        help="atomically apply questionnaire responses and complete onboarding",
    )
    finish.add_argument(
        "file",
        help="submission JSON file, or - to read JSON from standard input",
    )
    init = sub.add_parser("init", help="create a blank private profile")
    init.add_argument("--force", action="store_true", help="replace an existing profile")
    init.add_argument(
        "--ephemeral",
        action="store_true",
        help="create a private per-session run workspace with its own profile and print its paths",
    )
    init.add_argument("--session-id", help="session identifier used to name the run workspace")
    workdir = sub.add_parser("workdir", help="create a private run workspace and print its paths")
    workdir.add_argument("--session-id", help="session identifier used to name the run workspace")
    cleanup = sub.add_parser("cleanup", help="delete a run workspace created by init --ephemeral")
    cleanup.add_argument("workdir")
    cleanup.add_argument("--force", action="store_true", help="confirm deletion")
    sub.add_parser("validate", help="validate the profile")
    show = sub.add_parser("show", help="show a redacted profile summary")
    show.add_argument("--json", action="store_true", help="show full JSON, including sensitive fields")
    set_cmd = sub.add_parser("set", help="set one supported dot-path field")
    set_cmd.add_argument("field")
    set_cmd.add_argument("value")
    unset = sub.add_parser("unset", help="reset one field to its default")
    unset.add_argument("field")
    fill = sub.add_parser("fill", help="merge a partial version-5 JSON profile")
    fill.add_argument("file")
    export = sub.add_parser("export", help="write a private copy to another path")
    export.add_argument("destination")
    export.add_argument("--force", action="store_true")
    migrate = sub.add_parser("migrate", help="migrate a legacy version-2, 3, or 4 profile")
    migrate.add_argument("source")
    migrate.add_argument("--force", action="store_true", help="replace the destination profile")
    delete = sub.add_parser("delete", help="move the selected profile to the private trash")
    delete.add_argument("--force", action="store_true", help="confirm deletion")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    path = resolve_profile_path(args.profile_path)
    try:
        if args.command == "schema":
            print(json.dumps({"profile": default_profile(), "supported_fields": sorted(FIELD_RULES)}, indent=2))
            return 0
        if args.command == "questionnaire":
            data = load_profile(path) if path.exists() else None
            print(json.dumps(questionnaire(data), indent=2, ensure_ascii=False))
            return 0
        if args.command == "init":
            if args.ephemeral:
                print(json.dumps(start_run(args.session_id), indent=2))
                return 0
            if not init_profile(path, force=args.force):
                raise ValueError(f"refusing to overwrite {path}; rerun with --force only after approval")
            print(f"Created private profile: {path}")
            return 0
        if args.command == "workdir":
            run_dir = create_run_dir(args.session_id)
            print(json.dumps({"workdir": str(run_dir), "files": run_paths(run_dir)}, indent=2))
            return 0
        if args.command == "cleanup":
            if not args.force:
                raise ValueError("refusing to delete the run workspace without --force")
            result = cleanup_run(args.workdir)
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 2
        if args.command == "validate":
            data = load_profile(path)
            errors, warnings = validate_profile(data)
            if errors:
                raise ValueError("invalid profile:\n- " + "\n- ".join(errors))
            if os.name != "nt":
                mode = path.stat().st_mode & 0o777
                if mode & 0o077:
                    warnings.append(f"profile permissions are {mode:o}; run chmod 600 '{path}'")
            print("Profile is valid.")
            for warning in warnings:
                print(f"warning: {warning}")
            for missing in missing_required_fields(data):
                print(f"missing: {missing}")
            return 0
        if args.command == "finish-questionnaire":
            update = _read_json_source(args.file)
            result = complete_onboarding(path, update)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["ok"] else 2
        if args.command == "show":
            data = load_profile(path)
            print(json.dumps(data, indent=2, ensure_ascii=False) if args.json else profile_summary(data))
            return 0
        if args.command == "set":
            normalized = normalize_path(args.field)
            value = parse_value(normalized, args.value)
            with profile_lock(path):
                data = load_profile(path)
                set_path(data, normalized, value)
                _record_disposition(data, normalized)
                _save_validated(path, data)
            print(f"Set {normalized}")
            return 0
        if args.command == "unset":
            normalized = normalize_path(args.field)
            if normalized not in FIELD_RULES:
                raise ValueError(f"unsupported profile field: {args.field}")
            with profile_lock(path):
                data = load_profile(path)
                set_path(data, normalized, copy.deepcopy(get_path(default_profile(), normalized)))
                _record_disposition(data, normalized)
                _save_validated(path, data)
            print(f"Reset {normalized}")
            return 0
        if args.command == "fill":
            update = _read_json_source(args.file)
            if not isinstance(update, dict):
                raise ValueError("partial profile must be a JSON object")
            if "onboarding" in update or any(
                isinstance(key, str) and key.startswith("onboarding.") for key in update
            ):
                raise ValueError(
                    "fill cannot change onboarding state; use finish-questionnaire "
                    "with a response for every questionnaire field"
                )
            with profile_lock(path):
                data = load_profile(path)
                changed_fields = _present_supported_paths(update)
                _merge_known(data, update)
                data["schema_version"] = SCHEMA_VERSION
                for field in changed_fields:
                    _record_disposition(data, field)
                _save_validated(path, data)
            print(f"Updated private profile: {path}")
            if not optional_questionnaire_completed(data):
                print(
                    "Questionnaire is still incomplete. During onboarding, use "
                    "finish-questionnaire to apply answers and complete it atomically."
                )
            return 0
        if args.command == "export":
            destination = Path(args.destination).expanduser()
            if destination.exists() and not args.force:
                raise ValueError(f"refusing to overwrite {destination}; use --force to confirm")
            _save_validated(destination, load_profile(path))
            print(f"Exported private profile: {destination}")
            return 0
        if args.command == "migrate":
            source = Path(args.source).expanduser()
            raw = _json_loads(source.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("legacy profile must be a JSON object")
            data = migrate_legacy(raw)
            with profile_lock(path):
                if path.exists() and not args.force:
                    raise ValueError(f"refusing to overwrite {path}; use --force to confirm")
                sidecar = _write_legacy_tracking_sidecar(raw, path)
                _save_validated(path, data)
            print(f"Migrated profile to: {path}")
            if sidecar:
                print(f"Preserved old application tracking separately: {sidecar}")
            return 0
        if args.command == "delete":
            if not args.force:
                raise ValueError("refusing to delete the profile without --force")
            with profile_lock(path):
                if not path.exists():
                    raise ValueError(f"profile does not exist: {path}")
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                trash_path = _trash_dir() / f"{re.sub(r'[^a-z0-9-]+', '-', path.stem.casefold())}-{stamp}.json"
                os.replace(path, trash_path)
            print(f"Moved profile to the trash: {trash_path}")
            return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
