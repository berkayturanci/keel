import re
import timeit

_LOCATION_RE_OLD = re.compile(r"^\s*(?P<path>[^\s:][^:]*?):(?P<line>\d+)(?::\d+)?[:\s]")
_LOCATION_RE_NEW = re.compile(
    r"^[ \t]*(?P<path>[^\s\n:][^:\n]*?):(?P<line>\d+)(?::\d+)?(?:[:\s]|$)", re.MULTILINE
)

text = "Some output\n" * 1000 + "  src/file.py:42: error\n" + "More output\n" * 100

def old_way(text):
    for raw in text.splitlines():
        m = _LOCATION_RE_OLD.match(raw)
        if m:
            return m.group("path"), int(m.group("line"))
    return None, None

def regex_search_way(text):
    m = _LOCATION_RE_NEW.search(text)
    if m:
        return m.group("path"), int(m.group("line"))
    return None, None

print("Old way:", timeit.timeit(lambda: old_way(text), number=1000))
print("Regex search way:", timeit.timeit(lambda: regex_search_way(text), number=1000))
