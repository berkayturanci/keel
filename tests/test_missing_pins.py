"""Pins for four fixes that shipped without a test that would notice their removal.

From a mutation audit of the 2026-08-19..24 batch: each landed fix was reverted
in a scratch copy and the relevant tests re-run. These survived (#931). A fix
nothing pins is a fix that leaves whenever someone rebases over it — which is
not hypothetical here: #934 documents exactly that happening to #811.
"""

from __future__ import annotations

import ast
import subprocess
import unittest
from pathlib import Path

from keel import gates, runner, swarm_runtime

REPO_ROOT = Path(__file__).resolve().parent.parent


class SwarmWorkersNeverInheritStdin(unittest.TestCase):
    """#879 named three subprocess sites needing ``stdin=subprocess.DEVNULL``.

    All three got the flag; reverting the `swarm_runtime` one left the suite
    green. A worker inheriting the parent's stdin is exactly the hang #879 was
    filed about, and nothing would have caught its return.
    """

    def test_default_runner_closes_stdin(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            raise OSError("stopped after capturing the call")

        original = swarm_runtime.subprocess.run
        swarm_runtime.subprocess.run = fake_run
        try:
            swarm_runtime.default_runner(["true"], Path("."))
        finally:
            swarm_runtime.subprocess.run = original

        self.assertEqual(subprocess.DEVNULL, captured.get("stdin"))

    @staticmethod
    def _spawn_sites():
        """Every place the package starts a subprocess, in either form it is written.

        Two forms, because #879 fixed sites one at a time and the sweep has to
        see all of them: a direct ``subprocess.run(...)``, and a call through the
        injected ``_run`` seam whose default is ``subprocess.run``. Matching only
        the first covers exactly one site today, which the vacuity guard below
        caught on the first attempt.
        """
        for path in sorted((REPO_ROOT / "src" / "keel").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                direct = (
                    getattr(func, "attr", None) == "run"
                    and getattr(getattr(func, "value", None), "id", None) == "subprocess"
                )
                seam = isinstance(func, ast.Name) and func.id == "_run"
                if direct or seam:
                    yield path, node

    def test_every_spawn_site_in_the_package_closes_stdin(self):
        """The rule, not the one site.

        A subprocess that inherits stdin can block forever waiting on a terminal
        that is not there. Any new call site has to say what it wants rather
        than inherit silently.
        """
        offenders = [
            f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
            for path, node in self._spawn_sites()
            if not any(kw.arg == "stdin" for kw in node.keywords)
        ]

        self.assertEqual([], offenders, "a subprocess is started without an explicit stdin")

    def test_the_sweep_has_call_sites_to_sweep(self):
        """Keeps the rule above from passing on a tree it cannot read."""
        found = [f"{p.relative_to(REPO_ROOT)}:{n.lineno}" for p, n in self._spawn_sites()]

        self.assertGreaterEqual(len(found), 3, f"expected the known spawn sites, found {found}")


class TheGateRunnerAliasCannotDrift(unittest.TestCase):
    """#876 asked the two aliases to agree; #896 copied the definition byte for
    byte, and reverting that copy left `test_runner.py` and `test_gates.py`
    fully green because nothing compared them.

    Duplication removed rather than pinned: `runner` re-exports the definition
    `gates` owns, so there is nothing left to drift.
    """

    def test_runner_reexports_the_gates_definition(self):
        self.assertIs(gates.GateRunner, runner.GateRunner)

    def test_runner_does_not_declare_its_own_alias(self):
        # The identity assertion above would also pass if someone re-declared an
        # identical alias in a future Python where equal aliases are interned.
        # This reads the source and refuses a second definition outright.
        tree = ast.parse((REPO_ROOT / "src" / "keel" / "runner.py").read_text(encoding="utf-8"))
        declared = [
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name) and target.id == "GateRunner"
        ]

        self.assertEqual([], declared, "runner.py declares its own GateRunner again")


class TheCopyButtonFixIsPinned(unittest.TestCase):
    """#916's JS change is correct and `tests/test_website_integrations.py` asserts
    only string presence on unrelated tokens — reverting the fix left the suite
    green, and #917's body claimed "100% test coverage" for a `.js` file the
    Python coverage run does not measure at all.

    **These assertions are source-level, not behavioural.** There is no DOM
    harness and no JS runtime in this project's dependencies, so what is pinned
    is that the specific mechanism survives, not that the button works. Stated
    plainly rather than left for a reader to assume from a green tick.
    """

    def _script(self) -> str:
        candidates = sorted((REPO_ROOT / "website").rglob("*.js"))
        scripts = [p for p in candidates if "origLabel" in p.read_text(encoding="utf-8")]
        self.assertEqual(1, len(scripts), f"expected one copy-button script, got {scripts}")
        return scripts[0].read_text(encoding="utf-8")

    def test_the_label_is_restored_from_a_saved_original(self):
        source = self._script()

        self.assertIn("origLabel", source)
        # Restoring from a captured value rather than a literal is the fix: a
        # second click mid-timeout must not save "Copied!" as the original.
        self.assertIn("clearTimeout", source)

    def test_a_pending_timer_is_cancelled_before_a_new_one_starts(self):
        source = self._script()

        self.assertLess(
            source.index("clearTimeout"),
            source.index("setTimeout"),
            "the pending timer must be cleared before the next is scheduled",
        )


class SwarmLandDryRunSharesTheLiveExitContract(unittest.TestCase):
    """#871's PR body and the CHANGELOG claimed non-zero on `partial_failure` for
    `swarm-run` **and** `swarm-land`. Measured, that was true of `--live` only:
    the dry-run arm hardcodes `failed_clusters=()`, so "held" is the only route
    to `partial_failure` there, and held returned 0.

    Resolved toward the preview being useful: a preview exists so a caller can
    gate on it, and "this wave would be held" is not a pass.
    """

    def test_the_cli_has_one_exit_rule_for_both_modes(self):
        source = (REPO_ROOT / "src" / "keel" / "cli.py").read_text(encoding="utf-8")
        marker = "def _cmd_swarm_land"
        body = source[source.index(marker) :]
        body = body[: body.index("\ndef ", 1)]

        returns = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("return 0 if")]

        self.assertEqual(
            ['return 0 if result.status == "success" and not result.held_clusters else 1'],
            returns,
            "swarm-land should reach one exit rule, not one per mode",
        )

    def test_the_changelog_no_longer_claims_what_the_code_did_not_do(self):
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertNotIn(
            "Returned non-zero exit code (1) on `partial_failure` status in "
            "`swarm-run` and `swarm-land` CLI commands.",
            changelog,
            "the corrected line should replace the claim, not sit beside it",
        )


if __name__ == "__main__":
    unittest.main()
