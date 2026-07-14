1. Modify `src/keel/capture.py:recursion_guard` to return early if any condition matches.
   Currently, it evaluates all conditions using generator expressions (`any(...)`) across all paths regardless of early true conditions on title or label. By breaking this apart into separate checks and early returns, it skips evaluating the full file path loop when a title or label already triggers it, offering minor performance improvements.
2. Complete pre-commit steps to ensure proper testing, verification, review, and reflection are done.
3. Submit the change with "⚡ Bolt: [performance improvement]" format.
