"""The site's search-facing fields must stay present, sized, and in the sitemap.

Two separate problems this pins, both found by auditing the live site.

The copy targeted invented vocabulary. The page said "agentic work-ownership
backbone" and "step machine" — brand language with no search volume — while
containing zero occurrences of "pull request", "automate" or "CI/CD" and one of
"code review". Nobody searches for a phrase you made up, so the technically
perfect metadata was pointing at nothing.

And a page that nothing links to and no sitemap lists is a page search engines
have no reason to fetch. The article added alongside this is exactly that risk.

Offline and cheap — these are facts about the files, not about Google.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE = REPO_ROOT / "website"

#: The canonical origin. Every search-facing URL in the site is derived from
#: this one string, so the next domain move is a one-line change here plus the
#: files themselves — not a hunt through hand-written literals.
BASE = "https://keel-ship.dev/"
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

#: Pages a search engine should be able to find. Deliberately a list rather than
#: a glob: 404.html must *not* be in the sitemap, and asserting the glob would
#: quietly accept it.
INDEXED_PAGES = ("index.html", "docs.html", "coverage.html", "silent-revert.html")

#: Terms a person would actually type. The site is free to lead with its own
#: language — it just cannot be the *only* language on the page.
SEARCH_TERMS = ("code review", "pull request", "GitHub Action", "Claude Code")


def _head(name: str) -> str:
    text = (SITE / name).read_text(encoding="utf-8")
    return text[: text.find("</head>")]


class TestSearchFacingFields(unittest.TestCase):
    def test_every_indexed_page_exists(self):
        # Guards the rest: a renamed file would otherwise make the loops below
        # iterate over nothing and pass.
        for page in INDEXED_PAGES:
            with self.subTest(page=page):
                self.assertTrue((SITE / page).is_file(), f"{page} is listed but missing")

    def test_titles_and_descriptions_are_present_and_not_truncated(self):
        # Google shows roughly 60 characters of a title and 155 of a description.
        # Longer is not an error, but it is text nobody reads, and the call to
        # action is what gets cut.
        for page in INDEXED_PAGES:
            with self.subTest(page=page):
                head = _head(page)
                title = re.search(r"<title>([^<]+)</title>", head)
                self.assertIsNotNone(title, f"{page} has no <title>")
                self.assertLessEqual(
                    len(title.group(1).replace("&amp;", "&")),
                    70,
                    f"{page} title is longer than a search result shows",
                )
                description = re.search(r'<meta name="description" content="([^"]+)"', head)
                self.assertIsNotNone(description, f"{page} has no meta description")
                self.assertLessEqual(
                    len(description.group(1)),
                    165,
                    f"{page} description will be truncated in results",
                )

    def test_the_homepage_speaks_in_terms_people_search_for(self):
        # The whole point: brand language is fine, brand-language-only is not.
        text = (SITE / "index.html").read_text(encoding="utf-8").lower()
        missing = [term for term in SEARCH_TERMS if term.lower() not in text]
        self.assertEqual([], missing, "the homepage never uses these search terms")

    def test_every_indexed_page_declares_a_canonical_url(self):
        for page in INDEXED_PAGES:
            with self.subTest(page=page):
                self.assertRegex(
                    _head(page),
                    rf'rel="canonical"\s+href="{re.escape(BASE)}',
                )


class TestSitemap(unittest.TestCase):
    def _locs(self) -> set[str]:
        root = ET.parse(SITE / "sitemap.xml").getroot()
        return {e.text.strip() for e in root.iter(f"{SITEMAP_NS}loc") if e.text}

    def test_the_sitemap_lists_every_indexed_page(self):
        locs = self._locs()
        self.assertTrue(locs, "the sitemap is empty")
        for page in INDEXED_PAGES:
            expected = BASE + ("" if page == "index.html" else page)
            with self.subTest(page=page):
                self.assertIn(expected, locs, f"{page} is not in the sitemap")

    def test_the_sitemap_does_not_list_the_error_page(self):
        self.assertNotIn(BASE + "404.html", self._locs())

    def test_robots_points_at_the_sitemap(self):
        # How a crawler finds it without being told.
        robots = (SITE / "robots.txt").read_text(encoding="utf-8")
        self.assertIn(f"Sitemap: {BASE}sitemap.xml", robots)


class TestArticleIsReachable(unittest.TestCase):
    """A page nothing links to is a page nothing crawls."""

    def test_the_article_is_linked_from_the_homepage(self):
        self.assertIn(
            'href="silent-revert.html"',
            (SITE / "index.html").read_text(encoding="utf-8"),
        )

    def test_the_article_carries_article_structured_data(self):
        head = _head("silent-revert.html")
        self.assertIn("application/ld+json", head)
        self.assertIn('"@type": "TechArticle"', head)
        self.assertIn('property="og:type" content="article"', head)


class TestAdvertisedUrlsResolve(unittest.TestCase):
    """Every keel-ship.dev URL we hand people must actually be published.

    The sibling repo shipped a README that told users to run
    ``curl -fsSL https://<site>/install.sh | sh`` while the file sat at the repo
    root and the Pages artifact was built from ``website/`` — so the headline
    install command 404'd for months. No canonical or sitemap check can catch
    that: the URL is advertised in *prose*, not in the site.

    The move to keel-ship.dev is exactly when such a link rots, so pin it now.

    Both scans below walk the **same** tracked-file set. An earlier revision
    hand-listed the files and missed three that the migration itself had to
    edit — including ``docs/keel/badges.md``, the copy-paste source for the
    coverage badge — so a revert there would have shipped green. A guard that
    covers a hand-picked subset of the blast radius is not a guard.
    """

    #: Paths the Pages workflow generates into website/ at build time, mapped to
    #: the step name that must still produce them. Keys are matched on their
    #: first path segment, so /coverage, /coverage/ and /coverage/index.html all
    #: resolve to the same entry.
    BUILD_TIME = {
        "coverage": "website/coverage",
        "coverage-badge.json": "website/coverage-badge.json",
    }

    #: The retired Pages *host*, deliberately without the ``/keel`` path. GitHub
    #: keeps 301-ing the old URL once website/CNAME is set, so nothing visibly
    #: breaks — which is precisely why a leftover goes unnoticed. Matching the
    #: bare host also catches references that never carried the path, such as the
    #: CSP ``img-src`` entries that were dead the moment ``'self'`` changed.
    RETIRED = "berkayturanci.github.io"

    #: This file necessarily contains both BASE and RETIRED as literals.
    SELF = "tests/test_site_seo.py"

    #: History, not live copy: it is supposed to record the old address.
    HISTORICAL = {"CHANGELOG.md"}

    TEXT_SUFFIXES = {".md", ".html", ".txt", ".xml", ".js", ".json", ".toml", ".yml", ".yaml"}

    @classmethod
    def _tracked_text_files(cls) -> list[pathlib.Path]:
        """Every tracked text file — `git ls-files`, so build output cannot leak in.

        Deriving the set instead of listing it is the whole point: the previous
        literal list is exactly what let three edited files go unguarded.
        """
        git = shutil.which("git")
        if git is None:  # pragma: no cover - env guard
            return []
        proc = subprocess.run(
            [git, "-C", str(REPO_ROOT), "ls-files", "-z"],
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
        if proc.returncode != 0:  # pragma: no cover - env guard
            return []
        out = []
        for rel in proc.stdout.split("\0"):
            if not rel or rel == cls.SELF or rel in cls.HISTORICAL:
                continue
            if pathlib.PurePosixPath(rel).suffix not in cls.TEXT_SUFFIXES:
                continue
            path = REPO_ROOT / rel
            if path.is_file():
                out.append(path)
        return out

    def _require_git(self) -> list[pathlib.Path]:
        files = self._tracked_text_files()
        if not files:  # pragma: no cover - env guard
            self.skipTest("git is unavailable or this is not a checkout")
        return files

    def _advertised(self) -> set[str]:
        urls: set[str] = set()
        for path in self._require_git():
            urls.update(
                re.findall(
                    re.escape(BASE) + r"([A-Za-z0-9._/-]*)",
                    path.read_text(encoding="utf-8", errors="ignore"),
                )
            )
        # A URL at the end of an English sentence swallows the full stop, and
        # `.` has to stay in the class above for "docs.html" to match at all.
        return {re.sub(r"[.,;:!?]+$", "", u) for u in urls}

    def test_every_advertised_url_is_published(self):
        advertised = self._advertised()
        self.assertTrue(advertised, "no site URLs found — the scan is broken")
        for suffix in sorted(advertised):
            if suffix == "":  # the homepage
                self.assertTrue((SITE / "index.html").is_file())
                continue
            if suffix.split("/")[0] in self.BUILD_TIME:
                continue  # covered by the build-step test below
            with self.subTest(url=BASE + suffix):
                self.assertTrue(
                    (SITE / suffix).is_file(),
                    f"{BASE}{suffix} is advertised but website/{suffix} does "
                    "not exist and no build step creates it, so the link 404s",
                )

    def test_build_time_paths_are_still_produced_by_the_workflow(self):
        """Pin that the step *runs* and names the path, not how it is spelled.

        Matching the command text verbatim failed on an equivalent rewrite while
        still passing when the step was disabled with ``if: false`` — brittle and
        loose at once.
        """
        workflow = yaml.safe_load(
            (REPO_ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        )
        steps = [
            step
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if "run" in step
        ]
        self.assertTrue(steps, "pages.yml has no run steps")
        for suffix, produced_path in self.BUILD_TIME.items():
            with self.subTest(url=f"{BASE}{suffix}"):
                owners = [s for s in steps if produced_path in s["run"]]
                self.assertTrue(
                    owners,
                    f"{BASE}{suffix} is advertised but no pages.yml step writes "
                    f"{produced_path} any more, so the link would 404",
                )
                for step in owners:
                    self.assertNotIn(
                        "if",
                        step,
                        f"the step producing {produced_path} is conditional, so "
                        f"{BASE}{suffix} can silently stop being published",
                    )

    def test_the_cname_pins_the_custom_domain(self):
        """Without this file GitHub serves the old address and drops the domain.

        Necessary, not sufficient: the repo's Pages custom-domain setting and the
        DNS records are equally load-bearing and live outside the repo.
        """
        path = SITE / "CNAME"
        self.assertTrue(
            path.is_file(),
            "website/CNAME is missing, so GitHub Pages would stop serving "
            f"{BASE} and fall back to the github.io address",
        )
        self.assertEqual(
            path.read_text(encoding="utf-8").strip(),
            BASE.removeprefix("https://").rstrip("/"),
        )

    def test_nothing_still_points_at_the_retired_pages_url(self):
        for path in self._require_git():
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertNotIn(
                    self.RETIRED,
                    path.read_text(encoding="utf-8", errors="ignore"),
                    "the retired address survives here; GitHub still 301s it, so "
                    "nothing visibly breaks and it would go unnoticed",
                )


if __name__ == "__main__":
    unittest.main()
