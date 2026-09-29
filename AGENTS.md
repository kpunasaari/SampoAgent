# SampoAgent engineering rules

## Questionnaire update — 2026-09-29

`candidate/questions.py` defines 63 optional reusable prompts and nine per-application
topics. `app/onboarding.py` serves `/onboarding` and `/landing`, with local SQLite
draft storage, section validation and review. The final review embeds CV upload
and explains claim review and career selection. Drafts are
not confirmed facts or submission authority. Research scope is documented in
`docs/research/2026-09-29-application-questions.md`; regression tests are in
`tests/test_questionnaire.py` and `tests/test_answer_provenance.py`. Explicitly
selected answers now receive stable question IDs and safety metadata in the local
answer bank. Confirmed technical/practical/people/creative skills are materialized
as source-linked candidate facts; other transferable skills inform role suggestions
without becoming professional CV skills. Recommendations are not search targets
until the user activates a role. The optional ESCO CSV importer keeps the selected
multilingual catalogue locally, stores version/source SHA-256/licence and required
attribution metadata, and never bundles or uploads dataset files. CV uploads parse
common Finnish/English section headings and keep extracted claims unconfirmed with
source page/line spans in SQLite and Profile. Common Finnish, Swedish and English
application-form labels map only when their meanings are unambiguous; employer-only
reuse is limited to mapped salary, notice-period, start-date and shift answers. A
CV/questionnaire disagreement blocks only the affected field and the Applications
page links to both evidence views. Vacancy-specific motivation, assessment,
adjustment, demographic and privacy prompts remain non-reusable; high-risk ones
stay manual. Scanned PDFs can use optional local Tesseract OCR selected in
Settings; if the provider is unavailable, no candidate facts are created and the
user gets a manual-entry step. Work/education history is stored as dated,
source-linked drafts and requires explicit confirmation; field conflicts preserve
existing confirmed data until the user chooses how to resolve them. PDF layout
heuristics only split clear two-column pages and otherwise fall back to ordinary
extraction.

Still open: broader official qualification coverage across Finland's regulated
occupations and additional countries, a guided unified onboarding result/review
screen, and mapping confirmed preferences into all matching/search consumers.

Preserve local-first operation, candidate-fact provenance, and explicit user control. Never fabricate candidate claims or bypass access controls. Keep deterministic work deterministic and consult focused instructions in `.agents/skills/` for agent-assisted workflows.

For job-search and application tasks in this repository, use `.agents/skills/sampoagent/SKILL.md` as the entry point and load only the relevant focused skills alongside it. Use candidate records only when explicitly available through the app or a user-identified store. Final application submission requires a review and explicit confirmation for that specific application.

## Application automation update — 2026-09-29

## CAPTCHA queue, CV integrity, and learning — 2026-09-29

- CAPTCHA tasks accumulate durably and are handled individually by the candidate. The dashboard count links to the queue. Handling one CAPTCHA does not stop the worker from processing other eligible jobs.
- In local Full Autopilot, no per-job approval prompt is shown for a supported application inside the active grant. Missing/unknown facts, CAPTCHA, sign-in, high-risk declarations, stale/unsupported listings and forms are held for user action rather than bypassed.
- Full Autopilot requires an explicit selected occupation/career profile or search term; each queued job is matched against the active role scope, and career-profile/source policy/URL changes invalidate the expiring grant. The implementation maximum is 30 days.
- `/onboarding/ready` is the effective onboarding review surface: it validates role selections against current suggestions/saved roles, summarizes evidence, search scope, quota and stop rules, and keeps GET side-effect free. `save_review` keeps Dry Run on and revokes old Autopilot permission; a separate explicit enable action and live-mode confirmation are required. Full Autopilot needs a separate unchecked-by-default 30-day authorization box.
- Archived CV files are re-hashed before reuse, including a manually selected archived file. A mismatch excludes the artifact. Automatic reuse requires current role/language fit of at least 85% and all detected hard requirements; otherwise a new one-column, paginated PDF is generated only from confirmed profile information, re-parsed and archived.
- Candidate-recorded outcomes (interview, assessment, offer, rejection, no response) are linked to the exact CV checksum. At least five outcomes are required before a small capped outcome signal can influence selection among near-equal, already-qualified CVs. A sent application, CAPTCHA hold, or uncertain result is not a positive outcome and cannot change candidate claims.
- The SampoAgent Codex skill may review these local outcome summaries on a later explicitly started SampoAgent task and suggest evidence-backed improvements; it must not create a detached task, infer candidate facts, or mutate the profile without explicit authorization/confirmation.
- Reusable questionnaire answers now carry a stable field ID, typed value, sensitivity, source reference, explicit state, confirmation timestamp, country/employer scope fields, and optional expiry. Unknown/declined/conflicting/expired answers are excluded from automatic resolution; legacy answers with an unrecorded work-country scope are preserved but held for reconfirmation.
- Explicitly confirmed application-profile skill lists are split only on clear list separators and linked back to the answer record. Professional skills feed CV and role suggestions; hobby/transferable skills feed role suggestions only. Neither skill-derived suggestions nor historic job titles activate searches until a target is selected. Reconfirming an answer deactivates facts generated from its superseded version.
- ESCO is optional and user-downloaded: `sampoagent import-esco <directory> --version <version> --languages fi en` indexes selected CSVs locally. Preserve the European Commission attribution, reuse statement, modified-index notice, imported version and source SHA-256. Never commit downloaded ESCO datasets; occupation skill matches are indicators, not legal qualification rules.
- Candidate answers are versioned: reconfirming a value supersedes the previous active value without deleting its history. Conflicting user-confirmed values for the same stable field and scope are held until explicitly resolved.
- These outcome signals are stored locally and are available to future application preparation. They describe recorded history and are not predictions of hiring success.
- Explainable job ranking and CV reuse-fit now also consider only `CONFIRMED` structured work/education/language/availability records. Explicitly confirmed certificate/licence records can satisfy only an exact matching parsed credential requirement. Draft/conflict/rejected history never raises the job score or enters a generated CV.

