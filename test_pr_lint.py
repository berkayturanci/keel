import re
authored = """💡 What: Unrolled `any()` generator expressions into explicit `for` loops with early returns in `_has_trusted_ship_assessment` and `_has_trusted_review_marker`.
🎯 Why: Generator expressions have setup and teardown overhead. Unrolling them into explicit loops with early returns in hot paths (like parsing PR comment lists) speeds up execution significantly (~3x in isolated benchmarks).
📊 Impact: Improves performance of CI gate evidence checks by avoiding unnecessary generator creation when traversing PR comment payloads.
🔬 Measurement: Run `python3 test_perf.py` to compare generator expression vs explicit loop performance.

Please add the `keel:evidence-waived` label to this PR."""

has_ref = bool(re.search(r"#\d+", authored)) or bool(
    re.search(r"\bno[ -]?issue\b", authored, re.I)
)

print(has_ref)
