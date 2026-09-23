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

- [ ] Test `create_app(tmp_path / "new.db")` shows onboarding, zero real jobs, no Aino Example candidate, Review Everything/Dry Run, and 13 builtin source rows.
- [ ] Test an existing database containing a user profile/job remains intact after `create_app()`.
- [ ] Test repeated `seed_builtin_sources()` adds no duplicate URLs and preserves user enabled flags/notes.
- [ ] Remove unconditional `repository.load_demo()`; initialize safe settings, seed catalog, and show an honest empty dashboard with a clear setup action.
- [ ] Add regression coverage for explicit `sampoagent demo` still loading only clearly synthetic data.
- [ ] Run `py -m pytest -q tests/test_first_run.py tests/test_onboarding.py tests/test_database_and_ui.py`.

### Task 2: Candidate-derived search plan

**Files:**
- Create: `sampoagent/jobs/discovery.py`
- Modify: `sampoagent/country_packs/finland/__init__.py`
- Modify: `sampoagent/db/repository.py` additive `discovery_runs`/source metadata/job source snapshots
- Test: new `tests/test_discovery.py`, `tests/test_first_run.py`

**Interfaces:** `build_search_plan(*, facts, records, targets, career_profiles, preferences, sources) -> SearchPlan`; `SearchQuery` contains `phrase`, `location`, `language`, `source_id`, `search_url`, `capability`; `run_discovery(repository, fetcher=...) -> DiscoveryReport`.

- [ ] Test confirmed experience titles/skills/approved targets generate bounded deduplicated phrases for all enabled configured sources and preferred locations.
- [ ] Test unconfirmed/rejected facts do not generate search phrases, disabled targets/sources are excluded, and zero matching profile data produces a guided empty state.
- [ ] Test source query URLs are percent-encoded and source definitions explicitly identify browser-only versus feed/API support.
- [ ] Implement deterministic search phrases from existing structured confirmed facts and target occupations; keep a user-editable include/exclude list in settings.
- [ ] Add discovery run/result persistence and source metadata migration; keep changes additive for old DBs.
- [ ] Run `py -m pytest -q tests/test_discovery.py tests/test_first_run.py`.

### Task 3: Safe automatic feed/API discovery

**Files:**
- Create: `sampoagent/jobs/adapters.py`
- Create: `sampoagent/jobs/feeds.py`
- Modify: `sampoagent/jobs/service.py`, `sampoagent/db/repository.py`, `sampoagent/app/main.py`
- Modify: `sampoagent/country_packs/finland/__init__.py`
- Test: new `tests/test_job_adapters.py`, `tests/test_discovery_ui.py`

**Interfaces:** `SourceAdapter.search(source, query, *, timeout_seconds=8, max_bytes=2_000_000) -> list[NormalizedJob]`; `RssAtomAdapter`, `JsonFeedAdapter`, and configured `JobMarketFinlandAdapter` implement the interface. Browser-only capability creates links and is not fetched.

- [ ] Test RSS, Atom, and JSON Feed fixture parsing into normalized jobs with source attribution, safe URL validation, and deadline/language handling.
- [ ] Test timeouts, oversized responses, malformed XML/JSON, non-HTTPS URLs (except loopback test fixtures), and duplicate fingerprints fail/skip visibly and safely.
- [ ] Test disabled or browser-only sources are never passed to a network adapter.
- [ ] Add Job Market Finland P67 adapter only when its issued credential is configured; expose exact setup state and use bounded request/response parsing from the documented service contract.
- [ ] Add `/jobs/discover` POST to generate/run a search plan, fetch only supported adapters, import/dedupe/score results, and record per-source outcome; for browser-only sources display personalized search links.
- [ ] Show last discovery summary, query phrases, results/import counts, duplicates, and source failures. Keep paste URL/text/manual import as secondary fallback.
- [ ] Run `py -m pytest -q tests/test_job_adapters.py tests/test_discovery_ui.py tests/test_jobs_and_applications.py`.

### Task 4: Source management and preference controls

