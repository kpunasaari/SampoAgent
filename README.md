# SampoAgent — Open-Source Job Application & Career Agent

SampoAgent is an open-source AI job application tool and career agent that discovers relevant jobs, matches them to your skills and experience, tailors ATS-friendly CVs, assists with application forms, and tracks job applications.

It is a local-first job search agent, ATS CV/resume tailoring tool, job application tracker, and safe foundation for job application assistance. It is not a service that invents qualifications, bypasses CAPTCHAs, or promises hiring outcomes.

SampoAgent is designed for any candidate. Finland and Finnish/English workflows are the initial focus; country-specific job sources and rules are modular. The repository includes a reusable Codex skill named **SampoAgent** and smaller workflow skills under `.agents/skills/`.

## Requirements

- Python 3.11 or later
- Windows, macOS, or Linux
- No Docker, hosted database, or AI API key required for local use

## Quick start

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
sampoagent run
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
sampoagent demo
sampoagent run
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The service intentionally binds only to localhost.

`SampoAgent run` also supervises the local application worker. It starts only when Smart Approval or a valid Full Autopilot grant is active, Dry Run is off, the daily limit is positive, and the emergency stop is clear. The worker stops when those conditions no longer hold and is shut down with the app. The standalone `sampoagent automate --watch` command remains available for headless/manual operation.

`demo` loads clearly marked synthetic data. To start with an empty local profile instead, run `sampoagent init` and then `sampoagent run`. Use `sampoagent --help` for available commands.

## First-run workflow

1. Enter your own profile; a clean install never inserts a fictional candidate or job.
2. Upload a PDF, DOCX, or TXT CV and review every extracted claim and dated work/education draft before using it. OCR is an optional local extra; if unavailable, the app asks for text entry instead of guessing.
3. Confirm skills and experience; SampoAgent recommends roles but never activates them without your choice.
4. Review career suggestions, explicitly activate the roles you want to pursue, then find matching jobs for those roles and saved filters.
5. Configure thresholds, daily application limit, email connection, and application mode.
6. Start in Dry Run; it never writes candidate data into an employer form or makes final submissions.

### Application modes and browser automation

- **Review Everything** is the default; SampoAgent prepares and tracks applications but does not submit them.
- **Smart Approval** shows the exact destination, form answers, and CV SHA-256 before asking you to approve that package. It does not enter candidate data into the employer form until the package hash is approved. Any changed form, answer, listing or attachment invalidates that approval.
- **Full Autopilot** is an optional local-worker grant: the user must deliberately enable it for up to 30 days, set a positive daily limit and select at least one target occupation, career profile or explicit search term. The grant fingerprints those roles, candidate answers, source policy/URLs, filters and quota; every queued job is rechecked against that scope before the browser opens. A role/source change invalidates the grant. Submission is limited to supported, verified forms and confirmed facts. CAPTCHA, login/MFA, unknown or conflicting answers, legal/medical/criminal/identity/immigration declarations and unsupported forms still require the user.

Full Autopilot does not interrupt the user for each eligible application. A CAPTCHA creates a durable task in the CAPTCHA Queue; the candidate opens the official page and handles that application themselves, one at a time. Other eligible applications may continue. The dashboard shows how many CAPTCHA tasks need attention. A CAPTCHA or unsupported form is never bypassed.

CAPTCHA handoff always opens the verified job's original public HTTPS application URL; browser-observed redirects are never used as links. Existing queued tasks are also displayed from that verified URL, and no link is shown if it is no longer safe. The candidate can still record a manually completed task if an old link is no longer safe; the record does not invent a confirmation-page URL.

If a supported form has a required, otherwise-unresolved low/medium-risk scalar field (for example a start date or shift choice), the local Applications page can ask the candidate for that exact answer. The candidate must confirm it; the response is stored only for that application and exact form/listing snapshot, is not added to the reusable answer bank or candidate facts, and is requested again if the form or verified listing changes. After the final missing answer, the item returns to the queue and the existing mode, grant, quota and live-form checks decide what can happen next. This does not approve a Smart Approval package or grant any new submit authority. Optional fields, conflicting answers, high-impact declarations, CAPTCHA, authentication and unsupported controls stay on the manual path.

