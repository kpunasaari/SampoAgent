# Email Autopilot sender identity and automatic recipient plan

> **For Codex:** REQUIRED SUB-SKILL: Follow superpowers:executing-plans; execute this plan task-by-task with TDD and record progress here.

**Goal:** Support multiple visibly identified email send accounts and enable local Full Autopilot to prepare/send application-email jobs without per-job questions when the fresh verified listing has one deterministic, contextual recipient.

**Architecture:** Extend the current local FastAPI/SQLite OAuth and encrypted outbox. Add fixed-provider OIDC UserInfo lookup, account records/default selection, identity/snapshot-bound packages and grants, a deterministic FI/EN/SV recipient parser, and a worker branch that atomically diverts explicit email applications away from browser submissions. Never add mailbox-read scopes to send permission or retry ambiguous provider results.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, pytest, stdlib urllib, cryptography Fernet.

**Design:** [2026-09-30 email-autopilot-identity-and-recipient-design.md](../specs/2026-09-30-email-autopilot-identity-and-recipient-design.md)

---

### Task 1: Provider identity under minimal OIDC scopes

**Files:** `sampoagent/integrations/email_oauth.py`, `tests/test_email_oauth.py`

**Tests first:** Assert exact Gmail send scopes `openid email gmail.send`, Microsoft send scopes `openid email offline_access Mail.Send`; read scopes remain unchanged. Mock fixed UserInfo calls. Require valid subject and email; reject Google `email_verified=false`, malformed/missing values, provider mismatch, unsafe redirects and provider errors without leaking body/token data.

**Implementation:** Add provider-specific send-scope helper and fixed endpoint identity retrieval with existing HTTPS same-host redirect policy. Return only normalized provider, subject, address and explicit `email_verified`/provider-reported status. No external HTTP in tests.

**Verify:** `py -m pytest -q tests/test_email_oauth.py`.

### Task 2: Multi-account local records, default selection and legacy fail-closed migration

**Files:** `sampoagent/db/repository.py`, `sampoagent/app/main.py`, `tests/test_email_send.py`, `tests/test_email_autopilot_settings.py`

**Tests first:** Legacy singleton credential is not returned by new send lookup; new identity-bearing accounts coexist; provider+subject reconnect preserves one row but rotates `connected_at`; default selection is explicit; disconnecting one account revokes the scoped grant and cannot remove unrelated accounts; disconnect-all removes both new and legacy records. Existing databases upgrade additively.

**Implementation:** Add `mail_send_accounts` and connection methods; leave legacy singleton contents intact but unused for sends until reconnect. Default to the first newly established account only when no default exists. Bind Autopilot fingerprint to default account identity; validate OIDC and send scopes. Add CSRF-protected account selector and per-account disconnect controls. Show the exact provider-returned sender address with accurate verification label; allow another account for same provider.

**Verify:** `py -m pytest -q tests/test_email_send.py tests/test_email_autopilot_settings.py`.

### Task 3: Sender-bound outbox and explicit From review

**Files:** `sampoagent/db/repository.py`, `sampoagent/integrations/outbox.py`, `sampoagent/app/main.py`, `tests/test_email_send.py`

**Tests first:** Manual packages bind selected account ID/subject/address and show From; changing default/account invalidates pre-existing READY package before network; token refresh preserves identity; old outboxes without account binding cannot send; displayed content HTML-escapes address. Existing manual recipient confirmation remains required.

**Implementation:** Add nullable account ID migration to outbox; include sender identity in encrypted package/hash. Make verifier resolve exact account ID rather than current implicit connection. Show From and recipient provenance for READY and terminal packages. Account-bound refresh updates ciphertext only.

**Verify:** Focused email tests; `git diff --check`.

### Task 4: Deterministic contextual recipient extraction

**Files:** create `sampoagent/integrations/email_recipient.py`; create `tests/test_email_recipient.py`

**Tests first:** FI/EN/SV explicit application-email cues accept one nearby address; ordinary contact addresses are rejected; multiple/contradictory addresses, malformed addresses, excessive distance and unsupported language return a typed hold reason. Include Unicode/HTML-noise and punctuation cases. No model/provider dependency.

**Implementation:** Use bounded, curated cue patterns and strict email validation; return only the selected address plus stable cue ID/provenance hash. No raw listing excerpt in logs or activity details.

**Verify:** `py -m pytest -q tests/test_email_recipient.py`.

### Task 5: No-prompt eligible email package creation and worker routing

**Files:** `sampoagent/integrations/outbox.py`, `sampoagent/applications/worker.py`, `sampoagent/applications/packages.py` only if needed, `sampoagent/db/repository.py`, `tests/test_email_send.py`, new `tests/test_email_autopilot_recipient.py`

**Tests first:** Under full + separate email grants, dry-run off, current job verification, authorized role, confirmed data and managed archived PDF, worker auto-creates one email outbox, sends once and never calls browser. No address, ambiguous cue, stale snapshot, no account, missing grant, ineligible PDF or quota holds only that email task and proceeds to other tasks. Non-email listings still reach browser. Queue/email claims race in two database connections without dual submission.

**Implementation:** Add a separate autopilot package path that never accepts caller-provided recipients and never uses the manual confirm flag; require `_active_verified_job`, deterministic listing extraction, account grant, exact candidate/scope package and job-specific archived PDF. Worker routes explicit email-cue jobs before browser processing; missing/ambiguous email route is held, not browser-fallback. Existing atomic outbox reservation enforces daily cap and one provider attempt. Preserve no retries.

**Verify:** `py -m pytest -q tests/test_email_autopilot_recipient.py tests/test_email_send.py tests/test_email_autopilot_settings.py`.

### Task 6: UI/docs, integration matrix and full verification

**Files:** `sampoagent/app/main.py`, `README.md`, `AGENTS.md`, design/implementation docs, `tests/test_email_send.py`, `tests/test_security_boundaries.py`

**Tests first:** Settings show exact default sender and identity status, multiple account add/select/disconnect, legacy reconnect notice, and separate time-limited send grant. Applications page exposes From/To, verified-listing provenance, source snapshot/package hashes and truthful ACCEPTED/UNKNOWN states. XSS escapes identity/listing strings. Assert UI text distinguishes provider acceptance from delivery. No secret/PII in logs.

**Implementation:** Update user-facing privacy and least-privilege notes, migration/reconnect behavior, full automation limitations and exact remaining release gates. Do not claim live testing.

**Final checks:** `py -m pytest -q`; `py -m compileall -q sampoagent`; `py -m sampoagent --help`; `git diff --check`; final fresh-context branch review. Commit and push the completed branch to the authorized GitHub branch only after all checks pass. Do not mark the overarching goal complete while the independent release gates in the design remain open.

**Progress:** Tasks 1–6 implementation and focused tests are complete. UI now shows provider-returned sender identity/status, supports explicit multi-account selection and disconnect, warns about legacy reconnect, and exposes From/To plus recipient evidence for email packages. README, AGENTS.md and the overarching design/plan were aligned to the new rules. Final verification: `py -m pytest -q` → 442 passed, one existing upstream Starlette/httpx deprecation warning; `py -m compileall -q sampoagent`, CLI help and `git diff --check` passed. Independent read-only code review found no remaining actionable findings after fixes for negated instructions, ambiguous/malformed recipient text, contact-section boundaries and common FI/EN/SV instruction forms. No OAuth, email, employer site or real application was used. Release gates in the design remain open.
