## 2026-06-13 - Python set operations vs generator expressions

**Learning:** Using `frozenset.isdisjoint()` for checking intersection of a list/tuple of strings against a known set of targets is significantly faster (~3x-5x) than using a generator expression inside `any()`, like `any(item in target_set for item in items)`. Similarly, checking string suffixes against multiple values is faster using `str.endswith(tuple)` compared to `any(str.endswith(suffix) for suffix in suffixes)`.

**Action:** Prefer `.isdisjoint()` on pre-computed frozensets and passing tuples to `.startswith()` / `.endswith()` over generator expressions with `any()` when performing hot-path string validations in Python.

## 2026-06-14 - Efficient Unique Lists and String Slugs
**Learning:** In python, preserving list order while ensuring elements are unique using a generator over a standard `list` loop is vastly optimized through the use of `list(dict.fromkeys(iterator))`. Using C-level built-ins makes processing up to 300% faster compared to checking elements dynamically using sets.
**Action:** When working on pure python codebases running computationally repetitive mapping tasks, use `dict.fromkeys` for sequence uniqueness mapping instead of naive iterations/comprehensions.

## 2026-06-22 - YAML parsing performance with C-extension

**Learning:** Parsing and serializing YAML files using `yaml.safe_load` and `yaml.safe_dump` can be quite slow in Python, especially for large files. `PyYAML` provides C-extension implementations `CSafeLoader` and `CSafeDumper` which offer significantly faster performance (up to 8x speedup in our benchmarks).

**Action:** Whenever `PyYAML` is used for deserialization or serialization, use a custom wrapper module that falls back to the pure Python implementation only when the C-extensions are unavailable. This avoids performance bottlenecks when parsing large configuration files or extensive frontmatter.

## 2026-06-22 - Premature YAML parse optimization
**Learning:** While using `yaml.CSafeLoader` and `yaml.CSafeDumper` over pure Python equivalents yields significantly better raw parse/serialize times for large documents (e.g. ~8x faster), replacing standard library functions should only be done when the bottleneck is confirmed. Small configuration or frontmatter reads that take sub-milliseconds don't benefit from this micro-optimization on application startup, and introducing C-extension fallbacks can cause unexpected discrepancies in exception handling that break CI coverage and documentation invariants.
**Action:** Do not preemptively optimize low-cost operations (like parsing a single tiny config file) and focus performance optimization on provable bottlenecks or loops that are known to run frequently. Always verify the overall system impact vs pure benchmark speedup and adhere to existing security and test coverage invariants.

## 2026-07-03 - Optimizing Rule evaluation with frozensets
**Learning:** Checking for intersections between list of strings iteratively with generator expressions and `any` method can be slow. Pre-computing frozensets and using `.isdisjoint()` drastically speeds up the checks. Specifically, replacing `any(want.strip().casefold() in present for want in self.labels)` with `not self._frozenset_labels.isdisjoint(present)` provides more than 3x speedup.
**Action:** Use pre-computed `frozensets` and `.isdisjoint()` when dealing with list/set inclusion checks that are called frequently.

## 2026-07-06 - Optimizing Validation with frozenset.issuperset()
**Learning:** Using a generator expression with `any(item not in TUPLE for item in items)` to check if all items in a list belong to an allowed set is significantly slower than converting the tuple to a `frozenset` at module level and using `frozenset.issuperset(items)`. The set operation reduces an O(N*M) lookup to O(M) and provided ~6x speedup in isolated benchmarks in the codebase for checking list subsets.
**Action:** When validating if an unknown iterable sequence is a subset of a known collection of constants, prefer precomputing a `frozenset` and utilizing the `.issuperset()` method rather than generator comprehensions with `any` and `not in`.
## 2026-07-06 - Optimizing Validation with frozenset.issuperset()
**Learning:** Checking for CI check subsets using a generator comprehension and `all` (e.g. `all(p in CI_OK_STATES for p in parts)`) is significantly slower than using the C-level `.issuperset()` method on a pre-computed frozenset (e.g. `CI_OK_STATES.issuperset(parts)`).
**Action:** Replace generator loops validating element inclusion with `.issuperset()` on static `frozensets` for measurable ~3x-4x speedups in hot path logic.
## 2026-07-12 - Early returns and loop optimizations over `any()`
**Learning:** Using sequential `any()` generator expressions forces iteration to spin up generators and iterate over data that may not even need evaluating if an earlier condition is met. By unrolling `any()` checks into explicit early return `if` and `for` loops, evaluations can be short-circuited dramatically faster (up to ~90x speedup in isolated hot path cases where a short-circuit occurs early).
**Action:** When validating multiple cascading criteria, implement manual short-circuiting via sequential `if` and `for` loops with early returns rather than joining multiple generator expressions.
**Read 2026-08-17 (below) before acting on this.** It sets the bar this entry lacks: the µs saved per real run, counted at the real call site — not a percentage on a synthetic loop.

