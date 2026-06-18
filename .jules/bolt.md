## 2024-06-11 - Python set operations vs generator expressions

**Learning:** Using `frozenset.isdisjoint()` for checking intersection of a list/tuple of strings against a known set of targets is significantly faster (~3x-5x) than using a generator expression inside `any()`, like `any(item in target_set for item in items)`. Similarly, checking string suffixes against multiple values is faster using `str.endswith(tuple)` compared to `any(str.endswith(suffix) for suffix in suffixes)`.

**Action:** Prefer `.isdisjoint()` on pre-computed frozensets and passing tuples to `.startswith()` / `.endswith()` over generator expressions with `any()` when performing hot-path string validations in Python.

## 2026-06-14 - Efficient Unique Lists and String Slugs
**Learning:** In python, preserving list order while ensuring elements are unique using a generator over a standard `list` loop is vastly optimized through the use of `list(dict.fromkeys(iterator))`. Using C-level built-ins makes processing up to 300% faster compared to checking elements dynamically using sets.
**Action:** When working on pure python codebases running computationally repetitive mapping tasks, use `dict.fromkeys` for sequence uniqueness mapping instead of naive iterations/comprehensions.

## 2024-06-25 - Python YAML parsing performance
**Learning:** Parsing YAML configurations and specs repeatedly using `yaml.safe_load()` in pure Python is relatively slow. The underlying library (PyYAML) exposes a compiled C-extension `CSafeLoader` which brings significant performance improvements over the default `SafeLoader` when reading files or string streams.
**Action:** Always prefer importing `CSafeLoader` (with a `try/except` fallback to `SafeLoader`) and use `yaml.load(..., Loader=SafeLoader)` over `yaml.safe_load()` in Python projects for a major parse-speed boost.
