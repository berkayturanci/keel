1. **Add `_STEP_IDS_SET` to `src/keel/checkpoint.py`**:
   - Create a frozenset of `STEP_IDS` named `_STEP_IDS_SET` right after `STEP_IDS` is defined. This allows for O(1) lookups instead of O(N) when checking if steps exist in the valid set.
2. **Optimize subset validation in `src/keel/checkpoint.py`**:
   - Replace the generator expression `any(step not in STEP_IDS for step in completed)` on line 426 with the significantly faster set operation `not _STEP_IDS_SET.issuperset(completed)`.
3. **Verify and Run Tests**:
   - Run `pip install -e .[dev]` if necessary.
   - Run `make lint` and `make test` to ensure tests pass and code format is preserved.
4. **Complete pre-commit steps to ensure proper testing, verification, review, and reflection are done.**
5. **Submit changes**:
   - Create a commit and PR titled `⚡ Bolt: Optimize completed steps validation using frozenset` explaining the `issuperset` optimization reducing generator overhead.
