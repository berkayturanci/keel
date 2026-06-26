## 2026-06-14 - [Bandit B310: URL Open for Permitted Schemes]
**Vulnerability:** `urllib.request.urlopen` in `src/keel/cli.py` lacked explicit scheme validation, allowing potential access to `file://` or custom schemes.
**Learning:** Even hardcoded or default URLs (like `_PYPI_LATEST_URL`) should have their schemes validated before being opened to prevent Server-Side Request Forgery (SSRF) or Local File Inclusion (LFI) vulnerabilities if the input is ever manipulated or misconfigured.
**Prevention:** Always restrict URL schemes explicitly (e.g., `if not url.startswith(("http://", "https://")):` before passing the URL to fetching functions.

## 2026-06-20 - [Bandit B310: URL Open for Permitted Schemes with startswith]
**Vulnerability:** `startswith` used for URL scheme validation in `src/keel/cli.py` is insufficient and flagged by Bandit.
**Learning:** String matching like `startswith((http://, https://))` is fragile and can be bypassed or fail edge cases.
**Prevention:** Always use explicit URL parsing with `urllib.parse.urlparse(url).scheme.lower() in ('http', 'https')` for secure scheme validation.

## 2026-06-26 - [DoS Prevention via Payload Limit]
**Vulnerability:** External fetch (e.g. `urlopen` reading PyPI release JSON) was calling `response.read()` without any size limit, risking memory exhaustion (DoS).
**Learning:** Even trusted or seemingly benign endpoints like PyPI JSON metadata can contain extensive history, making them unexpectedly large. While small limits (like 2MB) might break the read, unbounded reads are unsafe against DoS via maliciously inflated payloads.
**Prevention:** Always enforce a generous but strict maximum byte limit on `read()` for external payloads (e.g. `response.read(50 * 1024 * 1024)` for 50MB) rather than an unbounded `read()`.
