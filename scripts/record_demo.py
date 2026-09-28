#!/usr/bin/env python3
"""Record the README's demo: keel stopping a change, then clearing it (#1330).

Run from the repository root::

    python3 scripts/record_demo.py

It builds a scratch git repository in a temporary directory — a two-line `calc.py`,
a test for it, and a `.keel/project.yaml` with a `build` and a `lint` gate — commits a
change that breaks `add()`, and then runs, for real, the commands the demo shows:

    keel validate .keel/project.yaml     the config loads
    keel run-gates .keel/project.yaml    the build gate fails: BLOCKED, exit 1
    git diff                             the fix (written by this script, shown by git)
    keel run-gates .keel/project.yaml    both gates pass, exit 0

`keel` is this checkout's CLI (`python -m keel` with `PYTHONPATH=src`), so the recording
is of the code in the tree, not of an installed release. It writes two files:

- `docs/assets/demo.svg` — an animated SVG (CSS keyframes, text only, no script, no
  external font) that GitHub renders inside the README.
- `docs/assets/demo.cast` — the same recording as an asciinema v2 cast.

**What is captured and what is not.** Every output line is the command's own stdout and
stderr, merged in the order a terminal would show them, byte for byte except for one
substitution: the temporary directory's path, should any output contain it, is replaced
with `~/calc`, so the recording does not depend on the machine. The `#` lines are
narration, fixed in `SCENARIO` below; the `[exit N]` lines print each command's real
return code. **Timings are fixed, not measured** (`TYPE_S`, `RUN_S`, …): a recording
whose pauses followed the machine's speed could never be compared with a fresh one.

The environment is made hermetic so any machine records the same bytes: no `KEEL_*`
variable, no GitHub token and an empty `gh` config (keel's capability probe then finds no
authenticated `gh`, and makes no network call), no system or global git config, fixed git
author and dates, UTF-8 output.

`tests/test_readme_demo.py` re-runs `capture()` and fails when the committed SVG or cast
differs from what keel prints today. `scripts/` is outside the coverage gate; that test is
what holds this file. Standard library only.
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

REPO_ROOT = Path(__file__).resolve().parents[1]
SVG_PATH = REPO_ROOT / "docs" / "assets" / "demo.svg"
CAST_PATH = REPO_ROOT / "docs" / "assets" / "demo.cast"

#: What the scratch repository's path is shown as, wherever an output line contains it.
PLACEHOLDER = "~/calc"
TITLE = "keel stops a change, then clears it"

CALC_BROKEN = "def add(a, b):\n    return a - b\n"
CALC_FIXED = "def add(a, b):\n    return a + b\n"
TEST_CALC = (
    "from calc import add\n"
    "\n"
    "got = add(2, 3)\n"
    "if got != 5:\n"
    '    raise SystemExit(f"test_add: add(2, 3) returned {got}, expected 5")\n'
    'print("test_add: ok")\n'
)

#: The demo, in order. `note` is narration; `keel` and `git` run for real; `fix`
#: rewrites calc.py (what the agent would do) and shows nothing itself.
SCENARIO: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("note", ("a change to calc.py is waiting to merge",)),
    ("keel", ("validate", ".keel/project.yaml")),
    ("keel", ("run-gates", ".keel/project.yaml")),
    ("note", ("keel stopped it: the build gate failed. The fix:",)),
    ("fix", ()),
    ("git", ("diff",)),
    ("keel", ("run-gates", ".keel/project.yaml")),
    ("note", ("cleared: the gates pass and no longer block the merge",)),
)

# Fixed timings, in seconds.
TYPE_S = 0.05  # per typed character of a command
RUN_S = 0.6  # between Enter and the first output line
LINE_S = 0.04  # between output lines
NOTE_S = 1.8  # a narration line stays alone this long
READ_S = 2.2  # after a command's output, before the next line
BLOCKED_READ_S = 3.6  # after a failed command: the line to read
END_S = 6.0  # the last frame holds this long before the loop restarts


@dataclass(frozen=True)
class Step:
    """One line typed at the prompt (a command or a note) and what it printed."""

    kind: str  # "note" | "cmd"
    text: str  # the narration, or the command exactly as the demo shows it
    output: tuple[str, ...] = ()
    returncode: int | None = None


def _yaml_single_quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _project_yaml() -> str:
    # The interpreter running this script runs the gates, so the recording needs no
    # `python3` on PATH (Windows runners have none). Its path is never printed.
    #
    # `-B`, and `tabnanny` rather than `py_compile` for the lint gate: nothing may write
    # a `.pyc`. The fix keeps calc.py's size, and within one second its mtime too, so a
    # `.pyc` left by the first run would be loaded by the second and the "fixed" code
    # would fail again. The first draft of this script recorded exactly that.
    python = f'"{sys.executable}" -B'
    return (
        "extends: keel\n"
        'core_version: "^1.0"\n'
        "owner: demo\n"
        "repo: calc\n"
        "base_branch: main\n"
        "timezone: UTC\n"
        'merge_window: "07:00-01:30"\n'
        "knobs:\n"
        f"  build_gate_cmd: {_yaml_single_quoted(python + ' test_calc.py')}\n"
        f"  lint_cmd: {_yaml_single_quoted(python + ' -m tabnanny calc.py test_calc.py')}\n"
        "gates: [build, lint]\n"
    )


def _env(base: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("KEEL_", "GH_", "GITHUB_", "GIT_", "PYTHON"))
    }
    gh_config = base / "gh"
    gh_config.mkdir(exist_ok=True)
    git_config = base / "gitconfig"
    git_config.write_bytes(b"")
    env.update(
        PYTHONPATH=str(REPO_ROOT / "src"),
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
        GH_CONFIG_DIR=str(gh_config),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=str(git_config),
        GIT_PAGER="cat",
        GIT_AUTHOR_NAME="demo",
        GIT_AUTHOR_EMAIL="demo@example.invalid",
        GIT_COMMITTER_NAME="demo",
        GIT_COMMITTER_EMAIL="demo@example.invalid",
        GIT_AUTHOR_DATE="2026-01-01T00:00:00Z",
        GIT_COMMITTER_DATE="2026-01-01T00:00:00Z",
        NO_COLOR="1",
        TERM="dumb",
    )
    return env


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))  # LF on every platform


def _run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )


def _git(repo: Path, env: dict[str, str], *args: str) -> None:
    proc = _run(["git", *args], cwd=repo, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stdout}")


def _scrub(text: str, paths: list[str]) -> str:
    for path in paths:
        text = text.replace(path, PLACEHOLDER)
    return text


def capture(base: Path) -> list[Step]:
    """Build the scratch repository under ``base`` and run the scenario in it."""
    base = Path(base)
    repo = base / "calc"
    repo.mkdir(parents=True)
    env = _env(base)
    paths = sorted(
        {str(p) for p in (repo, repo.resolve(), base, base.resolve())}
        | {p.as_posix() for p in (repo, repo.resolve(), base, base.resolve())},
        key=len,
        reverse=True,
    )

    _write(repo / "calc.py", CALC_FIXED)
    _write(repo / "test_calc.py", TEST_CALC)
    _write(repo / ".keel" / "project.yaml", _project_yaml())
    _write(repo / ".gitignore", ".keel/state/\n__pycache__/\n")
    _git(repo, env, "init", "-q", "-b", "main")
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-q", "-m", "calc")
    _git(repo, env, "checkout", "-q", "-b", "change")
    _write(repo / "calc.py", CALC_BROKEN)
    _git(repo, env, "commit", "-q", "-am", "the change")

    steps: list[Step] = []
    for kind, args in SCENARIO:
        if kind == "note":
            steps.append(Step("note", args[0]))
            continue
        if kind == "fix":
            _write(repo / "calc.py", CALC_FIXED)
            continue
        if kind == "keel":
            argv = [sys.executable, "-m", "keel", *args]
        else:
            argv = ["git", *args]
        proc = _run(argv, cwd=repo, env=env)
        output = tuple(_scrub(proc.stdout, paths).splitlines())
        steps.append(Step("cmd", " ".join((kind, *args)), output, proc.returncode))
    return steps


# --- the rendered lines and their times ------------------------------------------------


@dataclass(frozen=True)
class Line:
    text: str
    style: str  # "note" | "cmd" | "out" | "exit"
    at: float  # seconds into the loop when it appears
    typed: int = 0  # characters typed after the "$ " prompt (commands only)


def display_lines(steps: list[Step]) -> list[str]:
    """Every line the demo shows, top to bottom — what the drift test compares."""
    return [line.text for line in timeline(steps)[0]]


def timeline(steps: list[Step]) -> tuple[list[Line], float]:
    lines: list[Line] = []
    t = 0.4
    for step in steps:
        if step.kind == "note":
            lines.append(Line(f"# {step.text}", "note", t))
            t += NOTE_S
            continue
        lines.append(Line(f"$ {step.text}", "cmd", t, typed=len(step.text)))
        t += len(step.text) * TYPE_S + RUN_S
        for text in step.output:
            lines.append(Line(text, "out", t))
            t += LINE_S
        lines.append(Line(f"[exit {step.returncode}]", "exit", t))
        t += BLOCKED_READ_S if step.returncode else READ_S
    return lines, round(t - READ_S + END_S, 2)


def _columns(lines: list[Line]) -> int:
    return max(len(line.text) for line in lines)


# --- the animated SVG ------------------------------------------------------------------

FONT_PX = 13
CHAR_W = 7.9  # a generous monospace advance at 13px, so a command's cover hides it all
LINE_H = 18
PAD = 16
BAR_H = 30

_COLORS = {
    "bg": "#0d1117",
    "bar": "#161b22",
    "border": "#30363d",
    "fg": "#e6edf3",
    "dim": "#8b949e",
    "green": "#3fb950",
    "red": "#f85149",
    "amber": "#d29922",
    "cyan": "#39c5cf",
}


def _span(cls: str, text: str) -> str:
    return f'<tspan class="{cls}">{escape(text)}</tspan>'


def _markup(line: Line) -> str:
    """The line's text as SVG markup, colored by token. Its characters are unchanged."""
    text = line.text
    if line.style == "note":
        return _span("n", text)
    if line.style == "exit":
        return _span("n", text)
    if line.style == "cmd":
        return _span("g", "$") + escape(text[1:])
    stripped = text.lstrip()
    lead = text[: len(text) - len(stripped)]
    for word, cls in (("FAIL", "r b"), ("ok", "g"), ("OK", "g")):
        if stripped.startswith(word + " ") or stripped == word:
            return escape(lead) + _span(cls, word) + escape(stripped[len(word) :])
    if stripped.startswith("[major]"):
        return escape(lead) + _span("a", "[major]") + escape(stripped[len("[major]") :])
    if text.startswith("BLOCKED"):
        return _span("r b", "BLOCKED") + escape(text[len("BLOCKED") :])
    if text.startswith(("diff --git", "index ", "--- ", "+++ ")):
        return _span("b", text)
    if text.startswith("@@"):
        return _span("c", text)
    if text.startswith("-"):
        return _span("r", text)
    if text.startswith("+"):
        return _span("g", text)
    return escape(text)


