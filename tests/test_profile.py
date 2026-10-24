from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

import scholarship_profile as profiles
from conftest import BASE_RESPONSES, make_profile

V1_DISPOSITION_FIELDS = {
    "student.location.county", "student.location.city", "student.location.high_school",
    "student.location.school_district", "student.citizenship", "student.search_mode",
    "student.academics.unweighted_gpa", "student.academics.gpa_scale",
    "student.academics.weighted_gpa", "student.academics.sat", "student.academics.act",
    "student.target_colleges", "student.affiliations.clubs", "student.affiliations.professional",
    "student.affiliations.employer", "student.affiliations.credit_union",
    "student.affiliations.military", "student.first_generation", "student.financial_need",
    "student.sai", "student.identity.ethnicity", "student.identity.lgbtq",
    "student.identity.disability", "student.identity.religion", "student.identity.other",
    "preferences.include_identity_searches", "preferences.minimum_award",
    "preferences.maximum_results", "preferences.include_no_essay",
}


def test_disposition_fields_match_the_stored_v1_format():
    # Saved profiles record a disposition for exactly these fields.
    assert set(profiles.QUESTIONNAIRE_FIELDS) == V1_DISPOSITION_FIELDS


def test_questionnaire_batches_fit_clarify_and_cover_every_field():
    output = profiles.questionnaire()
    seen: list[str] = []
    for batch in output["batches"]:
        questions = batch["clarify"]["questions"]
        assert 1 <= len(questions) <= profiles.MAX_CLARIFY_QUESTIONS
        assert len(questions) == len(batch["fields"]) == len(batch["types"])
        for question in questions:
            assert set(question) <= {"question", "choices"}
            assert len(question.get("choices", [])) <= profiles.MAX_CLARIFY_CHOICES
        seen.extend(batch["fields"])
    assert len(seen) == len(set(seen))
    assert set(seen) == set(profiles.CORE_FIELDS) | set(profiles.QUESTIONNAIRE_FIELDS)
    assert output["batches"][0]["group"] == "core_profile"
    assert set(output["submission_template"]["responses"]) == set(seen)


def test_questionnaire_reports_current_values_and_previous_skips(profile_path: Path):
    output = profiles.questionnaire(profiles.load_profile(profile_path))
    first = output["batches"][0]
    assert first["current_values"]["student.location.state"] == "Ohio"
    skipped = [field for batch in output["batches"] for field in batch.get("previously_skipped", [])]
    assert "student.academics.act" in skipped


def test_complete_onboarding_converts_replies(tmp_path: Path):
    data = make_profile(tmp_path / "p.json")
    assert data["student"]["graduation_year"] == 2027
    assert data["student"]["search_mode"] == "high_school"
    assert data["student"]["academics"]["unweighted_gpa"] == 3.8
    assert data["student"]["academics"]["gpa_scale"] == 4.0
    assert data["student"]["academics"]["sat"] == 1450
    assert data["student"]["academics"]["act"] is None
    assert data["student"]["target_colleges"] == ["Ohio State University", "University of Cincinnati"]
    assert data["student"]["affiliations"]["employer"] == ""
    assert data["student"]["first_generation"] is False
    assert data["student"]["financial_need"] is None
    assert data["preferences"]["maximum_results"] == 20
    dispositions = data["onboarding"]["question_dispositions"]
    assert dispositions["student.academics.gpa_scale"] == "answered"
    assert dispositions["student.academics.act"] == "skipped"
    assert dispositions["student.affiliations.employer"] == "skipped"
    assert profiles.onboarding_complete(data)


def test_complete_onboarding_result_lists_answered_and_skipped(tmp_path: Path, responses):
    path = tmp_path / "p.json"
    profiles.init_profile(path)
    result = profiles.complete_onboarding(path, {"responses": responses})
    assert result["status"] == "ready_for_search"
    assert result["next_action"] == "generate_queries"
    assert "student.academics.sat" in result["answered_fields"]
    assert "student.sai" in result["skipped_fields"]


def test_missing_response_is_reported_and_nothing_is_written(tmp_path: Path, responses):
    path = tmp_path / "p.json"
    profiles.init_profile(path)
    before = path.read_text(encoding="utf-8")
    del responses["student.sai"]
    responses["student.academics.sat"] = "about fourteen hundred"
    result = profiles.complete_onboarding(path, {"responses": responses})
    assert not result["ok"]
    assert any("student.sai" in error for error in result["errors"])
    assert any("student.academics.sat" in error for error in result["errors"])
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("field", ["student.location.state", "student.search_mode"])
def test_required_fields_cannot_be_skipped(tmp_path: Path, responses, field):
    path = tmp_path / "p.json"
    profiles.init_profile(path)
    responses[field] = "prefer not to answer"
    result = profiles.complete_onboarding(path, {"responses": responses})
    assert not result["ok"]
    assert any(field in error for error in result["errors"])


