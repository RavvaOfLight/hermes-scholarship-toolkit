from __future__ import annotations

import csv
import io
from pathlib import Path

import pipeline
from conftest import TODAY, official_record


def evaluated_with(profile: dict, records: list[dict]) -> dict:
    return pipeline.evaluate_candidates(profile, {"scholarships": records}, TODAY, 7)


def test_report_escapes_page_text(profile):
    record = official_record(name="Award | <script>alert(1)</script> *bold* [link](x)")
    report = pipeline.render_report(evaluated_with(profile, [record]))
    assert "<script>" not in report
    assert "\\<script\\>" in report
    assert "\\| " in report
    assert "\\*bold\\*" in report


def test_report_links_are_autolinks(profile):
    report = pipeline.render_report(evaluated_with(profile, [official_record()]))
    assert "- **Official source:** <https://www.example.org/scholarships/engineering>" in report
    assert "- **Application:** <https://www.example.org/apply>" in report


def test_unsafe_url_is_not_rendered():
    assert pipeline._link("https://www.example.org/<x>") == ""
    assert pipeline._link("javascript:alert(1)") == ""


def test_report_has_overview_and_singular_wording(profile):
    report = pipeline.render_report(evaluated_with(profile, [official_record()]))
    assert "Found 1 verified match and 0 matches needing confirmation." in report
    assert "## At a glance" in report
    assert "| Example Engineering Scholarship | $2,000 | 2027-02-01 |" in report


def test_report_marks_closing_soon(profile):
    record = official_record(deadline={"date": "2026-10-30", "timezone": "America/New_York", "type": "received_by"})
    report = pipeline.render_report(evaluated_with(profile, [record]))
    assert "close within 14 days" in report
    assert "8 (soon)" in report
    assert "closing soon" in report


def test_report_without_matches(profile):
    report = pipeline.render_report(evaluated_with(profile, []))
    assert "No opportunities passed every verification and eligibility check." in report
    assert "## At a glance" not in report


def test_render_rejects_wrong_input():
    try:
        pipeline.render_report({"summary": {}})
    except ValueError as exc:
        assert "schema" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_ics_export_is_valid_calendar_text(profile):
    long_name = "Example; Engineering, Scholarship " + "x" * 120
    evaluated = evaluated_with(profile, [official_record(name=long_name)])
    text = pipeline.export_ics(evaluated)
    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.endswith("END:VCALENDAR\r\n")
    lines = text.split("\r\n")[:-1]
    assert all(len(line.encode("utf-8")) <= 75 for line in lines)
    unfolded = text.replace("\r\n ", "")
    assert "SUMMARY:Scholarship deadline: Example\\; Engineering\\, Scholarship" in unfolded
    assert "DTSTART;VALUE=DATE:20270201" in unfolded
    assert "DTEND;VALUE=DATE:20270202" in unfolded
    assert "TRIGGER:-P7D" in unfolded
    assert "\n" not in text.replace("\r\n", "")


def test_ics_skips_records_without_dates(profile):
    record = official_record(deadline={"date": None, "timezone": "", "type": "rolling"})
    text = pipeline.export_ics(evaluated_with(profile, [record]))
    assert "BEGIN:VEVENT" not in text


def test_csv_export_neutralizes_formulas(profile):
    record = official_record(name="=HYPERLINK(\"https://evil.example\")", sponsor="+Sponsor")
    text = pipeline.export_csv(evaluated_with(profile, [record]))
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][0] == "result"
    assert rows[1][1].startswith("'=")
    assert rows[1][2] == "'+Sponsor"


def test_write_export_keeps_crlf(profile, tmp_path: Path):
    evaluated = evaluated_with(profile, [official_record()])
    output = tmp_path / "deadlines.ics"
    result = pipeline.write_export(evaluated, "ics", output)
    assert result["ok"]
    raw = output.read_bytes()
    assert b"\r\n" in raw and b"\r\r\n" not in raw