def _pct(t: float, total: float) -> str:
    return f"{t / total * 100:.3f}".rstrip("0").rstrip(".") + "%"


def render_svg(steps: list[Step]) -> str:
    lines, total = timeline(steps)
    width = round(_columns(lines) * CHAR_W + 2 * PAD)
    height = BAR_H + PAD + len(lines) * LINE_H + PAD - 4
    times = sorted({line.at for line in lines})
    fade = _pct(total - 0.6, total)

    css = [
        f"text{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',"
        f"monospace;font-size:{FONT_PX}px;fill:{_COLORS['fg']};white-space:pre}}",
        f".n{{fill:{_COLORS['dim']}}}.g{{fill:{_COLORS['green']}}}"
        f".r{{fill:{_COLORS['red']}}}.a{{fill:{_COLORS['amber']}}}"
        f".c{{fill:{_COLORS['cyan']}}}.b{{font-weight:700}}",
        f".l,.k{{animation-duration:{total}s;animation-iteration-count:infinite;"
        "animation-timing-function:linear}",
        ".l{opacity:0}",
    ]
    for i, at in enumerate(times):
        css.append(
            f".t{i}{{animation-name:v{i}}}"
            f"@keyframes v{i}{{0%,{_pct(at, total)}{{opacity:0}}"
            f"{_pct(at + 0.01, total)},{fade}{{opacity:1}}100%{{opacity:0}}}}"
        )
    body = []
    for row, line in enumerate(lines):
        y = BAR_H + PAD + row * LINE_H + FONT_PX
        cls = f"l t{times.index(line.at)}"
        body.append(f'<text class="{cls}" x="{PAD}" y="{y}">{_markup(line)}</text>')
        if line.typed:
            # A background-colored cover over the typed text, stepped right one
            # character at a time: the command appears as if typed.
            x = PAD + 2 * CHAR_W
            w = round(line.typed * CHAR_W + 2, 1)
            start, end = line.at, line.at + line.typed * TYPE_S
            name = f"k{row}"
            css.append(
                f".{name}{{animation-name:{name}}}"
                f"@keyframes {name}{{0%,{_pct(start, total)}{{transform:translateX(0);"
                f"animation-timing-function:steps({line.typed},end)}}"
                f"{_pct(end, total)},100%{{transform:translateX({w}px)}}}}"
            )
            body.append(
                f'<rect class="k {name}" x="{x:.1f}" y="{y - FONT_PX}" width="{w}" '
                f'height="{LINE_H}" fill="{_COLORS["bg"]}"/>'
            )
    css.append(
        "@media (prefers-reduced-motion:reduce){.l{animation:none;opacity:1}"
        ".k{animation:none;display:none}}"
    )

    dots = "".join(
        f'<circle cx="{PAD + i * 18}" cy="{BAR_H / 2}" r="5.5" fill="{_COLORS["border"]}"/>'
        for i in range(3)
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc" '
        'xml:space="preserve">\n'
        f'<title id="title">{escape(TITLE)}</title>\n'
        '<desc id="desc">A terminal recording: keel run-gates fails a change whose test '
        "breaks and prints BLOCKED, the fix is shown with git diff, and the same command "
        "then passes. Generated by scripts/record_demo.py from real output.</desc>\n"
        f"<style>{''.join(css)}</style>\n"
        f'<defs><clipPath id="screen"><rect x="1" y="{BAR_H}" width="{width - 2}" '
        f'height="{height - BAR_H - 1}"/></clipPath></defs>\n'
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="8" '
        f'fill="{_COLORS["bg"]}" stroke="{_COLORS["border"]}"/>\n'
        f'<path d="M1 {BAR_H}V9a8 8 0 0 1 8-8h{width - 18}a8 8 0 0 1 8 8V{BAR_H}z" '
        f'fill="{_COLORS["bar"]}"/>\n'
        f"{dots}\n"
        f'<text class="n" x="{width / 2:g}" y="{BAR_H / 2 + 4}" text-anchor="middle">'
        f"{escape(PLACEHOLDER)}</text>\n"
        '<g clip-path="url(#screen)">\n' + "\n".join(body) + "\n</g>\n</svg>\n"
    )


