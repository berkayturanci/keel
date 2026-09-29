"""A hosted-API delegate's reported token usage reaches the run's activity record (#1373).

Fully offline. Every response below is a *recorded shape* — the usage object each vendor
documents, with the fields keel does not read left in so the parser is shown ignoring
them — served through a fake opener. No test reaches the network.

Sources for the shapes (also cited in ``keel.api_delegate.parse_usage``):

- Anthropic Messages ``usage``: https://docs.anthropic.com/en/api/messages
- OpenAI ``CompletionUsage``: https://github.com/openai/openai-openapi
- DeepSeek chat completion ``usage``: https://api-docs.deepseek.com/api/create-chat-completion
- OpenRouter usage accounting: https://openrouter.ai/docs/use-cases/usage-accounting
- Gemini ``UsageMetadata``: https://ai.google.dev/api/generate-content#UsageMetadata
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from keel import activity, api_delegate, cli, cost, delegate, delegaterun, lock, providers
from keel import config as cfg

PROJECTS = Path(__file__).resolve().parent.parent / "projects"
CONFIG_PATH = str(PROJECTS / "example-android.yaml")

# --- recorded response shapes -------------------------------------------------------

ANTHROPIC = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-4-5",
    "content": [{"type": "text", "text": "the diff"}],
    "stop_reason": "end_turn",
    "usage": {
        "input_tokens": 1200,
        "cache_creation_input_tokens": 300,
        "cache_read_input_tokens": 500,
        "cache_creation": {"ephemeral_5m_input_tokens": 300, "ephemeral_1h_input_tokens": 0},
        "output_tokens": 450,
        "output_tokens_details": {"thinking_tokens": 100},
    },
}

OPENAI = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "model": "gpt-4o-2024-08-06",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "the diff"}}],
    "usage": {
        "prompt_tokens": 2000,
        "completion_tokens": 600,
        "total_tokens": 2600,
        "prompt_tokens_details": {"cached_tokens": 1024},
        "completion_tokens_details": {"reasoning_tokens": 200},
    },
}

DEEPSEEK = {
    "id": "c1",
    "object": "chat.completion",
    "model": "deepseek-chat",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "verdict"}}],
    "usage": {
        "completion_tokens": 70,
        "prompt_tokens": 900,
        "prompt_cache_hit_tokens": 800,
        "prompt_cache_miss_tokens": 100,
        "total_tokens": 970,
        "completion_tokens_details": {"reasoning_tokens": 0},
    },
}

OPENROUTER = {
    "id": "gen-1",
    "object": "chat.completion",
    "choices": [{"message": {"role": "assistant", "content": "verdict"}}],
    "usage": {
        "completion_tokens": 2,
        "completion_tokens_details": {"reasoning_tokens": 0},
        "cost": 0.95,
        "prompt_tokens": 194,
        "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 100},
        "total_tokens": 196,
    },
}

GEMINI = {
    "candidates": [{"content": {"parts": [{"text": "the diff"}], "role": "model"}}],
    "usageMetadata": {
        "promptTokenCount": 800,
        "candidatesTokenCount": 120,
        "thoughtsTokenCount": 300,
        "totalTokenCount": 1220,
        "cachedContentTokenCount": 0,
    },
    "modelVersion": "gemini-2.5-pro",
}


def _with_usage(body: dict, key: str, usage) -> dict:
    changed = dict(body)
    if usage is _ABSENT:
        changed.pop(key, None)
    else:
        changed[key] = usage
    return changed


_ABSENT = object()


class FakeResponse:
    def __init__(self, body: str, status: int = 200):
        self._body = body.encode("utf-8")
        self.status = status

    def read(self, size: int = -1) -> bytes:
        return self._body[:size] if size >= 0 else self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, body: dict):
        self.body = json.dumps(body)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        return FakeResponse(self.body)


# --- the parser ---------------------------------------------------------------------


class ParseUsageReadsEachVendorsDocumentedShape(unittest.TestCase):
    def test_anthropic_input_is_the_sum_of_the_three_input_fields(self):
        """Anthropic: total input = input + cache creation + cache read (1200+300+500)."""
        self.assertEqual((2000, 450), api_delegate.parse_usage("anthropic-api", ANTHROPIC))

    def test_anthropic_cache_fields_may_be_null_or_absent(self):
        usage = {"input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": None}
        body = _with_usage(ANTHROPIC, "usage", usage)
        self.assertEqual((10, 4), api_delegate.parse_usage("anthropic-api", body))

    def test_openai_reads_prompt_and_completion_and_does_not_add_the_breakdowns(self):
        self.assertEqual((2000, 600), api_delegate.parse_usage("openai-api", OPENAI))

    def test_openai_compatible_reads_the_same_shape(self):
        for name, body, expected in (
            ("deepseek", DEEPSEEK, (900, 70)),
            ("openrouter", OPENROUTER, (194, 2)),
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    expected, api_delegate.parse_usage(api_delegate.OPENAI_COMPATIBLE, body)
                )

    def test_gemini_completion_counts_thoughts_as_well_as_candidates(self):
        """Gemini's total is prompt + thoughts + candidates: 800 / (120 + 300)."""
        self.assertEqual((800, 420), api_delegate.parse_usage("google-api", GEMINI))

    def test_gemini_without_thoughts_is_candidates_alone(self):
        usage = {"promptTokenCount": 8, "candidatesTokenCount": 3}
        body = _with_usage(GEMINI, "usageMetadata", usage)
        self.assertEqual((8, 3), api_delegate.parse_usage("google-api", body))

    def test_each_vendor_reads_its_own_key_only(self):
        """An OpenAI-shaped `usage` on a Gemini response is not Gemini's usage."""
        self.assertIsNone(api_delegate.parse_usage("google-api", OPENAI))
        self.assertIsNone(api_delegate.parse_usage("openai-api", GEMINI))


