# SampoAgent engineering rules

## Offline occupation suggestions — 2026-09-30

The built-in fallback matcher provides FI/EN skill aliases for 24 representative roles across multiple sectors. It is only a starter catalogue; never describe it as all occupations or as a qualification/eligibility check. Users may import their own downloaded ESCO CSV files into the local index for broader occupation coverage through Career Suggestions ZIP upload or the CLI directory importer. The app never submits the ESCO download email form; validate ZIP paths/size, extract only requested CSVs to a temporary directory, preserve the old index on failure, and keep the dataset out of Git. Do not send candidate skills to the public ESCO API. Recommendations never activate a search target automatically.

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
attribution metadata, and never bundles or uploads dataset files. The Career Suggestions
page accepts a user-downloaded ESCO ZIP after local CSRF validation; it rejects unsafe
archive paths, limits uploaded/expanded data, and deletes temporary CSVs after import.
CV uploads parse
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

The guided unified onboarding result/review screen is implemented at
`/onboarding/ready`; GET is read-only and a separate action is required to save
scope or enable Full Autopilot. Still open: broader official qualification
coverage across Finland's regulated occupations and additional countries, and
mapping confirmed preferences into all matching/search consumers.

Preserve local-first operation, candidate-fact provenance, and explicit user control. Never fabricate candidate claims or bypass access controls. Keep deterministic work deterministic and consult focused instructions in `.agents/skills/` for agent-assisted workflows.

For job-search and application tasks in this repository, use `.agents/skills/sampoagent/SKILL.md` as the entry point and load only the relevant focused skills alongside it. Use candidate records only when explicitly available through the app or a user-identified store. Final application submission requires a review and explicit confirmation for that specific application.

## Application automation update — 2026-09-29

## CAPTCHA queue, CV integrity, and learning — 2026-09-29

- CAPTCHA tasks accumulate durably and are handled individually by the candidate. The dashboard count links to the queue; the UI exposes a start action only for the next waiting item, hides employer links for all waiting tasks, then offers the next item and its link after the active task is completed. The database also serializes concurrent starts/completions across SQLite connections. Handling one CAPTCHA does not stop the worker from processing other eligible jobs.
- In local Full Autopilot, no per-job approval prompt is shown for a supported application inside the active grant. Missing/unknown facts, CAPTCHA, sign-in, high-risk declarations, stale/unsupported listings and forms are held for user action rather than bypassed.
- Full Autopilot does not ask per-job questions for missing low/medium-risk answers: it holds only that application before entering candidate data and keeps processing other eligible jobs. Application-scoped answer controls remain available only inside a collapsed, user-opened manual-review disclosure; a confirmed answer stays bound to that application and exact form/listing snapshot.
- Full Autopilot requires an explicit selected occupation/career profile or search term; each queued job is matched against the active role scope, and career-profile/source policy/URL changes invalidate the expiring grant. The implementation maximum is 30 days.
- `/onboarding/ready` is the effective onboarding review surface: it validates role selections against current suggestions/saved roles, summarizes evidence, search scope, quota and stop rules, and keeps GET side-effect free. `save_review` keeps Dry Run on and revokes old Autopilot permission; a separate explicit enable action and live-mode confirmation are required. Full Autopilot needs a separate unchecked-by-default 30-day authorization box.
- Archived CV files are re-hashed before reuse, including a manually selected archived file. A mismatch excludes the artifact. Automatic reuse requires current role/language fit of at least 85% and all detected hard requirements; otherwise a new one-column, paginated PDF is generated only from confirmed profile information, re-parsed and archived.
- Candidate-confirmed interview, assessment, offer, rejection, and no-response outcomes immediately feed a small Beta-prior-shrunk local role score and checksum-linked CV selection signal. CV effects only break ties among already-qualified documents within five fit points of the best current fit; neither signal changes candidate facts or hard requirements. A submission receipt, CAPTCHA hold, unknown result, or unconfirmed silence is not hiring feedback.
- The SampoAgent Codex skill consults a separate opt-in, read-only, de-identified aggregate as a side task inside the active job/CV/application task, only when the exact active database is already known and Settings consent is enabled. Shared cohorts remain hidden below five confirmed outcomes; Codex may explain patterns or break ties but must not mutate candidate facts or create detached work.
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
- Browser-adapter result strings are untrusted: failure diagnostics are never persisted verbatim, successful receipts use fixed generic text, and only short reference-shaped IDs are retained. Never include page text, candidate data, credentials, provider exceptions or raw form values in logs/timelines.
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
- Multi-step form V1 is supported only under a current Full Autopilot grant: at most eight distinct same-origin pages, one accessible form and one unambiguous Next/Continue control per intermediate page. Each page re-resolves confirmed data and checks risk, scope, validation and live value readback; only the final page may contain one explicitly identified CV upload. Smart Approval and Dry Run hold before candidate data entry because the complete package is not available up front. Next/Continue can save prior-page data on the employer side; if a later page needs review, earlier entries may remain saved. That behavior is stated in the grant UI. A new grant-policy version invalidates grants created before this disclosure.
- Multiple form contexts, cross-origin actions, CAPTCHA, sign-in, high-risk or unresolved required fields, repeated pages, ambiguous navigation and unsupported/dynamic controls remain manual. Unsupported tasks leave the queue for review rather than guessing.
- CV uploads check the detected CV field, employer-declared MIME/extension and explicit size limit when present. Browser snapshot signatures bind these constraints, and actual uploaded bytes are hashed again immediately before the only final click.
- Before each Next/Continue and final Submit, the browser re-reads the current page signature and every prepared field value; a mismatch prevents the click. Intermediate Next results are never treated as a final application receipt and are not automatically replayed after an uncertain transition.
- These are synthetic local-browser tests only; no Laura, ReachMee, Likeit, employer staging account or live application has been tested.

