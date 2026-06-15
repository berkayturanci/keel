## 2024-06-11 - Python set operations vs generator expressions

**Learning:** Using `frozenset.isdisjoint()` for checking intersection of a list/tuple of strings against a known set of targets is significantly faster (~3x-5x) than using a generator expression inside `any()`, like `any(item in target_set for item in items)`. Similarly, checking string suffixes against multiple values is faster using `str.endswith(tuple)` compared to `any(str.endswith(suffix) for suffix in suffixes)`.

**Action:** Prefer `.isdisjoint()` on pre-computed frozensets and passing tuples to `.startswith()` / `.endswith()` over generator expressions with `any()` when performing hot-path string validations in Python.

## 2026-06-14 - Efficient Unique Lists and String Slugs
**Learning:** In python, preserving list order while ensuring elements are unique using a generator over a standard `list` loop is vastly optimized through the use of `list(dict.fromkeys(iterator))`. Using C-level built-ins makes processing up to 300% faster compared to checking elements dynamically using sets.
**Action:** When working on pure python codebases running computationally repetitive mapping tasks, use `dict.fromkeys` for sequence uniqueness mapping instead of naive iterations/comprehensions.

## 2024-05-18 - yaml.CSafeLoader
**Learning:** `yaml.safe_load` uses the pure Python loader which is quite slow. When called many times (e.g. during config loading, extension parsing, test suites), this builds up. `yaml.load(..., Loader=yaml.CSafeLoader)` is around 10x faster (180ms vs 1500ms for 100 loads) because it uses the C implementation.
**Action:** Replace `yaml.safe_load` with a wrapper or direct call to `yaml.load(..., Loader=yaml.CSafeLoader)`. Since `PyYAML` might fall back to Python loader if C extensions are not available (e.g., depending on install), we should attempt to import `CSafeLoader` and fall back to `SafeLoader`. Ensure not to duplicate import blocks.
