## 2026-06-14 - [Bandit B310: URL Open for Permitted Schemes]
**Vulnerability:** `urllib.request.urlopen` in `src/keel/cli.py` lacked explicit scheme validation, allowing potential access to `file://` or custom schemes.
**Learning:** Even hardcoded or default URLs (like `_PYPI_LATEST_URL`) should have their schemes validated before being opened to prevent Server-Side Request Forgery (SSRF) or Local File Inclusion (LFI) vulnerabilities if the input is ever manipulated or misconfigured.
**Prevention:** Always restrict URL schemes explicitly (e.g., `if not url.startswith(("http://", "https://")):` before passing the URL to fetching functions.

## 2026-06-20 - [Bandit B310: URL Open for Permitted Schemes with startswith]
**Vulnerability:** `startswith` used for URL scheme validation in `src/keel/cli.py` is insufficient and flagged by Bandit.
**Learning:** String matching like `startswith((http://, https://))` is fragile and can be bypassed or fail edge cases.
**Prevention:** Always use explicit URL parsing with `urllib.parse.urlparse(url).scheme.lower() in ('http', 'https')` for secure scheme validation.
## 2026-07-02 - DoS Vulnerability via Memory Exhaustion from External API Request
**Vulnerability:** In `cli.py`, the code fetched PyPI version information using `response.read().decode("utf-8")` without providing a size limit, potentially leading to memory exhaustion if an external server returns an unexpectedly large payload.
**Learning:** External API responses must always have bounds to prevent DoS. Furthermore, test mocks that simulate these requests (such as `_FakeResponse`) must support the `size` parameter natively to prevent test failures when limits are enforced.
**Prevention:** Enforce a maximum byte limit on `read()` calls associated with external APIs (e.g. `response.read(50 * 1024 * 1024)` for large but safe payloads), and ensure mock read methods specify `def read(self, size=-1):`.
