# Browser egress pinning — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent DNS rebinding between SampoAgent's browser URL checks and Chromium's actual employer-site connection by using a loopback CONNECT proxy that pins each allowed HTTPS connection to a validated public IP.

**Architecture:** A small standard-library proxy owns DNS resolution and numeric-IP dialing; Playwright uses it as its context proxy. Existing route guards continue enforcing application origin and URL policy. TLS remains end-to-end. OS-level egress and browser fallback remain explicit release gates until separately verified.

**Tech Stack:** Python 3.11+, `http.server`/`socketserver`/`socket`/`select`, Playwright Python (optional), pytest.

**Spec:** [2026-09-30-browser-egress-pinning-design.md](../specs/2026-09-30-browser-egress-pinning-design.md)

## Global Constraints

- Never terminate TLS or inspect employer traffic.
- Never resolve a host a second time after selecting the upstream numeric IP.
- Reject DNS answers unless every answer is globally routable; fail closed on errors.
- In an application context, only the exact allowed origin and its HTTPS port may be tunneled; a manually controlled browser context is limited to port 443.
- No direct proxy bypass list or intentional direct-network fallback.
- Do not alter CAPTCHA, login/MFA, high-risk question, privacy-consent, or final-submit policy.
- No live job site, user profile, candidate database, OAuth account, or application submission in tests.
- Keep claims narrow: a loopback proxy is not OS firewall containment or production certification.

## Review Focus

- A malicious/mixed DNS response must not result in any upstream connection.
- The connector must receive a numeric validated IP, never the hostname.
- Unexpected destination host/port, malformed CONNECT authorities (including empty userinfo/query/fragment delimiters), HTTP GET, and DNS/connect failures must fail closed.
- Playwright must not be launched without the local proxy after the adapter's URL policy is enabled.
- Proxy lifetime must end on context close and on partial startup failure; explicit proxy close must terminate active tunnels and invalidate delayed DNS results before proxy restart.
- Existing browser submission guards and synthetic ATS tests must remain unchanged in behavior.

---

### Task 1: Add and integrate the pinned HTTPS proxy

**Files:**
- Create: `sampoagent/agents/pinned_proxy.py`
- Modify: `sampoagent/agents/playwright_adapter.py`
- Test: `tests/test_pinned_proxy.py`
- Test: `tests/test_playwright_adapter.py`
- Modify: `docs/superpowers/specs/2026-09-29-full-application-automation-design.md`
- Modify: `AGENTS.md`

**Interfaces:** `PinnedHttpsProxy` exposes `start()`, `set_allowed_origin(url | None)`, `proxy_url`, and idempotent `close()`. Pure helpers validate DNS answers and open a socket to a numeric address. Playwright's persistent context receives `proxy={"server": proxy_url}`. The browser route uses URL/origin checks but delegates final DNS/IP enforcement to the proxy.

- [x] Add failing unit tests `test_proxy_rejects_mixed_public_private_dns_answers`, `test_proxy_connects_to_validated_numeric_ip_without_second_hostname_resolution`, `test_proxy_rejects_cross_origin_and_wrong_port`, `test_manual_proxy_rejects_non_443_connect`, `test_proxy_rejects_non_connect_requests`, and `test_proxy_fails_closed_when_resolution_or_connect_fails`.
- [x] Run focused tests and confirm they fail for the missing proxy behavior (missing module, then missing proxy-auth challenge).
- [x] Implement the standard-library threaded CONNECT tunnel, safe shutdown, and Playwright lifecycle integration.
- [x] Add `test_browser_start_installs_local_pinned_proxy_before_page_use`, `test_proxy_is_closed_when_browser_start_fails`, and `test_browser_request_guard_does_not_resolve_dns_before_proxy`.
- [x] Run: `py -m pytest tests/test_pinned_proxy.py tests/test_playwright_adapter.py tests/test_application_automation.py tests/test_application_worker.py -q` → 66 passed.
- [x] Run: `py -m compileall -q sampoagent` and `git diff --check`.
- [x] Verify existing synthetic Chromium form matrix and all live-submit guard tests pass without network access: full suite → 453 passed, one upstream Starlette/httpx deprecation warning.
- [x] Update design/AGENTS wording with the implemented boundary and outstanding OS-level/browser failover gates.
- [x] Commit the completed task as `feat: pin browser https egress through local proxy`.

#### Review follow-up (2026-09-30)

- [x] Reproduce malformed CONNECT authorities that `urlsplit()` normalized and assert zero DNS/connector calls.
- [x] Reproduce post-close bytes traversing an active tunnel and terminate active client/upstream sockets on close.
- [x] Invalidate delayed resolver work across close/restart; serialize the bounded connector phase with proxy shutdown.
- [x] Tie unexpected Playwright context close to proxy shutdown.
- [x] Run the full verification suite again and record final review disposition: the read-only review found two Important findings, both were reproduced and fixed; no Critical findings were reported. Final full suite: 459 passed, 1 upstream deprecation warning.

**Expected:** proxy tests, Playwright guard tests and application runner safety tests pass; the application browser's request path uses a pinned numeric upstream address; existing application submission behavior is unchanged.

**Commit:** `feat: pin browser https egress through local proxy`
