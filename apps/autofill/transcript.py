"""Read classes, grades, credits and GPA lines out of a pasted high school transcript.

Transcripts come in many shapes: aligned tables with a header row, PowerSchool/Aspen style
rows with course codes and semester grades, two school years side by side, CSV or spreadsheet
pastes, and text copied out of a PDF viewer, which often puts every cell on its own line.
This module turns any of them into rows for the "Classes" list plus the GPA and rank lines.

It never guesses. Each row carries `issues`: a reason it may be wrong (two grades, a number
that could be credits or GPA points, a level read from the course name). The person reviews
every row before anything is saved, and rows with issues start unticked. Lines about the
student (name, birth date, ID, SSN, address) are dropped and never returned.

    python3 transcript.py FILE      # prints what it read, as JSON
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys

LEVELS = ["Regular", "Honors", "Advanced", "Pre-AP", "AP", "IB", "Dual enrollment", "College course", "Other"]
GRADE_LEVELS = {8: "8th grade", 9: "9th grade", 10: "10th grade", 11: "11th grade", 12: "12th grade"}
YEAR_WORDS = {"freshman": 9, "sophomore": 10, "junior": 11, "senior": 12}

# ------------------------------------------------------------------ tokens

LETTER = re.compile(r"^(?:A|B|C|D|E|F)[+-]?$")
PASS_FAIL = {"P", "PASS", "F", "FAIL", "CR", "NC", "S", "U", "I", "INC", "W", "WD", "WP", "WF", "IP", "NG", "AU", "E"}
SAFE_PF = {"PASS", "FAIL", "CR", "NC", "INC", "WD", "WP", "WF", "IP", "NG"}  # never part of a course name
IN_PROGRESS = {"IP", "INC", "NG", "*", "--", "-", "CURRENT", "INPROGRESS"}
NUM = re.compile(r"^\d{0,2}(?:\.\d{1,4})?$")
TERM = re.compile(r"^(?:S|SEM|SM|T|TRI|Q|QTR|MP)[- ]?\d$|^(?:FY|YR|YEAR|FALL|SPRING|SPR|SUMMER|SUM|WINTER|S1/S2|FULL)$", re.I)
SCHOOL_YEAR = re.compile(r"\b((?:19|20)\d{2})\s*[-/–]\s*((?:19|20)?\d{2})\b")
CODE_START = re.compile(r"^(?:\(?[A-Z]{0,6}[-.]?\d{2,7}[A-Z]{0,3}\)?|\d{3,7}[A-Z]{0,2}|[A-Z]{2,4}\s\d{3}[A-Z]?)\s+(?=\S)")
COLLEGE_CODE = re.compile(r"^[A-Z]{2,4}\s?\d{3}[A-Z]?\b")
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b|\bxxx-xx-\d{4}\b", re.I)

IGNORE = re.compile(
    r"\b(ssn|social security|s\.s\.n|date of birth|birth\s*date|birthdate|dob|student\s*(?:id|number|no|#)|state id|"
    r"address|zip|phone|e-?mail|parent|guardian|counsel(?:or|lor)|principal|registrar|signature|signed|"
    r"official|unofficial|page \d+|printed|issued|date issued|diploma|ceeb|school code|act code|attendance|absen|"
    r"tardies|immuniz|withdraw(?:al)? date|entry date|enroll(?:ment)? date|"
    r"test scores?|exams?|psat|sat|act|toefl|ielts|clep|key|legend|grading scale|grade scale|weighting|"
    r"this transcript|certify|certification of|accredit\w*|confidential|ferpa|notary|seal)\b"
    r"|^\s*(?:gender|sex|race|ethnicity|name|student|school|district|county|birth\w*)\s*:", re.I)
LABEL_VALUE = re.compile(r"^[A-Za-z][A-Za-z .'/#-]{1,25}:\s*\S{1,14}$")  # "Gender: F", "Status: Active"
# A student's own list: "3. Precalculus (DE-MAT 161) – In progress – Functions and …", "AP Chemistry-4"
LISTED = re.compile(r"^\s*(?:(\d{1,2})[.)]\s*)?(?P<name>[A-Za-z][^–—\n]{2,90}?)\s*(?:[–—]|\s-\s|-(?=\s*in[ -]?progress\b))\s*(?P<rest>.*)$", re.I)
LISTED_STATUS = re.compile(r"^(?:(in[ -]?progress|currently (?:taking|enrolled)|current)|(completed?|finished)|(planned|next year))\b", re.I)
EXAM_LINE = re.compile(r"^\s*(?:\d{1,2}[.)]\s*)?(?P<exam>(?:AP|IB)\b[^–—\n(]{2,60}?)\s*(?:\(\s*score\s*:?\s*(?P<s1>[1-7])\s*\)|[-–:]\s*(?P<s2>[1-7]))\s*(?:$|[–—-])", re.I)
TOTALS = re.compile(r"^\s*(?:sub)?totals?\b|^\s*(?:year|yearly|semester|term|grade)\s+totals?\b", re.I)
BARE_COURSE = re.compile(r"^(?=.*[A-Za-z]{3})[A-Za-z0-9&/().,' -]{3,60}$")  # a class name alone, under "In progress"
SUMMARY_WORDS = re.compile(r"\b(gpa|grade point|rank|class size|total credits|credits earned|cumulative credits)\b", re.I)

HEADER_WORDS = {
    "skip": r"verified(?:\s*credits?)?|v\.?\s*cr(?:edits?)?|sol\s*credits?|absences?",
    "name": r"course(?:\s*(?:title|name|description))?|title|description|class(?:\s*name)?|subject",
    "code": r"course\s*(?:no|num|number|#|code|id)|crs\s*(?:no|#)|code|number|no\.|id",
    "grade_level": r"grade\s*level|gr\s*lvl|gr\.?\s*lv|grd\s*lvl|gr|lvl|level\s*taken|grade\s*taken",
    "level": r"level|type|weight|wt|rigor|course\s*type|honors|weighting|attributes?|attr|flags?|designation",
    "grade": r"final(?:\s*grade|\s*mark)?|grade|mark|fin|grd|letter|ltr|fg|fm",
    "sem": r"s1|s2|sem\s*1|sem\s*2|semester\s*1|semester\s*2|q1|q2|q3|q4|t1|t2|t3|mp\d|tri\s*\d",
    "credits_att": r"att(?:empted)?|cr\s*att|credits?\s*att(?:empted)?",
    "credits": r"credits?(?:\s*earned)?|cr(?:ed)?(?:\s*earned)?|crd|units?|earned|ern|hrs|hours|credit\s*hours|potential\s*credits?|credits?\s*potential",
    "points": r"(?:quality|grade|gpa|weighted)?\s*points|pts|qp|gp|quality|q\.?\s*pts",
    "term": r"term|semester|sem|period|session",
    "year": r"(?:school\s*)?year|yr|sy",
}
HEADER_RE = {k: re.compile(rf"^(?:{v})$", re.I) for k, v in HEADER_WORDS.items()}


def _norm(text: str) -> str:
    text = text.replace(" ", " ").replace("–", "-").replace("—", "-").replace("−", "-")
    return text.replace("’", "'").replace("\r\n", "\n").replace("\r", "\n")


def _cells(line: str) -> list[str]:
    """Split a row into cells on tabs, pipes, or runs of two or more spaces."""
    if "\t" in line:
        parts = line.split("\t")
    elif "|" in line and line.count("|") >= 2:
        parts = line.split("|")
    else:
        parts = re.split(r" {2,}", line)
    return [p.strip() for p in parts if p.strip()]


def _is_grade(tok: str, strict: bool, after_number: bool = False) -> bool:
    """strict: single-spaced text, where "Algebra I" and "Spanish I" end in letters that aren't grades."""
    t = tok.upper().rstrip("*")
    if LETTER.match(t) or t in SAFE_PF:
        return True
    if strict:
        return t == "P" and after_number  # "Art I P 0.5"
    return t in PASS_FAIL