## Worker fencing and operator visibility — 2026-09-29

- SQLite `application_claims.job_id` is the canonical-job unique guard. Before inspecting an employer form, the application runner atomically transitions `READY` → `PREPARING` with a random owner token; a second connection cannot open the same application, and a stale worker cannot reserve a submit attempt after recovery changed that token.
- Startup recovery resets only abandoned `PREPARING` items. `SUBMITTING` attempts are instead changed to `UNKNOWN` / `DO_NOT_RETRY`. A new `CANCELLED` attempt is permitted only where the browser adapter has explicitly returned before the final click; this reservation does not consume the daily limit.
- The browser adapter runs the centralized pause/authorization/quota/listing gate after re-reading the form and upload hashes and immediately before clicking. Any unverified prefilled optional value on an employer page pauses before overwriting or submitting it.
- Discovery is sequential, checks at most eight automatic sources per cycle, and now stores exponential source-failure backoff (60 seconds doubling to a six-hour cap); a successful feed/API check clears the backoff. Browser-only sources remain unfetched.
- Dashboard and queue pages expose persistent worker status/heartbeat/next run, queue age, wait reason and per-source latest result/retry time. The default CLI watch loop remains a single local worker.
- Remaining limits: configurable parallel per-employer execution is not enabled, cancel cannot abort a currently hung third-party browser navigation instantly, and ATS behavior has only been checked against synthetic local Chromium fixtures. Browser requests now use the pinned-IP proxy described below; OS-level egress/fallback verification, employer staging, and pilot gates remain open. See the staged plan/spec under `docs/superpowers/`.

## App-managed automation worker — 2026-09-29

- `sampoagent run` attaches one lifecycle-managed local worker. It starts only for Smart Approval or an effective Full Autopilot grant, with Dry Run off, a positive daily cap, and emergency stop disabled. Autopilot still requires the profile/preferences-bound, expiring grant.
- The worker opens an independent SQLite connection for each cycle and owns the persistent visible Playwright browser on its own thread. It reconciles on settings save and pause/resume, rechecks current controls between cycles and at the final submit boundary, and is asked to stop when the app shuts down.
- `create_app()` does not start a background worker unless explicitly configured; synthetic tests inject a fake lifecycle worker/cycle. The standalone `sampoagent automate --watch` remains supported.
- A long-running navigation cannot be forcibly cancelled mid-call; its final submit remains blocked if authorization changes. A transient worker-cycle or browser initialization failure closes the possibly stale browser, reports `recovering`, waits the normal polling interval, and retries only after rechecking current authorization. Existing queue state survives sleep, network interruption, and application restart: abandoned pre-submit preparation can recover; interrupted web/email sending becomes `UNKNOWN` and is never replayed. Full Autopilot only runs while the app and interactive user session are available. No OS login startup task is installed; any future startup integration needs a separate explicit opt-in.
- Focused controller/lifecycle verification: controller/UI/CLI tests passed (31 application-automation/controller checks plus 6 worker/UI/CLI lifecycle checks); the upstream Starlette/httpx TestClient deprecation warning remains. Latest whole-suite verification: `py -m pytest -q` → 358 passed (2026-09-29).
- The application runner fingerprints candidate facts/records/answers, selected targets/sources, preferences, mode and quota before employer navigation. It rechecks after loading, before each field and attachment, and immediately before the submit click. This prevents stale pre-navigation answers from being used after a candidate change, including when a fresh Autopilot grant was issued during that navigation.

