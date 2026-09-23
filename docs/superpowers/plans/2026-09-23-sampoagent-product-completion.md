# SampoAgent Product Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing demo-like interface into an honest, user-operable local job-search workspace with profile-led discovery, manageable sources, and authenticated email response tracking.

**Architecture:** Keep FastAPI, SQLite, server-rendered HTML, and the current domain service boundaries. Add a deterministic discovery layer plus bounded feed/API adapters, extend repository methods with additive schema changes, and isolate OAuth/message sync in an email integration module; routes render and validate, services own policy and network boundaries.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, urllib/stdlib XML+JSON parsing, pytest, existing HTML/CSS shell.

**Spec:** `docs/superpowers/specs/2026-09-23-sampoagent-product-completion-design.md`

## Global Constraints

- Keep candidate/job data local in SQLite and bind to `127.0.0.1`.
- Never fabricate candidate facts or silently use CV-extracted facts in factual claims.
- Do not scrape browser-only sources, bypass logins/CAPTCHAs/robots, or claim unverified source support.
- Never store OAuth access/refresh tokens as plaintext or log provider credentials.
- Review Everything and Dry Run remain safe defaults; unknown high-risk answers require user action.
- Do not require Docker, Node, PostgreSQL, Redis, or live service credentials to run/test the core app.
- Demo records are opt-in; source catalog/default preferences may be seeded idempotently.

## Review Focus

- New install versus an existing/demo database: new installs are clean without deleting pre-existing records.
- Ambiguous or duplicate job-feed entries: report failures/duplicates and do not insert malformed vacancies.
- OAuth state replay, missing provider configuration, cancelled authorization, and token decryption failure: fail closed without logging tokens.
- Multiple overlapping search terms/locations: deduplicate bounded query count while preserving user-selected career intent.
- Disabled browser-only sources and unavailable official APIs: never report a successful automatic scan.

---

### Task 1: Clean first-run data and visible onboarding

**Files:**
- Modify: `sampoagent/app/main.py:create_app`, dashboard, onboarding
- Modify: `sampoagent/db/repository.py:initialize`, `load_demo`, source CRUD
- Modify: `sampoagent/cli.py`
- Test: `tests/test_onboarding.py`, `tests/test_database_and_ui.py`, new `tests/test_first_run.py`

**Interfaces:** `create_app(database_path)` initializes an empty candidate workspace and default settings; `Repository.seed_builtin_sources(definitions)` inserts missing source definitions idempotently; `Repository.load_demo()` remains explicit CLI-only behavior.

- [x] Test `create_app(tmp_path / "new.db")` shows onboarding, zero real jobs, no Aino Example candidate, Review Everything/Dry Run, and 13 builtin source rows.
- [x] Test an existing database containing a user profile/job remains intact after `create_app()`.
- [x] Test repeated `seed_builtin_sources()` adds no duplicate URLs and preserves user enabled flags/notes.
- [x] Remove unconditional `repository.load_demo()`; initialize safe settings, seed catalog, and show an honest empty dashboard with a clear setup action.
- [x] Add regression coverage for explicit `sampoagent demo` still loading only clearly synthetic data.
- [x] Run full verification: `py -m pytest -q` → 89 passed; `py -m compileall -q sampoagent` → passed; `py -m sampoagent --help` → passed.

### Task 2: Candidate-derived search plan

**Files:**
- Create: `sampoagent/jobs/discovery.py`
- Modify: `sampoagent/country_packs/finland/__init__.py`
- Modify: `sampoagent/db/repository.py` additive `discovery_runs`/source metadata/job source snapshots
- Test: new `tests/test_discovery.py`, `tests/test_first_run.py`

**Interfaces:** `build_search_plan(*, facts, candidate_records, targets, career_profiles, preferences, sources, max_queries=120) -> SearchPlan`; `SearchQuery` contains `phrase`, `location`, `language`, `source_id`, `search_url`, `capability`; Task 3's adapter runner consumes this plan and records its report in the repository.

