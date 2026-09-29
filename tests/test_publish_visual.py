"""keel-visual's release cannot publish the wrong version or drop a template (#1371).

keel-visual 0.9.0 shipped from `publish-visual.yml` (#1370) with two guards fewer
than core's `publish.yml`:

- **No tag guard.** Core runs `release_check.py --tag "$TAG"` before building;
  this workflow ran nothing. Both publish steps use `skip-existing: true`, so a tag
  that disagrees with `keel-visual/pyproject.toml` skips the upload as "already
  there", or publishes a version the tag does not name — green either way.
- **One template of four.** The wheel's `force-include` listed three templates
  (not `swarm.html`), and the workflow asserted only `runviz.html`. `swarm.html`
  shipped because the package directory is included, not because anything said so.

What only a real `keel-visual-v*` tag push can confirm is that the workflow runs
these steps in CI; the tests below hold the logic and the wiring, and run the
wheel check's own script against wheels built for the purpose.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

VISUAL = REPO_ROOT / "keel-visual"
TEMPLATES = VISUAL / "src" / "keel_visual" / "templates"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-visual.yml"
PYPROJECT = tomllib.loads((VISUAL / "pyproject.toml").read_text(encoding="utf-8"))


def _templates() -> list[str]:
    """Every HTML template keel-visual renders from, read off the source tree."""
    return sorted(path.name for path in TEMPLATES.glob("*.html"))


def _visual_fixture(root: Path, version: str) -> None:
    """A minimal tree carrying keel-visual's two version markers, agreeing."""
    (root / "keel-visual" / "src" / "keel_visual").mkdir(parents=True)
    (root / "keel-visual" / "pyproject.toml").write_text(
        f'[project]\nname = "keel-visual"\nversion = "{version}"\n', encoding="utf-8"
    )
    (root / "keel-visual" / "src" / "keel_visual" / "__init__.py").write_text(
        f'"""keel-visual."""\n\n__version__ = "{version}"\n', encoding="utf-8"
    )


def _release_check(*argv: str) -> subprocess.CompletedProcess:
    """Run `scripts/release_check.py` the way the workflow does: as a command.

    Through a subprocess rather than an import, so the test holds the command line
    `publish-visual.yml` runs — flags included — and not only the functions behind it.
    """
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "release_check.py"), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        # The child writes UTF-8 whatever the console code page; on Windows it
        # would write cp1252, which this side cannot decode (the "—" in messages).
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )


class TestTheTagMustNameKeelVisualsVersion(unittest.TestCase):
    def _check(self, version: str, *argv: str) -> subprocess.CompletedProcess:
        with TemporaryDirectory() as tmp:
            _visual_fixture(Path(tmp), version)
            return _release_check("--root", tmp, "--package", "keel-visual", *argv)

    def test_a_tag_naming_the_declared_version_passes(self):
        result = self._check("0.9.0", "--tag", "keel-visual-v0.9.0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS  tag", result.stdout)

    def test_a_tag_naming_another_version_is_refused(self):
        result = self._check("0.9.0", "--tag", "keel-visual-v0.9.1")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL  tag", result.stdout)
        self.assertIn("declared version 0.9.0", result.stdout)

    def test_a_core_tag_is_not_a_keel_visual_tag(self):
        result = self._check("0.9.0", "--tag", "v0.9.0")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("not of the form keel-visual-vX.Y.Z", result.stdout)

    def test_diverged_markers_are_refused_on_a_keel_visual_release_too(self):
        with TemporaryDirectory() as tmp:
            _visual_fixture(Path(tmp), "0.9.0")
            marker = Path(tmp) / "keel-visual" / "src" / "keel_visual" / "__init__.py"
            marker.write_text('__version__ = "0.8.0"\n', encoding="utf-8")
            result = _release_check(
                "--root", tmp, "--package", "keel-visual", "--tag", "keel-visual-v0.9.0"
            )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL  keel-visual markers", result.stdout)

    def test_only_keel_visuals_guards_run(self):
        """Core's guards read core's version line; a keel-visual tag is not one of its."""
        result = self._check("0.9.0", "--tag", "keel-visual-v0.9.0")
        guards = re.findall(r"^  (?:PASS|FAIL)  (.+)$", result.stdout, re.MULTILINE)
        self.assertEqual(guards, ["keel-visual markers", "tag"])
        self.assertIn("declares keel-visual 0.9.0", result.stdout)

    def test_the_tree_passes_its_own_keel_visual_check(self):
        tag = f"keel-visual-v{PYPROJECT['project']['version']}"
        result = _release_check("--package", "keel-visual", "--tag", tag)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class TestTheWheelListsEveryTemplate(unittest.TestCase):
    def test_every_template_is_force_included(self):
        templates = _templates()
        # Vacuity: a moved directory would make an empty list agree with anything.
        self.assertGreaterEqual(len(templates), 4, templates)
        forced = PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
        listed = {source: dest for source, dest in forced.items() if "/templates/" in source}
        self.assertEqual(
            listed,
            {
                f"src/keel_visual/templates/{name}": f"keel_visual/templates/{name}"
                for name in templates
            },
        )


