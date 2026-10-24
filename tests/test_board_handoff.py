"""send_to_board + apps/board/inbox.py: a finished run reaches the board only when one is set up,
merges into the student's tracker on later runs, and says plainly when it didn't get there."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import scholarship_api as api

REPO = Path(__file__).resolve().parents[1]
BOARD = REPO / "apps" / "board"
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the board uses POSIX file locks, like its own tests")


@pytest.fixture
def run_dir(tmp_path):
    run = tmp_path / "run-x"
    run.mkdir()
    shutil.copy(BOARD / "tests" / "fixtures" / "evaluated.json", run / "evaluated.json")
    (run / "profile.json").write_text(json.dumps({"student": {"location": {"state": "Kansas"}}}))
    (run / "run.json").write_text(json.dumps({"student": None}))
    return run


@pytest.fixture
def board(tmp_path, monkeypatch):
    """A board data folder, the skill's config pointing at its inbox, and a stand-in for the
    systemd path unit that runs inbox.py whenever a request appears."""
    data = tmp_path / "board-data"
    inbox = data / "inbox"
    config = tmp_path / "scholarship-board.json"
    config.write_text(json.dumps({"inbox": str(inbox), "url": "http://127.0.0.1:8765"}))
    monkeypatch.setattr(api, "BOARD_CONFIG", config)
    stop = threading.Event()
    env = {**os.environ, "BOARD_DATA_DIR": str(data)}

    def service():
        while not stop.is_set():
            if inbox.is_dir() and any(inbox.glob("*.json")):
                subprocess.run([sys.executable, str(BOARD / "inbox.py")], env=env, check=True, capture_output=True)
            time.sleep(0.05)
    thread = threading.Thread(target=service, daemon=True)
    thread.start()
    yield data
    stop.set()
    thread.join(2)


def test_no_board_says_it_was_not_updated(run_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "BOARD_CONFIG", tmp_path / "missing.json")
    result = api.send_to_board(run_dir)
    assert result["ok"] is False and "not updated" in result["error"]


def test_run_without_results_is_refused(run_dir, board):
    (run_dir / "evaluated.json").unlink()
    assert "evaluate first" in api.send_to_board(run_dir)["error"]


def test_results_reach_the_board_and_a_second_run_merges(run_dir, board):
    first = api.send_to_board(run_dir, wait=20)
    assert first["ok"] is True and first["tracker"] == "search-default" and first["label"] == "Scholarship search"
    assert first["url"] == "http://127.0.0.1:8765"
    registry = json.loads((board / "trackers.json").read_text(encoding="utf-8"))
    assert [t["id"] for t in registry] == ["search-default"] and registry[0]["awards"] > 0
    store = board / "stores" / "search-default.json"
    rows = json.loads(store.read_text(encoding="utf-8"))
    rows["scholarships"][0]["status"] = "applied"  # the student's own mark
    store.write_text(json.dumps(rows))
    assert api.send_to_board(run_dir, wait=20)["ok"] is True
    assert json.loads(store.read_text(encoding="utf-8"))["scholarships"][0]["status"] == "applied"
    assert len(json.loads((board / "trackers.json").read_text(encoding="utf-8"))) == 1
    assert not list((board / "inbox").iterdir())  # requests and answers are cleaned up


def test_named_student_gets_their_own_tracker(run_dir, board):
    (run_dir / "run.json").write_text(json.dumps({"student": "Sam Lee"}))
    result = api.send_to_board(run_dir, wait=20)
    assert result["tracker"] == "search-sam-lee" and result["label"] == "Scholarship search · Sam Lee"


def test_board_not_running_leaves_the_request_queued(run_dir, tmp_path, monkeypatch):
    config = tmp_path / "cfg.json"
    config.write_text(json.dumps({"inbox": str(tmp_path / "inbox")}))
    monkeypatch.setattr(api, "BOARD_CONFIG", config)
    result = api.send_to_board(run_dir, wait=0.3)
    assert result["ok"] is False and result["queued"] is True
    assert len(list((tmp_path / "inbox").glob("*.json"))) == 1


def test_inbox_refuses_bad_requests(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.json").write_text("not json")
    (inbox / "b.json").write_text(json.dumps({"run": str(tmp_path), "tracker": "Bad Id!"}))
    (inbox / "c.json").write_text(json.dumps({"run": str(tmp_path / "nowhere"), "tracker": "ok-id"}))
    env = {**os.environ, "BOARD_DATA_DIR": str(tmp_path / "data"), "BOARD_INBOX": str(inbox)}
    subprocess.run([sys.executable, str(BOARD / "inbox.py")], env=env, check=True, capture_output=True)
    answers = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in inbox.glob("*.done")}
    assert "unreadable" in answers["a"]["error"]
    assert "tracker id" in answers["b"]["error"]
    assert "no evaluated.json" in answers["c"]["error"]
    assert not (tmp_path / "data" / "trackers.json").exists()