def _is_percent(tok: str) -> bool:
    return bool(re.fullmatch(r"(?:100|[5-9]\d)(?:\.\d{1,2})?", tok))


def _is_number(tok: str) -> bool:
    return bool(tok) and bool(NUM.match(tok)) and tok not in (".",) and float(tok) <= 12


# ------------------------------------------------------------------ levels and names

def level_from_name(name: str) -> tuple[str | None, str | None, str]:
    """(level, issue, name without the level marker). Markers are read only as whole words."""
    n = name
    if re.search(r"\bpre[- ]?ap\b", n, re.I):
        return "Pre-AP", None, n
    if re.search(r"\b(?:AP|A\.P\.)(?=\s|$|[^\w])|\bAdvanced Placement\b", n):
        return "AP", None, n
    if re.search(r"\bIB\b|\bInternational Baccalaureate\b", n):
        return "IB", None, n
    if re.search(r"(?:^|[\s(\[])(?:DE|D\.E\.|DC|Dual\s*(?:Enroll(?:ment|ed)?|Credit|Enr\.?))(?=$|[\s)\]:,-])|\bConcurrent Enrollment\b|\bCollege Credit\b", n, re.I):
        return "Dual enrollment", None, n
    if re.search(r"\bHonou?rs?\b|\bHon\.?(?=\s|$)|\bHNRS?\b|\bHNR\b|\(H\)|(?<=\d)H\b|\bH(?=\s*$)", n, re.I):
        return "Honors", None, n
    if re.search(r"\bAdv\.?(?=\s|$)|\bAdvanced\b|\bAccel(?:erated)?\b|\bGT\b|\bGifted\b|\bEnriched\b", n, re.I):
        return "Advanced", "“Advanced” may just be part of the class name. Check the level.", n
    return None, None, n


LEVEL_CELL = {"AP": "AP", "IB": "IB", "DE": "Dual enrollment", "DC": "Dual enrollment", "DUAL": "Dual enrollment",
              "H": "Honors", "HON": "Honors", "HONORS": "Honors", "HN": "Honors", "HNR": "Honors", "ADV": "Advanced",
              "ACC": "Advanced", "ACCEL": "Advanced", "GT": "Advanced", "PREAP": "Pre-AP", "PRE-AP": "Pre-AP",
              "REG": "Regular", "REGULAR": "Regular", "CP": "Regular", "STD": "Regular", "STANDARD": "Regular",
              "GEN": "Regular", "ACAD": "Regular", "COL": "College course", "COLLEGE": "College course"}

ATTR_PRIORITY = [("AP", "AP"), ("IB", "IB"), ("DE", "Dual enrollment"), ("DC", "Dual enrollment"), ("CC", "Dual enrollment"),
                 ("H", "Honors"), ("HON", "Honors"), ("PREAP", "Pre-AP"), ("A", "Advanced"), ("AC", "Advanced"), ("ADV", "Advanced"), ("GT", "Advanced")]


def level_code(cell: str) -> str | None:
    """A level/attributes cell: "H", "AP", "DE,H", "AP/H" (the strongest one wins: AP > IB > DE > Honors)."""
    t = cell.strip().upper()
    if t in LEVEL_CELL:
        return LEVEL_CELL[t]
    parts = [p for p in re.split(r"[,/;+ ]+", t) if p]
    if len(parts) < 2 or not all(p in dict(ATTR_PRIORITY) or p in LEVEL_CELL for p in parts):
        return None
    for code, level in ATTR_PRIORITY:
        if code in parts:
            return level
    return LEVEL_CELL.get(parts[0])


KEEP_CAPS = {"AP", "IB", "DE", "DC", "US", "U.S.", "USA", "PE", "CP", "II", "III", "IV", "VI", "VII", "STEM", "JROTC",
             "ROTC", "CTE", "ESL", "ELL", "ELA", "GT", "HL", "SL", "AB", "BC", "UK", "EU", "TV", "3D", "2D", "CAD", "HTML",
             "IT", "AI", "AV", "DNA", "CNA", "EMT", "ASL", "NJROTC", "AFJROTC", "VCCS", "AVID", "SAT", "ACT"}


def tidy_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip(" -:*|·•")
    name = re.sub(r"\s*\*+$", "", name)
    if re.search(r"[a-z]", name):  # already mixed case: keep it as the school wrote it
        return name
    out = []
    for w in name.split(" "):
        bare = w.strip("(),:/&")
        if bare.upper() in KEEP_CAPS or re.fullmatch(r"[IVX]+", bare) or re.search(r"\d", bare):
            out.append(w)
        elif "/" in w or "-" in w:
            out.append(re.sub(r"[A-Za-z]+", lambda m: m.group().upper() if m.group().upper() in KEEP_CAPS else m.group().capitalize(), w))
        else:
            out.append(w.capitalize() if len(w) > 2 or not w.isalpha() else w.lower() if w in ("OF", "TO", "IN", "AND") else w.capitalize())
    return " ".join(out).replace(" And ", " and ").replace(" Of ", " of ")


