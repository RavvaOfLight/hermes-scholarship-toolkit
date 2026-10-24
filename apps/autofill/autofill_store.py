"""The saved answers the autofill companion types into application forms.

One owner-only JSON file holds them. Every value is checked against
extension/schema.json before it is written, so a typo like a 9-digit phone
number or an ACT score of 63 is refused instead of being filled later.
Each write keeps the previous version under data/history/.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import shutil
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

try:
    import fcntl
except ImportError:  # the portable extension runs this in the browser, where a Web Lock guards writes
    fcntl = None

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "extension" / "schema.json"
DATA_DIR = Path(os.environ.get("AUTOFILL_DATA_DIR", Path.home() / ".hermes" / "scholarship-fill" / "data"))
VERSION = 2
HISTORY_KEEP = 30

SENSITIVE_RE = re.compile(
    r"\b(social\s*security|ssn|ss\s*#|itin|tax\s*id|password|passcode|pin|security\s*code|cvv|cvc|"
    r"credit\s*card|debit\s*card|card\s*number|bank|routing|account\s*number|fsa\s*id|fafsa\s*id|"
    r"student\s*aid\s*id|driver'?s?\s*licen[cs]e|passport|alien|signature)\b", re.I)
SSN_SHAPE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '’.\-][^\W\d_]*)*\.?$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[A-Za-z]{2,}$")
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})


class ValidationError(ValueError):
    pass


RENAMED = {"household.income": "financial.family_income"}


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    schema = json.loads(path.read_text())
    schema["_items"] = {item["key"]: item for sec in schema["sections"] for item in sec["items"]}
    schema["_lists"] = {lst["id"]: lst for lst in schema["lists"]}
    schema["_states"] = {code: name for code, name in schema["states"]}
    return schema


SCHEMA = load_schema()


# ------------------------------------------------------------------ validation

def _text(value, limit=200) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    if len(text) > limit:
        raise ValidationError(f"Too long (over {limit} characters).")
    if SSN_SHAPE.search(text):
        raise ValidationError("That looks like a Social Security number. Don't save it here.")
    return text


def _date(text: str) -> str:
    text = text.strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", text)
        if not m:
            raise ValidationError("Use a date like 04/15/2009.")
        mo, d, y = int(m[1]), int(m[2]), int(m[3])
    if not 1900 <= y <= 2100:
        raise ValidationError("That year doesn't look right.")
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        raise ValidationError("That date doesn't exist.") from None


def _month(text: str) -> str:
    text = text.strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", text) or None
    if m:
        y, mo = int(m[1]), int(m[2])
    else:
        m = re.fullmatch(r"(\d{1,2})\s*[/.-]\s*(\d{4})", text)
        if m:
            mo, y = int(m[1]), int(m[2])
        else:
            m = re.fullmatch(r"([A-Za-z]+)\.?,?\s+(\d{4})", text)
            if not m or m[1].lower() not in MONTHS:
                raise ValidationError("Use a month and year like 05/2027 or May 2027.")
            mo, y = MONTHS[m[1].lower()], int(m[2])
    if not (1 <= mo <= 12 and 1900 <= y <= 2100):
        raise ValidationError("That month doesn't look right.")
    return f"{y:04d}-{mo:02d}"


def _number(text: str, spec: dict, integer: bool) -> str:
    text = re.sub(r"^\$|(?<=^-)\$", "", text.strip().replace(",", ""))  # "$52,000" and "-$1,500" are fine
    sign = "-?" if spec.get("min", 0) < 0 else ""
    pattern = sign + (r"\d+" if integer else r"(?:\d+(?:\.\d+)?|\.\d+)")
    if not re.fullmatch(pattern, text):
        raise ValidationError("Enter a whole number." if integer else "Enter a number like 3.85.")
    if not integer and "." in text and len(text.split(".")[1]) > spec.get("decimals", 2):
        text = text.rstrip("0").rstrip(".") if text.split(".")[1][spec.get("decimals", 2):].strip("0") == "" else text  # "4.4000" is 4.4
    if not integer and "." in text and len(text.split(".")[1]) > spec.get("decimals", 2):
        raise ValidationError(f"Use at most {spec.get('decimals', 2)} decimal places.")
    num = float(text)
    if "min" in spec and num < spec["min"] or "max" in spec and num > spec["max"]:
        raise ValidationError(f"Must be between {spec.get('min')} and {spec.get('max')}.")
    if integer:
        return str(int(text))
    return re.sub(r"^(-?)\.", r"\g<1>0.", text)


def clean_value(spec: dict, value) -> str | None:
    """Return the canonical text for a value, None to clear it, or raise ValidationError."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValidationError("Expected text.")
    raw = str(value)
    if not raw.strip():
        return None
    kind = spec["type"]
    if kind == "longtext":
        text = raw.replace("\r\n", "\n").strip()
        if len(text) > spec.get("maxlength", 1000):
            raise ValidationError(f"Too long (over {spec.get('maxlength', 1000)} characters).")
        if SSN_SHAPE.search(text):
            raise ValidationError("That looks like a Social Security number. Don't save it here.")
        return text
    text = _text(raw)
    if kind == "text":
        return text
    if kind == "name":
        if len(text) > 60 or not NAME_RE.match(text):
            raise ValidationError("Use letters only (spaces, hyphens and apostrophes are fine).")
        return text
    if kind == "email":
        if not EMAIL_RE.match(text) or len(text) > 120:
            raise ValidationError("That isn't a complete email address.")
        return text
    if kind == "phone":
        digits = re.sub(r"\D", "", text)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) != 10 or digits[0] in "01":
            raise ValidationError("Use a 10-digit U.S. phone number.")
        return digits
    if kind == "date":
        return _date(text)
    if kind == "month":
        return _month(text)
    if kind == "zip":
        if not re.fullmatch(r"\d{5}(-\d{4})?", text):
            raise ValidationError("Use a 5-digit ZIP code.")
        return text
    if kind == "ceeb":
        if not re.fullmatch(r"\d{6}", text):
            raise ValidationError("CEEB codes are 6 digits.")
        return text
    if kind == "state":
        states = SCHEMA["_states"]
        if text.upper() in states:
            return text.upper()
        by_name = {name.lower(): code for code, name in states.items()}
        if text.lower() in by_name:
            return by_name[text.lower()]
        raise ValidationError("Use a U.S. state, like KS or Kansas.")
    if kind in ("number", "integer"):
        return _number(text, spec, kind == "integer")
    if kind == "choice":
        for option in spec["options"]:
            if option.lower() == text.lower():
                return option
        raise ValidationError("Pick one of: " + ", ".join(spec["options"]) + ".")
    if kind == "yesno":
        low = text.lower()
        if low in ("yes", "y", "true"):
            return "Yes"
        if low in ("no", "n", "false"):
            return "No"
        raise ValidationError("Answer Yes or No.")
    raise ValidationError(f"Unknown field type {kind}.")


