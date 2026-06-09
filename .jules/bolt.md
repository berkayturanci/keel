## 2024-06-09 - Python Regex Performance for Multiline Searching
**Learning:** Using `re.search` with `re.MULTILINE` and `^` anchor on a large multiline string is ~30% faster (and consumes less memory) than splitting the string into a list of lines with `str.splitlines()` and iterating over them with `re.match`, especially when searching for line location matches in large CLI/build output logs.
**Action:** When searching for patterns line-by-line in a large string block, prefer `re.search(text, re.MULTILINE)` over `for line in text.splitlines(): re.match(pattern, line)`.