# ------------------------------------------------------------------ context lines

def grade_level_in(line: str) -> int | None:
    m = re.search(r"\b(?:grade(?:\s*level)?|gr\.?|grd)\s*[:#]?\s*(8|9|10|11|12)(?:th)?\b", line, re.I) \
        or re.search(r"\b(8|9|10|11|12)(?:th)?\s+grade\b", line, re.I)
    if m:
        return int(m.group(1))
    # "Senior Year" or a short "Junior" heading; not "Senior Research Seminar"
    m = re.search(r"\b(freshman|sophomore|junior|senior)\s+year\b", line, re.I) or (
        len(line.split()) <= 3 and re.search(r"\b(freshman|sophomore|junior|senior)\b", line, re.I))
    return YEAR_WORDS[m.group(1).lower()] if m else None


def school_year_in(line: str) -> str | None:
    m = SCHOOL_YEAR.search(line)
    if not m:
        return None
    a, b = m.group(1), m.group(2)
    b = b if len(b) == 4 else a[:2] + b
    if int(b) - int(a) != 1:
        return None
    return f"{a}-{b[2:]}"


def section_status(line: str) -> str | None:
    low = line.lower()
    if re.search(r"\b(in progress|current(?:ly enrolled)? courses|current schedule|currently enrolled|courses in progress|current year|senior schedule)\b", low):
        return "In progress"
    if re.search(r"\b(planned|proposed|scheduled|future|next year|requested) (courses|classes|schedule)\b|\bcourse requests\b", low):
        return "Planned"
    return None


def section_level(line: str) -> str | None:
    low = line.lower()
    if re.search(r"\b(dual enrollment|dual credit|college courses|concurrent enrollment|college credit)\b", low):
        return "Dual enrollment"
    return None


def cell_spans(line: str) -> list[tuple[int, int, str]]:
    """Cells split on tabs or 2+ spaces, with where each starts and ends on the line."""
    return [(m.start(), m.end(), m.group()) for m in re.finditer(r"[^\t ]+(?: [^\t ]+)*", line)]


def header_positions(line: str, kinds: list[str]) -> list[tuple[str, int, int]] | None:
    if "\t" in line or "|" in line or not re.search(r" {2,}", line):
        return None
    spans = cell_spans(line)
    return [(k, a, b) for k, (a, b, _t) in zip(kinds, spans)] if len(spans) == len(kinds) else None


def header_columns(line: str) -> list[str] | None:
    cells = _cells(line) if re.search(r"\t| {2,}|\|", line) else line.split()
    if len(cells) < 2:
        return None
    kinds = []
    for c in cells:
        c2 = re.sub(r"[^\w#. /]", "", c).strip()
        kind = next((k for k in ("skip", "code", "grade_level", "credits_att", "points", "sem", "credits", "grade", "level", "term", "year", "name")
                     if HEADER_RE[k].match(c2)), None)
        if kind is None:
            return None
        kinds.append(kind)
    if "name" not in kinds or not ({"grade", "credits", "points", "sem"} & set(kinds)):
        return None
    return kinds


# ------------------------------------------------------------------ one row

def _expected_points(grade: str) -> tuple[float, float] | None:
    """Unweighted points for a letter (A=4 … F=0, ±0.33) and the most a weighting could add."""
    m = re.fullmatch(r"([ABCDF])([+-]?)", grade.upper())
    if not m:
        return None
    base = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}[m.group(1)]
    base += {"+": 0.33, "-": -0.33, "": 0}[m.group(2)]
    return max(base - 0.34, 0), min(base, 4.34) + 1.5


def parse_positioned(line: str, ctx: dict, pos: list[tuple[str, int, int]]) -> dict | None:
    """A row of an aligned table: each cell belongs to the header column it sits under."""
    cells: dict[str, list[str]] = {}
    for a, b, text in cell_spans(line):
        best, best_score = None, None
        for kind, ha, hb in pos:
            overlap = min(b, hb) - max(a, ha)
            score = overlap if overlap > 0 else -min(abs(a - ha), abs(b - hb), abs((a + b) / 2 - (ha + hb) / 2))
            if best_score is None or score > best_score:
                best, best_score = kind, score
        if best_score is not None and best_score < -6:
            return None  # a cell far from every column: not a row of this table
        cells.setdefault(best, []).append(text)
    name = " ".join(cells.get("name", []))
    if not re.search(r"[A-Za-z]{3}", name):
        return None
    for kind in ("grade", "credits", "points", "credits_att", "grade_level"):
        if len(cells.get(kind, [])) > 1:
            return None
    vals = {k: v[0] for k, v in cells.items() if v and k not in ("name", "sem", "skip")}
    sems = cells.get("sem", [])
    if not any(vals.get(k) for k in ("grade", "credits", "points", "credits_att")) and not sems:
        return None
    for k in ("credits", "points", "credits_att"):
        if vals.get(k) and not _is_number(vals[k]):
            return None
    if vals.get("grade") and not (_is_grade(vals["grade"], False) or _is_percent(vals["grade"]) or vals["grade"].upper() in IN_PROGRESS):
        return None
    row = {"name": "", "level": None, "grade_level": None, "term": None, "grade": None, "credits": None,
           "gpa_points": None, "status": None, "issues": [], "line": line.strip()[:160]}
    code_m = CODE_START.match(name + " ")
    college_code = bool(COLLEGE_CODE.match(name)) or bool(cells.get("code") and COLLEGE_CODE.match(cells["code"][0]))
    if code_m and len(name) > code_m.end():
        name = name[code_m.end():]
    grade = vals.get("grade")
    real_sems = [g.upper() for g in sems if g.upper() not in IN_PROGRESS]
    if grade and grade.upper().rstrip("*") not in IN_PROGRESS:
        row["grade"] = grade.upper().rstrip("*") if not _is_percent(grade) else grade
        if sems:
            row["term_grades"] = [g.upper() for g in sems]
    elif sems and any(g.upper() in IN_PROGRESS for g in sems):
        row["term_grades"] = [g.upper() for g in sems]  # a semester still going: no final grade yet
    elif sems and "grade" not in [k for k, _a, _b in pos] and real_sems:
        row["grade"] = ", ".join(real_sems)
        if len(real_sems) > 1:
            row["issues"].append("Only semester grades; keep the one the form asks for.")
    if row["grade"] is None:
        row["status"] = "In progress"
        if real_sems:
            row["term_grades"] = [g.upper() for g in sems]
    if row["grade"] and row["grade"].upper() in ("W", "WD", "WP", "WF"):
        row["issues"].append("Withdrawn from this class.")
    if vals.get("credits"):
        row["credits"] = _num_text(vals["credits"])
    elif vals.get("credits_att"):
        row["credits"] = _num_text(vals["credits_att"])
    if vals.get("points"):
        row["gpa_points"] = _num_text(vals["points"])
    if vals.get("grade_level") and re.fullmatch(r"(?:8|9|10|11|12)(?:th)?", vals["grade_level"]):
        row["grade_level"] = GRADE_LEVELS[int(re.sub(r"\D", "", vals["grade_level"]))]
    if vals.get("term"):
        row["term"] = vals["term"]
    elif vals.get("year") and school_year_in(vals["year"]):
        row["term"] = school_year_in(vals["year"])
    lvl_cell = level_code(vals.get("level") or "")
    _finish_row(row, name, ctx, lvl_cell, college_code)
    return row


