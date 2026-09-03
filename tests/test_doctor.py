"""Unit tests for the pure ``keel doctor`` diagnostics and its CLI handler."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from keel import __version__, api_delegate, cli, doctor, install

PROJECTS = Path(__file__).resolve().parent.parent / "projects"
SAMPLE_PROJECT = PROJECTS / "example-android.yaml"


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _doctor(**overrides):
    base = {
        "installed_version": "1.2.3",
        "latest_version": "1.2.3",
        "adapter_markers": [],
        "orphans": [],
        "core_version": None,
        "state_paths": [],
    }
    base.update(overrides)
    return doctor.run_doctor(**base)


def _check(report, name):
    return next(c for c in report["checks"] if c["name"] == name)


class TestConstraintSatisfied(unittest.TestCase):
    def test_caret_same_major(self):
        self.assertTrue(doctor.constraint_satisfied("1.2.3", "^1.0"))
        self.assertTrue(doctor.constraint_satisfied("1.0.0", "^1.0"))

    def test_caret_below_pinned_minimum(self):
        self.assertFalse(doctor.constraint_satisfied("1.0.0", "^1.2"))

    def test_caret_different_major_fails(self):
        self.assertFalse(doctor.constraint_satisfied("2.0.0", "^1.0"))
        self.assertFalse(doctor.constraint_satisfied("0.9.0", "^1.0"))

    def test_caret_leading_zero_pins_minor(self):
        # ^0.2 pins the minor (first non-zero component).
        self.assertTrue(doctor.constraint_satisfied("0.2.5", "^0.2"))
        self.assertFalse(doctor.constraint_satisfied("0.3.0", "^0.2"))
        self.assertFalse(doctor.constraint_satisfied("0.1.0", "^0.2"))

    def test_caret_all_zero_constraint(self):
        # ^0.0 has no non-zero component: pins the last component, exact-ish lead.
        self.assertTrue(doctor.constraint_satisfied("0.0.1", "^0.0"))

    def test_tilde_pins_major_minor(self):
        self.assertTrue(doctor.constraint_satisfied("1.2.9", "~1.2"))
        self.assertFalse(doctor.constraint_satisfied("1.3.0", "~1.2"))
        self.assertFalse(doctor.constraint_satisfied("1.2.0", "~1.2.5"))

    def test_tilde_major_only(self):
        self.assertTrue(doctor.constraint_satisfied("1.5.0", "~1"))
        self.assertFalse(doctor.constraint_satisfied("2.0.0", "~1"))

    def test_comparison_operators(self):
        self.assertTrue(doctor.constraint_satisfied("1.2.3", ">=1.0"))
        self.assertFalse(doctor.constraint_satisfied("0.9.0", ">=1.0"))
        self.assertTrue(doctor.constraint_satisfied("1.2.3", ">1.0"))
        self.assertFalse(doctor.constraint_satisfied("1.0.0", ">1.0"))
        self.assertTrue(doctor.constraint_satisfied("1.0.0", "<=1.0"))
        self.assertFalse(doctor.constraint_satisfied("1.2.0", "<=1.0"))
        self.assertTrue(doctor.constraint_satisfied("0.9.0", "<1.0"))
        self.assertFalse(doctor.constraint_satisfied("1.0.0", "<1.0"))

    def test_exact_match(self):
        self.assertTrue(doctor.constraint_satisfied("1.2.3", "==1.2.3"))
        self.assertTrue(doctor.constraint_satisfied("1.2.3", "1.2.3"))
        self.assertTrue(doctor.constraint_satisfied("1.2.3", "=1.2.3"))
        self.assertFalse(doctor.constraint_satisfied("1.2.3", "1.2.4"))

    def test_unparseable_returns_none(self):
        self.assertIsNone(doctor.constraint_satisfied("nope", "^1.0"))
        self.assertIsNone(doctor.constraint_satisfied("1.2.3", "garbage"))
        self.assertIsNone(doctor.constraint_satisfied("1.2.3", 123))


class TestCliVersionCheck(unittest.TestCase):
    def test_up_to_date_is_ok(self):
        report = _doctor(installed_version="1.2.3", latest_version="1.2.3")
        self.assertEqual(_check(report, "cli_version")["status"], "ok")

    def test_behind_latest_is_fail(self):
        report = _doctor(installed_version="0.9.0", latest_version="1.2.2")
        check = _check(report, "cli_version")
        self.assertEqual(check["status"], "fail")
        self.assertIn("behind", check["summary"])

    def test_ahead_of_latest_is_warn(self):
        report = _doctor(installed_version="1.3.0", latest_version="1.2.3")
        check = _check(report, "cli_version")
        self.assertEqual(check["status"], "warn")
        self.assertIn("ahead", check["summary"])

    def test_offline_latest_unknown_is_warn(self):
        report = _doctor(latest_version=None)
        check = _check(report, "cli_version")
        self.assertEqual(check["status"], "warn")
        self.assertEqual(check["detail"]["latest"], "unknown")

    def test_unparseable_versions_warn(self):
        report = _doctor(installed_version="dev", latest_version="1.2.3")
        self.assertEqual(_check(report, "cli_version")["status"], "warn")
        report = _doctor(installed_version="1.2.3", latest_version="weird")
        self.assertEqual(_check(report, "cli_version")["status"], "warn")


class TestAdapterVersionCheck(unittest.TestCase):
    def test_no_markers_is_warn(self):
        report = _doctor(adapter_markers=[])
        self.assertEqual(_check(report, "adapter_version")["status"], "warn")

    def test_all_matching_is_ok(self):
        markers = [
            {"surface": "claude", "name": "ship.md", "keel_version": "1.2.3"},
            {"surface": "skills", "name": "keel-ship/SKILL.md", "keel_version": "1.2.3"},
        ]
        report = _doctor(installed_version="1.2.3", adapter_markers=markers)
        self.assertEqual(_check(report, "adapter_version")["status"], "ok")

    def test_drift_is_warn(self):
        markers = [
            {"surface": "claude", "name": "ship.md", "keel_version": "1.2.3"},
            {"surface": "claude", "name": "regression.md", "keel_version": "1.1.0"},
        ]
        report = _doctor(installed_version="1.2.3", adapter_markers=markers)
        check = _check(report, "adapter_version")
        self.assertEqual(check["status"], "warn")
        self.assertEqual(len(check["detail"]["drift"]), 1)
        self.assertEqual(check["detail"]["drift"][0]["name"], "regression.md")


class TestOrphanCheck(unittest.TestCase):
    def test_no_orphans_is_ok(self):
        self.assertEqual(_check(_doctor(orphans=[]), "orphan_adapters")["status"], "ok")

    def test_orphans_is_warn(self):
        orphans = [{"surface": "claude", "name": "ship-v2.md", "command": "ship-v2"}]
        check = _check(_doctor(orphans=orphans), "orphan_adapters")
        self.assertEqual(check["status"], "warn")
        self.assertEqual(check["detail"]["orphans"], orphans)


class TestCoreVersionCheck(unittest.TestCase):
    def test_no_config_skips(self):
        check = _check(_doctor(core_version=None), "core_version")
        self.assertEqual(check["status"], "ok")
        self.assertIn("skipped", check["summary"])

    def test_satisfied_is_ok(self):
        report = _doctor(installed_version="1.2.3", core_version="^1.0")
        self.assertEqual(_check(report, "core_version")["status"], "ok")

    def test_mismatch_is_fail(self):
        report = _doctor(installed_version="2.0.0", core_version="^1.0")
        check = _check(report, "core_version")
        self.assertEqual(check["status"], "fail")
        self.assertIn("does not satisfy", check["summary"])

    def test_unparseable_constraint_is_warn(self):
        report = _doctor(installed_version="1.2.3", core_version="garbage")
        self.assertEqual(_check(report, "core_version")["status"], "warn")


class TestStatePathsCheck(unittest.TestCase):
    def test_no_paths_is_ok(self):
        self.assertEqual(_check(_doctor(state_paths=[]), "state_paths")["status"], "ok")

    def test_present_and_missing_is_ok(self):
        paths = [
            {"label": "ledger", "path": "a.jsonl", "status": "present"},
            {"label": "checkpoint", "path": "b.json", "status": "missing"},
        ]
        check = _check(_doctor(state_paths=paths), "state_paths")
        self.assertEqual(check["status"], "ok")
        self.assertEqual(check["detail"]["present"], 1)

    def test_invalid_path_is_warn(self):
        paths = [
            {"label": "ledger", "path": "ok.jsonl", "status": "present"},
            {"label": "checkpoint", "path": None, "status": "invalid"},
        ]
        check = _check(_doctor(state_paths=paths), "state_paths")
        self.assertEqual(check["status"], "warn")
        self.assertEqual(check["detail"]["present"], 1)


class TestRollupAndRender(unittest.TestCase):
    def test_status_is_worst_check(self):
        report = _doctor(installed_version="0.9.0", latest_version="1.2.2")
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["counts"]["fail"], 1)

    def test_warn_rollup(self):
        report = _doctor(latest_version=None)
        self.assertEqual(report["status"], "warn")

    def test_all_ok_rollup(self):
        markers = [{"surface": "claude", "name": "ship.md", "keel_version": "1.2.3"}]
        report = _doctor(
            installed_version="1.2.3",
            latest_version="1.2.3",
            adapter_markers=markers,
            core_version="^1.0",
        )
        self.assertEqual(report["status"], "ok")

    def test_render_report_lines(self):
        report = _doctor(latest_version="1.2.3")
        text = doctor.render_report(report)
        self.assertIn("keel doctor", text)
        self.assertIn("cli_version", text)
        self.assertIn("summary", text)

    def test_schema_version_present(self):
        self.assertEqual(_doctor()["schema_version"], "keel.doctor.v1")


class TestFetchLatestPypi(unittest.TestCase):
    class _FakeResponse:
        def __init__(self, body):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size=-1):
            return self._body if size < 0 else self._body[:size]

    class _FakeOpener:
        """Stands in for the real opener; records what it was asked to fetch."""

        def __init__(self, response):
            self._response = response
            self.opened = []

        def open(self, url, timeout=None):
            self.opened.append((url, timeout))
            return self._response

    def test_parses_version(self):
        payload = json.dumps({"info": {"version": "1.4.0"}}).encode("utf-8")
        result = cli._fetch_latest_pypi_version(
            _opener=self._FakeOpener(self._FakeResponse(payload))
        )
        self.assertEqual(result, "1.4.0")

    def test_non_string_version_is_none(self):
        payload = json.dumps({"info": {"version": 14}}).encode("utf-8")
        result = cli._fetch_latest_pypi_version(
            _opener=self._FakeOpener(self._FakeResponse(payload))
        )
        self.assertIsNone(result)

    def test_network_error_is_none(self):
        class _Boom:
            def open(self, url, timeout=None):
                raise OSError("offline")

        self.assertIsNone(cli._fetch_latest_pypi_version(_opener=_Boom()))

    def test_malformed_json_is_none(self):
        result = cli._fetch_latest_pypi_version(
            _opener=self._FakeOpener(self._FakeResponse(b"not json"))
        )
        self.assertIsNone(result)

    def test_invalid_scheme_returns_none(self):
        result = cli._fetch_latest_pypi_version(
            url="file:///etc/passwd", _opener=self._FakeOpener(self._FakeResponse(b""))
        )
        self.assertIsNone(result)


class TheVersionCheckDoesNotFollowRedirects(unittest.TestCase):
    """The guard #811 shipped without, and #810 silently removed for six days.

    #811 replaced plain ``urlopen`` with a non-redirecting opener and added no
    test. A stale-base squash restored ``urlopen`` seventeen minutes later and CI
    stayed green for six days, because nothing anywhere asserted the handler set
    (#934). Both halves are pinned here: that the opener refuses redirects, and
    that the version check is the thing using it.
    """

    def test_the_shared_opener_registers_no_redirect_handler(self):
        opener = api_delegate.build_http_only_opener()

        classes = [type(h) for h in opener.handlers]
        self.assertNotIn(urllib.request.HTTPRedirectHandler, classes)
        # Not vacuous: the opener must still be able to make the request at all.
        # `issubclass`, not membership: #969 wrapped the handlers so the
        # connection can check where the host resolved, and the property here is
        # "an HTTPS handler is present", not "this exact class is".
        self.assertTrue(
            [c for c in classes if issubclass(c, urllib.request.HTTPSHandler)],
            "the opener the PyPI check uses cannot make an HTTPS request",
        )
        # A redirect handler cannot arrive by subclass either.
        self.assertFalse(
            [c for c in classes if issubclass(c, urllib.request.HTTPRedirectHandler)],
            "a redirect handler reached the opener the PyPI check uses",
        )

    def test_the_version_check_fetches_through_that_opener(self):
        # Behavioural, not structural: the default path must *call* the shared
        # builder. Reverting to `urlopen` leaves this patch unused and fails here,
        # which is precisely what did not happen in #934.
        payload = json.dumps({"info": {"version": "9.9.9"}}).encode("utf-8")
        fake = TestFetchLatestPypi._FakeOpener(TestFetchLatestPypi._FakeResponse(payload))

        with patch.object(api_delegate, "build_http_only_opener", return_value=fake) as built:
            result = cli._fetch_latest_pypi_version()

        self.assertEqual(result, "9.9.9")
        built.assert_called_once_with()
        self.assertEqual([url for url, _ in fake.opened], [cli._PYPI_LATEST_URL])

    def test_only_one_opener_is_hand_rolled_in_the_package(self):
        """Two openers with different handler sets is how the next drift happens.

        Counted over the package source: exactly one place may assemble an
        ``OpenerDirector``, so a second hand-rolled one has to displace this
        assertion rather than quietly coexist with it.
        """
        package = Path(cli.__file__).resolve().parent
        builders = sorted(
            path.relative_to(package).as_posix()
            for path in package.rglob("*.py")
            if "urllib.request.OpenerDirector()" in path.read_text(encoding="utf-8")
        )

        self.assertEqual(["api_delegate.py"], builders)


class TestScanAdapterMarkers(unittest.TestCase):
    def test_reads_keel_version_markers(self):
        with tempfile.TemporaryDirectory() as d:
            install.install_all(d)
            markers = install.scan_adapter_markers(d)
            self.assertTrue(markers)
            self.assertTrue(all(m["keel_version"] == __version__ for m in markers))

    def test_skips_marker_less_files(self):
        with tempfile.TemporaryDirectory() as d:
            cmd_dir = Path(d) / ".claude/commands/keel"
            cmd_dir.mkdir(parents=True)
            (cmd_dir / "hand.md").write_text("hand-written, no marker\n", encoding="utf-8")
            self.assertEqual(install.scan_adapter_markers(d), [])

    def test_empty_root_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(install.scan_adapter_markers(d), [])

    def test_directory_matching_pattern_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            cmd_dir = Path(d) / ".claude/commands/keel"
            cmd_dir.mkdir(parents=True)
            # a *directory* whose name matches the *.md glob must be ignored.
            (cmd_dir / "weird.md").mkdir()
            self.assertEqual(install.scan_adapter_markers(d), [])


class TestDoctorCli(unittest.TestCase):
    def setUp(self):
        self._real_fetch = cli._fetch_latest_pypi_version
        cli._fetch_latest_pypi_version = lambda **kw: "1.2.3"

    def tearDown(self):
        cli._fetch_latest_pypi_version = self._real_fetch

    def test_offline_json_shape(self):
        with tempfile.TemporaryDirectory() as d:
            install.install_all(d)
            rc, out, _ = run(["doctor", "--root", d, "--offline", "--json"])
        self.assertEqual(rc, 0)
        report = json.loads(out)
        self.assertEqual(report["schema_version"], "keel.doctor.v1")
        names = {c["name"] for c in report["checks"]}
        self.assertEqual(
            names,
            {
                "checkout_binding",
                "cli_version",
                "adapter_version",
                "orphan_adapters",
                "core_version",
                "state_paths",
                "python_toolchain",
            },
        )
        # --offline => latest unknown.
        cli_check = next(c for c in report["checks"] if c["name"] == "cli_version")
        self.assertEqual(cli_check["detail"]["latest"], "unknown")

    def test_human_output(self):
        with tempfile.TemporaryDirectory() as d:
            install.install_all(d)
            rc, out, _ = run(["doctor", "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("keel doctor", out)
        self.assertIn("cli_version", out)

    def test_online_fetch_used_when_not_offline(self):
        with tempfile.TemporaryDirectory() as d:
            install.install_all(d)
            rc, out, _ = run(["doctor", "--root", d, "--json"])
        report = json.loads(out)
        cli_check = next(c for c in report["checks"] if c["name"] == "cli_version")
        self.assertEqual(cli_check["detail"]["latest"], "1.2.3")
        self.assertEqual(rc, 0)

    def test_with_project_runs_core_version_and_state(self):
        with tempfile.TemporaryDirectory() as d:
            install.install_all(d)
            rc, out, _ = run(["doctor", str(SAMPLE_PROJECT), "--root", d, "--offline", "--json"])
        report = json.loads(out)
        core = next(c for c in report["checks"] if c["name"] == "core_version")
        self.assertEqual(core["status"], "ok")  # installed 1.2.3 satisfies ^1.0
        state = next(c for c in report["checks"] if c["name"] == "state_paths")
        self.assertEqual(len(state["detail"]["paths"]), 2)
        self.assertEqual(rc, 0)

    def test_missing_config_errors(self):
        rc, _, err = run(["doctor", "/no/such/project.yaml", "--offline"])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config_errors(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.yaml"
            bad.write_text("extends: keel\n", encoding="utf-8")  # missing required keys
            rc, _, err = run(["doctor", str(bad), "--offline"])
        self.assertEqual(rc, 1)
        self.assertTrue(err.strip())

    def test_strict_exits_nonzero_on_fail(self):
        # core_version ^1.0 cannot be satisfied by a 0.x installed version.
        self._real_setup()
        with tempfile.TemporaryDirectory() as d:
            install.install_all(d)
            rc, out, _ = run(
                ["doctor", str(SAMPLE_PROJECT), "--root", d, "--offline", "--strict", "--json"]
            )
        report = json.loads(out)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(rc, 1)

    def test_strict_fail_advisory_without_strict(self):
        self._real_setup()
        with tempfile.TemporaryDirectory() as d:
            install.install_all(d)
            rc, out, _ = run(["doctor", str(SAMPLE_PROJECT), "--root", d, "--offline", "--json"])
        report = json.loads(out)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(rc, 0)  # advisory by default

    def _real_setup(self):
        # Force the installed version to a 0.x so core_version ^1.0 fails.
        import keel.doctor as dmod

        orig = dmod.run_doctor

        def patched(**kwargs):
            kwargs["installed_version"] = "0.9.0"
            return orig(**kwargs)

        dmod.run_doctor = patched
        self.addCleanup(lambda: setattr(dmod, "run_doctor", orig))


class TestDoctorStatePaths(unittest.TestCase):
    def test_invalid_path_reported(self):
        from keel import config as cfg

        config = cfg.load_config(str(SAMPLE_PROJECT))
        # Patch the resolver to raise, exercising the invalid branch.
        from keel import ledger

        orig = ledger.resolve_path

        def boom(root, conf):
            raise ledger.LedgerError("escapes root")

        ledger.resolve_path = boom
        try:
            entries = cli._doctor_state_paths(".", config)
        finally:
            ledger.resolve_path = orig
        ledger_entry = next(e for e in entries if e["label"] == "ledger")
        self.assertEqual(ledger_entry["status"], "invalid")


class TestCheckoutBinding(unittest.TestCase):
    """The importable ``keel`` vs the checkout the command is pointed at."""

    def test_skipped_when_root_is_not_a_checkout(self):
        # Nothing to compare against, so this must not manufacture a warning.
        check = _check(
            _doctor(module_path="/anywhere/src/keel", checkout_root=None), "checkout_binding"
        )
        self.assertEqual(check["status"], "ok")
        self.assertIn("not run against a keel checkout", check["summary"])
        self.assertIsNone(check["detail"]["checkout_root"])

    def test_defaults_skip_the_check(self):
        # Callers predating the check omit both paths and keep their behaviour.
        self.assertEqual(_check(_doctor(), "checkout_binding")["status"], "ok")

    def test_unlocatable_module_warns(self):
        check = _check(_doctor(module_path=None, checkout_root="/repo"), "checkout_binding")
        self.assertEqual(check["status"], "warn")
        self.assertIn("could not be located", check["summary"])

    def test_module_inside_the_checkout_is_ok(self):
        check = _check(
            _doctor(module_path="/repo/src/keel", checkout_root="/repo"), "checkout_binding"
        )
        self.assertEqual(check["status"], "ok")
        self.assertIn("inside this checkout", check["summary"])

    def test_module_outside_the_checkout_warns_and_names_both_paths(self):
        check = _check(
            _doctor(module_path="/elsewhere/src/keel", checkout_root="/repo"),
            "checkout_binding",
        )
        self.assertEqual(check["status"], "warn")
        self.assertIn("/elsewhere/src/keel", check["summary"])
        self.assertIn("pip install -e .", check["summary"])
        self.assertEqual(check["detail"]["checkout_root"], "/repo")

    def test_sibling_prefix_is_not_treated_as_nested(self):
        # "/repo-two" starts with "/repo" as a string but is a different tree;
        # comparing path *parts* rather than characters is what catches this.
        check = _check(
            _doctor(module_path="/repo-two/src/keel", checkout_root="/repo"), "checkout_binding"
        )
        self.assertEqual(check["status"], "warn")

    def test_mismatch_rolls_up_into_the_report_status(self):
        report = _doctor(module_path="/elsewhere/src/keel", checkout_root="/repo")
        self.assertEqual(report["status"], "warn")

    def test_mismatch_never_escalates_to_fail(self):
        # Running against a deliberately installed keel is legitimate; a warn
        # must not change anyone's exit code.
        report = _doctor(module_path="/elsewhere/src/keel", checkout_root="/repo")
        self.assertEqual(report["counts"]["fail"], 0)


class TestDoctorCheckoutRoot(unittest.TestCase):
    """Thin I/O: does ``root`` even look like a keel source checkout?"""

    def test_returns_resolved_root_for_a_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            pkg = Path(d) / "src" / "keel"
            pkg.mkdir(parents=True)
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            self.assertEqual(cli._doctor_checkout_root(d), str(Path(d).resolve()))

    def test_returns_none_for_a_plain_directory(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(cli._doctor_checkout_root(d))

    def test_returns_none_when_the_package_marker_is_a_directory(self):
        # src/keel/__init__.py present but not a file => not a usable checkout.
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "src" / "keel" / "__init__.py").mkdir(parents=True)
            self.assertIsNone(cli._doctor_checkout_root(d))


class TestPythonToolchainCheck(unittest.TestCase):
    """The pure classifier: is the build gate's interpreter one keel can run on?"""

    def _run_check(self, **toolchain):
        base = {
            "interpreter": "/opt/py312/bin/python",
            "source": "scripts/find_python.sh",
            "version": "3.12.4",
            "yaml": True,
            "reason": "",
        }
        base.update(toolchain)
        return _check(_doctor(python_toolchain=base), "python_toolchain")

    def test_not_probed_is_ok(self):
        check = _check(_doctor(), "python_toolchain")
        self.assertEqual(check["status"], "ok")
        self.assertIn("not probed", check["summary"])
        self.assertEqual(check["detail"], {})

    def test_supported_interpreter_with_pyyaml_is_ok(self):
        check = self._run_check()
        self.assertEqual(check["status"], "ok")
        self.assertIn("/opt/py312/bin/python", check["summary"])
        self.assertIn("3.12.4", check["summary"])

    def test_detail_carries_the_gathered_facts(self):
        check = self._run_check(source="PY (environment)")
        self.assertEqual(check["detail"]["source"], "PY (environment)")
        self.assertEqual(check["detail"]["interpreter"], "/opt/py312/bin/python")
        self.assertTrue(check["detail"]["yaml"])

    def test_no_interpreter_warns_with_the_reason(self):
        check = self._run_check(interpreter=None, reason="scripts/find_python.sh resolved none")
        self.assertEqual(check["status"], "warn")
        self.assertIn("no usable interpreter", check["summary"])
        self.assertIn("resolved none", check["summary"])

    def test_no_interpreter_and_no_reason_still_says_something(self):
        check = self._run_check(interpreter=None, reason="")
        self.assertEqual(check["status"], "warn")
        self.assertIn("no interpreter resolved", check["summary"])

    def test_unreadable_version_warns(self):
        check = self._run_check(version=None, reason="probing /opt/py312/bin/python failed: boom")
        self.assertEqual(check["status"], "warn")
        self.assertIn("version is unknown", check["summary"])
        self.assertIn("boom", check["summary"])

    def test_non_string_version_warns(self):
        check = self._run_check(version=312)
        self.assertEqual(check["status"], "warn")
        self.assertIn("version is unknown", check["summary"])

    def test_unparseable_version_warns(self):
        check = self._run_check(version="3.13.0rc1")
        self.assertEqual(check["status"], "warn")
        self.assertIn("version is unknown", check["summary"])

    def test_below_the_minimum_warns_and_names_it(self):
        # The #1022 case: Xcode's python3 on macOS.
        check = self._run_check(interpreter="/usr/bin/python3", version="3.9.6")
        self.assertEqual(check["status"], "warn")
        self.assertIn("/usr/bin/python3", check["summary"])
        self.assertIn("below the required 3.11", check["summary"])

    def test_missing_pyyaml_warns(self):
        check = self._run_check(yaml=False)
        self.assertEqual(check["status"], "warn")
        self.assertIn("PyYAML is not importable", check["summary"])

    def test_old_interpreter_without_pyyaml_reports_both(self):
        check = self._run_check(version="3.9.6", yaml=False)
        self.assertIn("below the required 3.11", check["summary"])
        self.assertIn("PyYAML is not importable", check["summary"])

    def test_never_escalates_to_fail(self):
        # keel cannot know a red gate is *this* problem — advisory only.
        report = _doctor(
            python_toolchain={
                "interpreter": "/usr/bin/python3",
                "source": "python3 on PATH",
                "version": "3.9.6",
                "yaml": False,
                "reason": "",
            }
        )
        self.assertEqual(report["counts"]["fail"], 0)
        self.assertEqual(report["status"], "warn")

    def test_the_minimum_matches_requires_python(self):
        pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        minimum = ".".join(str(part) for part in doctor.MIN_PYTHON)
        self.assertIn(f'requires-python = ">={minimum}"', pyproject)


class _Proc:
    """Stand-in for what ``subprocess.run`` hands back to ``run_argv``."""

    def __init__(self, *, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _fake_run(*results):
    """A ``subprocess.run`` seam that replays ``results`` and records its calls."""
    calls = []

    def fake(argv, **kwargs):
        calls.append({"argv": list(argv), **kwargs})
        return results[len(calls) - 1]

    return fake, calls


def _ok(stdout):
    return _Proc(stdout=stdout)


def _failed(output, code=2):
    return _Proc(stderr=output, returncode=code)


def _config(build_gate_cmd):
    return SimpleNamespace(knobs=SimpleNamespace(build_gate_cmd=build_gate_cmd))


PROBE_OK = _ok('{"version": "3.12.4", "yaml": true}')


class TestDoctorPythonToolchain(unittest.TestCase):
    """Thin I/O: which interpreter will the configured build gate run on?"""

    def test_a_non_make_gate_reports_this_interpreter(self):
        # Nothing to resolve: the gate runs in this process's world.
        fake_run, calls = _fake_run()
        facts = cli._doctor_python_toolchain(
            ".", _config("./gradlew test"), _run=fake_run, _which=lambda _: None, _env={}
        )
        self.assertEqual(facts["interpreter"], sys.executable)
        self.assertEqual(facts["source"], "sys.executable")
        self.assertEqual(facts["version"], ".".join(str(p) for p in sys.version_info[:3]))
        self.assertTrue(facts["yaml"])
        self.assertEqual(calls, [])  # no subprocess for ourselves

    def test_no_config_reports_this_interpreter(self):
        fake_run, _ = _fake_run()
        facts = cli._doctor_python_toolchain(
            ".", None, _run=fake_run, _which=lambda _: None, _env={}
        )
        self.assertEqual(facts["interpreter"], sys.executable)

    def test_an_exported_py_wins_over_the_resolver(self):
        # `PY=` is what the Makefile honours first, so doctor must report it.
        fake_run, calls = _fake_run(PROBE_OK)
        with tempfile.TemporaryDirectory() as d:
            _write_resolver(d)
            facts = cli._doctor_python_toolchain(
                d,
                _config("make test"),
                _run=fake_run,
                _which=lambda _: None,
                _env={"PY": " /opt/py312/bin/python "},
            )
        self.assertEqual(facts["interpreter"], "/opt/py312/bin/python")
        self.assertEqual(facts["source"], "PY (environment)")
        self.assertEqual(facts["version"], "3.12.4")
        self.assertEqual(calls[0]["argv"][0], "/opt/py312/bin/python")

    def test_an_empty_py_is_not_an_override(self):
        fake_run, calls = _fake_run(_ok("/opt/py313/bin/python\n"), PROBE_OK)
        with tempfile.TemporaryDirectory() as d:
            _write_resolver(d)
            facts = cli._doctor_python_toolchain(
                d, _config("make test"), _run=fake_run, _which=lambda _: None, _env={"PY": "  "}
            )
        self.assertEqual(facts["source"], "scripts/find_python.sh")
        self.assertEqual(facts["interpreter"], "/opt/py313/bin/python")

    def test_a_make_gate_asks_the_resolver(self):
        fake_run, calls = _fake_run(_ok("/opt/py313/bin/python\n"), PROBE_OK)
        with tempfile.TemporaryDirectory() as d:
            resolver = _write_resolver(d)
            facts = cli._doctor_python_toolchain(
                d, _config("make test"), _run=fake_run, _which=lambda _: None, _env={}
            )
        self.assertEqual(calls[0]["argv"], ["/bin/sh", str(resolver)])
        self.assertEqual(facts["interpreter"], "/opt/py313/bin/python")
        self.assertEqual(facts["source"], "scripts/find_python.sh")

    def test_a_resolver_that_finds_nothing_warns_with_its_message(self):
        fake_run, _ = _fake_run(_failed("find_python: no Python >= 3.11 with PyYAML found"))
        with tempfile.TemporaryDirectory() as d:
            _write_resolver(d)
            facts = cli._doctor_python_toolchain(
                d, _config("make test"), _run=fake_run, _which=lambda _: None, _env={}
            )
        self.assertIsNone(facts["interpreter"])
        self.assertIn("no Python >= 3.11", facts["reason"])
        self.assertFalse(facts["yaml"])

    def test_a_silent_resolver_is_not_an_interpreter(self):
        fake_run, _ = _fake_run(_ok("   \n"))
        with tempfile.TemporaryDirectory() as d:
            _write_resolver(d)
            facts = cli._doctor_python_toolchain(
                d, _config("make test"), _run=fake_run, _which=lambda _: None, _env={}
            )
        self.assertIsNone(facts["interpreter"])

    def test_a_project_without_the_resolver_falls_back_to_python3(self):
        # Someone else's `make test` runs whatever their Makefile picks — `python3`.
        fake_run, calls = _fake_run(PROBE_OK)
        with tempfile.TemporaryDirectory() as d:
            facts = cli._doctor_python_toolchain(
                d,
                _config("make -C build test"),
                _run=fake_run,
                _which=lambda name: f"/usr/bin/{name}",
                _env={},
            )
        self.assertEqual(facts["interpreter"], "/usr/bin/python3")
        self.assertEqual(facts["source"], "python3 on PATH")
        self.assertEqual(calls[0]["argv"][0], "/usr/bin/python3")

    def test_no_python3_at_all_warns(self):
        fake_run, _ = _fake_run()
        with tempfile.TemporaryDirectory() as d:
            facts = cli._doctor_python_toolchain(
                d, _config("make test"), _run=fake_run, _which=lambda _: None, _env={}
            )
        self.assertIsNone(facts["interpreter"])
        self.assertIn("no python3 on PATH", facts["reason"])

    def test_an_interpreter_that_cannot_be_probed_reports_the_failure(self):
        fake_run, _ = _fake_run(_failed("bad interpreter: Permission denied", code=126))
        facts = cli._doctor_python_toolchain(
            ".",
            _config("make test"),
            _run=fake_run,
            _which=lambda _: None,
            _env={"PY": "/opt/broken/python"},
        )
        self.assertEqual(facts["interpreter"], "/opt/broken/python")
        self.assertIsNone(facts["version"])
        self.assertIn("Permission denied", facts["reason"])

    def test_unreadable_probe_output_is_not_a_crash(self):
        fake_run, _ = _fake_run(_ok("not json"))
        facts = cli._doctor_python_toolchain(
            ".",
            _config("make test"),
            _run=fake_run,
            _which=lambda _: None,
            _env={"PY": "/opt/py312/bin/python"},
        )
        self.assertIsNone(facts["version"])
        self.assertIn("failed", facts["reason"])

    def test_probe_output_missing_a_field_is_not_a_crash(self):
        fake_run, _ = _fake_run(_ok('{"version": "3.12.4"}'))
        facts = cli._doctor_python_toolchain(
            ".",
            _config("make test"),
            _run=fake_run,
            _which=lambda _: None,
            _env={"PY": "/opt/py312/bin/python"},
        )
        self.assertIsNone(facts["version"])

    def test_probe_output_of_the_wrong_shape_is_not_a_crash(self):
        fake_run, _ = _fake_run(_ok("[1, 2]"))
        facts = cli._doctor_python_toolchain(
            ".",
            _config("make test"),
            _run=fake_run,
            _which=lambda _: None,
            _env={"PY": "/opt/py312/bin/python"},
        )
        self.assertIsNone(facts["version"])

    def test_a_missing_pyyaml_is_reported_not_hidden(self):
        fake_run, _ = _fake_run(_ok('{"version": "3.12.4", "yaml": false}'))
        facts = cli._doctor_python_toolchain(
            ".",
            _config("make test"),
            _run=fake_run,
            _which=lambda _: None,
            _env={"PY": "/opt/py312/bin/python"},
        )
        self.assertFalse(facts["yaml"])

    def test_long_tool_output_is_trimmed_into_one_line(self):
        self.assertEqual(cli._short("a\n  b\tc  "), "a b c")
        self.assertEqual(len(cli._short("x " * 500)), 160)

    def test_the_cli_wires_the_check_up(self):
        # End to end through `keel doctor` with the real seams: the sample
        # project's gate is not `make`, so the answer is this interpreter and
        # nothing is spawned.
        with tempfile.TemporaryDirectory() as d:
            install.install_all(d)
            rc, out, _err = run(["doctor", str(SAMPLE_PROJECT), "--root", d, "--offline", "--json"])
        self.assertEqual(rc, 0)
        check = _check(json.loads(out), "python_toolchain")
        self.assertEqual(check["status"], "ok")
        self.assertEqual(check["detail"]["interpreter"], sys.executable)
        self.assertEqual(check["detail"]["source"], "sys.executable")


def _write_resolver(root):
    """Place a stand-in ``scripts/find_python.sh`` under ``root`` (never executed)."""
    resolver = Path(root) / "scripts" / "find_python.sh"
    resolver.parent.mkdir(parents=True, exist_ok=True)
    resolver.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    return resolver


class TestProvidersCheck(unittest.TestCase):
    """The `providers` check classifies an already-probed report (#1011)."""

    @staticmethod
    def _payload(**overrides):
        base = {
            "providers": [],
            "registry_path": "/home/op/.keel/providers.yaml",
            "registry_present": False,
            "warnings": [],
            "errors": [],
            "available": 2,
            "total": 7,
        }
        base.update(overrides)
        return base

    def test_absent_by_default_so_the_existing_checks_are_untouched(self):
        report = _doctor()
        self.assertNotIn("providers", {c["name"] for c in report["checks"]})
        self.assertNotIn("providers", report)

    def test_available_providers_are_ok_and_counted(self):
        check = _check(_doctor(providers=self._payload()), "providers")
        self.assertEqual(check["status"], "ok")
        self.assertEqual(check["summary"], "2 of 7 provider(s) available")
        self.assertEqual(check["detail"]["registry_present"], False)

    def test_a_name_clash_is_a_fail_that_names_both_sources(self):
        payload = self._payload(
            errors=[
                "~/.keel/providers.yaml: provider 'cursor' clashes with the project "
                "profile knobs.delegate_profiles.cursor; the project profile wins"
            ]
        )
        check = _check(_doctor(providers=payload), "providers")
        self.assertEqual(check["status"], "fail")
        self.assertIn("knobs.delegate_profiles.cursor", check["summary"])
        self.assertEqual(_doctor(providers=payload)["status"], "fail")

    def test_a_malformed_registry_is_a_warn_not_a_fail(self):
        payload = self._payload(warnings=["providers.yaml: unknown transport 'telepathy'"])
        check = _check(_doctor(providers=payload), "providers")
        self.assertEqual(check["status"], "warn")
        self.assertIn("telepathy", check["summary"])

    def test_a_machine_with_no_usable_delegate_warns(self):
        check = _check(_doctor(providers=self._payload(available=0)), "providers")
        self.assertEqual(check["status"], "warn")
        self.assertIn("no delegate is usable", check["summary"])

    def test_the_document_is_merged_at_the_top_level(self):
        rows = [{"name": "claude", "available": True}]
        report = _doctor(providers=self._payload(providers=rows, warnings=["w"], errors=[]))
        self.assertEqual(report["providers"], rows)
        self.assertEqual(report["registry_path"], "/home/op/.keel/providers.yaml")
        self.assertEqual(report["warnings"], ["w"])
        self.assertEqual(report["errors"], [])


class TestRenderProviders(unittest.TestCase):
    def _row(self, **overrides):
        row = {
            "name": "claude",
            "transport": "cli",
            "source": "builtin",
            "available": True,
            "reason": "/bin/claude (2.1.0)",
            "models": [],
            "capabilities": {"tools": True, "read_only_mode": True, "model_selection": True},
        }
        row.update(overrides)
        return row

    def test_table_names_transport_source_capabilities_and_reason(self):
        payload = {
            "providers": [
                self._row(),
                self._row(
                    name="ollama",
                    transport="local",
                    available=False,
                    reason="unreachable",
                    capabilities={
                        "tools": False,
                        "read_only_mode": False,
                        "model_selection": False,
                    },
                ),
            ],
            "registry_path": "/home/op/.keel/providers.yaml",
            "registry_present": True,
            "available": 1,
            "total": 2,
            "warnings": ["bad entry"],
            "errors": ["name clash"],
        }
        text = doctor.render_providers(payload)
        self.assertIn("keel providers — 1 of 2 available", text)
        self.assertIn("/home/op/.keel/providers.yaml (present)", text)
        self.assertIn("yes  claude", text)
        self.assertIn("tools,read-only,model", text)
        self.assertIn(" no  ollama", text)
        self.assertIn("warn  bad entry", text)
        self.assertIn("FAIL  name clash", text)

    def test_a_provider_with_no_capabilities_renders_a_dash(self):
        payload = {"providers": [self._row(capabilities={})], "available": 1, "total": 1}
        self.assertIn(" -  ", doctor.render_providers(payload))
        self.assertIn("(none) (not present)", doctor.render_providers(payload))

    def test_a_long_model_list_is_summarised(self):
        models = [f"m{i}" for i in range(9)]
        payload = {"providers": [self._row(name="agy", models=models)], "available": 1, "total": 1}
        text = doctor.render_providers(payload)
        self.assertIn("models: m0, m1, m2, m3, m4, m5, +3 more", text)

    def test_a_short_model_list_is_shown_whole(self):
        payload = {"providers": [self._row(models=["a", "b"])], "available": 1, "total": 1}
        self.assertIn("models: a, b\n", doctor.render_providers(payload) + "\n")
        self.assertNotIn("more", doctor.render_providers(payload))


class TestDoctorProvidersCli(unittest.TestCase):
    """`keel doctor --providers` wires the probe in without touching this machine."""

    def setUp(self):
        self._real_fetch = cli._fetch_latest_pypi_version
        cli._fetch_latest_pypi_version = lambda **kw: __version__

    def tearDown(self):
        cli._fetch_latest_pypi_version = self._real_fetch

    @staticmethod
    def _collect(**overrides):
        payload = {
            "schema_version": "keel.providers.v1",
            "providers": [
                {
                    "name": "claude",
                    "transport": "cli",
                    "source": "builtin",
                    "available": True,
                    "reason": "/bin/claude (2.1.0)",
                    "models": [],
                    "capabilities": {
                        "tools": True,
                        "read_only_mode": True,
                        "model_selection": True,
                    },
                },
                {
                    "name": "codex",
                    "transport": "cli",
                    "source": "builtin",
                    "available": False,
                    "reason": "codex not found on PATH",
                    "models": [],
                    "capabilities": {
                        "tools": True,
                        "read_only_mode": True,
                        "model_selection": True,
                    },
                },
            ],
            "registry_path": "/home/op/.keel/providers.yaml",
            "registry_present": False,
            "warnings": [],
            "errors": [],
            "available": 1,
            "total": 2,
        }
        payload.update(overrides)
        return payload

    def test_no_probe_runs_without_the_flag(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(cli.providerprobe, "collect") as collect:
                rc, out, _ = run(["doctor", "--root", d, "--offline", "--json"])
        collect.assert_not_called()
        self.assertEqual(rc, 0)
        self.assertNotIn("providers", json.loads(out))

    def test_json_lists_every_provider_with_a_reason(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(cli.providerprobe, "collect", return_value=self._collect()):
                rc, out, _ = run(["doctor", "--root", d, "--offline", "--providers", "--json"])
        report = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual([p["name"] for p in report["providers"]], ["claude", "codex"])
        self.assertTrue(report["providers"][0]["available"])
        self.assertEqual(report["providers"][1]["reason"], "codex not found on PATH")
        self.assertEqual(report["registry_path"], "/home/op/.keel/providers.yaml")
        self.assertEqual(report["warnings"], [])
        self.assertEqual(_check(report, "providers")["status"], "ok")

    def test_human_output_prints_the_table_under_the_checks(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(cli.providerprobe, "collect", return_value=self._collect()):
                rc, out, _ = run(["doctor", "--root", d, "--offline", "--providers"])
        self.assertEqual(rc, 0)
        self.assertIn("keel doctor", out)
        self.assertIn("keel providers — 1 of 2 available", out)
        self.assertIn("codex not found on PATH", out)

    def test_a_registry_name_clash_fails_under_strict(self):
        payload = self._collect(errors=["providers.yaml: provider 'codex' shadows the built-in"])
        with tempfile.TemporaryDirectory() as d:
            with patch.object(cli.providerprobe, "collect", return_value=payload):
                rc, out, _ = run(["doctor", "--root", d, "--offline", "--providers", "--strict"])
        self.assertEqual(rc, 1)
        self.assertIn("shadows the built-in", out)

    def test_the_probe_sees_the_loaded_project_config(self):
        seen = {}

        def fake_collect(config):
            seen["config"] = config
            return self._collect()

        with tempfile.TemporaryDirectory() as d:
            with patch.object(cli.providerprobe, "collect", fake_collect):
                rc, _, _ = run(
                    ["doctor", str(SAMPLE_PROJECT), "--root", d, "--offline", "--providers"]
                )
        self.assertEqual(rc, 0)
        self.assertIsNotNone(seen["config"])


if __name__ == "__main__":
    unittest.main()
