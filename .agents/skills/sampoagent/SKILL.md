---
name: sampoagent
description: Use when a user wants to find, assess, or apply for jobs, tailor a CV, prepare application answers, or track job applications with SampoAgent or available candidate records.
---

# SampoAgent job search and applications

Help the user find suitable openings and prepare accurate applications while keeping candidate data private and the user in control. Treat SampoAgent as a local-first career workspace, not as permission to submit applications automatically.

## Choose candidate records

- When working in the SampoAgent project and its candidate profile is explicitly available through the app or configured integration, use those records first. Read only the facts relevant to the selected job; preserve each fact's source and confirmation status.
- Use only user-provided files, records, or stores the user explicitly identified. Do not search arbitrary folders, accounts, or SQLite files for personal information. Never copy a local database, CV, or candidate fact into the public repository, logs, or unrelated output.
- If this skill is used outside the SampoAgent project, do not assume the app or its database is installed. Use only records made available for this task and explain when a requested fact is not available.
- Prefer current user-confirmed facts where records conflict. Point out material conflicts and ask which is correct; do not silently resolve them.
- Never invent or infer personal claims such as work dates, qualifications, language level, work authorization, salary history, references, licences, availability, or eligibility answers. AI-inferred or unconfirmed CV-extracted facts are not confirmed facts.
- When a required fact is absent, ambiguous, or contradictory, pause that field and ask the user. Keep application questions together where practical.

## Discover and assess jobs

1. Reuse the user's active target roles, locations, languages, schedule, and other preferences from the available SampoAgent profile; otherwise ask only for missing constraints that materially affect the search.
2. Search public, permitted sources using enabled SampoAgent adapters and available web/browser tools. Prefer the employer's original posting for current requirements and application route. Do not claim a source is monitored or exhaustive unless verified.
3. Use Scrapling only when already installed/configured and useful for public static HTML. Prefer structured job data; otherwise use ordinary page structure or a configured selector. Do not install it unless asked.
4. Respect site terms, robots policy, rate limits, authentication boundaries, and technical controls. Do not scrape private/member-only pages, evade blocks, use stealth/proxy/fingerprint techniques, defeat CAPTCHAs, or continue after access is denied. Stop automation and offer an official user-led route when blocked.
5. Verify the opening is still available, record source and check date, distinguish known from uncertain details, deduplicate, and explain fit and gaps against confirmed experience.

## Prepare applications and forms

- Before preparing an application, summarize the employer, role, location, deadline if stated, official link, key requirements, fit, and gaps.
- Tailor CVs, letters, and answers to the vacancy language using confirmed facts only. Keep suggested but unconfirmed claims clearly separated from factual application content.
- For a browser form, inspect each label and enter only supported values. Pause for missing facts and for sensitive or high-impact questions, including eligibility/work authorization, sponsorship, salary, notice period, availability, criminal/background disclosures, health/disability, demographic questions, legal declarations, and consent.
- Login, identity checks, one-time codes, CAPTCHA, and access denials are user actions. Do not request passwords or codes in chat or attempt to bypass a control. Save a draft only when the site supports it and confirm the saved state.
- Before any final Submit/Apply/Send action, show the exact job and a concise review of material answers, documents, and declarations. Submit only after the user explicitly confirms that specific application. General prior permission to apply is not confirmation for a particular submission.
- After submission, verify the confirmation state and report it accurately. If not submitted, say what remains and provide the official link.

## Track outcomes

Record only useful, non-secret status and evidence, such as found, selected, draft ready, submitted, confirmation reference, and follow-up date. Never store passwords, access tokens, or unnecessary sensitive answers. Preserve application history and distinguish a prepared draft from a verified submission.

## SampoAgent focused skills

When present in the repository, load only the focused workflow needed:

- `../sampoagent-job-discovery/SKILL.md` for permitted source discovery.
- `../sampoagent-job-analysis/SKILL.md` for explainable match assessment.
- `../sampoagent-cv-tailoring/SKILL.md` for confirmed-fact CV tailoring.
- `../sampoagent-application-fi/SKILL.md` or `../sampoagent-application-en/SKILL.md` for Finnish or English materials.
- `../sampoagent-form-filling/SKILL.md` for browser form assistance.
- `../sampoagent-application-tracking/SKILL.md` for status and evidence tracking.

If a focused skill or integration is unavailable, continue only with the safe capabilities actually available and state the limitation. Work in reviewable batches: shortlist jobs, let the user choose, resolve missing facts, prepare a draft, and obtain job-specific final confirmation before submission.