class ParseUsageNeverGuesses(unittest.TestCase):
    """Absent or malformed usage yields no numbers at all — not a partial sum."""

    def test_absent_or_non_object_usage_is_none(self):
        for usage in (_ABSENT, None, [], "12", 12):
            with self.subTest(usage=usage):
                body = _with_usage(OPENAI, "usage", usage)
                self.assertIsNone(api_delegate.parse_usage("openai-api", body))

    def test_a_non_object_response_is_none(self):
        for data in (None, [], "text", 3):
            with self.subTest(data=data):
                self.assertIsNone(api_delegate.parse_usage("openai-api", data))

    def test_a_missing_required_field_is_none(self):
        cases = (
            ("openai-api", "usage", {"prompt_tokens": 5}),
            ("openai-api", "usage", {"completion_tokens": 5}),
            ("anthropic-api", "usage", {"output_tokens": 5}),
            ("google-api", "usageMetadata", {"promptTokenCount": 5}),
        )
        for vendor, key, usage in cases:
            with self.subTest(vendor=vendor, usage=usage):
                body = _with_usage({}, key, usage)
                self.assertIsNone(api_delegate.parse_usage(vendor, body))

    def test_a_non_integer_or_negative_count_is_none(self):
        for bad in ("12", 12.0, 12.5, True, -1, [12], {"n": 12}):
            with self.subTest(bad=bad):
                body = {"usage": {"prompt_tokens": bad, "completion_tokens": 5}}
                self.assertIsNone(api_delegate.parse_usage("openai-api", body))

    def test_a_malformed_optional_field_voids_the_whole_reading(self):
        """A bad cache count must not be skipped: the sum without it is a guess."""
        usage = {"input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": "5"}
        self.assertIsNone(api_delegate.parse_usage("anthropic-api", {"usage": usage}))
        usage = {"promptTokenCount": 8, "candidatesTokenCount": 3, "thoughtsTokenCount": -2}
        self.assertIsNone(api_delegate.parse_usage("google-api", {"usageMetadata": usage}))

    def test_a_zero_on_either_side_is_a_gateway_that_does_not_count(self):
        for prompt, completion in ((0, 5), (5, 0), (0, 0)):
            with self.subTest(prompt=prompt, completion=completion):
                body = {"usage": {"prompt_tokens": prompt, "completion_tokens": completion}}
                self.assertIsNone(api_delegate.parse_usage("openai-api", body))