Multi-step forms have a limited Full Autopilot path: up to eight distinct same-origin pages, with one accessible form and one clearly identified Next/Continue control on each intermediate page. The system rechecks confirmed answers and their visible values on every page; the final page must have one unique Submit control, and only that page may request a single clearly labelled CV. Smart Approval and Dry Run stop before candidate data is entered because they cannot review the complete package in advance. Next/Continue may save candidate information page by page; if a later page presents a CAPTCHA, unknown answer or unsupported control, earlier entries may already be stored by the employer. This limitation is included in the explicit Full Autopilot grant, and older grants are invalidated so the user must re-authorize under the updated policy. These limits do not certify any specific employer ATS; staging tests and a supervised pilot remain release gates.

The app-managed local worker processes eligible applications serially (one active submission at a time). It starts from the localhost app lifecycle only while the saved mode and controls allow it, uses a separate SQLite connection, and owns its persistent visible browser on the worker thread. It records a persistent heartbeat, latest result and next run; the queue shows age and hold reason, while sources show last check and retry time. Successful source checks wait at least 60 seconds before polling again; failures use persisted backoff from one minute to a six-hour cap. These controls are not multi-worker scaling or proof of live ATS compatibility. The supported browser extra and Chromium must still be installed for form handling.

Full Autopilot is local availability, not a cloud or always-on service: it runs only while the machine is awake and the app and interactive user session are available. Existing queued work resumes on the next polling cycle after wake or network recovery, after authorization is checked again; source discovery continues to honor per-source cooldowns. A browser initialization or worker-cycle failure closes the potentially stale browser and retries after the normal poll interval. Any interrupted/uncertain web or email submission is reconciled as `UNKNOWN` and never retried. SampoAgent does not install an operating-system login startup task; any future startup integration must be a separate explicit opt-in.

The application questionnaire is a local draft until each answer is explicitly confirmed. Confirmed reusable answers carry a stable field ID, provenance, scope and optional expiry. Employer-only reuse is currently available only for clearly mapped salary, notice-period, start-date and shift answers, and only for the exact employer. Vacancy-specific motivation, assessment, adjustment, demographic and privacy answers are not reusable profile answers. Declined, unknown, conflicting, expired, or unmapped answers are not used to fill forms; old answers with missing country scope are preserved and held for reconfirmation. Answers collected from a live employer form use a separate application-local store and are never promoted into reusable profile answers. When confirmed CV facts conflict with an answer, the affected application field pauses and the application page links to both evidence views for review; no source silently wins.

### Optional occupation and skills catalogue