## Error and logging boundaries — 2026-09-29

- Do not interpolate exception messages, request URLs, submitted values, CV content, OAuth tokens, or local paths into errors, audit/status fields, or logs.
- The local `sampoagent run` server disables Uvicorn access logs to avoid recording OAuth callback query codes. Unhandled HTTP failures return a generic response and log only a random reference plus exception type; no traceback/message is logged by the app boundary.
- Source failures, worker status, and automation CLI errors use fixed summaries. Suppress exception chaining at CLI boundaries so Python does not print the original private exception.
- `activity_log` accepts only fixed event codes and stores a generic privacy detail; repository reads mask any legacy detail values rather than rendering them. Settings offers an exact-phrase, local-token-protected action to replace legacy detail fields while preserving event codes/timestamps. Existing exported backups are user-managed and are not retroactively changed.
- Synthetic PII/secret regression cases are in `tests/test_security_boundaries.py`. This does not de-identify intentionally stored local candidate/employer evidence; native Playwright/browser diagnostics and all historical evidence values still require supervised review before pilot.

## Source policy and browser request guard — 2026-09-29

- Scrapling public-page sources require an explicit recorded review of a terms-of-use URL. Changing a previously reviewed source or terms URL invalidates that review; this records the user's decision and is not automated legal interpretation. Robots.txt remains enforced, and browser-only sources are never fetched automatically.
- Successful automated source checks persist a 60-second minimum interval; failures retain exponential cooldown. Source capability, terms status and retry state are exposed in the local Sources screen.
- The visible Playwright context blocks service workers and WebSockets. During form handling it blocks cross-origin HTTP(S) requests before dispatch. All browser HTTPS traffic is configured through an authenticated, ephemeral loopback CONNECT proxy: application mode allows only the exact opened host/port, strictly parses CONNECT authority syntax, resolves once, rejects any mixed/private DNS answer set, and dials a validated numeric IP without terminating TLS. Proxy close terminates active tunnels and invalidates delayed DNS work; an unexpected Playwright context close also closes the proxy. The manual sign-in CLI can opt out of same-origin restriction but remains limited to public HTTPS port 443 through the same resolver/pinning policy.
- This application-layer proxy is not OS-level egress containment. A synthetic Windows test now checks the current Chromium build against upstream CONNECT failure and proxy shutdown, but other supported platforms/browser builds and OS-level egress policy remain unverified. User-controlled adapter staging E2E, supervised redaction review, and pilot are also release gates. Do not call browser automation universally production-ready or claim untested ATS support.

## Codex outcome-learning handoff — 2026-09-30

- Sharing is disabled by default and controlled in Settings. At the start of a SampoAgent job-discovery, job-analysis, CV-tailoring, application-preparation or application-tracking task, Codex reads the digest inside the current task when (and only when) the exact active database is already known and Settings consent is enabled. It runs `py -m sampoagent learning-summary --database <exact-active-db>`; the command is read-only and independently enforces consent. A similarly named file is not evidence that it is active. If the active database is unknown, absent, or sharing is off, continue without the digest; never search for databases, inspect tables directly, or ask solely for learning context. Do not create detached Codex tasks or automations.
- Output is limited to role-family and CV-language aggregates with at least five confirmed outcomes. It excludes candidate facts, CV content, direct identifiers, employer/job titles/locations, free text, paths, and checksums. Reuse one digest across a batch; refresh before a later batch only after the user confirms new outcomes.
- Only candidate-recorded interview, assessment, offer, rejection, and no-response statuses influence outcome signals. Application receipt, submission, CAPTCHA, unknown and unconfirmed silence do not. The digest is not a hiring probability or a per-CV identity/ranking feed; the local engine retains its checksum-linked CV selection signal.
- Focused consent/read-only/redaction/outcome verification passed (9 tests); final whole-suite result is recorded in the current implementation plan. No production candidate database, employer site, or application was read or changed in this increment.

## Local data lifecycle — 2026-09-29

