# Email sender identity and Full Autopilot email applications

Date: 2026-09-30
Status: implemented; 442 synthetic tests pass, independent review found no remaining actionable findings; no live account or application was used

## Objective

Allow the local SampoAgent worker to handle an explicit email-by-application route without asking the candidate to confirm each recipient, but only when a current verified public listing itself gives one unambiguous application email address and the candidate has separately enabled the time-limited email-send Autopilot grant. Make the exact sender account visible and bind its identity to every package and grant.

This does not authorize Codex to submit applications. It does not enable a generic email sender, infer an address with an LLM, read a mailbox to choose a sender, send follow-ups, or claim message delivery.

## Sender identity and account lifecycle

- Request the existing dedicated send scope plus OpenID Connect `openid email`; do not add Gmail read scopes or Microsoft `Mail.Read`/`User.Read` to the send grant.
- Retrieve identity only from the fixed official provider UserInfo endpoint over HTTPS. Require a non-empty stable `sub` and one syntactically valid email claim; if either is unavailable or invalid, do not connect the account. For Google, require `email_verified=true`. For Microsoft, label the returned value “provider-reported address”; never describe it as independently verified.
- Keep account tokens encrypted. Store the provider subject only for identity binding, the displayed address, scopes, connection timestamp and token expiry locally. Support multiple accounts; one explicit default account is selected for outgoing messages.
- Existing legacy send credentials have no captured OIDC identity. Keep their bytes recoverable in the legacy row, but do not select them for sending. The UI states that reconnection is required to identify the account. Disconnect removes legacy and current send accounts and revokes email-Autopilot consent.
- Email-Autopilot consent binds the general Autopilot scope and grant, selected sender account ID + subject + provider + address + connection timestamp, granted send scopes, and expiry. Changing default, reconnecting/replacing an account, changing scopes or general candidate/search scope invalidates this consent.
- Every encrypted email package binds exact From address/account ID, To, subject/body/language, verified listing snapshot and fingerprint, recipient provenance, candidate/search scope, CV path/name/hash and provider. The UI shows From for both ready and terminal outbox states. Refreshing access tokens must not change identity.

## Deterministic application-recipient extraction

- Examine only the exact current verified job description already stored by SampoAgent. Never fetch another URL, inspect arbitrary contacts, or use an AI model to guess a recipient.
- Recognize explicit application-by-email instructions in Finnish, English or Swedish. A contact address alone is not an application route.
- Accept exactly one unique syntactically valid address located within the bounded context of an accepted application instruction. Multiple addresses, contradictory instructions, malformed addresses, or no address are ambiguous. Store the cue identifier and verified snapshot hash as encrypted provenance; do not log the address or raw excerpt.
- In Full Autopilot, an explicit email-application cue is handled as an email route, not sent through a browser form. If recipient, current verified snapshot, confirmed candidate facts, archived job-specific PDF, active send account, or separate grant is missing, hold that application and continue unrelated eligible jobs. Never fall back from an ambiguous email route to a browser submission.
- If there is no explicit email-application cue, preserve the normal supported-form worker path.
- Before any provider call, retain all existing dry-run, pause, scope, fresh-listing, daily quota, unique-application, encrypted-package, application claim and no-retry checks. READY → EMAIL_READY must fence the browser path. An uncertain provider response remains UNKNOWN and is never replayed.
- Manual mode retains the existing per-application recipient confirmation and exact-message review.

## Acceptance criteria

1. Send scopes add only `openid email` to the corresponding send permission; read scopes remain unchanged.
2. Missing/invalid provider identity fails closed and stores no new send account. Google unverified email fails closed; Microsoft address is explicitly provider-reported.
3. Multiple accounts can coexist; changing the selected default invalidates the email-Autopilot grant and ready packages for the old identity.
4. Legacy single-slot tokens cannot be used by any new send path until the account reconnects and identity is established.
5. FI/EN/SV recipient extraction accepts explicit contextual application addresses only; unrelated contacts, zero/multiple candidates and conflicting cues never auto-send.
6. Worker automatically creates and sends one eligible verified email application without a per-job prompt only under both current Autopilot grants. Exact From/To/recipient-source and package hash are visible locally.
7. Ambiguous/ineligible email postings are held without browser fallback; normal web-form postings still use the browser runner.
8. Duplicate worker attempts, changed snapshot/scope/CV/account, quota, pause, dry-run, provider errors and interrupted send cannot produce a second provider call.
9. Tests use synthetic local databases and mocked provider HTTP; no live OAuth, email or employer submission occurs.

## Release gates unchanged

Employer-controlled ATS staging E2E, DNS-pinned egress, real user-controlled OAuth/send rehearsal, deterministic Sent-folder reconciliation, redaction review, availability policy and supervised pilot remain hard gates. Passing synthetic tests is not production or universal-automation certification.
