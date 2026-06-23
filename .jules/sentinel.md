## 2026-06-14 - [Bandit B310: URL Open for Permitted Schemes]
**Vulnerability:** `urllib.request.urlopen` in `src/keel/cli.py` lacked explicit scheme validation, allowing potential access to `file://` or custom schemes.
**Learning:** Even hardcoded or default URLs (like `_PYPI_LATEST_URL`) should have their schemes validated before being opened to prevent Server-Side Request Forgery (SSRF) or Local File Inclusion (LFI) vulnerabilities if the input is ever manipulated or misconfigured.
**Prevention:** Always restrict URL schemes explicitly (e.g., `if not url.startswith(("http://", "https://")):` before passing the URL to fetching functions.

## 2026-06-20 - [Bandit B310: URL Open for Permitted Schemes with startswith]
**Vulnerability:** `startswith` used for URL scheme validation in `src/keel/cli.py` is insufficient and flagged by Bandit.
**Learning:** String matching like `startswith((http://, https://))` is fragile and can be bypassed or fail edge cases.
**Prevention:** Always use explicit URL parsing with `urllib.parse.urlparse(url).scheme.lower() in ('http', 'https')` for secure scheme validation.

## 2026-06-23 - [DoS Risk: Missing Read Limit on External Fetches]
**Vulnerability:** `response.read()` in `_fetch_latest_pypi_version` was called without a size limit, making it vulnerable to DoS attacks (memory exhaustion) if the target server returns an infinite stream of data.
**Learning:** Even trusted or seemingly benign endpoints like PyPI JSON metadata can be compromised or MITM'd. Unbounded reads into memory are a classic DoS vector.
**Prevention:** Always provide a maximum byte limit (e.g., `response.read(2 * 1024 * 1024)`) when fetching external content, even for JSON.