- [x] Test confirmed experience titles/skills/approved targets generate bounded deduplicated phrases for all enabled configured sources and preferred locations.
- [x] Test unconfirmed/rejected facts do not generate search phrases and disabled targets/sources are excluded. The guided empty state is verified with the Jobs UI in Task 3/6.
- [x] Test source query URLs are percent-encoded and source definitions explicitly identify browser-only versus feed/API support.
- [x] Implement deterministic search phrases from existing structured confirmed facts and target occupations; expose included/excluded terms to preferences for the Settings UI in Task 4.
- [x] Add discovery run/result persistence and additive job source snapshots for existing SQLite databases.
- [x] Run `py -m pytest -q tests/test_discovery.py` → 5 passed; full `py -m pytest -q` → 94 passed; compileall passed.

### Task 3: Safe automatic feed/API discovery

**Files:**
- Create: `sampoagent/jobs/adapters.py`
- Create: `sampoagent/jobs/feeds.py`
- Modify: `sampoagent/jobs/service.py`, `sampoagent/db/repository.py`, `sampoagent/app/main.py`
- Modify: `sampoagent/country_packs/finland/__init__.py`
- Test: new `tests/test_job_adapters.py`, `tests/test_discovery_ui.py`

**Interfaces:** `SourceAdapter.search(source, query, *, timeout_seconds=8, max_bytes=2_000_000) -> list[NormalizedJob]`; `RssAtomAdapter`, `JsonFeedAdapter`, and configured `JobMarketFinlandAdapter` implement the interface. Browser-only capability creates links and is not fetched.

- [x] Test RSS, Atom, and JSON Feed fixture parsing into normalized jobs with source attribution, safe URL validation, and deadline/language handling.
- [x] Test timeouts, oversized responses, malformed XML/JSON, non-HTTPS URLs, unsafe private DNS destinations, robots denial, and duplicate fingerprints fail/skip visibly and safely.
- [x] Test disabled or browser-only sources are never passed to a network adapter.
- [x] Add Job Market Finland P67 adapter only when its issued credential is configured; expose exact setup state and use bounded request/response parsing from the documented service contract. Local matching filters the NDJSON results because the documented P67 filters do not accept free-text search terms.
- [x] Add `/jobs/discover` POST to generate/run a search plan, fetch only supported adapters, import/dedupe/score results, and record per-source outcome; for browser-only sources display personalized search links.
- [x] Show last discovery summary, query phrases, results/import counts, duplicates, and source failures. Keep manual import as secondary fallback.
- [x] Run focused adapter, runner, UI, and job-application tests (33 passed in the latest focused run).

### Task 4: Source management and preference controls

**Files:**
- Modify: `sampoagent/db/repository.py` source and preference methods
- Modify: `sampoagent/app/main.py` `/sources`, `/settings`, `/careers`, `/profile`
- Test: new `tests/test_source_crud.py`, `tests/test_preferences_full_ui.py`; update focused UI tests

**Interfaces:** Add validated `Repository.update_source`, `delete_source`, and `source_scan_summary`; add CRUD routes for sources and candidate records; preference form serializes repeatable location/work-schedule/employment/industry/employer/position values as validated JSON settings.

- [x] Test source edit/delete/toggle including invalid URL/capability, deletion of a source with imported jobs preserving job snapshots, and idempotent catalog setup.
- [x] Test exposed settings persist and render back, including location exclusions, work/employment/shift, search/title/employer/industry phrases, source category switches, salary/radius fields, scoring controls, mode, and daily cap.
- [x] Add missing candidate-record and career-target/profile edit/delete actions; add success/error status notices.
- [x] Make Sources a searchable responsive table with capability/last-run/result summaries, edit/delete/toggle controls, prominent add form, and one-click Finland catalog action.
- [x] Group Settings into labeled cards for safety, search/preferences, and explainable scoring.
- [x] Run `tests/test_source_crud.py`, `tests/test_preferences_full_ui.py`, `tests/test_source_settings.py`, and `tests/test_scoring_settings_ui.py` (combined relevant suite passed in later full run).