def svg_lines(svg: str) -> list[str]:
    """The text of every terminal line in a rendered SVG, top to bottom."""
    screen = svg[svg.index('<g clip-path="url(#screen)">') :]
    return [
        html.unescape(re.sub(r"<[^>]+>", "", inner))
        for inner in re.findall(r'<text class="l [^"]*"[^>]*>(.*?)</text>', screen)
    ]


# --- the asciinema v2 cast -------------------------------------------------------------


def render_cast(steps: list[Step]) -> str:
    lines, total = timeline(steps)
    header = {
        "version": 2,
        "width": _columns(lines),
        "height": len(lines),
        "title": TITLE,
        "env": {"SHELL": "/bin/sh", "TERM": "xterm-256color"},
    }
    events: list[list] = []
    for line in lines:
        if line.typed:
            events.append([line.at, "o", "$ "])
            for i, char in enumerate(line.text[2:]):
                events.append([line.at + i * TYPE_S, "o", char])
            events.append([line.at + line.typed * TYPE_S, "o", "\r\n"])
        else:
            events.append([line.at, "o", line.text + "\r\n"])
    events.append([total, "o", ""])
    rows = [json.dumps(header, ensure_ascii=False)]
    rows += [json.dumps([round(t, 3), kind, data], ensure_ascii=False) for t, kind, data in events]
    return "\n".join(rows) + "\n"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="keel-demo-") as tmp:
        steps = capture(Path(tmp))
    for path, text in ((SVG_PATH, render_svg(steps)), (CAST_PATH, render_cast(steps))):
        _write(path, text)
        print(f"wrote {path.relative_to(REPO_ROOT).as_posix()}  ({len(text.encode())} bytes)")
    print(f"loop: {timeline(steps)[1]}s, {len(display_lines(steps))} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