def test_bare_yes_is_not_an_organization_name(tmp_path: Path, responses):
    path = tmp_path / "p.json"
    profiles.init_profile(path)
    responses["student.affiliations.employer"] = "Yes"
    result = profiles.complete_onboarding(path, {"responses": responses})
    assert not result["ok"]
    assert any("not just yes" in error for error in result["errors"])


def test_conflicting_gpa_scale_is_rejected(tmp_path: Path, responses):
    path = tmp_path / "p.json"
    profiles.init_profile(path)
    responses["student.academics.gpa_scale"] = "5.0"
    result = profiles.complete_onboarding(path, {"responses": responses})
    assert not result["ok"]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("High school student applying to college", "high_school"),
        ("high_school", "high_school"),
        ("Current undergraduate", "undergraduate"),
        ("I'm a community college transfer student", "transfer"),
        ("Graduate student", "graduate"),
        ("master's program", "graduate"),
        ("trade school (welding certificate)", "trade_certificate"),
        ("apprenticeship", "trade_certificate"),
    ],
)
def test_search_mode_replies(reply, expected):
    assert profiles.coerce_response("student.search_mode", reply)[0] == expected


def test_unrecognized_search_mode_is_rejected():
    with pytest.raises(ValueError):
        profiles.coerce_response("student.search_mode", "something else")


@pytest.mark.parametrize(
    ("field", "reply", "expected"),
    [
        ("student.first_generation", "Yes", True),
        ("student.first_generation", "no", False),
        ("preferences.minimum_award", "$1,000", 1000),
        ("student.sai", "-1500", -1500),
        ("student.academics.unweighted_gpa", "92", 92.0),
        ("student.affiliations.clubs", '["Key Club", "HOSA"]', ["Key Club", "HOSA"]),
        ("student.affiliations.clubs", "Key Club; HOSA\nBand", ["Key Club", "HOSA", "Band"]),
    ],
)
def test_reply_conversion(field, reply, expected):
    assert profiles.coerce_response(field, reply)[0] == expected


@pytest.mark.parametrize(
    ("field", "reply", "skip"),
    [
        ("student.identity.religion", "None", True),
        ("student.affiliations.credit_union", "no", True),
        ("student.first_generation", "no", False),
        ("student.first_generation", "Prefer not to answer", True),
        ("student.affiliations.clubs", ["none"], True),
        ("student.affiliations.clubs", [], True),
        ("preferences.minimum_award", "No minimum", True),
    ],
)
def test_skip_detection(field, reply, skip):
    assert profiles.is_skip_response(field, reply) is skip


def test_legacy_answers_and_dispositions_format_still_completes(tmp_path: Path):
    path = tmp_path / "p.json"
    profiles.init_profile(path)
    answers = {
        "student.location.state": "Ohio",
        "student.academic_level": "High school senior",
        "student.graduation_year": 2027,
        "student.entry_term": "Fall 2027",
        "student.degree_level": "bachelor",
        "student.academics.intended_major": "Nursing",
        "student.search_mode": "high_school",
    }
    dispositions = {field: "skipped" for field in profiles.QUESTIONNAIRE_FIELDS}
    dispositions["student.search_mode"] = "answered"
    result = profiles.complete_onboarding(path, {"answers": answers, "question_dispositions": dispositions})
    assert result["status"] == "ready_for_search", result


def test_profiles_saved_by_earlier_versions_still_load(tmp_path: Path):
    # Build the stored shape the 0.5 release wrote: v5 schema, v1 dispositions.
    data = profiles.default_profile()
    data["student"]["location"]["state"] = "Kansas"
    data["student"]["academic_level"] = "senior"
    data["student"]["graduation_year"] = 2027
    data["student"]["entry_term"] = "Fall 2027"
    data["student"]["degree_level"] = "bachelor"
    data["student"]["search_mode"] = "high_school"
    data["student"]["academics"]["intended_major"] = "Biology"
    data["onboarding"]["question_dispositions"] = {
        field: "skipped" for field in profiles.QUESTIONNAIRE_FIELDS
    }
    data["onboarding"]["question_dispositions"]["student.search_mode"] = "answered"
    path = tmp_path / "old.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = profiles.load_profile(path)
    assert profiles.onboarding_complete(loaded)


