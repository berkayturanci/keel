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

import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

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
    """

    #: Paths the Pages workflow generates into website/ at build time, each
    #: mapped to the workflow text that must still produce it. Committed files
    #: are checked on disk; these cannot be, so pin their build step instead.
    BUILD_TIME = {
        "coverage/": "coverage html -d website/coverage",
        "coverage-badge.json": 'open("website/coverage-badge.json", "w")',
    }

    #: The retired Pages address. GitHub keeps 301-ing it once website/CNAME is
    #: set, so nothing breaks immediately — which is precisely why a leftover
    #: would go unnoticed.
    RETIRED = "berkayturanci.github.io/keel"

    def _advertised(self) -> set[str]:
        sources = [REPO_ROOT / "README.md"]
        sources += sorted((REPO_ROOT / "docs").rglob("*.md"))
        sources += sorted(SITE.glob("*.html"))
        sources += [SITE / "llms.txt", SITE / "robots.txt", SITE / "sitemap.xml"]
        urls: set[str] = set()
        for path in sources:
            if not path.is_file():
                continue
            urls.update(
                re.findall(
                    re.escape(BASE) + r"([A-Za-z0-9._/-]*)",
                    path.read_text(encoding="utf-8"),
                )
            )
        return urls

    def test_every_advertised_url_is_published(self):
        advertised = self._advertised()
        self.assertTrue(advertised, "no site URLs found — the scan is broken")
        for suffix in sorted(advertised):
            with self.subTest(url=BASE + suffix):
                if suffix == "":
                    self.assertTrue((SITE / "index.html").is_file())
                elif suffix in self.BUILD_TIME:
                    continue  # covered by the build-step test below
                else:
                    self.assertTrue(
                        (SITE / suffix).is_file(),
                        f"{BASE}{suffix} is advertised but website/{suffix} does "
                        "not exist and no build step creates it, so the link 404s",
                    )

    def test_build_time_paths_are_still_produced_by_the_workflow(self):
        workflow = (REPO_ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        for suffix, step in self.BUILD_TIME.items():
            with self.subTest(url=BASE + suffix):
                self.assertIn(
                    step,
                    workflow,
                    f"{BASE}{suffix} is advertised but pages.yml no longer "
                    f"produces it ({step!r} is gone), so the link would 404",
                )

    def test_the_cname_pins_the_custom_domain(self):
        """Without this file GitHub serves the old address and drops the domain."""
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
        for path in [
            REPO_ROOT / "README.md",
            REPO_ROOT / "pyproject.toml",
            REPO_ROOT / ".claude-plugin/plugin.json",
            REPO_ROOT / ".codex-plugin/plugin.json",
            REPO_ROOT / "editors/vscode/extension.js",
            *sorted(SITE.glob("*.html")),
            *sorted(SITE.glob("*.txt")),
            SITE / "sitemap.xml",
        ]:
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertNotIn(self.RETIRED, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
