"""The Python classifiers are claims CI backs (#1297).

`pyproject.toml` stopped at 3.13 while the installer and the package accepted 3.14,
so the PyPI badge understated support; the fix added the classifier only with a CI
leg for it. These tests keep the two in step: the lowest classifier is the
`requires-python` floor, there are no gaps, and every classified version is one a
CI matrix runs.
"""

from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_CLASSIFIER = re.compile(r"Programming Language :: Python :: (3\.(\d+))")


def _declared() -> list[tuple[str, int]]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    found = (_CLASSIFIER.fullmatch(c) for c in project["classifiers"])
    return sorted(((m.group(1), int(m.group(2))) for m in found if m), key=lambda v: v[1])


# The jobs whose matrices are the test runs. Other jobs (format, external-promises,
# release-lockfiles) pin one Python to run a tool, and a version named only there is
# not tested — counting them let a version leave both matrices unnoticed (#1302 gate).
_TEST_JOBS = ("test", "test-visual")


def _tested() -> set[str]:
    """Every `3.x` on a `python:` line inside a test job of ci.yml, comments dropped."""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    versions: set[str] = set()
    job = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        header = re.fullmatch(r"  ([\w-]+):", line)
        if header or (line and not line.startswith(" ")):
            job = header.group(1) if header else None
            continue
        if job in _TEST_JOBS and re.match(r"\s*python:", line):
            versions.update(re.findall(r"3\.\d+", line))
    return versions


class TheClassifiersAreClaimsCIBacks(unittest.TestCase):
    def test_the_lowest_classifier_is_the_floor_and_there_are_no_gaps(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        floor = re.fullmatch(r">=3\.(\d+)", project["project"]["requires-python"])
        minors = [minor for _, minor in _declared()]
        self.assertIsNotNone(floor)
        self.assertEqual(minors[0], int(floor.group(1)))
        self.assertEqual(minors, list(range(minors[0], minors[-1] + 1)))

    def test_both_test_matrices_are_read(self):
        """A parser that found no test job would make the check below vacuous."""
        self.assertGreaterEqual(_tested(), {"3.11", "3.12", "3.13"})

    def test_every_classified_version_is_one_ci_runs(self):
        untested = {version for version, _ in _declared()} - _tested()
        self.assertEqual(untested, set(), f"classified but no CI leg runs it: {untested}")


if __name__ == "__main__":
    unittest.main()
