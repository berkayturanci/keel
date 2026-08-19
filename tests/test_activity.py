"""Tests for the additive command-activity channel."""

import tempfile
import unittest
from pathlib import Path

from keel import activity
from keel import config as cfg


def _config(*, activity_dir: str | None = None) -> cfg.ProjectConfig:
    reports = {"activity": activity_dir} if activity_dir is not None else {}
    return cfg.ProjectConfig(
        extends="keel",
        core_version="^0.7",
        base_branch="main",
        knobs=cfg.Knobs(build_gate_cmd="true"),
        policy_pack={"name": "test", "reports": reports},
    )


def _record(**overrides):
    values = {"command": "triage", "run_id": "triage-2260", "phase": "classify",
              "issue": 2260, "pr": None, "note": "n"}
    values.update(overrides)
    return activity.build_activity_record(**values)


class TestContract(unittest.TestCase):
    def test_contract_is_additive_and_checkpoint_free(self):
        c = activity.activity_contract_as_dict()
        self.assertEqual(c["schema_version"], activity.ACTIVITY_SCHEMA_VERSION)
        self.assertTrue(c["additive"])
        self.assertFalse(c["touches_checkpoint"])
        self.assertEqual(c["keyed_by"], "run_id")
        self.assertIn("merged", c["statuses"])


class TestDir(unittest.TestCase):
    def test_default_dir(self):
        raw, source = activity.configured_activity_dir(_config())
        self.assertEqual(raw, activity.DEFAULT_ACTIVITY_DIR)
        self.assertEqual(source, "default")

    def test_override_dir(self):
        raw, source = activity.configured_activity_dir(_config(activity_dir="x/act"))
        self.assertEqual(raw, "x/act")
        self.assertEqual(source, "policy_pack.reports.activity")

    def test_reports_not_a_dict_falls_back(self):
        config = cfg.ProjectConfig(
            extends="keel", core_version="^0.7", base_branch="main",
            knobs=cfg.Knobs(build_gate_cmd="true"),
            policy_pack={"name": "t", "reports": "nope"})
        raw, source = activity.configured_activity_dir(config)
        self.assertEqual(source, "default")

    def test_resolve_dir_ok(self):
        with tempfile.TemporaryDirectory() as d:
            resolved = activity.resolve_dir(d, _config())
            self.assertEqual(resolved.parts[-2:], (".keel", "activity"))

    def test_resolve_dir_absolute_rejected(self):
        # an OS-correct absolute path (a drive-rooted path on Windows)
        absolute = str(Path(tempfile.gettempdir()).resolve())
        with self.assertRaises(activity.ActivityError):
            activity.resolve_dir(".", _config(activity_dir=absolute))

    def test_resolve_dir_escape_rejected(self):
        with self.assertRaises(activity.ActivityError):
            activity.resolve_dir(".", _config(activity_dir="../../escape"))


class TestRunIdSlug(unittest.TestCase):
    def test_slug_lowercases_and_replaces(self):
        self.assertEqual(activity.run_id_slug("Triage #2260!"), "triage-2260")

    def test_slug_rejects_non_string(self):
        with self.assertRaises(activity.ActivityError):
            activity.run_id_slug(None)  # type: ignore[arg-type]

    def test_slug_rejects_blank(self):
        with self.assertRaises(activity.ActivityError):
            activity.run_id_slug("   ")

    def test_slug_rejects_all_special(self):
        with self.assertRaises(activity.ActivityError):
            activity.run_id_slug("!!!")

    def test_record_path_uses_slug(self):
        with tempfile.TemporaryDirectory() as d:
            p = activity.record_path(d, _config(), "Triage #2260")
            self.assertEqual(p.name, "triage-2260.json")
            self.assertEqual(p.parent.parts[-2:], (".keel", "activity"))


