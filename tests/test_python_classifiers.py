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
# Each job is held to the classifiers on its own: a union let 3.14 leave `test` while
# `test-visual` still ran it, and the core package is what the classifier describes.
_TEST_JOBS = ("test", "test-visual")


def _read_matrices() -> tuple[dict[str, set[str]], set[str]]:
    """Each test job's `3.x` versions, read from its `strategy.matrix` block only,
    comments dropped — a `python:` step input or env value elsewhere is not a leg —
    and the test jobs whose matrix has an `exclude:` key."""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    found: dict[str, set[str]] = {}
    excluding: set[str] = set()
    job, matrix_indent = None, None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line:
            continue
        indent = len(line) - len(line.lstrip())
        header = re.fullmatch(r"  ([\w-]+):", line)
        if header or indent == 0:
            job, matrix_indent = (header.group(1) if header else None), None
            continue
        if job not in _TEST_JOBS:
            continue
        if re.fullmatch(r"\s*matrix:", line):
            matrix_indent = indent
            continue
        if matrix_indent is not None and indent <= matrix_indent:
            matrix_indent = None
        if matrix_indent is not None and re.match(r"\s*exclude:", line):
            excluding.add(job)
        if matrix_indent is not None and re.match(r"\s*python:", line):
            found.setdefault(job, set()).update(re.findall(r"3\.\d+", line))
    return found, excluding


def _matrix_versions() -> dict[str, set[str]]:
    return _read_matrices()[0]


class TheClassifiersAreClaimsCIBacks(unittest.TestCase):
    def test_the_lowest_classifier_is_the_floor_and_there_are_no_gaps(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        floor = re.fullmatch(r">=3\.(\d+)", project["project"]["requires-python"])
        minors = [minor for _, minor in _declared()]
        self.assertIsNotNone(floor)
        self.assertEqual(minors[0], int(floor.group(1)))
        self.assertEqual(minors, list(range(minors[0], minors[-1] + 1)))

    def test_every_test_job_has_a_matrix(self):
        """A parser that found no matrix would make the check below vacuous. The
        floor comes from the classifiers, so moving it needs no second edit here."""
        found = _matrix_versions()
        floor = _declared()[0][0]
        for job in _TEST_JOBS:
            with self.subTest(job):
                self.assertIn(floor, found.get(job, set()))

    def test_no_test_matrix_excludes_a_leg(self):
        """An `exclude:` drops a listed version from what runs, and this parser does
        not model it (#1302 lead). The matrices have never used one: drop the version
        from the list instead, so the check below sees it."""
        self.assertEqual(_read_matrices()[1], set())

    def test_every_classified_version_runs_in_every_test_job(self):
        declared = {version for version, _ in _declared()}
        found = _matrix_versions()
        for job in _TEST_JOBS:
            with self.subTest(job):
                missing = declared - found.get(job, set())
                self.assertEqual(missing, set(), f"classified but `{job}` never runs it")


if __name__ == "__main__":
    unittest.main()
