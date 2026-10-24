---
name: scholarship-form-fill
description: Use when saving answers for scholarship form autofill.
version: 0.3.1
author: Preston Campbell
license: PolyForm-Noncommercial-1.0.0
metadata:
  hermes:
    tags: [scholarships, forms, autofill, transcript, financial-aid, browser-extension]
    requires_toolsets: [terminal]
---

# Scholarship Form Fill (browser companion)

## When to Use

- The student gives you details to save for filling applications: names, parents, counselor
  email, GPA, ACT/SAT scores, address, financial aid answers (FAFSA, SAI, family income),
  classes, activities, recommenders.
- The student shares a transcript and wants their classes saved.
- The student asks what is saved, what is missing, or how to autofill a form.
- Don't use for: typing into web forms yourself, essays, logins, or submitting anything.

## How filling works

The student fills forms in their own browser (Edge or Chrome) with the **Scholarship Autofill**
side panel (toolbar button or Alt+Shift+F). They point at boxes, circle a group, or pick "Whole
page", review exactly what goes in each box, and press Fill. The extension never submits,
never clicks buttons, and blocks any submit the page tries while it fills. It also handles
custom dropdowns and range dropdowns ("$50,000 - $74,999"). Your job is only the saved answers.

**Never fill application forms yourself** with browser, computer_use or execute_code tools, and
never click Next, Submit or Apply. If asked to, explain the companion instead.

## Saving answers

Use `terminal` with the CLI (it validates every value and refuses bad ones):

```
cd ~/.hermes/scholarship-fill/app             # made by apps/autofill/install.sh
python3 autofill.py keys                       # item keys, labels, allowed choices
python3 autofill.py keys financial             # one section
python3 autofill.py status                     # what's saved per section (no values)
python3 autofill.py set school.counselor_email "counselor@school.example.org"
python3 autofill.py set parent1.relationship Mother
python3 autofill.py set financial.sai -- -1500
python3 autofill.py list-add activities name="Robotics Club" role=Captain hours_per_week=6
python3 autofill.py list-add courses name="AP Biology" level=AP grade_level="11th grade" grade=A credits=1.0 status=Completed
python3 autofill.py list-show courses
python3 autofill.py list-update courses 22 grade=A status=Completed   # fix one row; other fields stay
python3 autofill.py list-add exams exam="AP Biology" score=5
python3 autofill.py unset student.pronouns
```

1. Copy values exactly as the student typed them. Never guess, infer or "fix" a value; if a
   detail is unclear (which parent is Mother, weighted or unweighted GPA, GPA scale, a class's
   level), ask.
2. After each `set`, read back the saved value from the output and tell the student.
3. If the output says `NOT SAVED`, tell the student the reason and ask for the corrected value.
4. Save each parent's `relationship` so forms that say "Mother"/"Father" match the right person.
5. Never save Social Security numbers, passwords, FSA IDs, bank or card details, or signatures;
   the store refuses them. Tell the student to type those on the form themselves.
6. Don't print saved values (`show`) unless the student asks to see them.

## Financial aid answers

Keys are in `python3 autofill.py keys financial`: financial need (yes/no), applying for aid,
FAFSA submitted and its date, SAI, Pell eligibility, CSS Profile, total family income, parents'
AGI, the student's own income, unmet need, and free or reduced-price lunch.

- The SAI comes from the student's FAFSA Submission Summary and can be negative (down to
  -1500). It replaced the EFC; if a student gives an "EFC", ask whether it's really their SAI.
- Income values are whole dollars. Ask whether a number is total family income or AGI; they
  are different lines on the tax return.
- These are private. Never repeat them in chat unless the student asks.

## Transcripts

When the student shares a transcript (a file path, or text they paste):

1. Read it with the CLI. It saves nothing on its own:
   - a file: `python3 autofill.py transcript ~/Downloads/transcript.pdf` (also .txt, .csv)
   - pasted text: pass it on stdin with a quoted heredoc:
     `python3 autofill.py transcript - <<'EOF'` … `EOF`
2. Show the student the numbered table it prints, including every `CHECK:` note and any lines
   it couldn't read. Ask them to confirm or correct each flagged row (the level, the grade
   taken, which number is credits).
3. When they confirm, run it again with `--save` (add `--replace` only if they want to replace
   classes already saved). That saves the rows without questions and any AP/IB exam scores it
   read. Fix saved rows with `list-update courses NUMBER field=value` (it keeps the other
   fields, such as the term); add missing rows with `list-add courses …`, using exactly what
   the student told you.
4. AP/IB exam scores go in the `exams` list (`list-show exams`), not in class rows.
5. GPA and rank lines are never saved by `--save`. Confirm them with the student first: which
   GPA is weighted, and on what scale. Then use `set academics.gpa_weighted …` and the others.
6. If it reads no classes (a scan or a photo), don't transcribe it from an image yourself.
   Ask the student to copy the text from the PDF, or use the panel's "Paste your transcript"
   box, which shows the same rows for them to tick.

The panel's **My info → Classes → Paste your transcript** does the same thing: the student
pastes, reviews every row, and saves the ones they tick.

## Pitfalls

- If `~/.hermes/scholarship-fill/app` is missing, the companion isn't installed: point the
  student to `apps/autofill/install.sh` in the Hermes Scholarship Toolkit.
- The data lives in `~/.hermes/scholarship-fill/data/data.json` (owner-only, last 30 versions in
  `history/`). Never edit it with write_file or patch; use the CLI.
- The service is `systemctl --user status scholarship-autofill` (local port 8781 by default). If the
  panel says "Can't reach the service", restart it: `systemctl --user restart scholarship-autofill`.
- After the extension code changes, the student reruns `install.sh` and presses Reload on the
  extension in `edge://extensions` or `chrome://extensions`.
- A value that starts with a minus sign needs `--` before it (`set financial.sai -- -1500`).
