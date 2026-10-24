# Contributing

Thanks for helping students find real scholarships. The most useful contributions are new or
corrected trusted sources, form labels the autofill misreads, fixes for pages the excerpt logic misreads, and bug reports with a
reproducible record or profile.

## Ground rules

- Python code uses only the standard library (the board's optional PDF import uses `pypdf`)
  and supports Python 3.10+. The extension has no dependencies or build step.
- Tests never touch the network. Hermes web tools are replaced with fakes, and browser tests
  use local fixture pages.
- Never include a real student's data in an issue, test, or example. Use the fictional records
  in `skills/scholarship-research/references/candidate-example.json` as a model.
- Keep the skill clean under Hermes's hub scanner. `tests/test_skill_md.py` lists the patterns
  to avoid.

## Adding or fixing a trusted source

Sources live in `skills/scholarship-research/references/trusted-sources.json`. An entry's
`official_domains` let records on those domains verify through `trusted_registry`, so a wrong
domain is a security problem, not a typo.

1. Confirm the domain on the organization's own site (its homepage, About page, or footer),
   not from a search result or a database listing.
2. Fill in every field:
   - `name`: unique, the organization's own name for the program
   - `role`: `official_sponsor`, `official_program`, `official_sponsor_or_local_club`,
     `state_agency`, `authorized_administrator_and_discovery`, or a discovery role
   - `tier`: 1 to 5 (3 for state agencies, 4 for profile-triggered sponsors, 5 for broad
     discovery)
   - `official_domains`, `homepage` (https, on one of those domains), and `purpose`
   - `trigger`: keyword groups (`major_keywords`, `affiliation_keywords`, `military_keywords`,
     `identity_keywords`) match whole words in the profile, any group by default or every
     group with `"match": "all"`. Filters (`states`, `search_modes`, `true_fields`) must all
     hold. Identity triggers apply only after the student consents to identity searches.
   - `query_template`: plain words plus fields from `pipeline.TEMPLATE_FIELDS`
3. Update `last_reviewed`, then run `python -m pytest`. `pipeline.py check-registry` validates
   the file on its own.

Database-style sites (aggregators that list other sponsors' awards) belong in
`discovery_domains`, never in a verifying role.

## Pull requests

- Add a test for every behavior change.
- Run `python -m pytest` and `node --test "apps/autofill/tests/*.test.mjs"`. For changes
  to the extension or the board page, also run `node apps/autofill/tests/e2e.mjs` and
  `node apps/board/tests/browser_smoke.mjs` (they need Chromium: they find a Playwright or system one, or set
  `CHROME=/path/to/chromium`; branded Google Chrome won't load test extensions).
  With a hermes-agent checkout available, the Hermes scanner and linter tests run too.
- Autofill matching must stay "never guess": when a label could mean two saved answers, the
  box is left for the student. Add the label to `matcher.test.mjs` with the expected outcome.
- Update `CHANGELOG.md` and, for user-visible changes, `README.md` or `SKILL.md`.
- Bump `version` in `SKILL.md` for releases.
