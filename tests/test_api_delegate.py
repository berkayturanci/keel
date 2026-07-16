"""Unit tests for the hosted-API code-generation delegate (issue #548).

Fully offline: the opener and environment are injected, so no test touches the
network. Coverage matches the pure-core bar — every branch of the wrapper is
driven through fakes, the same pattern as ``runner``/``git``/``github``.
"""

import http.client
import io
import json
import unittest
import urllib.error

from keel import api_delegate


class FakeResponse:
    def __init__(self, body: str, status: int = 200):
        self._body = body.encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RaisingReadResponse(FakeResponse):
    """Response whose body read fails mid-stream (connection dropped)."""

    def __init__(self, exc):
        super().__init__("")
        self._exc = exc

    def read(self) -> bytes:
        raise self._exc


class NoStatusResponse(FakeResponse):
    """Response object without a ``status`` attribute (older urllib shims)."""

    def __init__(self, body: str):
        super().__init__(body)
        del self.status  # type: ignore[attr-defined]

    def __getattr__(self, name):
        raise AttributeError(name)


class FakeOpener:
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.exc is not None:
            raise self.exc
        return self.response


def _anthropic_body(text="the diff"):
    return json.dumps({"content": [{"type": "text", "text": text}]})


def _openai_body(text="the diff"):
    return json.dumps({"choices": [{"message": {"content": text}}]})


ENV = {"ANTHROPIC_API_KEY": "sk-ant-key", "OPENAI_API_KEY": "sk-oai-key"}


class TestKeyHelpers(unittest.TestCase):
    def test_env_key_name_known(self):
        self.assertEqual(api_delegate.env_key_name("anthropic-api"), "ANTHROPIC_API_KEY")
        self.assertEqual(api_delegate.env_key_name("openai-api"), "OPENAI_API_KEY")

    def test_env_key_name_unknown(self):
        self.assertIsNone(api_delegate.env_key_name("ollama"))

    def test_has_api_token_present(self):
        self.assertTrue(api_delegate.has_api_token("anthropic-api", _env=ENV))

    def test_has_api_token_absent_blank_unknown(self):
        self.assertFalse(api_delegate.has_api_token("anthropic-api", _env={}))
        self.assertFalse(
            api_delegate.has_api_token("anthropic-api", _env={"ANTHROPIC_API_KEY": "  "})
        )
        self.assertFalse(api_delegate.has_api_token("ollama", _env=ENV))

    def test_present_key_names_lists_set_keys_only(self):
        self.assertEqual(
            api_delegate.present_key_names(_env=ENV),
            ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"),
        )
        self.assertEqual(
            api_delegate.present_key_names(_env={"OPENAI_API_KEY": "k"}),
            ("OPENAI_API_KEY",),
        )
        self.assertEqual(api_delegate.present_key_names(_env={}), ())


class TestKeyValidation(unittest.TestCase):
    def test_control_characters_rejected(self):
        for bad in ("a\rb", "a\nb", "a\0b"):
            self.assertIsNotNone(api_delegate._invalid_key_reason(bad))

    def test_non_ascii_rejected(self):
        self.assertIsNotNone(api_delegate._invalid_key_reason("sk-ké"))

    def test_clean_key_accepted(self):
        self.assertIsNone(api_delegate._invalid_key_reason("sk-clean-key"))

    def test_scrub_removes_key(self):
        self.assertEqual(
            api_delegate._scrub("error: sk-ant-key rejected", "sk-ant-key"),
            "error: [REDACTED:api-key] rejected",
        )

    def test_scrub_empty_key_is_noop(self):
        self.assertEqual(api_delegate._scrub("text", ""), "text")


