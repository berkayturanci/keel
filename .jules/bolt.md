## 2024-06-11 - Python set operations vs generator expressions

**Learning:** Using `frozenset.isdisjoint()` for checking intersection of a list/tuple of strings against a known set of targets is significantly faster (~3x-5x) than using a generator expression inside `any()`, like `any(item in target_set for item in items)`. Similarly, checking string suffixes against multiple values is faster using `str.endswith(tuple)` compared to `any(str.endswith(suffix) for suffix in suffixes)`.

**Action:** Prefer `.isdisjoint()` on pre-computed frozensets and passing tuples to `.startswith()` / `.endswith()` over generator expressions with `any()` when performing hot-path string validations in Python.

## 2026-06-14 - Efficient Unique Lists and String Slugs
**Learning:** In python, preserving list order while ensuring elements are unique using a generator over a standard `list` loop is vastly optimized through the use of `list(dict.fromkeys(iterator))`. Using C-level built-ins makes processing up to 300% faster compared to checking elements dynamically using sets.
**Action:** When working on pure python codebases running computationally repetitive mapping tasks, use `dict.fromkeys` for sequence uniqueness mapping instead of naive iterations/comprehensions.

## 2024-06-21 - YAML parsing performance with C-extension

**Learning:** Parsing and serializing YAML files using `yaml.safe_load` and `yaml.safe_dump` can be quite slow in Python, especially for large files. `PyYAML` provides C-extension implementations `CSafeLoader` and `CSafeDumper` which offer significantly faster performance (up to 8x speedup in our benchmarks).

**Action:** Whenever `PyYAML` is used for deserialization or serialization, use a custom wrapper module that falls back to the pure Python implementation only when the C-extensions are unavailable. This avoids performance bottlenecks when parsing large configuration files or extensive frontmatter.

## 2024-06-21 - Premature YAML parse optimization
**Learning:** While using `yaml.CSafeLoader` and `yaml.CSafeDumper` over pure Python equivalents yields significantly better raw parse/serialize times for large documents (e.g. ~8x faster), replacing standard library functions should only be done when the bottleneck is confirmed. Small configuration or frontmatter reads that take sub-milliseconds don't benefit from this micro-optimization on application startup, and introducing C-extension fallbacks can cause unexpected discrepancies in exception handling that break CI coverage and documentation invariants.
**Action:** Do not preemptively optimize low-cost operations (like parsing a single tiny config file) and focus performance optimization on provable bottlenecks or loops that are known to run frequently. Always verify the overall system impact vs pure benchmark speedup and adhere to existing security and test coverage invariants.

## 2024-07-01 - Optimizing Rule evaluation with frozensets
**Learning:** Checking for intersections between list of strings iteratively with generator expressions and `any` method can be slow. Pre-computing frozensets and using `.isdisjoint()` drastically speeds up the checks. Specifically, replacing `any(want.strip().casefold() in present for want in self.labels)` with `not self._frozenset_labels.isdisjoint(present)` provides more than 3x speedup.
**Action:** Use pre-computed `frozensets` and `.isdisjoint()` when dealing with list/set inclusion checks that are called frequently.

## 2024-07-15 - Optimizing Validation with frozenset.issuperset()
**Learning:** Using a generator expression with `any(item not in TUPLE for item in items)` to check if all items in a list belong to an allowed set is significantly slower than converting the tuple to a `frozenset` at module level and using `frozenset.issuperset(items)`. The set operation reduces an O(N*M) lookup to O(M) and provided ~6x speedup in isolated benchmarks in the codebase for checking list subsets.
**Action:** When validating if an unknown iterable sequence is a subset of a known collection of constants, prefer precomputing a `frozenset` and utilizing the `.issuperset()` method rather than generator comprehensions with `any` and `not in`.
## 2024-07-23 - Optimizing Validation with frozenset.issuperset()
**Learning:** Checking for CI check subsets using a generator comprehension and `all` (e.g. `all(p in CI_OK_STATES for p in parts)`) is significantly slower than using the C-level `.issuperset()` method on a pre-computed frozenset (e.g. `CI_OK_STATES.issuperset(parts)`).
**Action:** Replace generator loops validating element inclusion with `.issuperset()` on static `frozensets` for measurable ~3x-4x speedups in hot path logic.
## 2024-07-28 - Early returns and loop optimizations over `any()`
**Learning:** Using sequential `any()` generator expressions forces iteration to spin up generators and iterate over data that may not even need evaluating if an earlier condition is met. By unrolling `any()` checks into explicit early return `if` and `for` loops, evaluations can be short-circuited dramatically faster (up to ~90x speedup in isolated hot path cases where a short-circuit occurs early).
**Action:** When validating multiple cascading criteria, implement manual short-circuiting via sequential `if` and `for` loops with early returns rather than joining multiple generator expressions.

## 2024-07-15 - Unrolling any() with pre-computation
**Learning:** In Python, replacing an `any()` generator expression with a standard `for` loop and an early return avoids generator setup/teardown overhead. When dealing with repeated string operations (like `.lower()`) inside the loop, pre-computing the target string outside the loop further speeds up execution.
**Action:** Always consider unrolling `any()` generators in hot paths, and hoist loop-invariant transformations to avoid redundant processing.

## 2024-07-31 - Fast Substring Checks with explicit `or`
**Learning:** When checking a string for the presence of a small, fixed set of substrings in Python hot paths, using a direct sequence of `in` checks linked by `or` (e.g., `"a" in s or "b" in s`) avoids generator setup overhead and is significantly faster than using an `any()` generator expression.
**Action:** Replace `any(sub in s for sub in ("a", "b", "c"))` with direct `"a" in s or "b" in s or "c" in s` for micro-optimizations in string validations.
## 2024-05-16 - Unroll any() generator in intake.py
**Learning:** In Python hot paths, unrolling chained `any()` generator expressions into explicit sequential `if` and `for` loops with early returns can bypass generator overhead and significantly improve performance by properly short-circuiting.
**Action:** Unroll `any()` generator loops in hot paths to explicit loops.

## 2024-05-18 - Fast list filtering check via length comparison
**Learning:** In Python, when filtering a list using a list comprehension with a predicate, checking if elements were filtered using `any()` on the same predicate is redundant and slow. Comparing the lengths of the filtered and original lists (`len(filtered) < len(original)`) is significantly faster (approx ~2.8x speedup) as it avoids redundant predicate evaluation and generator overhead.
**Action:** Use list length comparison (`len(filtered) < len(original)`) instead of `any()` or `all()` when verifying if a sequence was altered during list comprehension filtering.

## 2024-05-20 - Fast multiple regex matching
**Learning:** Checking a string against multiple regex patterns by condensing them into a single pattern using the `|` (OR) operator is significantly faster (~44% faster) than evaluating them individually via multiple `re.search` calls or `any()` generator expressions.
**Action:** When validating a string against multiple related regex patterns, combine them into a single regex string using `|` instead of checking them iteratively in a loop or generator expression.
