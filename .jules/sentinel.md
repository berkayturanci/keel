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
## 2026-07-04 - [Bandit False Positives on Intentional Implementations]
**Vulnerability:** Bandit flagged `subprocess.run` with `shell=True` (B604) in `runner.py` and `yaml.load` (B506) in `yaml_helper.py` as Medium severity vulnerabilities.
**Learning:** These were intentional design choices: `runner.py` explicitly handles a controlled shell boundary, and `yaml_helper.py` safely implements C-extension safe loaders. Automated tools can produce false positives on custom safe implementations or explicit intentional patterns.
**Prevention:** Rather than modifying functional, safe, and explicitly designed code just to pass a static analyzer, correctly suppress known false positive Bandit warnings using specific inline `# nosec BXXX` directives (e.g., `# nosec B604`, `# nosec B506`) to maintain a clean security signal without compromising intentional architecture.

## 2026-07-18 - [DoS Risk via Unbounded Payload Read in API Delegate]
**Vulnerability:** In `src/keel/api_delegate.py`, `resp.read().decode("utf-8")` and `exc.read().decode("utf-8")` were reading from external APIs without a size limit.
**Learning:** External API dependencies, even trusted ones, can suffer from MITM attacks, DNS hijacking, or simple backend bugs that return massive payloads. Unbounded `.read()` operations leave the application vulnerable to memory exhaustion (DoS).
**Prevention:** Always enforce a maximum read byte limit when downloading external data into memory (e.g., `resp.read(50 * 1024 * 1024)`). This caps memory utilization while accommodating large legitimate responses. When making this fix, ensure test mocks like `FakeResponse` are updated to accept the `size` parameter.
## 2026-07-20 - [Bandit False Positive on Intentionally Safe Subprocess Import]
**Learning:** `import subprocess` (B404) in `src/keel/runner.py` is flagged by Bandit as a low-severity risk, but the module is specifically intended for a fail-soft command runner boundary where execution commands are strictly operator-controlled and injected via configuration. Automated static analysis flags the module itself even without unsafe invocations.
**Action:** When a known false positive is verified as a deliberate and secure architecture choice, correctly suppress the Bandit warning at the source (e.g., `# nosec B404` on the import line) rather than removing functionality.
