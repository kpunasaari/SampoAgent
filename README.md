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

Full Autopilot does not interrupt the user for each eligible application. A CAPTCHA creates a durable task in the CAPTCHA Queue; the candidate opens the official page and handles that application themselves, one at a time. Other eligible applications may continue. The dashboard shows how many CAPTCHA tasks need attention. A CAPTCHA or unsupported/unknown form is never bypassed.

The app-managed local worker processes eligible applications serially (one active submission at a time). It starts from the localhost app lifecycle only while the saved mode and controls allow it, uses a separate SQLite connection, and owns its persistent visible browser on the worker thread. It records a persistent heartbeat, latest result and next run; the queue shows age and hold reason, while sources show last check and retry time. Successful source checks wait at least 60 seconds before polling again; failures use persisted backoff from one minute to a six-hour cap. These controls are not multi-worker scaling or proof of live ATS compatibility. The supported browser extra and Chromium must still be installed for form handling.

The application questionnaire is a local draft until each answer is explicitly confirmed. Confirmed reusable answers carry a stable field ID, provenance, scope and optional expiry. Employer-only reuse is currently available only for clearly mapped salary, notice-period, start-date and shift answers, and only for the exact employer. Vacancy-specific motivation, assessment, adjustment, demographic and privacy answers are not reusable profile answers. Declined, unknown, conflicting, expired, or unmapped answers are not used to fill forms; old answers with missing country scope are preserved and held for reconfirmation. When confirmed CV facts conflict with an answer, the affected application field pauses and the application page links to both evidence views for review; no source silently wins.

### Optional occupation and skills catalogue

The built-in occupation list is only a small fallback. To use the broader ESCO occupation/skill classification, download a CSV package yourself from the [European Commission ESCO download page](https://esco.ec.europa.eu/en/use-esco/download), selecting the version, `classification`, the language files you need (for example `fi` and `en`), and CSV. ESCO currently emails the requested download link after its privacy/licence form; SampoAgent does not automate that request or send your email to ESCO.

After extracting the package, import only the selected files into the local database:

```powershell
sampoagent import-esco "C:\path\to\extracted-esco" --database sampoagent.db --version 1.2.1 --languages fi en
```

The import is local and atomic. SampoAgent stores the dataset version, languages, source-file SHA-256, source URL, reuse statement, required European Commission attribution and the notice that it builds a derived matching index. Imported taxonomy files are not included in the repository or uploaded. The Career Suggestions page explains that ESCO skill links are not legal licensing or education requirements; Finland's initial country pack separately shows official-source authority-check prompts for selected health/social-care, early-childhood-education and private-security titles. Those prompts are not eligibility decisions. Only a role you explicitly activate becomes a job-search target.

Install the optional visible browser adapter with `pip install -e ".[browser]"` and `playwright install chromium`. Sign in to employer sites manually inside the local persistent browser profile. SampoAgent does not bypass access controls and does not claim that every ATS is supported. During form handling, cross-origin HTTP(S) requests are blocked before DNS resolution, same-origin requests get an all-answer public-DNS preflight, and WebSockets are blocked. Chromium still resolves names again; this is not a DNS-rebinding-proof network sandbox. No Laura, ReachMee, or Likeit staging E2E has been completed, so the system is not production-validated for universal Full Autopilot. The local worker's scoped Autopilot grant is separate from Codex-assisted applications; Codex still requires explicit confirmation for each final submission under this repository's agent instructions.

### Optional email connection and application outbox

The inbox connection can connect Gmail or Microsoft Outlook to find likely job-application replies. It reads a bounded number of message headers/snippets and never sends, moves, or deletes mail. A match is only a suggestion: the application tracker changes after you select the related application and explicitly confirm the outcome.

Outgoing application email is a separate, optional integration. It uses a separate provider OAuth grant (`gmail.send` or Microsoft delegated `Mail.Send`); the existing read token is never silently upgraded. To prepare a draft, the candidate must copy and confirm the recipient from the current employer listing. The message and archived PDF are encrypted in a single-application local outbox and bound to the job, current candidate/search scope and file checksum. The application leaves the browser-form queue while an email draft is pending, preventing two submission paths for the same vacancy.

By default an email is only sent after the candidate reviews and confirms the exact recipient, subject, body and attachment. Full Autopilot email sending is a second, separate, optional grant, bound to the existing Autopilot scope/account and expiry; it can process only those candidate-confirmed email drafts. The current version does not discover/copy the contact address automatically, send follow-ups, accept offers, or claim delivery. The provider account address is not displayed because the send-only consent does not request mailbox/profile-reading scopes; verify the selected account at the provider consent screen. Direct attachments are limited to 2 MiB PDFs. Gmail success and Outlook `202 Accepted` are recorded as provider acceptance only. Timeout, process interruption or any uncertain provider result becomes `UNKNOWN` and is never automatically retried; reconcile the Sent folder before taking any manual action. A definite provider rejection also consumes its one attempt.

Before connecting, configure the provider OAuth app and copy `.env.example` to a private local `.env` file. Register the exact callback URL for the local server (`http://127.0.0.1:8765/email/callback/gmail` or `/microsoft`) and set `SAMPOAGENT_TOKEN_ENCRYPTION_KEY` to a Fernet key. The key protects mailbox tokens stored in SQLite; keep it private and backed up. If the key is lost, disconnect the mailbox and authorize it again. Secrets are never entered into the UI or committed to Git.

Google inbox reading requests `gmail.readonly`; Microsoft inbox reading requests delegated `Mail.Read`, `User.Read` and offline refresh access. Sending separately requests only `gmail.send` or delegated `Mail.Send` plus offline refresh. Local tests use mocked provider responses; a real connection requires your own provider registration and consent. The current UI records the provider but does not read/display the OAuth account's email address under the send-only grant, so verify the account in the provider consent screen. Real Gmail/Graph send, employer delivery, and provider-account identity verification have not been tested; do not treat synthetic tests as a production pilot.

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
- Explicit answer-bank provenance and scope checks, including limited exact-employer reuse; application-specific and unresolved answers remain out of automatic form filling. FI/SV/EN ambiguous or high-risk fields stay manual, and confirmed CV/answer conflicts pause the affected field with links to both evidence records.
- Job-specific CV selection: reuse an archived file only when its reviewed SHA-256 still matches, role and language fit, evidence match is at least 85%, and all detected hard requirements are present. Otherwise generate and archive a readable, paginated CV from confirmed facts, prioritizing job-relevant supported skills.
- Explainable application learning: only candidate-recorded interview, assessment, offer, rejection, or no-response outcomes count. After five such outcomes, a small bounded signal may break ties among already-qualified CVs; it cannot invent facts or override a hard requirement. Metrics are feedback signals, not hiring probabilities.
- Optional Codex retrospective: sharing is off by default. When explicitly enabled in Settings, `py -m sampoagent learning-summary --database sampoagent.db` reads the existing database read-only and emits only role-family/CV-language aggregate groups with at least five confirmed outcomes. It never exports candidate facts, CV text, identifiers, employer/job details, free text, paths, or checksums, and does not start a background Codex task.
- Provider-neutral browser-agent interface, optional visible Playwright browser, exact-package approval, field-value/CV checksum readback, and a safe unconfigured fallback. Form snapshots include accessible prompts, descriptions, select/radio options, origin/action and declared file constraints. Radio groups fill only on exact option match; checkbox groups remain manual. Multi-step or ambiguous multi-form pages stop before candidate data entry. The adapter rechecks live selected-file hashes immediately before the final click.

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
