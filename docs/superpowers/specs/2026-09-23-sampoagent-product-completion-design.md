# SampoAgent product completion design

## Purpose and user outcome

SampoAgent must feel like a usable job-search workspace, not a developer demo. A new user should see an honest empty/onboarding state, edit candidate and search settings in the UI, get role searches derived from their profile instead of entering every job manually, manage sources, connect an email account for application-response tracking, and understand which automation is genuinely available. Preserve the local-first, privacy-conscious, fact-grounded product in the original objective.

The user specifically reported that the interface remains poor, settings do not feel editable, mail has no authentication/connection area, jobs require one-by-one entry, and source management is missing from the experience. Source CRUD does exist in the current code, but it is visually buried and not seeded into a genuinely new install; the revised UX must make it discoverable.

## Current-state evidence

- The FastAPI app loads synthetic demo profile, jobs, settings, and three sources on every first launch (`create_app()` calls `repository.load_demo()`), although demo is also exposed as an explicit CLI command.
- The Finland country pack defines 13 browser-only source homepages. `/sources` supports adding and toggling sources but not editing/deleting, source diagnostics, or discovery status.
- `/jobs` only imports manually entered postings and scores stored jobs. It has no profile-derived search workflow, feed adapters, or discovery-run feedback.
- `/settings` exposes only mode, daily cap, AI usage, a few job preferences, and score dimensions.
- There is no mailbox integration or authorization flow.
- Existing UI is a single-file inline HTML/CSS FastAPI interface and does not require a frontend build tool. Existing tests pass (86 passed) and cover several CRUD/domain workflows, but do not cover the missing user-facing workflows above.

## Design

### 1. Honest first-run experience

Production `create_app()` initializes SQLite and app defaults but never inserts synthetic candidate/job data. It seeds the Finnish source catalog idempotently, with each capability accurately labeled. The explicit `sampoagent demo` command remains the only way to load fictional sample jobs/person data. The dashboard shows onboarding progress and one primary next action when no real profile exists; demo rows are labeled synthetic.

### 2. Profile-led job discovery

Add a focused discovery service that builds a deduplicated set of search phrases from confirmed work titles, confirmed skills, active target occupations/career profiles, language, and location preferences. Users can exclude/edit generated search phrases but do not have to type each target role. An explicit “Find matching jobs” action records a run, uses enabled sources, filters imported jobs through saved preferences, deduplicates, scores, and reports new/duplicate/failed counts.

Sources advertise capabilities: `feed`, `official_api`, or `browser_search_only`, plus login/manual-action state. RSS/Atom and JSON feeds are fetched only from user-enabled, explicitly configured feed URLs with bounded timeouts/response sizes and safe URL checks. Browser-only sources receive generated per-source search links; the app never scrapes pages, bypasses restrictions, or claims those links equal imported listings. Job Market Finland's P67 adapter is available only when a user has activated the API and supplied its key; the UI documents the KEHA onboarding prerequisite. Search/query generation remains useful without credentials.

### 3. Mailbox authorization and response review

Settings contains a dedicated Email connections panel. Gmail and Microsoft mail use OAuth authorization-code + PKCE with read-only mail scopes, random one-use state, exact localhost redirect URI validation, short timeouts, and explicit disconnect. Client IDs/secrets and a local token-encryption key come from environment configuration, never form fields or SQLite plaintext. Encrypted OAuth token data is stored locally; only connection identity and minimal message metadata are displayed. Sync imports a bounded set of recent messages, flags likely employer/application conversations for human confirmation, and never sends mail or changes application status without user action. If provider credentials/key protection are unavailable, the page explains the exact missing setup and retains manual status tracking.

### 4. User-operable workspace UI

Keep the lightweight server-rendered stack, but simplify navigation and page hierarchy around Today, Profile, Job search, Sources, Applications, and Settings. Make source management visible from Job search and onboarding. Add full user-editable employment preferences and CRUD for candidate records, career profiles/targets, and custom sources. Group settings into readable cards with save-state feedback. Every form has labels, validation messages, keyboard focus, responsive behavior, and server-side validation. Preserve safety gates: Review Everything and Dry Run stay defaults, hard requirements and duplicate checks cannot be bypassed, and unknown factual/high-risk application answers still require review.

### 5. Migration, compatibility, and privacy

SQLite changes are additive and idempotent; existing `sampoagent.db` files continue to open. Existing synthetic demo data is not deleted from a user's database, but new installations stop receiving it automatically. Source deletion handles references safely; stored jobs keep their source label/url snapshot. Do not overwrite user application data during tests or QA. Keep the server bound to `127.0.0.1`.

## Validation

- Test clean install has no fictional candidate/job rows and has the Finland source catalog and safe defaults.
- Test deterministic profile-to-search generation, duplicate phrases, location reuse, and no confirmed-fact leakage.
- Test RSS/Atom/JSON adapters with fixtures, timeouts, malformed input, duplicate jobs, and disabled sources; no test hits live job sites.
- Test source add/edit/toggle/delete and visible search links for browser-only capability.
- Test mailbox OAuth state/PKCE/token encryption/callback failures/disconnect/safe bounded sync using mocked HTTP; no live mailbox credentials in tests.
- Exercise onboarding, settings, source management, discovery, and email settings in a real local browser at desktop and mobile viewport sizes.
- Run full pytest, Python compile, and package/install smoke tests; inspect git diff/secret scan; publish the verified branch/PR on GitHub.

## Explicit limitations

Job-board access depends on each source's officially supported capability. The Job Market Finland retrieval interface requires external activation and issued credentials; browser-only sources can be opened with automatically generated profile-specific searches but cannot be represented as automatically indexed. Gmail/Microsoft OAuth requires developer application credentials configured by the user, and live authorization cannot be verified without those accounts/credentials.
