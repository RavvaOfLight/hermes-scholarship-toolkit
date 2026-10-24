"""Offline tests for the board's HTTP rules (login, POST-only changes) and for import.py."""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from importlib import import_module
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import server  # noqa: E402

board_import = import_module("import")

EVALUATED = {
    "verified": [{
        "name": "Example Community Foundation Award", "sponsor": "Example Community Foundation",
        "deadline": {"date": "2099-03-01", "type": "fixed"}, "amount": {"display": "$2,500", "maximum": 2500},
        "status": "open", "match_reasons": ["county residents"], "application_tasks": ["Write a 500-word essay"],
        "official_url": "https://example.org/award", "verified_at": "2026-10-20"}],
    "needs_confirmation": [{
        "name": "Example Engineering Society Scholarship", "sponsor": "Example Society",
        "deadline": {"date": None, "type": "unknown"}, "amount": {"display": "$1,000"},
        "status": "not_open_yet", "eligibility_state": "needs_confirmation",
        "uncertainties": ["Fee wording not found."], "official_url": "https://example.org/society"}],
    "unverified": [{"name": "Database Lead Award", "official_url": "https://lead.example.com/x",
                    "validation_notes": ["Only seen on a database."]}],
}


@pytest.fixture
def data(tmp_path, monkeypatch):
    for module in (server, board_import):
        monkeypatch.setattr(module, "STORES", tmp_path / "stores")
        monkeypatch.setattr(module, "REGISTRY", tmp_path / "trackers.json")
        monkeypatch.setattr(module, "LOCK_FILE", tmp_path / "stores" / ".write.lock")
    monkeypatch.setattr(board_import, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "WATCH_LATEST", tmp_path / "watch-latest.json")
    monkeypatch.setattr(server, "AUTH", tmp_path / ".auth.json")
    run = tmp_path / "run-01"
    run.mkdir()
    (run / "evaluated.json").write_text(json.dumps(EVALUATED))
    (run / "profile.json").write_text(json.dumps({"student": {
        "location": {"state": "OH", "county": "Lakeside"}, "intended_major": "Nursing",
        "academics": {"gpa": "3.9"}}}))
    return tmp_path


def do_import(data, *args):
    monkey_argv = ["import.py", *args]
    old, sys.argv = sys.argv, monkey_argv
    try:
        return board_import.main()
    finally:
        sys.argv = old


def store(data, tid="fall"):
    return json.loads((data / "stores" / f"{tid}.json").read_text())


# ------------------------------------------------------------------ import.py

def test_run_import_keeps_buckets_and_no_private_profile_fields(data):
    assert do_import(data, "--run", str(data / "run-01"), "--id", "fall", "--label", "Fall") == 0
    doc = store(data)
    rows = {r["id"]: r for r in doc["scholarships"]}
    assert rows["example-community-foundation-award"]["verified"] is True
    assert rows["example-engineering-society-scholarship"]["deadline"] is None
    lead = rows["database-lead-award"]
    assert lead["lead"] and lead["url"] == "" and lead["leadUrl"].startswith("https://lead.")
    assert doc["profile"]["fields"] == [] and doc["profile"]["tags"] == ["OH", "Nursing"]
    assert "3.9" not in json.dumps(doc) and "Lakeside" not in json.dumps(doc)


def test_reimport_keeps_marks_and_records_changes(data):
    do_import(data, "--run", str(data / "run-01"), "--id", "fall")
    server.set_status({"tracker": "fall", "id": "example-community-foundation-award", "status": "applied"})
    server.add_note({"tracker": "fall", "id": "example-community-foundation-award", "note": "sent 10/20"})
    changed = json.loads(json.dumps(EVALUATED))
    changed["verified"][0]["deadline"]["date"] = "2099-03-15"
    del changed["unverified"]
    (data / "run-01" / "evaluated.json").write_text(json.dumps(changed))
    do_import(data, "--run", str(data / "run-01"), "--id", "fall")
    rows = {r["id"]: r for r in store(data)["scholarships"]}
    award = rows["example-community-foundation-award"]
    assert (award["status"], award["note"]) == ("applied", "sent 10/20")
    assert award["changes"][-1]["field"] == "deadline"
    assert rows["database-lead-award"]["missingSince"]  # kept, marked, not dropped


def test_bad_tracker_id_is_refused(data):
    with pytest.raises(SystemExit):
        do_import(data, "--run", str(data / "run-01"), "--id", "../escape")


