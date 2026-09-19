"""Each page's Content Security Policy allows what that page actually loads (#1230).

#540 added a CSP to every page with `connect-src 'self' https://cloudflareinsights.com`.
`app.js` had fetched the latest release from api.github.com — falling back to pypi.org — since
#307, so from that day the browser refused both requests on every page view: four console
errors per visit, and a version refresh `docs/keel/release.md` still describes that had not
run for two months. Nothing visible broke, because the build-time version stamps are release
surfaces and always current. That is why a policy and the code it governs could drift apart
unnoticed: nothing compared them.

This derives what each page needs from the page itself and the local scripts it loads, and
requires the page's own policy to allow it. It reads the files; it does not run a browser.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from urllib.parse import urlsplit

WEBSITE = Path(__file__).resolve().parent.parent / "website"

_CSP = re.compile(r'<meta[^>]+http-equiv="Content-Security-Policy"[^>]+content="([^"]*)"', re.I)
_TAG = re.compile(r"<(script|link|img)\b([^>]*)>", re.I)
_ATTR = re.compile(r"""([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
#: `fetch("https://…")` with a literal URL. A computed URL cannot be read from the source,
#: and the site has none; `test_every_fetch_in_a_site_script_is_a_literal` holds it to that.
_FETCH = re.compile(r"""\bfetch\(\s*["'](https?://[^"']+)["']""")
_ANY_FETCH = re.compile(r"\bfetch\(\s*([^)\s][^,)]*)")


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _policy(page: Path) -> dict[str, set[str]]:
    """The page's CSP as ``{directive: {source, …}}``; empty when the page declares none."""
    match = _CSP.search(page.read_text(encoding="utf-8"))
    directives: dict[str, set[str]] = {}
    for part in (match.group(1) if match else "").split(";"):
        words = part.split()
        if words:
            directives[words[0]] = set(words[1:])
    return directives


def _allowed(policy: dict[str, set[str]], directive: str) -> set[str]:
    return policy.get(directive, policy.get("default-src", set()))


def _needs(page: Path) -> list[tuple[str, str, str]]:
    """``(directive, origin, where)`` for every external resource ``page`` loads."""
    text = page.read_text(encoding="utf-8")
    needs: list[tuple[str, str, str]] = []
    scripts = [text]
    for tag, raw in _TAG.findall(text):
        attrs = {name.lower(): a or b for name, a, b in _ATTR.findall(raw)}
        tag = tag.lower()
        url = attrs.get("href" if tag == "link" else "src", "")
        if tag == "script" and url and not re.match(r"https?://", url):
            local = WEBSITE / url.split("?")[0]
            if local.is_file():
                scripts.append(local.read_text(encoding="utf-8"))
            continue
        if not re.match(r"https?://", url):
            continue
        if tag == "script":
            needs.append(("script-src", _origin(url), f"<script src={url}>"))
        elif tag == "img":
            needs.append(("img-src", _origin(url), f"<img src={url}>"))
        elif "stylesheet" in attrs.get("rel", "").lower():
            needs.append(("style-src", _origin(url), f"<link stylesheet {url}>"))
    for source in scripts:
        for url in _FETCH.findall(source):
            needs.append(("connect-src", _origin(url), f"fetch({url})"))
    return needs


def _pages() -> list[Path]:
    return sorted(WEBSITE.glob("*.html"))


class TestEachPagesPolicyAllowsWhatItLoads(unittest.TestCase):
    def test_every_external_resource_is_allowed_by_the_pages_own_policy(self):
        refused = []
        checked = 0
        for page in _pages():
            policy = _policy(page)
            if not policy:
                continue
            for directive, origin, where in _needs(page):
                checked += 1
                if origin not in _allowed(policy, directive):
                    refused.append(f"{page.name}: {directive} does not allow {origin} — {where}")
        # Vacuity: patterns that matched nothing would pass every page.
        self.assertGreater(checked, 15)
        self.assertEqual([], refused, "\n".join(refused))

    def test_the_version_refresh_is_one_of_the_things_checked(self):
        # The case this file exists for, named: app.js's two fetches, on a page loading it.
        needs = {(d, o) for d, o, _ in _needs(WEBSITE / "index.html")}
        self.assertIn(("connect-src", "https://api.github.com"), needs)
        self.assertIn(("connect-src", "https://pypi.org"), needs)

    def test_a_page_that_loads_no_such_script_keeps_the_narrow_policy(self):
        # 404.html and the article load no local script, so nothing there fetches; widening
        # their policy to match the others would allow what they have no use for.
        for name in ("404.html", "silent-revert.html"):
            with self.subTest(page=name):
                connect = _allowed(_policy(WEBSITE / name), "connect-src")
                self.assertNotIn("https://api.github.com", connect)
                self.assertNotIn("https://pypi.org", connect)

    def test_every_fetch_in_a_site_script_is_a_literal(self):
        # A fetch whose URL is computed is invisible to `_FETCH`, and so to this whole file.
        # Relative URLs are same-origin ('self') and need nothing.
        computed = []
        for script in sorted([*WEBSITE.glob("*.js"), *WEBSITE.glob("*.html")]):
            for argument in _ANY_FETCH.findall(script.read_text(encoding="utf-8")):
                argument = argument.strip()
                if not re.match(r"""["'](https?://|[^"':]*["'])""", argument):
                    computed.append(f"{script.name}: fetch({argument}")
        self.assertEqual([], computed, "name the URL literally so the CSP check can see it")

    def test_the_parser_reads_a_policy_and_a_page(self):
        policy = _policy(WEBSITE / "index.html")
        self.assertIn("'self'", policy["default-src"])
        self.assertIn("https://static.cloudflareinsights.com", policy["script-src"])
        # A directive the policy omits falls back to default-src, as the browser does.
        self.assertEqual(_allowed({"default-src": {"'self'"}}, "img-src"), {"'self'"})
        self.assertEqual(
            _origin("https://api.github.com/repos/x/y?per_page=30"), "https://api.github.com"
        )


if __name__ == "__main__":
    unittest.main()