def clean_list_item(list_id: str, item: dict) -> dict:
    spec = SCHEMA["_lists"].get(list_id)
    if spec is None:
        raise ValidationError(f"Unknown list {list_id}.")
    if not isinstance(item, dict):
        raise ValidationError("Expected an object.")
    fields = {f["id"]: f for f in spec["fields"]}
    out, errors = {}, {}
    for key, value in item.items():
        if key not in fields:
            errors[key] = "Unknown field."
            continue
        try:
            cleaned = clean_value(fields[key], value)
        except ValidationError as err:
            errors[key] = str(err)
            continue
        if cleaned is not None:
            out[key] = cleaned
    if list_id == "custom":
        label = out.get("label", "")
        if not errors and (len(label) < 2 or "value" not in out):
            errors["label" if len(label) < 2 else "value"] = "Needs a question and an answer."
        if SENSITIVE_RE.search(label + " " + out.get("also", "")):
            errors["label"] = "That's private (SSN, passwords, bank or ID numbers). Type it yourself on the form."
    if errors:
        raise ValidationError(json.dumps(errors))
    if not out:
        raise ValidationError("Fill in at least one box.")
    return out


# ------------------------------------------------------------------ storage

def empty() -> dict:
    return {"_meta": {"version": VERSION, "rev": 0, "updated": None},
            "values": {}, "lists": {lst["id"]: [] for lst in SCHEMA["lists"]}}


