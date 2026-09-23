# SampoAgent Public Skill and Repository Release Plan

> **For agentic workers:** Use the native implementation workflow for each task and verify the result before publishing.

**Goal:** Make the reusable `sampoagent` Codex skill part of the SampoAgent repository, document use for all users, and retire the separate job-search skill repository.

**Architecture:** Add one project-level orchestration skill under `.agents/skills/sampoagent/` that delegates to the existing focused skills in this repository. Keep the skill generic and candidate-data-free; when used in this repository, it may rely only on explicitly available SampoAgent records, while standalone use must not assume a database exists. Update the repository's user-facing and package metadata without changing the already-correct Apache-2.0 license grant.

**Tech Stack:** Codex Agent Skills format, Markdown, Python packaging metadata, Git/GitHub.

**Spec:** User request in this task and the SampoAgent engineering principles in `AGENTS.md`.

## Global Constraints

- Keep the application local-first and preserve candidate-fact provenance.
- Never fabricate candidate claims or bypass access controls.
- Do not put candidate personal data, local database files, or secrets into the skill or repository.
- Require review and explicit confirmation for each final application submission.
- Preserve the existing Apache License 2.0 and copyright attribution.
- Do not delete the separate GitHub repository until its exact identity is verified and the explicit per-action confirmation requirement is met.

## Review Focus

- Missing/contradictory candidate facts: pause and ask, rather than infer.
- Local database unavailable or skill used outside project: use only explicitly shared records and disclose the limitation.
- CAPTCHA, login wall, or blocked scraping: stop automated access and offer an official user-led route.
- General prior approval vs. a particular application: require specific final confirmation before submit.
- Public repository contents: exclude local candidate databases and secrets; retain accurate Apache-2.0 notices.

---

### Task 1: Add the SampoAgent orchestration skill

**Files:**
- Create: `.agents/skills/sampoagent/SKILL.md`
- Create: `.agents/skills/sampoagent/agents/openai.yaml`

- [x] Re-run the pressure scenario without the skill and record its boundary behavior.
- [x] Write the skill with repository-aware database behavior, safe standalone fallback, and links to focused skills.
- [x] Validate YAML frontmatter, naming, links, length, and the Codex metadata file.
- [x] Re-run the pressure scenario with the skill and confirm CAPTCHA/unknown facts/per-submission review boundaries.

**Baseline (without repository skill):** In the combined time-pressure/CAPTCHA/missing-fact/general-approval scenario, the agent declined submission, left work authorization unanswered, refused CAPTCHA bypass, and required application-specific review. This is the target behavior; the new skill must retain it while clarifying when project records may be used.

### Task 2: Finalize public project documentation and license metadata

**Files:**
- Modify: `README.md`
- Modify: `pyproject.toml`
- Verify unchanged: `LICENSE`, `NOTICE`, `.gitignore`

- [x] Document prerequisites, install/run steps, the repository-local skill, optional personal skill installation, privacy, and integrations that are unavailable without configuration.
- [x] Declare Apache-2.0 and the existing copyright holder in package metadata.
- [x] Check that local databases and personal data remain ignored and absent from the staged diff.

### Task 3: Verify and publish in SampoAgent

**Files:**
- Verify: all changed files, tests, package build, git remote/branch

- [x] Run the skill format checks, `py -m pytest -q`, `py -m compileall -q sampoagent`, and a package metadata/build check.
- [x] Confirm the diff contains only intended documentation/skill/metadata files.
- [ ] Push the release changes to the SampoAgent GitHub repository.

### Task 4: Retire the separate repository

**External target:** `kpunasaari/job-search-apply-skill`

- [ ] Verify the exact GitHub repository and that the canonical SampoAgent skill is published first.
- [ ] Obtain the UI's required confirmation immediately before its irreversible deletion action.
- [ ] Verify the old repository is no longer available after deletion.