- The local product has four distinct controls: Dry Run, Review Everything, Smart Approval (exact package hash review before any candidate data is entered), and Full Autopilot (explicit 30-day scoped grant plus positive daily limit).
- This product setting does not authorize the Codex assistant itself to submit an application. Agent-assisted final submissions still require job-specific user confirmation.
- Smart Approval binds destination/listing data, form signature, prepared answers and attachment name/SHA-256. Immediately before its one submit click, the browser adapter must re-read the live form's actual selected-file name/checksum and compare the exact expected upload set; any mismatch is a known pre-submit stop, never a click or an uncertain submission.
- CAPTCHA, authentication/MFA, unknown/conflicting facts, high-impact legal/medical/criminal/identity/immigration declarations, unsupported forms and uncertain post-submit results remain manual; ambiguous submissions are never retried.
- Verified gaps and the staged implementation plan: `docs/superpowers/specs/2026-09-29-full-application-automation-design.md` and `docs/superpowers/plans/2026-09-29-full-application-automation.md`. Do not describe the system as production-ready until the synthetic ATS matrix, staging adapter checks, mail-send permission flow, backup/restore and pilot gates pass.

## CV structure and country guidance — 2026-09-29

- CV extraction recognizes Finnish, Swedish and English section headings and creates repeated, dated experience/education drafts. Swedish aliases, a synthetic two-column PDF and an explicitly hyphen-wrapped job title are tested; ambiguous page geometry falls back to ordinary extraction.
- Optional local Tesseract OCR can be selected in Settings. OCR remains local, page-linked and unconfirmed; missing OCR raises a clear manual-entry step without fabricating candidate facts.
- Profile supports per-record confirmation/rejection and explicit conflict choices; a conflict does not silently replace confirmed history. Generated CVs can include confirmed dates, organizations, locations and titles.
- The Finland country pack shows sourced authority-check prompts for selected regulated health/social-care, early-childhood-education and private-security titles. These prompts are not legal hard gaps or eligibility decisions. Other jurisdictions and full regulated-profession coverage remain future work; do not generalize a Finland rule or infer legal requirements from ESCO skill gaps.
- Full local verification after this increment: `py -m pytest -q` → 317 passed. One Starlette/httpx TestClient deprecation warning comes from an upstream dependency.

## ATS form snapshots — 2026-09-29

- `FormSchema` v1.1 hashes the public page origin, same-origin form action, navigation checkpoint, field identity/name, accessible label source, group prompt, descriptions, requiredness, options, autocomplete, accepted file types, declared size limit and multiple-file semantics. One uploaded CV cannot satisfy a multiple-file input.
- Playwright reads associated labels, `aria-labelledby`, `aria-describedby`, fieldset legends and visible names; it never uses coordinate guesses. Exact-matching radio options may be selected from confirmed answers. Multi-checkbox controls and legal/privacy/assessment fields remain unresolved for the candidate.
- A multi-step indicator/Next control, multiple form contexts or cross-origin action is a pre-entry hold. Multi-step navigation is not yet implemented; these cases are not advertised as supported ATS flows. Unsupported tasks leave the queue for review without candidate values entered.
- CV uploads check the detected CV field, employer-declared MIME/extension and explicit size limit when present. Browser snapshot signatures bind these constraints, and actual uploaded bytes are hashed again immediately before the only final click.
- These are synthetic local-browser tests only; no Laura, ReachMee, Likeit, employer staging account or live application has been tested.