class GenerateCarriesTheCounts(unittest.TestCase):
    ENV = {"OPENAI_API_KEY": "sk-key"}

    def test_a_success_carries_the_reported_counts(self):
        result = api_delegate.generate(
            "openai-api", "gpt-4o", "p", _env=self.ENV, _opener=FakeOpener(OPENAI)
        )
        self.assertTrue(result.ok)
        self.assertEqual((2000, 600), (result.prompt_tokens, result.completion_tokens))
        self.assertEqual({"prompt_tokens": 2000, "completion_tokens": 600}, result.usage)

    def test_a_response_without_text_still_carries_what_the_vendor_counted(self):
        body = dict(OPENAI, choices=[{"message": {"content": None}}])
        result = api_delegate.generate(
            "openai-api", "gpt-4o", "p", _env=self.ENV, _opener=FakeOpener(body)
        )
        self.assertEqual("bad-response", result.error_code)
        self.assertEqual({"prompt_tokens": 2000, "completion_tokens": 600}, result.usage)

    def test_a_response_without_usage_carries_none(self):
        body = _with_usage(OPENAI, "usage", _ABSENT)
        result = api_delegate.generate(
            "openai-api", "gpt-4o", "p", _env=self.ENV, _opener=FakeOpener(body)
        )
        self.assertTrue(result.ok)
        self.assertIsNone(result.prompt_tokens)
        self.assertIsNone(result.usage)


# --- the delegate run document ------------------------------------------------------


def _builtin(name):
    for provider in providers.builtin_providers():
        if provider.name == name:
            return provider
    raise AssertionError(name)  # pragma: no cover


def _api_plan(vendor, model):
    return delegate.plan_run(_builtin(vendor), "review", "/tmp/brief.md", model=model)


def _read(_path):
    return "review this\n"


class TheRunDocumentReportsUsage(unittest.TestCase):
    def test_a_hosted_run_reports_the_vendors_counts(self):
        result = delegaterun.execute(
            _api_plan("google-api", "gemini-2.5-pro"),
            _opener=FakeOpener(GEMINI),
            _env={"GEMINI_API_KEY": "k"},
            _read=_read,
        )
        self.assertTrue(result["ok"])
        self.assertEqual({"prompt_tokens": 800, "completion_tokens": 420}, result["usage"])

    def test_a_failed_hosted_run_still_reports_what_was_counted(self):
        body = dict(ANTHROPIC, content=[])
        result = delegaterun.execute(
            _api_plan("anthropic-api", "claude-opus-4-5"),
            _opener=FakeOpener(body),
            _env={"ANTHROPIC_API_KEY": "k"},
            _read=_read,
        )
        self.assertEqual("bad-response", result["error_code"])
        self.assertEqual({"prompt_tokens": 2000, "completion_tokens": 450}, result["usage"])

    def test_every_document_shape_carries_the_key(self):
        """`delegate wait` and a planning failure hand back the same shape, usage null."""
        planned = delegaterun.planning_failure("x", "review", code="unknown-provider", message="")
        lost = delegaterun._detached_failure({"run_id": "r"}, code="lost", message="gone")
        self.assertIn("usage", planned)
        self.assertIsNone(planned["usage"])
        self.assertIn("usage", lost)
        self.assertIsNone(lost["usage"])


# --- the activity record ------------------------------------------------------------


def _record(**extra):
    record = activity.build_activity_record(command="ship", run_id="r1", phase="s4")
    record.update(extra)
    return record


ENTRY = {"model": "gpt-4o", "prompt_tokens": 10, "completion_tokens": 4}


