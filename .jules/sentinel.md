## 2026-06-14 - [Bandit B310: URL Open for Permitted Schemes]
**Vulnerability:** `urllib.request.urlopen` in `src/keel/cli.py` lacked explicit scheme validation, allowing potential access to `file://` or custom schemes.
**Learning:** Even hardcoded or default URLs (like `_PYPI_LATEST_URL`) should have their schemes validated before being opened to prevent Server-Side Request Forgery (SSRF) or Local File Inclusion (LFI) vulnerabilities if the input is ever manipulated or misconfigured.
**Prevention:** Always restrict URL schemes explicitly (e.g., `if not url.startswith(("http://", "https://")):` before passing the URL to fetching functions.

## 2026-06-20 - [Bandit B310: URL Open for Permitted Schemes with startswith]
**Vulnerability:** `startswith` used for URL scheme validation in `src/keel/cli.py` is insufficient and flagged by Bandit.
**Learning:** String matching like `startswith((http://, https://))` is fragile and can be bypassed or fail edge cases.
**Prevention:** Always use explicit URL parsing with `urllib.parse.urlparse(url).scheme.lower() in ('http', 'https')` for secure scheme validation.

## 2024-05-18 - Prevented DoS by Limiting External Payload Read Size
**Vulnerability:** External HTTP response body payload (from PyPI) was read into memory entirely without size limits using `response.read()`, posing a memory exhaustion (Denial of Service) risk if the endpoint serves excessively large data.
**Learning:** Functions that parse remote JSON payloads typically read the stream synchronously. Even trusted sources like PyPI can experience caching errors or return unexpected giant payloads.
**Prevention:** Use byte size limit to safely read from external sources like `response.read(50 * 1024 * 1024)`. Make sure to mock the optional `size` parameter in tests.
