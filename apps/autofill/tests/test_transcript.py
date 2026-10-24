"""Transcript reading: seven real-world layouts plus the lines that must never be read as classes."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import autofill_store as store  # noqa: E402
import transcript  # noqa: E402

FIX = HERE / "tests" / "fixtures" / "transcripts"


def read(name):
    return transcript.parse((FIX / name).read_text())


def table(result):
    return [(c["name"], c["level"], c["grade_level"], c["grade"], c["credits"], c["gpa_points"], c["status"]) for c in result["courses"]]


def test_aligned_table_with_year_headers():
    r = read("aligned.txt")
    rows = table(r)
    assert len(rows) == 11
    assert rows[0] == ("English 9 Honors", "Honors", "9th grade", "A", "1.0", None, "Completed")
    assert ("Spanish I", "Regular", "9th grade", "A", "1.0", None, "Completed") in rows  # the "I" stays a name
    assert ("Physical Education", "Regular", "9th grade", "P", "0.5", None, "Completed") in rows
    assert ("English 10 H", "Honors", "10th grade", "A-", "1.0", None, "Completed") in rows
    assert ("AP World History", "AP", "10th grade", "A", "1.0", None, "Completed") in rows
    assert {c["term"] for c in r["courses"]} == {"2022-23", "2023-24"}
    s = r["summary"]
    assert s["gpa_unweighted"]["value"] == "3.78" and s["gpa_unweighted"]["scale"] == "4.00"
    assert s["gpa_weighted"]["value"] == "4.12"
    assert (s["class_rank"], s["class_size"]) == ("15", "312")


def test_student_details_never_come_back():
    r = read("aligned.txt")
    text = json.dumps(r)
    for secret in ("Ava", "Testerson", "04/15/2009", "1234567", "Main St", "XXX-XX-1234", "1234"):
        assert secret not in text, secret
    assert r["ignored"] >= 4


def test_powerschool_codes_semesters_and_dual_enrollment():
    r = read("powerschool.txt")
    by = {c["name"]: c for c in r["courses"]}
    assert set(by) == {"English 11 AP Lang/Comp", "Pre-Calculus Honors", "AP Physics 1", "College Composition I DE",
                       "AP English Lit/Comp", "AP Physics C"}
    assert by["English 11 AP Lang/Comp"]["grade"] == "A" and by["English 11 AP Lang/Comp"]["term_grades"] == ["A", "A-"]
    assert by["Pre-Calculus Honors"]["level"] == "Honors"
    assert by["College Composition I DE"]["level"] == "Dual enrollment" and by["College Composition I DE"]["credits"] == "3.0"
    lit = by["AP English Lit/Comp"]
    assert lit["grade"] is None and lit["status"] == "In progress" and lit["grade_level"] == "12th grade"
    assert by["AP Physics C"]["level"] == "AP" and by["AP Physics C"]["status"] == "In progress"
    assert r["summary"]["gpa_weighted"]["value"] == "4.21" and r["summary"]["gpa_unweighted"]["value"] == "3.89"
    assert "3.91" not in json.dumps(r["summary"])  # a one-term GPA is not the cumulative one


def test_two_school_years_side_by_side():
    r = read("two_column.txt")
    rows = {(c["name"], c["grade_level"], c["grade"]) for c in r["courses"]}
    assert ("Algebra I", "9th grade", "B") in rows and ("Health/PE", "9th grade", "A") in rows
    assert ("Geometry H", "10th grade", "B+") in rows and ("AP Human Geography", "10th grade", "A") in rows
    assert len(rows) == 8
    assert r["summary"]["gpa_unlabeled"]["value"] == "3.71"  # not called weighted or unweighted: the student picks


@pytest.mark.parametrize("name", ["export.csv", "sheet.tsv"])
def test_spreadsheet_exports(name):
    r = read(name)
    assert r["format"] == "spreadsheet"
    if name == "export.csv":
        assert table(r) == [("English 9", "Honors", "9th grade", "A", "1", "4.5", "Completed"),
                            ("Algebra I", "Regular", "9th grade", "B", "1", "3", "Completed"),
                            ("AP Biology", "AP", "11th grade", "A-", "1", "4.7", "Completed"),
                            ("AP Calculus BC", "AP", "12th grade", None, None, None, "In progress")]
        assert r["courses"][0]["term"] == "2022-23"
    else:
        assert table(r)[2] == ("Dual Enrollment Psychology", "Dual enrollment", "12th grade", "A", "3.0", None, "Completed")


def test_pdf_copy_with_one_cell_per_line():
    r = read("pdf_cells.txt")
    assert table(r) == [("AP English Language", "AP", "11th grade", "A", "1.0", "5.0", "Completed"),
                        ("Honors Pre-Calculus", "Honors", "11th grade", "B+", "1.0", "3.83", "Completed"),
                        ("US History", "Regular", "11th grade", "A-", "1.0", "3.67", "Completed")]
    assert r["summary"]["gpa_weighted"]["value"] == "4.05"


def test_single_spaced_text_keeps_names_whole():
    r = read("single_spaced.txt")
    by = {c["name"]: c for c in r["courses"]}
    assert by["Art I"]["grade"] == "P" and by["Art I"]["credits"] == "0.5"
    assert by["Spanish II"]["grade"] == "95"
    assert by["AP European History"]["gpa_points"] == "4.0"
    assert by["AP Physics C"]["grade"] is None and by["AP Physics C"]["status"] == "In progress"
    assert by["Pre-Calculus Honors"]["status"] == "In progress" and by["Pre-Calculus Honors"]["grade_level"] == "11th grade"
    assert by["Advanced Algebra"]["issues"] and not by["Advanced Algebra"]["ok"]  # "Advanced" may be the name
    assert "Gender" not in by


@pytest.mark.parametrize("line,expected", [
    ("Weighted GPA 4.312 Unweighted GPA 3.876", {"gpa_weighted": "4.312", "gpa_unweighted": "3.876"}),
    ("Unweighted Cumulative GPA: 3.91", {"gpa_unweighted": "3.91"}),
    ("GPA (W): 4.2", {"gpa_weighted": "4.2"}),
    ("Grade Point Average: 3.6", {"gpa_unlabeled": "3.6"}),
    ("Semester GPA 3.5  Cumulative GPA 3.7", {"gpa_unlabeled": "3.7"}),
    ("Term GPA: 3.2", {}),
])
def test_gpa_lines(line, expected):
    out = {}
    transcript.read_summary(line, out)
    got = {k: v["value"] for k, v in transcript._finish_summary(out)["values"].items() if k.startswith("gpa")}
    assert got == expected


@pytest.mark.parametrize("line,rank,size", [("Rank 12/280", "12", "280"), ("Rank in class: 22 of 300", "22", "300"),
                                            ("Class rank: Not ranked", None, None)])
def test_rank_lines(line, rank, size):
    out = {}
    transcript.read_summary(line, out)
    assert (out.get("class_rank"), out.get("class_size")) == (rank, size)


def test_ambiguous_numbers_are_flagged_not_guessed():
    r = transcript.parse("Grade 11\nChemistry A 4.0\nPhysics B 1.0 1.0 3.0 4.0\n")
    by = {c["name"]: c for c in r["courses"]}
    assert by["Chemistry"]["credits"] is None and by["Chemistry"]["gpa_points"] is None and by["Chemistry"]["issues"]


def test_rows_save_through_the_store(tmp_path):
    r = read("powerschool.txt")
    items = [transcript.to_list_item(c) for c in r["courses"]]
    data, errors = store.list_add_many("courses", items, data_dir=tmp_path)
    assert errors == {} and len(data["lists"]["courses"]) == 6
    _, errors = store.list_add_many("courses", [{"name": "X", "grade_level": "13th grade"}], data_dir=tmp_path)
    assert "0" in errors and len(store.load(tmp_path)["lists"]["courses"]) == 6  # nothing half-saved


def test_cli_reads_and_saves(tmp_path):
    env = {"AUTOFILL_DATA_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    out = subprocess.run([sys.executable, str(HERE / "autofill.py"), "transcript", str(FIX / "single_spaced.txt")],
                         capture_output=True, text=True, env=env, check=True).stdout
    assert "Advanced Algebra" in out and "check" in out.lower()
    out = subprocess.run([sys.executable, str(HERE / "autofill.py"), "transcript", str(FIX / "single_spaced.txt"), "--save"],
                         capture_output=True, text=True, env=env, check=True).stdout
    saved = store.load(tmp_path)["lists"]["courses"]
    assert len(saved) == 9 and "Advanced Algebra" not in {c["name"] for c in saved}  # flagged rows wait for the student
    assert "not saved" in out.lower()


@pytest.mark.parametrize("text,expected", [
    ("Course | Term | Grade | Credit\nEnglish 11 | FY | A | 1.0\nAP Chemistry | FY | B+ | 1.0\n",
     [("English 11", "Regular", None, "A", "1.0", None, "Completed"), ("AP Chemistry", "AP", None, "B+", "1.0", None, "Completed")]),
    ("2023-24  10  English 10  A  1.0\n2023-24  10  Geometry H  B  1.0\n",
     [("English 10", "Regular", "10th grade", "A", "1.0", None, "Completed"), ("Geometry H", "Honors", "10th grade", "B", "1.0", None, "Completed")]),
    ("Grade 12 (In Progress)\nSpanish 3 1.0\nAP Statistics 1.0\n",
     [("Spanish 3", "Regular", "12th grade", None, "1.0", None, "In progress"), ("AP Statistics", "AP", "12th grade", None, "1.0", None, "In progress")]),
    ("Grade 11\nAP Biology*      A    1.0\nHEALTH & PE      A    0.5\n",
     [("AP Biology", "AP", "11th grade", "A", "1.0", None, "Completed"), ("Health & PE", "Regular", "11th grade", "A", "0.5", None, "Completed")]),
    ("Course Title          Sem 1   Sem 2   Credits\nGrade 10\nSpanish II            B+      IP      1.0\n",
     [("Spanish II", "Regular", "10th grade", None, "1.0", None, "In progress")]),
    ("Grade 9\nEnglish 9   95   A   1.0\n", [("English 9", "Regular", "9th grade", "A", "1.0", None, "Completed")]),
])
def test_more_layouts(text, expected):
    assert table(transcript.parse(text)) == expected


@pytest.mark.parametrize("text", [
    "Dear Scholarship Committee,\nI am writing to apply for the award. My GPA is strong and I lead the robotics team.\nSincerely,\nAva",
    "SAT  Total 1450  Math 740  EBRW 710  03/2025\nAP Exam Scores\nAP Biology Exam  5\nPSAT/NMSQT 1380\n",
    "Name: Ava Testerson\nGender: F\nDate of Birth: 04/15/2009\nStudent ID: 1234567\nCounselor: D. Lee\n",
])
def test_text_that_is_not_classes_gives_no_classes(text):
    r = transcript.parse(text)
    assert r["courses"] == []
    assert "Testerson" not in json.dumps(r) and "1234567" not in json.dumps(r)


@pytest.mark.parametrize("name", ["attributes.txt", "attributes_single.txt"])
def test_attribute_codes_verified_credits_and_school_gpa_names(name):
    r = read(name)
    assert table(r) == [
        ("Hon World Literature", "Honors", "10th grade", "A", "1.0", None, "Completed"),
        ("Statistics & Probability", "Dual enrollment", "10th grade", "B+", "1.0", None, "Completed"),  # "DE,H": DE wins
        ("Keyboarding Basics", "Regular", "10th grade", "A", "0.0", None, "Completed"),          # 0 credits is fine
        ("AP Chemistry", "AP", "11th grade", "A", "1.0", None, "Completed"),
        ("Composition 11 RSA", "Dual enrollment", "11th grade", None, "1.0", None, "In progress"),    # Work in Progress
    ]
    s = r["summary"]
    # the school's own GPA names are shown as they are, never sorted by the word "Weighted"
    assert s["gpa_other"] == [{"value": "4.4000", "label": "District-Weighted with Honors points"},
                              {"value": "3.9000", "label": "District-Weighted-no added value"}]
    assert "gpa_weighted" not in s and "gpa_unweighted" not in s
    assert (s["class_rank"], s["class_size"]) == ("3", "250")
    assert "Testerson" not in json.dumps(r) and "9999999999" not in json.dumps(r)


def test_a_students_own_class_list_and_exam_scores():
    r = read("student_list.txt")
    by = {c["name"]: c for c in r["courses"]}
    assert by["Intro to Engineering (DE-EGR 120, 121)"]["status"] == "In progress"
    assert by["Intro to Engineering (DE-EGR 120, 121)"]["level"] == "Dual enrollment"
    assert by["Composition (DE-WRT 101, 102)"]["grade_level"] == "11th grade"  # "taken during grade 11"
    assert by["AP Macroeconomics"]["status"] == "In progress"
    assert by["Senior Capstone Seminar (DE-HUM 230)"]["grade_level"] is None  # "Senior Capstone" is a name, not 12th grade
    for unsure in ("Precalculus (DE-MAT 161)", "AP Chemistry"):  # no status written: never ticked by itself
        assert by[unsure]["issues"] and not by[unsure]["ok"]
    assert r["exams"] == [{"exam": "AP Chemistry", "score": "4"}, {"exam": "AP Human Geography", "score": "3"}]


def test_cli_list_update_keeps_other_fields(tmp_path):
    env = {"AUTOFILL_DATA_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    run = lambda *a: subprocess.run([sys.executable, str(HERE / "autofill.py"), *a], capture_output=True, text=True, env=env)
    run("list-add", "courses", "name=Composition 11 RSA", "level=Dual enrollment", "grade_level=11th grade", "term=2025-26", "status=In progress")
    out = run("list-update", "courses", "1", "grade=A", "status=Completed").stdout
    row = store.load(tmp_path)["lists"]["courses"][0]
    assert (row["grade"], row["status"], row["term"], row["level"]) == ("A", "Completed", "2025-26", "Dual enrollment"), out
    assert "NOT SAVED" in run("list-update", "courses", "1", "grade_level=13th grade").stdout
