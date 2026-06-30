## 2026-06-14 - [Bandit B310: URL Open for Permitted Schemes]
**Vulnerability:** `urllib.request.urlopen` in `src/keel/cli.py` lacked explicit scheme validation, allowing potential access to `file://` or custom schemes.
**Learning:** Even hardcoded or default URLs (like `_PYPI_LATEST_URL`) should have their schemes validated before being opened to prevent Server-Side Request Forgery (SSRF) or Local File Inclusion (LFI) vulnerabilities if the input is ever manipulated or misconfigured.
**Prevention:** Always restrict URL schemes explicitly (e.g., `if not url.startswith(("http://", "https://")):` before passing the URL to fetching functions.

## 2026-06-20 - [Bandit B310: URL Open for Permitted Schemes with startswith]
**Vulnerability:** `startswith` used for URL scheme validation in `src/keel/cli.py` is insufficient and flagged by Bandit.
**Learning:** String matching like `startswith((http://, https://))` is fragile and can be bypassed or fail edge cases.
**Prevention:** Always use explicit URL parsing with `urllib.parse.urlparse(url).scheme.lower() in ('http', 'https')` for secure scheme validation.

## 2026-06-30 - [Bandit B310: Suppressed False Positive & Fixed DoS in URL Fetch]
**Vulnerability:** The `urllib.request.urlopen` call in `src/keel/cli.py` lacked a read limit, opening it up to memory exhaustion DoS if a massive payload was returned by the external server. Additionally, Bandit flagged it with `B310` despite scheme validation already being properly done using `urlparse`.
**Learning:** Always explicitly bound reads from external sources to mitigate DoS attacks. The `urlopen(url).read()` method should always be passed a size constraint like `response.read(50 * 1024 * 1024)`. If security controls (like checking URL scheme) are properly placed but static analyzers still complain, use `# nosec BXXX` carefully and explicitly document why it's a false positive.
**Prevention:** Implement limits (e.g., `read(max_bytes)`) for all HTTP response reads. Add `# nosec B310` only *after* confirming scheme validation is handled via explicit `urllib.parse` parsing. Ensure tests are updated (e.g., adjusting test mocks to accept a `size` parameter) to accommodate the new parameters.
