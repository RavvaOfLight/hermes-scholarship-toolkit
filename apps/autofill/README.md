# Scholarship autofill companion

A browser side panel (Microsoft Edge or Google Chrome) that fills the repetitive boxes on
scholarship applications: names, parents, counselor, school, GPA, test scores, address,
financial aid answers (FAFSA, SAI, family income), classes, activities, and recommenders. It
uses answers you save once on your own computer, and it can read your classes from a pasted
transcript.

**It never submits anything.** It doesn't click buttons or links, and while it types it blocks
any submit the page itself tries. The only presses it makes are on a dropdown you asked it to
fill: opening it, choosing the exact matching option, and the Escape key to close it again
when nothing matches. You review exactly what goes in each box before anything is typed.

## How you use it

Open the panel with the toolbar button or **Alt+Shift+F**, then choose boxes in one of four ways:

- **Point**: click the boxes on the form, one at a time.
- **Circle**: draw a loop around a group of boxes, such as the parent section.
- **Whole page**: list every box the page has.
- **Tell it**: type what you want, for example "counselor email", then **Find on this page** or
  **Put in a box** (click the box).

Each box gets a numbered tag on the page and a row in the panel showing the exact text it will
receive. Nothing is typed until you press **Fill**. **Undo** puts every box back.

## Dropdowns

- **Normal dropdowns** (`<select>`) pick the option whose text matches your answer exactly or
  through a short list of synonyms ("KS" or "Kansas", "12th grade" or "Senior").
- **Range dropdowns** ("Less than $30,000", "$50,000 - $74,999", "6 or more") pick the one range
  your number falls in. A number on a shared boundary, where two ranges could hold it, is left
  for you. These rows start unticked, so you see the range before it's chosen.
- **Custom dropdowns** built from `div`s (React, Material UI, Headless UI, Google Forms, Select2,
  Chosen, bootstrap-select, type-to-search boxes) are opened only when you press Fill. The
  panel reads the real choices and applies the same exact-match rules, then picks one and
  checks that the dropdown shows it. If no choice matches, or it can't tell which list belongs
  to the box, it closes the dropdown and tells you.
- A look-alike that doesn't follow its hidden dropdown (Tom Select, for example) is put back
  and marked "Didn't fill" rather than left showing something different from what it would send.
- A dropdown whose button is also the form's submit button is never clicked.
- Undo puts custom dropdowns back to their earlier choice. One that was empty before can't be
  emptied again, and the panel tells you which ones to change by hand.

## Classes and transcripts

**My info → Classes** holds your classes: name, level (Regular, Honors, Advanced, Pre-AP, AP,
IB, Dual enrollment, College course), the grade you took it in, term, final grade, credits,
GPA points, and status (completed, in progress, planned).

**Paste your transcript:** open your transcript (a PDF, your school portal, or a spreadsheet),
select everything, copy it, paste it into the box, and press **Read it**. The reader handles
aligned tables, PowerSchool-style rows with course codes and semester grades, two school years
side by side, CSV and spreadsheet pastes, and text copied out of a PDF viewer, which often puts
every cell on its own line. Every class appears as an editable row:

- Rows it wasn't sure about are amber and unticked, with the reason. For example, "Advanced"
  may just be part of the class name, a number could be credits or GPA points, or a row has
  two semester grades.
- Lines about you (name, birth date, ID, SSN, address) are dropped, never shown or saved.
- GPA, class rank and class size lines are listed separately and saved only if you tick them.
  A GPA the transcript doesn't call weighted or unweighted waits until you say which it is.
- **Work out GPA points from my school's scale:** you set what each level adds (for example,
  AP +1.0, Honors +0.5) and what a + or − is worth, and it fills GPA points for letter grades.
- Nothing is saved until you press **Save**. The pasted text is cleared from the panel as soon as
  it's read.

Forms that ask for classes work too: "Course 1 name", "Course 2 level", "Number of AP courses"
(counted from your saved classes) and "List your AP courses".

Hermes can do the same from the command line (`autofill.py transcript FILE`, or `-` for pasted
text). It prints the rows for the student to confirm and saves nothing without `--save`.

## Why it doesn't guess

- A box is ticked only when its label clearly names one saved answer for one person. Anything
  unclear is left unticked (*check*), asks you to pick (*choose*), or is left alone. A plain
  "GPA" asks whether you mean weighted or unweighted, and "Email" inside a parent section never
  gets the student's email.
- Dropdown choices must match the visible text exactly or through a short list of known
  synonyms. Substring matches are never used, so "Kansas" never matches "Arkansas".
- It never fills Social Security numbers, passwords, signatures, essays, or "I certify / I
  agree" boxes. It doesn't replace text already in a box unless you tick that box.
- After filling, it reads every box back. If a site rejects or changes a value, the panel says
  so and puts the old value back.
- Labels that ask you to explain or describe something ("Explain your financial need") are left
  for you, even when they mention something you saved.

No tool can promise zero mistakes on every website. This one is built to skip or ask rather
than guess, and to show you every value before it goes in.

## Install

Needs Python 3.10+, `curl`, and a Chromium-based browser (Edge or Chrome 116 or later).

```bash
apps/autofill/install.sh
```

The script:
1. Makes a private key and port in `~/.hermes/scholarship-fill/companion.json` (owner-only).
2. Starts the local service `scholarship-autofill` (systemd user service on `127.0.0.1:8781`).
3. Copies the extension, with your key, to a folder for the browser to load. On WSL that's
   `C:\Users\<you>\ScholarshipAutofill`; elsewhere, `~/.local/share/scholarship-autofill/extension`.
   Set `EXTENSION_DIR=...` to choose the folder.