class TestBuildRequest(unittest.TestCase):
    def test_anthropic_shape(self):
        url, headers, body = api_delegate._build_request(
            "anthropic-api", "claude-sonnet-5", "p", "key", 100
        )
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers["x-api-key"], "key")
        self.assertIn("anthropic-version", headers)
        payload = json.loads(body)
        self.assertEqual(payload["model"], "claude-sonnet-5")
        self.assertEqual(payload["max_tokens"], 100)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "p"}])

    def test_openai_shape(self):
        url, headers, body = api_delegate._build_request("openai-api", "gpt-5", "p", "key", 100)
        self.assertEqual(url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(headers["authorization"], "Bearer key")
        payload = json.loads(body)
        self.assertEqual(payload["max_completion_tokens"], 100)


class TestParseContent(unittest.TestCase):
    def test_anthropic_text_blocks_joined(self):
        data = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
        self.assertEqual(api_delegate._parse_content("anthropic-api", data), "ab")

    def test_anthropic_non_text_blocks_skipped(self):
        data = {"content": [{"type": "thinking", "thinking": "x"}]}
        self.assertIsNone(api_delegate._parse_content("anthropic-api", data))

    def test_anthropic_malformed(self):
        self.assertIsNone(api_delegate._parse_content("anthropic-api", {"nope": 1}))

    def test_openai_content(self):
        self.assertEqual(
            api_delegate._parse_content("openai-api", json.loads(_openai_body("x"))), "x"
        )

    def test_openai_null_content(self):
        data = {"choices": [{"message": {"content": None}}]}
        self.assertIsNone(api_delegate._parse_content("openai-api", data))

    def test_openai_malformed(self):
        self.assertIsNone(api_delegate._parse_content("openai-api", {"choices": []}))


class TestOpener(unittest.TestCase):
    def test_opener_has_only_http_https_handlers(self):
        opener = api_delegate._build_opener()
        names = {type(h).__name__ for h in opener.handlers}
        self.assertIn("HTTPHandler", names)
        self.assertIn("HTTPSHandler", names)
        self.assertNotIn("HTTPRedirectHandler", names)
        self.assertNotIn("FileHandler", names)
        self.assertNotIn("FTPHandler", names)


class TestGenerate(unittest.TestCase):
    def test_unknown_vendor(self):
        result = api_delegate.generate("nope-api", "m", "p", _env=ENV)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "unknown-vendor")

    def test_no_key(self):
        result = api_delegate.generate("anthropic-api", "m", "p", _env={})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "no-key")
        self.assertIn("ANTHROPIC_API_KEY", result.error)

    def test_bad_key(self):
        result = api_delegate.generate(
            "anthropic-api", "m", "p", _env={"ANTHROPIC_API_KEY": "bad\nkey"}
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "bad-key")

    def test_success_anthropic(self):
        opener = FakeOpener(response=FakeResponse(_anthropic_body("PATCH")))
        result = api_delegate.generate("anthropic-api", "m", "p", _env=ENV, _opener=opener)
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "PATCH")
        request, timeout = opener.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(timeout, api_delegate.DEFAULT_TIMEOUT)

    def test_success_openai(self):
        opener = FakeOpener(response=FakeResponse(_openai_body("PATCH")))
        result = api_delegate.generate("openai-api", "m", "p", _env=ENV, _opener=opener)
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "PATCH")

    def test_response_without_status_attr_defaults_ok(self):
        opener = FakeOpener(response=NoStatusResponse(_anthropic_body()))
        result = api_delegate.generate("anthropic-api", "m", "p", _env=ENV, _opener=opener)
        self.assertTrue(result.ok)

    def test_http_401_maps_to_auth_and_scrubs_key(self):
        exc = urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages", 401, "Unauthorized", {},
            io.BytesIO(b"invalid key sk-ant-key"),
        )
        result = api_delegate.generate(
            "anthropic-api", "m", "p", _env=ENV, _opener=FakeOpener(exc=exc)
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "auth")
        self.assertNotIn("sk-ant-key", result.error)
        self.assertIn("[REDACTED:api-key]", result.error)

    def test_http_429_maps_to_rate_limit(self):
        exc = urllib.error.HTTPError("u", 429, "Too Many", {}, io.BytesIO(b"slow down"))
        result = api_delegate.generate(
            "anthropic-api", "m", "p", _env=ENV, _opener=FakeOpener(exc=exc)
        )
        self.assertEqual(result.error_code, "rate-limit")

    def test_http_500_maps_to_http(self):
        exc = urllib.error.HTTPError("u", 500, "Boom", {}, io.BytesIO(b"server error"))
        result = api_delegate.generate(
            "anthropic-api", "m", "p", _env=ENV, _opener=FakeOpener(exc=exc)
        )
        self.assertEqual(result.error_code, "http")

    def test_network_error_scrubbed(self):
        exc = urllib.error.URLError("dns failure for sk-ant-key")
        result = api_delegate.generate(
            "anthropic-api", "m", "p", _env=ENV, _opener=FakeOpener(exc=exc)
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "network")
        self.assertNotIn("sk-ant-key", result.error)

    def test_timeout_maps_to_network(self):
        result = api_delegate.generate(
            "anthropic-api", "m", "p", _env=ENV, _opener=FakeOpener(exc=TimeoutError("slow"))
        )
        self.assertEqual(result.error_code, "network")

    def test_non_raising_error_status_mapped(self):
        # An opener without an HTTPErrorProcessor can return >=400 directly.
        opener = FakeOpener(response=FakeResponse("denied", status=403))
        result = api_delegate.generate("anthropic-api", "m", "p", _env=ENV, _opener=opener)
        self.assertEqual(result.error_code, "auth")

    def test_invalid_json_response(self):
        opener = FakeOpener(response=FakeResponse("not json"))
        result = api_delegate.generate("anthropic-api", "m", "p", _env=ENV, _opener=opener)
        self.assertEqual(result.error_code, "bad-response")

    def test_empty_completion(self):
        opener = FakeOpener(response=FakeResponse(json.dumps({"content": []})))
        result = api_delegate.generate("anthropic-api", "m", "p", _env=ENV, _opener=opener)
        self.assertEqual(result.error_code, "bad-response")
        self.assertIn("no completion text", result.error)

    def test_incomplete_read_is_fail_soft_not_raised(self):
        # Regression (review SEC-1): http.client exceptions are NOT OSError
        # subclasses; a connection dropped mid-body must become a scrubbed
        # ApiResult, never an unhandled traceback.
        exc = http.client.IncompleteRead(b"partial sk-ant-key body")
        opener = FakeOpener(response=RaisingReadResponse(exc))
        result = api_delegate.generate("anthropic-api", "m", "p", _env=ENV, _opener=opener)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "network")
        self.assertNotIn("sk-ant-key", result.error)

    def test_bad_status_line_is_fail_soft_not_raised(self):
        exc = http.client.BadStatusLine("garbage")
        result = api_delegate.generate(
            "anthropic-api", "m", "p", _env=ENV, _opener=FakeOpener(exc=exc)
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "network")

    def test_anthropic_string_content_is_bad_response_not_raised(self):
        # Regression: {"content": "hello"} used to raise AttributeError.
        opener = FakeOpener(response=FakeResponse(json.dumps({"content": "hello"})))
        result = api_delegate.generate("anthropic-api", "m", "p", _env=ENV, _opener=opener)
        self.assertEqual(result.error_code, "bad-response")

    def test_anthropic_non_dict_block_skipped_not_raised(self):
        # Regression: {"content": ["junk"]} used to raise AttributeError.
        opener = FakeOpener(response=FakeResponse(json.dumps({"content": ["junk"]})))
        result = api_delegate.generate("anthropic-api", "m", "p", _env=ENV, _opener=opener)
        self.assertEqual(result.error_code, "bad-response")

    def test_openai_non_string_content_is_bad_response(self):
        body = json.dumps({"choices": [{"message": {"content": {"weird": 1}}}]})
        opener = FakeOpener(response=FakeResponse(body))
        result = api_delegate.generate("openai-api", "m", "p", _env=ENV, _opener=opener)
        self.assertEqual(result.error_code, "bad-response")

    def test_redirect_status_never_parsed_as_completion(self):
        # Pin the load-bearing opener property: a 3xx (this opener never
        # follows redirects) must map to an error, not a parsed completion —
        # even when the body looks like a valid vendor response.
        opener = FakeOpener(response=FakeResponse(_anthropic_body("evil"), status=302))
        result = api_delegate.generate("anthropic-api", "m", "p", _env=ENV, _opener=opener)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "http")

    def test_non_raising_error_body_is_scrubbed(self):
        opener = FakeOpener(response=FakeResponse("denied for sk-ant-key", status=403))
        result = api_delegate.generate("anthropic-api", "m", "p", _env=ENV, _opener=opener)
        self.assertNotIn("sk-ant-key", result.error)
        self.assertIn("[REDACTED:api-key]", result.error)

    def test_max_tokens_override_lands_in_payload(self):
        opener = FakeOpener(response=FakeResponse(_anthropic_body()))
        api_delegate.generate(
            "anthropic-api", "m", "p", max_tokens=42, _env=ENV, _opener=opener
        )
        request, _ = opener.requests[0]
        self.assertEqual(json.loads(request.data)["max_tokens"], 42)


if __name__ == "__main__":
    unittest.main()
