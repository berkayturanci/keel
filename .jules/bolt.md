## 2025-06-08 - String splitting optimization

**Learning:** `text.splitlines()` allocates a new string for every line in memory, causing O(N) overhead.
**Action:** When extracting information line by line (like from command outputs), use `re.search` with `re.MULTILINE` flag instead of `.splitlines()` combined with a python-level loop. This avoids the O(N) memory overhead and delegating the iteration entirely to the highly optimized C-level regex engine. Be sure to strictly constrain matches by explicitly disallowing `\n` to prevent bleeding across newlines.
