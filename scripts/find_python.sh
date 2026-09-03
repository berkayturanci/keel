#!/usr/bin/env sh
# Resolve the Python interpreter this repository's developer targets run on.
#
# keel needs >= 3.11 (`requires-python`) and PyYAML (its one runtime dependency).
# `python3` is not that interpreter everywhere: on macOS it is Xcode's 3.9, and
# `make test` there fails with ~110 import errors that read like a regression
# (#1022). This script picks the first interpreter that actually satisfies both,
# so the Makefile no longer has to assume.
#
# Order: the repository venv, then the newest named interpreter on PATH.
# Prints the interpreter on stdout; exits 2 with a one-line install hint on
# stderr when nothing qualifies. `PY=/path/to/python make test` bypasses it.
set -eu

# Shell builtins only, deliberately: this script must resolve its own repository
# root before it knows whether the PATH it was handed contains anything at all
# (`dirname` included).
case "$0" in
    */*) here=${0%/*} ;;
    *) here=. ;;
esac
root=$(CDPATH= cd -- "$here/.." && pwd)

# The requirement, asserted by the candidate itself: no version parsing here, and
# an interpreter that cannot import yaml is not a candidate whatever its version.
probe='import sys, yaml; assert sys.version_info >= (3, 11)'

usable() {
    "$1" -c "$probe" >/dev/null 2>&1
}

venv="$root/.venv/bin/python"
if [ -x "$venv" ] && usable "$venv"; then
    printf '%s\n' "$venv"
    exit 0
fi

for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if resolved=$(command -v "$candidate" 2>/dev/null) && usable "$resolved"; then
        printf '%s\n' "$resolved"
        exit 0
    fi
done

printf '%s\n' "find_python: no Python >= 3.11 with PyYAML found (tried .venv/bin/python, python3.14, python3.13, python3.12, python3.11, python3) — install one (e.g. python3.12 + pip install pyyaml), or create a venv and run pip install -e \".[dev]\"; PY=/path/to/python overrides this resolver." >&2
exit 2
