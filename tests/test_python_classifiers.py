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


def _tested() -> set[str]:
    """Every `3.x` named on a matrix `python:` line of ci.yml."""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    versions: set[str] = set()
    for line in text.splitlines():
        if re.match(r"\s*python(-version)?:", line):
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

    def test_every_classified_version_is_one_ci_runs(self):
        untested = {version for version, _ in _declared()} - _tested()
        self.assertEqual(untested, set(), f"classified but no CI leg runs it: {untested}")


if __name__ == "__main__":
    unittest.main()
