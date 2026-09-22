# Security policy and local trust model

SampoAgent is designed for local use. The server binds to `127.0.0.1`; do not expose it to an untrusted network. Candidate data, CVs, and generated documents are private local data and must never be committed. `.gitignore` excludes databases, uploads, generated application data, and environment files.

The app does not store provider keys in SQLite. It rejects unsupported uploads, preserves provenance, and requires user action for unknown or high-risk application answers. It does not circumvent CAPTCHA, login, robots rules, or access controls. Please report vulnerabilities privately to the maintainer before public disclosure.