4. Links `~/.hermes/scholarship-fill/app` to this folder, so the Hermes skill
   `scholarship-form-fill` can save answers for you.

Then, once, in the browser:
1. Open `edge://extensions` (or `chrome://extensions`).
2. Turn on **Developer mode** and click **Load unpacked**.
3. Pick the folder the script printed, then pin the toolbar button.

After you update the code, run `install.sh` again and press **Reload** on the extension card.

**Windows + WSL:** run the script inside WSL and load the extension in Windows Edge or Chrome.
The browser reaches the service through WSL's localhost forwarding. If systemd is off in WSL,
the script says so and tells you how to start the service by hand.

## Chromebooks: the portable copy

Chromebooks can't run the companion service, so there's a second way to install: a portable
extension folder that keeps your answers in the browser itself. The same checks and the same
transcript reader run inside the extension (Python compiled for the browser, with
[Pyodide](https://pyodide.org)), so it works with no internet connection and nothing else to install.
It also works in Chrome or Edge on Windows, Mac or Linux.

```bash
python3 apps/autofill/portable/build.py --out ~/usb-copy            # blank: for anyone
AUTOFILL_SEED_PASSPHRASE='four random words here' \
  python3 apps/autofill/portable/build.py --out ~/usb-mine --seed ~/.hermes/scholarship-fill/data/data.json
```

- The build downloads Pyodide once from the npm registry, checks its SHA-256, and caches it.
  The folder is about 14 MB.
- `--seed` puts your saved answers in the folder as `seed.locked`, encrypted with the
  passphrase (PBKDF2 + AES-256-GCM). The first time the panel opens it asks for the passphrase,
  checks every answer again, and saves them in that browser. Without the passphrase the file is
  unreadable, so a lost USB drive doesn't give your answers away.
- Install it with the steps in **How to install.html** next to the folder: copy the folder onto
  the computer (not the USB drive), open `chrome://extensions`, turn on Developer mode, then
  Load unpacked. Nothing can install it automatically when the drive is plugged in, and school-managed
  Chromebooks often block Load unpacked.
- Removing the extension or resetting the Chromebook deletes its answers. Use **My info → Back
  up or move your answers** to keep a backup file (not encrypted) or move to another computer.
- Hermes can't save answers into a portable copy; use the panel's My info tab.

## Saving your answers

- In the panel's **My info** tab.
- Or ask Hermes (the `scholarship-form-fill` skill), for example "save my counselor's email".
- Or from the command line:

```bash
python3 autofill.py keys                                   # every item, its type and choices
python3 autofill.py set academics.act_composite 31
python3 autofill.py set financial.sai -- -1500            # a negative number needs -- first
python3 autofill.py list-add activities name="Robotics Club" role=Captain hours_per_week=6
python3 autofill.py status                                 # what's saved (no values shown)
```

Every value is checked before it's saved, and bad values are refused (an ACT of 40, a 9-digit
phone, a GPA above its scale). Social Security numbers, passwords, FSA IDs, and bank details
are refused outright. Answers stay in `~/.hermes/scholarship-fill/data/data.json` (owner-only,
with the last 30 versions in `history/`). The fill log records which box got which item, never
the value.

## Security

- The service listens on `127.0.0.1` only and needs the private key on every request. Requests
  from web pages (an `http(s)` `Origin`) or with another `Host` are refused, and there is no
  CORS, so websites can't read your answers. **Don't put it behind Tailscale.** Only the
  board is meant to be shared.
- The extension reads boxes only on the tab where you open the panel.

## Limits

- A custom dropdown that only shows its choices after you type several letters, or that
  loads them from the internet slowly (more than about 2 seconds), may not fill. The panel says
  so and offers **Copy**.
- Transcripts that are pictures (scans or photos) have no text to read. Copy the text from the
  PDF instead, or type the classes in.
- A form embedded from another website (a cross-origin iframe) has to be opened in its own tab.
- PDF forms aren't supported.

## Files

| File | Job |
| --- | --- |
| `extension/schema.json` | every saved item, its type and checks (shared by Python and JS) |
| `extension/matcher.js` | which saved answer fits a box and how to type it; the "never guess" rules |
| `extension/content.js` | in-page: reads boxes, Point/Circle overlay, tags, fill + read-back + undo |
| `extension/panel.*` | side panel: review list, fill, "tell me what to fill", My info editor |
| `autofill_store.py` | validated, owner-only store (30 versions kept) |
| `autofill_server.py` | local API for the extension |
| `transcript.py` | reads classes, grades, credits and GPA lines from pasted transcript text |
| `autofill.py` | command line, also used by the Hermes skill |
| `install.sh` | service + extension copy + skill link |
| `autofill_api.py` | what each request does, shared by the service and the portable copy |
| `extension/portable.js`, `portable/` | the portable copy: in-browser store, builder, passphrase lock, install guide |

## Tests

```bash
node --test "apps/autofill/tests/*.test.mjs"      # ~200 real-world label cases
python3 -m pytest apps/autofill/tests              # store, service and 20+ transcript layouts
node apps/autofill/tests/e2e.mjs                   # headless Chromium + the real extension + submit traps
PORTABLE=1 node apps/autofill/tests/e2e.mjs        # the same against a portable build
```

The end-to-end test loads the extension in headless Chromium (`CHROME=/path/to/chrome`). The
test form tries to submit in three different ways while its boxes change, and the test checks
that every one is blocked and that your own Submit still works afterward. A second page has
seven kinds of custom dropdowns, including one that tries to submit when a choice is made and
one whose button is a submit button. A third step pastes a transcript into the panel and saves it.

## Uninstall

`apps/autofill/install.sh --uninstall`, then remove the extension in the browser. Your saved
answers stay in `~/.hermes/scholarship-fill/data/` until you delete that folder.
