from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import pipeline
from conftest import TODAY, load_reference, make_profile, official_record


def _has_tzdata() -> bool:
    return pipeline._zone("America/Los_Angeles") is not None


needs_tzdata = pytest.mark.skipif(not _has_tzdata(), reason="time zone database unavailable")


def evaluate(profile: dict, records: list[dict], today: date | pipeline.Clock = TODAY) -> dict:
    return pipeline.evaluate_candidates(profile, {"scholarships": records}, today, 7)


def only_bucket(result: dict) -> tuple[str, dict]:
    found = [(name, record) for name in pipeline.BUCKETS for record in result[name]]
    assert len(found) == 1, result["summary"]
    return found[0]


def test_valid_official_record_is_verified(profile):
    bucket, record = only_bucket(evaluate(profile, [official_record()]))
    assert bucket == "verified"
    assert record["eligibility_state"] == "eligible"
    assert record["days_left"] == (date(2027, 2, 1) - TODAY).days


def test_worked_example_file_is_valid(tmp_path: Path):
    data = make_profile(tmp_path / "p.json", {"student.affiliations.employer": "Parent: Example Manufacturing Company"})
    result = pipeline.evaluate_candidates(data, load_reference("candidate-example.json"), TODAY, 7)
    assert result["summary"]["invalid"] == 0, result["invalid"]
    assert [record["name"] for record in result["verified"]] == ["Example Valley STEM Scholarship"]
    assert result["needs_confirmation"][0]["name"] == "Example Manufacturing Dependents Scholarship"
    assert result["unverified"][0]["name"] == "Example Robotics Award"


def test_discovery_domain_cannot_be_the_official_source(profile):
    record = official_record(official_url="https://www.scholarships.com/example")
    record["source_verification"]["authority_url"] = "https://www.scholarships.com/about"
    result = evaluate(profile, [record])
    assert result["summary"]["invalid"] == 1
    assert "discovery database" in result["invalid"][0]["validation_notes"][0]


def test_discovery_lead_is_unverified(profile):
    record = official_record(source_kind="discovery", source_verification=None, official_url="https://www.fastweb.com/x")
    bucket, _ = only_bucket(evaluate(profile, [record]))
    assert bucket == "unverified"


def registry_record(name: str, url: str, kind: str = "official") -> dict:
    return official_record(
        source_kind=kind,
        official_url=url,
        source_verification={
            "method": "trusted_registry",
            "authority_url": url,
            "evidence": "The agency's own site.",
            "trusted_source": name,
        },
    )


def test_trusted_registry_accepts_registered_domains(profile):
    record = registry_record("Ohio Department of Higher Education", "https://highered.ohio.gov/grants/ocog")
    bucket, stored = only_bucket(evaluate(profile, [record]))
    assert bucket == "verified"
    assert stored["source_verification"]["trusted_source"] == "Ohio Department of Higher Education"


def test_trusted_registry_name_match_ignores_case(profile):
    record = registry_record("ohio department of higher education", "https://highered.ohio.gov/x")
    bucket, stored = only_bucket(evaluate(profile, [record]))
    assert bucket == "verified"
    assert stored["source_verification"]["trusted_source"] == "Ohio Department of Higher Education"


def test_unknown_registry_name_suggests_matches(profile):
    record = registry_record("Ohio Higher Ed", "https://highered.ohio.gov/x")
    result = evaluate(profile, [record])
    assert "Ohio Department of Higher Education" in result["invalid"][0]["validation_notes"][0]


def test_registry_domain_mismatch_is_rejected(profile):
    record = registry_record("Ohio Department of Higher Education", "https://www.example.org/ohio")
    result = evaluate(profile, [record])
    assert "does not match the registered domains" in result["invalid"][0]["validation_notes"][0]


def test_discovery_registry_entries_cannot_verify(profile):
    record = registry_record("BigFuture Scholarship Search", "https://bigfuture.collegeboard.org/x")
    result = evaluate(profile, [record])
    assert "discovery" in result["invalid"][0]["validation_notes"][0]


def test_registry_role_decides_source_kind(profile):
    record = registry_record("Scholarship America", "https://scholarshipamerica.org/x")
    result = evaluate(profile, [record])
    assert "authorized_administrator" in result["invalid"][0]["validation_notes"][0]


def test_sponsor_domain_needs_same_domain(profile):
    record = official_record()
    record["source_verification"]["authority_url"] = "https://www.example.com/about"
    result = evaluate(profile, [record])
    assert "same domain" in result["invalid"][0]["validation_notes"][0]


def test_authorized_administrator_needs_independent_sponsor_page(profile):
    record = official_record(
        source_kind="authorized_administrator",
        source_verification={
            "method": "authorized_by_sponsor",
            "authority_url": "https://www.example.org/sponsor",
            "evidence": "Same site.",
            "trusted_source": None,
        },
    )
    result = evaluate(profile, [record])
    assert "independent sponsor" in result["invalid"][0]["validation_notes"][0]