class TestThePublishVisualWorkflow(unittest.TestCase):
    """The wiring, and the wheel check's own script run against built wheels."""

    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.steps = yaml.safe_load(self.text)["jobs"]["build-n-publish"]["steps"]

    @staticmethod
    def _code(script: str) -> str:
        """``script`` without comment lines, so prose about a command is not the command."""
        return "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))

    def _wheel_check(self) -> str:
        """The Python the "verify the wheel" step feeds to `python -`."""
        scripts = [step["run"] for step in self.steps if "zipfile" in step.get("run", "")]
        self.assertEqual(len(scripts), 1, "exactly one step inspects the wheel")
        match = re.search(r"<<'PY'\n(.*?)\n\s*PY\s*$", scripts[0], re.DOTALL)
        self.assertIsNotNone(match, "the wheel check is a `python - <<'PY'` heredoc")
        return match.group(1)

    def _run_wheel_check(self, *, shipped: list[str], source: list[str]):
        """Run the step's script where `dist/` holds a wheel carrying ``shipped``."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dist").mkdir()
            with zipfile.ZipFile(root / "dist" / "keel_visual-0.0.0-py3-none-any.whl", "w") as whl:
                whl.writestr("keel_visual/__init__.py", "")
                for name in shipped:
                    whl.writestr(f"keel_visual/templates/{name}", "<html></html>")
            templates = root / "keel-visual" / "src" / "keel_visual" / "templates"
            templates.mkdir(parents=True)
            for name in source:
                (templates / name).write_text("<html></html>", encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-"],
                input=self._wheel_check(),
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                check=False,
            )

    def test_the_tag_guard_runs_before_the_build(self):
        code = self._code(self.text)
        self.assertIn('scripts/release_check.py --package keel-visual --tag "$TAG"', code)
        self.assertLess(
            code.index("scripts/release_check.py"),
            code.index("python -m build"),
            "the tag guard must run before anything is built or uploaded",
        )

    def test_the_tag_guard_reads_the_pushed_tag(self):
        guard = [step for step in self.steps if "release_check.py" in step.get("run", "")]
        self.assertEqual(len(guard), 1)
        self.assertEqual(guard[0]["env"]["TAG"], "${{ github.ref_name }}")

    def test_a_wheel_missing_any_template_is_refused(self):
        templates = _templates()
        for missing in templates:
            with self.subTest(missing=missing):
                shipped = [name for name in templates if name != missing]
                result = self._run_wheel_check(shipped=shipped, source=templates)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(missing, result.stderr)

    def test_a_wheel_carrying_every_template_passes(self):
        templates = _templates()
        result = self._run_wheel_check(shipped=templates, source=templates)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{len(templates)} templates present", result.stdout)

    def test_finding_no_templates_is_a_failure_not_a_pass(self):
        result = self._run_wheel_check(shipped=_templates(), source=[])
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("no templates found", result.stderr)

    def test_any_core_floor_the_header_states_is_the_one_keel_visual_declares(self):
        """The header said "matching core (>= 1.3.0)" while pyproject required 1.15.0."""
        (floor,) = [
            dep.split(">=", 1)[1]
            for dep in PYPROJECT["project"]["dependencies"]
            if dep.startswith("keel-workflow>=")
        ]
        stated = re.findall(r">=\s*(\d+\.\d+\.\d+)", self.text)
        self.assertEqual([version for version in stated if version != floor], [])


class TestTheRunbookNamesEveryTemplate(unittest.TestCase):
    def test_the_manual_fallback_names_every_template(self):
        """RELEASING.md said `force-include` ships "the HTML template (`runviz.html`)"."""
        runbook = (VISUAL / "RELEASING.md").read_text(encoding="utf-8")
        missing = [name for name in _templates() if f"`{name}`" not in runbook]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
