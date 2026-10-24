"""Offline tests for the board's checklist tools and the weekly page watch."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import server  # noqa: E402
import watch  # noqa: E402

TODAY = date(2026, 10, 26)


def row(rid, **kw):
    base = {"id": rid, "name": rid.replace("-", " ").title(), "sponsor": "Sponsor", "status": "verify",
            "deadline": None, "deadlineLabel": "Not announced", "appStatus": "not_open_yet", "amount": "$1,000",
            "tasks": [], "url": f"https://example.org/{rid}", "note": ""}
    base.update(kw)
    return base


@pytest.fixture
def home(tmp_path, monkeypatch):
    stores = tmp_path / "stores"
    stores.mkdir()
    rows = [
        row("alpha", tasks=["Submit two letters of recommendation", "Write a 500-word essay", "Send the official transcript"]),
        row("beta", deadline="2026-12-01", deadlineLabel="2026-12-01", appStatus="open",
            tasks=["Submit the FAFSA", "Request a recommendation from a teacher"]),
        row("gamma", status="rejected"),
        row("delta", lead=True, url="", leadUrl="https://example.org/delta-lead"),
    ]
    (stores / "main.json").write_text(json.dumps({"scholarships": rows, "log": [], "imported": "2026-10-23"}))
    registry = tmp_path / "trackers.json"
    registry.write_text(json.dumps([{"id": "main", "label": "Main", "source": "report.pdf", "awards": 4, "dated": 1}]))
    for module in (server, watch):
        monkeypatch.setattr(module, "STORES", stores)
        monkeypatch.setattr(module, "REGISTRY", registry)
        monkeypatch.setattr(module, "LOCK_FILE", stores / ".write.lock")
    monkeypatch.setattr(server, "WATCH_LATEST", tmp_path / "watch-latest.json")
    monkeypatch.setattr(watch, "LATEST", tmp_path / "watch-latest.json")
    monkeypatch.setattr(watch, "STATE", tmp_path / "watch-state.json")
    return tmp_path


def store(home):
    return json.loads((home / "stores" / "main.json").read_text())


# ------------------------------------------------------------------ checklists

def test_checklist_items_have_stable_keys_and_categories(home):
    award = next(a for a in server.board({"tracker": "main"})["awards"] if a["id"] == "alpha")
    cats = {item["text"]: item["cats"] for item in award["checklist"]}
    assert cats["Submit two letters of recommendation"] == ["rec"]
    assert cats["Write a 500-word essay"] == ["essay"]
    assert cats["Send the official transcript"] == ["transcript"]
    assert len({item["key"] for item in award["checklist"]}) == 3
    assert server.task_key("Write a 500-word  essay") == server.task_key("write a 500-word essay")


def test_toggle_add_and_remove_steps(home):
    award = next(a for a in server.board({"tracker": "main"})["awards"] if a["id"] == "alpha")
    key = award["checklist"][0]["key"]
    assert server.toggle_task({"tracker": "main", "id": "alpha", "key": key, "done": True})["done"] is True
    saved = next(r for r in store(home)["scholarships"] if r["id"] == "alpha")
    assert key in saved["taskDone"]
    server.toggle_task({"tracker": "main", "id": "alpha", "key": key, "done": False})
    assert key not in next(r for r in store(home)["scholarships"] if r["id"] == "alpha")["taskDone"]
    added = server.add_task({"tracker": "main", "id": "alpha", "text": "Ask Ms. Lee for a letter"})
    award = next(a for a in server.board({"tracker": "main"})["awards"] if a["id"] == "alpha")
    custom = [i for i in award["checklist"] if i["custom"]]
    assert custom[0]["text"] == "Ask Ms. Lee for a letter" and custom[0]["cats"] == []
    server.toggle_task({"tracker": "main", "id": "alpha", "key": added["key"], "done": True})
    server.remove_task({"tracker": "main", "id": "alpha", "key": added["key"]})
    saved = next(r for r in store(home)["scholarships"] if r["id"] == "alpha")
    assert saved["customTasks"] == [] and added["key"] not in saved["taskDone"]
    assert [entry["text"][:2] for entry in store(home)["log"]][0] == "✓ "


def test_step_validation(home):
    with pytest.raises(ValueError):
        server.toggle_task({"tracker": "main", "id": "alpha", "key": "nope", "done": True})
    with pytest.raises(ValueError):
        server.add_task({"tracker": "main", "id": "alpha", "text": "x"})
    report_key = server.checklist(next(r for r in store(home)["scholarships"] if r["id"] == "alpha"))[0]["key"]
    with pytest.raises(ValueError):
        server.remove_task({"tracker": "main", "id": "alpha", "key": report_key})


# ------------------------------------------------------------------- page watch

PAGE_V1 = """# Alpha Scholarship
The Alpha Scholarship awards $1,000.
Applications open January 5, 2027 and are due March 1, 2027.
© 2026 Example Foundation · Last updated October 1, 2026
"""
PAGE_V2 = PAGE_V1.replace("due March 1, 2027", "due March 15, 2027").replace("October 1", "October 20")


def test_find_dates_reads_kinds_and_never_invents_a_year():
    found = watch.find_dates(PAGE_V1, TODAY)
    assert {"kind": "opens", "date": "2027-01-05"} in [{k: f[k] for k in ("kind", "date")} for f in found]
    assert {"kind": "deadline", "date": "2027-03-01"} in [{k: f[k] for k in ("kind", "date")} for f in found]
    no_year = watch.find_dates("Applications are due February 2.", TODAY)
    assert no_year[0]["date"] == "February 2 (no year on the page)"
    assert watch.find_dates("Our 2025 winners were announced May 3, 2025.", TODAY) == []


def test_watch_targets_waiting_awards_and_detects_changes(home):
    pages = {"https://example.org/alpha": PAGE_V1, "https://example.org/delta-lead": "Delta lead page. Apply now!"}
    fetched = []

    def fetch(urls):
        fetched.append(sorted(urls))
        return {u: pages[u] for u in urls if u in pages}

    first = watch.run_watch(fetch, today=TODAY)
    assert fetched[0] == ["https://example.org/alpha", "https://example.org/delta-lead"]  # beta has a date, gamma rejected
    assert len(fetched) == watch.PASSES
    assert first["awards_watched"] == 2 and first["pages_watched"] == 2 and first["changed"] == []
    alpha = next(r for r in store(home)["scholarships"] if r["id"] == "alpha")
    assert alpha["watch"]["status"] == "baseline" and alpha["watch"]["found"]
    delta = next(r for r in store(home)["scholarships"] if r["id"] == "delta")
    assert "Apply now" in delta["watch"]["openSignal"]

    learning = watch.run_watch(lambda urls: {u: pages[u] for u in urls if u in pages}, today=TODAY)
    assert learning["changed"] == []  # the second check still only learns the page
    flaky = watch.run_watch(lambda urls: {u: "Short excerpt with no dates." for u in urls}, today=date(2026, 10, 27))
    assert flaky["changed"] == []  # facts missing from a thinner fetch are not a change
    assert next(r for r in store(home)["scholarships"] if r["id"] == "alpha")["watch"]["found"]  # still shown

    pages["https://example.org/alpha"] = PAGE_V2
    pages.pop("https://example.org/delta-lead")
    second = watch.run_watch(fetch, today=date(2026, 11, 2))
    alpha = next(r for r in store(home)["scholarships"] if r["id"] == "alpha")
    assert alpha["watch"]["status"] == "changed" and alpha["watch"]["changedOn"] == "2026-11-02"
    assert [c["name"] for c in second["changed"]] == ["Alpha"]
    assert second["unreadable"] == ["Delta"]
    assert any("page watch · Alpha: page updated" in entry["text"] for entry in store(home)["log"])
    assert server.board({"tracker": "main"})["watch"]["changed"][0]["name"] == "Alpha"

    third = watch.run_watch(fetch, today=date(2026, 11, 9))
    alpha = next(r for r in store(home)["scholarships"] if r["id"] == "alpha")
    assert alpha["watch"]["status"] == "same" and alpha["watch"]["changedOn"] == "2026-11-02"
    assert third["changed"] == []


def test_dry_run_writes_nothing(home):
    before = (home / "stores" / "main.json").read_text()
    watch.run_watch(lambda urls: {u: PAGE_V1 for u in urls}, today=TODAY, dry_run=True)
    assert (home / "stores" / "main.json").read_text() == before
    assert not (home / "watch-state.json").exists()


def test_same_date_with_and_without_year_is_listed_once():
    text = "Applications open November 1, 2026. Deadline: February 1, 2027. Portal opens November 1."
    dates = [f["date"] for f in watch.find_dates(text, TODAY)]
    assert dates == ["2026-11-01", "2027-02-01"]