def test_reset_clears_marks(data, monkeypatch):
    do_import(data, "--run", str(data / "run-01"), "--id", "fall")
    server.set_status({"tracker": "fall", "id": "database-lead-award", "status": "rejected"})
    reset = import_module("reset")
    monkeypatch.setattr(reset, "board_import", board_import)
    assert reset.main(["fall"]) == 0
    assert {r["status"] for r in store(data)["scholarships"]} == {"verify"}


# ------------------------------------------------------------------ HTTP rules

@pytest.fixture
def http(data):
    do_import(data, "--run", str(data / "run-01"), "--id", "fall")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def call(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def post(base, tool, args, **headers):
    return call(f"{base}/api/{tool}", "POST", json.dumps(args).encode(),
                {"Content-Type": "application/json", **headers})


def test_page_board_and_downloads(http):
    assert call(http + "/")[0] == 200
    status, body = call(http + "/api/board?tracker=fall")
    assert status == 200 and len(json.loads(body)["awards"]) == 3
    status, body = call(http + "/api/export.csv?tracker=fall")
    assert status == 200 and body.startswith("﻿Award,".encode())
    status, body = call(http + "/api/deadlines.ics?tracker=fall")
    assert status == 200 and body.count(b"BEGIN:VEVENT") == 1
    assert call(http + "/api/nope")[0] == 404


def test_changes_need_a_json_post_from_the_page(http, data):
    rid = "example-community-foundation-award"
    assert call(f"{http}/api/set_status?tracker=fall&id={rid}&status=applied")[0] == 405
    form = call(f"{http}/api/set_status", "POST", b"tracker=fall", {"Content-Type": "text/plain"})
    assert form[0] == 403
    assert post(http, "set_status", {"tracker": "fall", "id": rid, "status": "applied"},
                **{"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert post(http, "set_status", {"tracker": "fall", "id": rid, "status": "won"})[0] == 400
    assert post(http, "set_status", {"tracker": "fall", "id": rid, "status": "applied"},
                **{"Sec-Fetch-Site": "same-origin"})[0] == 200
    assert next(r for r in store(data)["scholarships"] if r["id"] == rid)["status"] == "applied"


def test_login_when_auth_file_exists(http, data):
    salt = b"0123456789abcdef"
    (data / ".auth.json").write_text(json.dumps({
        "user": "student", "salt": salt.hex(), "iterations": 1000,
        "hash": hashlib.pbkdf2_hmac("sha256", b"correct horse", salt, 1000).hex()}))
    assert call(http + "/api/board?tracker=fall")[0] == 401
    bad = "Basic " + base64.b64encode(b"student:wrong").decode()
    assert call(http + "/", headers={"Authorization": bad})[0] == 401
    good = "Basic " + base64.b64encode(b"student:correct horse").decode()
    assert call(http + "/api/board?tracker=fall", headers={"Authorization": good})[0] == 200
    assert call(http + "/api/board?tracker=fall", headers={"Authorization": good})[0] == 200  # cached path


def test_shared_board_without_a_password_is_refused(http, data):
    for header in ("X-Forwarded-For", "Tailscale-Funnel-Request"):
        status, body = call(http + "/", headers={header: "1"})
        assert status == 403 and b"set_password.py" in body
        assert post(http, "set_status", {"tracker": "fall", "id": "x", "status": "applied"}, **{header: "1"})[0] == 403
    assert call(http + "/")[0] == 200  # on this computer it still opens
    salt = b"0123456789abcdef"
    (data / ".auth.json").write_text(json.dumps({
        "user": "student", "salt": salt.hex(), "iterations": 1000,
        "hash": hashlib.pbkdf2_hmac("sha256", b"pw", salt, 1000).hex()}))
    good = "Basic " + base64.b64encode(b"student:pw").decode()
    assert call(http + "/", headers={"X-Forwarded-For": "203.0.113.5", "Authorization": good})[0] == 200


def test_arguments_that_are_not_text_are_refused_plainly(data):
    do_import(data, "--run", str(data / "run-01"), "--id", "fall")
    for args in ({"tracker": 5}, {"tracker": "fall", "id": ["x"]}, {"tracker": "fall", "id": "x", "status": True}):
        with pytest.raises(ValueError):
            server.set_status(args)


@pytest.mark.parametrize("text, expected", [
    ("Renewable awards; the program provides more than $38 million in renewable scholarships", 0),
    ("$2 million in scholarships awarded to 600 students", 0),
    ("Scholarships range from $1,000 to $60,000", 60000),
    ("Last-dollar award providing as much as $55,000 per year", 55000),
    ("$1,000-$3,000 ($4,000-$12,000 four-year value)", 12000),
])
def test_award_max_skips_program_totals(text, expected):
    assert server.award_max(text) == expected
