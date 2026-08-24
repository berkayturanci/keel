"""Unit tests for the pure ``keel doctor`` diagnostics and its CLI handler."""

import contextlib
import io
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
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
            installed_version="1.2.3", latest_version="1.2.3",
            adapter_markers=markers, core_version="^1.0",
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
            url="file:///etc/passwd",
            _opener=self._FakeOpener(self._FakeResponse(b""))
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
        self.assertIn(urllib.request.HTTPSHandler, classes)
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
            {"checkout_binding", "cli_version", "adapter_version",
             "orphan_adapters", "core_version", "state_paths"},
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
            rc, out, _ = run(
                ["doctor", str(SAMPLE_PROJECT), "--root", d, "--offline", "--json"]
            )
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
                ["doctor", str(SAMPLE_PROJECT), "--root", d, "--offline",
                 "--strict", "--json"]
            )
        report = json.loads(out)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(rc, 1)

    def test_strict_fail_advisory_without_strict(self):
        self._real_setup()
        with tempfile.TemporaryDirectory() as d:
            install.install_all(d)
            rc, out, _ = run(
                ["doctor", str(SAMPLE_PROJECT), "--root", d, "--offline", "--json"]
            )
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
        check = _check(_doctor(module_path="/anywhere/src/keel", checkout_root=None),
                       "checkout_binding")
        self.assertEqual(check["status"], "ok")
        self.assertIn("not run against a keel checkout", check["summary"])
        self.assertIsNone(check["detail"]["checkout_root"])

    def test_defaults_skip_the_check(self):
        # Callers predating the check omit both paths and keep their behaviour.
        self.assertEqual(_check(_doctor(), "checkout_binding")["status"], "ok")

    def test_unlocatable_module_warns(self):
        check = _check(_doctor(module_path=None, checkout_root="/repo"),
                       "checkout_binding")
        self.assertEqual(check["status"], "warn")
        self.assertIn("could not be located", check["summary"])

    def test_module_inside_the_checkout_is_ok(self):
        check = _check(_doctor(module_path="/repo/src/keel", checkout_root="/repo"),
                       "checkout_binding")
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
        check = _check(_doctor(module_path="/repo-two/src/keel", checkout_root="/repo"),
                       "checkout_binding")
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


if __name__ == "__main__":
    unittest.main()
