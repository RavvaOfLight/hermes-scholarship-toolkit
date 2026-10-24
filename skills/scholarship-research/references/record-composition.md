# Record composition recipes

How to write records that `add_candidates` accepts the first time, close the gaps that keep a
record out of "verified", and measure accuracy. The rules themselves are in
`verification-rules.md`; field shapes are in `record-schema.json`.

## Contents

1. Storing records
2. Gates that reject records
3. Common program shapes
4. Discovery leads
5. Closing gaps after evaluation
6. Re-confirming a stored report
7. Measuring accuracy

## Storing records

- Store records with `api.add_candidates(RUN["profile"], RUN["files"]["candidates"], [record, ...])`.
  It returns one verdict per record (`ok`, `bucket`, `error`) and `stored_records`, the count
  in the file. Fix and re-send only the rejected records.
- Keep the record list in a file in the run workspace (for example `records.py` defining
  `RECORDS = [...]`) when it spans several calls, and load it from disk each call: names bound
  in one `execute_code` call do not survive to the next.
- Identity is name plus sponsor. Re-adding the same name and sponsor replaces the record. When
  a record is renamed (for example a lead promoted to an official record), remove the old one
  with `api.remove_candidate(RUN["files"]["candidates"], "<old name>")`.
- Search evidence is saved automatically: each batch appends to `evidence.jsonl` in the
  workspace, and every extracted page is kept under `pages/` (its path is the page's
  `saved_page`). Re-read a page with `read_file` instead of extracting it again.

## Gates that reject records

- `status: "open"` needs a dated deadline (or `type: "rolling"`) and a non-empty
  `application_url`. When the route is offline (financial aid office, counselor, paper packet)
  or not yet announced, use `status: "not_open_yet"` with
  `{"date": null, "timezone": "", "type": "not_announced"}`. A closed cycle is
  `status: "closed"` (the evaluator calls it expired); "expired" is not a record status.
- `deadline` holds exactly `date`, `timezone`, and `type`. A cutoff time such as
  "11:59 PM CT" belongs in `evidence.deadline`; the page check reads the zone from the page.
- Pair `source_kind` with `source_verification.method`: `official` with `sponsor_domain` or
  `trusted_registry`, `authorized_administrator` with `authorized_by_sponsor` or
  `trusted_registry`, `discovery` with `source_verification: null`. Change both together.
- `sponsor_domain`: `authority_url` must be on the same host as `official_url` (a shared
  parent domain is not enough). When the program lives on a subdomain, find a page on that
  host (`site:<host>`) and move the URLs onto it.
- `authorized_by_sponsor`: `official_url` is the administrator's program page and
  `authority_url` is the sponsor's own, different-domain page naming the administrator. When an
  official page's apply link goes to an application platform, record the program as
  `authorized_administrator` with `official_url` on the platform's program page. Without an
  independent sponsor page, store the program as a discovery lead.
- A requirement's `field` must be one listed in `record-schema.json`; invented paths are
  rejected. Values must match the field's type: categorical fields take booleans, list
  fields take lists, and list fields (such as `student.target_colleges`) reject `not_in`
  (use `contains` on a term the list lacks and say so in `description`). There is no income
  field: income or asset caps are soft requirements plus an `uncertainties` entry.
- A hard requirement on a field the profile lacks evaluates as unknown (needs confirmation),
  not ineligible. To document a known failure, encode it on a field the profile has (for
  example `student.location.county`) with `hard: true`.

## Common program shapes

- Automatic consideration (merit awards through the admission application): the admissions
  apply page is the `application_url`, the dated admission deadline is the record deadline
  (`status: "open"`, `received_by`), and `application_fee` is `false` with the
  automatic-consideration wording quoted.
- Nomination-only programs: `status: "not_open_yet"`, `application_url: ""`, an unannounced
  deadline, and the counselor or chapter route in `application_tasks`.
- Rolling deadlines: `{"date": null, "type": "rolling"}` is allowed for open records and
  classifies as needs confirmation; put consideration dates (for example the FAFSA date) in
  evidence and tasks.
- Community-foundation portals with one application for many named funds: store each named
  fund the student fits as its own record, with `official_url` on the page that names that
  fund (the page check flags a general "apply" page that does not name it).
- Major- or school-restricted awards the student does not fit: encode the failing
  requirement (hard) so the report documents why the award is ineligible.

## Discovery leads

For database leads never confirmed on a sponsor page: `source_kind: "discovery"`,
`source_verification: null`, `status: "cycle_unknown"`, `cycle: "Unknown"`, the three flags
`null`, and evidence fields that say "Database lead; details not extracted from a sponsor
page". Snippets are not evidence. Leads appear only in the report's unverified section; keep
them (they are where many local awards are found) and promote them when an official page
turns up.

## Closing gaps after evaluation

`api.evaluate` returns a `worklist`:

| Code | Meaning | What to do |
| --- | --- | --- |
| `application_fee`, `is_loan`, `is_sweepstakes` | the flag is still `null` | read the sponsor's FAQ, eligibility, or apply page for fee, repayment, and selection wording |
| `timezone` | dated deadline without a zone | check the application portal page for the cutoff time |
| `page_check` | `api.audit` found a mismatch | correct the record from the page, or point `official_url` at the page naming the program |
| `record_doubt` | an `uncertainties` entry you wrote | resolve it from the page or leave it |
| `ask_user:<field>` | a hard requirement needs a profile answer | ask the user once, resubmit onboarding with the full set |
| `cycle_not_open`, `amount_vs_minimum`, `rolling_deadline` | waiting on the sponsor | nothing to fix now; say when the cycle opens |

Work `fixable_by_page_check` once: update each record with page-backed values and evidence,
`add_candidates`, `audit`, and `evaluate` again. Never relax a rule to clear a gap.

## Re-confirming a stored report

When the user asks to re-check everything: `api.audit(RUN["files"]["candidates"])` re-reads
every official page (it fetches pages not saved yet). For unreadable or JavaScript-only pages,
use the browser tool if available; a moved page (404) is re-found with
`web_search` restricted to the sponsor's site, and a re-versioned page can change amounts,
counts, portals, and deadlines, so rewrite the record from the newest page. Then work the
worklist, `evaluate`, `render`, and tell the user what changed (unchanged, corrected dates or
amounts, newly verified, still leads).

## Measuring accuracy

Keep a hand-checked gold list for a student in a private file such as
`~/.hermes/scholarship-profiles/<name>.gold.json`:

```json
{"scholarships": [
  {"name": "Example Engineering Scholarship", "official_url": "https://www.example.org/scholarships/engineering",
   "deadline": "2027-02-01", "amount_max": 2000, "eligible": true}
]}
```

Only `name` is required; add the fields you have checked yourself. After a run,
`api.score(RUN["files"]["evaluated"], "<gold file>")` reports recall (how many gold
scholarships were found) and deadline, amount, source, and eligibility accuracy, with the
missed names. Compare scores between runs before and after changing the skill or the model.
