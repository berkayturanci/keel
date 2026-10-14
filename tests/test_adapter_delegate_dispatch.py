"""Adapter prose describes *policy*; `keel delegate run` owns the *argv* (#1012).

The `claude`/`codex`/`agy` argv shapes, the stdin framing, the Ollama endpoint and the
JSON return contract lived only as prose in `ship.md` s4/s7. Prose cannot be executed, so
every host agent re-implemented it and the copies drifted: a live run on 2026-09-03 wrote
its own argv, its own response parsing and its own return contract, recorded a vendor keel
does not use, and lost three reviewers' verdicts.

Moving the mechanics into core fixes that only for as long as the prose stays out of the
business. This sweep is what keeps it out: a vendor flag re-added to an adapter file is a
second source of truth the day it lands, and a second source of truth is the bug.

Both surfaces are swept — the adapter sources under ``src/keel/adapters/commands/`` and
every generated copy (``commands/``, ``.claude/commands/keel/``, ``.agents/skills/``) —
because the generated files are what an agent actually reads.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every file an agent could read a dispatch instruction out of.
_SURFACES = (
    "src/keel/adapters/commands/*.md",
    "commands/*.md",
    ".claude/commands/keel/*.md",
    ".agents/skills/keel-*/SKILL.md",
)

#: Vendor invocation fragments that must never reappear. Each was in the prose before
#: #1012 and is now a property of :mod:`keel.delegate`, asserted in ``tests/test_delegate``.
_FORBIDDEN = {
    "codex exec": "the codex argv belongs to keel.delegate, not to adapter prose",
    "agy --print": "the agy invocation belongs to keel.delegate, not to adapter prose",
    "agy --": "the agy argv belongs to keel.delegate, not to adapter prose",
    "claude -p": "the claude argv belongs to keel.delegate, not to adapter prose",
    "--dangerously-skip-permissions": "a permission flag is core's to choose, not prose's",
    "--disallowed-tools": "the read-only tool list is core's, not prose's",
    "--allowed-tools": "the read-only tool list is core's, not prose's",
    "model_reasoning_effort": "codex's effort spelling is core's, not prose's",
    "-s read-only": "the codex sandbox flag is core's, not prose's",
    "-s workspace-write": "the codex sandbox flag is core's, not prose's",
    "--input-format stream-json": "agy's stdin framing is core's, not prose's",
    "/api/generate": "the Ollama endpoint is a hardcoded constant in core, not prose",
    "/api/tags": "the Ollama endpoint is a hardcoded constant in core, not prose",
    "prompt_mode": "how a prompt is delivered is core's decision, not prose's",
    "role_args(": "role flag selection is core's, not prose's",
}

#: The command every adapter must route dispatch through, and the files that must say so.
_DISPATCH_COMMAND = "keel delegate run"
_MUST_DISPATCH = ("ship.md", "implement.md", "review-cycle.md")

#: What an orchestrator must be told to use instead of polling.
_WAIT_COMMAND = "keel delegate wait"

#: Prose that contradicts the code. The resolution order was inverted in four places at
#: once — the adapter source, its three generated copies, `providers.plan_probes` and
#: `configuration.md` — because each restated it in its own words rather than pointing at
#: one place. An operator who reads any of them and lets a registry entry shadow `claude`
#: gets a `keel doctor --providers` error they were told to expect to work.
_STALE_PRECEDENCE = re.compile(r"profile\s*>\s*(?:machine\s+)?registry\s*>\s*built-in")

#: Fields of the return contract an adapter must actually name, because reading the wrong
#: one is a silent failure rather than an error. `read_only` alone reports the role that
#: was *asked for*; only `read_only_backed` says whether anything enforces it.
_MUST_NAME = ("read_only_backed",)

#: Sleep-and-poll advice: a loop in the host's own turn cannot outlive the turn.
_SLEEP_ADVICE = re.compile(r"\bsleep\s+\d|\bsleep loop\b|poll(ing)? (in a )?loop", re.IGNORECASE)


def _adapter_files():
    for pattern in _SURFACES:
        yield from sorted(REPO_ROOT.glob(pattern))


class AdapterProseCarriesNoVendorArgv(unittest.TestCase):
    def test_the_sweep_has_files_to_sweep(self):
        """Keeps every rule below from passing on a tree it cannot read."""
        files = list(_adapter_files())
        self.assertGreaterEqual(len(files), 60, f"expected the adapter surfaces, found {files}")

    def test_no_adapter_file_spells_a_vendor_invocation(self):
        offenders = []
        for path in _adapter_files():
            text = path.read_text(encoding="utf-8")
            for fragment, why in _FORBIDDEN.items():
                if fragment in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {fragment!r} — {why}")
        self.assertEqual([], offenders, "\n".join(offenders))

    def test_the_dispatching_adapters_route_through_the_command(self):
        for name in _MUST_DISPATCH:
            for path in _adapter_files():
                if path.name == name or path.parent.name.endswith(name[:-3]):
                    with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                        self.assertIn(_DISPATCH_COMMAND, path.read_text(encoding="utf-8"))

    def test_no_adapter_states_the_inverted_resolution_order(self):
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in _adapter_files()
            if _STALE_PRECEDENCE.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual([], offenders, "resolution order is built-in > profile > registry")

    def test_a_dispatching_adapter_names_the_field_that_gates_a_reviewer(self):
        for name in _MUST_DISPATCH:
            for path in _adapter_files():
                if path.name == name or path.parent.name.endswith(name[:-3]):
                    text = path.read_text(encoding="utf-8")
                    if _DISPATCH_COMMAND not in text:
                        continue
                    for field in _MUST_NAME:
                        with self.subTest(path=str(path.relative_to(REPO_ROOT)), field=field):
                            self.assertIn(field, text)

    def test_every_detach_example_bounds_the_run(self):
        """A detached run with no `--timeout` has no deadline, so a killed child is
        indistinguishable from a slow one and `wait` can block forever."""
        for path in _adapter_files():
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                if "--detach" not in line:
                    continue
                with self.subTest(path=str(path.relative_to(REPO_ROOT)), line=line.strip()):
                    # the flag may sit on a continuation line of the same example
                    block = text[max(0, text.index(line) - 400) : text.index(line) + 400]
                    self.assertIn("--timeout", block)

    def test_wrap_adapter_stages_before_checking_staged_contamination(self):
        wrap_md = (REPO_ROOT / "src/keel/adapters/commands/wrap.md").read_text(encoding="utf-8")
        step3 = wrap_md.split("## Step 3 — Commit", 1)[1]
        self.assertLess(
            step3.index("git add -A"),
            step3.index("keel wrap .keel/project.yaml"),
            "git add -A must precede the staged contamination check so unstaged edits are caught",
        )

    def test_ship_and_review_cycle_adapters_use_safe_isolated_checkouts(self):
        import os
        import subprocess
        import tempfile

        ship_md = (REPO_ROOT / "src/keel/adapters/commands/ship.md").read_text(encoding="utf-8")
        review_cycle_md = (REPO_ROOT / "src/keel/adapters/commands/review-cycle.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("read-only checkout", ship_md)
        self.assertNotIn("read-only checkout", review_cycle_md)
        self.assertIn("separate detached checkout", review_cycle_md)
        self.assertIn("git fetch origin", review_cycle_md)
        self.assertIn("SLOT_INDEX", ship_md)
        self.assertNotIn('review-$RUN_ID-$SLOT"', ship_md)
        self.assertNotIn("-$$", ship_md)
        self.assertNotIn("-$$", review_cycle_md)
        self.assertNotIn("rather than running the test suite", ship_md)
        self.assertNotIn("when the index is empty or neither baseline flag is passed", ship_md)

        from keel import cli

        parser = cli.build_parser()
        # Verify real CLI parser accepts the exact flags used in ship.md snippets:
        parsed_sync = parser.parse_args(
            [
                "delegate",
                "run",
                "--provider",
                "claude",
                "--role",
                "review",
                "--prompt-file",
                "/tmp/p.md",
                "--cwd",
                "/tmp/wt",
                "--timeout",
                "900",
                "--project",
                ".keel/project.yaml",
                "--activity-run-id",
                "r1",
            ]
        )
        self.assertEqual(parsed_sync.role, "review")
        parsed_detach = parser.parse_args(
            [
                "delegate",
                "run",
                "--provider",
                "claude",
                "--role",
                "review",
                "--prompt-file",
                "/tmp/p.md",
                "--cwd",
                "/tmp/wt",
                "--timeout",
                "900",
                "--project",
                ".keel/project.yaml",
                "--detach",
                "--run-id",
                "r1-slot-0",
                "--root",
                "/tmp/root",
                "--activity-run-id",
                "r1",
            ]
        )
        self.assertTrue(parsed_detach.detach)
        parsed_wait = parser.parse_args(
            ["delegate", "wait", "r1-slot-0", "--root", "/tmp/root", "--timeout", "900"]
        )
        self.assertEqual(parsed_wait.run_id, "r1-slot-0")
        parsed_rm = parser.parse_args(["worktree-remove", "/tmp/wt", "--root", "/tmp/root"])
        self.assertEqual(parsed_rm.worktree, "/tmp/wt")

        # Extract the s7 bash blocks from ship.md and verify their behaviour:
        blocks = re.findall(r"```bash\n(.*?)```", ship_md, re.DOTALL)
        jury_block = next(b for b in blocks if "JURY_WT=" in b)
        self.assertIn("keel review .keel/project.yaml", jury_block)
        jury_panel_script = jury_block.replace("<PR>", "42")
        reviewer_block = next(b for b in blocks if "--role review" in b and "REVIEW_WT=" in b)
        slot_script, gate_script = reviewer_block.split("# the gate review", 1)
        gate_script = "ROOT=$(pwd)\nHEAD_SHA=$(git rev-parse HEAD)\n" + gate_script
        detach_block = next(b for b in blocks if "--detach" in b and "REVIEW_WT=" in b)
        detach_launch_script, detach_collect_script = detach_block.split("# collect + cleanup", 1)
        detach_collect_script = "# collect + cleanup" + detach_collect_script

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            log_file = root / "calls.log"
            (root / "diff.patch").write_text("diff\n", encoding="utf-8")
            (root / "jury.toml").write_text("[jury]\n", encoding="utf-8")
            (root / "jury-policy.toml").write_text("[policy]\n", encoding="utf-8")
            (root / ".jury" / "sub").mkdir(parents=True)
            (root / ".jury" / "tracked.toml").write_text("local = true\n", encoding="utf-8")
            (root / ".jury" / "untracked.toml").write_text("extra = true\n", encoding="utf-8")
            (root / ".jury" / "sub" / "nested.toml").write_text("n = 1\n", encoding="utf-8")

            (bin_dir / "git").write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "rev-parse" ]; then echo "abc1234"; exit 0; fi\n'
                'echo "git $*" >> "$CALL_LOG"\n'
                'if [ "$FAIL_WORKTREE_ADD" = "1" ]; then exit 128; fi\n'
                'case "$*" in *review-r1-slot-0*)'
                ' [ "$FAIL_REVIEW_WORKTREE_ADD" = "1" ] && exit 128 ;; esac\n'
                'case "$*" in *review-r1-gate*)'
                ' [ "$FAIL_GATE_WORKTREE_ADD" = "1" ] && exit 128 ;; esac\n'
                'mkdir -p "$4/.jury"\n'
                'printf "committed = true\\n" > "$4/.jury/tracked.toml"\n'
                "exit 0\n",
                encoding="utf-8",
            )
            (bin_dir / "keel").write_text(
                '#!/bin/sh\necho "keel $*" >> "$CALL_LOG"\n'
                'if [ "$1" = "worktree-remove" ]; then\n'
                '  [ "$FAIL_WORKTREE_REMOVE" = "1" ] && exit 1\n'
                '  rm -rf "$2"\n'
                "  exit 0\n"
                "fi\n"
                'case "$*" in *"--role review"*)'
                ' [ "$FAIL_REVIEW_DELEGATE" = "1" ] && exit 2 ;; esac\n'
                'case "$*" in *"--role gate"*)'
                ' [ "$FAIL_GATE_DELEGATE" = "1" ] && exit 3 ;; esac\n'
                "exit 0\n",
                encoding="utf-8",
            )
            (bin_dir / "jury").write_text(
                "#!/bin/sh\n"
                'echo "jury $*" >> "$CALL_LOG"\n'
                'cat .jury/tracked.toml > "$JURY_CHECK_TRACKED"\n'
                'cat .jury/untracked.toml > "$JURY_CHECK_UNTRACKED"\n'
                'cat .jury/sub/nested.toml > "$JURY_CHECK_NESTED"\n'
                'if [ "$FAIL_JURY" = "1" ]; then exit 4; fi\n'
                "exit 0\n",
                encoding="utf-8",
            )
            (bin_dir / "cp").write_text(
                "#!/bin/sh\n"
                'if [ -n "$FAIL_CP_MATCH" ]; then\n'
                '  case "$*" in *"$FAIL_CP_MATCH"*) exit 1 ;; esac\n'
                "fi\n"
                'exec /bin/cp "$@"\n',
                encoding="utf-8",
            )
            os.chmod(bin_dir / "git", 0o755)
            os.chmod(bin_dir / "keel", 0o755)
            os.chmod(bin_dir / "jury", 0o755)
            os.chmod(bin_dir / "cp", 0o755)

            env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
                "CALL_LOG": str(log_file),
                "RUN_ID": "r1",
                "SLOT_INDEX": "0",
                "SLOT_PROVIDER": "claude",
                "GATE_PROVIDER": "codex",
                "RUBRIC_AND_DIFF": "/tmp/prompt.md",
                "DIFF": str(root / "diff.patch"),
                "JURY_CHECK_TRACKED": str(root / "checked_tracked.txt"),
                "JURY_CHECK_UNTRACKED": str(root / "checked_untracked.txt"),
                "JURY_CHECK_NESTED": str(root / "checked_nested.txt"),
            }

            # 1. When git worktree add fails, keel delegate run must NOT be dispatched
            res_fail = subprocess.run(
                ["bash", "-c", slot_script],
                cwd=root,
                env={**env, "FAIL_WORKTREE_ADD": "1"},
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res_fail.returncode, 0)
            calls_on_fail = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
            self.assertNotIn("keel delegate run", calls_on_fail)
            self.assertNotIn("keel worktree-remove", calls_on_fail)

            # 2. When git worktree add succeeds, delegate run executes and worktree-remove cleans up
            log_file.unlink(missing_ok=True)
            res_ok = subprocess.run(
                ["bash", "-c", slot_script],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_ok.returncode, 0, res_ok.stderr)
            calls_on_ok = log_file.read_text(encoding="utf-8").splitlines()
            self.assertTrue(any(c.startswith("git worktree add -d ") for c in calls_on_ok))
            self.assertTrue(any(c.startswith("keel delegate run ") for c in calls_on_ok))
            self.assertTrue(
                any(
                    c.startswith("keel worktree-remove ") and f"--root {root.resolve()}" in c
                    for c in calls_on_ok
                )
            )

            # 3. Gate block executes in isolated checkout and cleans up
            log_file.unlink(missing_ok=True)
            res_gate = subprocess.run(
                ["bash", "-c", gate_script],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_gate.returncode, 0, res_gate.stderr)
            gate_calls = log_file.read_text(encoding="utf-8").splitlines()
            self.assertTrue(any("--role gate" in c for c in gate_calls))
            self.assertTrue(any("review-r1-gate" in c for c in gate_calls))

            # 3b. Combined synchronous reviewer+gate snippet fails closed when either checkout
            # or delegate dispatch fails, even when the other dispatch succeeds
            with self.subTest(case="combined_reviewer_worktree_add_fails"):
                log_file.unlink(missing_ok=True)
                res_combined_wt_fail = subprocess.run(
                    ["bash", "-c", reviewer_block],
                    cwd=root,
                    env={**env, "FAIL_REVIEW_WORKTREE_ADD": "1"},
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(res_combined_wt_fail.returncode, 0)
                combined_wt_calls = log_file.read_text(encoding="utf-8")
                self.assertNotIn("--role review", combined_wt_calls)

            with self.subTest(case="combined_reviewer_delegate_fails"):
                log_file.unlink(missing_ok=True)
                res_combined_rev_fail = subprocess.run(
                    ["bash", "-c", reviewer_block],
                    cwd=root,
                    env={**env, "FAIL_REVIEW_DELEGATE": "1"},
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(res_combined_rev_fail.returncode, 0)
                combined_rev_calls = log_file.read_text(encoding="utf-8").splitlines()
                self.assertTrue(
                    any(
                        c.startswith("keel worktree-remove ") and "review-r1-slot-0" in c
                        for c in combined_rev_calls
                    )
                )

            with self.subTest(case="combined_gate_worktree_add_fails"):
                log_file.unlink(missing_ok=True)
                res_combined_gate_wt_fail = subprocess.run(
                    ["bash", "-c", reviewer_block],
                    cwd=root,
                    env={**env, "FAIL_GATE_WORKTREE_ADD": "1"},
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(res_combined_gate_wt_fail.returncode, 0)
                combined_gate_wt_calls = log_file.read_text(encoding="utf-8")
                self.assertNotIn("--role gate", combined_gate_wt_calls)

            with self.subTest(case="combined_gate_delegate_fails"):
                log_file.unlink(missing_ok=True)
                res_combined_gate_fail = subprocess.run(
                    ["bash", "-c", reviewer_block],
                    cwd=root,
                    env={**env, "FAIL_GATE_DELEGATE": "1"},
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(res_combined_gate_fail.returncode, 0)
                combined_gate_calls = log_file.read_text(encoding="utf-8").splitlines()
                self.assertTrue(
                    any(
                        c.startswith("keel worktree-remove ") and "review-r1-gate" in c
                        for c in combined_gate_calls
                    )
                )

            # 4. Jury block merges untracked .jury files while preserving tracked .jury files
            # and converts ballots via keel review --from-jury on success
            log_file.unlink(missing_ok=True)
            res_jury = subprocess.run(
                ["bash", "-c", jury_panel_script],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_jury.returncode, 0, res_jury.stderr)
            self.assertEqual(
                (root / "checked_tracked.txt").read_text(encoding="utf-8"),
                "committed = true\n",
            )
            self.assertEqual(
                (root / "checked_untracked.txt").read_text(encoding="utf-8"),
                "extra = true\n",
            )
            self.assertEqual(
                (root / "checked_nested.txt").read_text(encoding="utf-8"),
                "n = 1\n",
            )
            jury_ok_calls = log_file.read_text(encoding="utf-8").splitlines()
            self.assertTrue(
                any(
                    c.startswith("keel review ") and "--from-jury .keel/state/jury/r1.json" in c
                    for c in jury_ok_calls
                ),
                jury_ok_calls,
            )

            # 4b. Complete jury block fails closed before step 2 evidence conversion (even with
            # an existing $RUN_ID.json on disk) when checkout, copy, jury, or cleanup fails
            existing_report = root / ".keel" / "state" / "jury" / "r1.json"
            existing_report.parent.mkdir(parents=True, exist_ok=True)
            for fail_target in ("jury-policy.toml", "untracked.toml"):
                with self.subTest(fail_target=fail_target):
                    existing_report.write_text('{"stale": true}\n', encoding="utf-8")
                    log_file.unlink(missing_ok=True)
                    res_jury_cp_fail = subprocess.run(
                        ["bash", "-c", jury_panel_script],
                        cwd=root,
                        env={**env, "FAIL_CP_MATCH": fail_target},
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(res_jury_cp_fail.returncode, 0)
                    cp_fail_calls = log_file.read_text(encoding="utf-8").splitlines()
                    self.assertFalse(any(c.startswith("jury ") for c in cp_fail_calls))
                    self.assertFalse(any(c.startswith("keel review ") for c in cp_fail_calls))
                    self.assertTrue(
                        any(
                            c.startswith("keel worktree-remove ") and "review-r1-jury" in c
                            for c in cp_fail_calls
                        )
                    )

            for fail_case, fail_env in (
                ("worktree_add", {"FAIL_WORKTREE_ADD": "1"}),
                ("jury_exec", {"FAIL_JURY": "1"}),
                ("worktree_remove", {"FAIL_WORKTREE_REMOVE": "1"}),
            ):
                with self.subTest(jury_fail_case=fail_case):
                    existing_report.write_text('{"stale": true}\n', encoding="utf-8")
                    log_file.unlink(missing_ok=True)
                    res_jury_fail = subprocess.run(
                        ["bash", "-c", jury_panel_script],
                        cwd=root,
                        env={**env, **fail_env},
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(res_jury_fail.returncode, 0)
                    jury_fail_calls = (
                        log_file.read_text(encoding="utf-8").splitlines()
                        if log_file.exists()
                        else []
                    )
                    self.assertFalse(
                        any(c.startswith("keel review ") for c in jury_fail_calls),
                        jury_fail_calls,
                    )

            # 5. Detached launch and collect+cleanup run in separate fresh shells
            log_file.unlink(missing_ok=True)
            res_launch = subprocess.run(
                ["bash", "-c", detach_launch_script],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_launch.returncode, 0, res_launch.stderr)
            res_collect = subprocess.run(
                ["bash", "-c", detach_collect_script],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_collect.returncode, 0, res_collect.stderr)
            detach_calls = log_file.read_text(encoding="utf-8").splitlines()
            self.assertTrue(
                any(
                    c.startswith("keel delegate wait r1-slot-0 ")
                    and f"--root {root.resolve()}" in c
                    for c in detach_calls
                ),
                detach_calls,
            )
            self.assertTrue(
                any(
                    c.startswith("keel worktree-remove ")
                    and f"{root.resolve()}/.keel/worktrees/review-r1-slot-0" in c
                    and f"--root {root.resolve()}" in c
                    for c in detach_calls
                ),
                detach_calls,
            )

            # 5b. Detached collect propagates worktree-remove failure even when wait succeeded
            res_collect_rm_fail = subprocess.run(
                ["bash", "-c", detach_collect_script],
                cwd=root,
                env={**env, "FAIL_WORKTREE_REMOVE": "1"},
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res_collect_rm_fail.returncode, 0)

    def test_the_detach_primitive_replaces_sleep_polling_advice(self):
        for path in _adapter_files():
            text = path.read_text(encoding="utf-8")
            match = _SLEEP_ADVICE.search(text)
            if match is None:
                continue
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                # A file may only mention polling in order to forbid it, and only where
                # it also names the primitive that replaces it.
                self.assertIn(_WAIT_COMMAND, text, f"{match.group(0)!r} with no wait primitive")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
