"""Unit tests for the delegate executor (:mod:`keel.delegaterun`, #1012).

Fully offline and deterministic: the subprocess runner, the HTTP opener, the
environment, the clock, the sleep and the prompt reader are all injected. Nothing here
spawns a process, opens a socket, or waits on wall-clock time.
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from keel import config as cfg
from keel import delegate, delegaterun, providers, runner
from keel.api_delegate import OPENAI_COMPATIBLE
from keel.runner import CommandResult

PROMPT_PATH = "/tmp/brief.md"
PROMPT = "review this diff\n"


def _builtin(name):
    for provider in providers.builtin_providers():
        if provider.name == name:
            return provider
    raise AssertionError(f"no built-in provider {name!r}")  # pragma: no cover


def _plan(name, role="review", **kwargs):
    return delegate.plan_run(_builtin(name), role, PROMPT_PATH, **kwargs)


def _read(_path):
    return PROMPT


def _clock(*ticks):
    """A monotonic clock returning ``ticks`` in order, then repeating the last."""
    values = list(ticks)

    def now():
        return values.pop(0) if len(values) > 1 else values[0]

    return now


class _Recorder:
    """A fake ``runner.run_argv`` that records its call and returns a fixed result."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        return self.result


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
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.exc is not None:
            raise self.exc
        return self.response


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "boom", {}, None)


class CliExecutionTest(unittest.TestCase):
    def test_a_successful_run_returns_the_full_json_contract(self):
        run = _Recorder(CommandResult(True, 0, "the review", stdout="the review"))
        result = delegaterun.execute(
            _plan("claude", model="opus-4.5"),
            _run=run,
            _read=_read,
            _now=_clock(10.0, 12.5),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "the review")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["duration_s"], 2.5)
        self.assertFalse(result["timed_out"])
        self.assertIsNone(result["error_code"])
        self.assertEqual(result["provider"], "claude")
        self.assertEqual(result["vendor"], "claude")
        self.assertEqual(result["model"], "opus-4.5")
        self.assertEqual(result["role"], "review")
        self.assertEqual(result["transport"], "cli")
        self.assertEqual(result["attribution"]["agent_label"], "agent:claude")
        self.assertFalse(result["effort_applied"])
        self.assertEqual(result["warnings"], [])

    def test_the_prompt_travels_on_stdin_and_never_on_the_argv(self):
        run = _Recorder(CommandResult(True, 0, "ok", stdout="ok"))
        delegaterun.execute(_plan("codex"), _run=run, _read=_read)
        argv, kwargs = run.calls[0]
        self.assertNotIn(PROMPT, argv)
        self.assertEqual(kwargs["stdin_text"], PROMPT)
        self.assertEqual(kwargs["timeout"], delegate.DEFAULT_TIMEOUT_S)

    def test_agy_gets_an_ndjson_frame_and_its_reply_is_parsed_back(self):
        stdout = '{"event": "result", "result": {"response": "the verdict"}}'
        run = _Recorder(CommandResult(True, 0, stdout, stdout=stdout))
        result = delegaterun.execute(_plan("agy", model="gemini-3"), _run=run, _read=_read)
        _argv, kwargs = run.calls[0]
        self.assertEqual(json.loads(kwargs["stdin_text"])["message"]["content"], PROMPT)
        self.assertEqual(result["text"], "the verdict")

    def test_prompt_mode_arg_appends_the_prompt_as_the_last_argument(self):
        profile = cfg.DelegateProfile(vendor="cli", command="cursor-agent", prompt_mode="arg")
        config = cfg.ProjectConfig(
            extends="keel",
            core_version="^1.0",
            base_branch="main",
            knobs=cfg.Knobs(build_gate_cmd="true", delegate_profiles={"cursor": profile}),
        )
        resolution = delegate.resolve_provider(config, None, "cursor")
        plan = delegate.plan_run(
            resolution.provider, "implement", PROMPT_PATH, profile=resolution.profile
        )
        run = _Recorder(CommandResult(True, 0, "ok", stdout="ok"))
        delegaterun.execute(plan, _run=run, _read=_read)
        argv, kwargs = run.calls[0]
        self.assertEqual(argv[-1], PROMPT)
        self.assertIsNone(kwargs["stdin_text"])

    def test_a_nonzero_exit_fails_soft_with_the_output_tail(self):
        run = _Recorder(CommandResult(False, 2, "boom: bad flag", stderr="boom: bad flag"))
        result = delegaterun.execute(_plan("codex"), _run=run, _read=_read)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "nonzero-exit")
        self.assertEqual(result["exit_code"], 2)
        self.assertIn("boom", result["error"])

    def test_a_missing_binary_is_named_as_such(self):
        """Built through the real runner, because the shape is the whole point.

        `run_argv`'s OSError path returns code 127 *with the message in `output`*, so the
        first cut's "code 127 and no output" test could never fire on a real run — the
        branch was unreachable and a hand-built CommandResult hid that. The classifier now
        reads the runner's own `spawn_failed` signal.
        """

        def explode(*_args, **_kwargs):
            raise FileNotFoundError(2, "No such file or directory: 'agy'")

        real = runner.run_argv(["agy"], _run=explode)
        self.assertTrue(real.spawn_failed)
        self.assertEqual(real.code, 127)
        result = delegaterun.execute(_plan("agy"), _run=_Recorder(real), _read=_read)
        self.assertEqual(result["error_code"], "missing-binary")
        self.assertIn("agy", result["error"])

    def test_a_cli_that_ran_and_exited_127_is_a_plain_nonzero_exit(self):
        run = _Recorder(
            CommandResult(False, 127, "command not understood", stdout="command not understood")
        )
        result = delegaterun.execute(_plan("codex"), _run=run, _read=_read)
        self.assertEqual(result["error_code"], "nonzero-exit")

    def test_a_timeout_is_reported_as_a_timeout_not_a_failure(self):
        run = _Recorder(CommandResult(False, 124, "timed out after 5s", timed_out=True))
        result = delegaterun.execute(_plan("claude", timeout=5), _run=run, _read=_read)
        self.assertEqual(result["error_code"], "timeout")
        self.assertTrue(result["timed_out"])
        self.assertIn("5s", result["error"])

    def test_a_cli_quota_refusal_maps_to_rate_limit_so_the_no_retry_rule_applies(self):
        run = _Recorder(CommandResult(False, 1, "Error: 429 Too Many Requests"))
        result = delegaterun.execute(_plan("claude"), _run=run, _read=_read)
        self.assertEqual(result["error_code"], "rate-limit")

    def test_a_clean_exit_with_no_output_is_not_a_success(self):
        run = _Recorder(CommandResult(True, 0, "   ", stdout="   "))
        result = delegaterun.execute(_plan("claude"), _run=run, _read=_read)
        self.assertEqual(result["error_code"], "empty-output")

    def test_stdout_is_preferred_over_the_stderr_contaminated_output(self):
        run = _Recorder(
            CommandResult(True, 0, "warning\nthe review", stdout="the review", stderr="warning\n")
        )
        result = delegaterun.execute(_plan("claude"), _run=run, _read=_read)
        self.assertEqual(result["text"], "the review")

    def test_stderr_noise_on_an_empty_stdout_is_not_the_delegates_answer(self):
        """Exit 0 + nothing on stdout + chatter on stderr must not read as a success.

        `result.stdout or result.output` fell back to the concatenation, so a CLI that
        printed a login notice and produced no answer came back `ok: true` with the notice
        as its `text` — downstream, a diff to apply or a verdict to post.
        """
        noise = "Warning: config deprecated\nUsing cached credentials\n"
        run = _Recorder(CommandResult(True, 0, noise, stdout="", stderr=noise))
        result = delegaterun.execute(_plan("claude"), _run=run, _read=_read)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "empty-output")
        self.assertEqual(result["text"], "")

    def test_a_failure_tail_still_shows_both_streams(self):
        run = _Recorder(
            CommandResult(
                False, 2, "partial\nboom: bad flag", stdout="partial\n", stderr="boom: bad flag"
            )
        )
        result = delegaterun.execute(_plan("claude"), _run=run, _read=_read)
        self.assertIn("boom: bad flag", result["error"])
        self.assertEqual(result["text"], "partial\n")

    def test_an_agy_stream_with_no_result_frame_never_borrows_stderr(self):
        run = _Recorder(
            CommandResult(True, 0, "agy: starting\n", stdout="", stderr="agy: starting\n")
        )
        result = delegaterun.execute(_plan("agy"), _run=run, _read=_read)
        self.assertEqual(result["error_code"], "empty-output")