def test_past_deadline_is_expired(profile):
    record = official_record(deadline={"date": "2026-10-21", "timezone": "America/New_York", "type": "received_by"})
    bucket, _ = only_bucket(evaluate(profile, [record]))
    assert bucket == "expired"


@needs_tzdata
def test_deadline_uses_its_own_time_zone(profile):
    record = official_record(deadline={"date": "2027-03-01", "timezone": "America/Los_Angeles", "type": "received_by"})
    # 05:00 UTC on March 2 is still March 1 in Los Angeles.
    still_open = pipeline.Clock(now=datetime(2027, 3, 2, 5, 0, tzinfo=timezone.utc))
    late = pipeline.Clock(now=datetime(2027, 3, 2, 9, 0, tzinfo=timezone.utc))
    record["verified_at"] = "2027-03-01"
    assert pipeline._classify(pipeline._validate_candidate(record, profile, still_open, 7), profile, still_open)[0] == "verified"
    assert pipeline._classify(pipeline._validate_candidate(record, profile, late, 7), profile, late)[0] == "expired"


def test_unknown_time_zone_never_expires_early(profile):
    record = official_record(deadline={"date": "2027-03-01", "timezone": "", "type": "received_by"}, verified_at="2027-03-01")
    clock = pipeline.Clock(now=datetime(2027, 3, 2, 11, 0, tzinfo=timezone.utc))
    bucket, result = pipeline._classify(pipeline._validate_candidate(record, profile, clock, 7), profile, clock)
    assert bucket == "needs_confirmation"
    assert "Confirm the deadline timezone with the sponsor." in result["uncertainties"]


@needs_tzdata
def test_timezone_abbreviations_are_understood():
    assert pipeline._zone("ET") == pipeline._zone("America/New_York")
    assert pipeline._zone("Pacific") == pipeline._zone("America/Los_Angeles")
    assert pipeline._zone("Not/AZone") is None


def test_closing_soon_flag(profile):
    record = official_record(deadline={"date": "2026-10-30", "timezone": "America/New_York", "type": "received_by"})
    _, stored = only_bucket(evaluate(profile, [record]))
    assert stored["closing_soon"] is True
    assert stored["days_left"] == 8


def test_rolling_deadline_without_date_needs_confirmation(profile):
    record = official_record(deadline={"date": None, "timezone": "", "type": "rolling"})
    bucket, stored = only_bucket(evaluate(profile, [record]))
    assert bucket == "needs_confirmation"
    assert any("Rolling deadline" in item for item in stored["uncertainties"])


def test_open_record_needs_a_date_unless_rolling(profile):
    record = official_record(deadline={"date": None, "timezone": "", "type": "received_by"})
    assert evaluate(profile, [record])["summary"]["invalid"] == 1


def test_minimum_award_preference(tmp_path: Path):
    data = make_profile(tmp_path / "p.json", {"preferences.minimum_award": "$1,000"})
    too_small = official_record(amount={"display": "$500", "minimum": 500, "maximum": 500})
    assert only_bucket(evaluate(data, [too_small]))[0] == "rejected"
    at_least = official_record(amount={"display": "At least $1,500", "minimum": 1500, "maximum": None})
    bucket, stored = only_bucket(evaluate(data, [at_least]))
    assert bucket == "verified", stored["uncertainties"]
    unknown = official_record(amount={"display": "Varies", "minimum": None, "maximum": None})
    assert only_bucket(evaluate(data, [unknown]))[0] == "needs_confirmation"


def gpa_rule(minimum: float) -> dict:
    return {
        "field": "student.academics.unweighted_gpa",
        "operator": "gte",
        "value": minimum,
        "hard": True,
        "description": f"Minimum {minimum} GPA",
        "evidence": f"Minimum {minimum} GPA.",
    }


def test_gpa_rules_compare_on_a_four_point_scale(profile):
    assert only_bucket(evaluate(profile, [official_record(requirements=[gpa_rule(3.5)])]))[0] == "verified"
    assert only_bucket(evaluate(profile, [official_record(requirements=[gpa_rule(3.9)])]))[0] == "ineligible"


def test_other_gpa_scales_need_confirmation(tmp_path: Path):
    data = make_profile(tmp_path / "p.json", {
        "student.academics.unweighted_gpa": "92",
        "student.academics.gpa_scale": "100",
    })
    bucket, stored = only_bucket(evaluate(data, [official_record(requirements=[gpa_rule(3.0)])]))
    assert bucket == "needs_confirmation"
    assert any("100-point scale" in item for item in stored["uncertainties"])


def test_any_logic_passes_when_one_branch_matches(profile):
    rules = [
        gpa_rule(3.9),
        {
            "field": "student.affiliations.clubs",
            "operator": "contains_any",
            "value": ["robotics"],
            "hard": True,
            "description": "Robotics team member",
            "evidence": "Robotics team members may apply.",
        },
    ]
    record = official_record(requirements=rules, eligibility_logic={"any": [{"requirement": 0}, {"requirement": 1}]})
    assert only_bucket(evaluate(profile, [record]))[0] == "verified"
    record["eligibility_logic"] = {"all": [{"requirement": 0}, {"requirement": 1}]}
    assert only_bucket(evaluate(profile, [record]))[0] == "ineligible"
    record["eligibility_logic"] = {"all": [{"not": {"requirement": 0}}, {"requirement": 1}]}
    assert only_bucket(evaluate(profile, [record]))[0] == "verified"


