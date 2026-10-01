"""`website/revert-check.html` claims only what keel does (#1403).

The article shows a configuration, a diff and the output of `keel run-gates` on it. Each is
re-derived here rather than trusted: the configuration is the one the run uses, the diff is
what git prints for the fixture, and the output is what keel prints for it today. The
defaults, the off-by-default claim and the audit's framing are held to their sources.
"""

import html
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import yaml

from keel import cli, revertcheck

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTICLE = REPO_ROOT / "website" / "revert-check.html"

_BASE = (
    "def discount(total, percent):\n    saving = total * percent / 100\n    return total - saving\n"
)
_FIX = (
    "def discount(total, percent):\n"
    "    percent = min(max(percent, 0), 100)\n"
    "    saving = total * percent / 100\n"
    "    return round(total - saving, 2)\n"
)
_TESTS = (
    "import unittest\n\nfrom pkg import prices\n\n\n"
    "class Prices(unittest.TestCase):\n"
    "    def test_discount(self):\n"
    "        self.assertEqual(prices.discount(200, 10), 180)\n"
)
_CAP_TEST = (
    "\n    def test_discount_is_capped_at_100_percent(self):\n"
    "        self.assertEqual(prices.discount(200, 150), 0)\n"
)
_ROUND_TEST = (
    "\n    def test_discount_is_rounded_to_cents(self):\n"
    "        self.assertEqual(prices.discount(19.99, 15), 16.99)\n"
)


def _body() -> str:
    page = ARTICLE.read_text(encoding="utf-8")
    return page[page.index("<body>") :]


def _blocks(body: str) -> list[str]:
    return [html.unescape(b) for b in re.findall(r"<pre><code>(.*?)</code></pre>", body, re.S)]


def _text(body: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", body)).split())


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


class TheRevertCheckArticleClaimsOnlyWhatKeelDoes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = _body()
        cls.text = _text(cls.body)
        cls.blocks = _blocks(cls.body)

    def _block(self, starts: str) -> str:
        found = [b for b in self.blocks if b.startswith(starts)]
        self.assertEqual(len(found), 1, f"no single code block starting {starts!r}")
        return found[0]

    def _repo(self, root: Path) -> Path:
        """The article's fixture: `main` holds the base, `fix/discount` the fix and a cap test."""
        _git(root, "init", "-q", "-b", "main")
        for key, value in (
            ("user.email", "t@example.com"),
            ("user.name", "T"),
            ("gc.auto", "0"),
            ("maintenance.auto", "false"),
        ):
            _git(root, "config", key, value)
        (root / "pkg").mkdir()
        (root / "tests").mkdir()
        (root / ".keel").mkdir()
        (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        (root / "pkg" / "prices.py").write_text(_BASE, encoding="utf-8")
        (root / "tests" / "test_prices.py").write_text(_TESTS, encoding="utf-8")
        # The article's configuration, run with this interpreter instead of `python3`.
        config = yaml.safe_load(self._block("gates:"))
        shown = config["knobs"]["build_gate_cmd"]
        self.assertTrue(shown.startswith("python3 -m unittest"), shown)
        here = shown.replace("python3", f'"{sys.executable}"', 1)
        config["knobs"]["build_gate_cmd"] = here
        config.update(extends="keel", core_version="^1.25", repo="example/shop", base_branch="main")
        (root / ".keel" / "project.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "base")
        _git(root, "checkout", "-qb", "fix/discount")
        (root / "pkg" / "prices.py").write_text(_FIX, encoding="utf-8")
        (root / "tests" / "test_prices.py").write_text(_TESTS + _CAP_TEST, encoding="utf-8")
        _git(root, "commit", "-qam", "fix")
        return root

    def _run_gates(self, root: Path) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        cwd = os.getcwd()
        try:
            os.chdir(root)
            with redirect_stdout(out), redirect_stderr(err):
                rc = cli.main(["run-gates", ".keel/project.yaml", "--root", "."])
        finally:
            os.chdir(cwd)
        return rc, out.getvalue()

    def test_the_diff_output_and_follow_up_are_what_keel_prints(self):
        shown = self._block("$ keel run-gates").splitlines()
        self.assertEqual(shown[0], "$ keel run-gates .keel/project.yaml --root .")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            root = self._repo(Path(d))
            diff = _git(root, "diff", "--no-color", "--no-ext-diff", "-U0", "main", "--", "pkg/")
            hunks = diff[diff.index("@@") :].rstrip("\n")
            self.assertEqual(hunks, self._block("@@").rstrip("\n"))
            rc, out = self._run_gates(root)
            self.assertEqual(rc, 1, out)
            self.assertEqual(out.rstrip("\n").splitlines(), shown[1:])
            # "After adding a test that asserts discount(19.99, 15) == 16.99 …"
            self.assertIn("<code>discount(19.99, 15) == 16.99</code>", self.body)
            (root / "tests" / "test_prices.py").write_text(
                _TESTS + _CAP_TEST + _ROUND_TEST, encoding="utf-8"
            )
            _git(root, "commit", "-qam", "test")
            rc, out = self._run_gates(root)
        self.assertEqual(rc, 0, out)
        self.assertIn("ok  revert-check", out)
        summary = "2 of 2 production change(s) made a test fail as an assertion when reverted alone"
        self.assertIn(summary, out)
        self.assertIn(f"<code>{summary}</code>", self.body)

    def test_the_defaults_it_states_are_the_codes(self):
        self.assertIn(f"(default {revertcheck.DEFAULT_MAX_CHANGES})", self.text)
        self.assertIn(f"(default {revertcheck.DEFAULT_BUDGET_S} seconds)", self.text)

    def test_it_is_off_for_keel_itself_as_the_article_says(self):
        self.assertIn(
            "including in keel's own configuration, which lists build and lint", self.text
        )
        for path in ("projects/keel.yaml", ".keel/project.yaml"):
            with self.subTest(config=path):
                raw = yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))
                self.assertEqual(raw["gates"], ["build", "lint"])

    def test_the_audit_is_told_in_the_changelogs_tense(self):
        """Today all fourteen fail; three survived only at the time their issue closed."""
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("Today all 14 fail without their fix", changelog)
        self.assertIn("three survived at the time their issue was closed", changelog)
        self.assertIn("Today all fourteen fail without their fix", self.text)
        self.assertIn("at the time their issues were closed, three had survived", self.text)

    def test_what_counts_as_an_assertion_is_what_the_gate_counts(self):
        """#1408: the article's assertion list and its missing-name exception, checked
        against the gate itself."""
        # pytest-timeout's reason is not an assertion, and the article says so.
        self.assertIsNone(revertcheck._ASSERTION_REASON.match("Failed: Timeout >30.0s"))
        self.assertIsNotNone(revertcheck._ASSERTION_REASON.match("Failed: DID NOT RAISE"))
        self.assertIn("except Failed: Timeout", self.text)
        # The exception needs every counted failure described, and the article says so.
        name = "NameError: name 'g' is not defined"
        one_of_two = f"FAILED tests/t.py::test_a - {name}\n2 failed in 0.10s\n"
        both = f"FAILED tests/t.py::test_a - {name}\nFAILED tests/t.py::test_b - {name}\n"
        self.assertFalse(revertcheck.missing_names_only(one_of_two))
        self.assertTrue(revertcheck.missing_names_only(both + "2 failed in 0.10s\n"))
        self.assertIn("with every failure the run counts explained by one of them", self.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
