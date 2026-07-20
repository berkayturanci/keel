"""Thin I/O: hosted-API code-generation delegate (issue #548).

Lets the s4 implement / s7 review steps run with **only an API token in the
environment** (``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``) and no agent CLI
installed. The delegate follows the same no-tools contract as ``ollama:MODEL``
in ship.md s4: the orchestrator owns every git/PR step and calls this module
exactly once per attempt to turn a prompt into text (a unified diff for the
implementer, a structured verdict for the reviewer).

Design (docs/proposals/api-token-delegate.md):

- **Stdlib only** — plain ``urllib`` over an opener that registers only
  HTTP/HTTPS handlers and follows no redirects (the SSRF-safe pattern from
  ai-jury's hosted adapters). No vendor SDK, no new runtime dependency.
- **Fail-soft** — like ``runner``/``git``/``github``, every failure becomes an
  :class:`ApiResult` with an ``error_code``; nothing here raises. HTTP 429 maps
  to ``rate-limit`` so the caller can honour the no-retry-on-quota rule.
- **Secrets** — the key is read from the environment only, validated against
  header injection, and scrubbed out of any error text before it is surfaced.
  The ``secrets`` consent-scope requirement is part of the adapter contract
  (ship.md s4/s7: resolve to ``HOST_AGENT`` before any key is read when the
  scope is absent) — the same prose-level enforcement model as every other
  delegate rule; this module itself performs no consent check, so any new
  code surface that calls :func:`generate` must gate it the same way.
"""

from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

#: Response cap for a single unattended completion; overridable per call.
DEFAULT_MAX_TOKENS = 16384
#: Per-request timeout in seconds; overridable per call.
DEFAULT_TIMEOUT = 300

_ANTHROPIC_VERSION = "2023-06-01"

#: vendor -> (endpoint, env var carrying the key). Only these two vendors exist
#: (``agents.API_VENDORS``); Gemini is additive later, same pattern.
_VENDORS: dict[str, tuple[str, str]] = {
    "anthropic-api": ("https://api.anthropic.com/v1/messages", "ANTHROPIC_API_KEY"),
    "openai-api": ("https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY"),
}


@dataclass(frozen=True)
class ApiResult:
    """Outcome of one hosted-API generation call (fail-soft, never raised)."""

    ok: bool
    text: str = ""
    #: machine-readable failure class: ``unknown-vendor`` | ``no-key`` |
    #: ``bad-key`` | ``auth`` | ``rate-limit`` | ``http`` | ``network`` |
    #: ``bad-response``; ``None`` on success.
    error_code: str | None = None
    error: str | None = None


def env_key_name(vendor: str) -> str | None:
    """The env var a vendor's key is read from, or ``None`` for unknown vendors."""
    entry = _VENDORS.get(vendor)
    return entry[1] if entry else None


def has_api_token(vendor: str, *, _env=os.environ) -> bool:
    """Dispatch probe: is the *selected* vendor's key present and non-blank?

    Contextual by design: running with ``openai-api:`` and only
    ``ANTHROPIC_API_KEY`` set reports absent.
    """
    name = env_key_name(vendor)
    return bool(name and _env.get(name, "").strip())


def present_key_names(*, _env=os.environ) -> tuple[str, ...]:
    """Env-var *names* (never values) of the vendor keys currently set.

    Backs the ``api-token`` runtime capability: the capability reports whether
    any supported vendor key is present; the per-vendor dispatch check is
    :func:`has_api_token`.
    """
    return tuple(name for _, name in _VENDORS.values() if _env.get(name, "").strip())


def _invalid_key_reason(key: str) -> str | None:
    """Reject keys that cannot safely travel in an HTTP header (pre-flight)."""
    # ⚡ Bolt Optimization: Use chained 'in' and 'or' to avoid any() generator overhead
    if "\r" in key or "\n" in key or "\0" in key:
        return "API key contains control characters"
    if not key.isascii():
        return "API key contains non-ASCII characters"
    return None


def _scrub(text: str, key: str) -> str:
    """Remove the raw key from any surfaced text (error bodies echo headers)."""
    return text.replace(key, "[REDACTED:api-key]") if key else text