The built-in occupation list is only a small fallback. To use the broader ESCO occupation/skill classification, download a CSV package yourself from the [European Commission ESCO download page](https://esco.ec.europa.eu/en/use-esco/download), selecting the version, `classification`, the language files you need (for example `fi` and `en`), and CSV. ESCO currently emails the requested download link after its privacy/licence form; SampoAgent does not automate that request or send your email to ESCO.

After extracting the package, import only the selected files into the local database:

```powershell
sampoagent import-esco "C:\path\to\extracted-esco" --database sampoagent.db --version 1.2.1 --languages fi en
```

The import is local and atomic. SampoAgent stores the dataset version, languages, source-file SHA-256, source URL, reuse statement, required European Commission attribution and the notice that it builds a derived matching index. Imported taxonomy files are not included in the repository or uploaded. The Career Suggestions page explains that ESCO skill links are not legal licensing or education requirements; Finland's initial country pack separately shows official-source authority-check prompts for selected health/social-care, early-childhood-education and private-security titles. Those prompts are not eligibility decisions. Only a role you explicitly activate becomes a job-search target.

Install the optional visible browser adapter with `pip install -e ".[browser]"` and `playwright install chromium`. Sign in to employer sites manually inside the local persistent browser profile. SampoAgent does not bypass access controls and does not claim that every ATS is supported. The browser sends HTTPS through an authenticated ephemeral loopback CONNECT proxy: application mode is restricted to the inspected origin, DNS answers are checked as a whole, and the proxy connects to a validated numeric IP while leaving TLS end-to-end. Cross-origin application traffic, WebSockets, and insecure URLs are blocked. This application-layer proxy is not an OS-level egress sandbox; Chromium fallback/direct-egress behavior still needs supported-platform verification. No Laura, ReachMee, or Likeit staging E2E has been completed, so the system is not production-validated for universal Full Autopilot. The local worker's scoped Autopilot grant is separate from Codex-assisted applications; Codex still requires explicit confirmation for each final submission under this repository's agent instructions.

### Optional email connection and application outbox

The inbox connection can connect Gmail or Microsoft Outlook to find likely job-application replies. It reads a bounded number of message headers/snippets and never sends, moves, or deletes mail. A match is only a suggestion: the application tracker changes after you select the related application and explicitly confirm the outcome.

Outgoing application email is a separate, optional integration. The send permission is distinct from inbox-read scopes: Gmail requests `gmail.send`; Microsoft requests delegated `Mail.Send`; both request only the OIDC identity scopes `openid email` needed to identify the sender. The existing read token is never silently upgraded. SampoAgent fetches identity from the provider's fixed HTTPS UserInfo endpoint; it requires a stable subject and valid address. Google addresses must have `email_verified=true`. A Microsoft email claim is labelled provider-reported, not independently verified, and may be absent, in which case that connection is not enabled. The OIDC address/subject and encrypted send token are stored locally. Multiple accounts can be connected; the selected default sender is shown in Email settings. Legacy send tokens without identity are kept locally but cannot send until reconnect; disconnect-all removes them.

In Review mode, the candidate still copies/confirms the recipient and reviews the exact sender, recipient, subject, body and archived PDF before one manual send. Full Autopilot email submission is a second, separate, time-limited grant. Under that grant, the worker can create and send an email application with no per-job prompt only if the current verified listing itself explicitly says to apply by email, one unique address appears in the bounded instruction context, and the generated package needs no additional material beyond the candidate's confirmed name, the reviewed job title/employer and the archived job-specific PDF. Address extraction is deterministic (Finnish, English and Swedish); no AI guess or extra page crawl is used. Multiple/missing/contradictory addresses, unsupported additional application requirements, stale verification, missing confirmed facts/CV, missing sender or expired consent hold that posting for review and never fall back to a browser submission. Ordinary web-form jobs retain their existing form flow. The encrypted outbox binds exact From identity, To address and evidence, subject/body/language, listing snapshot, candidate/search scope and CV checksum; `EMAIL_READY` prevents a browser path for the same posting.

Neither mode sends follow-ups, accepts offers, or claims delivery. Direct attachments are limited to 2 MiB PDFs. Gmail success and Outlook `202 Accepted` are recorded as provider acceptance only. Timeout, process interruption or any uncertain provider result becomes `UNKNOWN` and is never automatically retried; reconcile the Sent folder before taking any manual action. A definite provider rejection also consumes its one attempt. Real Gmail/Graph OAuth or delivery is not validated; tests use mocked provider responses only.

Before connecting, configure the provider OAuth app and copy `.env.example` to a private local `.env` file. Register the exact callback URL for the local server (`http://127.0.0.1:8765/email/callback/gmail` or `/microsoft`) and set `SAMPOAGENT_TOKEN_ENCRYPTION_KEY` to a Fernet key. The key protects mailbox tokens stored in SQLite; keep it private and backed up. If the key is lost, disconnect the mailbox and authorize it again. Secrets are never entered into the UI or committed to Git.

Google inbox reading requests `gmail.readonly`; Microsoft inbox reading requests delegated `Mail.Read`, `User.Read` and offline refresh access. Sending adds OIDC `openid email` to the separate `gmail.send` or delegated `Mail.Send` permission; it does not add Gmail mailbox-read, Microsoft `Mail.Read`, `User.Read` or profile scopes. Provider references: [Google OpenID Connect UserInfo](https://developers.google.com/identity/openid-connect/reference), [Microsoft OIDC scopes](https://learn.microsoft.com/en-us/entra/identity-platform/scopes-oidc), and [Microsoft UserInfo](https://learn.microsoft.com/en-us/entra/identity-platform/userinfo). A real connection requires your own provider registration and user consent. Synthetic tests do not verify real OAuth consent, provider sender behavior, delivery, Sent-folder reconciliation, employer ATS staging or production safety; do not treat them as a pilot.

To preview clearly synthetic example data instead, run `sampoagent demo` once before `sampoagent run`.

### Local backup, restore, and deletion

The Settings page offers a ZIP export. The archive contains a transactionally
consistent SQLite snapshot (profile, answer bank, applications, submission
evidence, and encrypted mailbox-token ciphertext) and the managed `uploads`,
`generated`, `archive`, and `applications` files. Browser login cookies and the
separate `.env` encryption key are excluded; after restore you may need to sign
in to employer sites again, and the original `SAMPOAGENT_TOKEN_ENCRYPTION_KEY`
is required to use the restored mailbox connection. The downloaded `.sampobak`
envelope is encrypted using a passphrase-derived AES-256-GCM key; the passphrase
is never stored and cannot be recovered if lost. Use at least 12 characters and
keep it separately from the backup file. SampoAgent does not keep or automatically
delete a second backup copy; downloaded files remain under your control.

Create a backup without changing or migrating the selected database:

```powershell
sampoagent backup --database sampoagent.db --storage-dir application_data --output "$env:USERPROFILE\Documents\sampoagent-local-backup.sampobak"
```

Restore and permanently erase require SampoAgent to be stopped and an exact
confirmation string. Restore validates the archive paths, per-file SHA-256
checksums, and SQLite integrity before replacing the selected database and
managed data directories. It preserves the current browser profile. Erasure
removes the selected database (including SQLite WAL/SHM files) and SampoAgent's
managed data folders, including the browser profile; it does not remove `.env`,
downloaded backups, unrelated files under the storage directory, or the small
sibling runtime-lock files.

```powershell
sampoagent restore --database sampoagent.db --storage-dir application_data --archive "$env:USERPROFILE\Documents\sampoagent-local-backup.sampobak" --confirm "RESTORE LOCAL SAMPOAGENT DATA"
sampoagent erase-local-data --database sampoagent.db --storage-dir application_data --confirm "ERASE ALL LOCAL SAMPOAGENT DATA"
```

## Current V1 scope

- Finland country pack with a built-in source catalog. Each source is labeled with its real discovery capability; protected sources remain browser-only.
- Optional Scrapling adapter for static public pages using JSON-LD JobPosting data or a user-provided job-card CSS selector; robots policy and access-control failures are respected, terms URL review must be explicitly recorded, and requests are rate limited.
- Landing CV upload with Finnish/Swedish/English section parsing, structured dated employment/education drafts, PDF page/line excerpt provenance, conservative two-column and hyphen-wrap handling, and explicit claim/conflict review. Optional local Tesseract OCR is user-selectable; OCR claims remain unconfirmed.
- Separate Finnish and English CV labels and template selection.
- Deterministic matching, duplicate detection, language detection, hard blockers, explainable three-part scoring.
- Explainable matching and CV fit use confirmed structured work, education and credentials as well as confirmed facts. CV-extracted drafts and conflicts do not affect ranking, satisfy hard credentials or enter a generated CV.
- Confirmed questionnaire skill lists are provenance-linked into candidate facts; transferable/hobby abilities can inform role recommendations without being represented as professional work history in generated CVs.
- Optional multilingual ESCO CSV import for broad occupation/skill recommendations; local version/hash metadata and required attribution are visible. Suggestions do not start searches until explicitly selected.
- Confirmed-fact-only PDF CV output and ATS text re-parse validation.
- Explicit answer-bank provenance and scope checks, including limited exact-employer reuse; unknown required low/medium scalar fields can be answered through an application-local, confirmed form on the Applications page and are never promoted to reusable candidate answers. FI/SV/EN ambiguous or high-risk fields stay manual, and confirmed CV/answer conflicts pause the affected field with links to both evidence records.
- Job-specific CV selection: reuse an archived file only when its reviewed SHA-256 still matches, role and language fit, evidence match is at least 85%, and all detected hard requirements are present. Otherwise generate and archive a readable, paginated CV from confirmed facts, prioritizing job-relevant supported skills.
- Explainable application learning: only candidate-recorded interview, assessment, offer, rejection, or no-response outcomes count. After five such outcomes, a small bounded signal may break ties among already-qualified CVs; it cannot invent facts or override a hard requirement. Metrics are feedback signals, not hiring probabilities.
- Opt-in Codex learning side task: sharing is off by default. At the start of each SampoAgent job/CV/application task, Codex reads `py -m sampoagent learning-summary --database <exact-active-db>` inside the current task only if the active database is already identified and Settings consent is enabled. The command is read-only and independently checks consent; if the path is unknown or consent is off, Codex continues without it and never searches for databases. Output is limited to role-family/CV-language groups with at least five confirmed outcomes, excludes candidate facts, CV text, identifiers, employer/job details, free text, paths and checksums, and can only break ties among already-qualified options. No detached Codex task or automation is launched.
- Provider-neutral browser-agent interface, optional visible Playwright browser, exact-package approval, field-value/CV checksum readback, and a safe unconfigured fallback. Form snapshots include accessible prompts, descriptions, select/radio options, origin/action, declared file constraints and native input constraints (`min`, `max`, `step`, `pattern`, `minlength`, `maxlength`). Application-local question answers are bound to that schema; numeric/date bounds and steps are checked locally, and Playwright rechecks native browser validity after every fill. Radio groups fill only on exact option match; checkbox groups remain manual. Multi-step forms are supported only in Full Autopilot, for at most eight distinct same-origin pages with one accessible form and one unique Next/Continue control per intermediate page; Smart Approval and Dry Run stop before candidate data entry. Ambiguous multi-form, unsupported, or dynamic pages remain manual. The adapter rechecks live field values and selected-file hashes before each Next/Continue and the final click. Synthetic browser tests do not certify Laura, ReachMee, Likeit, or any employer ATS; staging validation remains open.

The end-to-end automation assessment, design decisions, open safety gates and phased implementation checklist are in the [design specification](docs/superpowers/specs/2026-09-29-full-application-automation-design.md) and [implementation plan](docs/superpowers/plans/2026-09-29-full-application-automation.md). The plan is not a claim that every ATS is supported or that production pilot gates have passed.

## Interface

SampoAgent uses a responsive dark interface with a persistent sidebar,
explicit safety states, keyboard-visible focus, and local server-rendered
forms. It works without a Node or browser build step.

## Codex skill

When you open this repository in Codex, the project skill at [`.agents/skills/sampoagent/SKILL.md`](.agents/skills/sampoagent/SKILL.md) is available as **SampoAgent**. It guides job discovery, explainable assessment, truthful CV tailoring, form assistance, and application tracking, and links to the focused skills in `.agents/skills/`.

To use the skill across other projects, copy `.agents/skills/sampoagent/` into your personal Codex skills folder (`%USERPROFILE%\.codex\skills\sampoagent` on Windows, or `$CODEX_HOME/skills/sampoagent` / `~/.codex/skills/sampoagent` on macOS and Linux), then restart or refresh Codex. The skill contains no candidate profile or personal application data. In this repository it can use candidate records only when they are explicitly available through SampoAgent; outside it, provide the records needed for the task.

## Safe use and integrations

- Candidate records and uploaded CVs stay in local SQLite and local application storage by default. Keep local databases, uploads, credentials, and generated personal documents out of commits.
- Review extracted CV facts and confirm them before they are used as application claims. Missing or conflicting facts require the candidate's answer.
- Job-source availability depends on each source's terms and access rules. The app does not bypass login, CAPTCHA, robots rules, rate limits, or other access controls.
- Live form submission requires a compatible configured browser and an explicit application mode. Smart Approval requires exact-package approval; local Full Autopilot requires a separate scoped, expiring grant. Codex-assisted submission still requires job-specific confirmation.
- Demo content is synthetic; do not mistake it for a real candidate profile or live job listing.

## Privacy and limitations

Candidate data is stored in local SQLite. No AI key is required: Minimal deterministic mode is the default. Live job discovery requires a permitted public feed or an activated official API; browser-only sources remain personalized links. Gmail/Outlook OAuth credentials are supplied by the operator, and mailbox tokens are encrypted before local persistence. Final web-form submission may require user action for authentication, CAPTCHAs, or high-risk questions.

See [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md), and [docs/CLAUDE_INTEGRATION.md](docs/CLAUDE_INTEGRATION.md). Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Please report security issues according to [SECURITY.md](SECURITY.md).

Scrapling is an external BSD-3-Clause dependency; this project uses its static parser only. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Licence

Apache-2.0. Copyright 2026 Kai Punasaari. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Application profile questionnaire

Open `/onboarding` (also available at `/landing`) for 63 optional application
preparation questions in seven sections. Save each section before navigating away.
Answers remain local drafts; review them and your CV facts before using them in
applications. The final review step includes local CV upload. English, Finnish,
and Swedish skill, language, licence, certificate, work and education sections are
parsed into unconfirmed claims and structured dated records with page/line evidence
when available. Review the claims and resolve conflicts in Profile, then choose
desired roles under Career Suggestions; a suggested or inferred role never starts
job discovery until you activate it as a target. Scanned PDFs can use the optional
local OCR extra (`pip install -e ".[ocr]"` plus a local Tesseract installation);
if OCR is disabled or unavailable, the app asks for manual text entry instead.
Employer-specific declarations and permissions are asked separately.
After reviewing candidate facts, open `/onboarding/ready` for a single review of
CV evidence, skill-backed role suggestions, selected target roles, locations,
source coverage, application mode, daily quota, and stop rules. Saving this scope
keeps Dry Run on and starts no search. A separate Jobs action starts discovery;
enabling Smart Approval or Full Autopilot requires its own explicit live-mode
choice, and Full Autopilot also requires a separate 30-day scoped authorization.
Research and scope: [questionnaire research](docs/research/2026-09-29-application-questions.md).
