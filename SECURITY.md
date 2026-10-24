# Security and privacy

## Reporting a problem

Please report vulnerabilities privately through GitHub's "Report a vulnerability" button on
the repository's Security tab rather than in a public issue. Include the Hermes Agent version,
the skill or app version, and the smallest record, page, or profile that reproduces
the problem. Use fictional student data.

## What the skill defends against

- **Untrusted web content.** Pages can contain text aimed at AI agents. Excerpts drop lines
  addressed to an AI, SKILL.md tells the agent to treat page text as data, and nothing a page
  says can change which tools run. Logins, form submissions, uploads, and downloads are never
  allowed.
- **Unverified or fake listings.** Database pages cannot be labeled official, a source claim
  needs a matching authority page or registered domain, and fees, loans, sweepstakes, stale
  evidence, and past deadlines are filtered out deterministically.
- **Data exposure.** Search queries never include GPA, test scores, SAI, or disability details,
  and identity details are used in searches only with explicit consent. Run files are
  owner-only, and cleanup removes only files the skill created. Profile data is never written
  to the agent's long-term memory.
- **Unsafe output.** Report text is Markdown-escaped, links are rendered only for validated
  `https` URLs, and CSV cells that start like spreadsheet formulas are neutralized.
- **Path tricks.** The full-text pointer from a truncated page is passed to the agent only when
  it names a file in Hermes's web cache.

## The board (`apps/board`)

- Listens on `127.0.0.1` only. Other devices reach it only through Tailscale Serve (your
  tailnet) or Funnel (public). Funnel must be used with the password login.
- Changes are accepted only as `POST` requests with a JSON body. Browsers won't send those
  cross-site without a CORS preflight, which the server never grants, and requests that a
  browser labels cross-site are refused. Links and images can't change anything.
- The login is a salted PBKDF2-SHA256 hash in an owner-only file. Each wrong password is
  delayed by one second.
- Page text is escaped, only `http(s)` links are rendered, and the server sends `nosniff`,
  `DENY` framing, and `no-referrer` headers.
- Profile details aren't stored on the board beyond the state and the intended major.

## The autofill companion (`apps/autofill`)

- It never submits: it doesn't click buttons or links, and while it fills it blocks the page's
  own submit attempts in every frame. If that blocker can't be armed, it refuses to fill. The
  only synthetic presses are on a custom dropdown the student asked it to fill (open it, choose
  an `role=option`, Escape to close it), and never on a submit button or link. The end-to-end
  test checks this statically in `content.js` and with a dropdown that tries to submit.
- Pasted transcripts are read by the local service and never stored as text. Lines about the
  student (name, birth date, ID, SSN, address) are dropped before anything comes back.
- The local service listens on `127.0.0.1` only and needs a random key that only the extension
  copy has. Requests with a web `Origin` or another `Host` are refused, and there is no CORS.
  It must never be exposed through Tailscale or any other proxy.
- The store refuses SSN-shaped values, passwords, FSA IDs, and bank or card details, and the
  fill log never contains values.
- The portable copy (`portable/build.py`, for Chromebooks) has no service: the same Python
  store runs in the extension page with Pyodide (pinned by SHA-256, loaded from the extension
  folder, no network), and answers live in the extension's `chrome.storage.local`. Anyone using
  that browser profile can open them. A copy built with `--seed` carries one person's answers
  only as `seed.locked` (PBKDF2-SHA256, 600,000 rounds, AES-256-GCM); the passphrase is never
  written into the folder. Unlocking re-validates every answer before saving it. Backup files
  are not encrypted.

## Out of scope

- What the configured model provider or search provider does with data sent to it.
- Hermes session history, which stores the conversation on the local machine.