def _build_request(
    vendor: str, model: str, prompt: str, key: str, max_tokens: int
) -> tuple[str, dict[str, str], bytes]:
    """Build ``(url, headers, body)`` for one single-shot generation call."""
    url = _VENDORS[vendor][0]
    if vendor == "anthropic-api":
        headers = {
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": _ANTHROPIC_VERSION,
        }
        payload: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
    else:  # openai-api — the only other key in _VENDORS
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {key}",
        }
        payload = {
            "model": model,
            "max_completion_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
    return url, headers, json.dumps(payload).encode("utf-8")


def _parse_content(vendor: str, data: object) -> str | None:
    """Extract the completion text from a decoded response, ``None`` if malformed."""
    try:
        if vendor == "anthropic-api":
            blocks = data["content"]  # type: ignore[index]
            parts = [b["text"] for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
            text = "".join(parts) if parts else None
        else:
            text = data["choices"][0]["message"]["content"]  # type: ignore[index]
        # A gateway/proxy or format drift can return valid JSON with a non-str
        # payload; report bad-response instead of leaking a dict/list to callers.
        return text if isinstance(text, str) and text else None
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def _build_opener() -> urllib.request.OpenerDirector:
    """HTTP/HTTPS-only opener: no redirect handler, no file/ftp/proxy handlers."""
    opener = urllib.request.OpenerDirector()
    opener.add_handler(urllib.request.HTTPHandler())
    opener.add_handler(urllib.request.HTTPSHandler())
    opener.add_handler(urllib.request.HTTPErrorProcessor())
    opener.add_handler(urllib.request.HTTPDefaultErrorHandler())
    return opener


def _status_error(status: int, body: str) -> ApiResult:
    """Map an HTTP error status onto the fail-soft vocabulary."""
    if status in (401, 403):
        return ApiResult(False, error_code="auth", error=f"HTTP {status}: {body[:200]}")
    if status == 429:
        # Quota/rate-limit: the s4 rule says do not retry — fail soft and fall back.
        return ApiResult(False, error_code="rate-limit", error=f"HTTP {status}: {body[:200]}")
    return ApiResult(False, error_code="http", error=f"HTTP {status}: {body[:200]}")


def generate(
    vendor: str,
    model: str,
    prompt: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: int = DEFAULT_TIMEOUT,
    _env=os.environ,
    _opener=None,
) -> ApiResult:
    """One single-shot generation call against a hosted vendor API.

    Pure request/response — no retries (the s4 contract owns the 2-retry loop on
    a bad diff and the no-retry-on-429 rule), no streaming, no tools. ``_env``
    and ``_opener`` are injectable so the wrapper is fully unit-testable offline.
    """
    entry = _VENDORS.get(vendor)
    if entry is None:
        return ApiResult(False, error_code="unknown-vendor", error=f"unknown API vendor: {vendor}")
    key = _env.get(entry[1], "").strip()
    if not key:
        return ApiResult(
            False, error_code="no-key", error=f"{entry[1]} is not set in the environment"
        )
    reason = _invalid_key_reason(key)
    if reason is not None:
        return ApiResult(False, error_code="bad-key", error=reason)

    url, headers, body = _build_request(vendor, model, prompt, key, max_tokens)
    # URL comes only from the hardcoded _VENDORS constants — never config, env,
    # or model/prompt content.
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # nosec B310
    opener = _opener if _opener is not None else _build_opener()
    try:
        with opener.open(request, timeout=timeout) as resp:
            raw = resp.read(50 * 1024 * 1024).decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(50 * 1024 * 1024).decode("utf-8", errors="replace")
        except OSError:  # pragma: no cover - defensive; HTTPError bodies rarely fail to read
            detail = str(exc)
        return _status_error(exc.code, _scrub(detail, key))
    except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError) as exc:
        # http.client exceptions (IncompleteRead, BadStatusLine, ...) are NOT
        # OSError subclasses and would otherwise escape the fail-soft contract.
        return ApiResult(False, error_code="network", error=_scrub(str(exc), key))

    if not 200 <= status < 300:
        # Non-2xx (incl. a 3xx from a redirecting intermediary — this opener
        # never follows redirects) must never be parsed as a completion.
        return _status_error(status, _scrub(raw, key))
    try:
        data = json.loads(raw)
    except ValueError:
        return ApiResult(
            False, error_code="bad-response", error="response is not valid JSON"
        )
    text = _parse_content(vendor, data)
    if not text:
        return ApiResult(
            False, error_code="bad-response", error="response carried no completion text"
        )
    return ApiResult(True, text=text)
