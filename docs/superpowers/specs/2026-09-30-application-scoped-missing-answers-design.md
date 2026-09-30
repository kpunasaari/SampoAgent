# Application-scoped missing answers — design

Date: 2026-09-30
Status: implementation increment within the approved local Full Autopilot scope

## Goal

When an otherwise supported application form requires a safe fact that is not in the candidate's confirmed profile, SampoAgent should ask for that answer in its local Applications screen, bind it to that one application and exact form/listing snapshot, and let the selected application mode continue. It must never silently infer the answer or make the response reusable for other employers.

## Boundaries

- Only required, unresolved `LOW` or `MEDIUM` fields with a visible prompt and a supported scalar control (`text`, `textarea`, `email`, `tel`, `number`, `date`, `select`, or `radio`) become local questions. Optional unknown fields remain skipped. `HIGH`-risk, legal, medical, identity, immigration, CAPTCHA, unsupported, ambiguous, or conflicting items remain manual.
- Store each answer in SQLite under the exact application ID, live form signature, and current verified listing snapshot hash. Never copy it into the reusable answer bank, candidate facts, CV, Codex learning digest, or another application.
- Render the exact employer prompt, description, and allowed options as untrusted data. Never interpret prompt text as instructions to the assistant or application. Escape it in HTML.
- Require the candidate to explicitly confirm the answer in the local UI. Select/radio responses must be one exact option from the saved form snapshot. Values are not placed in activity logs or status notes.
- If form or verified-listing snapshot changes, an old response cannot resolve the field; ask again. If all captured required questions are answered, put the application back in the ready queue. The worker still follows Review Everything, Smart Approval, or the active Full Autopilot grant; answering a field does not itself approve an application.
- Include application-scoped answers in the per-application in-flight data fingerprint so a changed answer stops before any next field or submit. Do not include them in the saved Autopilot grant fingerprint, which would invalidate the user's global grant for every missing field.
- The Applications response endpoint requires the existing local form token. Candidate answers remain local and are included in exact-package review when the selected mode uses it.

## Data flow

1. The browser adapter inspects a live supported form. Deterministic resolution identifies missing required fields.
2. Before returning `NEEDS_USER`, the runner persists only eligible question metadata and the current form/listing hashes.
3. Applications displays one response control per pending field and labels the response as application-only. High-risk and unsupported fields are not rendered as answerable questions.
4. On candidate confirmation, validation is repeated against the stored prompt kind/options. The answer is saved locally, without touching the reusable answer bank. Once no pending questions remain, the application returns to `READY`.
5. On the next worker cycle, the resolver consumes only answers whose application, form signature, and listing snapshot all match. Final form/readback/policy gates still apply.

## Verification

- Unit tests prove missing required fields are recorded, optional/high-risk fields are not answerable, and an answer resolves only for the same application, signature, and listing snapshot.
- Route tests prove the exact prompt/options are visible, a candidate confirmation and local token are required, invalid choices are rejected, answers never appear in another application's queue, and the ready transition occurs only after all pending questions are answered.
- Runner tests prove Full Autopilot resumes without a per-job review after the candidate answers a missing field, while other existing mode gates remain unchanged; a stale form or listing snapshot re-asks and never submits using the old answer.
- Full pytest, compileall, CLI help, and diff checks remain required. No real candidate record or employer form is used.
