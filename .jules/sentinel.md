## 2026-06-14 - [Bandit B310: URL Open for Permitted Schemes]
**Vulnerability:** `urllib.request.urlopen` in `src/keel/cli.py` lacked explicit scheme validation, allowing potential access to `file://` or custom schemes.
**Learning:** Even hardcoded or default URLs (like `_PYPI_LATEST_URL`) should have their schemes validated before being opened to prevent Server-Side Request Forgery (SSRF) or Local File Inclusion (LFI) vulnerabilities if the input is ever manipulated or misconfigured.
**Prevention:** Always restrict URL schemes explicitly (e.g., `if not url.startswith(("http://", "https://")):` before passing the URL to fetching functions.

## 2026-06-20 - [Bandit B310: URL Open for Permitted Schemes with startswith]
**Vulnerability:** `startswith` used for URL scheme validation in `src/keel/cli.py` is insufficient and flagged by Bandit.
**Learning:** String matching like `startswith((http://, https://))` is fragile and can be bypassed or fail edge cases.
**Prevention:** Always use explicit URL parsing with `urllib.parse.urlparse(url).scheme.lower() in ('http', 'https')` for secure scheme validation.

## 2026-06-24 - [DoS via Memory Exhaustion in URL Read]
**Vulnerability:** Reading external URL payloads without limits allowed arbitrary memory exhaustion.
**Learning:** Legitimate JSON endpoints like PyPI metadata can be significantly larger than expected (e.g., >10MB). A limit of 2MB truncated data causing JSON decode errors, highlighting that security limits must not regress core functionality.
**Prevention:** Always enforce byte limits on external reads (e.g., `response.read(50 * 1024 * 1024)`), but size the limit appropriately for the expected data profile to prevent functionality regressions.
