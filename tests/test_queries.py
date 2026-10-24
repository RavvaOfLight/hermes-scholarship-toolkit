from __future__ import annotations

from pathlib import Path

import pytest

import pipeline
import scholarship_profile as profiles
from conftest import make_profile


def plan_for(tmp_path: Path, overrides: dict | None = None, max_queries: int = 50) -> dict:
    data = make_profile(tmp_path / "p.json", overrides)
    return pipeline.generate_queries(data, max_queries=max_queries)


def query_texts(plan: dict) -> list[str]:
    return [record["query"] for record in plan["queries"]]


def sources(plan: dict) -> set[str]:
    return {record["suggested_source"] for record in plan["queries"]}


def test_queries_require_completed_onboarding(tmp_path: Path):
    path = tmp_path / "p.json"
    profiles.init_profile(path)
    with pytest.raises(ValueError, match="questionnaire is incomplete|missing required"):
        pipeline.generate_queries(profiles.load_profile(path))


def test_queries_never_contain_scores_or_sai(tmp_path: Path):
    plan = plan_for(tmp_path, {"student.sai": "4200", "student.academics.act": "33"})
    text = " ".join(query_texts(plan))
    for secret in ("3.8", "1450", "4200", "33 ", "4.2"):
        assert secret not in text


def test_identity_queries_need_consent(tmp_path: Path):
    shared = {"student.identity.ethnicity": "Black", "student.identity.lgbtq": "Yes"}
    without = plan_for(tmp_path / "a", shared)
    assert not any("Black" in query or "LGBTQ" in query for query in query_texts(without))
    assert "UNCF Scholarships" not in sources(without)
    assert "Point Foundation" not in sources(without)

    with_consent = plan_for(tmp_path / "b", {**shared, "preferences.include_identity_searches": "Yes"})
    assert any(query.startswith("Black scholarship") for query in query_texts(with_consent))
    assert {"UNCF Scholarships", "Point Foundation"} <= sources(with_consent)


def test_disability_query_uses_generic_wording(tmp_path: Path):
    plan = plan_for(tmp_path, {
        "student.identity.disability": "Yes",
        "preferences.include_identity_searches": "Yes",
    })
    assert any("students with disabilities scholarship" in query for query in query_texts(plan))


def test_state_agency_query_uses_registry_entry(tmp_path: Path):
    plan = plan_for(tmp_path)
    agency = [record for record in plan["queries"] if record["suggested_source"] == "Ohio Department of Higher Education"]
    assert len(agency) == 1
    assert agency[0]["tier"] == 3
    assert "highered.ohio.gov" in agency[0]["official_domains"]
    assert "California Student Aid Commission" not in sources(plan)


def test_state_abbreviation_triggers_state_agency(tmp_path: Path):
    plan = plan_for(tmp_path, {"student.location.state": "KS"})
    assert "Kansas Board of Regents" in sources(plan)


def test_keywords_match_whole_words(tmp_path: Path):
    plan = plan_for(tmp_path)  # clubs include "Academic Decathlon"
    assert "DECA Scholarships" not in sources(plan)
    assert "FIRST Scholarships" in sources(plan)
    with_deca = plan_for(tmp_path / "deca", {"student.affiliations.clubs": "DECA"})
    assert "DECA Scholarships" in sources(with_deca)


def test_match_all_needs_every_keyword_group(tmp_path: Path):
    engineer = plan_for(tmp_path / "a", {
        "student.identity.other": "female",
        "preferences.include_identity_searches": "Yes",
    })
    assert "Society of Women Engineers Scholarships" in sources(engineer)
    nurse = plan_for(tmp_path / "b", {
        "student.academics.intended_major": "Nursing",
        "student.identity.other": "female",
        "preferences.include_identity_searches": "Yes",
    })
    assert "Society of Women Engineers Scholarships" not in sources(nurse)
    no_consent = plan_for(tmp_path / "c", {"student.identity.other": "female"})
    assert "Society of Women Engineers Scholarships" not in sources(no_consent)


def test_need_based_programs_follow_need_and_path(tmp_path: Path):
    unknown_need = plan_for(tmp_path / "a")
    assert "QuestBridge" not in sources(unknown_need)
    need = plan_for(tmp_path / "b", {"student.financial_need": "Yes"})
    assert {"QuestBridge", "Jack Kent Cooke Foundation", "Dell Scholars Program"} <= sources(need)
    transfer = plan_for(tmp_path / "c", {
        "student.financial_need": "Yes",
        "student.search_mode": "transfer student",
    })
    assert "Jack Kent Cooke Foundation" in sources(transfer)
    assert "QuestBridge" not in sources(transfer)


def test_military_affiliation_triggers_family_programs(tmp_path: Path):
    plan = plan_for(tmp_path, {"student.affiliations.military": "Mother: Army veteran"})
    assert {"Army Emergency Relief Scholarships", "Fisher House Scholarships for Military Children"} <= sources(plan)
    assert any(query.startswith("Army veteran family scholarship") for query in query_texts(plan))


def test_queries_are_tier_ordered_and_numbered(tmp_path: Path):
    plan = plan_for(tmp_path)
    tiers = [record["tier"] for record in plan["queries"]]
    assert tiers == sorted(tiers)
    assert [record["id"] for record in plan["queries"]] == [
        f"q{index:03d}" for index in range(1, len(tiers) + 1)
    ]
    assert all(record["must_verify_officially"] for record in plan["queries"])


def test_query_limit_drops_lowest_priority_first(tmp_path: Path):
    full = plan_for(tmp_path / "a")
    limited = plan_for(tmp_path / "b", max_queries=10)
    assert len(limited["queries"]) == 10
    assert limited["omitted_query_count"] == len(full["queries"]) - 10
    assert max(record["tier"] for record in limited["queries"]) <= 3
    assert any("omitted" in warning for warning in limited["warnings"])


def test_relationship_prefixes_are_removed_from_queries(tmp_path: Path):
    plan = plan_for(tmp_path, {"student.affiliations.employer": "Parent: Example Hospital"})
    assert any(query.startswith("Example Hospital employee family scholarship") for query in query_texts(plan))


def test_high_school_sources_only_for_high_school_path(tmp_path: Path):
    senior = plan_for(tmp_path / "a")
    assert any("scholarship bulletin" in query for query in query_texts(senior))
    undergrad = plan_for(tmp_path / "b", {"student.search_mode": "Current undergraduate"})
    assert not any("scholarship bulletin" in query for query in query_texts(undergrad))


def test_missing_location_produces_warning(tmp_path: Path):
    plan = plan_for(tmp_path, {
        "student.location.county": "unknown",
        "student.location.city": "unknown",
    })
    assert any("local coverage" in warning for warning in plan["warnings"])