## Worker fencing and operator visibility — 2026-09-29

- SQLite `application_claims.job_id` is the canonical-job unique guard. Before inspecting an employer form, the application runner atomically transitions `READY` → `PREPARING` with a random owner token; a second connection cannot open the same application, and a stale worker cannot reserve a submit attempt after recovery changed that token.
- Startup recovery resets only abandoned `PREPARING` items. `SUBMITTING` attempts are instead changed to `UNKNOWN` / `DO_NOT_RETRY`. A new `CANCELLED` attempt is permitted only where the browser adapter has explicitly returned before the final click; this reservation does not consume the daily limit.
- The browser adapter runs the centralized pause/authorization/quota/listing gate after re-reading the form and upload hashes and immediately before clicking. Any unverified prefilled optional value on an employer page pauses before overwriting or submitting it.
- Discovery is sequential, checks at most eight automatic sources per cycle, and now stores exponential source-failure backoff (60 seconds doubling to a six-hour cap); a successful feed/API check clears the backoff. Browser-only sources remain unfetched.
- Dashboard and queue pages expose persistent worker status/heartbeat/next run, queue age, wait reason and per-source latest result/retry time. The default CLI watch loop remains a single local worker.
- Remaining limits: configurable parallel per-employer execution is not enabled, cancel cannot abort a currently hung third-party browser navigation instantly, and ATS behavior has only been checked against synthetic local Chromium fixtures. See the staged plan/spec under `docs/superpowers/`.

## App-managed automation worker — 2026-09-29

- `sampoagent run` attaches one lifecycle-managed local worker. It starts only for Smart Approval or an effective Full Autopilot grant, with Dry Run off, a positive daily cap, and emergency stop disabled. Autopilot still requires the profile/preferences-bound, expiring grant.
- The worker opens an independent SQLite connection for each cycle and owns the persistent visible Playwright browser on its own thread. It reconciles on settings save and pause/resume, rechecks current controls between cycles and at the final submit boundary, and is asked to stop when the app shuts down.
- `create_app()` does not start a background worker unless explicitly configured; synthetic tests inject a fake lifecycle worker/cycle. The standalone `sampoagent automate --watch` remains supported.
- A long-running navigation cannot be forcibly cancelled mid-call; its final submit remains blocked if authorization changes. App restart is required after an unexpected worker/browser initialization failure.
- Focused controller/lifecycle verification: controller/UI/CLI tests passed (31 application-automation/controller checks plus 6 worker/UI/CLI lifecycle checks); the upstream Starlette/httpx TestClient deprecation warning remains. Latest whole-suite verification: `py -m pytest -q` → 358 passed (2026-09-29).
- The application runner fingerprints candidate facts/records/answers, selected targets/sources, preferences, mode and quota before employer navigation. It rechecks after loading, before each field and attachment, and immediately before the submit click. This prevents stale pre-navigation answers from being used after a candidate change, including when a fresh Autopilot grant was issued during that navigation.

## Error and logging boundaries — 2026-09-29

- Do not interpolate exception messages, request URLs, submitted values, CV content, OAuth tokens, or local paths into errors, audit/status fields, or logs.
- The local `sampoagent run` server disables Uvicorn access logs to avoid recording OAuth callback query codes. Unhandled HTTP failures return a generic response and log only a random reference plus exception type; no traceback/message is logged by the app boundary.
- Source failures, worker status, and automation CLI errors use fixed summaries. Suppress exception chaining at CLI boundaries so Python does not print the original private exception.
- Synthetic PII/secret regression cases are in `tests/test_security_boundaries.py`. This does not de-identify intentionally stored local candidate/employer evidence; native Playwright/browser diagnostics and all historical evidence values still require supervised review before pilot.

## Source policy and browser request guard — 2026-09-29