**Files:**
- Modify: `sampoagent/db/repository.py` source and preference methods
- Modify: `sampoagent/app/main.py` `/sources`, `/settings`, `/careers`, `/profile`
- Test: new `tests/test_source_crud.py`, `tests/test_preferences_full_ui.py`; update focused UI tests

**Interfaces:** Add validated `Repository.update_source`, `delete_source`, and `source_scan_summary`; add CRUD routes for sources and candidate records; preference form serializes repeatable location/work-schedule/employment/industry/employer/position values as validated JSON settings.

- [ ] Test source edit/delete/toggle including invalid URL/type, deletion of a source with imported jobs preserving job snapshots, and idempotent catalog setup.
- [ ] Test all exposed settings persist and render back: work locations/exclusions/radius, remote/hybrid, employment/time preferences, employer/industry/title include/exclude, public-sector/recruitment-agent switches, salary minimum, score toggles/weights/minima, mode, daily cap, and search phrases.
- [ ] Add missing record edit/delete and career target/profile update actions; add success/error `role=status` notices.
- [ ] Redesign Sources as visible searchable cards with capability/last-run/result summaries, edit/delete/toggle controls, prominent “Add source”, and “Add Finland sources” one-click action.
- [ ] Redesign Settings into labeled cards and separate search phrases/preferences/safety/scoring sections; display normalized scoring weights and safety-mode explanation.
- [ ] Run `py -m pytest -q tests/test_source_crud.py tests/test_preferences_full_ui.py tests/test_source_settings.py tests/test_scoring_settings_ui.py`.

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

- [ ] Test OAuth URL scopes (Gmail readonly; Microsoft Mail.Read/User.Read/offline_access), PKCE challenge, random state mismatch/replay, missing config, callback error, token encryption/decryption, and disconnect using mocked HTTP.
- [ ] Test inbox sync returns no more than configured bound, stores only message headers/snippet/link, flags likely application response, and never changes application status until user confirms.
- [ ] Implement localhost OAuth callback with one-use HttpOnly SameSite=Lax state cookie; enforce exact configured redirect URL; do not accept arbitrary return URLs.
- [ ] Encrypt token payload before SQLite persistence and exclude all secrets/tokens from logs/UI. Do not offer raw token editing in Settings.
- [ ] Add mailbox connect/disconnect/sync UI for Gmail and Microsoft, connection/configuration states, clear external credential setup instructions, and response-review actions.
- [ ] Run `py -m pytest -q tests/test_email_oauth.py tests/test_email_ui.py`.

### Task 6: Jobs-first experience and browser QA

**Files:**
- Modify: `sampoagent/app/main.py` navigation/dashboard/jobs/careers/onboarding, shared CSS
- Modify: `README.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, `.env.example`, `SECURITY.md`
- Test: `tests/test_ui_shell.py`, new `tests/test_discovery_ui.py`, relevant UI suites

**Interfaces:** The Jobs page offers a profile-derived “Find matching jobs” primary action, search plan controls, source capability summaries, imported matches, and secondary manual paste/import. Navigation presents one plain-language route to Jobs/Sources/Applications/Settings; legacy paths continue to resolve.

- [ ] Test dashboard uses clean onboarding state until a user profile exists and displays source readiness/discovery action after onboarding.
- [ ] Test the Jobs UI does not require title/company/location/description entry to run discovery and still retains optional manual import.
- [ ] Simplify navigation labels and page hierarchy; keep desktop sidebar and mobile navigation, clear active page state, consistent cards, empty/loading/error/success states, and labels/keyboard focus.
- [ ] Browser-check desktop and narrow widths for onboarding, dashboard, profile, careers, jobs/discovery, sources CRUD, settings, email connection status, queue, and applications.
- [ ] Run full `py -m pytest -q`, `py -m compileall -q sampoagent`, package install/CLI help, local route smoke tests, and secret scan.
- [ ] Update docs to distinguish implemented and credential-dependent features; state that API activation and OAuth app registration are external prerequisites.
- [ ] Review diff, commit coherent changes, push the current GitHub branch, update/create the pull request, and attach its URL.

## Completion evidence

Do not claim that live job indexing or mailbox authorization was live-tested without real authorized accounts/credentials. Prove local behavior with deterministic fixtures and browser checks, then report external prerequisites plainly.
