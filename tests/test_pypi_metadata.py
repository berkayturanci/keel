"""The PyPI project page describes keel as it is (#1340).

`pyproject.toml`'s `[project]` table is what PyPI renders beside the README: the
summary under the name, the keyword tags, the classifiers, and the sidebar links.
Before #1340 the summary was the internal architecture line ("Project-neutral,
multi-agent workflow core …"), the keywords named `claude` and none of the other
hosts, and "Documentation" pointed at a directory listing on GitHub while the site
had a docs page.

The Python-version classifiers are held by `tests/test_python_classifiers.py`.
Classifier *validity* and URL *reachability* need the `trove-classifiers` package
and the network, which this offline suite has neither of; they were checked when
the values were set (see the pull request) and PyPI rejects an unknown classifier
at upload.
"""

from __future__ import annotations

import importlib.util
import json
import re
import tomllib
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _install_page_hosts() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The hosts `docs/keel/install.md` measures and the names that are not hosts,
    read from the test that owns that list.

    Loaded by path, the way these tests load `scripts/`: `tests/` is not a package.
    """
    path = ROOT / "tests" / "test_agent_install_docs.py"
    spec = importlib.util.spec_from_file_location("_install_docs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.install_page_hosts(), module.NOT_A_HOST


class TheSummaryIsVendorNeutral(unittest.TestCase):
    def test_it_names_no_single_host(self):
        """keel runs under four hosts; a summary naming one reads as that host's tool."""
        hosts, not_hosts = _install_page_hosts()
        self.assertEqual(len(hosts), 4, hosts)
        named = [
            name
            for name in (*hosts, *not_hosts, "Claude")
            if re.search(rf"\b{re.escape(name)}\b", PROJECT["description"])
        ]
        self.assertEqual(named, [], f"the PyPI summary names {named}")

    @staticmethod
    def _about_line() -> str:
        marketplace = json.loads(
            (ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        return marketplace["plugins"][0]["description"]

    def test_it_leads_with_the_about_lines_lead_sentence(self):
        """The summary opens with the sentence the GitHub About line and the plugin
        marketplace already open with, so the three front doors say one thing."""
        lead = self._about_line().split(".")[0]
        self.assertEqual(lead, "Turn coding agents into work owners")
        self.assertTrue(
            PROJECT["description"].startswith(lead),
            f"the PyPI summary does not open with {lead!r}: {PROJECT['description']!r}",
        )

    def test_it_uses_the_about_lines_framing_of_what_keel_is(self):
        """A backbone that takes an issue to a merged PR — the About line's words."""
        framing = re.search(
            r"a vendor-neutral backbone that takes a GitHub issue to a merged",
            self._about_line(),
            re.IGNORECASE,
        )
        self.assertIsNotNone(framing, "the About line no longer carries its framing")
        self.assertIn(framing.group(0).lower(), PROJECT["description"].lower())

    def test_it_does_not_say_the_cli_does_the_work(self):
        """The agent host does the work and `keel ship` is a dry assessment; the CLI
        enforces the backbone (README, #1335). "A CLI that drives an issue to a
        merged pull request" claimed the part the host does."""
        description = PROJECT["description"]
        self.assertNotRegex(description, r"\bCLI\b")
        self.assertNotRegex(description, r"(?i)\bdrives?\b")

    def test_it_fits_pypis_summary_limit(self):
        """PyPI rejects a `Summary` longer than 512 characters, or one with a newline."""
        self.assertLessEqual(len(PROJECT["description"]), 512)
        self.assertNotIn("\n", PROJECT["description"])


class TheKeywordsNameEveryHost(unittest.TestCase):
    def test_every_host_the_install_page_measures_is_a_keyword(self):
        hosts, _ = _install_page_hosts()
        slugs = {name.lower().replace(" ", "-") for name in hosts}
        self.assertEqual(slugs, {"claude-code", "codex", "cursor", "antigravity"})
        missing = sorted(slugs - set(PROJECT["keywords"]))
        self.assertEqual(missing, [], f"PyPI keywords leave out the hosts {missing}")

    def test_claude_is_spelled_as_the_host(self):
        """`claude` is a model family; the host keel installs into is Claude Code."""
        self.assertNotIn("claude", PROJECT["keywords"])

    def test_no_keyword_repeats(self):
        keywords = PROJECT["keywords"]
        self.assertEqual(len(keywords), len(set(keywords)), keywords)


class TheClassifiersDescribeWhatShips(unittest.TestCase):
    def test_a_console_script_is_classified_as_a_console_environment(self):
        self.assertIn("keel", PROJECT["scripts"], "the console script moved")
        self.assertIn("Environment :: Console", PROJECT["classifiers"])


class TheSidebarLinksGoWhereTheySay(unittest.TestCase):
    def test_documentation_is_the_sites_docs_page(self):
        """The site's own domain, and a page the site actually serves."""
        url = urlsplit(PROJECT["urls"]["Documentation"])
        domain = (ROOT / "website" / "CNAME").read_text(encoding="utf-8").strip()
        self.assertEqual(url.scheme, "https")
        self.assertEqual(url.netloc, domain)
        self.assertTrue(
            (ROOT / "website" / url.path.lstrip("/")).is_file(),
            f"website/ serves no {url.path}",
        )

    def test_release_notes_are_this_repositorys_releases_page(self):
        urls = PROJECT["urls"]
        self.assertEqual(urls.get("Release notes"), f"{urls['Repository']}/releases")

    def test_every_link_is_https(self):
        for label, url in PROJECT["urls"].items():
            with self.subTest(label=label):
                self.assertEqual(urlsplit(url).scheme, "https", url)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
