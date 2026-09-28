"""The coverage page shows figures generated from the coverage run, never typed (#1320).

`website/content.js` fed `/coverage.html` 1,840 statements, 612 branches, seven modules
and `cli.py` at 376 statements. The report one click away said 16,032, 5,600, 73 files
and 4,310. Those numbers had been typed by hand and nothing compared them with anything.

`scripts/coverage_page_data.py` now reduces `coverage json` to the page's data at
site-build time, and `coverage.js` fetches it. This file pins the four things that make
that true:

1. the generator reads the real report shape and computes the figures correctly;
2. the Pages workflow and `make site` actually run it, into the file the page fetches;
3. no hand-typed statement or branch count comes back into the page's sources;
4. the page script renders the generated figures, and shows none when the file is
   absent or stale — driven under node against a stub DOM, not grepped.

`scripts/` is maintenance tooling outside the coverage gate, so these tests are what
hold it.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE = REPO_ROOT / "website"
_SCRIPT = REPO_ROOT / "scripts" / "coverage_page_data.py"
_spec = importlib.util.spec_from_file_location("coverage_page_data", _SCRIPT)
cpd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpd)

#: The path the page fetches and the build writes. Read from the page script below, so
#: the two cannot be renamed apart without a test noticing.
DATA_FILE = "coverage-summary.json"


def _summary(statements, missing, branches, covered_branches, partial=0):
    return {
        "covered_lines": statements - missing,
        "num_statements": statements,
        "percent_covered": 0.0,
        "missing_lines": missing,
        "excluded_lines": 0,
        "num_branches": branches,
        "num_partial_branches": partial,
        "covered_branches": covered_branches,
        "missing_branches": branches - covered_branches,
    }


def _report(**overrides):
    """A `coverage json` (format 3) report whose two files have different figures."""
    report = {
        "meta": {
            "format": 3,
            "version": "7.10.0",
            "timestamp": "2026-09-23T04:05:06.789012",
            "branch_coverage": True,
            "show_contexts": False,
        },
        "files": {
            "src/keel/zeta.py": {"summary": _summary(40, 0, 10, 10)},
            "src/keel/cli.py": {"summary": _summary(4310, 3, 1196, 1190, partial=4)},
        },
        "totals": _summary(4350, 3, 1206, 1200, partial=4),
    }
    report.update(overrides)
    return report


class TestPercent(unittest.TestCase):
    def test_a_fraction_is_floored_so_it_never_reads_as_the_gate(self):
        self.assertEqual(cpd.percent(9999, 10000), 99.99)
        self.assertEqual(cpd.percent(99999, 100000), 99.99)
        self.assertEqual(cpd.percent(1, 3), 33.33)
        self.assertEqual(cpd.percent(10, 10), 100.0)

    def test_nothing_to_cover_is_fully_covered(self):
        self.assertEqual(cpd.percent(0, 0), 100.0)


class TestSummarize(unittest.TestCase):
    def test_the_totals_are_the_reports_not_a_sum_of_anything_typed(self):
        totals = cpd.summarize(_report())["totals"]
        self.assertEqual(totals["statements"], 4350)
        self.assertEqual(totals["missing"], 3)
        self.assertEqual(totals["branches"], 1206)
        self.assertEqual(totals["partial"], 4)
        self.assertEqual(totals["files"], 2)
        self.assertEqual(totals["line"], cpd.percent(4347, 4350))
        self.assertEqual(totals["branch"], cpd.percent(1200, 1206))
        # The pooled figure `fail_under` is compared against.
        self.assertEqual(totals["total"], cpd.percent(4347 + 1200, 4350 + 1206))

    def test_one_row_per_file_sorted_by_path(self):
        summary = cpd.summarize(_report())
        self.assertEqual(summary["columns"][0], "file")
        self.assertEqual(
            summary["files"],
            [
                ["src/keel/cli.py", 4310, 3, 1196, 4, cpd.percent(4307, 4310), 99.49],
                ["src/keel/zeta.py", 40, 0, 10, 0, 100.0, 100.0],
            ],
        )

    def test_the_timestamp_is_the_reports_own(self):
        self.assertEqual(cpd.summarize(_report())["generated"], "2026-09-23T04:05:06.789012")
        self.assertIsNone(cpd.summarize(_report(meta={}))["generated"])
        self.assertEqual(cpd.summarize(_report())["schema"], cpd.SCHEMA)

    def test_a_windows_path_is_written_with_forward_slashes(self):
        report = _report(files={"src\\keel\\cli.py": {"summary": _summary(5, 0, 2, 2)}})
        self.assertEqual(cpd.summarize(report)["files"][0][0], "src/keel/cli.py")

    def test_an_unreadable_report_is_refused_rather_than_shown_as_zeros(self):
        bad_totals = _summary(10, 0, 4, 4)
        del bad_totals["num_branches"]
        cases = {
            "not an object": [],
            "no files": _report(files={}),
            "files not a mapping": _report(files=[]),
            "no totals": _report(totals=None),
            "a count missing": _report(totals=bad_totals),
            "a boolean count": _report(totals={**_summary(1, 0, 0, 0), "missing_lines": True}),
            "a negative count": _report(totals={**_summary(1, 0, 0, 0), "missing_lines": -1}),
            "missing exceeds statements": _report(totals=_summary(1, 2, 0, 0)),
            "covered exceeds branches": _report(totals=_summary(1, 0, 1, 2)),
            "a file entry not an object": _report(files={"src/keel/a.py": 7}),
            "measured without branches": _report(meta={"branch_coverage": False}),
        }
        for name, report in cases.items():
            with self.subTest(name), self.assertRaises(cpd.ReportError):
                cpd.summarize(report)

    def test_render_is_stable_compact_json(self):
        summary = cpd.summarize(_report())
        text = cpd.render(summary)
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(json.loads(text), summary)
        self.assertEqual(text, cpd.render(cpd.summarize(_report())))
        self.assertNotIn(" ", text.replace("T04", ""))  # compact separators


class TestMain(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / DATA_FILE

    def test_it_writes_the_page_data(self):
        report = self.tmp / "coverage.json"
        report.write_text(json.dumps(_report()), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as said:
            self.assertEqual(cpd.main([str(report), str(self.out)]), 0)
        self.assertEqual(json.loads(self.out.read_text(encoding="utf-8")), cpd.summarize(_report()))
        self.assertIn("2 files, 4350 statements, 1206 branches", said.getvalue())

    def test_a_bad_report_fails_the_build_and_writes_nothing(self):
        for name, body in {"missing": None, "not json": "{", "wrong shape": "[]"}.items():
            with self.subTest(name):
                report = self.tmp / f"{name}.json"
                if body is not None:
                    report.write_text(body, encoding="utf-8")
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(cpd.main([str(report), str(self.out)]), 1)
                self.assertIn("coverage_page_data:", err.getvalue())
                self.assertFalse(self.out.exists())


def _page_script_data_file() -> str:
    found = re.findall(
        r"""\bfetch\(\s*["']([^"']+\.json)["']""", (SITE / "coverage.js").read_text()
    )
    return found[0] if found else ""


class TestTheBuildWritesWhatThePageReads(unittest.TestCase):
    def test_the_page_fetches_the_generated_file(self):
        self.assertEqual(_page_script_data_file(), DATA_FILE)

    def test_the_pages_workflow_runs_the_generator_unconditionally(self):
        workflow = yaml.safe_load(
            (REPO_ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        )
        # A change to the generator alone must redeploy the page it writes. PyYAML reads
        # the bare key `on` as True.
        trigger = workflow.get("on", workflow.get(True))
        self.assertIn("scripts/coverage_page_data.py", trigger["push"]["paths"])
        job = workflow["jobs"]["build"]
        steps = job["steps"]
        runs = [
            i
            for i, step in enumerate(steps)
            if re.search(
                r"python scripts/coverage_page_data\.py coverage\.json "
                rf"website/{re.escape(DATA_FILE)}(?![\w.-])",
                step.get("run", ""),
            )
        ]
        self.assertEqual(len(runs), 1, "no pages.yml build step writes the coverage page data")
        step = steps[runs[0]]
        # Its input is produced first, in the same script.
        script = step["run"]
        self.assertLess(
            script.index("coverage json -o coverage.json"),
            script.index("scripts/coverage_page_data.py"),
        )
        for holder in (step, job):
            self.assertNotIn("if", holder)
            self.assertNotIn("continue-on-error", holder)
        # ...and before the site is packaged, from the directory it writes into.
        upload = [i for i, s in enumerate(steps) if "upload-pages-artifact" in s.get("uses", "")]
        self.assertEqual(len(upload), 1)
        self.assertLess(runs[0], upload[0])
        self.assertEqual(steps[upload[0]]["with"]["path"], "website")

    def test_make_site_runs_it_too(self):
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        recipe = re.search(r"^site:\n((?:\t.*\n)+)", makefile, re.M)
        self.assertIsNotNone(recipe)
        body = recipe.group(1)
        self.assertIn("coverage json -o coverage.json", body)
        self.assertIn(f"scripts/coverage_page_data.py coverage.json website/{DATA_FILE}", body)
        self.assertLess(body.index("coverage json"), body.index("coverage_page_data.py"))
        # The server starts last: nothing after it runs until it is stopped.
        self.assertLess(body.index("coverage_page_data.py"), body.index("http.server"))

    def test_the_generated_file_is_not_committed(self):
        ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(f"website/{DATA_FILE}", ignored)


class TestNoHandTypedCoverageFigures(unittest.TestCase):
    """The regression the issue asks for: typed figures must not come back.

    Written against the shapes the old data took — ``statements: 1840``,
    ``["src/keel/cli.py", 100, 100, 376]`` — and the prose shape a page would use
    ("16,032 statements"), in every source the coverage page is built from.
    """

    SOURCES = ("content.js", "coverage.js", "coverage.html")

    PATTERNS = (
        # an object field holding a count: `statements: 1840`, `"branches": 612`
        re.compile(
            r"""["']?\b(?:statements|branches|missing|partial|modules|files)["']?\s*:\s*\d"""
        ),
        # a per-file row literal: `["src/keel/cli.py", 100, 100, 376]`
        re.compile(r"""\[\s*["'][^"']*\.py["']\s*,\s*\d"""),
        # prose: `1,840 statements`, `612 branches`, `73 files`
        re.compile(r"\b\d+(?:,\d{3})*\s+(?:statements|branches|modules|files)\b"),
    )

    def _hits(self, text: str) -> list[str]:
        return [m.group(0) for p in self.PATTERNS for m in p.finditer(text)]

    def test_the_patterns_catch_the_figures_this_replaced(self):
        # Guards the guard: each shape the old page used must be caught.
        old = (
            "overall: { line: 100, branch: 100, statements: 1840, missing: 0, branches: 612 },\n"
            'modules: [ ["src/keel/cli.py", 100, 100, 376] ],\n'
            "<p>1,840 statements across 7 modules</p>"
        )
        self.assertEqual(len(self._hits(old)), 6, self._hits(old))

    def test_no_coverage_source_types_a_count(self):
        hits = {
            name: self._hits((SITE / name).read_text(encoding="utf-8")) for name in self.SOURCES
        }
        self.assertEqual({k: v for k, v in hits.items() if v}, {})

    def test_content_js_carries_no_coverage_block(self):
        text = (SITE / "content.js").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(?m)^\s*coverage\s*:\s*\{")


#: Runs the real `coverage.js` against a stub DOM with a stubbed `fetch`, and reports
#: what the page ended up showing. `prefers-reduced-motion` is on, so every figure is
#: written at once instead of animated.
_DRIVER = r"""
const fs = require("fs"), vm = require("vm");
const [, , script, mode, payload] = process.argv;
function node(tag, id) {
  const n = { tag, id, className: "", textContent: "", hidden: false, dataset: {}, style: {},
    children: [], listeners: {},
    classList: { add() {}, contains(k) { return this.owner.className.split(" ").includes(k); } },
    appendChild(c) { this.children.push(c); return c; },
    addEventListener(k, f) { this.listeners[k] = f; },
    querySelectorAll(sel) {
      const out = [];
      const match = (n) => sel === "[data-n]" ? n.dataset.n !== undefined
        : sel.startsWith(".") ? n.className.split(" ").includes(sel.slice(1))
        : sel === "tr.cov-r" ? n.tag === "tr" && n.className === "cov-r" : false;
      const walk = (n) => n.children.forEach((c) => { if (match(c)) out.push(c); walk(c); });
      walk(this);
      return out;
    } };
  n.classList.owner = n;
  return n;
}
const ids = ["cov-ring-num", "cov-ring-arc", "cov-ring-cap", "cov-gate-state", "cov-date",
  "cov-date-wrap", "cov-summary", "cov-scope", "cov-tbody", "cov-tbl-wrap", "cov-nodata",
  "cov-report"];
const nodes = {};
ids.forEach((id) => { nodes[id] = node("div", id); });
// hidden in coverage.html until the script decides
["cov-summary", "cov-tbl-wrap", "cov-nodata", "cov-date-wrap"]
  .forEach((id) => { nodes[id].hidden = true; });
const document = { getElementById: (id) => nodes[id] || null, createElement: (t) => node(t) };
const fetch = (url) => {
  fetched.push(url);
  if (mode === "absent") return Promise.resolve({ ok: false, json: () => Promise.reject(0) });
  // "slow": the body takes 120 ms to parse, as on a loaded CI runner (#1375)
  const parse = () => {
    const until = Date.now() + (mode === "slow" ? 120 : 0);
    while (Date.now() < until) { /* bounded by the deadline above */ }
    return JSON.parse(payload);
  };
  return Promise.resolve({ ok: true, json: () => Promise.resolve().then(parse) });
};
const fetched = [];
const window = {};
// The page's timers are counted, and the page is read once none is left, not at a
// fixed moment: a fixed 50 ms raced the rows' own timers on a slow runner (#1375).
let pending = 0;
const pageTimeout = (f, ms) => { pending++; return setTimeout(() => { pending--; f(); }, ms); };
vm.runInNewContext(fs.readFileSync(script, "utf8"), {
  document, window, fetch, console, setTimeout: pageTimeout, Math, JSON, Promise,
  matchMedia: () => ({ matches: true }), requestAnimationFrame: (f) => pageTimeout(f, 0),
});
const settle = (tries) => {
  if (pending === 0) return report();
  if (tries >= 200) {
    console.error(`TIMEOUT: ${pending} page timer(s) still pending after 2 s`);
    process.exit(3);
  }
  setTimeout(() => settle(tries + 1), 10);
};
setTimeout(() => settle(0), 0);
function report() {
  const deep = (n) => n.textContent + n.children.map(deep).join("");
  const rows = nodes["cov-tbody"].querySelectorAll("tr.cov-r");
  console.log(JSON.stringify({
    fetched,
    nodata_hidden: nodes["cov-nodata"].hidden,
    summary_hidden: nodes["cov-summary"].hidden,
    table_hidden: nodes["cov-tbl-wrap"].hidden,
    date_hidden: nodes["cov-date-wrap"].hidden,
    ring: nodes["cov-ring-num"].textContent,
    ring_caption: nodes["cov-ring-cap"].textContent,
    gate_state: nodes["cov-gate-state"].textContent,
    cards: nodes["cov-summary"].children.map((c) => c.children.map(deep)),
    scope: nodes["cov-scope"].textContent,
    rows: rows.map((r) => r.children.map(deep)),
  }));
}
"""

NODE = shutil.which("node")


@unittest.skipUnless(NODE, "needs node to execute the page script")
class TestThePageRendersTheGeneratedData(unittest.TestCase):
    """The page script, run on the generator's own output — and on none."""

    def _drive(self, mode: str, payload: object = None) -> dict:
        workdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, workdir, True)
        driver = workdir / "drive.js"
        driver.write_text(_DRIVER, encoding="utf-8")
        done = subprocess.run(
            [NODE, str(driver), str(SITE / "coverage.js"), mode, json.dumps(payload)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_the_generated_figures_are_what_the_page_shows(self):
        shown = self._drive("data", cpd.summarize(_report()))
        self.assertEqual(shown["fetched"], [DATA_FILE])
        self.assertTrue(shown["nodata_hidden"])
        self.assertFalse(shown["summary_hidden"])
        self.assertFalse(shown["table_hidden"])
        self.assertFalse(shown["date_hidden"])
        self.assertEqual(
            shown["cards"],
            [
                ["99.93%", "line coverage"],
                ["99.5%", "branch coverage"],
                ["4,350", "statements"],
                ["1,206", "branches"],
            ],
        )
        self.assertEqual(shown["scope"], "2 files · line + branch")
        self.assertEqual(
            shown["rows"],
            [
                # file, statements, missing, branch, line
                ["cli.py", "4,310", "3", "99.49%", "99.93%"],
                ["zeta.py", "40", "0", "100%", "100%"],
            ],
        )
        self.assertEqual(shown["ring"], "99.83%")  # (4347 + 1200) / (4350 + 1206), floored
        self.assertEqual(shown["ring_caption"], "measured")
        self.assertEqual(shown["gate_state"], " · below the gate")

    def test_a_generated_figure_is_shown_as_given(self):
        # The generator floors to two decimals; the page must not floor again. In
        # floating point 0.29 * 100 is 28.999999999999996 and 1.13 * 100 is
        # 112.99999999999999, so a second floor showed 0.28% and 1.12%.
        payload = cpd.summarize(_report())
        payload["totals"] = {**payload["totals"], "line": 0.29, "branch": 1.13, "total": 57.57}
        payload["files"] = [["src/keel/a.py", 7, 3, 4, 1, 0.29, 1.13]]
        shown = self._drive("data", payload)
        self.assertEqual(shown["cards"][0], ["0.29%", "line coverage"])
        self.assertEqual(shown["cards"][1], ["1.13%", "branch coverage"])
        self.assertEqual(shown["rows"], [["a.py", "7", "3", "1.13%", "0.29%"]])
        self.assertEqual(shown["ring"], "57.57%")

    def test_a_slow_page_is_read_once_it_has_settled(self):
        # On a loaded runner the body took longer to parse than the old fixed 50 ms
        # snapshot, which then read every row as 0 (#1375). The driver now waits for the
        # page's own timers, so a slow parse shows the same figures as a fast one.
        payload = cpd.summarize(_report())
        self.assertEqual(self._drive("slow", payload), self._drive("data", payload))

    def test_every_two_decimal_percentage_reads_back_unchanged(self):
        # The whole range the generator can emit, through the page's own formatter.
        source = (SITE / "coverage.js").read_text(encoding="utf-8")
        formatter = re.search(r"^\s*function pct\(n\) \{.*\}$", source, re.M).group(0)
        script = (
            formatter
            + "\nconst out = [];"
            + "\nfor (let i = 0; i <= 10000; i++) out.push(pct(i / 100));"
            + "\nconsole.log(JSON.stringify(out));"
        )
        done = subprocess.run(
            [NODE, "-e", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=True,
        )
        shown = json.loads(done.stdout)
        want = [f"{i / 100:.2f}".rstrip("0").rstrip(".") + "%" for i in range(10001)]
        wrong = [(w, g) for w, g in zip(want, shown, strict=True) if w != g]
        self.assertEqual(wrong[:5], [], f"{len(wrong)} of 10001 percentages misread")

    def test_a_full_run_reads_as_passing(self):
        report = _report(
            files={"src/keel/a.py": {"summary": _summary(9, 0, 2, 2)}},
            totals=_summary(9, 0, 2, 2),
        )
        shown = self._drive("data", cpd.summarize(report))
        self.assertEqual(shown["ring"], "100%")
        self.assertEqual(shown["gate_state"], " · passing")

    def test_the_stub_page_starts_where_the_real_one_does(self):
        # The driver's DOM is only evidence while it matches coverage.html: every id it
        # stubs exists there, and the ones it starts hidden are hidden there.
        page = (SITE / "coverage.html").read_text(encoding="utf-8")
        ids = re.search(r"const ids = \[(.*?)\];", _DRIVER, re.S).group(1)
        for name in re.findall(r'"([\w-]+)"', ids):
            with self.subTest(id=name):
                self.assertIn(f'id="{name}"', page)
        hidden = re.search(r"// hidden in coverage.html until.*?\n(\[.*?\])", _DRIVER, re.S)
        for name in json.loads(hidden.group(1)):
            with self.subTest(hidden=name):
                self.assertRegex(page, rf'<[^>]*\bid="{name}"[^>]*\shidden[\s>]')

    def _assert_no_figures(self, shown: dict) -> None:
        self.assertFalse(shown["nodata_hidden"])
        self.assertTrue(shown["summary_hidden"])
        self.assertTrue(shown["table_hidden"])
        self.assertTrue(shown["date_hidden"])
        self.assertEqual(shown["cards"], [])
        self.assertEqual(shown["rows"], [])
        # The one figure left is the gate, and it says so.
        self.assertEqual(shown["ring"], "100%")
        self.assertEqual(shown["ring_caption"], "gate")
        self.assertEqual(shown["gate_state"], "")

    def test_without_the_file_the_page_shows_the_gate_and_no_numbers(self):
        self._assert_no_figures(self._drive("absent"))

    def test_a_stale_or_malformed_file_is_treated_as_absent(self):
        good = cpd.summarize(_report())
        cases = {
            "another schema": {**good, "schema": cpd.SCHEMA + 1},
            "no files": {**good, "files": []},
            "a short row": {**good, "files": [["src/keel/a.py", 1, 0]]},
            "a text count": {**good, "totals": {**good["totals"], "statements": "1,840"}},
            "not an object": [1, 2],
        }
        for name, payload in cases.items():
            with self.subTest(name):
                self._assert_no_figures(self._drive("data", payload))


if __name__ == "__main__":
    unittest.main()
