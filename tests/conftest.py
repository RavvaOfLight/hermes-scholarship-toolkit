"""Shared fixtures. Tests are offline: Hermes web tools are replaced by fakes."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "scholarship-research"
SCRIPTS = SKILL_DIR / "scripts"
REFERENCES = SKILL_DIR / "references"
sys.path.insert(0, str(SCRIPTS))

import pipeline  # noqa: E402
import scholarship_profile as profiles  # noqa: E402

TODAY = date(2026, 10, 22)

BASE_RESPONSES: dict[str, Any] = {
    "student.location.state": "Ohio",
    "student.academic_level": "High school senior",
    "student.graduation_year": "2027",
    "student.entry_term": "Fall 2027",
    "student.degree_level": "Bachelor's",
    "student.academics.intended_major": "Mechanical Engineering",
    "student.search_mode": "High school student applying to college",
    "student.location.county": "Franklin County",
    "student.location.city": "Columbus",
    "student.location.high_school": "Example High School",
    "student.location.school_district": "Example City Schools",
    "student.citizenship": "U.S. citizen",
    "student.academics.unweighted_gpa": "3.8/4.0",
    "student.academics.gpa_scale": "unknown",
    "student.academics.weighted_gpa": "4.2",
    "student.academics.sat": "1450",
    "student.academics.act": "none",
    "student.target_colleges": "Ohio State University, University of Cincinnati",
    "student.affiliations.clubs": "FIRST Robotics Competition, Academic Decathlon",
    "student.affiliations.professional": "none",
    "student.affiliations.employer": "no",
    "student.affiliations.credit_union": "Example Credit Union",
    "student.affiliations.military": "prefer not to answer",
    "student.first_generation": "No",
    "student.financial_need": "Unknown",
    "student.sai": "unknown",
    "student.identity.ethnicity": "prefer not to answer",
    "student.identity.lgbtq": "Prefer not to answer",
    "student.identity.disability": "Prefer not to answer",
    "student.identity.religion": "none",
    "student.identity.other": "none",
    "preferences.include_identity_searches": "No",
    "preferences.minimum_award": "No minimum",
    "preferences.maximum_results": "20",
    "preferences.include_no_essay": "No",
}


@pytest.fixture(autouse=True)
def isolated_tempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep run workspaces and registry caches inside each test's own directory."""
    temp_root = tmp_path / "systemp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    pipeline._REGISTRY_CACHE.clear()
    return temp_root


@pytest.fixture
def responses() -> dict[str, Any]:
    return copy.deepcopy(BASE_RESPONSES)


def make_profile(path: Path, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a profile and complete onboarding with BASE_RESPONSES plus overrides."""
    replies = copy.deepcopy(BASE_RESPONSES)
    replies.update(overrides or {})
    profiles.init_profile(path)
    result = profiles.complete_onboarding(path, {"responses": replies})
    assert result["ok"], result
    return profiles.load_profile(path)


@pytest.fixture
def profile_path(tmp_path: Path) -> Path:
    path = tmp_path / "profile.json"
    make_profile(path)
    return path


@pytest.fixture
def profile(profile_path: Path) -> dict[str, Any]:
    return profiles.load_profile(profile_path)


def official_record(**overrides: Any) -> dict[str, Any]:
    """A valid official record that matches the base profile."""
    record: dict[str, Any] = {
        "name": "Example Engineering Scholarship",
        "sponsor": "Example Engineering Society",
        "source_kind": "official",
        "source_verification": {
            "method": "sponsor_domain",
            "authority_url": "https://www.example.org/about",
            "evidence": "The society's About page says it funds the scholarship.",
            "trusted_source": None,
        },
        "cycle": "2027-2028 academic year",
        "applicant_entry_year": 2027,
        "status": "open",
        "amount": {"display": "$2,000", "minimum": 2000, "maximum": 2000},
        "deadline": {"date": "2027-02-01", "timezone": "America/New_York", "type": "received_by"},
        "requirements": [
            {
                "field": "student.location.state",
                "operator": "eq",
                "value": "OH",
                "hard": True,
                "description": "Ohio resident",
                "evidence": "Open to Ohio residents.",
            }
        ],
        "eligibility_logic": None,
        "application_tasks": ["Online application"],
        "official_url": "https://www.example.org/scholarships/engineering",
        "application_url": "https://www.example.org/apply",
        "verified_at": TODAY.isoformat(),
        "evidence": {
            "amount": "$2,000 award",
            "deadline": "Due February 1, 2027",
            "eligibility": "Ohio residents",
            "application": "Apply online",
        },
        "uncertainties": [],
        "application_fee": False,
        "is_loan": False,
        "is_sweepstakes": False,
    }
    record.update(overrides)
    return record


def load_reference(name: str) -> Any:
    return json.loads((REFERENCES / name).read_text(encoding="utf-8"))
