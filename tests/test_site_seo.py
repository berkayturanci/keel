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
                    len(title.group(1).replace("&amp;", "&")), 70,
                    f"{page} title is longer than a search result shows",
                )
                description = re.search(
                    r'<meta name="description" content="([^"]+)"', head
                )
                self.assertIsNotNone(description, f"{page} has no meta description")
                self.assertLessEqual(
                    len(description.group(1)), 165,
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
                    r'rel="canonical"\s+href="https://berkayturanci\.github\.io/keel/',
                )


class TestSitemap(unittest.TestCase):
    def _locs(self) -> set[str]:
        root = ET.parse(SITE / "sitemap.xml").getroot()
        return {e.text.strip() for e in root.iter(f"{SITEMAP_NS}loc") if e.text}

    def test_the_sitemap_lists_every_indexed_page(self):
        locs = self._locs()
        self.assertTrue(locs, "the sitemap is empty")
        for page in INDEXED_PAGES:
            expected = "https://berkayturanci.github.io/keel/" + (
                "" if page == "index.html" else page
            )
            with self.subTest(page=page):
                self.assertIn(expected, locs, f"{page} is not in the sitemap")

    def test_the_sitemap_does_not_list_the_error_page(self):
        self.assertNotIn("https://berkayturanci.github.io/keel/404.html", self._locs())

    def test_robots_points_at_the_sitemap(self):
        # How a crawler finds it without being told.
        robots = (SITE / "robots.txt").read_text(encoding="utf-8")
        self.assertIn(
            "Sitemap: https://berkayturanci.github.io/keel/sitemap.xml", robots
        )


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


if __name__ == "__main__":
    unittest.main()
