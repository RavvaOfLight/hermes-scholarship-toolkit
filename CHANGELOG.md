# Changelog

## Toolkit 1.2.0 (2026-10-24)

License: changed from MIT to the **PolyForm Noncommercial License 1.0.0**, created by Preston
Campbell. Free for any non-commercial use; copies and changed versions must keep the
Required Notice naming the author.

Autofill (`apps/autofill`):

- **Portable copy for Chromebooks** (`portable/build.py`): an extension folder that needs no
  service. The same store and transcript reader run in the browser with Pyodide 0.26.4 (pinned
  by SHA-256), and answers are kept in the extension's storage. `--seed` adds one person's
  answers, locked with a passphrase. Includes "How to install.html" for ChromeOS, Chrome and Edge.
- **Back up or move your answers** in My info: download a backup file, or restore one. Every
  value is checked again, and one bad entry restores nothing (`/v1/import`, `store.import_data`).
- The service's request handling moved to `autofill_api.py`, shared with the portable copy.
- `PORTABLE=1 node tests/e2e.mjs` runs the end-to-end test against a portable build, including
  the passphrase unlock.

Fixes from the release check:

- **Search runs now reach the board.** The research skill had no hand-off, so a finished run
  never appeared there (and the agent called the report files "the dashboard"). New
  `api.send_to_board(workdir)` (skill step 6.6) drops a request in the board's inbox;
  `apps/board/inbox.py`, started by the new `scholarship-board-inbox.path` unit, imports it into
  the student's tracker and answers. The skill says the board was updated only when it was.
  The skill itself still runs no programs, so it passes Hermes's skill scanner.
- Skill instructions learned in use: match a resumed run to the right student and refresh a
  stale run profile; re-queue rate-limited extracts; map audit ids to names; copy a sponsor's
  list of eligible places exactly and test place rules on county or city when the school
  district is blank.
- Board: with no password set, it now refuses requests that come through Tailscale Serve or
  Funnel (or any proxy) and says to run `set_password.py`, so sharing it early can't expose it.
  On the computer itself it still opens without a password.
- Autofill: a PDF whose name starts with `-` is passed to `pdftotext` as a full path, never as an
  option.
- Restoring a damaged backup (for example `"_meta": null`) now says it isn't a backup file
  instead of failing with a Python error; `autofill_api.dispatch` answers 400 to a request
  body that isn't a JSON object.
- Board: a tracker, award or status that isn't text gets a 400 with the reason, not a 500.
- Board: a figure of a million dollars or more, or one followed by "in renewable scholarships",
  is read as a program total, never as one student's award.
- Both services answer a malformed `Content-Length` header with an error instead of dropping
  the connection.
- Test fixtures and examples are all invented (new transcript layouts, class lists and
  test-state values); the browser tests find Chromium by themselves.

## Toolkit 1.1.0 (2026-10-24)

Autofill (`apps/autofill`, skill `scholarship-form-fill` 0.3.0):

- **Financial aid** section: financial need, applying for aid, FAFSA submitted and date, SAI
  (negative values allowed), Pell eligibility, CSS Profile, total family income (moved from
  Household; saved values carry over), parents' AGI, your own income, unmet need, and free or
  reduced-price lunch. "EFC" boxes map to the SAI, unticked, with a note.
- **Classes** list (level, grade taken, term, grade, credits, GPA points, status), filled from
  a pasted transcript by `transcript.py`. It handles aligned tables, PowerSchool-style codes and
  semester grades, two school years side by side, CSV and TSV, and one-cell-per-line PDF
  copies. Uncertain rows are flagged, and student details are dropped. The panel review screen
  can work out GPA points from your school's scale. The CLI has `transcript FILE|- [--save]`,
  and the service has `/v1/transcript` and `/v1/list-many`.
- Forms that ask for classes: course rows by number, "Number of AP courses" and
  "List your AP courses" (counted from saved classes).
- **Dropdowns:** range options ("$50,000 - $74,999", "6 or more") match the number they hold,
  and shared boundaries are refused. Custom dropdowns (ARIA comboboxes and listboxes, Google
  Forms, type-to-search) are opened at Fill time, and the real choices are matched with the
  same rules. Select2, Chosen and bootstrap-select fill their hidden `<select>` and check that
  the look-alike shows the choice. Undo covers custom dropdowns.
- Boxes that ask you to explain or describe are never filled.

## Toolkit 1.0.0 (2026-10-23)

The research skill is now one part of the Hermes Scholarship Toolkit, next to:

- `apps/board`: a phone-friendly scholarship board (statuses, notes, checklists, calendar,
  `.ics`/CSV downloads, weekly page watch). Changes are accepted only as JSON POSTs from the
  board page; there's an optional password login, and data lives in `~/.hermes/scholarship-board/`.
  `install.sh` sets it up as a systemd user service.
- `apps/autofill` and `skills/scholarship-form-fill` 0.2.0: a browser side panel that fills
  application boxes from answers saved locally, never submits, and reads every box back.
  Values are checked against their limits (a GPA above its scale or a class rank above class size
  is refused).
- `docs/remote-access.md`: open the board from a phone or another computer with Tailscale
  Serve (private) or Funnel (public, password required).

## 0.7.0 (2026-10-23)

Accuracy release, built from what the first long runs got wrong. It also folds in the lessons
Hermes wrote into its installed copy of the skill during those runs.

### Added

