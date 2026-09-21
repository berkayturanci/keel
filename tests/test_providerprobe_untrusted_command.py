"""A provider probe must not execute a program the inspected project supplies (#1247).

`keel plan`, a `keel ship` dry-run and `keel swarm-plan` probe the configured coding-agent
CLIs to report who is reachable — and the command they probe is read from the project's own
`.keel/project.yaml`. When keel is pointed at a project it did not write (a clone, a fork's PR
branch), a `command` that names a path inside the checkout would be executed as
`<command> --version` / `<command> models` / `<command> --doctor --json`. A probe is a
read-only readiness check; it must run only PATH tools, never a relative path resolved against
the untrusted working tree.
"""

from __future__ import annotations

import os
import unittest

from keel import providerprobe, runner


def _result(ok=True, stdout="1.0.0\n"):
    return runner.CommandResult(ok=ok, code=0 if ok else 1, output=stdout, stdout=stdout)


def _recording_run(result=None):
    calls: list[list[str]] = []

    def run(argv, **_kwargs):
        calls.append(list(argv))
        return result if result is not None else _result()

    return run, calls


def _cli(command):
    return providerprobe.providers.Provider(
        name="x", vendor="cli", transport="cli", command=command
    )


class TestARelativeCommandIsNeverExecuted(unittest.TestCase):
    def test_repo_relative_detects_only_in_tree_paths(self):
        # A Windows drive-relative `C:evil` (isabs False, no separator) still resolves
        # against the current drive's cwd, so it must be caught too.
        for bad in ("scripts/x", "./x", "../x", "tools/agent.sh", r".\x", "C:evil", r"C:evil\x"):
            with self.subTest(command=bad):
                self.assertTrue(providerprobe._repo_relative(bad))
        for ok in ("claude", "cursor-agent", "", None):
            with self.subTest(command=ok):
                self.assertFalse(providerprobe._repo_relative(ok))
        # A path is safe to probe only where the running platform reads it as absolute; the
        # invariant is "bare name, or absolute here". `os.path.isabs` is platform-specific — a
        # POSIX `/usr/...` is not absolute on Windows (rooted but drive-less), and Python 3.13
        # dropped even the leading-slash special case — so assert the invariant, not a guess.
        for path in (r"C:\abs\claude.exe", "/usr/local/bin/claude", "/opt/tools/claude"):
            with self.subTest(command=path):
                self.assertEqual(providerprobe._repo_relative(path), not os.path.isabs(path))

    def test_cli_probe_refuses_a_relative_command_without_running_it(self):
        run, calls = _recording_run()
        found, reason, models = providerprobe._probe_cli(
            _cli("scripts/find_python.sh"), which=lambda _c: "/cwd/scripts/find_python.sh", run=run
        )
        self.assertFalse(found)
        self.assertEqual(calls, [])
        self.assertIn("path", reason.lower())
        self.assertEqual(models, ())

    def test_cli_probe_still_runs_a_bare_path_command(self):
        run, calls = _recording_run()
        found, _reason, _models = providerprobe._probe_cli(
            _cli("cursor-agent"), which=lambda _c: "/usr/bin/cursor-agent", run=run
        )
        self.assertTrue(found)
        self.assertEqual(calls[0], ["cursor-agent", "--version"])

    def test_cli_probe_still_runs_an_absolute_command(self):
        # An absolute path is an explicit operator choice; an attacker cannot predict a
        # victim's checkout location, so absolute paths are not the in-tree threat. Use a path
        # that is absolute on the running platform (Windows needs a drive letter).
        abs_cmd = r"C:\tools\claude.exe" if os.name == "nt" else "/opt/tools/claude"
        run, calls = _recording_run()
        providerprobe._probe_cli(_cli(abs_cmd), which=lambda _c: abs_cmd, run=run)
        self.assertEqual(calls[0], [abs_cmd, "--version"])

    def test_a_relative_command_never_reaches_the_models_call_either(self):
        # `_cli_models` runs `<command> models`; the guard in `_probe_cli` returns before
        # it, so the second execution site is covered by the same refusal.
        run, calls = _recording_run()
        agy = providerprobe.providers.Provider(
            name="agy", vendor="cli", transport="cli", command="./bin/agy", source="builtin"
        )
        found, _reason, models = providerprobe._probe_cli(
            agy, which=lambda _c: "./bin/agy", run=run
        )
        self.assertFalse(found)
        self.assertEqual(models, ())
        self.assertEqual(calls, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
