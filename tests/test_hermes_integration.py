"""Runs Hermes's own skill scanner and linter when a Hermes checkout is available.

Set HERMES_AGENT_DIR to a hermes-agent source tree (default: ~/.hermes/hermes-agent).
Skipped in CI, where Hermes is not installed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from conftest import ROOT

HERMES_DIR = Path(os.environ.get("HERMES_AGENT_DIR", Path.home() / ".hermes" / "hermes-agent"))


@pytest.fixture(scope="module")
def hermes_tools_package():
    if not (HERMES_DIR / "tools" / "skills_guard.py").is_file():
        pytest.skip("hermes-agent source tree not found")
    # Hermes always ships PyYAML; without it the linter's fallback parser misreads lists.
    pytest.importorskip("yaml")
    sys.path.insert(0, str(HERMES_DIR))
    try:
        from tools import skill_linter, skills_guard
    except Exception as exc:  # noqa: BLE001 - any import problem means Hermes is unusable here
        pytest.skip(f"cannot import Hermes tools: {exc}")
    return skills_guard, skill_linter


SKILLS = sorted(p.parent for p in (ROOT / "skills").glob("*/SKILL.md"))


@pytest.mark.parametrize("skill_dir", SKILLS, ids=lambda p: p.name)
def test_hermes_scanner_reports_no_findings(hermes_tools_package, skill_dir):
    skills_guard, _ = hermes_tools_package
    result = skills_guard.scan_skill(skill_dir, source="community")
    assert result.verdict == "safe", skills_guard.format_scan_report(result)
    assert not result.findings, skills_guard.format_scan_report(result)


@pytest.mark.parametrize("skill_dir", SKILLS, ids=lambda p: p.name)
def test_hermes_linter_is_clean(hermes_tools_package, skill_dir):
    _, skill_linter = hermes_tools_package
    findings = skill_linter.lint_skill(skill_dir / "SKILL.md")
    assert not findings, findings
