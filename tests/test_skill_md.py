"""Checks that keep SKILL.md loadable by Hermes and the skill clean under the hub scanner."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import scholarship_api
from conftest import SKILL_DIR

SKILL_MD = SKILL_DIR / "SKILL.md"
TEXT = SKILL_MD.read_text(encoding="utf-8")


def frontmatter() -> dict[str, str]:
    assert TEXT.startswith("---\n"), "frontmatter must start at byte 0"
    end = TEXT.index("\n---\n", 4)
    fields: dict[str, str] = {}
    for line in TEXT[4:end].splitlines():
        match = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if match:
            fields[match.group(1)] = match.group(2).strip()
    return fields


def body() -> str:
    return TEXT[TEXT.index("\n---\n", 4) + 5:]


def skill_files() -> list[Path]:
    return [
        path for path in SKILL_DIR.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and not path.name.startswith(".")
    ]


def test_frontmatter_follows_hermes_conventions():
    fields = frontmatter()
    assert fields["name"] == SKILL_DIR.name
    assert re.fullmatch(r"[a-z0-9-]{1,64}", fields["name"])
    description = fields["description"]
    assert len(description) <= 60, f"description is {len(description)} characters"
    assert description.endswith(".")
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?", fields["version"])
    assert fields["license"] == "PolyForm-Noncommercial-1.0.0"
    assert fields["platforms"] == "[linux, macos, windows]"
    assert fields["author"]


def test_metadata_requirements_name_real_toolsets():
    assert "requires_toolsets: [web, code_execution]" in TEXT
    assert "requires_tools: [web_search, web_extract, execute_code]" in TEXT


def test_size_and_sections():
    assert len(TEXT) <= 100_000
    assert len(body().splitlines()) <= 220
    for heading in ("## When to Use", "## Prerequisites", "## Procedure", "## Pitfalls", "## Verification"):
        assert heading in TEXT


def test_referenced_files_exist():
    for reference in set(re.findall(r"references/[A-Za-z0-9_.-]+", TEXT)):
        assert (SKILL_DIR / reference).is_file(), reference
    for script in set(re.findall(r"scripts/[A-Za-z0-9_.-]+\.py", TEXT)):
        assert (SKILL_DIR / script).is_file(), script


def test_api_calls_in_skill_md_exist():
    called = set(re.findall(r"\bapi\.([a-z_]+)\(", TEXT))
    assert called, "SKILL.md should show api calls"
    assert called <= set(scholarship_api.__all__)


def test_hermes_tool_names_are_current():
    # Hermes 0.21 tool names; "todo" and "cronjob" are only toolset names now.
    assert "`todo`" not in TEXT
    assert "`todo_list`" in TEXT and "`cronjob_manage`" in TEXT


def test_no_machine_local_paths():
    for path in skill_files():
        text = path.read_text(encoding="utf-8")
        assert "/home/" not in text and "/Users/" not in text, path
        assert not re.search(r"[A-Z]:\\Users\\", text), path


# A subset of the Hermes skills-hub scanner patterns (tools/skills_guard.py in
# hermes-agent). Keeping the skill clean of these avoids install warnings.
SCANNER_PATTERNS = {
    "prompt_injection_ignore": r"ignore\s+(?:\w+\s+)*(previous|all|above|prior)\s+instructions",
    "disregard_rules": r"disregard\s+(?:\w+\s+)*(your|all|any)\s+(?:\w+\s+)*(instructions|rules|guidelines)",
    "role_hijack": r"you\s+are\s+(?:\w+\s+)*now\s+",
    "role_pretend": r"pretend\s+(?:\w+\s+)*(you\s+are|to\s+be)\s+",
    "deception_hide": r"do\s+not\s+(?:\w+\s+)*tell\s+(?:\w+\s+)*the\s+user(?!.*\b(?:unless|except|until|confirm|diagnose|verify|check)\b)",
    "html_comment_injection": r"<!--[^>]*(?:ignore|override|system|secret|hidden)[^>]*-->",
    "shell_rc_mod": r"\.(bashrc|zshrc|profile|bash_profile|bash_login|zprofile|zlogin)\b",
    "persistence_cron": r"\bcrontab\b",
    "hex_encoded_string": r"\\x[0-9a-fA-F]{2}.*\\x[0-9a-fA-F]{2}.*\\x[0-9a-fA-F]{2}",
    "unicode_escape_chain": r"\\u[0-9a-fA-F]{4}.*\\u[0-9a-fA-F]{4}.*\\u[0-9a-fA-F]{4}",
    "python_os_environ": r"^[^#\n]*os\.environ\b(?!\s*\.get\s*\()",
    "python_subprocess": r"subprocess\.(run|call|Popen|check_output)\s*\(",
    "python_os_system": r"os\.system\s*\(",
    "python_rmtree": r"shutil\.rmtree\s*\(\s*[\"'/]",
    "hardcoded_ip_port": r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5}",
    "eval_string": r"\beval\s*\(\s*[\"']",
    "exec_string": r"\bexec\s*\(\s*[\"']",
    "send_to_url": r"(send|post|upload|transmit)\s+[^\n]{0,2048}\s+(to|at)\s+https?://",
}
INVISIBLE = {"​", "‌", "‍", "⁠", "⁢", "⁣", "⁤", "﻿",
             "‪", "‫", "‬", "‭", "‮", "⁦", "⁧", "⁨", "⁩"}


@pytest.mark.parametrize("pattern_id", sorted(SCANNER_PATTERNS))
def test_skill_files_avoid_scanner_patterns(pattern_id):
    pattern = re.compile(SCANNER_PATTERNS[pattern_id], re.IGNORECASE)
    hits = []
    for path in skill_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(SKILL_DIR)}:{number}: {line.strip()[:100]}")
    assert not hits, hits


def test_no_invisible_unicode():
    for path in skill_files():
        text = path.read_text(encoding="utf-8")
        assert not any(char in text for char in INVISIBLE), path


def test_skill_directory_shape():
    files = skill_files()
    assert len(files) <= 50
    assert all(path.stat().st_size <= 256 * 1024 for path in files)
    assert not any(path.suffix in {".pyc", ".exe", ".so", ".dll"} for path in files)