def _finish_row(row: dict, name: str, ctx: dict, level_cell: str | None, college_code: bool) -> None:
    issues = row["issues"]
    lvl, lvl_issue, _ = level_from_name(name)
    row["level"] = row["level"] or level_cell or lvl or ctx.get("level") or "Regular"
    if lvl_issue and not level_cell and not ctx.get("level") and row["level"] == "Advanced":
        issues.append(lvl_issue)
    if college_code and row["level"] == "Regular":
        issues.append("This has a college-style course code. If it's dual enrollment, change the level.")
    row["name"] = tidy_name(name)
    if row["grade_level"] is None and ctx.get("grade_level"):
        row["grade_level"] = GRADE_LEVELS[ctx["grade_level"]]
    if row["term"] is None and ctx.get("year"):
        row["term"] = ctx["year"]
    if row["status"] is None:
        row["status"] = "Completed" if row["grade"] else (ctx.get("status") or "In progress")
    if ctx.get("status") == "Planned" and not row["grade"]:
        row["status"] = "Planned"
    if row["grade_level"] is None:
        issues.append("Couldn't tell which grade you took this in.")


def parse_row(line: str, ctx: dict, columns: list[str] | None, pos=None) -> dict | None:
    """One course row, or None when the line isn't a course."""
    raw = line.strip()
    if not raw or not re.search(r"[A-Za-z]{2}", raw):
        return None
    if pos and re.search(r" {2,}", line):
        row = parse_positioned(line, ctx, pos)
        if row:
            return row
    cells = _cells(raw)
    strict = len(cells) < 2  # single-spaced text: "Algebra I" must not lose its "I"
    if len(cells) >= 2:
        tokens = cells
    else:
        tokens = raw.split(" ")
    # peel value tokens off the right end: numbers, then grades, then terms/years
    values: list[str] = []
    rest = list(tokens)
    phase = "numbers"
    while rest:
        t = rest[-1].strip()
        tu = t.upper().rstrip("*")
        if phase == "numbers" and (_is_number(t) and ("." in t or not strict) or t in ("-", "--")):  # "Spanish 3 1.0" keeps its 3
            values.insert(0, t); rest.pop(); continue
        if _is_percent(t) and phase in ("numbers", "grades"):
            values.insert(0, t); rest.pop(); phase = "grades"; continue
        if _is_grade(t, strict, after_number=bool(values)) or tu in IN_PROGRESS and tu not in ("-", "--"):
            if strict and any(_is_grade(v, False) or _is_percent(v) for v in values) and not (columns and columns.count("sem")):
                break  # one grade at most in single-spaced text: "AP Physics C A 1.0"
            if strict and tu == "C" and len(rest) > 1 and rest[-2].lower() == "physics":
                break  # "AP Physics C" is a class, not a C
            values.insert(0, t); rest.pop(); phase = "grades"; continue
        if not strict and (TERM.match(t) or SCHOOL_YEAR.fullmatch(t) or re.fullmatch(r"(?:8|9|10|11|12)(?:th)?", t) or level_code(t)):
            values.insert(0, t); rest.pop(); continue
        if strict and (phase == "grades" or values) and columns and "level" in columns and level_code(t) and len(rest) > 1:
            values.insert(0, t); rest.pop(); phase = "level"; continue  # the Attributes column: "Hon World Literature H A 1.00"
        if strict and "," in t and level_code(t):  # "DE,H" between the name and the grade
            values.insert(0, t); rest.pop(); continue
        if strict and TERM.match(t) and phase != "numbers":
            values.insert(0, t); rest.pop(); continue
        break
    # leading cells: "2023-24  10  English 10  A  1.0"
    lead: list[str] = []
    while len(rest) > 1 and (school_year_in(rest[0]) and SCHOOL_YEAR.fullmatch(rest[0].strip()) or re.fullmatch(r"(?:8|9|10|11|12)(?:th)?", rest[0])
                             or (not strict and TERM.match(rest[0]))):
        lead.append(rest.pop(0))
    if not rest:
        return None
    name = " ".join(rest).strip()
    code_m = CODE_START.match(name + " ")
    college_code = bool(COLLEGE_CODE.match(name))
    if code_m and len(name) > code_m.end():
        name = name[code_m.end():]
    if not re.search(r"[A-Za-z]{3}", name) or len(name) > 80:
        return None
    grades = [v for v in values if _is_grade(v, False) or _is_percent(v) or v.upper().rstrip("*") in IN_PROGRESS]
    numbers = [v for v in values if _is_number(v) and v not in grades]
    if not grades and not numbers:
        return None  # a heading or a sentence, not a course
    row = {"name": "", "level": None, "grade_level": None, "term": None, "grade": None, "credits": None,
           "gpa_points": None, "status": None, "issues": [], "line": raw[:160]}
    issues = row["issues"]
    for v in lead:
        if SCHOOL_YEAR.fullmatch(v.strip()) and school_year_in(v):
            row["term"] = school_year_in(v)
        elif re.fullmatch(r"(?:8|9|10|11|12)(?:th)?", v):
            row["grade_level"] = GRADE_LEVELS[int(re.sub(r"\D", "", v))]
        elif TERM.match(v):
            row["term"] = v
    for v in values:  # extra cells: terms, years, grade levels, levels
        if TERM.match(v):
            row["term"] = v
        elif SCHOOL_YEAR.fullmatch(v) and school_year_in(v):
            row["term"] = school_year_in(v)
        elif re.fullmatch(r"(?:8|9|10|11|12)(?:th)?", v) and v not in numbers:
            row["grade_level"] = GRADE_LEVELS[int(re.sub(r"\D", "", v))]
        elif level_code(v) and v not in grades:
            row["level"] = level_code(v)
    # grades
    real = [g for g in grades if g.upper().rstrip("*") not in IN_PROGRESS]
    pct = [g for g in real if _is_percent(g)]
    if len(real) == 2 and len(pct) == 1:  # "95  A": the letter is the grade, the percent rides along
        row["percent"] = pct[0]
        real = [g for g in real if g not in pct]
    if not real:
        row["status"] = "In progress" if grades or not numbers or ctx.get("status") != "Planned" else ctx.get("status")
    elif len(real) == 1:
        row["grade"] = real[0].rstrip("*").upper() if not _is_percent(real[0]) else real[0]
    else:
        sem_cols = [c for c in (columns or []) if c in ("sem", "grade")]
        if columns and len(sem_cols) == len(real) and columns.count("grade") == 1 and sem_cols[-1] == "grade":
            row["grade"] = real[-1].upper()  # S1, S2 … then the final grade
            row["term_grades"] = [g.upper() for g in real[:-1]]
        else:
            row["grade"] = ", ".join(g.upper() for g in real)
            issues.append("Two or more grades on this line (semesters?). Keep the one the form asks for.")
    if row["grade"] and row["grade"].upper() in ("W", "WD", "WP", "WF"):
        issues.append("Withdrawn from this class.")
    # numbers: credits and GPA points
    _assign_numbers(row, numbers, columns, issues)
    _finish_row(row, name, ctx, None, college_code)
    return row


