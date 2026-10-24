"""python3 -m pytest tests/ — the saved-answers store and the local service."""
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autofill_server as srv  # noqa: E402
import autofill_store as store  # noqa: E402


def test_values_are_checked_and_normalized(tmp_path):
    data, errors = store.set_values({
        "student.first_name": "Ava", "student.phone": "(555) 555-0182", "student.state": "kansas",
        "student.date_of_birth": "4/15/2009", "school.graduation": "May 2027", "academics.act_composite": "32",
        "academics.gpa_unweighted": "3.92", "student.gender": "female", "household.first_generation": "n",
    }, tmp_path)
    assert errors == {}
    v = data["values"]
    assert v["student.phone"] == "5555550182" and v["student.state"] == "KS"
    assert v["student.date_of_birth"] == "2009-04-15" and v["school.graduation"] == "2027-05"
    assert v["student.gender"] == "Female" and v["household.first_generation"] == "No"
    _, errors = store.set_values({"academics.act_composite": "63", "student.phone": "555-0182", "student.email": "ava@",
                                  "student.first_name": "Ava2", "student.date_of_birth": "2009-02-30", "nope.key": "x"}, tmp_path)
    assert set(errors) == {"academics.act_composite", "student.phone", "student.email", "student.first_name", "student.date_of_birth", "nope.key"}
    assert store.load(tmp_path)["values"]["academics.act_composite"] == "32"  # bad values never overwrite good ones
    data, _ = store.set_values({"student.first_name": ""}, tmp_path)
    assert "student.first_name" not in data["values"]
    assert (tmp_path / "data.json").stat().st_mode & 0o777 == 0o600
    assert list((tmp_path / "history").glob("data-rev*.json"))


def test_lists_and_private_answers(tmp_path):
    store.list_op("activities", "add", item={"name": "Robotics Club", "hours_per_week": "6"}, data_dir=tmp_path)
    data = store.list_op("activities", "update", 0, {"role": "Captain"}, data_dir=tmp_path)
    assert data["lists"]["activities"][0] == {"name": "Robotics Club", "hours_per_week": "6", "role": "Captain"}
    with pytest.raises(store.ValidationError):
        store.list_op("custom", "add", item={"label": "SSN", "value": "123"}, data_dir=tmp_path)
    with pytest.raises(store.ValidationError):
        store.list_op("custom", "add", item={"label": "Student ID", "value": "123-45-6789"}, data_dir=tmp_path)
    store.list_op("custom", "add", item={"label": "Student ID", "value": "S123456"}, data_dir=tmp_path)
    assert store.list_op("activities", "remove", 0, data_dir=tmp_path)["lists"]["activities"] == []


def test_a_foreign_data_file_is_refused_not_overwritten(tmp_path):
    (tmp_path / "data.json").write_text(json.dumps({"_meta": {"version": 1}, "personal": {"first_name": "Ava"}}))
    with pytest.raises(store.ValidationError):
        store.load(tmp_path)
    with pytest.raises(store.ValidationError):
        store.set_values({"student.first_name": "Ava"}, data_dir=tmp_path)
    assert json.loads((tmp_path / "data.json").read_text())["personal"] == {"first_name": "Ava"}


def test_new_key_file_is_owner_only(tmp_path):
    import autofill_server

    cfg = autofill_server.load_config(tmp_path / "cfg" / "companion.json")
    assert len(cfg["token"]) >= 32 and cfg["port"] == 8781
    assert oct((tmp_path / "cfg" / "companion.json").stat().st_mode & 0o777) == "0o600"
    assert autofill_server.load_config(tmp_path / "cfg" / "companion.json") == cfg  # made once, then reused


def test_log_keeps_no_values(tmp_path):
    store.append_log([{"site": "example.org", "label": "First name", "item": "student.first_name", "status": "filled", "value": "Ava"}], tmp_path)
    row = json.loads((tmp_path / "fills.jsonl").read_text())
    assert "value" not in row and row["item"] == "student.first_name"


@pytest.fixture
def server(tmp_path):
    s = srv.make_server(0, "test-token", tmp_path)
    port = s.server_address[1]
    s.RequestHandlerClass.port = port
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield port
    s.shutdown()