def test_logic_must_cover_every_hard_requirement(profile):
    record = official_record(requirements=[gpa_rule(3.0), gpa_rule(2.0)], eligibility_logic={"requirement": 0})
    assert "must cover every hard requirement" in evaluate(profile, [record])["invalid"][0]["validation_notes"][0]


def test_unknown_profile_value_needs_confirmation(profile):
    rule = {
        "field": "student.financial_need",
        "operator": "truthy",
        "value": True,
        "hard": True,
        "description": "Demonstrated financial need",
        "evidence": "Need-based.",
    }
    bucket, stored = only_bucket(evaluate(profile, [official_record(requirements=[rule])]))
    assert bucket == "needs_confirmation"
    assert "Confirm requirement: Demonstrated financial need" in stored["uncertainties"]


@pytest.mark.parametrize(
    ("field", "value", "bucket"),
    [
        ("application_fee", True, "rejected"),
        ("is_loan", True, "rejected"),
        ("is_sweepstakes", True, "rejected"),
        ("application_fee", None, "needs_confirmation"),
    ],
)
def test_fee_loan_and_sweepstakes(profile, field, value, bucket):
    assert only_bucket(evaluate(profile, [official_record(**{field: value})]))[0] == bucket


@pytest.mark.parametrize("verified_at", ["2026-10-10", "2026-10-25"])
def test_evidence_must_be_from_this_run(profile, verified_at):
    assert evaluate(profile, [official_record(verified_at=verified_at)])["summary"]["invalid"] == 1


def test_wrong_cycle_is_invalid_and_unknown_cycle_is_unverified(profile):
    assert evaluate(profile, [official_record(cycle="2025-2026")])["summary"]["invalid"] == 1
    unknown = official_record(status="cycle_unknown", cycle="Not stated")
    assert only_bucket(evaluate(profile, [unknown]))[0] == "unverified"


def test_abbreviated_cycle_labels(profile):
    assert only_bucket(evaluate(profile, [official_record(cycle="2026-27", applicant_entry_year=None)]))[0] == "verified"


def test_duplicates_keep_the_best_record(profile):
    weaker = official_record(name="Example Engineering Scholarship 2027", application_fee=None)
    result = evaluate(profile, [weaker, official_record()])
    assert result["summary"]["verified"] == 1
    assert result["summary"]["needs_confirmation"] == 0


def test_result_limit_moves_extra_matches_aside(tmp_path: Path):
    data = make_profile(tmp_path / "p.json", {"preferences.maximum_results": "1"})
    records = [official_record(name=f"Award {index}") for index in range(3)]
    result = evaluate(data, records)
    assert result["summary"]["returned_matching_results"] == 1
    assert result["summary"]["omitted_by_result_limit"] == 2


@pytest.mark.parametrize(
    "url",
    [
        "http://www.example.org/x",
        "https://user:pass@example.org/x",
        "https://localhost/x",
        "https://192.168.1.10/x",
        "https://www.example.org/<script>",
        "https://www.example.org/a b",
        "https://example.local/x",
    ],
)
def test_unsafe_urls_are_rejected(profile, url):
    assert evaluate(profile, [official_record(official_url=url)])["summary"]["invalid"] == 1


def test_add_candidate_validates_stores_and_counts(profile_path: Path, tmp_path: Path):
    candidates = tmp_path / "candidates.json"
    first = pipeline.add_candidate(profile_path, candidates, official_record(), today=TODAY)
    assert first["ok"] and first["status"] == "added" and first["bucket"] == "verified"
    replaced = pipeline.add_candidate(profile_path, candidates, json.dumps(official_record()), today=TODAY)
    assert replaced["status"] == "replaced"
    assert replaced["stored_records"] == 1
    rejected = pipeline.add_candidate(profile_path, candidates, official_record(cycle="2019"), today=TODAY)
    assert not rejected["ok"] and "cycle" in rejected["error"]
    stored = json.loads(candidates.read_text(encoding="utf-8"))["scholarships"]
    assert len(stored) == 1
    assert first["counts"]["verified"] == 1
    assert first["enough_matches"] is False


def test_add_candidate_reports_enough_matches(tmp_path: Path):
    path = tmp_path / "p.json"
    make_profile(path, {"preferences.maximum_results": "1"})
    result = pipeline.add_candidate(path, tmp_path / "c.json", official_record(), today=TODAY)
    assert result["enough_matches"] is True


def test_evaluation_does_not_mutate_input(profile):
    record = official_record()
    snapshot = copy.deepcopy(record)
    evaluate(profile, [record])
    assert record == snapshot