- Settings exports a consistent SQLite snapshot plus only `uploads`, `generated`, `archive`, and `applications`; browser-profile cookies and `.env` are excluded. OAuth token ciphertext stays encrypted in the database and needs the separately preserved encryption key after restore.
- The `.sampobak` ZIP envelope uses a scrypt-derived AES-256-GCM key; Settings asks twice and CLI prompts without echo, requires 12+ characters, and never stores the passphrase. It is unrecoverable if lost. Downloaded backups are user-managed and have no automatic retention/deletion policy.
- Restore verifies archive paths, hashes, and SQLite integrity before staging, requires the app stopped and the exact CLI confirmation, replaces only the selected database/managed document folders, preserves the existing browser-profile, and attempts rollback on filesystem failure.
- Explicit erase removes the selected database and SQLite sidecars plus SampoAgent-owned data folders including browser-profile; it leaves `.env`, exports, unrelated storage-root files, and stable sibling lock files. CLI server, worker, browser-login, restore and erase share cross-process locks.
- Verification uses synthetic temporary state only. TestClient validates the Settings export; no real candidate database, browser session, employer site, or application was opened. Full desktop UI smoke, supervised redaction audit, OS-level egress/fallback enforcement, ATS staging E2E, and end-to-end pilot remain open release gates. The >20 synthetic form-shape matrix exists but does not qualify live ATS adapters; no public “universally automatic” claim.

## Email Autopilot identity and recipient routing — 2026-09-30

- This section supersedes the earlier same-day email-outbox progress notes in the design/implementation history. Inbox read scopes remain unchanged; send OAuth adds only `openid email` plus Gmail `gmail.send` or Microsoft `Mail.Send`. Sender identity comes from fixed official UserInfo endpoints. Google `email_verified` must be true; Microsoft addresses are explicitly labeled provider-reported. Tokens remain encrypted; multiple accounts can be connected, one visible default is selected, and identity-less legacy send tokens remain recoverable but cannot send until reconnected.
- Local Full Autopilot may route an application to email only when the exact current verified job description has one deterministic contextual application-by-email instruction in Finnish, English or Swedish and a single unique valid address. A general contact address does not count. A bounded deterministic parser records cue ID and listing snapshot hash; it does not use AI, browse for more addresses, or log raw excerpts. Missing/ambiguous recipients, extra required materials, stale listings, missing confirmed facts/CV, sender or grant are held for review, never redirected to the browser path.
- A generated encrypted outbox binds exact From account/subject/address, To and its source, subject/body/language, listing snapshot, candidate/search scope and job-specific archived PDF checksum. Manual mode still requires an application-specific exact-package confirmation. Full Autopilot uses the separate time-limited email-send grant and does not prompt per eligible job. One daily slot and one provider attempt are reserved atomically; ambiguous outcomes remain `UNKNOWN`/`DO_NOT_RETRY`. `ACCEPTED` means provider acceptance, not delivery.
- Tests use synthetic postings and mocked OAuth/provider HTTP only. No live OAuth account, email, candidate database or employer was accessed. Actual user-controlled OAuth/send, delivery/Sent-folder reconciliation, OS-level egress/fallback enforcement, employer staging, supervised review of historical/native browser diagnostics, and the supervised pilot remain hard release gates; no universal automation claim. V1 local availability and recovery behavior are defined above, but actual Windows sleep/wake testing remains unverified.

## Local availability and recovery — 2026-09-30

