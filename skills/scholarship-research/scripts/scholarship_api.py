"""One-import interface for running the scholarship workflow from Hermes's execute_code tool.

Every function returns a JSON-serializable dict and reports failures as
{"ok": false, "error": ...} instead of raising, so the agent can print the
result and act on it:

    import json, sys
    sys.path.insert(0, "<skill dir>/scripts")
    import scholarship_api as api
    print(json.dumps(api.start_run(session_id="..."), indent=1))
"""

from __future__ import annotations

import functools
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import audit as page_audit  # noqa: E402
import pipeline  # noqa: E402
import scholarship_profile as profiles  # noqa: E402
import web_batch  # noqa: E402


def _safe(function: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return function(*args, **kwargs)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": str(exc)}

    return wrapper


def _path(value: str | Path) -> Path:
    return Path(value).expanduser()


@_safe
def start_run(
    session_id: str | None = None,
    persistent: bool = False,
    profile: str | None = None,
    student: str | None = None,
    resume: str | None = None,
) -> dict[str, Any]:
    """Create or resume a private run workspace.

    student="name" uses that student's saved profile; persistent=True alone uses the default
    saved profile; resume="run-..." reopens a workspace from list_runs().
    """
    return profiles.start_run(session_id, persistent=persistent, profile=profile, student=student, resume=resume)


@_safe
def list_profiles() -> dict[str, Any]:
    """Saved student profiles (school, class year, major) and anything in the trash."""
    return {"ok": True, "profiles": profiles.list_profiles(), "trash": profiles.list_trash()}


@_safe
def delete_profile(student: str, confirm: bool = False) -> dict[str, Any]:
    """Step 1 (confirm=False) returns the summary to show the user; step 2 moves it to the trash."""
    return profiles.delete_profile(student, confirm=confirm)


@_safe
def restore_profile(trash_file: str, student: str | None = None) -> dict[str, Any]:
    """Bring a profile back from the trash."""
    return profiles.restore_profile(trash_file, student)


@_safe
def empty_trash(trash_file: str) -> dict[str, Any]:
    """Permanently delete one trashed profile (only when the user asks)."""
    return profiles.empty_trash(trash_file)


@_safe
def list_runs() -> dict[str, Any]:
    """Earlier run workspaces, newest first; pass an entry's "resume" value to start_run."""
    return {"ok": True, "runs": profiles.list_runs()}


@_safe
def questionnaire(profile: str | Path) -> dict[str, Any]:
    """Every onboarding question as clarify-ready batches, with any stored values."""
    path = _path(profile)
    data = profiles.load_profile(path) if path.exists() else None
    return {"ok": True, **profiles.questionnaire(data)}


@_safe
def complete_onboarding(profile: str | Path, submission: dict[str, Any] | str) -> dict[str, Any]:
    """Apply {"responses": {field: answer}} for every question and report readiness."""
    return profiles.complete_onboarding(_path(profile), submission)


@_safe
def profile_summary(profile: str | Path) -> dict[str, Any]:
    """Redacted summary for confirming a saved profile with the user."""
    data = profiles.load_profile(_path(profile))
    return {
        "ok": True,
        "summary": profiles.profile_summary(data),
        "onboarding_complete": profiles.onboarding_complete(data),
    }


@_safe
def generate_queries(profile: str | Path, output: str | Path, max_queries: int = 60) -> dict[str, Any]:
    """Write the ordered query plan to output and summarize it."""
    if not 1 <= max_queries <= pipeline.MAX_QUERY_LIMIT:
        raise ValueError(f"max_queries must be between 1 and {pipeline.MAX_QUERY_LIMIT}")
    plan = pipeline.generate_queries(profiles.load_profile(_path(profile)), max_queries)
    pipeline.atomic_write(_path(output), json.dumps(plan, indent=2, ensure_ascii=False))
    return {
        "ok": True,
        "path": str(_path(output)),
        "query_count": len(plan["queries"]),
        "entry_year": plan["entry_year"],
        "warnings": plan["warnings"],
        "omitted_query_count": plan["omitted_query_count"],
        "next_action": "search_batch",
        "next_cursor": 0,
    }


@_safe
def search_batch(queries: str | Path, cursor: int = 0, **options: Any) -> dict[str, Any]:
    """Run one bounded search-and-extract batch through hermes_tools."""
    return web_batch.run_from_file(_path(queries), cursor, **options)


@_safe
def add_candidate(
    profile: str | Path,
    candidates: str | Path,
    record: dict[str, Any] | str,
    today: str | None = None,
) -> dict[str, Any]:
    """Validate one scholarship record and store it; invalid records are rejected with a reason."""
    return pipeline.add_candidate(
        _path(profile),
        _path(candidates),
        record,
        today=date.fromisoformat(today) if today else None,
    )


@_safe
def add_candidates(
    profile: str | Path,
    candidates: str | Path,
    records: list[dict[str, Any]] | str,
    today: str | None = None,
) -> dict[str, Any]:
    """add_candidate for a list of records: one verdict per record plus the stored count."""
    return pipeline.add_candidates(
        _path(profile),
        _path(candidates),
        records,
        today=date.fromisoformat(today) if today else None,
    )


@_safe
def remove_candidate(candidates: str | Path, name: str, sponsor: str | None = None) -> dict[str, Any]:
    """Delete a stored record by name, for example a lead replaced by a renamed official record."""
    return pipeline.remove_candidate(_path(candidates), name, sponsor)


@_safe
def audit(candidates: str | Path, fetch_missing: bool = True, time_budget: float = 240) -> dict[str, Any]:
    """Check every stored record against its own pages (no model): name, deadline, amount,
    fee/repayment/selection wording, time zone. Writes audit.json; evaluate uses it."""
    fetch = None
    if fetch_missing:
        try:
            from hermes_tools import web_extract as fetch
        except ImportError:
            fetch = None
    result = page_audit.audit_candidates(_path(candidates), fetch=fetch, time_budget=time_budget)
    if fetch_missing and fetch is None:
        result["warning"] = "Hermes web tools are unavailable here, so only pages saved by search_batch were checked."
    return result


@_safe
def evaluate(
    profile: str | Path,
    candidates: str | Path,
    output: str | Path,
    max_verification_age: int = 7,
    today: str | None = None,
    use_audit: bool = True,
) -> dict[str, Any]:
    """Run the deterministic evaluator (with audit.json findings when present) and write the
    evaluated JSON. The worklist says what still blocks "verified" and how to close it."""
    if not 0 <= max_verification_age <= 365:
        raise ValueError("max_verification_age must be between 0 and 365")
    candidate_path = _path(candidates)
    data = pipeline.load_json(candidate_path) if candidate_path.exists() else {"scholarships": []}
    evaluated = pipeline.evaluate_candidates(
        profiles.load_profile(_path(profile)),
        data,
        pipeline.Clock(date.fromisoformat(today) if today else None),
        max_verification_age,
        audit=page_audit.load_audit(candidate_path) if use_audit else None,
    )
    pipeline.atomic_write(_path(output), json.dumps(evaluated, indent=2, ensure_ascii=False))
    return {
        "ok": True,
        "path": str(_path(output)),
        "summary": evaluated["summary"],
        "invalid": [
            {"name": item["name"], "reason": item["validation_notes"][0]}
            for item in evaluated["invalid"]
        ],
        "worklist": pipeline.worklist(evaluated),
    }


@_safe
def score(evaluated: str | Path, gold: str | Path) -> dict[str, Any]:
    """Compare a run with a hand-checked gold list: recall and deadline/amount/source accuracy."""
    return pipeline.score_against_gold(pipeline.load_json(_path(evaluated)), pipeline.load_json(_path(gold)))


@_safe
def render(evaluated: str | Path, output: str | Path, include_excluded: bool = False) -> dict[str, Any]:
    """Render the Markdown report, write it, and return its text."""
    report = pipeline.render_report(pipeline.load_json(_path(evaluated)), include_excluded)
    pipeline.atomic_write(_path(output), report)
    return {"ok": True, "path": str(_path(output)), "markdown": report}


@_safe
def export(evaluated: str | Path, fmt: str, output: str | Path) -> dict[str, Any]:
    """Export results as an .ics deadline calendar (fmt='ics') or a spreadsheet (fmt='csv')."""
    return pipeline.write_export(pipeline.load_json(_path(evaluated)), fmt, _path(output))


BOARD_CONFIG = Path("~/.hermes/scholarship-board.json").expanduser()


@_safe
def send_to_board(workdir: str | Path, tracker: str | None = None, label: str | None = None,
                  wait: float = 60.0) -> dict[str, Any]:
    """Add this run's evaluated results to the scholarship board (apps/board), when one is set up.

    The board's install.sh writes ~/.hermes/scholarship-board.json with its inbox folder. This
    drops a request there; the board's inbox service imports the run and answers with a .done
    file. Each student gets one tracker, so a new run merges into it: statuses, notes, and
    checklist ticks are kept, and awards a run misses stay, marked.
    """
    run_dir = _path(workdir)
    if not BOARD_CONFIG.exists():
        return {"ok": False, "board": False, "error": f"no scholarship board is set up on this computer ({BOARD_CONFIG} is missing), so the board was not updated"}
    if not (run_dir / "evaluated.json").exists():
        return {"ok": False, "error": "this run has no evaluated.json yet: run evaluate first"}
    config = json.loads(BOARD_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not config.get("inbox"):
        return {"ok": False, "error": f"{BOARD_CONFIG} has no \"inbox\" folder; rerun the board's install.sh"}
    run_info = json.loads((run_dir / "run.json").read_text(encoding="utf-8")) if (run_dir / "run.json").exists() else {}
    student = str(run_info.get("student") or "")
    tid = tracker or "search-" + (re.sub(r"[^a-z0-9]+", "-", student.lower()).strip("-")[:40] or "default")
    name = label or (f"Scholarship search · {student}" if student else "Scholarship search")
    inbox = Path(str(config["inbox"])).expanduser()
    inbox.mkdir(parents=True, exist_ok=True)
    request = inbox / f"{tid}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    pipeline.atomic_write(request, json.dumps({"run": str(run_dir), "tracker": tid, "label": name}))
    answer = request.with_suffix(".done")
    give_up = time.monotonic() + wait
    while time.monotonic() < give_up:
        if answer.exists():
            result = json.loads(answer.read_text(encoding="utf-8"))
            answer.unlink()
            return {**result, "url": config.get("url")}
        time.sleep(0.5)
    return {"ok": False, "queued": True, "error": "the board didn't answer yet (is its inbox service running?); the results stay queued and are added when it runs"}


@_safe
def cleanup(workdir: str | Path) -> dict[str, Any]:
    """Delete a run workspace created by start_run (only its known files)."""
    return profiles.cleanup_run(_path(workdir))


__all__ = [
    "start_run",
    "list_profiles",
    "delete_profile",
    "restore_profile",
    "empty_trash",
    "list_runs",
    "questionnaire",
    "complete_onboarding",
    "profile_summary",
    "generate_queries",
    "search_batch",
    "add_candidate",
    "add_candidates",
    "remove_candidate",
    "audit",
    "evaluate",
    "score",
    "render",
    "export",
    "send_to_board",
    "cleanup",
]
