# Browser egress pinning design

Status: implementation approved under the user's standing instruction to build SampoAgent autonomously through its release gates; not a claim of production certification.

## Intent and constraints

The Playwright form adapter currently preflights DNS answers in Python and then lets Chromium resolve the hostname again. A rebinding answer can change between those operations. Keep the existing safety contract—HTTPS only, no private/reserved destinations, application traffic limited to the inspected employer origin, no WebSockets—and make the browser's actual TCP destination use the exact IP address that the local policy validates. Preserve end-to-end TLS: the proxy must not terminate or inspect employer HTTPS.

## Chosen design

Run a small ephemeral HTTP CONNECT proxy on loopback for the lifetime of the Playwright context. Playwright's persistent context uses that proxy for network traffic. The proxy accepts CONNECT only. In application mode it permits only the exact host and port of the current HTTPS origin; in the separate manually controlled browser session it permits only HTTPS port 443 and public destinations. It resolves the host once, rejects the whole DNS result set if any address is non-global, and connects to a validated numeric IP literal. It tunnels the browser's original TLS stream without interception. No third-party dependency is needed.

The request route remains a second application-layer check for URL scheme, credentials, sensitive URL parameters, and the exact employer origin, but no longer treats its DNS preflight as the final network boundary. The proxy's pinned connection is the final DNS decision. Proxy shutdown is tied to browser shutdown and startup failures fail closed.

## Alternatives considered

1. Chromium host-resolver rules: avoids a second name lookup but adds browser-specific mutable mapping state and is difficult to manage for IPv4/IPv6 rotation and allowed-origin changes.
2. OS firewall rules: strongest containment but requires administrator/platform-specific installation and cannot be enabled portably by this local app.
3. Browser-only DNS preflight: already exists and has the documented rebinding race, so it does not meet this design's goal.

The loopback CONNECT proxy is the best portable application-layer step. It does not claim to replace OS-level egress controls; that release gate remains until real-browser proxy failure/fallback behavior is verified on supported platforms.

## Safety and failure behavior

- Only a syntactically valid CONNECT is considered.
- The job-application context can tunnel only to the exact origin opened by SampoAgent; other hostnames/ports are rejected even if DNS is public. A non-default HTTPS port is allowed only when it is part of that exact inspected origin.
- The manually controlled browser context can tunnel only to HTTPS port 443.
- Every resolved A/AAAA address must be globally routable. Mixed public/private answers fail closed.
- The TCP connector receives a numeric validated address, not the requested hostname; TLS SNI and certificate validation remain browser-owned inside the tunnel.
- Resolution errors, proxy bind/start errors, invalid authorities, and upstream connection errors fail closed without direct application fallback being intentionally configured.
- Proxy diagnostics must not log URL paths, query strings, candidate data, or employer form content.
- Existing CAPTCHA, login/MFA, high-risk question, consent, form-signature, and final-click controls remain unchanged.

## Verification

Use deterministic tests with fake DNS/connect functions to prove: mixed/private answers are rejected; a public hostname is connected using its validated IP literal; disallowed origin/port and non-CONNECT requests are denied; and the Playwright context receives only the local proxy configuration. Keep the real Chromium synthetic-form suite. Do not contact a live employer or submit an application. Document the residual OS-level egress and real-browser failover verification gate explicitly.