- Full Autopilot is only available while the machine is awake and the local app and interactive user session are running; there is no installed OS startup task or cloud worker. Settings and README state this contract.
- After a transient browser initialization or worker-cycle exception, the controller closes the possibly stale browser, keeps the authorized worker in `recovering`, waits the configured polling interval, and reopens only after re-reading current permissions. The durable queue resumes one cycle at a time; discovery still obeys per-source cooldown.
- Recovery never replays an interrupted final web submit or email send: existing repository recovery changes those attempts to `UNKNOWN`/`DO_NOT_RETRY`. Synthetic recovery/UI/controller regression tests passed; no actual Windows sleep/wake, network-device transition, or Chromium direct-fallback experiment was run.
- A synthetic Chromium employer-form E2E now covers required fields that are naturally invalid before filling, exact CV bytes received by a loopback HTTPS fixture, one POST and same-origin receipt; all non-fixture browser requests are blocked. The fixture does not qualify any real ATS/employer adapter; employer-controlled staging remains open.
- Playwright's pinned HTTPS proxy explicitly supplies Chromium's `<-loopback>` bypass-subtraction rule. The synthetic employer E2E now routes through that CONNECT proxy; Chromium's test-only hostname rule prevents fixture egress if the proxy is unavailable, and an attempted reload after proxy shutdown fails without a second fixture request. This is isolated Chromium evidence, not OS-level egress or platform-wide direct-fallback certification.
- Latest proxy/CV queue verification: focused application/Playwright/proxy suite 64 passed; `py -m pytest -q` → 468 passed, one existing upstream Starlette/httpx deprecation warning; compileall, CLI help and `git diff --check` exited successfully.
- CAPTCHA task completion is transactional across local DB connections. `finish_captcha_task` obtains an immediate write transaction before re-reading task state, so only one concurrent final outcome can complete and only one manual receipt is recorded. Regression coverage synchronizes two SQLite connections at the transaction boundary. Latest full suite after this hardening: 469 passed; focused automation-controls/worker/application suite: 58 passed; one existing Starlette/httpx deprecation warning.
- Latest verification for the combined availability + synthetic browser-form increment: focused application/Playwright suite 48 passed; `py -m pytest -q` → 467 passed, one upstream Starlette/httpx TestClient deprecation warning. `py -m compileall -q sampoagent`, `py -m sampoagent --help`, and `git diff --check` all exited successfully.
- Follow-up found and fixed an automatic CV-preparation queue defect: the lightweight jobs list omitted the verified-snapshot hash, so the queue now resolves each job through the authoritative repository view before applying the verified-listing gate. The regression test and loopback HTTPS Chromium E2E cover automatic CV selection/generation from confirmed synthetic facts through exact archived-PDF upload. Verification: focused CV/application/worker/Playwright suite 69 passed; full suite 468 passed (one upstream Starlette/httpx deprecation warning); compileall, CLI help and diff check passed. No live candidate record, employer or application was used; real ATS staging and remaining release gates are still open.

## Application-local missing answers — 2026-09-30

- A supported form's required, unresolved LOW/MEDIUM scalar fields are now asked on the local Applications page. The candidate confirms each answer explicitly; visible prompts and options are escaped as untrusted employer data, and select/radio/date/email/number values receive server-side validation.
- Form snapshots use schema version 1.2 and bind native `min`, `max`, `step`, `pattern`, `minlength`, and `maxlength` constraints into the semantic signature and application-only answer record. Numeric/date ranges, steps and text lengths are validated locally; the Playwright adapter also calls the live control's native `checkValidity()` after filling so employer-specific patterns and other browser constraints fail closed before a step or final click.
- Responses live in an application-specific SQLite table bound to the application ID, semantic form signature and verified job snapshot. They never enter the global answer bank, confirmed facts, generated CVs, activity/timeline notes, or opt-in outcome-learning digest. A changed field or listing snapshot cannot resolve with an old response.
- Completing the pending question set returns the application to READY; it does not approve a Smart Approval package or modify the Full Autopilot grant. The worker rechecks the live form and follows the selected mode, scoped grant, quota and existing safety gates. CAPTCHA/authentication, HIGH-risk, conflicting, optional, blank-prompt and unsupported questions remain manual.
- Single-page and multi-step synthetic Full Autopilot tests cover resuming after a confirmed application-only answer. UI tests cover HTML escaping, CSRF, explicit confirmation, exact options, text/email/number/date controls, native constraint preservation and application-scoped storage. No actual candidate record, employer form, external email account or live application was accessed.
- Added design and execution documents: `docs/superpowers/specs/2026-09-30-application-scoped-missing-answers-design.md` and `docs/superpowers/plans/2026-09-30-application-scoped-missing-answers.md`. Verification: `py -m pytest -q` → 503 passed with one upstream Starlette/httpx deprecation warning; compileall, CLI help and `git diff --check` passed. No live candidate data or employer was used. Employer ATS staging, OS-level egress verification and supervised pilot remain open release gates.

## Source-category preference enforcement — 2026-09-30