- Scrapling public-page sources require an explicit recorded review of a terms-of-use URL. Changing a previously reviewed source or terms URL invalidates that review; this records the user's decision and is not automated legal interpretation. Robots.txt remains enforced, and browser-only sources are never fetched automatically.
- Successful automated source checks persist a 60-second minimum interval; failures retain exponential cooldown. Source capability, terms status and retry state are exposed in the local Sources screen.
- The visible Playwright context blocks service workers and WebSockets. During form handling it blocks cross-origin HTTP(S) requests before DNS resolution, then preflights same-origin destinations for HTTPS and all-answer public IPv4/IPv6 resolution. DNS failures and mixed public/private answers block the request. The manual sign-in CLI can opt out of same-origin restriction, but keeps the public HTTPS/DNS guard.
- This is defense in depth, not a DNS-rebinding-proof egress sandbox: Chromium resolves the host again after preflight, and browser DNS lookup egress is not fully constrained at the application layer. Do not call browser automation production-ready or expand supported ATS claims until an IP-pinning proxy/OS egress control and user-controlled adapter staging E2E are verified.

## Codex outcome-learning handoff — 2026-09-29

- Sharing is disabled by default and controlled in Settings. `py -m sampoagent learning-summary --database <active-local-db>` opens only the explicitly identified existing database read-only; it never initializes/migrates the file, searches for other databases, or starts a background Codex task.
- Output is limited to role-family and CV-language aggregates with at least five confirmed outcomes. It excludes candidate facts, CV content, direct identifiers, employer/job titles/locations, free text, paths, and checksums. Codex must use only this command and only after the Settings consent is visibly enabled; the command independently enforces the same gate.
- Only candidate-recorded interview, assessment, offer, rejection, and no-response statuses influence outcome signals. Application receipt, submission, CAPTCHA, unknown and unconfirmed silence do not. The digest is not a hiring probability or a per-CV identity/ranking feed; the local engine retains its checksum-linked CV selection signal.
- Focused consent/read-only/redaction/outcome verification passed (9 tests); final whole-suite result is recorded in the current implementation plan. No production candidate database, employer site, or application was read or changed in this increment.

## Local data lifecycle — 2026-09-29

- Settings exports a consistent SQLite snapshot plus only `uploads`, `generated`, `archive`, and `applications`; browser-profile cookies and `.env` are excluded. OAuth token ciphertext stays encrypted in the database and needs the separately preserved encryption key after restore.
- The `.sampobak` ZIP envelope uses a scrypt-derived AES-256-GCM key; Settings asks twice and CLI prompts without echo, requires 12+ characters, and never stores the passphrase. It is unrecoverable if lost. Downloaded backups are user-managed and have no automatic retention/deletion policy.
- Restore verifies archive paths, hashes, and SQLite integrity before staging, requires the app stopped and the exact CLI confirmation, replaces only the selected database/managed document folders, preserves the existing browser-profile, and attempts rollback on filesystem failure.
- Explicit erase removes the selected database and SQLite sidecars plus SampoAgent-owned data folders including browser-profile; it leaves `.env`, exports, unrelated storage-root files, and stable sibling lock files. CLI server, worker, browser-login, restore and erase share cross-process locks.
- Verification uses synthetic temporary state only. TestClient validates the Settings export; no real candidate database, browser session, employer site, or application was opened. Full desktop UI smoke, redaction audit, 20+ form matrix, ATS staging E2E, egress sandbox, and end-to-end pilot remain open release gates; no public “universally automatic” claim.

## Separate email-send outbox — 2026-09-30

- Inbox read scopes remain unchanged. Sending uses a separate Gmail `gmail.send` or Microsoft delegated `Mail.Send` OAuth connection; the previous read token is never silently expanded. OAuth cancellation leaves both connections unchanged.
- An email application draft requires a current verified posting, the candidate's explicit confirmation that the recipient was copied from that posting, an application-specific archived PDF, and a separate send connection. The encrypted outbox binds recipient, subject/body/language, role/employer/job fingerprint, send-account connection fingerprint, candidate/search scope and PDF name/hash.
- Preparing an email draft moves that application to `EMAIL_READY`, removing it from the browser-form worker queue to avoid two channels for one vacancy. A user can inspect the exact package, manually confirm a single send, or separately enable a 30-day email-Autopilot grant bound to the general Autopilot scope and send connection. Unchecking, pausing, scope changes, grant expiry, or disconnect revoke/disable it.
- One transaction reserves one daily slot and one provider attempt. `ACCEPTED` means provider accepted, not delivered; timeout, interrupted process, 408/5xx or other ambiguous result becomes `UNKNOWN` and is never auto-retried. Gmail/Graph responses, token refresh and send were tested only with mocks; no real mail was sent.
- Outgoing sender-account email is not surfaced under the current least-privilege send scopes, each job's email address is still copied/confirmed by the candidate, and provider Sent-folder reconciliation is not automated. These are explicit limitations; do not describe email applications as zero-touch or provider-validated. The current direct file-attachment path caps archived PDFs at 2 MiB to stay below Graph's one-call attachment limit.