class PromptTest(unittest.TestCase):
    def test_an_unreadable_prompt_file_fails_soft(self):
        def boom(_path):
            raise OSError("no such file")

        result = delegaterun.execute(_plan("claude"), _read=boom)
        self.assertEqual(result["error_code"], "no-prompt")
        self.assertIn("no such file", result["error"])

    def test_an_empty_prompt_file_fails_soft(self):
        result = delegaterun.execute(_plan("claude"), _read=lambda _p: "  \n ")
        self.assertEqual(result["error_code"], "no-prompt")

    def test_the_default_reader_reads_the_real_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "brief.md"
            path.write_text("hello", encoding="utf-8")
            run = _Recorder(CommandResult(True, 0, "ok", stdout="ok"))
            plan = delegate.plan_run(_builtin("claude"), "review", str(path))
            delegaterun.execute(plan, _run=run)
            self.assertEqual(run.calls[0][1]["stdin_text"], "hello")


class ApiExecutionTest(unittest.TestCase):
    ENV = {"ANTHROPIC_API_KEY": "sk-key"}

    def test_a_hosted_run_makes_exactly_one_call_and_returns_its_text(self):
        body = json.dumps({"content": [{"type": "text", "text": "the diff"}]})
        opener = FakeOpener(FakeResponse(body))
        plan = _plan("anthropic-api", model="claude-opus-4-5", effort="high")
        result = delegaterun.execute(plan, _opener=opener, _env=self.ENV, _read=_read)
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "the diff")
        self.assertIsNone(result["exit_code"])
        self.assertEqual(len(opener.requests), 1)
        sent = json.loads(opener.requests[0][0].data.decode("utf-8"))
        self.assertEqual(sent["thinking"], {"type": "enabled", "budget_tokens": 32768})
        self.assertGreater(sent["max_tokens"], 32768)

    def test_a_429_is_reported_as_rate_limit(self):
        opener = FakeOpener(exc=_http_error(429))
        plan = _plan("anthropic-api", model="claude-opus-4-5")
        result = delegaterun.execute(plan, _opener=opener, _env=self.ENV, _read=_read)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "rate-limit")

    def test_a_missing_key_is_reported_without_a_traceback(self):
        plan = _plan("openai-api", model="gpt-5.5")
        result = delegaterun.execute(plan, _opener=FakeOpener(), _env={}, _read=_read)
        self.assertEqual(result["error_code"], "no-key")

    def test_an_openai_compatible_profile_reaches_its_configured_endpoint(self):
        profile = cfg.DelegateProfile(
            vendor=OPENAI_COMPATIBLE,
            endpoint="http://localhost:8000/v1/chat/completions",
            api_key_env="VLLM_API_KEY",
            model="qwen",
        )
        config = cfg.ProjectConfig(
            extends="keel",
            core_version="^1.0",
            base_branch="main",
            knobs=cfg.Knobs(build_gate_cmd="true", delegate_profiles={"vllm": profile}),
        )
        resolution = delegate.resolve_provider(config, None, "vllm")
        plan = delegate.plan_run(
            resolution.provider, "review", PROMPT_PATH, effort="low", profile=resolution.profile
        )
        body = json.dumps({"choices": [{"message": {"content": "verdict"}}]})
        opener = FakeOpener(FakeResponse(body))
        result = delegaterun.execute(plan, _opener=opener, _env={"VLLM_API_KEY": "k"}, _read=_read)
        self.assertTrue(result["ok"])
        self.assertEqual(len(opener.requests), 1)
        request = opener.requests[0][0]
        self.assertEqual(request.full_url, "http://localhost:8000/v1/chat/completions")
        self.assertEqual(json.loads(request.data.decode("utf-8"))["reasoning_effort"], "low")

    def test_a_google_effort_fragment_does_not_drop_max_output_tokens(self):
        body = json.dumps({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
        opener = FakeOpener(FakeResponse(body))
        plan = _plan("google-api", model="gemini-3-pro", effort="medium")
        delegaterun.execute(plan, _opener=opener, _env={"GEMINI_API_KEY": "k"}, _read=_read)
        sent = json.loads(opener.requests[0][0].data.decode("utf-8"))
        self.assertEqual(sent["generationConfig"]["thinkingConfig"]["thinkingBudget"], 8192)
        self.assertIn("maxOutputTokens", sent["generationConfig"])


class OllamaExecutionTest(unittest.TestCase):
    def _plan(self, **kwargs):
        return _plan("ollama", model="qwen2.5", **kwargs)

    def test_a_local_generation_posts_to_the_hardcoded_loopback_endpoint(self):
        opener = FakeOpener(FakeResponse(json.dumps({"response": "the diff"})))
        result = delegaterun.execute(self._plan(), _opener=opener, _read=_read)
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "the diff")
        request = opener.requests[0][0]
        self.assertEqual(request.full_url, delegate.OLLAMA_GENERATE_URL)
        self.assertEqual(json.loads(request.data.decode("utf-8"))["stream"], False)

    def test_an_unparseable_body_fails_soft(self):
        opener = FakeOpener(FakeResponse("not json"))
        result = delegaterun.execute(self._plan(), _opener=opener, _read=_read)
        self.assertEqual(result["error_code"], "bad-response")

    def test_a_body_with_no_completion_fails_soft(self):
        opener = FakeOpener(FakeResponse(json.dumps({"done": True})))
        result = delegaterun.execute(self._plan(), _opener=opener, _read=_read)
        self.assertEqual(result["error_code"], "bad-response")

    def test_a_stopped_server_is_a_network_error_not_a_traceback(self):
        opener = FakeOpener(exc=urllib.error.URLError("connection refused"))
        result = delegaterun.execute(self._plan(), _opener=opener, _read=_read)
        self.assertEqual(result["error_code"], "network")
        self.assertIn("connection refused", result["error"])

    def test_a_429_from_the_local_server_is_still_a_rate_limit(self):
        opener = FakeOpener(exc=_http_error(429))
        result = delegaterun.execute(self._plan(), _opener=opener, _read=_read)
        self.assertEqual(result["error_code"], "rate-limit")

    def test_another_http_error_is_reported_as_http(self):
        opener = FakeOpener(exc=_http_error(500))
        result = delegaterun.execute(self._plan(), _opener=opener, _read=_read)
        self.assertEqual(result["error_code"], "http")

    def test_a_non_2xx_that_the_opener_returned_is_never_parsed_as_a_completion(self):
        opener = FakeOpener(FakeResponse(json.dumps({"response": "x"}), status=302))
        result = delegaterun.execute(self._plan(), _opener=opener, _read=_read)
        self.assertEqual(result["error_code"], "http")

    def test_the_default_opener_is_the_shared_non_redirecting_one(self):
        # Not "whatever urllib does": keel makes outbound HTTP from one opener, so this
        # path must reach `api_delegate.build_http_only_opener` rather than `urlopen`.
        opener = FakeOpener(FakeResponse(json.dumps({"response": "local"})))
        with patch.object(
            delegaterun.api_delegate, "build_http_only_opener", return_value=opener
        ) as built:
            result = delegaterun.execute(self._plan(), _read=_read)
        built.assert_called_once_with()
        self.assertEqual(result["text"], "local")


class RunIdTest(unittest.TestCase):
    def test_a_traversing_run_id_is_refused_before_it_becomes_a_path(self):
        for bad in ("../escape", "a/b", "", "..", "run id", "x\x00y"):
            with self.subTest(run_id=bad), self.assertRaises(delegaterun.RunIdError):
                delegaterun.check_run_id(bad)

    def test_a_normal_run_id_passes(self):
        self.assertEqual(delegaterun.check_run_id("1770000000-abc123"), "1770000000-abc123")

    def test_a_generated_run_id_is_time_prefixed_and_unique(self):
        run_id = delegaterun.new_run_id(_clock=lambda: 1770000000.9, _token=lambda: "deadbeef")
        self.assertEqual(run_id, "1770000000-deadbeef")
        delegaterun.check_run_id(delegaterun.new_run_id())


class _FakeChild:
    def __init__(self, pid):
        self.pid = pid


class _FakePopen:
    def __init__(self, pid=4242, exc=None):
        self.pid = pid
        self.exc = exc
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if self.exc is not None:
            raise self.exc
        return _FakeChild(self.pid)


class DetachLifecycleTest(unittest.TestCase):
    def test_the_parent_records_a_running_run_before_the_child_writes_anything(self):
        with tempfile.TemporaryDirectory() as root:
            popen = _FakePopen()
            record = delegaterun.start_detached(
                ["python", "-m", "keel"], root=root, run_id="r1", _popen=popen
            )
            self.assertEqual(record["status"], "running")
            self.assertEqual(record["pid"], 4242)
            self.assertIsNone(record["result"])
            if hasattr(os, "setsid"):
                self.assertTrue(popen.calls[0][1]["start_new_session"])
            else:  # Windows has no sessions; the flag must not be passed at all
                self.assertNotIn("start_new_session", popen.calls[0][1])
            self.assertTrue(Path(record["out_path"]).exists())
            # The pid lives beside the record, never in it: two writers, one file each.
            self.assertNotIn("pid", delegaterun.load_state(root, "r1"))
            self.assertEqual(delegaterun.read_pid(root, "r1"), 4242)
            self.assertEqual(delegaterun.run_record(root, "r1"), record)

    def test_the_child_overwrites_the_record_with_its_result(self):
        with tempfile.TemporaryDirectory() as root:
            delegaterun.start_detached([], root=root, run_id="r1", _popen=_FakePopen())
            run = _Recorder(CommandResult(True, 0, "done", stdout="done"))
            result = delegaterun.execute(_plan("claude"), _run=run, _read=_read)
            delegaterun.finish_detached(root, "r1", result)
            record = delegaterun.run_record(root, "r1")
            self.assertEqual(record["status"], "done")
            self.assertEqual(record["pid"], 4242)
            self.assertTrue(record["result"]["ok"])
            self.assertIn("finished_at", record)

    def test_a_child_finishing_a_run_the_parent_never_recorded_still_writes_a_record(self):
        with tempfile.TemporaryDirectory() as root:
            delegaterun.finish_detached(root, "orphan", {"ok": False})
            record = delegaterun.load_state(root, "orphan")
            self.assertEqual(record["status"], "done")
            self.assertEqual(record["argv"], [])

    def test_a_spawn_failure_is_recorded_as_a_finished_failed_run(self):
        with tempfile.TemporaryDirectory() as root:
            record = delegaterun.start_detached(
                ["nope"], root=root, run_id="r1", _popen=_FakePopen(exc=OSError("no such binary"))
            )
            self.assertEqual(record["status"], "done")
            self.assertEqual(record["result"]["error_code"], "spawn-failed")
            self.assertIsNone(record["pid"])

    def test_an_unwritable_output_file_is_recorded_rather_than_raised(self):
        with tempfile.TemporaryDirectory() as root:
            # A directory where the .out file should go makes open() fail.
            delegaterun.state_dir(root).mkdir(parents=True)
            delegaterun.out_path(root, "r1").mkdir()
            record = delegaterun.start_detached(
                ["nope"], root=root, run_id="r1", _popen=_FakePopen()
            )
            self.assertEqual(record["result"]["error_code"], "spawn-failed")

    def test_an_unwritable_root_is_a_contract_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(delegaterun.Path, "mkdir", side_effect=PermissionError("denied")):
                record = delegaterun.start_detached([], root=root, run_id="r1", _popen=_FakePopen())
            self.assertEqual(record["status"], "done")
            self.assertEqual(record["result"]["error_code"], "spawn-failed")
            self.assertIn("denied", record["result"]["error"])

    def test_a_root_that_cannot_hold_the_record_still_returns_the_contract(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(delegaterun, "write_state", side_effect=OSError("read-only fs")):
                record = delegaterun.start_detached([], root=root, run_id="r1", _popen=_FakePopen())
            self.assertEqual(record["result"]["error_code"], "spawn-failed")

    def test_start_detached_refuses_an_unsafe_run_id(self):
        with tempfile.TemporaryDirectory() as root, self.assertRaises(delegaterun.RunIdError):
            delegaterun.start_detached([], root=root, run_id="../x", _popen=_FakePopen())

    def test_list_runs_reports_every_readable_record_and_skips_the_rest(self):
        with tempfile.TemporaryDirectory() as root:
            delegaterun.start_detached([], root=root, run_id="b", _popen=_FakePopen())
            delegaterun.start_detached([], root=root, run_id="a", _popen=_FakePopen())
            (delegaterun.state_dir(root) / "torn.json").write_text("{oops", encoding="utf-8")
            names = [record["run_id"] for record in delegaterun.list_runs(root)]
            self.assertEqual(names, ["a", "b"])

    def test_list_runs_on_a_root_with_no_state_directory_is_empty(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(delegaterun.list_runs(root), [])

    def test_load_state_is_none_for_an_unsafe_or_missing_id(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(delegaterun.load_state(root, "../escape"))
            self.assertIsNone(delegaterun.load_state(root, "absent"))


#: A liveness probe that says the child is still running, for the tests that are not
#: about liveness. The fake pids the detach tests use do not exist, so the real probe
#: would (correctly) call every one of them lost.
_ALIVE = staticmethod(lambda _pid: True)


def _utc():
    return datetime.datetime.now(datetime.UTC)


def _at(offset_seconds):
    """A UTC clock fixed ``offset_seconds`` from now — for deadline arithmetic."""
    moment = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=offset_seconds)
    return lambda: moment


class WaitTest(unittest.TestCase):
    def _start(self, root, run_id="r1", **kwargs):
        return delegaterun.start_detached(
            [], root=root, run_id=run_id, _popen=_FakePopen(), **kwargs
        )

    def test_wait_returns_the_result_once_the_child_has_finished(self):
        with tempfile.TemporaryDirectory() as root:
            self._start(root)
            delegaterun.finish_detached(root, "r1", {"ok": True, "text": "done"})
            result, error = delegaterun.wait(root, "r1", _sleep=lambda _s: None, _alive=_ALIVE)
            self.assertIsNone(error)
            self.assertEqual(result["text"], "done")

    def test_wait_polls_until_the_state_file_says_done(self):
        with tempfile.TemporaryDirectory() as root:
            self._start(root)
            sleeps = []

            def sleep(seconds):
                sleeps.append(seconds)
                if len(sleeps) == 2:
                    delegaterun.finish_detached(root, "r1", {"ok": True})

            result, error = delegaterun.wait(
                root,
                "r1",
                timeout=100,
                poll=0.25,
                _sleep=sleep,
                _now=_clock(0.0),
                _alive=_ALIVE,
            )
            self.assertIsNone(error)
            self.assertTrue(result["ok"])
            self.assertEqual(sleeps, [0.25, 0.25])

    def test_wait_on_an_unknown_run_id_fails_closed_immediately(self):
        with tempfile.TemporaryDirectory() as root:
            result, error = delegaterun.wait(root, "absent", _sleep=lambda _s: None)
            self.assertIsNone(result)
            self.assertEqual(error, "unknown-run")

    def test_wait_gives_up_at_the_callers_timeout(self):
        with tempfile.TemporaryDirectory() as root:
            self._start(root)
            result, error = delegaterun.wait(
                root,
                "r1",
                timeout=5,
                _sleep=lambda _s: None,
                _now=_clock(0.0, 1.0, 9.0),
                _alive=_ALIVE,
            )
            self.assertIsNone(result)
            self.assertEqual(error, "timeout")
            # The run may still be alive, so nothing is marked.
            self.assertEqual(delegaterun.run_record(root, "r1")["status"], "running")
            self.assertIsNone(delegaterun.read_crash(root, "r1"))

    def test_a_killed_child_is_marked_crashed_instead_of_blocking_forever(self):
        """The SIGKILL case: without liveness, `wait` with no --timeout never returns.

        The record stays `running` because the only writer that would have changed it is
        the process that just died, so a caller polling the file waits out the heat death
        of the universe.
        """
        with tempfile.TemporaryDirectory() as root:
            self._start(root)
            result, error = delegaterun.wait(
                root, "r1", _sleep=lambda _s: None, _alive=lambda _pid: False
            )
            self.assertEqual(error, "lost")
            self.assertEqual(result["error_code"], "lost")
            self.assertIn("pid 4242", result["error"])
            # The crash is noted beside the record, never written into it: the record
            # keeps exactly one writer, the child.
            self.assertEqual(delegaterun.load_state(root, "r1")["status"], "running")
            self.assertEqual(delegaterun.run_record(root, "r1")["status"], "crashed")
            # `status` must stop claiming it is running.
            self.assertEqual(delegaterun.list_runs(root)[0]["status"], "crashed")

    def test_a_crashed_record_is_reported_without_re_deciding_it(self):
        with tempfile.TemporaryDirectory() as root:
            self._start(root)
            delegaterun.wait(root, "r1", _sleep=lambda _s: None, _alive=lambda _pid: False)
            result, error = delegaterun.wait(root, "r1", _sleep=lambda _s: None, _alive=_ALIVE)
            self.assertEqual(error, "lost")
            self.assertEqual(result["error_code"], "lost")

    def test_a_child_that_wrote_its_result_just_before_exiting_is_not_called_lost(self):
        """The exit-before-observe race: the result lands, then the pid disappears.

        A dead pid is re-checked against the file before the run is declared lost,
        because a delegate necessarily writes its result *before* the process ends and
        the two are observed in the other order often enough to matter.
        """
        with tempfile.TemporaryDirectory() as root:
            self._start(root)

            def dead(_pid):
                delegaterun.finish_detached(root, "r1", {"ok": True, "text": "landed"})
                return False

            result, error = delegaterun.wait(root, "r1", _sleep=lambda _s: None, _alive=dead)
            self.assertIsNone(error)
            self.assertEqual(result["text"], "landed")
            self.assertEqual(delegaterun.run_record(root, "r1")["status"], "done")

    def test_a_run_past_its_own_deadline_is_lost_even_with_no_pid_and_no_timeout(self):
        with tempfile.TemporaryDirectory() as root:
            self._start(root, timeout=30)
            record = delegaterun.load_state(root, "r1")
            self.assertIsNotNone(record["deadline_at"])
            result, error = delegaterun.wait(
                root,
                "r1",
                _sleep=lambda _s: None,
                _alive=_ALIVE,
                _clock=_at(30 + delegaterun.DEADLINE_GRACE_S + 1),
            )
            self.assertEqual(error, "lost")
            self.assertIn("deadline", result["error"])

    def test_a_run_inside_its_deadline_keeps_waiting(self):
        with tempfile.TemporaryDirectory() as root:
            self._start(root, timeout=3600)
            result, error = delegaterun.wait(
                root, "r1", timeout=1, _sleep=lambda _s: None, _now=_clock(0.0, 9.0), _alive=_ALIVE
            )
            self.assertEqual(error, "timeout")
            self.assertIsNone(result)

    def test_a_run_started_without_a_timeout_stamps_no_deadline(self):
        with tempfile.TemporaryDirectory() as root:
            record = self._start(root)
            self.assertIsNone(record["deadline_at"])
            self.assertIsNone(record["timeout"])

    def test_an_unparseable_deadline_is_ignored_rather_than_crashing_the_wait(self):
        with tempfile.TemporaryDirectory() as root:
            self._start(root)
            record = delegaterun.load_state(root, "r1")
            record["deadline_at"] = "not a timestamp"
            delegaterun.write_state(root, record)
            result, error = delegaterun.wait(
                root, "r1", timeout=1, _sleep=lambda _s: None, _now=_clock(0.0, 9.0), _alive=_ALIVE
            )
            self.assertEqual(error, "timeout")
            self.assertIsNone(result)

    def test_the_run_disappearing_mid_wait_is_reported_as_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            self._start(root)

            def dead(_pid):
                delegaterun.state_path(root, "r1").unlink()
                return False

            result, error = delegaterun.wait(root, "r1", _sleep=lambda _s: None, _alive=dead)
            self.assertIsNone(result)
            self.assertEqual(error, "unknown-run")


class LivenessProbeTest(unittest.TestCase):
    def test_our_own_process_is_alive(self):
        self.assertTrue(delegaterun.process_is_alive(os.getpid()))

    def test_a_pid_that_cannot_exist_is_dead(self):
        with patch.object(delegaterun.os, "kill", side_effect=ProcessLookupError):
            self.assertFalse(delegaterun.process_is_alive(4242))

    def test_a_pid_we_may_not_signal_is_alive_not_dead(self):
        # PermissionError means "it exists and belongs to someone else" — reading it as
        # dead would declare a perfectly healthy delegate lost.
        with patch.object(delegaterun.os, "kill", side_effect=PermissionError):
            self.assertTrue(delegaterun.process_is_alive(1))

    def test_any_other_os_error_errs_towards_alive(self):
        with patch.object(delegaterun.os, "kill", side_effect=OSError("EINVAL")):
            self.assertTrue(delegaterun.process_is_alive(1))


class LostUpdateTest(unittest.TestCase):
    """The parent must never overwrite a terminal record written by its own child."""

    def test_a_child_that_finishes_during_the_spawn_keeps_its_result(self):
        with tempfile.TemporaryDirectory() as root:

            def finishing_popen(argv, **kwargs):
                # The child races to completion between Popen returning and the
                # parent's next write — the window that lost the result.
                delegaterun.finish_detached(root, "r1", {"ok": True, "text": "fast"})
                return _FakeChild(4242)

            delegaterun.start_detached([], root=root, run_id="r1", _popen=finishing_popen)
            record = delegaterun.load_state(root, "r1")
            self.assertEqual(record["status"], "done")
            self.assertEqual(record["result"]["text"], "fast")

    def test_the_record_exists_before_the_child_is_spawned(self):
        with tempfile.TemporaryDirectory() as root:
            seen = {}

            def checking_popen(argv, **kwargs):
                seen["record"] = delegaterun.load_state(root, "r1")
                return _FakeChild(4242)

            delegaterun.start_detached([], root=root, run_id="r1", _popen=checking_popen)
            self.assertIsNotNone(seen["record"])
            self.assertEqual(seen["record"]["status"], "running")

    def test_the_parent_never_writes_the_record_after_spawning(self):
        """The reason the pid is a separate file rather than a guarded merge.

        Any read-check-write from the parent is a race: the child's terminal record can
        land between the read and the write, and the parent puts `running` back over the
        result the caller is waiting for. Here the parent's post-spawn write goes to a
        different file, so there is no window to lose.
        """
        with tempfile.TemporaryDirectory() as root:
            writes = []
            real = delegaterun.write_state

            def spy(target_root, record):
                writes.append(record["status"])
                return real(target_root, record)

            def finishing_popen(argv, **kwargs):
                delegaterun.finish_detached(root, "r1", {"ok": True, "text": "fast"})
                return _FakeChild(4242)

            with patch.object(delegaterun, "write_state", spy):
                delegaterun.start_detached([], root=root, run_id="r1", _popen=finishing_popen)
            # exactly one record write from the parent, and it happened before the spawn
            self.assertEqual(writes, ["running", "done"])
            record = delegaterun.run_record(root, "r1")
            self.assertEqual(record["status"], "done")
            self.assertEqual(record["result"]["text"], "fast")
            self.assertEqual(record["pid"], 4242)

    def test_an_unreadable_pid_file_reads_as_no_pid_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as root:
            delegaterun.state_dir(root).mkdir(parents=True)
            delegaterun.pid_path(root, "r1").write_text("not a number", encoding="utf-8")
            self.assertIsNone(delegaterun.read_pid(root, "r1"))
            self.assertIsNone(delegaterun.read_pid(root, "absent"))
            self.assertIsNone(delegaterun.read_pid(root, "../escape"))

    def test_a_pid_that_cannot_be_written_does_not_fail_the_spawn(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(delegaterun.workspace, "write_text_atomic", side_effect=OSError):
                delegaterun.write_pid(root, "r1", 7)
            self.assertIsNone(delegaterun.read_pid(root, "r1"))

    def test_run_record_on_a_missing_run_is_none(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(delegaterun.run_record(root, "absent"))


class RunIdReuseTest(unittest.TestCase):
    """A reused `--run-id` must inherit nothing from the run before it.

    Naming a run after the issue it implements is the obvious thing an orchestrator
    does, so the retry reuses the id. The previous run's `.pid` file then pairs the new
    record with a dead pid, and `keel delegate status` — which reaps — marks a run that
    started milliseconds ago as crashed and lost.
    """

    def test_a_reused_run_id_does_not_inherit_the_previous_runs_pid(self):
        with tempfile.TemporaryDirectory() as root:
            delegaterun.start_detached([], root=root, run_id="issue-1012", _popen=_FakePopen(11))
            delegaterun.wait(root, "issue-1012", _sleep=lambda _s: None, _alive=lambda _p: False)
            self.assertEqual(delegaterun.run_record(root, "issue-1012")["status"], "crashed")

            record = delegaterun.start_detached(
                [], root=root, run_id="issue-1012", _popen=_FakePopen(22)
            )
            self.assertEqual(record["status"], "running")
            self.assertEqual(delegaterun.read_pid(root, "issue-1012"), 22)
            self.assertIsNone(delegaterun.read_crash(root, "issue-1012"))
            self.assertEqual(delegaterun.run_record(root, "issue-1012")["status"], "running")

    def test_a_reaper_racing_the_respawn_cannot_kill_the_new_run(self):
        """The window the fix closes: the stale pid is gone before the record exists.

        `clear_sidecars` runs between `write_state` and the spawn, so a reaper that lands
        anywhere in the setup sees either the old record with the old pid, or the new
        record with no pid at all — never the new record paired with the dead old one.
        """
        with tempfile.TemporaryDirectory() as root:
            delegaterun.start_detached([], root=root, run_id="reused", _popen=_FakePopen(11))
            seen = {}

            class _ReapingPopen(_FakePopen):
                def __call__(self, argv, **kwargs):
                    seen["reaped"] = delegaterun.reap_abandoned(root, _alive=lambda _p: False)
                    return super().__call__(argv, **kwargs)

            delegaterun.start_detached([], root=root, run_id="reused", _popen=_ReapingPopen(22))
            self.assertEqual(seen["reaped"], [])
            self.assertEqual(delegaterun.run_record(root, "reused")["status"], "running")

    def test_a_stale_crash_marker_alone_would_have_been_enough(self):
        with tempfile.TemporaryDirectory() as root:
            delegaterun.state_dir(root).mkdir(parents=True)
            delegaterun.crashed_path(root, "reused").write_text('{"reason": "old"}', "utf-8")
            delegaterun.start_detached([], root=root, run_id="reused", _popen=_FakePopen())
            self.assertEqual(delegaterun.run_record(root, "reused")["status"], "running")

    def test_clearing_sidecars_survives_an_unlink_that_fails(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(delegaterun.Path, "unlink", side_effect=PermissionError):
                delegaterun.clear_sidecars(root, "r1")


class CrashMarkerTest(unittest.TestCase):
    def test_a_child_that_answered_wins_over_a_crash_marker(self):
        """A marker only says "this looked abandoned"; a `done` record says the delegate
        answered. Composing rather than overwriting is what makes the stronger claim win
        even when the marker was written later."""
        with tempfile.TemporaryDirectory() as root:
            delegaterun.start_detached([], root=root, run_id="r1", _popen=_FakePopen())
            delegaterun.finish_detached(root, "r1", {"ok": True, "text": "answered"})
            delegaterun._mark_crashed(root, "r1", "looked gone", clock=_utc)
            record = delegaterun.run_record(root, "r1")
            self.assertEqual(record["status"], "done")
            self.assertEqual(record["result"]["text"], "answered")

    def test_an_unreadable_crash_marker_reads_as_no_marker(self):
        with tempfile.TemporaryDirectory() as root:
            delegaterun.state_dir(root).mkdir(parents=True)
            delegaterun.crashed_path(root, "r1").write_text("{oops", encoding="utf-8")
            self.assertIsNone(delegaterun.read_crash(root, "r1"))
            delegaterun.crashed_path(root, "r1").write_text('["not a dict"]', encoding="utf-8")
            self.assertIsNone(delegaterun.read_crash(root, "r1"))
            self.assertIsNone(delegaterun.read_crash(root, "../escape"))
            self.assertIsNone(delegaterun.read_crash(root, "absent"))

    def test_a_marker_that_cannot_be_written_still_returns_the_record(self):
        with tempfile.TemporaryDirectory() as root:
            delegaterun.start_detached([], root=root, run_id="r1", _popen=_FakePopen())
            with patch.object(delegaterun.workspace, "write_text_atomic", side_effect=OSError):
                record = delegaterun._mark_crashed(root, "r1", "gone", clock=_utc)
            self.assertEqual(record["status"], "running")

    def test_a_marker_without_a_reason_still_composes_a_contract(self):
        with tempfile.TemporaryDirectory() as root:
            delegaterun.start_detached([], root=root, run_id="r1", _popen=_FakePopen())
            delegaterun.crashed_path(root, "r1").write_text("{}", encoding="utf-8")
            record = delegaterun.run_record(root, "r1")
            self.assertEqual(record["status"], "crashed")
            self.assertEqual(record["result"]["error_code"], "lost")
            self.assertIn("no result", record["result"]["error"])


class ReapTest(unittest.TestCase):
    def _start(self, root, run_id):
        return delegaterun.start_detached([], root=root, run_id=run_id, _popen=_FakePopen())

    def test_reaping_marks_every_run_that_can_no_longer_finish(self):
        with tempfile.TemporaryDirectory() as root:
            self._start(root, "gone")
            self._start(root, "alsogone")
            delegaterun.finish_detached(root, "finished", {"ok": True})
            reaped = delegaterun.reap_abandoned(root, _alive=lambda _pid: False)
            self.assertEqual(sorted(reaped), ["alsogone", "gone"])
            statuses = {r["run_id"]: r["status"] for r in delegaterun.list_runs(root)}
            self.assertEqual(
                statuses, {"alsogone": "crashed", "finished": "done", "gone": "crashed"}
            )

    def test_reaping_leaves_a_live_run_alone(self):
        with tempfile.TemporaryDirectory() as root:
            self._start(root, "alive")
            self.assertEqual(delegaterun.reap_abandoned(root, _alive=_ALIVE), [])
            self.assertEqual(delegaterun.run_record(root, "alive")["status"], "running")

    def test_reaping_an_empty_root_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(delegaterun.reap_abandoned(root, _alive=_ALIVE), [])


class DocumentShapeTest(unittest.TestCase):
    def test_a_planning_failure_carries_the_same_keys_as_a_real_result(self):
        run = _Recorder(CommandResult(True, 0, "ok", stdout="ok"))
        real = delegaterun.execute(_plan("claude"), _run=run, _read=_read)
        failure = delegaterun.planning_failure(
            "nope", "review", code="unknown-provider", message="unknown provider 'nope'"
        )
        self.assertEqual(set(real), set(failure))
        self.assertFalse(failure["ok"])
        self.assertEqual(failure["error_code"], "unknown-provider")

    def test_rate_limited_reads_the_vendors_own_prose(self):
        self.assertTrue(delegaterun.rate_limited("Error: RESOURCE_EXHAUSTED"))
        self.assertTrue(delegaterun.rate_limited("usage limit reached"))
        self.assertFalse(delegaterun.rate_limited("syntax error"))
        self.assertFalse(delegaterun.rate_limited(""))

    def test_the_state_directory_lives_under_the_gitignored_keel_state_tree(self):
        self.assertEqual(delegaterun.state_dir("/repo").as_posix(), "/repo/.keel/state/delegate")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