@contextmanager
def locked(data_dir: Path | None = None):
    data_dir = Path(data_dir or DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(data_dir, 0o700)
    if fcntl is None:
        yield data_dir
        return
    with open(data_dir / ".lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield data_dir
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load(data_dir: Path | None = None) -> dict:
    data_dir = Path(data_dir or DATA_DIR)
    path = data_dir / "data.json"
    if not path.exists():
        return empty()
    data = json.loads(path.read_text())
    if data.get("_meta", {}).get("version") != VERSION:
        raise ValidationError(f"{path} is not a version {VERSION} data file; move it aside to start fresh")
    for old, new in RENAMED.items():  # items that moved to another section keep their saved value
        if old in data.get("values", {}) and new not in data["values"]:
            data["values"][new] = data["values"].pop(old)
    for lst in SCHEMA["lists"]:
        data.setdefault("lists", {}).setdefault(lst["id"], [])
    data.setdefault("values", {})
    return data


def _write(data: dict, data_dir: Path) -> dict:
    path = data_dir / "data.json"
    if path.exists():
        history = data_dir / "history"
        history.mkdir(exist_ok=True)
        os.chmod(history, 0o700)
        prev_rev = json.loads(path.read_text()).get("_meta", {}).get("rev", 0)
        shutil.copy2(path, history / f"data-rev{prev_rev:05d}.json")
        for old in sorted(history.glob("data-rev*.json"))[:-HISTORY_KEEP]:
            old.unlink()
    data["_meta"]["rev"] = int(data["_meta"].get("rev", 0)) + 1
    data["_meta"]["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as fh:
        os.chmod(tmp, 0o600)
        json.dump(data, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return data


# (value, the most it can be, why): checked together, so a typo like 4.5 on a 4.0 scale is refused
LIMITS = (("academics.gpa_unweighted", "academics.gpa_unweighted_scale", "Higher than the unweighted GPA scale."),
          ("academics.gpa_weighted", "academics.gpa_weighted_scale", "Higher than the weighted GPA scale."),
          ("academics.class_rank", "academics.class_size", "Class rank can't be larger than class size."))


def set_values(changes: dict, data_dir: Path | None = None) -> tuple[dict, dict]:
    """Apply {key: value}; empty values clear the key. Returns (data, errors). Invalid keys are skipped."""
    if not isinstance(changes, dict):
        raise ValidationError("Expected an object of changes.")
    with locked(data_dir) as d:
        data = load(d)
        before = dict(data["values"])
        errors = {}
        changed = False
        for key, value in changes.items():
            spec = SCHEMA["_items"].get(key)
            if spec is None:
                errors[key] = "Unknown item."
                continue
            try:
                cleaned = clean_value(spec, value)
            except ValidationError as err:
                errors[key] = str(err)
                continue
            if cleaned is None:
                changed |= data["values"].pop(key, None) is not None
            elif data["values"].get(key) != cleaned:
                data["values"][key] = cleaned
                changed = True
        values = data["values"]
        for low, high, why in LIMITS:
            if low in values and high in values and float(values[low]) > float(values[high]):
                for key in (low, high):
                    if key in changes:  # undo only what this request tried to change
                        errors[key] = why
                        if key in before:
                            values[key] = before[key]
                        else:
                            values.pop(key, None)
        changed = changed and values != before
        if changed:
            data = _write(data, d)
        return data, errors


def list_op(list_id: str, op: str, index: int | None = None, item: dict | None = None,
            to: int | None = None, data_dir: Path | None = None) -> dict:
    spec = SCHEMA["_lists"].get(list_id)
    if spec is None:
        raise ValidationError(f"Unknown list {list_id}.")
    with locked(data_dir) as d:
        data = load(d)
        rows = data["lists"][list_id]
        if op != "add" and not (isinstance(index, int) and 0 <= index < len(rows)):
            raise ValidationError("That row doesn't exist.")
        if op == "add":
            if len(rows) >= spec["max"]:
                raise ValidationError(f"You can save up to {spec['max']}.")
            rows.append(clean_list_item(list_id, item or {}))
        elif op == "update":
            merged = {**rows[index], **(item or {})}
            rows[index] = clean_list_item(list_id, {k: v for k, v in merged.items()})
        elif op == "remove":
            rows.pop(index)
        elif op == "move":
            if not (isinstance(to, int) and 0 <= to < len(rows)):
                raise ValidationError("Can't move it there.")
            rows.insert(to, rows.pop(index))
        else:
            raise ValidationError(f"Unknown list action {op}.")
        return _write(data, d)


def list_add_many(list_id: str, items: list, replace: bool = False, data_dir: Path | None = None) -> tuple[dict, dict]:
    """Add several rows at once (a pasted transcript). All or nothing: any bad row saves none.
    Returns (data, errors) with errors keyed by row number (0-based)."""
    spec = SCHEMA["_lists"].get(list_id)
    if spec is None:
        raise ValidationError(f"Unknown list {list_id}.")
    if not isinstance(items, list) or not items:
        raise ValidationError("Nothing to add.")
    cleaned, errors = [], {}
    for i, item in enumerate(items):
        try:
            cleaned.append(clean_list_item(list_id, item))
        except ValidationError as err:
            msg = str(err)
            try:
                errors[str(i)] = json.loads(msg)
            except json.JSONDecodeError:
                errors[str(i)] = {"_": msg}
    with locked(data_dir) as d:
        data = load(d)
        if errors:
            return data, errors
        rows = [] if replace else data["lists"][list_id]
        if len(rows) + len(cleaned) > spec["max"]:
            raise ValidationError(f"That would be more than {spec['max']} {spec['title'].lower()}.")
        data["lists"][list_id] = rows + cleaned
        return _write(data, d), {}


def import_data(incoming: dict, data_dir: Path | None = None) -> tuple[dict, dict]:
    """Replace everything with a backup file's answers. Every value and row is checked again,
    and one bad entry imports nothing. Returns (data, errors); the old answers stay in history."""
    meta = incoming.get("_meta") if isinstance(incoming, dict) else None
    if not isinstance(meta, dict) or meta.get("version") != VERSION:
        raise ValidationError("That isn't a Scholarship Autofill backup file.")
    values, lists = incoming.get("values") or {}, incoming.get("lists") or {}
    if not isinstance(values, dict) or not isinstance(lists, dict):
        raise ValidationError("That backup file is damaged.")
    values = {RENAMED.get(k, k): v for k, v in values.items()}
    fresh, errors = empty(), {}
    for key, value in values.items():
        spec = SCHEMA["_items"].get(key)
        try:
            if spec is None:
                raise ValidationError("Unknown item.")
            cleaned = clean_value(spec, value)
        except ValidationError as err:
            errors[key] = str(err)
            continue
        if cleaned is not None:
            fresh["values"][key] = cleaned
    for low, high, why in LIMITS:
        v = fresh["values"]
        if low in v and high in v and float(v[low]) > float(v[high]):
            errors[low] = why
    for list_id, rows in lists.items():
        spec = SCHEMA["_lists"].get(list_id)
        if spec is None or not isinstance(rows, list) or len(rows) > spec["max"]:
            errors[f"lists.{list_id}"] = "Unknown list." if spec is None else f"Up to {spec['max']} rows."
            continue
        for i, row in enumerate(rows):
            try:
                fresh["lists"][list_id].append(clean_list_item(list_id, row))
            except ValidationError as err:
                errors[f"lists.{list_id}.{i}"] = str(err)
    with locked(data_dir) as d:
        data = load(d)
        if errors:
            return data, errors
        fresh["_meta"]["rev"] = data["_meta"].get("rev", 0)
        return _write(fresh, d), {}


def append_log(entries: list, data_dir: Path | None = None) -> int:
    """Record what was filled where: site, box label, item key, outcome. Never the values."""
    allowed = {"site", "label", "item", "status"}
    with locked(data_dir) as d:
        path = d / "fills.jsonl"
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        count = 0
        with open(path, "a") as fh:
            os.chmod(path, 0o600)
            for entry in entries[:200]:
                if isinstance(entry, dict):
                    row = {k: str(v)[:160] for k, v in entry.items() if k in allowed}
                    row["ts"] = stamp
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    count += 1
        return count


def summary(data: dict) -> list[dict]:
    """Per-section counts without values, for chat answers that shouldn't repeat private data."""
    out = []
    for sec in SCHEMA["sections"]:
        keys = [item["key"] for item in sec["items"]]
        filled = [k for k in keys if k in data["values"]]
        out.append({"section": sec["title"], "saved": len(filled), "of": len(keys),
                    "missing": [SCHEMA["_items"][k]["label"] for k in keys if k not in data["values"]]})
    for lst in SCHEMA["lists"]:
        out.append({"section": lst["title"], "saved": len(data["lists"].get(lst["id"], [])), "of": lst["max"]})
    return out