- Public-sector and recruitment-agency choices now use one deterministic category predicate across source discovery, each imported job, job-list matching, queue eligibility, and the final Autopilot gate. Jobs snapshot their source category; initialization backfills only from extant source records, and category history survives source deletion. Legacy sourced jobs with an unknown category are held whenever either category is opted out rather than guessed.
- Test-first verification reproduced existing-job, per-result discovery, and deleted-source persistence gaps. Focused matching/discovery/preferences/application suite: 59 passed. Full suite: `py -m pytest -q` → 542 passed with one existing Starlette/httpx TestClient deprecation warning; `compileall`, CLI help, and `git diff --check` passed.
- Monthly salary matching now handles only explicit monthly EUR figures; only an offer whose stated maximum is below the user's floor is excluded. Ambiguous, missing, non-EUR, hourly and annual values remain visible and are never converted by assumption. Radius filtering still needs geocoded/normalized distance data. No candidate database or employer site was accessed. Employer ATS staging, supported-platform OS egress/fallback tests, supervised privacy review, and supervised pilot remain open release gates.

## Monthly salary preference matching — 2026-09-30

- The saved minimum monthly salary is now applied centrally to discovered and existing listings, queue eligibility and the final Autopilot policy gate. A local deterministic parser accepts only an explicit EUR currency and an explicit monthly period in English, Finnish or Swedish; ranges and “from / up to” qualifiers retain their one-sided meaning. Hourly/annual/non-EUR/unstated/contradictory amounts are not compared.
- Only a stated upper bound strictly below the preference floor filters a posting. A range crossing the threshold and uncomparable pay remain listed; the Jobs table shows the parsed monthly-EUR value or “Not stated or not comparable”. Settings explains the scope, so missing salary is not treated as invented data.
- TDD covered range parsing, qualifier handling, multilingual cases, unknown pay visibility, Jobs filtering, and a Full Autopilot pre-browser hold for a known-low offer. Full suite: `py -m pytest -q` → 546 passed with one existing Starlette/httpx TestClient deprecation warning; compileall, CLI help and `git diff --check` passed. Radius preference still needs reliable geographic coordinates/distance and remains future work. No candidate database or employer site was accessed.

## CAPTCHA handoff origin hardening — 2026-09-30

- CAPTCHA tasks always link to the candidate-reviewed public HTTPS application URL, never the browser-observed redirect. Legacy active tasks are projected through the reviewed URL as well; if that URL is no longer safe, the UI omits the link but still lets the candidate record the outcome. Manual CAPTCHA reports do not claim a confirmation URL that was not captured.
- Test-first verification reproduced bad CAPTCHA redirects in both regular and multi-step runner paths, unsafe legacy-link handling, false confirmation-URL labeling, and unsafe current links preventing durable CAPTCHA tasks. Focused regressions passed. Full suite: `py -m pytest -q` → 514 passed with one existing upstream Starlette/httpx deprecation warning; compileall, CLI help and `git diff --check` passed. Synthetic browser tests did not use an employer site or candidate database.

## CAPTCHA challenge recognition — 2026-09-30

- A real-Chromium synthetic regression reproduced that a Cloudflare Turnstile iframe could pass as an ordinary application page: the selector recognized reCAPTCHA/hCaptcha but not the Cloudflare challenge-platform iframe. The browser now detects common CAPTCHA/challenge iframe, title, widget, hidden-response and Cloudflare managed-challenge markers before form filling; visible “checking your browser” text and challenge-platform paths are also held.
- Detection remains heuristic, not exhaustive. It never solves a challenge; recognized CAPTCHA results use the existing durable, one-at-a-time user queue, and worker processing continues for other eligible applications.
- TDD: both the managed-challenge detector test and real-Chromium Turnstile iframe test failed before implementation and passed after. Full suite: `py -m pytest -q` → 547 passed, one upstream Starlette/httpx TestClient deprecation warning. No employer, candidate database or live submission was used. Employer staging, cross-platform egress, supervised privacy review and supervised pilot remain open release gates.

## Chromium proxy failure-path check — 2026-09-30

- On Windows with Playwright Chromium 149.0.7827.55, a synthetic HTTPS host mapped only to 127.0.0.1 was tested through the production proxy configuration. A stubbed upstream TCP failure produced a browser navigation error without reaching the fake employer directly; after closing the loopback proxy, a second navigation also failed with zero direct GETs. The resolver/connector call assertions confirm the first navigation actually traversed the pinned proxy.
- Focused proxy/adapter/application verification: 86 passed; full suite: `py -m pytest -q` → 522 passed with one existing Starlette/httpx deprecation warning. Compileall, CLI help and `git diff --check` passed. The new browser test is local-only; it does not prove OS firewall policy, machine-wide egress containment, other browser/platform combinations, ATS staging, or pilot readiness.
