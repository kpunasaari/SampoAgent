# Application-scoped missing answers implementation plan

> **For agentic workers:** Execute task-by-task with test-first changes. This task is being implemented natively in the current worktree at the user's explicit request.

**Goal:** Collect only genuinely missing, safe required form answers inside SampoAgent and bind them to one application, live form signature, and verified listing snapshot.

**Architecture:** Add a local application-response table separate from the global answer bank. The runner registers eligible unresolved fields and resolves a response only when all application/form/listing bindings still match. A CSRF-protected Applications form gathers the response; completing the question set requeues the item without changing its selected application mode or granting submit authority.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, pytest, existing browser FormSchema and deterministic field resolver.

**Spec:** [2026-09-30-application-scoped-missing-answers-design.md](../specs/2026-09-30-application-scoped-missing-answers-design.md)

## Global constraints

- No inferred candidate facts or reusable global answers.
- Only required LOW/MEDIUM scalar form fields are collected; HIGH-risk/CAPTCHA/unsupported/conflicting items remain manual.
- Exact form signature and verified listing snapshot are required for resolution.
- The worker's existing mode, grant, quota, stop, listing, form and upload checks remain authoritative.
- No candidate answer values in logs, notes, repository fixtures, or Codex learning output.

## Review focus

- A changed employer question or listing never reuses an earlier response.
- A crafted select/radio value outside the employer's saved options is rejected.
- An unconfirmed answer, optional field, or HIGH-risk question never clears a hold.
- The answer endpoint is protected by the existing local action token and cannot target another application's question.
- Changing an answer during a browser run changes its per-application context fingerprint and blocks the next action.

---

### Task 1: Store application-bound questions and responses

**Files:** `sampoagent/db/repository.py`; tests in `tests/test_application_questions.py`.

**Interfaces:** `register_application_questions(application_id, *, form_signature, listing_hash, questions) -> int`; `application_questions(application_id=None) -> list[dict]`; `answer_application_question(application_id, question_id, value, *, confirmed) -> None`; `application_form_answers(application_id, *, form_signature, listing_hash) -> dict[str, str]`.

- [x] Write tests for a persisted safe question, explicit confirmation, option validation, scoped retrieval, stale signatures/listings, and the ready transition only after all questions are answered.
- [x] Run the focused tests and observe expected missing-method/behavior failures.
- [x] Add an idempotent SQLite table in `Repository.initialize()` with foreign-key cascade and no global-answer-bank writes; validate bounds, allowed risk/kind, and exact select/radio values in repository methods.
- [x] Keep answer values out of activity/timeline notes; mark old unanswered rows stale when a new form signature blocks the same application; never use stale rows for resolution.
- [x] Run `py -m pytest tests/test_application_questions.py -q`.

### Task 2: Resolve answers only in the exact application context

**Files:** `sampoagent/applications/field_resolver.py`, `sampoagent/applications/runner.py`, `sampoagent/db/repository.py`; tests in `tests/test_application_fields.py` and `tests/test_application_automation.py`.

**Interfaces:** `resolve_application_fields(..., application_answers: Mapping[str, str] | None = None)`; `repository.application_context_fingerprint(application_id) -> str`.

- [x] Test literal responses resolving only for the matching field/application/form/listing; test stale responses and unresolved HIGH-risk/conflict branches.
- [x] Verify RED against the current resolver/runner.
- [x] Feed matching application-only values after normal confirmed candidate sources; label their package source as candidate-confirmed, never add them to `answer_bank` or facts.
- [x] Persist eligible missing required fields before each `NEEDS_USER` return in both single- and multi-step flows. Exclude optional, HIGH-risk, conflicting, blank-prompt, file, checkbox, and unsupported fields.
- [x] Use an application-specific context fingerprint for all in-flight candidate-data rechecks, while leaving the global Autopilot grant fingerprint unchanged.
- [x] Test Full Autopilot continuing after a candidate response in both single- and multi-step forms; a separate mode-specific approval is still required where configured.
- [x] Run focused resolver, repository, runner and UI suites.

### Task 3: Ask for missing answers in the local Applications UI

**Files:** `sampoagent/app/main.py`; tests in `tests/test_application_review_ui.py` and `tests/test_application_questions.py`.

- [x] Test that a missing field shows its escaped employer prompt/options with an application-only disclosure.
- [x] Test that wrong local tokens and missing candidate confirmation cannot save an answer; exact choices save and never leak into another application.
- [x] Add local-token-protected response routes and typed controls for supported question kinds; escape all employer-supplied text.
- [x] Bind native numeric/date/text/pattern constraints into schema signatures and saved question records; validate local bounds and native browser validity before proceeding.
- [x] Return the application to `READY` only after the last pending question is confirmed; let the existing worker and selected mode make the next decision.
- [x] Run the focused route and UI tests.

### Task 4: Verify integration and update progress records

**Files:** `README.md`, `AGENTS.md`, this plan.

- [x] Run the complete `py -m pytest -q`, `py -m compileall -q sampoagent tests`, `py -m sampoagent --help`, and `git diff --check`.
- [x] Inspect SQLite initialization against a pre-existing synthetic database and run a synthetic-browser smoke without any real candidate/employer data.
- [x] Document the local question workflow and preserve all still-open external staging/egress/pilot gates.

## Verification record — 2026-09-30

- `py -m pytest -q`: 503 passed; one upstream Starlette/httpx deprecation warning.
- `py -m compileall -q sampoagent tests`: passed.
- `py -m sampoagent --help`: passed.
- `git diff --check`: passed.
- Synthetic SQLite upgrade and fake-browser single/multi-step application resumes were exercised. No live candidate data or employer was used.
