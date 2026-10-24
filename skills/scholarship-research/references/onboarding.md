# Onboarding reference

## Contents

1. Flow
2. Asking the batches
3. Recording replies
4. How replies are converted
5. Saved profiles
6. Errors and interrupted onboarding

## Flow

`api.questionnaire(profile)` returns ten batches in a fixed order: the core search profile
(state, academic level, graduation year, entry term, degree, major, applicant path), local
context, academics and college plans, memberships, financial background, identity, and
search preferences. Each batch holds at most five questions so it fits one `clarify` call.

Search and evaluation refuse to run until `api.complete_onboarding` accepts a response for
every question. That submission is the record that each question was offered.

## Asking the batches

- Pass `batch["clarify"]` as the `clarify` arguments without editing the questions or choices.
  `clarify` adds an "Other" row, so the user can always type a free answer.
- Ask the batches in order. Do not merge, reorder, or skip a batch, including the optional
  `sensitive` ones; the user decides what to skip.
- Without `clarify` (for example in a messaging gateway), send each batch as one numbered
  chat message and wait for the reply.
- When the user already stated a value earlier in the conversation, or the batch has
  `current_values`, confirm it ("You said Ohio; still right?") rather than asking afresh.
- The applicant-path question has four choices. A trade, certificate, or apprenticeship
  student answers through "Other".
- Ask the identity-search consent question exactly as written, after the identity questions.
  Sharing an identity detail is not consent to search with it.

## Recording replies

Submit `{"responses": {"<field path>": <reply>, ...}}` with one entry for every field listed in
the batches' `fields` arrays. Use the user's own words; the helper converts them.

- Required fields (`required_fields` in each batch) need a real answer.
- Any other field may be answered with a skip word: unknown, none, n/a, skip, prefer not to
  answer, decline. For text and list questions, "no" also means nothing to record.
- Never fill a field the user did not answer. A guess, an inference from another answer, or an
  estimate (for example a predicted SAT score or SAI) is not an answer.

## How replies are converted

| Question type | Accepted replies | Stored as |
| --- | --- | --- |
| Yes/no | yes, no, true, false (and choice labels) | true or false |
| Number | 2027, 1310, $1,000 | integer or decimal |
| GPA | 3.8, or 3.8/4.0 (which also sets the GPA scale) | decimal |
| List | "Robotics, Key Club" or a JSON array | list of names |
| Applicant path | choice label or a description such as "trade school" | one of high_school, undergraduate, transfer, graduate, trade_certificate |
| Text | any text except a bare "yes" | text |

A text or list question answered with a bare "yes" (for example, "Does an employer offer
scholarships?") is rejected: ask for the organization's name.

## Saved profiles

- Each student has their own saved profile. `api.start_run(..., student="<first name>")`
  uses (and on first use creates) that student's file in `~/.hermes/scholarship-profiles/`.
  `persistent=True` alone uses the original default profile at
  `~/.hermes/scholarship-profile.json` (or `SCHOLARSHIP_PROFILE` when set). Save a profile only
  when the user asks.
- When the user may be helping several students, call `api.list_profiles()` first and confirm
  which student this run is for. Never reuse one student's profile for another.
- A session's workspace belongs to one student; `start_run` refuses to switch it to another
  student's saved profile. Use a new session, or `api.list_runs()` and
  `start_run(resume="run-...")` to reopen that student's earlier workspace.
- For a complete saved profile, show `api.profile_summary` and ask what changed. To change
  answers, re-ask only those questions, then resubmit every field: changed replies, the
  batches' `current_values` for unchanged answers, and "unknown" for each field listed in
  `previously_skipped`. A later chat answer replaces an earlier "unknown".
- Deleting is two steps and only on request: `api.delete_profile("<name>")` returns the
  profile summary; show it and ask whether this is the right student; only after a clear yes
  call `api.delete_profile("<name>", confirm=True)`. The profile moves to a private trash
  (`api.restore_profile(trash_file)` brings it back; `api.empty_trash(trash_file)` erases it)
  and that student's run workspaces are removed. Never delete or overwrite a profile file
  with `terminal`, `write_file`, or `patch`.
- Run workspaces live under the system temporary directory with owner-only permissions and
  are removed by `api.cleanup` (only the files this skill creates are deleted, including the
  saved `pages/` and `evidence.jsonl`).

## Questionnaire practice

- Pass the profile file path (`RUN["profile"]`) to `api.questionnaire`, never a loaded dict.
- After the first `clarify` timeout, ask every remaining batch as one numbered chat message;
  re-sending forms to a user who answers in chat wastes the timeout on every batch.
- Before submitting, confirm in chat any value that drives searches and looks misspelled
  (county, city, high school); never correct it silently.
- An employer-scholarship "yes" without a company name is not searchable: ask once for the
  name. If the academic level and degree level contradict (a high-school senior answering
  "Master's"), settle it in chat before generating queries.

## Errors and interrupted onboarding

- `complete_onboarding` writes nothing unless every response is valid. On `ok: false`, each
  entry in `errors` names a field: ask about that field only, then resubmit the full set.
- If the conversation stops partway, start again from the first unanswered batch; earlier
  answers are not saved until the submission succeeds.
- In one-shot or scripted Hermes runs, `clarify` replies "no user available; assume". Treat
  that as no answer and stop onboarding.