### Task 5: Mail OAuth and response review

**Files:**
- Create: `sampoagent/integrations/email_oauth.py`
- Create: `sampoagent/integrations/mailbox.py`
- Modify: `pyproject.toml` for a vetted Fernet encryption dependency
- Modify: `sampoagent/db/repository.py` mailbox token/message tables and methods
- Modify: `sampoagent/app/main.py` `/settings/email`, `/email/connect/{provider}`, `/email/callback/{provider}`, `/email/sync`, `/email/disconnect`
- Modify: `.gitignore`, `.env.example`, `README.md`, `SECURITY.md`
- Test: new `tests/test_email_oauth.py`, `tests/test_email_ui.py`

**Interfaces:** `OAuthConfig.from_environment(provider)` loads Google/Microsoft client config; `begin_authorization(provider, state, verifier, redirect_uri)` returns authorization URL; `exchange_code(...)` returns token data; `encrypt_token_payload`/`decrypt_token_payload` use Fernet with `SAMPOAGENT_TOKEN_ENCRYPTION_KEY`; mailbox providers return bounded `MailboxMessage` metadata only.

- [x] Test OAuth URL scopes, PKCE, one-time state/replay/cancel, missing configuration, local callback validation, token encryption/decryption, and disconnect.
- [x] Test bounded inbox sync stores only metadata/snippets/links and never changes application status until user confirms.
- [x] Implement localhost OAuth callback with one-use HttpOnly SameSite=Lax state cookie; validate exact local callback path and do not accept arbitrary return URLs.
- [x] Encrypt token payload before SQLite persistence and exclude secrets/tokens from logs/UI. Do not offer raw token editing in Settings.
- [x] Add mailbox connect/disconnect/sync UI for Gmail and Microsoft, configuration states, provider setup instructions, and response-review actions.
- [x] Run focused email OAuth/mailbox/UI tests (12 passed in the latest focused run).

### Task 6: Jobs-first experience and browser QA

**Files:**
- Modify: `sampoagent/app/main.py` navigation/dashboard/jobs/careers/onboarding, shared CSS
- Modify: `README.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, `.env.example`, `SECURITY.md`
- Test: `tests/test_ui_shell.py`, new `tests/test_discovery_ui.py`, relevant UI suites

**Interfaces:** The Jobs page offers a profile-derived “Find matching jobs” primary action, search plan controls, source capability summaries, imported matches, and secondary manual paste/import. Navigation presents one plain-language route to Jobs/Sources/Applications/Settings; legacy paths continue to resolve.

- [x] Test dashboard uses clean onboarding state until a user profile exists and displays source readiness/discovery action after onboarding.
- [x] Test the Jobs UI does not require title/company/location/description entry to run discovery and still retains optional manual import.
- [x] Simplify navigation by keeping the eight primary destinations visible and grouping secondary tools without removing them; retain active-path state, responsive shell, labels, and keyboard focus.
- [ ] Browser-check desktop and narrow widths for onboarding, dashboard, profile, careers, jobs/discovery, sources CRUD, settings, email connection status, queue, and applications.
- [x] Run full `py -m pytest -q` (153 passed), `py -m compileall -q sampoagent`, CLI help, local route smoke tests, and secret scan.
- [x] Update docs to distinguish implemented and credential-dependent features; state that API activation and OAuth app registration are external prerequisites.
- [ ] Review diff, commit coherent changes, push the current GitHub branch, update/create the pull request, and attach its URL.

## Completion evidence

Do not claim that live job indexing or mailbox authorization was live-tested without real authorized accounts/credentials. Prove local behavior with deterministic fixtures and browser checks, then report external prerequisites plainly.
