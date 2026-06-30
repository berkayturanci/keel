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