class TheRecordValidatesItsCounts(unittest.TestCase):
    def test_a_well_formed_usage_field_validates(self):
        activity.validate_activity(_record(delegate_usage={"call-1": dict(ENTRY)}))

    def test_a_malformed_usage_field_is_refused(self):
        bad_entries = (
            "not an object",
            {"prompt_tokens": 1, "completion_tokens": 1},
            {"model": " ", "prompt_tokens": 1, "completion_tokens": 1},
            {"model": "m", "prompt_tokens": "1", "completion_tokens": 1},
            {"model": "m", "prompt_tokens": 1, "completion_tokens": -1},
            {"model": "m", "prompt_tokens": True, "completion_tokens": 1},
        )
        for entry in bad_entries:
            with self.subTest(entry=entry):
                with self.assertRaises(activity.ActivityError):
                    activity.validate_activity(_record(delegate_usage={"c": entry}))
        with self.assertRaises(activity.ActivityError):
            activity.validate_activity(_record(delegate_usage=[ENTRY]))

    def test_the_contract_names_the_field(self):
        self.assertEqual("delegate_usage", activity.activity_contract_as_dict()["usage_field"])


class AddingAndCarryingCounts(unittest.TestCase):
    def test_a_call_is_added_under_its_id(self):
        updated = activity.with_delegate_usage(
            _record(), "call-1", model="gpt-4o", prompt_tokens=10, completion_tokens=4
        )
        self.assertEqual({"call-1": ENTRY}, updated["delegate_usage"])

    def test_recording_the_same_call_twice_is_not_a_double_count(self):
        once = activity.with_delegate_usage(
            _record(), "call-1", model="gpt-4o", prompt_tokens=10, completion_tokens=4
        )
        twice = activity.with_delegate_usage(
            once, "call-1", model="gpt-4o", prompt_tokens=10, completion_tokens=4
        )
        other = activity.with_delegate_usage(
            twice, "call-2", model="gpt-4o", prompt_tokens=1, completion_tokens=1
        )
        self.assertEqual(["call-1", "call-2"], sorted(other["delegate_usage"]))
        self.assertEqual(10 + 1, cost.calculate_cost_report([other]).total_prompt_tokens)

    def test_a_bad_count_or_call_id_is_refused_before_it_is_built_on(self):
        with self.assertRaises(activity.ActivityError):
            activity.with_delegate_usage(
                _record(), "c", model="m", prompt_tokens=-1, completion_tokens=1
            )
        with self.assertRaises(activity.ActivityError):
            activity.with_delegate_usage(
                _record(), " ", model="m", prompt_tokens=1, completion_tokens=1
            )

    def test_a_restamp_keeps_the_counts_the_old_record_held(self):
        existing = _record(delegate_usage={"call-1": dict(ENTRY)})
        rebuilt = activity.build_activity_record(command="ship", run_id="r1", phase="s8")
        carried = activity.carry_usage(rebuilt, existing)
        self.assertEqual({"call-1": ENTRY}, carried["delegate_usage"])
        self.assertEqual("s8", carried["phase"])

    def test_carrying_from_nothing_leaves_the_record_alone(self):
        rebuilt = _record()
        self.assertIs(rebuilt, activity.carry_usage(rebuilt, None))
        self.assertIs(rebuilt, activity.carry_usage(rebuilt, _record()))


class TheRecordLock(unittest.TestCase):
    def test_a_free_record_is_held_then_released(self):
        with tempfile.TemporaryDirectory() as d:
            with activity.record_lock(d, "x/r1.json", owner="me") as held:
                self.assertTrue(held)
                self.assertTrue(lock.resource_path(d, "activity-r1").exists())
            self.assertFalse(lock.resource_path(d, "activity-r1").exists())

    def test_a_held_record_is_waited_for_a_bounded_time_then_reported(self):
        with tempfile.TemporaryDirectory() as d:
            lock.claim_resource(d, "activity-r1", owner="other")
            sleeps = []
            with activity.record_lock(
                d, "r1.json", owner="me", attempts=3, _sleep=sleeps.append
            ) as held:
                self.assertFalse(held)
            self.assertEqual([activity.LOCK_POLL_S] * 2, sleeps)
            # the other owner's claim is not released by a waiter that never held it
            self.assertTrue(lock.resource_path(d, "activity-r1").exists())

    def test_no_attempts_is_not_held(self):
        with tempfile.TemporaryDirectory() as d:
            with activity.record_lock(d, "r1.json", owner="me", attempts=0) as held:
                self.assertFalse(held)

    def test_a_claim_that_cannot_be_written_is_not_held(self):
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "file"
            blocker.write_text("", encoding="utf-8")
            try:
                with activity.record_lock(blocker, "r1.json", owner="me") as held:
                    self.assertFalse(held)
            except OSError as exc:  # the contract: an I/O failure is "not held", not a raise
                self.fail(f"record_lock raised {exc!r}")