def _assign_numbers(row: dict, numbers: list[str], columns: list[str] | None, issues: list) -> None:
    if not numbers:
        return
    nums = [float(n) for n in numbers]
    num_cols = [c for c in (columns or []) if c in ("credits_att", "credits", "points")]
    if num_cols and len(num_cols) == len(nums):
        for col, n, text in zip(num_cols, nums, numbers):
            if col == "credits":
                row["credits"] = _num_text(text)
            elif col == "points":
                row["gpa_points"] = _num_text(text)
        if "credits" not in num_cols and "credits_att" in num_cols:
            row["credits"] = _num_text(numbers[num_cols.index("credits_att")])
        return
    expected = _expected_points(row["grade"] or "")
    if len(nums) == 1:
        n = nums[0]
        if num_cols == ["credits"] or (not num_cols and 0 <= n <= 2 and expected is None):
            row["credits"] = _num_text(numbers[0])
        elif num_cols == ["points"]:
            row["gpa_points"] = _num_text(numbers[0])
        elif 0 <= n <= 2 and not (expected and expected[0] <= n <= expected[1] and n >= 1.5):  # 0.00 can't be an A's points
            row["credits"] = _num_text(numbers[0])
        else:
            issues.append(f"Couldn't tell whether {numbers[0]} is credits or GPA points.")
        return
    if len(nums) == 2:
        a, b = nums
        if a == b and a <= 2 and not (expected and expected[0] <= b <= expected[1]):
            row["credits"] = _num_text(numbers[1])  # attempted and earned
        elif a <= 2 and expected and expected[0] <= b <= expected[1]:
            row["credits"], row["gpa_points"] = _num_text(numbers[0]), _num_text(numbers[1])
            if a == b:
                issues.append(f"Read {numbers[0]} as credits and {numbers[1]} as GPA points. Check them.")
        elif a <= 2 and b <= 2 and expected is None:
            row["credits"] = _num_text(numbers[1])
            issues.append(f"Two numbers ({numbers[0]}, {numbers[1]}); used {numbers[1]} as credits earned.")
        else:
            issues.append(f"Couldn't tell what {numbers[0]} and {numbers[1]} are (credits, GPA points?).")
        return
    a, b, c = nums[-3:]
    if a == b and a <= 2 and (expected is None or expected[0] <= c <= expected[1]):
        row["credits"], row["gpa_points"] = _num_text(numbers[-2]), _num_text(numbers[-1])
    else:
        issues.append("Several numbers on this line; couldn't tell which are credits and GPA points.")


def _num_text(text: str) -> str:
    text = text.strip()
    if text.startswith("."):
        text = "0" + text
    if "." in text:
        text = text.rstrip("0").rstrip(".") if not re.fullmatch(r"\d+\.0+", text) else text.split(".")[0] + ".0"
    return text


# ------------------------------------------------------------------ GPA, rank, credits

