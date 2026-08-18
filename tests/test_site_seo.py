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

Since the keel-ship.dev move this module also pins URL resolution (everything
advertised in prose must be published), the publish chain in pages.yml, the
CNAME consistency anchor, and the accuracy of the publicly-served analytics
prose in website/README.md.
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

    #: This file necessarily contains both BASE and RETIRED as literals. It only
    #: needs excluding because ``.py`` is scanned — a URL can rot in a docstring
    #: or a default just as easily as in prose.
    SELF = "tests/test_site_seo.py"

    #: A changelog is *supposed* to record the address the project used to have,
    #: so it is exempt from the retired-host scan only. It is still scanned for
    #: advertised URLs, because a dead link is dead wherever it is written.
    HISTORICAL = {"CHANGELOG.md"}

    #: Everything served or shipped that can carry a URL. ``.css`` (``url(...)``),
    #: ``.svg`` and ``.webmanifest`` are all inside the Pages artifact; ``.py``
    #: ships in the package.
    TEXT_SUFFIXES = {
        ".css",
        ".html",
        ".js",
        ".json",
        ".md",
        ".py",
        ".svg",
        ".toml",
        ".txt",
        ".webmanifest",
        ".xml",
        ".yaml",
        ".yml",
    }

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
            if not rel or rel == cls.SELF:
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
        """Pin that the step *runs* and writes the path, not how it is spelled.

        Matching the command text verbatim was brittle and loose at once: an
        equivalent rewrite failed it, while ``if: false`` on the step kept it
        green. Matching the path as a bare substring then swapped one hole for
        another — ``website/coverage-badge.json`` contains ``website/coverage``,
        so deleting the command that builds the report left the guard green. The
        match therefore has to end at a path boundary.

        Known limit, accepted: a step that merely *names* the path (``ls``,
        ``echo``) satisfies this. Pinning the exact producing command shape is
        the verbatim-text brittleness this test just escaped; the boundary drawn
        here is "a step still references the path and the whole publish chain is
        unconditional".
        """
        workflow = yaml.safe_load(
            (REPO_ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        )
        owners_of = {
            suffix: [
                (job_name, job, step)
                for job_name, job in workflow["jobs"].items()
                for step in job.get("steps", [])
                if "run" in step and re.search(rf"{re.escape(path)}(?![\w.-])", step["run"])
            ]
            for suffix, path in self.BUILD_TIME.items()
        }
        for suffix, path in self.BUILD_TIME.items():
            with self.subTest(url=f"{BASE}{suffix}"):
                owners = owners_of[suffix]
                self.assertTrue(
                    owners,
                    f"{BASE}{suffix} is advertised but no pages.yml step names "
                    f"{path} any more, so the link would 404",
                )
                for job_name, job, step in owners:
                    for holder, label in ((step, "step"), (job, f"job {job_name!r}")):
                        for key in ("if", "continue-on-error"):
                            self.assertNotIn(
                                key,
                                holder,
                                f"the {label} producing {path} carries {key!r}, "
                                f"so {BASE}{suffix} can silently stop being "
                                "published",
                            )

        # Producing the file is one link of three: build -> upload -> deploy.
        # `if: false` on the deploy job publishes nothing while every producing
        # step stays green, so the whole chain must be unconditional.
        publish_markers = ("upload-pages-artifact", "deploy-pages")
        chain = [
            (job_name, job, step)
            for job_name, job in workflow["jobs"].items()
            for step in job.get("steps", [])
            if any(m in str(step.get("uses", "")) for m in publish_markers)
        ]
        for marker in publish_markers:
            self.assertTrue(
                any(marker in str(step.get("uses", "")) for _, _, step in chain),
                f"pages.yml no longer has a {marker} step, so the site cannot be published at all",
            )
        for job_name, job, step in chain:
            for holder, label in ((step, "step"), (job, f"job {job_name!r}")):
                for key in ("if", "continue-on-error"):
                    self.assertNotIn(
                        key,
                        holder,
                        f"the publish-chain {label} ({step.get('uses')}) carries "
                        f"{key!r}, so the site can silently stop being published",
                    )

    def test_the_cname_pins_the_custom_domain(self):
        """The consistency anchor every other URL in the repo derives from.

        Under this repo's Actions-based Pages publishing GitHub *ignores* the
        CNAME file — the real switch is the Pages custom-domain setting
        (`gh api -X PUT repos/<owner>/<repo>/pages -f cname=...`), which lives
        outside the repo, alongside the DNS records. The file still earns its
        pin: BASE, the manifests and test_install's expected homepage all
        derive from it, and under a future move to branch publishing it would
        become load-bearing for real.
        """
        path = SITE / "CNAME"
        self.assertTrue(
            path.is_file(),
            "website/CNAME is missing — it is the consistency anchor the "
            f"tests derive {BASE} from (and load-bearing under branch "
            "publishing)",
        )
        self.assertEqual(
            path.read_text(encoding="utf-8").strip(),
            BASE.removeprefix("https://").rstrip("/"),
        )

    def test_nothing_still_points_at_the_retired_pages_url(self):
        for path in self._require_git():
            if path.relative_to(REPO_ROOT).as_posix() in self.HISTORICAL:
                continue
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertNotIn(
                    self.RETIRED,
                    path.read_text(encoding="utf-8", errors="ignore"),
                    "the retired address survives here; GitHub still 301s it, so "
                    "nothing visibly breaks and it would go unnoticed",
                )


class TestAnalyticsDocMatchesReality(unittest.TestCase):
    """website/README.md is inside the Pages artifact, so it is served publicly.

    It already carried one publicly-served fiction — a GA4 + Consent Mode setup
    no page has ever had. The replacement then claimed *every* page loads the
    Cloudflare beacon, which was also untrue. Documentation nobody can check
    drifts; pin it to the files instead.
    """

    BEACON = "beacon.min.js"

    def _pages_with_beacon(self) -> set[str]:
        return {
            f.name
            for f in sorted(SITE.glob("*.html"))
            if self.BEACON in f.read_text(encoding="utf-8")
        }

    def test_no_page_carries_google_analytics(self):
        for f in sorted(SITE.glob("*.html")):
            with self.subTest(page=f.name):
                text = f.read_text(encoding="utf-8")
                for marker in ("gtag(", "googletagmanager"):
                    self.assertNotIn(
                        marker,
                        text,
                        "website/README.md states there is no Google Analytics "
                        "on this site; add it back to the docs if that changed",
                    )

    def _analytics_section(self) -> str:
        """Only the ``## Analytics`` section — the whole README also contains a
        file-inventory table naming every page, which would satisfy a whole-file
        search for reasons that have nothing to do with analytics (the same
        substring-satisfaction defect this suite has now grown twice)."""
        readme = (SITE / "README.md").read_text(encoding="utf-8")
        start = readme.index("## Analytics")
        end = readme.find("\n## ", start + 1)
        return readme[start:end] if end != -1 else readme[start:]

    def test_the_analytics_section_names_the_pages_without_a_beacon(self):
        all_pages = {f.name for f in sorted(SITE.glob("*.html"))}
        without = all_pages - self._pages_with_beacon()
        section = self._analytics_section()
        for page in sorted(without):
            with self.subTest(page=page):
                self.assertIn(
                    page,
                    section,
                    f"{page} carries no analytics beacon but the published "
                    "Analytics section does not say so — it would be claiming "
                    "coverage the site does not have",
                )

    def test_the_analytics_section_counts_match_the_files(self):
        """Pin "Four of the five" to the files so the prose cannot go stale."""
        all_pages = {f.name for f in sorted(SITE.glob("*.html"))}
        with_beacon = self._pages_with_beacon()
        words = "zero one two three four five six seven eight nine".split()
        self.assertLess(len(all_pages), len(words), "extend the words list for the count pin")
        expected = f"{words[len(with_beacon)].capitalize()} of the {words[len(all_pages)]} pages"
        self.assertIn(
            expected,
            self._analytics_section(),
            "the Analytics section's page count no longer matches the files",
        )


if __name__ == "__main__":
    unittest.main()
