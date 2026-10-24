"""Accuracy features: excerpt coverage, saved evidence, page audit, notes, worklist,
batch records, broader query plans, per-student profiles, and gold scoring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import TODAY, make_profile, official_record

import audit as page_audit
import pipeline
import scholarship_api as api
import scholarship_profile as profiles
import web_batch

OFFICIAL = "https://www.example.org/scholarships/engineering"
APPLY = "https://www.example.org/apply"
ABOUT = "https://www.example.org/about"

GOOD_PAGE = """# Example Engineering Scholarship
The Example Engineering Society funds the Example Engineering Scholarship for Ohio students.
Award amount: $2,000 for one student each year.
Applications are due February 1, 2027 at 11:59 p.m. ET.
There is no application fee and the scholarship does not need to be repaid.
Recipients are selected based on academic merit by a committee.
"""


# --------------------------------------------------------------------- excerpts

def test_excerpt_keeps_fee_and_time_zone_lines_when_budget_is_tight():
    filler = "\n".join(
        f"Eligibility requirement {i}: applicants must be enrolled full-time seniors with a 3.0 GPA by March {i % 28 + 1}, 2027."
        for i in range(60)
    )
    page = (
        "Example Scholarship\nDeadline: March 1, 2027.\n" + filler
        + "\nThere is no application fee.\nSubmit by 5 p.m. Central Time.\n"
    )
    excerpt = web_batch.build_excerpt(page)["excerpt"]
    assert len(excerpt) <= web_batch.MAX_EXCERPT_CHARS
    assert "no application fee" in excerpt
    assert "5 p.m. Central Time" in excerpt


def test_run_from_file_saves_pages_and_evidence(tmp_path: Path):
    queries = tmp_path / "queries.json"
    queries.write_text(json.dumps({"queries": [{"id": "q001", "query": "example scholarship", "purpose": "test"}]}))

    def search(query, limit=5):
        return {"data": {"web": [{"url": OFFICIAL, "title": "Example", "description": "Example scholarship"}]}}

    def extract(urls, char_limit=None):
        return {"results": [{"url": url, "title": "Example", "content": GOOD_PAGE} for url in urls]}

    result = web_batch.run_from_file(queries, 0, batch_size=1, search=search, extract=extract)
    saved = web_batch.load_page(tmp_path, OFFICIAL)
    assert saved is not None and "no application fee" in saved
    assert result["results"][0]["extracted_pages"][0]["saved_page"].endswith(".md")
    lines = [json.loads(line) for line in (tmp_path / "evidence.jsonl").read_text().splitlines()]
    assert {line["kind"] for line in lines} == {"search_result", "page"}
    assert oct(web_batch.page_path(tmp_path, OFFICIAL).stat().st_mode & 0o777) == "0o600"


def test_page_urls_normalize_trailing_slash_and_fragment():
    assert web_batch.page_path("/w", OFFICIAL + "/#top") == web_batch.page_path("/w", OFFICIAL)
    assert web_batch.page_path("/w", OFFICIAL.replace("www.example.org", "WWW.Example.org")) == web_batch.page_path("/w", OFFICIAL)


# ------------------------------------------------------------------- page checks

@pytest.mark.parametrize("name,text,expected", [
    ("Provost Scholarship", "State U's Provost Scholarship covers tuition.", True),
    ("Deans' Scholarship", "The Deans’ Scholarship is renewable.", True),
    ("Mascot Scholarship (Riverbend/Lakeside County → State University)",
     "Apply for a scholarship: the Foundation administers 75 funds.", False),
    ("Regeneron Science Talent Search 2027", "Regeneron Science Talent Search entrants must be seniors.", True),
    ("Scholarship Program", "Anything at all.", None),
])
def test_name_on_page(name, text, expected):
    assert page_audit.name_on_page(name, text) is expected


@pytest.mark.parametrize("text,found,near", [
    ("Materials must be received by Nov. 1, 2026.", True, True),
    ("Deadline: November 1st.", True, False),
    ("Due 11/01/2026", True, True),
    ("Due 11/10/2026", False, False),
    ("Apply by 1 November 2026", True, True),
    ("Applications close in December.", False, False),
])
def test_deadline_on_page(text, found, near):
    seen = page_audit.deadline_on_page("2026-11-01", text)
    assert (seen["found"], seen["year_near"]) == (found, near)


def test_amount_on_page_and_program_totals():
    assert page_audit.amount_on_page(64000, "an estimated total value of $64,000 over four years") == {
        "found": True, "aggregate_only": False}
    assert page_audit.amount_on_page(412490, "June 2026: $412,490 awarded to 165 students")["aggregate_only"]
    assert page_audit.amount_on_page(25000, "Top award: $25K") ["found"]
    assert not page_audit.amount_on_page(2000, "Awards of $20,000")["found"]


def test_check_record_fills_unknowns_from_explicit_page_wording():
    record = official_record(application_fee=None, is_loan=None, is_sweepstakes=None,
                             deadline={"date": "2027-02-01", "timezone": "", "type": "received_by"})
    findings = page_audit.check_record(record, {OFFICIAL: GOOD_PAGE})
    assert findings["flags"] == []
    assert findings["application_fee"]["value"] is False
    assert findings["is_loan"]["value"] is False
    assert findings["is_sweepstakes"]["value"] is False
    assert findings["timezone"]["value"] == "America/New_York"


def test_check_record_flags_wrong_page_missing_facts_fees_and_drawings():
    page = "Community Foundation\nThe Foundation awarded $412,490 to 165 students. A $25 application fee applies. Winners are randomly selected in a drawing."
    record = official_record(amount={"display": "$412,490", "minimum": None, "maximum": 412490})
    flags = " ".join(page_audit.check_record(record, {OFFICIAL: page})["flags"])
    assert "does not name this scholarship" in flags
    assert "February 1, 2027 does not appear" in flags
    assert "program total" in flags
    assert "mentions a fee" in flags
    assert "drawing" in flags


def test_fee_waiver_is_not_a_no_fee_statement_and_multiple_zones_are_ambiguous():
    page = "Example Engineering Scholarship. Fee waivers are available. Due 11:59 p.m. PT (2:59 a.m. ET)."
    findings = page_audit.check_record(official_record(), {OFFICIAL: page})
    assert "application_fee" not in findings
    assert "timezone" not in findings


def test_apply_findings_never_overrides_and_ignores_stale_findings():
    record = pipeline._validate_candidate(official_record(application_fee=None), make_profile_cached(), TODAY, 7)
    findings = page_audit.check_record(official_record(), {OFFICIAL: GOOD_PAGE})
    stale = {**findings, "official_url": "https://www.example.org/other"}
    assert page_audit.apply_findings(dict(record, uncertainties=[]), stale)["application_fee"] is None
    merged = page_audit.apply_findings(dict(record, uncertainties=[], notes=[]), findings)
    assert merged["application_fee"] is False
    assert any(note.startswith("Page check (no fee)") for note in merged["notes"])
    explicit = dict(record, application_fee=True, uncertainties=[], notes=[])
    assert page_audit.apply_findings(explicit, findings)["application_fee"] is True


_PROFILE_CACHE: dict = {}


def make_profile_cached():
    if "p" not in _PROFILE_CACHE:
        import tempfile
        path = Path(tempfile.mkdtemp()) / "profile.json"
        _PROFILE_CACHE["p"] = make_profile(path)
    return _PROFILE_CACHE["p"]


def test_audit_then_evaluate_moves_a_record_to_verified(tmp_path: Path, profile_path: Path):
    workdir = tmp_path / "run"
    workdir.mkdir()
    candidates = workdir / "candidates.json"
    record = official_record(application_fee=None, is_loan=None, is_sweepstakes=None)
    result = api.add_candidate(profile_path, candidates, record, today=TODAY.isoformat())
    assert result["bucket"] == "needs_confirmation"
    web_batch.save_page(workdir, OFFICIAL, "Example", GOOD_PAGE)
    audit_result = page_audit.audit_candidates(candidates, fetch=None)
    assert audit_result["fills"]["application_fee"] == 1 and audit_result["records_flagged"] == 0
    evaluated = api.evaluate(profile_path, candidates, workdir / "evaluated.json", today=TODAY.isoformat())
    assert evaluated["summary"]["verified"] == 1
    assert evaluated["summary"]["page_checked"] == 1
    without = api.evaluate(profile_path, candidates, workdir / "e2.json", today=TODAY.isoformat(), use_audit=False)
    assert without["summary"]["needs_confirmation"] == 1


def test_audit_fetches_missing_pages_and_reports_unreadable(tmp_path: Path, profile_path: Path):
    workdir = tmp_path / "run"
    workdir.mkdir()
    candidates = workdir / "candidates.json"
    api.add_candidate(profile_path, candidates, official_record(), today=TODAY.isoformat())
    calls = []

    def fetch(urls, char_limit=None):
        calls.append(list(urls))
        return {"results": [{"url": OFFICIAL, "content": GOOD_PAGE}]}

    result = page_audit.audit_candidates(candidates, fetch=fetch)
    assert calls and result["fetched_pages"] == 1
    assert set(result["unreadable_urls"]) == {APPLY, ABOUT}
    assert web_batch.load_page(workdir, OFFICIAL) is not None
    assert json.loads((workdir / "audit.json").read_text())["schema_version"] == 1


# --------------------------------------------------------------- notes & records

def test_plain_notes_render_and_hedged_notes_become_doubts(tmp_path: Path, profile_path: Path):
    candidates = tmp_path / "c.json"
    plain = official_record(notes=["No GPA minimum is published; selection uses essays."])
    hedged = official_record(name="Second Scholarship", notes=["Deadline might move to March."])
    api.add_candidates(profile_path, candidates, [plain, hedged], today=TODAY.isoformat())
    evaluated = api.evaluate(profile_path, candidates, tmp_path / "e.json", today=TODAY.isoformat())
    assert evaluated["summary"]["verified"] == 1 and evaluated["summary"]["needs_confirmation"] == 1
    report = api.render(tmp_path / "e.json", tmp_path / "r.md")["markdown"]
    assert "**Notes:** No GPA minimum is published" in report


def test_long_amount_text_is_shortened_not_rejected(tmp_path: Path, profile_path: Path):
    long_text = "Up to $55,000 per year; " + "renewable for up to four years with satisfactory progress; " * 6
    record = official_record(amount={"display": long_text, "minimum": None, "maximum": 55000})
    result = api.add_candidate(profile_path, tmp_path / "c.json", record, today=TODAY.isoformat())
    assert result["ok"], result
    evaluated = api.evaluate(profile_path, tmp_path / "c.json", tmp_path / "e.json", today=TODAY.isoformat())
    stored = json.loads((tmp_path / "e.json").read_text())
    row = (stored["verified"] + stored["needs_confirmation"])[0]
    assert len(row["amount"]["display"]) <= 200 and row["amount"]["display"].endswith("…")
    assert "Full award text" in row["evidence"]["amount"]
    assert evaluated["ok"]


def test_add_candidates_reports_each_record_and_remove_candidate(tmp_path: Path, profile_path: Path):
    candidates = tmp_path / "c.json"
    bad = official_record(name="Broken", status="open", application_url="")
    result = api.add_candidates(profile_path, candidates, [official_record(), bad], today=TODAY.isoformat())
    assert result["added_or_replaced"] == 1 and result["stored_records"] == 1
    assert result["rejected"][0]["name"] == "Broken" and "application_url" in result["rejected"][0]["error"]
    removed = api.remove_candidate(candidates, "Example Engineering Scholarship")
    assert removed == {"ok": True, "removed": 1, "stored_records": 0}
    assert api.remove_candidate(candidates, "Nope")["ok"] is False


def test_worklist_separates_fixable_ask_user_and_waiting(tmp_path: Path, profile_path: Path):
    candidates = tmp_path / "c.json"
    records = [
        official_record(name="Fee Unknown", application_fee=None),
        official_record(name="Not Open", status="not_open_yet",
                        deadline={"date": None, "timezone": "", "type": "not_announced"}, application_url=""),
        official_record(name="Needs FAFSA Info", requirements=[{
            "field": "student.sai", "operator": "lte", "value": 5000, "hard": True,
            "description": "SAI of 5,000 or less", "evidence": "SAI at most 5,000."}]),
    ]
    api.add_candidates(profile_path, candidates, records, today=TODAY.isoformat())
    work = api.evaluate(profile_path, candidates, tmp_path / "e.json", today=TODAY.isoformat())["worklist"]
    assert work["blocking_reasons"]["application_fee"] == 1
    assert work["fixable_by_page_check"][0]["name"] == "Fee Unknown"
    assert work["ask_user_about"] == {"student.sai": 1}
    assert work["waiting_for_cycle_or_amount"] == 1


# ------------------------------------------------------------------ query plans

def test_query_plan_adds_local_school_portal_and_interest_families(tmp_path: Path):
    path = tmp_path / "p.json"
    make_profile(path, {"student.affiliations.clubs": "Varsity football, FIRST Robotics Competition"})
    plan = pipeline.generate_queries(profiles.load_profile(path))
    purposes = {query["purpose"] for query in plan["queries"]}
    for expected in ("college scholarship portal", "community foundation fund directory", "civic club scholarships",
                     "high-school counselor scholarship list", "school-district scholarship packet",
                     "STEM research competitions", "scholar-athlete scholarships"):
        assert expected in purposes, expected
    assert len(plan["queries"]) <= 60


def test_query_limit_bounds(tmp_path: Path, profile_path: Path):
    assert api.generate_queries(profile_path, tmp_path / "q.json", max_queries=100)["ok"]
    assert api.generate_queries(profile_path, tmp_path / "q.json", max_queries=101)["ok"] is False


def test_as_many_as_possible_means_the_maximum(tmp_path: Path):
    data = make_profile(tmp_path / "p.json", {"preferences.maximum_results": "as many as possible"})
    assert data["preferences"]["maximum_results"] == 100


# ------------------------------------------------------------ student profiles

@pytest.fixture
def profile_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "hermes"
    monkeypatch.setattr(profiles, "DEFAULT_PROFILE_PATH", home / "scholarship-profile.json")
    monkeypatch.setattr(profiles, "PROFILES_DIR", home / "scholarship-profiles")
    return home


def test_each_student_gets_their_own_profile_and_workspace(profile_home: Path):
    first = api.start_run(session_id="s1", student="Jordan")
    assert first["profile"].endswith("scholarship-profiles/jordan.json") and first["student"] == "jordan"
    clash = api.start_run(session_id="s1", student="Riley")
    assert clash["ok"] is False and "belongs to jordan" in clash["error"]
    default = api.start_run(session_id="s2", persistent=True)
    assert default["profile"].endswith("scholarship-profile.json") and default["student"] == "default"
    runs = api.list_runs()["runs"]
    assert {run["student"] for run in runs} == {"jordan", "default"}
    resumed = api.start_run(resume=first["workdir"].rsplit("/", 1)[1])
    assert resumed["workdir"] == first["workdir"] and resumed["student"] == "jordan"


def test_temporary_profile_can_switch_to_a_saved_one_in_the_same_session(profile_home: Path):
    api.start_run(session_id="s1")
    assert api.start_run(session_id="s1", student="Jordan")["ok"]


def test_delete_needs_confirmation_moves_to_trash_and_can_be_restored(profile_home: Path):
    run = api.start_run(session_id="s1", student="Jordan")
    make_profile(Path(run["profile"]))
    listed = api.list_profiles()["profiles"]
    assert listed[0]["student"] == "jordan" and "Example High School" in listed[0]["identity"]
    step1 = api.delete_profile("Jordan")
    assert step1["status"] == "confirm_required" and "Example High School" in step1["summary"]
    assert Path(run["profile"]).exists()
    step2 = api.delete_profile("Jordan", confirm=True)
    assert step2["status"] == "moved_to_trash" and not Path(run["profile"]).exists()
    assert step2["removed_run_workspaces"] == [run["workdir"]] and not Path(run["workdir"]).exists()
    trash = api.list_profiles()["trash"]
    assert trash[0]["trash_file"] == step2["trash_file"]
    restored = api.restore_profile(step2["trash_file"])
    assert restored["ok"] and Path(restored["profile"]).exists()
    assert api.delete_profile("nobody")["ok"] is False


def test_empty_trash_only_takes_trash_names(profile_home: Path):
    run = api.start_run(session_id="s1", student="Sam")
    make_profile(Path(run["profile"]))
    trash_file = api.delete_profile("Sam", confirm=True)["trash_file"]
    assert api.empty_trash("../scholarship-profile.json")["ok"] is False
    assert api.empty_trash(trash_file) == {"ok": True, "deleted": trash_file}


def test_cleanup_removes_saved_pages(profile_home: Path):
    run = api.start_run(session_id="s1")
    web_batch.save_page(run["workdir"], OFFICIAL, "Example", GOOD_PAGE)
    (Path(run["workdir"]) / "evidence.jsonl").write_text("{}\n")
    result = api.cleanup(run["workdir"])
    assert result["ok"] and not Path(run["workdir"]).exists()


# ------------------------------------------------------------------ gold scoring

def test_score_against_gold(tmp_path: Path, profile_path: Path):
    candidates = tmp_path / "c.json"
    api.add_candidates(profile_path, candidates, [official_record()], today=TODAY.isoformat())
    api.evaluate(profile_path, candidates, tmp_path / "e.json", today=TODAY.isoformat())
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({"scholarships": [
        {"name": "Example Engineering Scholarship", "official_url": OFFICIAL, "deadline": "2027-02-01",
         "amount_max": 2000, "eligible": True},
        {"name": "Missing Memorial Award", "deadline": "2027-03-01"},
    ]}))
    result = api.score(tmp_path / "e.json", gold)
    assert result["recall"] == 0.5 and result["deadline_accuracy"] == 1.0 and result["amount_accuracy"] == 1.0
    assert result["source_accuracy"] == 1.0 and result["eligibility_accuracy"] == 1.0
    assert result["missed"] == ["Missing Memorial Award"]


def test_audit_stops_for_time_and_asks_to_continue(tmp_path: Path, profile_path: Path):
    workdir = tmp_path / "run"
    workdir.mkdir()
    candidates = workdir / "candidates.json"
    records = [official_record(name=f"Award {i}", official_url=f"https://www.example.org/award-{i}",
                               application_url=f"https://www.example.org/apply-{i}") for i in range(4)]
    api.add_candidates(profile_path, candidates, records, today=TODAY.isoformat())
    ticks = iter(range(0, 10_000, 500))
    result = page_audit.audit_candidates(candidates, fetch=lambda urls, char_limit=None: {"results": []},
                                         time_budget=100, clock=lambda: next(ticks))
    assert result["not_reached_count"] > 0
    assert result["next_action"] == "audit_again_to_read_remaining_pages"


def test_fafsa_name_is_not_a_no_fee_statement_but_automatic_consideration_is():
    fafsa = "Applicants are encouraged to submit a Free Application for Federal Student Aid (FAFSA)."
    assert "application_fee" not in page_audit.check_record(official_record(), {OFFICIAL: fafsa})
    auto = "All incoming first-year students who apply to the university by Nov. 1 will be considered."
    assert page_audit.check_record(official_record(), {OFFICIAL: auto})["application_fee"]["value"] is False
    assert page_audit.check_record(official_record(), {OFFICIAL: "Submitting it is a free application."})["application_fee"]["value"] is False