## 2026-07-16 - Unrolling any() with pre-computation
**Learning:** In Python, replacing an `any()` generator expression with a standard `for` loop and an early return avoids generator setup/teardown overhead. When dealing with repeated string operations (like `.lower()`) inside the loop, pre-computing the target string outside the loop further speeds up execution.
**Action:** Always consider unrolling `any()` generators in hot paths, and hoist loop-invariant transformations to avoid redundant processing.
**Read 2026-08-17 (below) before acting on this.** It sets the bar this entry lacks: the µs saved per real run, counted at the real call site — not a percentage on a synthetic loop.

## 2026-07-20 - Fast Substring Checks with explicit `or`
**Learning:** When checking a string for the presence of a small, fixed set of substrings in Python hot paths, using a direct sequence of `in` checks linked by `or` (e.g., `"a" in s or "b" in s`) avoids generator setup overhead and is significantly faster than using an `any()` generator expression.
**Action:** Replace `any(sub in s for sub in ("a", "b", "c"))` with direct `"a" in s or "b" in s or "c" in s` for micro-optimizations in string validations.
## 2026-07-21 - Unroll any() generator in intake.py
**Learning:** In Python hot paths, unrolling chained `any()` generator expressions into explicit sequential `if` and `for` loops with early returns can bypass generator overhead and significantly improve performance by properly short-circuiting.
**Action:** Unroll `any()` generator loops in hot paths to explicit loops.
**Read 2026-08-17 (below) before acting on this.** It sets the bar this entry lacks: the µs saved per real run, counted at the real call site — not a percentage on a synthetic loop.

## 2026-07-30 - Fast list filtering check via length comparison
**Learning:** In Python, when filtering a list using a list comprehension with a predicate, checking if elements were filtered using `any()` on the same predicate is redundant and slow. Comparing the lengths of the filtered and original lists (`len(filtered) < len(original)`) is significantly faster (approx ~2.8x speedup) as it avoids redundant predicate evaluation and generator overhead.
**Action:** Use list length comparison (`len(filtered) < len(original)`) instead of `any()` or `all()` when verifying if a sequence was altered during list comprehension filtering.

## 2026-08-10 - Fast multiple regex matching
**Learning:** Checking a string against multiple regex patterns by condensing them into a single pattern using the `|` (OR) operator is significantly faster (~44% faster) than evaluating them individually via multiple `re.search` calls or `any()` generator expressions.
**Action:** When validating a string against multiple related regex patterns, combine them into a single regex string using `|` instead of checking them iteratively in a loop or generator expression.

## 2026-08-17 - Unrolling any() is real but usually not worth it: measure the µs, not the %

**Learning:** The earlier notes in this file are **right about the mechanism and
misleading about when it matters**. Both halves need saying, because acting on the
percentage alone produced a PR that was closed (keel#789).

*Right:* `any(f(x) for x in xs)` builds a generator object per call, and that setup
is real. Unrolling into an explicit loop with an early return removes it:

```
predicate inline   (x > 999)         any() 0.119s   unroll 0.063s   47% faster
predicate a call   (cheap(x))        any() 0.279s   unroll 0.222s   20% faster
predicate real work (dict + `in`)    any() 0.654s   unroll 0.285s   56% faster
```

*Misleading:* those are millions of iterations of nothing else. At the sizes this
codebase actually sees, the same comparison is:

```
keel's real call site, 20 PR comments:
  any()  1.83 µs      unroll  1.69 µs      8% faster, 0.15 µs saved
  two calls per evidence run          =    0.30 µs
  GitHub API calls in the same run    ≈ 200000 µs
  share of the run reclaimed          =    0.00015 %
```

One thing in the older notes *is* simply wrong: a claim that combining "list
comprehensions with `any()` forces full iteration before short-circuiting". That
describes `any([f(x) for x in xs])` — with brackets, which does materialise the list.
A generator expression short-circuits fine. One bracket, entirely different fix:
delete the brackets, do not unroll.

**Action:** Report the **absolute** saving at the real input size, not the percentage
on a synthetic loop. A 50% win on 0.3 µs is 0.15 µs. Unroll when a profile shows the
line is hot — a tight loop over thousands of items, called repeatedly. Do not unroll
code that runs a handful of times per command, and especially not in
`src/keel/evidence.py`, which decides whether a PR may merge: churn there needs to buy
something. keel#789 proposed exactly that and was closed. See keel#791.

**Closed for this reason so far.** Add a line here instead of writing a new entry, and
search this list for the function before proposing an unroll — the same one-line change
to `tdd.is_test_path` was opened twice, two days apart, and closed twice:

- keel#789 — `src/keel/evidence.py`: 0.30 µs per evidence run (measured above).
- keel#1214 and keel#1237 — `tdd.is_test_path`. Per call the unroll saves 108–195 ns
  (32–45 %), so the percentage in the PR was honest. But the function is reached only
  from `tdd.check_order`, once per `tdd-order` gate: counted on three merged branches
  (keel#1206, keel#1220, keel#1228) it ran 5–19 times, so the saving is 0.5–2 µs per
  run, beside a `git log` subprocess of ≈ 17 000 µs in the same gate.