def read_summary(line: str, out: dict) -> bool:
    low = line.lower()
    found = False
    if re.search(r"\b(term|semester|sem|quarter|annual|yearly|year|current term|mp)\s+gpa\b|\bgpa\s*\((?:term|sem)", low) \
            and not re.search(r"\bcum", low):
        return bool(re.search(r"\bgpa\b", low))  # a one-term GPA: not the one forms want
    # "GPA (District-Weighted with Honors points): 4.4000": the school's own name for it. Shown with that name;
    # never sorted into weighted/unweighted by a keyword ("Weighted-no added value" is really unweighted).
    for m in list(re.finditer(r"\b(?:cumulative\s+|cum\.?\s+|overall\s+)?gpa\s*\(([^)]{1,60})\)\s*:?\s*(\d\.\d{1,4})", low)):
        label = re.sub(r"\s+", " ", m.group(1)).strip()
        if label in ("w", "uw", "weighted", "unweighted", "wtd", "unwtd", "unw"):
            continue
        orig = line[m.start(1):m.end(1)] if len(line) == len(low) else label
        out.setdefault("gpa_other", []).append({"value": m.group(2), "label": re.sub(r"\s+", " ", orig).strip(), "line": line.strip()[:120]})
        low = low[:m.start()] + " " * (m.end() - m.start()) + low[m.end():]
        found = True
    for m in re.finditer(r"(?:\b(unweighted|unwtd|unw|uw|weighted|wtd|wgt|w)\b[\s:().-]*)?(?:cumulative\s+|cum\.?\s+|overall\s+|total\s+)?"
                         r"(?:\b(unweighted|unwtd|unw|uw|weighted|wtd|wgt|w)\b[\s:().-]*)?"
                         r"\b(?:gpa|grade point average)\b\s*(?:\(?\b(unweighted|unwtd|unw|uw|weighted|wtd|wgt|w)\b\)?)?\s*[:=-]?\s*"
                         r"(\d\.\d{1,4})(?:\s*(?:/|out of|on a)\s*(\d{1,2}(?:\.\d{1,2})?))?", low):
        if re.search(r"\b(term|semester|sem|quarter|qtr|annual|yearly|year|mp)\s*$", low[max(0, m.start() - 12):m.start()]):
            continue  # "Semester GPA 3.5": one term, not the cumulative GPA forms ask for
        kind = next((k for k in m.groups()[:3] if k), None)
        value, scale = m.group(4), m.group(5)
        scale = scale or (re.search(r"\b(\d(?:\.\d)?)\s*(?:-point\s*)?scale\b", low) or [None, None])[1]
        key = "gpa_unweighted" if kind and kind.startswith("u") else "gpa_weighted" if kind else "gpa_unlabeled"
        out.setdefault(key, []).append({"value": value, "scale": scale, "line": line.strip()[:120]})
        found = True
    # "GPA  W 4.21  UW 3.89" / "Weighted 4.21 Unweighted 3.89": every labeled number on a GPA line
    if re.search(r"\bgpa\b|grade point", low):
        taken = {h["value"] for k in ("gpa_unweighted", "gpa_weighted", "gpa_unlabeled") for h in out.get(k, []) if h["line"] == line.strip()[:120]}
        for kind, value in re.findall(r"\b(unweighted|unwtd|unw|uw|weighted|wtd|wgt|w)\b\s*[:=)]?\s*(\d\.\d{1,4})", low):
            if value in taken:
                continue
            key = "gpa_unweighted" if kind.startswith("u") else "gpa_weighted"
            out.setdefault(key, []).append({"value": value, "scale": None, "line": line.strip()[:120]})
            found = True
    m = re.search(r"\brank\b[^\d\n]{0,40}?(\d{1,4})\s*(?:/|of|out of|in (?:a )?(?:graduating )?class of)\s*(\d{1,5})\b", low)
    if m and int(m.group(1)) <= int(m.group(2)):
        out["class_rank"], out["class_size"] = m.group(1), m.group(2)
        found = True
    else:
        m = re.search(r"\bclass\s+rank\b\s*[:#]?\s*(\d{1,4})\b", low)
        if m:
            out["class_rank"] = m.group(1)
            found = True
    m = re.search(r"\bclass\s+size\b\s*[:#]?\s*(\d{1,5})\b", low)
    if m:
        out["class_size"] = m.group(1)
        found = True
    m = re.search(r"\b(?:total|cumulative|earned)\s+credits?(?:\s+earned)?\b\s*[:=]?\s*(\d{1,3}(?:\.\d{1,3})?)", low)
    if m:
        out["total_credits"] = m.group(1)
        found = True
    return found or bool(SUMMARY_WORDS.search(low) and re.search(r"\d", low) and not re.search(r"[a-z]{4,}\s+[a-f][+-]?\s", low))


def _finish_summary(raw: dict) -> dict:
    out, notes = {}, []
    for key in ("gpa_unweighted", "gpa_weighted", "gpa_unlabeled"):
        hits = raw.get(key) or []
        if not hits:
            continue
        values = {h["value"] for h in hits}
        last = hits[-1]  # transcripts repeat a running cumulative GPA; the last one is the latest
        out[key] = {"value": last["value"], "scale": last["scale"], "line": last["line"]}
        if len(values) > 1:
            notes.append(f"Found {len(values)} different {key.replace('gpa_', '').replace('unlabeled', '')} GPA lines; used the last one ({last['value']}).".replace("  ", " "))
    if raw.get("gpa_other"):
        seen, others = set(), []
        for h in raw["gpa_other"]:
            if (h["label"], h["value"]) not in seen:
                seen.add((h["label"], h["value"]))
                others.append({"value": h["value"], "label": h["label"]})
        out["gpa_other"] = others
    for key in ("class_rank", "class_size", "total_credits"):
        if raw.get(key):
            out[key] = raw[key]
    return {"values": out, "notes": notes}


# ------------------------------------------------------------------ whole transcript

def _rejoin_cell_lines(lines: list[str]) -> list[str]:
    """PDF viewers often copy a table one cell per line. Put each course back on one line."""
    def is_value(s):
        s = s.strip()
        return bool(s) and (_is_grade(s, False) or _is_percent(s) or _is_number(s) or TERM.match(s) or s.upper() in IN_PROGRESS
                            or bool(re.fullmatch(r"(?:8|9|10|11|12)", s)))
    nonempty = [l for l in lines if l.strip()]
    if not nonempty or sum(1 for l in nonempty if is_value(l)) < 0.3 * len(nonempty):
        return lines
    out: list[str] = []
    for l in lines:
        if is_value(l) and out and out[-1].strip() and not grade_level_in(out[-1]) and not header_columns(out[-1]):
            out[-1] = out[-1].rstrip() + "  " + l.strip()
        else:
            out.append(l)
    return out