class RecordingIntoTheRun(unittest.TestCase):
    def _args(self, **kwargs):
        return {
            "call_id": "call-1",
            "model": "gpt-4o",
            "prompt_tokens": 10,
            "completion_tokens": 4,
            **kwargs,
        }

    def test_counts_are_added_to_the_existing_record(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "r1.json"
            activity.write_activity(path, _record())
            status = activity.record_delegate_usage(path, Path(d) / "locks", **self._args())
            self.assertEqual("recorded", status)
            self.assertEqual({"call-1": ENTRY}, activity.read_activity(path)["delegate_usage"])

    def test_a_count_never_creates_a_record(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "r1.json"
            try:
                status = activity.record_delegate_usage(path, Path(d) / "locks", **self._args())
            except (TypeError, AttributeError) as exc:
                self.fail(f"a missing record was built on: {exc!r}")
            self.assertEqual("no-record", status)
            self.assertFalse(path.exists())

    def test_a_busy_record_is_left_alone_rather_than_risk_a_lost_update(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "r1.json"
            activity.write_activity(path, _record())
            lock.claim_resource(Path(d) / "locks", "activity-r1", owner="other")
            status = activity.record_delegate_usage(
                path, Path(d) / "locks", attempts=2, _sleep=lambda _s: None, **self._args()
            )
            self.assertEqual("busy", status)
            self.assertNotIn("delegate_usage", activity.read_activity(path))


# --- the CLI: restamps keep counts, `delegate run` records them ---------------------


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _config():
    return cfg.load_config(CONFIG_PATH)


def _path(root, run_id="ship-7"):
    return activity.record_path(root, _config(), run_id)


def _seed(root, run_id="ship-7", **extra):
    record = activity.build_activity_record(command="ship", run_id=run_id, phase="s4")
    record.update(extra)
    activity.write_activity(_path(root, run_id), record)


class RestampsKeepTheCounts(unittest.TestCase):
    """Every stamp rebuilds the record; none may drop what a delegate recorded."""

    def test_an_autostamp_keeps_them(self):
        with tempfile.TemporaryDirectory() as d:
            _seed(d, delegate_usage={"call-1": dict(ENTRY)})
            cli._autostamp(_config(), d, "ship", "ship-7", "s8", verdict="pass")
            record = activity.read_activity(_path(d))
            self.assertEqual("s8", record["phase"])
            self.assertEqual({"call-1": ENTRY}, record.get("delegate_usage"))

    def test_an_activity_write_keeps_them(self):
        with tempfile.TemporaryDirectory() as d:
            _seed(d, delegate_usage={"call-1": dict(ENTRY)})
            rc, _out, _err = _run(
                ["activity", CONFIG_PATH, "--root", d, "--write", "--run-id", "ship-7"]
                + ["--phase", "s7"]
            )
            self.assertEqual(0, rc)
            record = activity.read_activity(_path(d))
            self.assertEqual("s7", record["phase"])
            self.assertEqual({"call-1": ENTRY}, record.get("delegate_usage"))

    def test_an_activity_write_still_replaces_a_malformed_record(self):
        with tempfile.TemporaryDirectory() as d:
            path = _path(d)
            path.parent.mkdir(parents=True)
            path.write_text("{not json", encoding="utf-8")
            rc, _out, _err = _run(
                ["activity", CONFIG_PATH, "--root", d, "--write", "--run-id", "ship-7"]
                + ["--phase", "s7"]
            )
            self.assertEqual(0, rc)
            self.assertEqual("s7", activity.read_activity(path)["phase"])

    def test_each_rebuilding_writer_takes_the_record_lock(self):
        """A stamp that ignores the lock can overwrite a delegate's concurrent count."""
        writers = {
            "autostamp": lambda d: cli._autostamp(_config(), d, "ship", "ship-7", "s8"),
            "write": lambda d: _run(
                ["activity", CONFIG_PATH, "--root", d, "--write", "--run-id", "ship-7"]
                + ["--phase", "s7"]
            ),
            "done": lambda d: _run(
                ["activity", CONFIG_PATH, "--root", d, "--done", "--run-id", "ship-7"]
            ),
        }
        landed = {
            "autostamp": ("s8", "running"),
            "write": ("s7", "running"),
            "done": ("s4", "done"),
        }
        for name, write in writers.items():
            with self.subTest(writer=name), tempfile.TemporaryDirectory() as d:
                _seed(d)
                lock.claim_resource(cli._lock_root(d), "activity-ship-7", owner="delegate")
                with (
                    patch.object(activity, "LOCK_ATTEMPTS", 3),
                    patch.object(activity.time, "sleep") as slept,
                ):
                    write(d)
                # it waited on the held lock, then landed anyway: a stamp must not be lost
                self.assertEqual(2, slept.call_count)
                record = activity.read_activity(_path(d))
                self.assertEqual(landed[name], (record["phase"], record["status"]))


class DelegateRunRecordsIntoTheRun(unittest.TestCase):
    def _prompt(self, d):
        path = Path(d) / "brief.md"
        path.write_text("write the fix", encoding="utf-8")
        return str(path)

    def _delegate(self, d, body, *extra, provider="openai-api:gpt-4o", env=None):
        argv = [
            "delegate",
            "run",
            "--provider",
            provider,
            "--role",
            "review",
            "--prompt-file",
            self._prompt(d),
            "--root",
            d,
            "--project",
            CONFIG_PATH,
            *extra,
        ]
        with (
            patch.object(api_delegate, "build_http_only_opener", return_value=FakeOpener(body)),
            patch.dict("os.environ", env or {"OPENAI_API_KEY": "sk-test"}),
        ):
            try:
                rc, out, _err = _run(argv)
            except Exception as exc:  # the contract: a counting failure never raises
                self.fail(f"keel delegate run raised {exc!r}")
        return rc, json.loads(out)

    def test_the_counts_reach_the_record_and_cost_report_reads_them_as_measured(self):
        with tempfile.TemporaryDirectory() as d:
            _seed(d)
            before = cost.generate_cost_report(d)
            rc, result = self._delegate(
                d, OPENAI, "--activity-run-id", "ship-7", "--run-id", "call-a"
            )
            self.assertEqual(0, rc)
            self.assertEqual("recorded", result.get("activity_usage"))
            usage = activity.read_activity(_path(d))["delegate_usage"]
            self.assertEqual(
                {"call-a": {"model": "gpt-4o", "prompt_tokens": 2000, "completion_tokens": 600}},
                usage,
            )
            after = cost.generate_cost_report(d)
            self.assertEqual(("estimated", "measured"), (before.token_basis, after.token_basis))
            self.assertEqual(
                (2000, 600), (after.total_prompt_tokens, after.total_completion_tokens)
            )
            self.assertEqual(["gpt-4o"], list(after.model_breakdown))

    def test_two_calls_in_one_run_are_both_counted(self):
        with tempfile.TemporaryDirectory() as d:
            _seed(d)
            self._delegate(d, OPENAI, "--activity-run-id", "ship-7")
            self._delegate(d, OPENAI, "--activity-run-id", "ship-7")
            report = cost.generate_cost_report(d)
            self.assertEqual(4000, report.total_prompt_tokens)
            self.assertEqual(1, report.model_breakdown["gpt-4o"]["runs"])

    def test_without_the_flag_nothing_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            _seed(d)
            _rc, result = self._delegate(d, OPENAI)
            self.assertNotIn("activity_usage", result)
            self.assertEqual({"prompt_tokens": 2000, "completion_tokens": 600}, result["usage"])
            self.assertNotIn("delegate_usage", activity.read_activity(_path(d)))

    def test_a_response_without_usage_records_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _seed(d)
            body = _with_usage(OPENAI, "usage", _ABSENT)
            _rc, result = self._delegate(d, body, "--activity-run-id", "ship-7")
            self.assertEqual("no-usage", result.get("activity_usage"))
            self.assertEqual("estimated", cost.generate_cost_report(d).token_basis)

    def test_a_run_nothing_has_stamped_gets_no_record(self):
        with tempfile.TemporaryDirectory() as d:
            _rc, result = self._delegate(d, OPENAI, "--activity-run-id", "ship-7")
            self.assertEqual("no-record", result.get("activity_usage"))
            self.assertFalse(_path(d).exists())

    def test_without_a_config_the_record_cannot_be_found(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(cli, "_delegate_config", side_effect=[_config(), None]):
                _rc, result = self._delegate(d, OPENAI, "--activity-run-id", "ship-7")
            self.assertEqual("no-config", result.get("activity_usage"))

    def test_a_malformed_record_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as d:
            path = _path(d)
            path.parent.mkdir(parents=True)
            path.write_text("{not json", encoding="utf-8")
            rc, result = self._delegate(d, OPENAI, "--activity-run-id", "ship-7")
            self.assertEqual(0, rc, "the delegate's answer is not lost to a counting failure")
            self.assertEqual("error", result.get("activity_usage"))

    def test_a_busy_record_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            _seed(d)
            lock.claim_resource(cli._lock_root(d), "activity-ship-7", owner="other")
            with (
                patch.object(activity, "LOCK_ATTEMPTS", 1),
                patch.object(activity.time, "sleep"),
            ):
                _rc, result = self._delegate(d, OPENAI, "--activity-run-id", "ship-7")
            self.assertEqual("busy", result.get("activity_usage"))

    def test_the_detached_child_is_told_where_to_record(self):
        with tempfile.TemporaryDirectory() as d:
            with patch("keel.delegaterun.subprocess.Popen") as popen:
                popen.return_value.pid = 1
                _rc, record = self._delegate(
                    d, OPENAI, "--activity-run-id", "ship-7", "--run-id", "r9", "--detach"
                )
            child = record["argv"]
            self.assertIn("--activity-run-id", child)
            self.assertEqual("ship-7", child[child.index("--activity-run-id") + 1])

    def test_the_child_records_under_its_run_id_and_stores_the_outcome(self):
        with tempfile.TemporaryDirectory() as d:
            _seed(d)
            _rc, result = self._delegate(
                d, OPENAI, "--activity-run-id", "ship-7", "--run-id", "r9", "--_child"
            )
            self.assertEqual("recorded", result.get("activity_usage"))
            self.assertIn("r9", activity.read_activity(_path(d))["delegate_usage"])
            stored = delegaterun.load_state(d, "r9")["result"]
            self.assertEqual("recorded", stored.get("activity_usage"))


class TheShipAdapterJoinsTheRuns(unittest.TestCase):
    """The adapter is what gives `delegate run` the ship run's id; without it no count
    reaches a ship record. Every s4/s7 invocation in ship.md must carry it."""

    def test_every_delegate_run_example_passes_the_activity_run_id(self):
        source = (
            Path(__file__).resolve().parent.parent / "src/keel/adapters/commands/ship.md"
        ).read_text(encoding="utf-8")
        commands = [
            " ".join(block.replace("\\\n", " ").split())
            for fence in re.findall(r"```bash\n(.*?)```", source, re.S)
            for block in re.split(r"\n(?=\s*keel )", fence)
            if block.strip().startswith("keel delegate run")
        ]
        self.assertGreaterEqual(len(commands), 4, commands)
        for command in commands:
            with self.subTest(command=command):
                self.assertIn('--activity-run-id "$RUN_ID"', command)


# --- the report ---------------------------------------------------------------------


def _usage(**entries):
    return {"delegate_usage": dict(entries)}


class TheReportPricesEachCallAtItsOwnModel(unittest.TestCase):
    def test_a_run_with_calls_to_two_models_prices_each_at_its_own(self):
        rec = _usage(
            a={"model": "gpt-4o", "prompt_tokens": 1_000_000, "completion_tokens": 0},
            b={"model": "gemini-2.5-flash", "prompt_tokens": 1_000_000, "completion_tokens": 0},
        )
        report = cost.calculate_cost_report([rec])
        self.assertEqual(("measured", 1), (report.token_basis, report.total_runs))
        self.assertEqual(2.50, report.model_breakdown["gpt-4o"]["cost_usd"])
        self.assertEqual(0.15, report.model_breakdown["gemini-2.5-flash"]["cost_usd"])
        self.assertEqual(2.65, report.total_cost_usd)

    def test_an_unpriced_call_counts_the_run_once_and_leaves_the_priced_saving(self):
        rec = _usage(
            a={"model": "gpt-4o", "prompt_tokens": 1_000_000, "completion_tokens": 0},
            b={"model": "nobody-listed-this", "prompt_tokens": 10, "completion_tokens": 1},
            c={"model": "also-unlisted", "prompt_tokens": 10, "completion_tokens": 1},
        )
        report = cost.calculate_cost_report([rec])
        self.assertEqual(1, report.unpriced_runs)
        self.assertEqual(round(15.00 - 2.50, 4), report.estimated_savings_usd)

    def test_a_record_counts_both_its_own_counts_and_its_delegates(self):
        rec = {"model": "gpt-4o", "prompt_tokens": 5, "completion_tokens": 5}
        rec.update(_usage(a={"model": "gpt-4o", "prompt_tokens": 7, "completion_tokens": 1}))
        report = cost.calculate_cost_report([rec])
        self.assertEqual((12, 6), (report.total_prompt_tokens, report.total_completion_tokens))

    def test_zero_or_malformed_entries_are_not_a_measurement(self):
        for entry in (
            {"model": "gpt-4o", "prompt_tokens": 0, "completion_tokens": 0},
            {"model": "gpt-4o", "prompt_tokens": "9", "completion_tokens": 9},
        ):
            with self.subTest(entry=entry):
                try:
                    report = cost.calculate_cost_report([_usage(a=entry)])
                except TypeError as exc:
                    self.fail(f"a malformed entry was summed: {exc!r}")
                self.assertEqual("estimated", report.token_basis)
                self.assertEqual(cost.ASSUMED_PROMPT_TOKENS, report.total_prompt_tokens)
        try:
            report = cost.calculate_cost_report([{"delegate_usage": ["not", "an", "object"]}])
        except AttributeError as exc:
            self.fail(f"a non-object usage field was read as one: {exc!r}")
        self.assertEqual("estimated", report.token_basis)


class TheReportSaysWhatAMeasurementCovers(unittest.TestCase):
    ENTRY = {"model": "gpt-4o", "prompt_tokens": 10, "completion_tokens": 1}
    SCOPE = "never an agent host's own tokens"

    def _render(self, records):
        return cost.render_cost_report(cost.calculate_cost_report(records))

    def test_a_single_measured_run_is_not_all_1_runs(self):
        text = self._render([_usage(a=self.ENTRY)])
        self.assertIn("measured (the 1 run carries token counts)", text)
        self.assertNotIn("all 1 runs", text)
        self.assertIn(
            "measured (all 2 runs carry token counts)", self._render([_usage(a=self.ENTRY)] * 2)
        )

    def test_measured_and_mixed_say_what_a_count_covers_and_estimated_does_not(self):
        measured = self._render([_usage(a=self.ENTRY)])
        mixed = self._render([_usage(a=self.ENTRY), {}])
        estimated = self._render([{}])
        self.assertIn(self.SCOPE, " ".join(measured.split()))
        self.assertIn(self.SCOPE, " ".join(mixed.split()))
        self.assertNotIn(self.SCOPE, " ".join(estimated.split()))

    def test_the_title_does_not_call_an_estimate_a_ledger(self):
        self.assertTrue(self._render([{}]).startswith("Keel Token & Cost Report\n"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
