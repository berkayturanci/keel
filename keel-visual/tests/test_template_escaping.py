"""Every value a template interpolates into markup goes through ``esc()``.

swarm.html built its cards with JS template literals assigned to ``innerHTML``
and interpolated run data raw, so a worker's details (child keel ship output,
which can quote issue and PR text) ran as markup when the page opened. This test
reads each template's inline scripts, finds every template literal that builds
markup, and requires each ``${...}`` in it to be an ``esc(...)`` or
``statusClass(...)`` call, or one of the few allow-listed expressions below. A new
raw ``${...}`` in a markup-building template literal fails here, with node or
without it.

What this scanner does not see, so it is not the guard for them: markup built by
string concatenation (runviz.html's pattern — ``'<b>' + x``), a literal with no tag
of its own assigned to ``innerHTML`` (``el.innerHTML = `${x}` ``), an allow-listed
name rebound to a raw value, and unquoted-attribute or URL contexts. The behaviour
itself, including runviz.html, is pinned by ``tests/js/swarm.test.mjs`` and
``tests/js/runviz.test.mjs``, which feed hostile values through the real scripts.
"""

from __future__ import annotations

import re
import unittest
from importlib import resources

TEMPLATES = ("board.html", "dashboard.html", "runviz.html", "swarm.html")

# A literal builds markup when its static text opens or closes a tag.
_MARKUP = re.compile(r"</?[A-Za-z]")
# HTML tag names are case-insensitive and a browser ends a script at `</script` whatever
# follows before `>`, so `<SCRIPT>` and `</script foo>` are script blocks too; missing
# them would leave a whole block unscanned.
_SCRIPT = re.compile(r"<script(\s[^>]*)?>(.*?)</script[^>]*>", re.S | re.I)
_SAFE_CALL = re.compile(r"(esc|statusClass)\(")
# Expressions that are safe without esc(), each for a reason the test can state:
# the two mode values are one of two string constants, and the *Html/*Block values are
# markup literals built just above, which this test checks in their own right
# (``test_allow_listed_markup_is_built_from_literals`` holds that). A nested literal is
# shown as `...`; it is checked separately as its own literal.
SWARM_MARKUP_NAMES = (
    "issuesHtml",
    "issueRowsHtml",
    "scopeHtml",
    "depsHtml",
    "depsBlock",
    "bandHtml",
    "implHtml",
)
ALLOWED = {
    "swarm.html": {
        "modeClass",
        "modeLabel",
        *SWARM_MARKUP_NAMES,
        "w.details ? `...` : ''",
        # A worker's wave and stage (keel #1280); each nested literal escapes its value.
        "w.wave ? `...` : ''",
        "w.stage ? `...` : ''",
    },
}


def inline_scripts(html: str) -> list[str]:
    return [m.group(2) for m in _SCRIPT.finditer(html) if "src=" not in (m.group(1) or "").lower()]


def _skip_quoted(src: str, i: int) -> int:
    """Index just past the '...' or "..." string that starts at ``src[i]``."""
    quote = src[i]
    i += 1
    while src[i] != quote:
        i += 2 if src[i] == "\\" else 1
    return i + 1


def _read_literal(src: str, i: int, found: list[tuple[str, list[str]]]) -> int:
    """Parse the template literal opening at ``src[i]``; record it and any nested ones.

    Records ``(static_text, [expressions])`` in ``found`` and returns the index just
    past the closing backtick. Nested literals inside an expression are recorded too
    and appear in the outer expression as ```...```.
    """
    text: list[str] = []
    exprs: list[str] = []
    i += 1
    while src[i] != "`":
        if src[i] == "\\":
            text.append(src[i : i + 2])
            i += 2
        elif src.startswith("${", i):
            i += 2
            depth = 0
            expr: list[str] = []
            while depth or src[i] != "}":
                ch = src[i]
                if ch == "`":
                    i = _read_literal(src, i, found)
                    expr.append("`...`")
                    continue
                if ch in "'\"":
                    end = _skip_quoted(src, i)
                    expr.append(src[i:end])
                    i = end
                    continue
                depth += {"{": 1, "}": -1}.get(ch, 0)
                expr.append(ch)
                i += 1
            exprs.append("".join(expr).strip())
            i += 1
        else:
            text.append(src[i])
            i += 1
    found.append(("".join(text), exprs))
    return i + 1


def template_literals(src: str) -> list[tuple[str, list[str]]]:
    """Every template literal in ``src``, nested ones included.

    The scan looks only for backticks, so the templates keep them out of comments,
    strings and regular expressions; ``test_backticks_only_open_literals`` holds it
    for comments, the one place one has appeared.
    """
    found: list[tuple[str, list[str]]] = []
    i = 0
    while i < len(src):
        i = _read_literal(src, i, found) if src[i] == "`" else i + 1
    return found


def is_single_safe_call(expr: str) -> bool:
    """True when ``expr`` is exactly one ``esc(...)``/``statusClass(...)`` call."""
    m = _SAFE_CALL.match(expr)
    if not m:
        return False
    depth = 0
    for pos in range(m.end() - 1, len(expr)):
        depth += {"(": 1, ")": -1}.get(expr[pos], 0)
        if depth == 0:
            return pos == len(expr) - 1
    return False


def raw_interpolations(name: str, html: str) -> list[str]:
    """The markup interpolations in ``html`` that neither escape nor are allow-listed."""
    allowed = ALLOWED.get(name, set())
    bad = []
    for src in inline_scripts(html):
        for text, exprs in template_literals(src):
            if not _MARKUP.search(text):
                continue
            bad += [e for e in exprs if not is_single_safe_call(e) and e not in allowed]
    return bad


def load(name: str) -> str:
    return resources.files("keel_visual.templates").joinpath(name).read_text(encoding="utf-8")


