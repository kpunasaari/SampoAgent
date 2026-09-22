# SampoAgent — Open-Source Job Application & Career Agent

SampoAgent is an open-source AI job application tool and career agent that discovers relevant jobs, matches them to your skills and experience, tailors ATS-friendly CVs, assists with application forms, and tracks job applications.

It is a local-first job search agent, ATS CV/resume tailoring tool, job application tracker, and safe foundation for job application automation. It is not a service that invents qualifications, bypasses CAPTCHAs, or promises hiring outcomes.

## Quick start

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
sampoagent demo
sampoagent run
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The service intentionally binds only to localhost.

## First-run workflow

1. Review the synthetic demo or enter your own profile.
2. Upload a text CV and review all extracted facts before using them.
3. Add confirmed skills; SampoAgent recommends roles without auto-selecting them.
4. Configure sources, thresholds, daily application limit, and application mode.
5. Start in Dry Run; it never makes final submissions.

## Current V1 scope

- Finland country pack with built-in source metadata; source access is browser-only unless a compliant adapter is added.
- Separate Finnish and English CV labels and template selection.
- Deterministic matching, duplicate detection, language detection, hard blockers, explainable three-part scoring.
- Confirmed-fact-only PDF CV output and ATS text re-parse validation.
- Provider-neutral browser-agent interface and a safe unconfigured fallback.

## Privacy and limitations

Candidate data is stored in local SQLite. No AI key is required: Minimal deterministic mode is the default. Live job discovery and final web-form submission require a compliant source or browser-agent integration and may require user action for authentication, CAPTCHAs, or high-risk questions.

See [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md), and [docs/CLAUDE_INTEGRATION.md](docs/CLAUDE_INTEGRATION.md). Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

Apache-2.0. Copyright 2026 Kai Punasaari.
