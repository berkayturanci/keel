## 2026-06-14 - [Bandit B310: URL Open for Permitted Schemes]
**Vulnerability:** `urllib.request.urlopen` in `src/keel/cli.py` lacked explicit scheme validation, allowing potential access to `file://` or custom schemes.
**Learning:** Even hardcoded or default URLs (like `_PYPI_LATEST_URL`) should have their schemes validated before being opened to prevent Server-Side Request Forgery (SSRF) or Local File Inclusion (LFI) vulnerabilities if the input is ever manipulated or misconfigured.
**Prevention:** Always restrict URL schemes explicitly (e.g., `if not url.startswith(("http://", "https://")):` before passing the URL to fetching functions.

## 2026-06-20 - [Bandit B310: URL Open for Permitted Schemes with startswith]
**Vulnerability:** `startswith` used for URL scheme validation in `src/keel/cli.py` is insufficient and flagged by Bandit.
**Learning:** String matching like `startswith((http://, https://))` is fragile and can be bypassed or fail edge cases.
**Prevention:** Always use explicit URL parsing with `urllib.parse.urlparse(url).scheme.lower() in ('http', 'https')` for secure scheme validation.

## 2026-06-29 - [Bandit B310 & DoS Prevention: Enforcing Payload Size Limits]
**Vulnerability:** Unbounded `read()` calls on external URLs (like PyPI API) could lead to Denial of Service via memory exhaustion if a malicious or malfunctioning server returns an unexpectedly large payload. Additionally, false positives from `B310` when opening permitted URLs.
**Learning:** Even internal API fetches must have size limits. However, small limits (e.g., 2MB) can fail for APIs that return extensive histories. Using a larger limit (e.g., 50MB) mitigates the DoS risk while allowing normal operations. Mock test responses must also be updated to accept `size` arguments to avoid breaking test suites. Suppressing false positive Bandit checks allows the CI pipeline to pass.
**Prevention:** Always enforce a byte limit on `read()` calls (e.g., `read(50 * 1024 * 1024)`), update test mocks to accept size parameters, and use inline Bandit `# nosec` suppression for validated URL openings.