def _split_columns(lines: list[str]) -> list[str]:
    """Two school years printed side by side: read the left column, then the right one."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        marks = [m for m in re.finditer(r"(?:\bgrade(?:\s*level)?\s*:?\s*(?:8|9|10|11|12)\b|\b(?:8|9|10|11|12)(?:th)?\s+grade\b|\b(?:freshman|sophomore|junior|senior)\b|"
                                        r"\b(?:19|20)\d{2}\s*-\s*(?:19|20)?\d{2}\b)", line, re.I)]
        starts = sorted({m.start() for m in marks})
        split_at = None
        for s in starts[1:]:
            if line[max(0, s - 3):s] != "   ":
                continue
            gl_l, gl_r = grade_level_in(line[:s]), grade_level_in(line[s:])
            sy_l, sy_r = school_year_in(line[:s]), school_year_in(line[s:])
            if (gl_l and gl_r and gl_l != gl_r) or (sy_l and sy_r and sy_l != sy_r):
                split_at = s
                break
        hdr = re.finditer(r"\bcourse\b", line, re.I)
        hdr_starts = [m.start() for m in hdr]
        if split_at is None and len(hdr_starts) == 2 and header_columns(line[:hdr_starts[1]]) and header_columns(line[hdr_starts[1]:]):
            split_at = hdr_starts[1]
        if split_at is None:
            out.append(line)
            i += 1
            continue
        left, right = [line[:split_at]], [line[split_at:]]
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if not nxt.strip():
                left.append(""); right.append(""); j += 1
                if j < len(lines) and not lines[j].strip():
                    break
                continue
            more = [m.start() for m in re.finditer(r"\bgrade\s*(?:8|9|10|11|12)\b|\b(?:19|20)\d{2}\s*-\s*\d{2,4}\b", nxt, re.I)]
            if len(more) == 1 and more[0] < split_at - 10 and len(nxt) < split_at:
                break  # a new single-column section
            cut = _gap_near(nxt, split_at)
            if cut is None:
                left.append(nxt)
            else:
                left.append(nxt[:cut]); right.append(nxt[cut:])
            j += 1
        out.extend(left + [""] + right)
        i = j
    return out


def _gap_near(line: str, at: int) -> int | None:
    if len(line) <= at - 4:
        return None
    best = None
    for m in re.finditer(r" {2,}", line):
        mid = m.end()
        if abs(mid - at) <= 10 and (best is None or abs(mid - at) < abs(best - at)):
            best = mid
    return best


def _parse_delimited(text: str) -> dict | None:
    sample = [l for l in text.splitlines() if l.strip()][:40]
    if len(sample) < 2:
        return None
    for delim in ("\t", ",", ";"):
        rows = list(csv.reader(io.StringIO("\n".join(sample)), delimiter=delim))
        widths = [len(r) for r in rows if len(r) > 1]
        if len(widths) < max(2, len(sample) // 2):
            continue
        for hi, header in enumerate(rows[:6]):
            kinds = header_columns("\t".join(header))
            if kinds and len(kinds) == len(header):
                return {"delim": delim, "header_index": hi, "kinds": kinds}
    return None


def parse(text: str) -> dict:
    """Everything readable in a transcript: {courses, summary, notes, unread, ignored}."""
    text = _norm(text or "")
    if len(text) > 200_000:
        text = text[:200_000]
    notes: list[str] = []
    summary_raw: dict = {}
    courses: list[dict] = []
    unread: list[str] = []
    exams: list[dict] = []
    ignored = 0
    ctx = {"grade_level": None, "year": None, "status": None, "level": None}
    delimited = _parse_delimited(text)
    if delimited:
        lines_iter = list(csv.reader(io.StringIO(text), delimiter=delimited["delim"]))
        kinds = delimited["kinds"]
        for row in lines_iter[delimited["header_index"] + 1:]:
            if not any(c.strip() for c in row):
                continue
            joined = " ".join(c.strip() for c in row if c.strip())
            if SSN.search(joined) or IGNORE.search(joined) and not re.search(r"\bcourse\b", joined, re.I):
                ignored += 1
                continue
            if read_summary(joined, summary_raw):
                continue
            course = _row_from_columns(row, kinds, ctx)
            if course:
                courses.append(course)
            elif grade_level_in(joined) or school_year_in(joined):
                ctx["grade_level"] = grade_level_in(joined) or ctx["grade_level"]
                ctx["year"] = school_year_in(joined) or ctx["year"]
            else:
                unread.append(joined[:160])
        fmt = "spreadsheet"
    else:
        lines = _split_columns(_rejoin_cell_lines(text.split("\n")))
        columns, pos = None, None
        fmt = "text"
        for line in lines:
            s = line.strip()
            if not s:
                continue
            if SSN.search(s) or re.search(r"\b[\w.+-]+@[\w-]+\.\w", s) or re.search(r"\(?\d{3}\)?[ .-]\d{3}-\d{4}", s):
                ignored += 1
                continue
            exam = parse_exam(s)
            if exam:
                exams.append(exam)
            listed = parse_listed(s, ctx) if (LISTED.match(s) and (exam or not re.search(r"\s(?:[A-F][+-]?|\d\.\d{1,3})\s*$", s))) else None
            if listed:
                courses.append(listed)
                continue
            if exam:
                continue
            if read_summary(s, summary_raw):
                continue
            cols = header_columns(s)
            if cols:
                columns, pos = cols, header_positions(line, cols)
                continue
            gl, sy, st, lv = grade_level_in(s), school_year_in(s), section_status(s), section_level(s)
            looks_header = (gl or sy or st or lv) and not re.search(r"\s(?:[A-F][+-]?|\d\.\d{1,3})\s*$", s)
            if looks_header and len(s) < 90:
                if gl or sy:
                    ctx["grade_level"] = gl if gl else (None if sy and sy != ctx["year"] else ctx["grade_level"])
                    ctx["year"] = sy or (None if gl and gl != ctx["grade_level"] else ctx["year"])
                    ctx["status"] = st
                    ctx["level"] = lv
                else:
                    ctx["status"] = st or ctx["status"]
                    ctx["level"] = lv or ctx["level"]
                continue
            if IGNORE.search(s) or LABEL_VALUE.match(s):
                ignored += 1
                continue
            if TOTALS.match(s):
                continue
            course = parse_row(line, ctx, columns, pos)
            if not course and ctx.get("status") in ("In progress", "Planned") and BARE_COURSE.match(s):
                course = {"name": tidy_name(s), "level": None, "grade_level": None, "term": None, "grade": None, "credits": None,
                          "gpa_points": None, "status": ctx["status"], "issues": [], "line": s[:160]}
                _finish_row(course, s, ctx, None, False)
            if course:
                courses.append(course)
            elif re.search(r"\s(?:[A-F][+-]?|\d\.\d{1,3}|(?:100|[5-9]\d))\s*$", s):
                unread.append(s[:160])
    # repeated pages print the same rows twice
    seen, kept = set(), []
    for c in courses:
        key = (c["name"].lower(), c["grade_level"], c["term"], c["grade"])
        if key in seen:
            continue
        seen.add(key)
        kept.append(c)
    if len(kept) < len(courses):
        notes.append(f"Dropped {len(courses) - len(kept)} repeated row(s).")
    for c in kept:
        c["ok"] = not c["issues"]
    summ = _finish_summary(summary_raw)
    notes += summ["notes"]
    if kept and not any(c["credits"] for c in kept):
        notes.append("No credits were found. Add them if the form asks.")
    kept_exams: list[dict] = []
    for e in exams:  # the same exam written twice: keep the fuller spelling ("AP Calculus A.B." over "AP Calculus AB")
        k = re.sub(r"[^a-z0-9]", "", e["exam"].lower())
        twin = next((x for x in kept_exams if re.sub(r"[^a-z0-9]", "", x["exam"].lower()) == k), None)
        if twin is None:
            kept_exams.append(e)
        elif len(e["exam"]) > len(twin["exam"]) and e["score"] == twin["score"]:
            twin["exam"] = e["exam"]
    return {"format": fmt, "courses": kept, "exams": kept_exams, "summary": summ["values"], "notes": notes,
            "unread": unread[:40], "ignored": ignored}


def _row_from_columns(row: list[str], kinds: list[str], ctx: dict) -> dict | None:
    cells = {}
    sems = []
    for kind, value in zip(kinds, row):
        value = value.strip()
        if not value:
            continue
        if kind == "sem":
            sems.append(value)
        else:
            cells.setdefault(kind, value)
    name = cells.get("name", "")
    if not re.search(r"[A-Za-z]{3}", name):
        return None
    out = {"name": tidy_name(name), "level": None, "grade_level": None, "term": None, "grade": None, "credits": None,
           "gpa_points": None, "status": None, "issues": [], "line": " | ".join(c.strip() for c in row)[:160]}
    grade = cells.get("grade")
    if grade and grade.upper().rstrip("*") in IN_PROGRESS:
        grade = None
    if grade:
        out["grade"] = grade.upper() if not _is_percent(grade) else grade
    elif sems:
        real = [s for s in sems if s.upper() not in IN_PROGRESS]
        if real:
            out["grade"] = ", ".join(s.upper() for s in real)
            if len(real) > 1:
                out["issues"].append("Only semester grades; keep the one the form asks for.")
    for kind, field in (("credits", "credits"), ("points", "gpa_points")):
        v = cells.get(kind)
        if v and re.fullmatch(r"\d{0,2}(?:\.\d{1,4})?", v) and v != ".":
            out[field] = _num_text(v)
    if not out["credits"] and cells.get("credits_att") and re.fullmatch(r"\d{0,2}(?:\.\d{1,4})?", cells["credits_att"]):
        out["credits"] = _num_text(cells["credits_att"])
    lvl_cell = level_code(cells.get("level") or "")
    lvl, lvl_issue, _ = level_from_name(name)
    out["level"] = lvl_cell or lvl or ctx.get("level") or "Regular"
    if lvl_issue and not lvl_cell:
        out["issues"].append(lvl_issue)
    gl = cells.get("grade_level")
    if gl and re.fullmatch(r"(?:8|9|10|11|12)(?:th)?", gl.strip()):
        out["grade_level"] = GRADE_LEVELS[int(re.sub(r"\D", "", gl))]
    elif ctx.get("grade_level"):
        out["grade_level"] = GRADE_LEVELS[ctx["grade_level"]]
    year = cells.get("year") and school_year_in(cells["year"])
    out["term"] = cells.get("term") or year or ctx.get("year")
    out["status"] = "Completed" if out["grade"] else "In progress"
    if out["grade_level"] is None:
        out["issues"].append("Couldn't tell which grade you took this in.")
    return out


def parse_listed(line: str, ctx: dict) -> dict | None:
    """A class from a list the student wrote. Kept only with a status word, or as a numbered item."""
    m = LISTED.match(line)
    if not m:
        return None
    name = re.sub(r"\s*\(\s*score\s*:?\s*\d\s*\)", "", m.group("name"), flags=re.I).strip(" .:-")
    st = LISTED_STATUS.match(m.group("rest").strip())
    if not st and not m.group(1):
        return None
    if not re.search(r"[A-Za-z]{3}", name) or IGNORE.search(name) or len(name.split()) > 12:
        return None
    status = "In progress" if st and st.group(1) else "Completed" if st and st.group(2) else "Planned" if st else None
    row = {"name": "", "level": None, "grade_level": None, "term": None, "grade": None, "credits": None,
           "gpa_points": None, "status": status, "issues": [], "line": line.strip()[:160]}
    gl = grade_level_in(m.group("rest"))
    if gl:
        row["grade_level"] = GRADE_LEVELS[gl]
    if status is None:
        row["issues"].append("No status written for this class: finished, or still in progress?")
        row["status"] = "Completed"
    _finish_row(row, name, {**ctx, "status": None}, None, False)
    return row


def parse_exam(line: str) -> dict | None:
    m = EXAM_LINE.match(line)
    if not m:
        return None
    exam = re.sub(r"\s+", " ", m.group("exam")).strip(" -:")
    exam = " ".join(w if w.isupper() or not w[:1].isalpha() else w[:1].upper() + w[1:] for w in exam.split(" "))
    return {"exam": exam, "score": m.group("s1") or m.group("s2")}


def to_list_item(course: dict) -> dict:
    """The fields the Classes list stores (drops parse details)."""
    keep = ("name", "level", "grade_level", "term", "grade", "credits", "gpa_points", "status")
    return {k: course[k] for k in keep if course.get(k) not in (None, "")}


def read_file(path: str) -> str:
    """Text from a .txt/.csv file, or from a PDF with pdftotext (poppler) or pypdf when installed."""
    if path == "-":
        return sys.stdin.read()
    if path.lower().endswith(".pdf"):
        import os
        import shutil
        import subprocess
        if shutil.which("pdftotext"):
            return subprocess.run(["pdftotext", "-layout", os.path.abspath(path), "-"], check=True, capture_output=True, text=True).stdout  # abspath: a name starting with "-" is never an option
        try:
            import pypdf
        except ImportError:
            raise SystemExit("Can't read PDFs here. Install poppler-utils (sudo apt install poppler-utils), "
                             "or open the PDF, copy all its text and paste it into a .txt file.") from None
        return "\n".join(page.extract_text(extraction_mode="layout") or "" for page in pypdf.PdfReader(path).pages)
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    print(json.dumps(parse(read_file(sys.argv[1])), indent=1, ensure_ascii=False))
