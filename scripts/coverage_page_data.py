#!/usr/bin/env python3
"""Turn ``coverage json`` output into the data ``website/coverage.html`` shows (#1320).

The coverage page used to print figures typed into ``website/content.js`` by hand:
1,840 statements, 612 branches, seven modules, ``cli.py`` at 376 statements. The
report one click away, built from the same commit, said 16,032 statements, 5,600
branches, 73 files and ``cli.py`` at 4,310. Nothing compared the two, so the page
drifted about 9x from the truth while still reading as a "live" dashboard.

The Pages workflow already runs the suite under coverage and writes
``coverage.json``. This reduces that report to the few numbers the page renders —
totals, the report's own timestamp, and one row per measured file — and writes
them to ``website/coverage-summary.json``, which ``coverage.js`` fetches. When the
file is absent (a plain static server over ``website/``), the page shows the
enforced gate and a link to the full report, and no numbers at all.

``summarize`` and ``render`` are pure; ``main`` is the only part that touches the
filesystem. Stdlib only, like the rest of ``scripts/``.

Usage::

    python scripts/coverage_page_data.py coverage.json website/coverage-summary.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: Bumped when the shape ``coverage.js`` reads changes, so a stale file is refused
#: by the page instead of half-rendered.
SCHEMA = 1


class ReportError(ValueError):
    """The input is not a ``coverage json`` report this script can read."""


def percent(covered: int, total: int) -> float:
    """``covered / total`` as a percentage, floored to two decimals.

    Floored, not rounded: 99.996 % must never be displayed as 100 %, because 100 %
    is the one figure the gate turns on. Nothing to cover counts as fully covered,
    which is what coverage.py itself reports for such a file.
    """
    if total <= 0:
        return 100.0
    return (covered * 10000 // total) / 100


def _count(summary: object, key: str, where: str) -> int:
    value = summary.get(key) if isinstance(summary, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReportError(f"{where}: {key!r} is missing or not a count")
    return value


def _figures(summary: object, where: str) -> dict[str, float | int]:
    statements = _count(summary, "num_statements", where)
    missing = _count(summary, "missing_lines", where)
    branches = _count(summary, "num_branches", where)
    covered_branches = _count(summary, "covered_branches", where)
    if missing > statements or covered_branches > branches:
        raise ReportError(f"{where}: covered/missing counts exceed their totals")
    return {
        "statements": statements,
        "missing": missing,
        "branches": branches,
        "partial": _count(summary, "num_partial_branches", where),
        "line": percent(statements - missing, statements),
        "branch": percent(covered_branches, branches),
        # coverage.py's own total: lines and branches pooled, which is the figure
        # `fail_under` is compared against.
        "total": percent(statements - missing + covered_branches, statements + branches),
    }


def summarize(report: object) -> dict:
    """The page's data from a parsed ``coverage json`` report (format 2 or 3).

    Files are sorted by path so the output is stable for identical input. Raises
    ``ReportError`` rather than guessing when a count is missing: a page that
    silently shows zeros is the defect this replaces, in another form.
    """
    if not isinstance(report, dict):
        raise ReportError("the report is not a JSON object")
    meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
    if meta.get("branch_coverage") is False:
        raise ReportError("the report was measured without branch coverage")
    files = report.get("files")
    if not isinstance(files, dict) or not files:
        raise ReportError("the report lists no files")
    rows = []
    for path in sorted(files, key=lambda p: p.replace("\\", "/")):
        entry = files[path]
        fig = _figures(entry.get("summary") if isinstance(entry, dict) else None, path)
        rows.append(
            [
                path.replace("\\", "/"),
                fig["statements"],
                fig["missing"],
                fig["branches"],
                fig["partial"],
                fig["line"],
                fig["branch"],
            ]
        )
    totals = _figures(report.get("totals"), "totals")
    totals["files"] = len(rows)
    generated = meta.get("timestamp")
    return {
        "schema": SCHEMA,
        "generated": generated if isinstance(generated, str) else None,
        "totals": totals,
        "columns": ["file", "statements", "missing", "branches", "partial", "line", "branch"],
        "files": rows,
    }


def render(summary: dict) -> str:
    """The summary as compact, key-sorted JSON with a trailing newline."""
    return json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", type=Path, help="coverage json output (coverage.json)")
    parser.add_argument("out", type=Path, help="where to write the page data")
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        summary = summarize(report)
    except (OSError, ValueError) as exc:
        print(f"coverage_page_data: {args.report}: {exc}", file=sys.stderr)
        return 1
    args.out.write_text(render(summary), encoding="utf-8")
    totals = summary["totals"]
    print(
        f"coverage page data: {totals['files']} files, {totals['statements']} statements, "
        f"{totals['branches']} branches -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
