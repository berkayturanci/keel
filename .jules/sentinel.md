## 2026-06-14 - [Bandit B310: URL Open for Permitted Schemes]
**Vulnerability:** `urllib.request.urlopen` in `src/keel/cli.py` lacked explicit scheme validation, allowing potential access to `file://` or custom schemes.
**Learning:** Even hardcoded or default URLs (like `_PYPI_LATEST_URL`) should have their schemes validated before being opened to prevent Server-Side Request Forgery (SSRF) or Local File Inclusion (LFI) vulnerabilities if the input is ever manipulated or misconfigured.
**Prevention:** Always restrict URL schemes explicitly (e.g., `if not url.startswith(("http://", "https://")):` before passing the URL to fetching functions.

## 2026-06-20 - [Bandit B310: URL Open for Permitted Schemes with startswith]
**Vulnerability:** `startswith` used for URL scheme validation in `src/keel/cli.py` is insufficient and flagged by Bandit.
**Learning:** String matching like `startswith((http://, https://))` is fragile and can be bypassed or fail edge cases.
**Prevention:** Always use explicit URL parsing with `urllib.parse.urlparse(url).scheme.lower() in ('http', 'https')` for secure scheme validation.
## YYYY-MM-DD - [DoS Prevention on External Payloads]
**Vulnerability:** External HTTP requests (e.g. `urlopen` for PyPI metadata) without a read size limit could be exploited to cause a Denial of Service via memory exhaustion if the response is unexpectedly large.
**Learning:** Python's `response.read()` will read the entire response into memory. External endpoints, even trusted ones, should have a safe upper bound. For JSON payloads that can grow significantly (like PyPI history), use a generous but finite limit like 50MB (`response.read(50 * 1024 * 1024)`). Also, Bandit's `B310` rule for `urlopen` can trigger false positives if the URL scheme (`http`/`https`) has already been explicitly validated before the call; it can be suppressed with `# nosec B310`.
**Prevention:** Always enforce a maximum byte limit on `read()` calls when consuming external content, and ensure mock `read` methods in tests are updated to accept the `size` parameter (`def read(self, size=-1):`).
