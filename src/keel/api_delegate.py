"""Thin I/O: hosted-API code-generation delegate (issue #548).

Lets the s4 implement / s7 review steps run with **only an API token in the
environment** (``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` / ``GEMINI_API_KEY``,
or a configured OpenAI-compatible key) and no agent CLI installed. The delegate
follows the same no-tools contract as ``ollama:MODEL`` in ship.md s4: the
orchestrator owns every git/PR step and calls this module exactly once per attempt
to turn a prompt into text (a unified diff for the implementer, a structured
verdict for the reviewer).

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

import functools
import http.client
import ipaddress
import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import config

#: Response cap for a single unattended completion; overridable per call.
DEFAULT_MAX_TOKENS = 16384
#: Per-request timeout in seconds; overridable per call.
DEFAULT_TIMEOUT = 300

_ANTHROPIC_VERSION = "2023-06-01"

#: The one vendor whose endpoint and key-env name come from ``knobs.delegate_profiles``
#: instead of the hardcoded table below (#666).
OPENAI_COMPATIBLE = "openai-compatible"

#: vendor -> (endpoint, env var carrying the key), matching ``agents.API_VENDORS``.
#: Every URL here is a **hardcoded constant** — that is what keeps the SSRF story
#: trivial, and why a config-supplied endpoint is a separate decision (#666).
#:
#: ``google-api``'s URL carries the model in its *path* rather than the body, so it
#: is a template. See :func:`_unsafe_model_reason`: a model that reaches a URL path
#: is untrusted input in a way the other two vendors' models are not.
_VENDORS: dict[str, tuple[str, str]] = {
    "anthropic-api": ("https://api.anthropic.com/v1/messages", "ANTHROPIC_API_KEY"),
    "openai-api": ("https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY"),
    "google-api": (
        "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "GEMINI_API_KEY",
    ),
}

#: Characters a model id may contain when it is interpolated into a URL path.
_MODEL_PATH_OK = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_")


def _unsafe_model_reason(model: str) -> str | None:
    """Reject a model id that cannot safely be interpolated into a URL path.

    Only ``google-api`` puts the model in the URL; ``anthropic-api``/``openai-api``
    carry it in the JSON body, where a stray ``/`` or ``?`` is inert. Here it is not:
    the model arrives from ``--delegate google-api:MODEL`` or a ``delegate-model:``
    issue label, so a value containing ``/``, ``..``, ``?`` or ``#`` could retarget
    the request to a different path or smuggle query parameters onto a URL that also
    carries an API key header. Rejected rather than escaped — no real Gemini model id
    needs anything outside ``[A-Za-z0-9._-]``.
    """
    if not model:
        return "model is empty"
    if not _MODEL_PATH_OK.issuperset(model):
        return "model contains characters that are not URL-path safe"
    if ".." in model:
        return "model contains a path traversal sequence"
    return None


@dataclass(frozen=True)
class ApiResult:
    """Outcome of one hosted-API generation call (fail-soft, never raised)."""

    ok: bool
    text: str = ""
    #: machine-readable failure class: ``unknown-vendor`` | ``no-key`` |
    #: ``bad-key`` | ``bad-model`` | ``auth`` | ``rate-limit`` | ``http`` |
    #: ``network`` | ``bad-response``; ``None`` on success.
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
    vendor: str, model: str, prompt: str, key: str, max_tokens: int, base: str | None = None
) -> tuple[str, dict[str, str], bytes]:
    """Build ``(url, headers, body)`` for one single-shot generation call.

    ``base`` is the endpoint. It is a hardcoded ``_VENDORS`` constant for every vendor
    except ``openai-compatible``, where it comes from the validated profile.
    """
    payload: dict
    template = base if base is not None else _VENDORS[vendor][0]
    url = template.format(model=model) if "{model}" in template else template
    if vendor == OPENAI_COMPATIBLE:
        # OpenAI-shaped by definition — that is what "compatible" means, and why one
        # profile reaches OpenRouter, Groq, DeepSeek, Together, LiteLLM and vLLM.
        headers = {"content-type": "application/json", "authorization": f"Bearer {key}"}
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
    elif vendor == "google-api":
        # Key travels as a header, never as ?key= — a URL carries into logs,
        # referrers and error text in a way a header does not.
        headers = {"content-type": "application/json", "x-goog-api-key": key}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
    elif vendor == "anthropic-api":
        headers = {
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": _ANTHROPIC_VERSION,
        }
        payload = {
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
        if vendor == OPENAI_COMPATIBLE:
            text = data["choices"][0]["message"]["content"]  # type: ignore[index]
        elif vendor == "google-api":
            parts = data["candidates"][0]["content"]["parts"]  # type: ignore[index]
            chunks = [p["text"] for p in parts if isinstance(p, dict) and "text" in p]
            text = "".join(chunks) if chunks else None
        elif vendor == "anthropic-api":
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


class GuardedAddressError(OSError):
    """A connection was refused because of where the host name resolved."""


def _permitted_addresses(host, port, *, env, resolve):
    """Every address ``host`` may be connected to. Raises if none may be.

    Resolution happens **once** and the caller connects to what was checked.
    Re-resolving after the check is the whole vulnerability: a name that answers
    a public address to the check and a private one to ``connect`` passes both.
    """
    try:
        candidates = resolve(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        # Not a policy refusal. Reporting an unresolvable name as a security
        # block would send an operator hunting for a threat that is a typo.
        raise GuardedAddressError(f"cannot resolve {host!r}: {exc}") from None
    permitted, refusals = [], []
    for candidate in candidates:
        try:
            ip = ipaddress.ip_address(candidate[4][0])
        except ValueError:  # pragma: no cover - getaddrinfo yields literals
            continue
        refusal = config.resolved_address_refusal(host, ip, env=env)
        if refusal is None:
            permitted.append(candidate)
        else:
            refusals.append(refusal)
    if not permitted:
        detail = refusals[0] if refusals else "resolved to no usable address"
        raise GuardedAddressError(f"endpoint host {host!r} {detail}")
    return permitted


def _guarded_create_connection(*, env, resolve):
    """A ``socket.create_connection`` that only reaches checked addresses.

    Substituted for ``HTTPConnection._create_connection`` rather than overriding
    ``connect``: ``HTTPSConnection.connect`` calls ``super().connect()`` and then
    wraps the socket in TLS with ``server_hostname``. Replacing this one seam
    leaves that intact, so the guard cannot accidentally change how the
    certificate is checked — which reimplementing ``connect`` for HTTPS would
    have risked.
    """

    def create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
        host, port = address[0], address[1]
        permitted = _permitted_addresses(host, port, env=env, resolve=resolve)
        # Hand stdlib the **checked address**, not the name. Resolving a literal
        # is a no-op, so this pins the connection to what was approved while
        # reusing socket.create_connection's timeout, source-address and
        # address-family handling rather than reimplementing them here.
        return socket.create_connection((permitted[0][4][0], port), timeout, source_address)

    return create_connection


def _guarded_handlers(*, env, resolve):
    """HTTP and HTTPS handlers whose connections check where the name went."""
    create = _guarded_create_connection(env=env, resolve=resolve)

    # Assigned *after* `super().__init__`, not as a class attribute:
    # `HTTPConnection.__init__` does `self._create_connection =
    # socket.create_connection`, so an instance attribute shadows anything set
    # on the subclass. A class-level override silently does nothing — which is
    # how the first cut of this guard let a rebinding resolver straight through
    # while every unit test still passed.
    def _init(self, *args, _base, **kwargs):
        _base.__init__(self, *args, **kwargs)
        self._create_connection = create

    http_conn = type(
        "GuardedHTTPConnection",
        (http.client.HTTPConnection,),
        {"__init__": functools.partialmethod(_init, _base=http.client.HTTPConnection)},
    )
    https_conn = type(
        "GuardedHTTPSConnection",
        (http.client.HTTPSConnection,),
        {"__init__": functools.partialmethod(_init, _base=http.client.HTTPSConnection)},
    )

    class GuardedHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(http_conn, req)

    class GuardedHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(https_conn, req)

    return GuardedHTTPHandler(), GuardedHTTPSHandler()


def build_http_only_opener(
    *, _env=None, _resolve=socket.getaddrinfo
) -> urllib.request.OpenerDirector:
    """HTTP/HTTPS-only opener: no redirect handler, no file/ftp/proxy handlers.

    Public and shared rather than private to this module, because keel makes
    outbound HTTP from two places — this delegate and ``keel doctor``'s PyPI
    version check — and each having its own hand-rolled opener is how the handler
    sets drift apart. #811 hardened the second one; #810 squashed a stale base
    over it and restored plain ``urlopen`` for six days without anything noticing
    (#934). One opener, one owner, one place to audit the handler list.

    The handlers also guard **where a name resolves** (#969). `config`'s
    endpoint check classifies the host as written, so a hostname carries no
    address and reaches what its literal spelling could not — `10.0.0.5` is
    refused while a name resolving to it is not. The address is checked and the
    connection pinned to what was checked, because re-resolving afterwards is
    the vulnerability rather than a detail of it.

    `_env` and `_resolve` are injectable so a rebinding resolver can be tested
    offline; both callers use the defaults.
    """
    http_handler, https_handler = _guarded_handlers(
        env=os.environ if _env is None else _env, resolve=_resolve
    )
    opener = urllib.request.OpenerDirector()
    opener.add_handler(http_handler)
    opener.add_handler(https_handler)
    opener.add_handler(urllib.request.HTTPErrorProcessor())
    opener.add_handler(urllib.request.HTTPDefaultErrorHandler())
    return opener


def _status_error(status: int, body: str) -> ApiResult:
    """Map an HTTP error status onto the fail-soft vocabulary.

    ``400`` is read as an auth failure when the body says so, because Google answers
    an invalid ``GEMINI_API_KEY`` with ``400 INVALID_ARGUMENT: API key not valid``
    rather than 401 (verified against the live endpoint). Classifying that as a
    generic ``http`` error would tell an operator with a mistyped key to look
    anywhere but at the key.
    """
    if status == 400 and "api key not valid" in body.lower():
        return ApiResult(False, error_code="auth", error=f"HTTP {status}: {body[:200]}")
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
    endpoint: str | None = None,
    api_key_env: str | None = None,
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
    if vendor == OPENAI_COMPATIBLE:
        # The only vendor whose URL and key-env come from config rather than from
        # the hardcoded table. keel.config.endpoint_issues has already refused a
        # non-http(s) scheme and a non-loopback host without the env opt-in, at
        # `keel validate` time; this is the dispatch-time contract check.
        if not endpoint or not api_key_env:
            return ApiResult(
                False,
                error_code="unknown-vendor",
                error=f"{OPENAI_COMPATIBLE} requires both endpoint and api_key_env",
            )
        entry: tuple[str, str] | None = (endpoint, api_key_env)
    else:
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
    if "{model}" in entry[0]:
        unsafe = _unsafe_model_reason(model)
        if unsafe is not None:
            return ApiResult(False, error_code="bad-model", error=unsafe)

    url, headers, body = _build_request(vendor, model, prompt, key, max_tokens, entry[0])
    # URL comes only from the hardcoded _VENDORS constants — never config, env,
    # or model/prompt content.
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # nosec B310
    opener = _opener if _opener is not None else build_http_only_opener()
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
        return ApiResult(False, error_code="bad-response", error="response is not valid JSON")
    text = _parse_content(vendor, data)
    if not text:
        return ApiResult(
            False, error_code="bad-response", error="response carried no completion text"
        )
    return ApiResult(True, text=text)