class TestTemplateEscaping(unittest.TestCase):
    def test_every_markup_interpolation_is_escaped(self) -> None:
        for name in TEMPLATES:
            with self.subTest(template=name):
                self.assertEqual(raw_interpolations(name, load(name)), [])

    def test_swarm_markup_literals_are_found(self) -> None:
        # The scan must see the swarm cards, or the check above passes vacuously.
        literals = [t for s in inline_scripts(load("swarm.html")) for t in template_literals(s)]
        markup = [exprs for text, exprs in literals if _MARKUP.search(text)]
        self.assertGreaterEqual(len(markup), 6)
        self.assertIn("esc(w.details)", [e for exprs in markup for e in exprs])

    def test_allow_listed_mode_values_are_constants(self) -> None:
        src = load("swarm.html")
        self.assertIn(
            "const modeClass = w.eligible_direct_landing ? 'parallel' : 'sequential';", src
        )
        self.assertIn(
            "const modeLabel = w.eligible_direct_landing ? 'Orthogonal Parallel' : "
            "'Dependent — Refused';",
            src,
        )

    def test_allow_listed_markup_is_built_from_literals(self) -> None:
        # Each allow-listed name is assigned exactly once, and outside its template
        # literals (whose interpolations the scan checks) and quoted strings its right-hand
        # side names only local lists, array methods and other allow-listed names — never a
        # plan value. A new plan field has to arrive through esc() to be shown (#1275).
        src = load("swarm.html")
        known = {
            *SWARM_MARKUP_NAMES,
            *("issues", "scope", "deps", "band", "implName", "scopes", "num", "s"),
            *("map", "filter", "join", "slice", "length", "String", "typeof"),
        }
        for name in SWARM_MARKUP_NAMES:
            with self.subTest(name=name):
                self.assertEqual(len(re.findall(rf"\b{name}\s*\+?=(?!=)", src)), 1, name)
                (rhs,) = re.findall(rf"\bconst {name} = ([^;]*);", src)
                outside = re.sub(r"`[^`]*`|'[^']*'", "", rhs)
                stray = set(re.findall(r"[A-Za-z_]\w*", outside)) - known
                self.assertEqual(stray, set(), f"{name} = {rhs}")

    def test_plan_values_the_dag_shows_are_escaped(self) -> None:
        # The fields the DAG gained when it began drawing the persisted plan (#1275).
        src = load("swarm.html")
        for expr in (
            "esc(scopes[String(num)].title || '')",
            "esc(scopes[String(num)].scope_source || '')",
            "esc(band)",
            "esc(implName)",
            "esc(num)",
        ):
            self.assertIn("${" + expr + "}", src)
        # The no-plan sentence carries a path and a reason; it is text, never markup.
        self.assertIn("noticeEl.textContent = notice;", src)
        self.assertNotIn("noticeEl.innerHTML", src)

    def test_the_swarm_view_never_calls_a_dependent_wave_a_funnel(self) -> None:
        # keel swarm-land refuses a dependent wave (#1276); a "funnel" label
        # promises a rebase the CLI never performs.
        self.assertNotIn("funnel", load("swarm.html").lower())

    def test_backticks_only_open_literals(self) -> None:
        # A backtick in a // comment would open a literal the scan invents.
        for name in TEMPLATES:
            with self.subTest(template=name):
                for src in inline_scripts(load(name)):
                    lines = [ln.strip() for ln in src.splitlines()]
                    self.assertFalse([ln for ln in lines if ln.startswith("//") and "`" in ln])

    def test_raw_interpolation_is_reported(self) -> None:
        html = "<script>el.innerHTML = `<b>${esc(a)}</b><i>${b}</i>`;</script>"
        self.assertEqual(raw_interpolations("x.html", html), ["b"])

    def test_nested_literal_is_checked_on_its_own(self) -> None:
        html = "<script>x.innerHTML = `<p>${c ? `<i>${d}</i>` : ''}</p>`;</script>"
        self.assertEqual(raw_interpolations("x.html", html), ["d", "c ? `...` : ''"])

    def test_a_call_followed_by_more_is_not_safe(self) -> None:
        self.assertTrue(is_single_safe_call("esc(a || 'b')"))
        self.assertTrue(is_single_safe_call("statusClass(w.status)"))
        self.assertFalse(is_single_safe_call("esc(a) + b"))
        self.assertFalse(is_single_safe_call("escape(a)"))
        self.assertFalse(is_single_safe_call("esc(a"))
        self.assertFalse(is_single_safe_call("a"))

    def test_quoted_braces_and_escapes_do_not_end_an_expression(self) -> None:
        src = '`<b>${esc(a || \'}\')}</b><i>\\${x}</i>${esc("q\\"")}`'
        expected = [("<b></b><i>\\${x}</i>", ["esc(a || '}')", 'esc("q\\"")'])]
        self.assertEqual(template_literals(src), expected)

    def test_non_markup_literals_are_ignored(self) -> None:
        html = "<script>n.className = `card ${st}`; f = `${n}px monospace`;</script>"
        self.assertEqual(raw_interpolations("x.html", html), [])

    def test_external_scripts_are_skipped(self) -> None:
        for html in ('<script src="x.js">`<b>${a}</b>`</script>', '<SCRIPT SRC="x.js"></SCRIPT>'):
            with self.subTest(html=html):
                self.assertEqual(inline_scripts(html), [])

    def test_script_tags_are_matched_in_any_case(self) -> None:
        html = "<SCRIPT>`<b>${a}</b>`</Script foo>"
        self.assertEqual(inline_scripts(html), ["`<b>${a}</b>`"])
        self.assertEqual(raw_interpolations("x.html", html), ["a"])


if __name__ == "__main__":
    unittest.main()
