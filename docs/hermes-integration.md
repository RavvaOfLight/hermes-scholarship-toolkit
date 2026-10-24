# How the skill uses Hermes Agent

Notes for maintainers on which Hermes Agent features the skill relies on, what each one
requires, and features that were considered and left out. Checked against Hermes Agent 0.21.

## Features the skill relies on

### `execute_code` and `hermes_tools`

Every deterministic step runs as one `execute_code` call that imports
`scripts/scholarship_api.py`. Python calls avoid shell quoting, heredocs, and the
`python3`/`py` difference between platforms, so one procedure works on Linux, macOS, and
Windows.

- `execute_code` runs in a persistent per-session kernel. The skill still re-runs
  `api.start_run(session_id=...)` in every call, because a timed-out call loses kernel
  state and `start_run` resumes the same workspace for the same session.
- One call allows at most 50 tool calls and 5 minutes. A search batch uses at most 5 queries
  × (1 search + 1 extract) calls and stops starting new queries after 200 seconds, returning
  the cursor where it stopped.
- Output over 50 KB is truncated. Batches use compact excerpts to stay below that, and every
  extracted page and excerpt is also written to the run workspace (`pages/`,
  `evidence.jsonl`), so a truncated print loses nothing: the agent re-reads the saved file.
- `api.audit` fetches pages not saved yet in chunks of 5 URLs (one `web_extract` call each)
  and stops starting new chunks after 240 seconds; it reports `not_reached_count` and asks to
  be called again, reusing the pages already saved.

### `web_search` and `web_extract`

- `web_search(query, limit)` returns `{"data": {"web": [{url, title, description}]}}`.
- `web_extract(urls, char_limit)` returns `{"results": [{url, title, content, error}]}` with
  Markdown content. Pages over `char_limit` (default 15,000) are cut to head and tail, and a
  footer gives the path of the full text under `~/.hermes/cache/web/`.
- The batch requests `char_limit=40000` so most pages arrive whole inside the sandbox, then
  sends the model only the passages that score highest for amount, deadline, eligibility, and
  application terms, always keeping the best fee/repayment and time-zone passages. The full-text path is passed on only when it matches the Hermes cache
  layout, so a page cannot point the agent at an arbitrary file.
- `web_extract` already blocks private-network URLs and URLs that carry secrets. The skill
  also validates every URL it stores and renders.

### `clarify`

Onboarding batches are shaped for `clarify`: at most 5 questions per call and 4 choices per
question (the UI adds an "Other" row). One-shot and scripted Hermes runs answer `clarify`
with "no user available; make the most reasonable assumption", so the skill tells the agent to
treat that reply as no answer and stop. Profile fields are never filled from an assumption.

### Skill loading

- `${HERMES_SKILL_DIR}` and `${HERMES_SESSION_ID}` are substituted when SKILL.md loads. When no
  session exists, the session placeholder stays literal; `start_run` then uses a fixed
  workspace name, so repeated calls still resume one workspace.
- The skill index shows about 57 characters of the description, so it is kept under 60.
- `requires_toolsets: [web, code_execution]` hides the skill when those toolsets are off.

### Hub publishing and scanning

- `hermes skills install owner/repo/path` downloads the whole skill directory except dotfiles
  and `__pycache__`, so tests live outside the skill directory.
- Taps list skill directories under `skills/` by default, which is why the skill lives at
  `skills/scholarship-research/`.
- Every hub install is scanned by `tools/skills_guard.py`. The scanner flags `.profile`
  attribute names (shell startup files), chains of `\x` escapes, and bare `os.environ` use, so
  the code avoids those patterns. `tests/test_skill_md.py` checks a copy of the relevant
  patterns, and `tests/test_hermes_integration.py` runs the real scanner and linter when a
  Hermes checkout is available. The current result is a safe verdict with no findings.

### Tool names in 0.21

The planning tool is `todo_list` and the scheduler tool is `cronjob_manage`; `todo` and
`cronjob` are toolset names. SKILL.md uses the tool names.

## Features used only on request

- `cronjob_manage`: deadline reminders, created only when the user asks. Prompts carry the
  scholarship name, deadline, and URL, never profile data.
- `browser_navigate` and `browser_snapshot`: a read-only fallback for official pages that
  `web_extract` cannot read. Logging in, submitting, filling forms, and downloading are not
  allowed.

## Considered and left out

- `delegate_task` (parallel verification by subagents): each subagent would need the
  student's profile, which widens its exposure, and single-slot local model servers run
  subagents one at a time anyway.
- `memory`: the skill forbids saving profile details or results there, because memory is
  injected into future, unrelated sessions.
- Blueprints (`metadata.hermes.blueprint`): a scheduled run needs an onboarded profile and
  interactive consent, which a cron job cannot collect. Individual deadline reminders cover
  the useful part.
- `skills.config` settings: the only setting, the saved-profile path, is already covered by
  the `SCHOLARSHIP_PROFILE` environment variable.
- Inline shell snippets in SKILL.md: off by default in Hermes, and not needed.
