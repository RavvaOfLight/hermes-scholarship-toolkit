# Scholarship board

A phone-friendly tracker for the awards a `scholarship-research` run found. It's one page
(`app.html`, with no CDN or build step) served by a standard-library Python server.

- Compact cards that expand for details, with status buttons (to verify, in progress, applied,
  accepted, rejected) and notes.
- Each award's application steps as checkboxes, plus your own steps. A **To do** tab shows the
  next steps across awards, soonest deadline first, and a **What you'll need** summary groups
  them (letters, essays, transcripts, FAFSA, and so on).
- A month calendar, filters, search, and sort.
- `.ics` calendar download with reminders 7 days and 1 day before each deadline, and a CSV
  spreadsheet download.
- Several searches ("trackers") side by side. Re-importing a newer run keeps your marks and
  notes, flags new awards, records what changed, and keeps awards the new run missed.
- An optional weekly **page watch** re-reads the official pages of awards that aren't open yet
  and flags new opening dates, deadlines, or "applications are open" wording. No AI model is
  involved.

## Run it

```bash
python3 apps/board/import.py --run ~/path/to/run-workspace   # after a scholarship-research run
python3 apps/board/server.py                                  # http://127.0.0.1:8765
```

Or install it as a service that starts with your user session:

```bash
apps/board/install.sh                  # board service + weekly page watch (if Hermes is installed)
apps/board/install.sh --uninstall
```

To open it from your phone, see [Remote access with Tailscale](../../docs/remote-access.md).
Set a login first:

```bash
python3 apps/board/set_password.py YOUR_NAME
```

## Adding searches

After `install.sh`, each scholarship-research run adds its results by itself: the skill's last
step (`api.send_to_board`) drops a request in `BOARD_DATA_DIR/inbox`, and the
`scholarship-board-inbox.path` unit runs `inbox.py`, which imports it into that student's
tracker ("Scholarship search", or "Scholarship search · Name" for a named profile) and tells
the skill whether it worked. `install.sh` writes `~/.hermes/scholarship-board.json` so the skill
knows where the inbox is. Your statuses, notes and checklist ticks are kept on every run.

By hand:

```bash
# a scholarship-research run workspace (reads its evaluated.json, or results.csv)
python3 import.py --run ~/scholarship-runs/run-01 --id fall-2026 --label "Fall 2026"
# an evaluated.json file directly
python3 import.py --evaluated path/to/evaluated.json --id fall-2026
# a report PDF (needs: python3 -m pip install pypdf)
python3 import.py --pdf scholarship-report.pdf --label "Report"
# a narrower view of an existing tracker, by keywords
python3 import.py --subset "state university, community college" --from fall-2026 --id in-state
```

The run workspace path is printed by the skill when a run starts (`api.start_run`).
Re-importing an existing `--id` merges the new results into it.

## Where data lives

Everything is in `BOARD_DATA_DIR` (default `~/.hermes/scholarship-board/`, owner-only), never
next to the code:

| File | What it is |
| --- | --- |
| `trackers.json` | list of trackers |
| `stores/<id>.json` | one tracker's awards with your marks, notes, steps and activity log |
| `.auth.json` | the board login (salted PBKDF2 hash), if set |
| `watch-state.json`, `watch-latest.json` | what the page watch has seen |

The board keeps no profile details (GPA, scores, income, county, school) beyond two tags: the
state and the intended major.

`reset.py [tracker-id]` clears marks, notes, checklists and the log.

## Page watch

```bash
systemctl --user list-timers scholarship-board-watch.timer     # next run (Mondays 8:00)
systemctl --user start scholarship-board-watch.service         # run now
~/.hermes/hermes-agent/venv/bin/python watch.py --dry-run       # look without writing
```

The page watch uses Hermes's configured `web_extract` backend, so it runs with Hermes's Python.
Extract backends return different amounts of text for the same page from one call to the
next, so it compares remembered facts (dates and "open" wording), not page text. It reads each
page twice per run, and its first two checks only learn the page.

## Security

- The server listens on `127.0.0.1` only. Use Tailscale Serve or Funnel to reach it from other
  devices.
- Changes are accepted only as JSON `POST` requests from the board page itself. A link, an
  image, or another website can't change a status.
- With `.auth.json` present, every request needs the login. A wrong password costs one second.
  Without it, requests that come through Tailscale Serve/Funnel or another proxy are refused.
- All text on the page is escaped, and only `http(s)` links are rendered. CSV cells that look
  like spreadsheet formulas are neutralized.

## Tests

```bash
python3 -m pytest apps/board/tests      # offline
node apps/board/tests/browser_smoke.mjs # headless Chromium (finds one, or CHROME=/path/to/chromium)
```