def call(port, path, body=None, headers=None, host=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_service_needs_token_and_refuses_web_pages(server):
    tok = {"X-Autofill-Token": "test-token"}
    assert call(server, "/v1/health")[0] == 200
    assert call(server, "/v1/data")[0] == 401
    assert call(server, "/v1/data", headers={"X-Autofill-Token": "wrong"})[0] == 401
    assert call(server, "/v1/data", headers={**tok, "Origin": "https://evil.example"})[0] == 403
    assert call(server, "/v1/data", headers=tok, host="evil.example:80")[0] == 403
    status, body = call(server, "/v1/set", {"changes": {"student.first_name": "Ava", "academics.act_composite": "99"}},
                        headers={**tok, "Origin": "chrome-extension://abc"})
    assert status == 200 and body["data"]["values"] == {"student.first_name": "Ava"} and "academics.act_composite" in body["errors"]
    assert call(server, "/v1/data", headers=tok)[1]["data"]["values"]["student.first_name"] == "Ava"


def test_values_checked_against_their_limits(tmp_path):
    store.set_values({"academics.gpa_unweighted_scale": "4.0", "academics.class_size": "300"}, data_dir=tmp_path)
    data, errors = store.set_values({"academics.gpa_unweighted": "4.5", "academics.class_rank": "12"}, data_dir=tmp_path)
    assert "academics.gpa_unweighted" in errors and "academics.gpa_unweighted" not in data["values"]
    assert data["values"]["academics.class_rank"] == store.load(tmp_path)["values"]["academics.class_rank"]
    data, errors = store.set_values({"academics.class_size": "5"}, data_dir=tmp_path)
    assert "academics.class_size" in errors and store.load(tmp_path)["values"]["academics.class_size"] == data["values"]["academics.class_size"]
    assert float(data["values"]["academics.class_size"]) == 300


def test_service_reads_a_transcript_and_saves_many(server):
    tok = {"X-Autofill-Token": "test-token"}
    text = (Path(__file__).parent / "fixtures" / "transcripts" / "aligned.txt").read_text()
    status, body = call(server, "/v1/transcript", {"text": text}, headers=tok)
    assert status == 200 and len(body["result"]["courses"]) == 11 and "Testerson" not in json.dumps(body)
    keep = ("name", "level", "grade_level", "grade", "credits", "status")
    items = [{k: v for k, v in c.items() if k in keep and v} for c in body["result"]["courses"]]
    status, body = call(server, "/v1/list-many", {"list": "courses", "items": items}, headers=tok)
    assert body["ok"] and len(body["data"]["lists"]["courses"]) == 11
    assert call(server, "/v1/transcript", {"text": text})[0] == 401


def test_gpa_with_four_decimals_and_trailing_zeros(tmp_path):
    data, errors = store.set_values({"academics.gpa_weighted": "4.3817", "academics.gpa_unweighted": "4.0000"}, data_dir=tmp_path)
    assert errors == {} and data["values"]["academics.gpa_weighted"] == "4.3817"
    _, errors = store.set_values({"academics.gpa_weighted": "4.38171"}, data_dir=tmp_path)
    assert errors  # a fifth real decimal is still refused
    data, errors = store.set_values({"academics.gpa_unweighted_scale": "4.000"}, data_dir=tmp_path)  # 2 decimals allowed; zeros trimmed
    assert errors == {} and data["values"]["academics.gpa_unweighted_scale"] == "4"


def test_import_replaces_everything_or_nothing(tmp_path):
    store.set_values({"student.first_name": "Ava", "academics.act_composite": "31"}, tmp_path)
    backup = {"_meta": {"version": store.VERSION}, "values": {"student.first_name": "Ava", "household.income": "52000",
                                                             "academics.gpa_unweighted": "3.9", "academics.gpa_unweighted_scale": "4.0"},
              "lists": {"courses": [{"name": "AP Biology", "level": "AP"}]}}
    for broken in ({**backup, "values": {**backup["values"], "academics.act_composite": "63"}},
                   {**backup, "values": {**backup["values"], "academics.gpa_unweighted": "4.5"}},
                   {**backup, "lists": {"courses": [{"name": "AP Biology", "level": "Wizard"}]}},
                   {**backup, "lists": {"nope": []}}):
        data, errors = store.import_data(broken, tmp_path)
        assert errors and data["values"] == {"student.first_name": "Ava", "academics.act_composite": "31"}
    for not_a_backup in ({"values": {}}, {"_meta": None}, {"_meta": "2"}, [backup], "x", None):
        with pytest.raises(store.ValidationError):
            store.import_data(not_a_backup, tmp_path)
    data, errors = store.import_data(backup, tmp_path)
    assert errors == {} and "academics.act_composite" not in data["values"]
    assert data["values"]["financial.family_income"] == "52000"  # an older backup's renamed item still lands
    assert data["lists"]["courses"] == [{"name": "AP Biology", "level": "AP"}]
    assert store.load(tmp_path) == data and list((tmp_path / "history").glob("data-rev*.json"))


def test_api_answers_every_body_shape_without_crashing(tmp_path):
    from autofill_api import dispatch
    for path in ("/v1/set", "/v1/list", "/v1/list-many", "/v1/import", "/v1/transcript", "/v1/log", "/v1/nope"):
        for body in (None, {}, [], "x", 5, {"data": {"_meta": None}}, {"changes": None}, {"text": None}, {"entries": None}):
            status, reply = dispatch("POST", path, body, tmp_path)
            assert status in (200, 400, 404) and reply.get("ok") in (True, False), (path, body)
