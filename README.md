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

`demo` loads clearly marked synthetic data. To start with an empty local profile instead, run `sampoagent init` and then `sampoagent run`. Use `sampoagent --help` for available commands.

## First-run workflow

1. Enter your own profile; a clean install never inserts a fictional candidate or job.
2. Upload a text CV and review all extracted facts before using them.
3. Confirm skills and experience; SampoAgent recommends roles but never activates them without your choice.
4. Use the Finland source catalog, then find matching jobs based on your confirmed profile and locations.
5. Configure thresholds, daily application limit, email connection, and application mode.
6. Start in Dry Run; it never makes final submissions.

### Optional read-only email connection

The Email page can connect Gmail or Microsoft Outlook to find likely job-application replies. It only reads a bounded number of message headers/snippets; it never sends, moves, or deletes mail. A match is only a suggestion: the application tracker changes after you select the related application and explicitly confirm the outcome.

Before connecting, configure the provider OAuth app and copy `.env.example` to a private local `.env` file. Register the exact callback URL for the local server (`http://127.0.0.1:8765/email/callback/gmail` or `/microsoft`) and set `SAMPOAGENT_TOKEN_ENCRYPTION_KEY` to a Fernet key. The key protects mailbox tokens stored in SQLite; keep it private and backed up. If the key is lost, disconnect the mailbox and authorize it again. Secrets are never entered into the UI or committed to Git.

Google requests the `gmail.readonly` scope; Google may require OAuth consent-screen configuration and app verification for wider use. Microsoft requests delegated `Mail.Read` plus `User.Read` and offline refresh access. Local tests use mocked provider responses; a real connection requires your own provider registration and consent.

To preview clearly synthetic example data instead, run `sampoagent demo` once before `sampoagent run`.

## Current V1 scope

- Finland country pack with a built-in source catalog. Each source is labeled with its real discovery capability; protected sources remain browser-only.
- Optional Scrapling adapter for static public pages using JSON-LD JobPosting data or a user-provided job-card CSS selector; robots policy and access-control failures are respected.
- Separate Finnish and English CV labels and template selection.
- Deterministic matching, duplicate detection, language detection, hard blockers, explainable three-part scoring.
- Confirmed-fact-only PDF CV output and ATS text re-parse validation.
- Provider-neutral browser-agent interface and a safe unconfigured fallback.

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
- Browser-agent integrations and live application workflows require a compatible configured agent. Final application submission always requires review and explicit confirmation for that specific application.
- Demo content is synthetic; do not mistake it for a real candidate profile or live job listing.

## Privacy and limitations

Candidate data is stored in local SQLite. No AI key is required: Minimal deterministic mode is the default. Live job discovery requires a permitted public feed or an activated official API; browser-only sources remain personalized links. Gmail/Outlook OAuth credentials are supplied by the operator, and mailbox tokens are encrypted before local persistence. Final web-form submission may require user action for authentication, CAPTCHAs, or high-risk questions.

See [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md), and [docs/CLAUDE_INTEGRATION.md](docs/CLAUDE_INTEGRATION.md). Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Please report security issues according to [SECURITY.md](SECURITY.md).

Scrapling is an external BSD-3-Clause dependency; this project uses its static parser only. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Licence

Apache-2.0. Copyright 2026 Kai Punasaari. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
