# Security and local-data lifecycle

## Trust boundaries

SampoAgent is a single-user local application and binds to `127.0.0.1`. It has
no sign-in gate; do not expose the development server or desktop port to a LAN,
reverse proxy, or untrusted browser profile. The application treats CVs, job
descriptions, employer forms, email content, and source HTML as untrusted data,
not as instructions that can change automation policy.

Candidate facts retain provenance and confirmation state. Unknown, conflicting,
declined, expired, unsupported, and high-risk answers are not automatically
filled. Full Autopilot is a local, expiring and revocable grant limited by the
saved scope and daily quota. CAPTCHA, authentication/MFA, legal or sensitive
declarations, unsupported forms, and uncertain submissions stop for the user.
SampoAgent does not bypass access controls, and an uncertain post-submit result
is never retried automatically. Codex-assisted final submission follows its
separate job-specific confirmation rule.

## Credentials and logs

Provider API keys are configured outside SQLite. Gmail/Outlook inbox-read and
application-send scopes are separate; send authorization is granted explicitly
and send tokens are encrypted in SQLite with `SAMPOAGENT_TOKEN_ENCRYPTION_KEY`.
Keep that key outside backups, source control, and shared folders. A restored
mail connection is usable only with the same key. Browser passwords are entered
manually by the candidate and stay in the local Playwright browser profile; they
are not placed in the application database or export.

Local-server access logging is disabled because OAuth callbacks contain
short-lived authorization codes and state values in their query strings.
Unhandled web-request exceptions are caught at the application boundary: the
browser receives a generic 500 response with an opaque reference, while the
application logger records only that reference and the exception type. It does
not log the exception message, traceback, request URL, or submitted form data.
User-facing form failures, failed source-result summaries, worker status, and
automation CLI failures use fixed messages rather than interpolated exception
text; CLI exception chaining is suppressed at those boundaries. Chromium
startup, manual-login navigation, and shutdown failures are normalized before
leaving the CLI, and the automation database connection still closes if
browser teardown fails.
Synthetic PII/secret driver failures verify these boundaries in
`tests/test_security_boundaries.py`.

The activity/audit trail stores only a fixed allowlisted action code and a
generic privacy detail; callers cannot persist free-form values through
`Repository.log()`. Repository reads and the dashboard replace details on old
rows as well, so legacy event text is not rendered there. Settings provides an
explicit cleanup form protected by a local action token and exact `REDACT`
confirmation; it replaces only legacy detail text while preserving event codes
and timestamps. Previously downloaded backups remain unchanged and user-managed.
No production candidate database was opened or scrubbed in this code change.

This does not mean that the local database is de-identified: candidate facts,
confirmed answers, employer-response evidence, CVs, and email snippets are
intentionally stored locally and remain sensitive personal data. SampoAgent
does not attach page-console, request-body, response-body, or Playwright trace
listeners. Native diagnostics emitted directly by a browser/driver dependency
and every possible audit/evidence value have not been exhaustively reviewed;
keep supervised pilot and log review as release gates. Do not add candidate
answers, tokens, full email bodies, CV text, browser storage state, or
secret-bearing URLs to logs, application timelines, source errors, test
snapshots, or bug reports.

## Backup contents and handling

The Settings export uses SQLite's online backup API for a consistent snapshot.
Its ZIP manifest lists each included relative path, byte size, and SHA-256 hash.
The allowlist contains only `database.sqlite3` and regular files under
`storage/uploads`, `storage/generated`, `storage/archive`, and
`storage/applications`. This includes the answer bank and submission evidence
inside SQLite, generated/tailored CVs, uploaded source CVs, and encrypted mailbox
token ciphertext. It excludes `.env` and the token encryption key, browser login
cookies/session state, and arbitrary files elsewhere in the storage root.

The ZIP is wrapped in a passphrase-derived AES-256-GCM envelope (`.sampobak`),
using scrypt and a random salt/nonce. The Settings export requires the passphrase
twice; CLI backup prompts without echoing it and CLI restore prompts once. At
least 12 characters are required. The passphrase is never stored and cannot be
recovered; store it separately from the archive. CVs, answers, application
history, and evidence remain sensitive personal data even when encrypted.
SampoAgent keeps no additional server backup and does not automatically expire
or delete the downloaded file; its retention is controlled by the person who
saved it.

## Restore and deletion

Stop the SampoAgent server and worker before restore or deletion. CLI restore and
erase operations use cross-process database/storage lock files so they fail
closed when the normal `sampoagent run`, `automate`, or `browser-login` process
is active. Lock files are small sibling coordination files and intentionally
remain after an operation to avoid lock-file replacement races.

Restore requires the exact `RESTORE LOCAL SAMPOAGENT DATA` confirmation. It
verifies ZIP structure, supported manifest version, canonical allowlisted paths,
file sizes and hashes, safe regular-file entries, and SQLite integrity/core
schema before staging anything. It replaces the selected database and the four
managed document folders; it does not replace the existing browser profile, `.env`,
or unrelated storage files. Re-login may be required. The operation stages old
state and rolls back on a filesystem failure; if rollback itself fails, it
preserves the named recovery directories for manual recovery rather than
silently deleting them.

`erase-local-data` requires the exact `ERASE ALL LOCAL SAMPOAGENT DATA` string.
It deletes only the explicitly selected database plus its SQLite `-wal`/`-shm`
sidecars, and the SampoAgent-owned `uploads`, `generated`, `archive`,
`applications`, and `browser-profile` children. It leaves the storage root,
unrelated sibling files, `.env`, downloaded exports, and runtime-lock files.
Deleting the database removes mailbox token ciphertext; the separate key file
is not deleted. Any downloaded copy must be removed separately.

The commands and their scope are documented in README's “Local backup,
restore, and deletion” section. Never run them against a database or storage
directory unless you have checked the exact paths.

## Source and browser controls

Automated public source adapters require HTTPS, public DNS answers, bounded
responses/timeouts, redirect revalidation, and robots checks where applicable.
Browser-only and denied sources are not fetched. The Playwright request guard
blocks cross-origin requests and preflights same-origin requests, but Chromium
resolves names again after preflight; this is not a DNS-rebinding-proof network
sandbox. Do not describe browser automation as production-ready until OS-level
or proxy egress pinning and supported-employer staging tests pass.

See root [SECURITY.md](../SECURITY.md), [README](../README.md), and the staged
[automation design](superpowers/specs/2026-09-29-full-application-automation-design.md).