- `audit` (new `scripts/audit.py`): re-reads every stored record's own pages and checks that the
  page names the scholarship, shows the deadline with its year, and shows the award amount (not
  a program total), and flags fee, repayment, and drawing wording. Explicit no-fee,
  no-repayment, and selection statements and a single stated time zone fill fields the record
  left unknown, with the page quoted in the record's notes. `evaluate` applies the findings.
- `evaluate` returns a `worklist`: why each record still needs confirmation, the records one
  more page check can fix, profile fields worth asking about, and records only waiting for a
  cycle to open.
- Saved evidence: `search_batch` keeps every extracted page under `pages/` and logs results and
  excerpts to `evidence.jsonl`, so a truncated chat print never loses evidence.
- `add_candidates` (one verdict per record and the stored count) and `remove_candidate`.
- Optional record `notes` for page-backed observations that do not block "verified"; hedged
  notes move to `uncertainties` automatically.
- One saved profile per student (`start_run(student=...)`, `list_profiles`), workspaces that
  remember their student (`list_runs`, `start_run(resume=...)`), and a two-step
  `delete_profile` that shows the summary first and moves the profile to a private trash
  (`restore_profile`, `empty_trash`).
- `score` and `pipeline.py score`: compare a run with a hand-checked gold list.
- New query families: college scholarship portals, community-foundation fund directories,
  civic clubs, local businesses, counselor scholarship lists, district scholarship packets,
  STEM research competitions, and scholar-athlete awards.
- `references/record-composition.md`: recipes for records that pass the first time, closing
  gaps, re-confirming a report, and measuring accuracy.

### Changed

- Page excerpts always keep the best fee/repayment and time-zone passages when the page has
  them; before, those passages were usually cut, leaving most records unable to reach
  "verified".
- Searches read 3 pages of 6 results per query (was 2 of 5); the default plan is 60 queries
  (up to 100).
- The whole plan always runs: reaching the result limit no longer ends the search early.
  Maximum results go up to 100, and "as many as possible" is accepted.
- An award description over 200 characters is shortened at a clause boundary (full text kept
  in the amount evidence) instead of rejecting the record.
- `profile.py delete` moves the profile to the trash instead of erasing it.


## 0.6.0 (2026-10-22)

First public release. Updated for Hermes Agent 0.21.

### Added

- `scripts/scholarship_api.py`: one-import interface for every step, called from
  `execute_code`. It works the same on Linux, macOS, and Windows, with no shell heredocs or
  hard-coded `/tmp` paths.
- Private per-session run workspaces (`start_run`, `cleanup`) with owner-only permissions.
  Cleanup deletes only the files the skill creates.
- Questionnaire batches shaped for Hermes's `clarify` tool (at most 5 questions and 4 choices),
  and a `responses` submission that converts the user's own words: yes/no, "$1,000", "3.8/4.0",
  comma lists, and applicant-path descriptions.
- `add_candidate`: validates each record as soon as it is written and reports running counts
  and when enough matches exist.
- Registry expanded from 9 to 109 sources: the student-aid agency for all 50 states and DC,
  plus national programs, professional and membership organizations, military-family
  programs, and identity-focused organizations (used only with consent). The registry is
  validated on load.
- Deadlines are checked in the sponsor's time zone, with "days left" and "closing soon" in the
  report.
- Rolling deadlines are supported (they need confirmation).
- `.ics` calendar export with reminders a week ahead, and CSV export with spreadsheet-formula
  protection.
- An offline test suite and a CI workflow for Linux, macOS, and Windows.

### Changed

- The search batch ranks official domains ahead of databases and never extracts database or
  social-media pages. It sends keyword-focused excerpts instead of the first 3,000 characters,
  drops navigation menus and lines addressed to an AI, and remembers extracted and failed
  pages across batches.
- Batches stop before Hermes's 5-minute `execute_code` limit and return the cursor to resume
  from.
- SKILL.md rewritten to Hermes's authoring standard: a 55-character description, When to Use,
  Prerequisites, step-by-step completion checks, Pitfalls, and Verification. Detailed rules
  moved to `references/`.
- Tool names updated for Hermes 0.21 (`todo_list`, `cronjob_manage`).
- GPA bounds allow 100-point scales. GPA rules on a different scale now need confirmation
  instead of being compared numerically.
- Minimum-award checks use the documented minimum when no maximum is stated.

### Fixed

- Long pages lost their deadline and eligibility text, and Hermes's full-text pointer, to
  head-only truncation.
- Failed page extractions were reported as empty pages instead of errors.
- Club keywords matched inside other words (for example "DECA" in "Academic Decathlon").
- An official record on a known scholarship-database domain could pass verification through
  a same-domain "about" page.
- URLs containing characters such as `<` or `"` were accepted and rendered raw into reports.
- A future-dated check on the user's local date was rejected when it was already the next day
  in UTC.
- Hermes skill-scanner findings (shell-startup-file and hex-escape patterns) are gone; the
  scanner now reports no findings.

### Compatibility

- Profiles saved by 0.5.x still load and keep their onboarding status.
- The older `answers` plus `question_dispositions` submission format is still accepted.
- The `profile.py` and `pipeline.py` command-line interfaces keep their commands. New commands:
  `profile.py init --ephemeral`, `workdir`, `cleanup`; `pipeline.py add-candidate`, `export`,
  `check-registry`.

## 0.5.0-beta.2 (2026-08-09)

Private beta: two-stage onboarding with an atomic questionnaire submission, deterministic
query generation, source-verification methods, structured eligibility logic, and Markdown
reports.
