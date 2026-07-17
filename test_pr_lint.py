import os, re, sys, pathlib

def strip_comments(text):
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)

body = """🚨 Severity: MEDIUM
💡 Vulnerability: `resp.read()` and `exc.read()` in `src/keel/api_delegate.py` fetched external API response bodies without an upper bound limit, presenting a risk of memory exhaustion (DoS).
🎯 Impact: A malicious endpoint or DNS intercept could return extremely large payloads, crashing the application process due to Out of Memory (OOM) errors.
🔧 Fix: Enforced a 50MB read limit (`read(50 * 1024 * 1024)`) on both successful responses and error bodies, and updated the test mocks to accommodate the size argument.
✅ Verification: Ran `make lint` and `make test` which both passed successfully. Logged the learning to the Sentinel journal.

no issue
"""

authored = strip_comments(body)
has_ref = bool(re.search(r"#\d+", authored)) or bool(
    re.search(r"\bno[ -]?issue\b", authored, re.I)
)
print(f"has_ref: {has_ref}")