def test_v4_migration_recollects_silently_defaulted_fields():
    legacy = {
        "schema_version": 4,
        "student": {
            "location": {"state": "Ohio"},
            "academic_level": "senior",
            "degree_level": "bachelor",
            "search_mode": "high_school",
        },
    }
    migrated = profiles.migrate_legacy(legacy)
    assert migrated["student"]["location"]["state"] == "Ohio"
    assert migrated["student"]["academic_level"] == ""
    assert migrated["student"]["search_mode"] == ""


def test_v2_migration_drops_unproven_identity_defaults():
    legacy = {"version": 2, "student": {"state": "Ohio", "identity": {"lgbtq": False, "disability": True}}}
    migrated = profiles.migrate_legacy(legacy)
    assert migrated["student"]["identity"]["lgbtq"] is None
    assert migrated["student"]["identity"]["disability"] is True


def test_control_characters_are_rejected():
    data = profiles.default_profile()
    data["student"]["location"]["city"] = "Columbus\x07"
    errors, _ = profiles.validate_profile(data)
    assert any("control characters" in error for error in errors)


def test_start_run_is_private_and_resumable(isolated_tempdir: Path):
    first = profiles.start_run("session-123")
    second = profiles.start_run("session-123")
    assert first["created"] and not second["created"]
    assert first["profile"] == second["profile"]
    workdir = Path(first["workdir"])
    assert workdir.name == "run-session-123"
    assert workdir.parent.parent == isolated_tempdir
    assert set(first["files"]) >= {"profile", "queries", "candidates", "evaluated", "report"}
    if os.name != "nt":
        assert workdir.stat().st_mode & 0o777 == 0o700
        assert Path(first["profile"]).stat().st_mode & 0o777 == 0o600


def test_unsubstituted_session_placeholder_still_resumes(isolated_tempdir: Path):
    first = profiles.start_run("${HERMES_SESSION_ID}")
    second = profiles.start_run("${HERMES_SESSION_ID}")
    assert first["workdir"] == second["workdir"]
    assert Path(first["workdir"]).name == "run-default"


def test_session_ids_are_sanitized(isolated_tempdir: Path):
    run = profiles.start_run("../../etc/passwd")
    assert Path(run["workdir"]).parent.parent == isolated_tempdir
    assert Path(run["workdir"]).name == "run-etcpasswd"


def test_cleanup_removes_only_known_files(isolated_tempdir: Path):
    run = profiles.start_run("cleanup-test")
    workdir = Path(run["workdir"])
    Path(run["files"]["report"]).write_text("report", encoding="utf-8")
    (workdir / "notes.txt").write_text("user file", encoding="utf-8")
    result = profiles.cleanup_run(workdir)
    assert not result["ok"]
    assert result["kept_unknown_files"] == ["notes.txt"]
    assert "profile.json" in result["removed"] and "report.md" in result["removed"]
    assert (workdir / "notes.txt").exists()
    (workdir / "notes.txt").unlink()
    assert profiles.cleanup_run(workdir)["ok"]
    assert not workdir.exists()


def test_cleanup_refuses_other_directories(tmp_path: Path, isolated_tempdir: Path):
    outside = tmp_path / "run-not-ours"
    outside.mkdir()
    (outside / "profile.json").write_text("{}", encoding="utf-8")
    result = profiles.cleanup_run(outside)
    assert not result["ok"]
    assert (outside / "profile.json").exists()


def test_cli_finish_questionnaire_reads_standard_input(tmp_path: Path, monkeypatch, capsys):
    path = tmp_path / "p.json"
    assert profiles.main(["--profile", str(path), "init"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"responses": BASE_RESPONSES})))
    assert profiles.main(["--profile", str(path), "finish-questionnaire", "-"]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["status"] == "ready_for_search"


def test_cli_init_refuses_to_overwrite(tmp_path: Path, capsys):
    path = tmp_path / "p.json"
    assert profiles.main(["--profile", str(path), "init"]) == 0
    assert profiles.main(["--profile", str(path), "init"]) == 2
    assert "refusing to overwrite" in capsys.readouterr().err


def test_cli_validate_and_show(profile_path: Path, capsys):
    assert profiles.main(["--profile", str(profile_path), "validate"]) == 0
    assert "Profile is valid." in capsys.readouterr().out
    assert profiles.main(["--profile", str(profile_path), "show"]) == 0
    summary = capsys.readouterr().out
    assert "State: Ohio" in summary
    assert "1450" not in summary