class TestBuild(unittest.TestCase):
    def test_build_full(self):
        rec = _record()
        self.assertEqual(rec["command"], "triage")
        self.assertEqual(rec["phase"], "classify")
        self.assertEqual(rec["status"], "running")
        self.assertEqual(rec["issue"], 2260)

    def test_build_minimal(self):
        rec = activity.build_activity_record(
            command="morning", run_id="m1", phase="config")
        self.assertIsNone(rec["issue"])
        self.assertIsNone(rec["pr"])
        self.assertIsNone(rec["note"])

    def test_build_done_status(self):
        self.assertEqual(_record(status="done")["status"], "done")

    def test_build_merged_status(self):
        self.assertEqual(_record(status="merged")["status"], "merged")

    def test_verdict_defaults_to_absent_not_to_pass(self):
        """No verdict is not a pass. That conflation is the #636 defect."""
        self.assertIsNone(_record()["verdict"])

    def test_build_records_a_blocked_verdict(self):
        rec = _record(verdict="blocked")
        self.assertEqual(rec["verdict"], "blocked")
        # status stays `running`: the run advanced but did not finish. The two
        # facts are deliberately separate.
        self.assertEqual(rec["status"], "running")

    def test_build_records_a_pass_verdict(self):
        self.assertEqual(_record(verdict="pass")["verdict"], "pass")

    def test_build_bad_verdict(self):
        with self.assertRaises(activity.ActivityError):
            _record(verdict="green")

    def test_build_unknown_command(self):
        with self.assertRaises(activity.ActivityError):
            activity.build_activity_record(command="nope", run_id="x", phase="config")

    def test_build_bad_phase(self):
        with self.assertRaises(activity.ActivityError):
            activity.build_activity_record(command="triage", run_id="x", phase="s7")

    def test_build_bad_status(self):
        with self.assertRaises(activity.ActivityError):
            activity.build_activity_record(
                command="triage", run_id="x", phase="classify", status="weird")


class TestValidate(unittest.TestCase):
    def test_ok(self):
        activity.validate_activity(_record())

    def test_a_missing_verdict_validates(self):
        # Records written before #636 carry no verdict field at all.
        rec = _record()
        del rec["verdict"]
        activity.validate_activity(rec)

    def test_a_wrong_verdict_is_rejected(self):
        # A board that trusts this field must never read a typo as a pass.
        rec = _record()
        rec["verdict"] = "ok"
        with self.assertRaises(activity.ActivityError):
            activity.validate_activity(rec)

    def test_merged_ok(self):
        activity.validate_activity(_record(status="merged"))

    def test_not_dict(self):
        with self.assertRaises(activity.ActivityError):
            activity.validate_activity(["not", "a", "dict"])

    def test_bad_schema(self):
        with self.assertRaises(activity.ActivityError):
            activity.validate_activity({**_record(), "schema_version": "x"})

    def test_bad_record_type(self):
        with self.assertRaises(activity.ActivityError):
            activity.validate_activity({**_record(), "record_type": "x"})

    def test_bad_command_type(self):
        with self.assertRaises(activity.ActivityError):
            activity.validate_activity({**_record(), "command": 7})

    def test_unknown_command(self):
        with self.assertRaises(activity.ActivityError):
            activity.validate_activity({**_record(), "command": "nope"})

    def test_bad_run_id(self):
        with self.assertRaises(activity.ActivityError):
            activity.validate_activity({**_record(), "run_id": "  "})

    def test_bad_phase(self):
        with self.assertRaises(activity.ActivityError):
            activity.validate_activity({**_record(), "phase": "zzz"})

    def test_bad_status(self):
        with self.assertRaises(activity.ActivityError):
            activity.validate_activity({**_record(), "status": "zzz"})


class TestEncodeParse(unittest.TestCase):
    def test_round_trip(self):
        rec = _record()
        self.assertEqual(activity.parse_activity(activity.encode_activity(rec)), rec)

    def test_parse_invalid_json(self):
        with self.assertRaises(activity.ActivityError):
            activity.parse_activity("{not json")


class TestIO(unittest.TestCase):
    def test_read_missing_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(activity.read_activity(Path(d) / "missing.json"))

    def test_write_then_read(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "r.json"
            activity.write_activity(p, _record())
            self.assertEqual(activity.read_activity(p)["phase"], "classify")

    def test_write_activity_cleanup_on_replace_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.json"
            with unittest.mock.patch("os.replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    activity.write_activity(p, _record())
            self.assertEqual(list(Path(d).glob(".*")), [])

    def test_remove(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.json"
            self.assertFalse(activity.remove_activity(p))
            activity.write_activity(p, _record())
            self.assertTrue(activity.remove_activity(p))
            self.assertFalse(p.exists())

    def test_read_all_no_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(activity.read_all_activity(Path(d) / "none"), [])

    def test_read_all_skips_bad_files(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            activity.write_activity(directory / "good.json", _record(run_id="good"))
            (directory / "bad.json").write_text("{nope", encoding="utf-8")
            records = activity.read_all_activity(directory)
            self.assertEqual([r["run_id"] for r in records], ["good"])


if __name__ == "__main__":
    unittest.main()
