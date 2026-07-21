import timeit

setup = """
def _type_matches(instance, t):
    if t == "string": return isinstance(instance, str)
    if t == "number": return isinstance(instance, (int, float))
    if t == "integer": return isinstance(instance, int) and not isinstance(instance, bool)
    if t == "boolean": return isinstance(instance, bool)
    if t == "null": return instance is None
    if t == "array": return isinstance(instance, list)
    if t == "object": return isinstance(instance, dict)
    return False

types = ["string", "null"]
instance = "test"

def old_check():
    return not any(_type_matches(instance, t) for t in types)

def new_check():
    for t in types:
        if _type_matches(instance, t):
            return False
    return True
"""

print("old:", timeit.timeit("old_check()", setup=setup, number=1000000))
print("new:", timeit.timeit("new_check()", setup=setup, number=1000000))
