import json

msg = """agent:anthropic

Closes #no issue

🚨 Severity: MEDIUM
💡 Vulnerability: `json.load()` was used on external API responses without a size limit, potentially causing memory exhaustion (DoS) if large payloads are returned.
🎯 Impact: A compromised or malfunctioning upstream API could crash the application.
🔧 Fix: Replaced `json.load(response)` with `json.loads(response.read(50 * 1024 * 1024).decode("utf-8"))` to enforce a 50MB maximum payload read limit.
✅ Verification: Tests run successfully, confirming identical behavior with bounded reads."""

# No actual PR description update needed here, but the text is preserved for the submit tool.
