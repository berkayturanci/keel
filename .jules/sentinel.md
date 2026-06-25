## 2026-06-14 - [Bandit B310: URL Open for Permitted Schemes]
**Vulnerability:** `urllib.request.urlopen` in `src/keel/cli.py` lacked explicit scheme validation, allowing potential access to `file://` or custom schemes.
**Learning:** Even hardcoded or default URLs (like `_PYPI_LATEST_URL`) should have their schemes validated before being opened to prevent Server-Side Request Forgery (SSRF) or Local File Inclusion (LFI) vulnerabilities if the input is ever manipulated or misconfigured.
**Prevention:** Always restrict URL schemes explicitly (e.g., `if not url.startswith(("http://", "https://")):` before passing the URL to fetching functions.

## 2026-06-20 - [Bandit B310: URL Open for Permitted Schemes with startswith]
**Vulnerability:** `startswith` used for URL scheme validation in `src/keel/cli.py` is insufficient and flagged by Bandit.
**Learning:** String matching like `startswith((http://, https://))` is fragile and can be bypassed or fail edge cases.
**Prevention:** Always use explicit URL parsing with `urllib.parse.urlparse(url).scheme.lower() in ('http', 'https')` for secure scheme validation.

## 2026-06-25 - [DoS Risk via Unbounded Payload Read from PyPI Metadata]
**Vulnerability:** In `src/keel/cli.py`, `json.loads(response.read().decode("utf-8"))` fetched data from `urlopen` without a size limit, potentially allowing memory exhaustion (DoS) if the upstream metadata endpoint returns an unexpectedly massive payload.
**Learning:** Even trusted or well-known endpoints like PyPI JSON endpoints (`https://pypi.org/pypi/*/json`) can sometimes return very large responses due to extensive release histories or malicious MITM/DNS hijacking if validation fails. Using an unbounded `.read()` opens the application up to memory consumption attacks or accidental crashes.
**Prevention:** Always enforce a maximum read byte limit when downloading data into memory. For large API metadata where a 2MB limit may be too restrictive, a conservative cap like 50MB (e.g., `response.read(50 * 1024 * 1024)`) should be used to provide robust DoS protection.
