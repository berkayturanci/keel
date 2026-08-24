"""The evidence gate's *reported* verdict must match the verdict it computed.

#849 gave ``keel evidence-verify`` a third status and wired the workflow to
``exit 0`` for it. A job that exits 0 concludes green, so a check named for the
evidence gate reported success while zero verdicts existed — worse than the red
it replaced, because red at least said "not yet" (#928).

The library half of that lifecycle is covered by ``test_evidence.py``. Nothing
covered the half a human looks at: the check GitHub shows.

These parse the workflow and slice each ``case`` arm, then assert on **that
arm's** status and conclusion. An earlier draft asserted only that each
conclusion string appeared *somewhere* in the step, which a review showed was
no guard at all: swapping the pass and fail arms, or hardcoding every
conclusion to ``success``, left the whole suite green. Every assertion below is
per-arm for that reason.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github/workflows/keel-ship.yml"
DOC = REPO / "docs/keel/evidence.md"

#: The check-run the gate publishes. This is the name that belongs in branch
#: protection, and it must differ from every job name in the file — Actions
#: names a job's own check after the job, and branch protection matches by name.
CHECK_NAME = "keel evidence (required)"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _evidence_run() -> str:
    """The shell body of the evidence job's verification step."""
    steps = _workflow()["jobs"]["evidence"]["steps"]
    step = next((s for s in steps if s.get("name") == "Verify posted ship evidence"), None)
    assert step is not None, "the evidence-verification step is gone"
    return step["run"]


def _case_arm(label: str) -> str:
    """One arm of the `case "$RC"` block, sliced on line-anchored labels.

    Anchoring to a line that is only the label plus ``)`` keeps a comment that
    happens to contain ``2)`` or ``*)`` from silently shifting the slice — the
    previous substring search could produce an empty arm and a passing test.
    """
    run = _evidence_run()
    labels = ["0)", "2)", "*)"]
    bounds = {}
    for name in labels:
        match = re.search(rf"^\s*{re.escape(name)}\s*$", run, re.MULTILINE)
        assert match, f"the case arm {name!r} is gone"
        bounds[name] = (match.start(), match.end())
    start = bounds[label][1]
    later = [s for (s, _) in (bounds[n] for n in labels) if s > bounds[label][0]]
    return run[start : min(later)] if later else run[start:]


class TestEachArmReportsItsOwnVerdict(unittest.TestCase):
    """Pass, waiting and fail must set three *different* verdicts."""

    #: (case label, STATUS, CONCLUSION or None, FALLBACK_EXIT)
    ARMS = (
        ("0)", "completed", "success", "0"),
        ("2)", "in_progress", None, "1"),
        ("*)", "completed", "failure", "1"),
    )

    def test_each_arm_sets_its_own_status_and_conclusion(self):
        for label, status, conclusion, _ in self.ARMS:
            arm = _case_arm(label)
            with self.subTest(arm=label):
                self.assertIn(f"STATUS={status}", arm)
                if conclusion is None:
                    # The waiting arm must set NO conclusion. An incomplete check
                    # blocks a merge; every conclusion GitHub accepts as "not
                    # failing" — success, skipped, neutral — satisfies a required
                    # check, which is the #928 defect by another route.
                    self.assertIn('CONCLUSION=""', arm)
                    self.assertNotRegex(arm, r"CONCLUSION=(success|neutral|skipped)")
                else:
                    self.assertIn(f"CONCLUSION={conclusion}", arm)

    def test_no_two_arms_report_the_same_verdict(self):
        """Kills a wholesale swap, and one verdict hardcoded for every state."""
        seen = []
        for label, _, _, _ in self.ARMS:
            arm = _case_arm(label)
            status = re.search(r"STATUS=(\S+)", arm)
            conclusion = re.search(r"CONCLUSION=(\S*)", arm)
            self.assertIsNotNone(status, f"the {label} arm sets no status")
            seen.append((status.group(1), (conclusion.group(1) if conclusion else "")))
        self.assertEqual(len(set(seen)), len(seen), f"two arms report the same: {seen}")

    def test_only_the_waiting_arm_lets_the_job_stay_green_without_a_check(self):
        """FALLBACK_EXIT is the fork path's verdict; it must not be 0 while waiting."""
        for label, _, _, fallback in self.ARMS:
            with self.subTest(arm=label):
                self.assertIn(f"FALLBACK_EXIT={fallback}", _case_arm(label))


class TestTheGateCannotReportSilently(unittest.TestCase):
    def test_the_check_is_published_and_pinned_to_the_head(self):
        run = _evidence_run()
        self.assertIn("check-runs", run, "the step no longer creates a check-run")
        self.assertIn('name=${CHECK_NAME}"', run.replace("'", '"'))
        self.assertIn('head_sha=${HEAD_SHA}', run, "the check must be pinned to the head")

    def test_the_published_name_differs_from_every_job_name(self):
        """Two same-named checks on one commit are indistinguishable to protection.

        Actions names a job's own check after the job, and that check is driven
        by the exit code — it can only ever say "the job ran". If the job the
        gate lives in were also called `keel evidence (required)`, the green
        exit-code check #928 reports would still be sitting there under the
        gating name.
        """
        workflow = _workflow()
        job_names = {job.get("name") for job in workflow["jobs"].values()}
        self.assertNotIn(
            CHECK_NAME,
            job_names,
            f"a job is named {CHECK_NAME!r}, colliding with the published check-run",
        )

    def test_the_step_declares_the_check_name_it_publishes(self):
        env = next(
            s for s in _workflow()["jobs"]["evidence"]["steps"]
            if s.get("name") == "Verify posted ship evidence"
        )["env"]
        self.assertEqual(env.get("CHECK_NAME"), CHECK_NAME)

    def test_the_check_is_upserted_never_blindly_created(self):
        """`POST /check-runs` has no upsert, so every run would stack another.

        Branch protection matches by name; several same-named checks on one
        commit — at least one permanently incomplete — is the exact ambiguity
        the job rename above exists to avoid. Reproducing it against ourselves
        would be no improvement.
        """
        body = self._publisher()
        self.assertIn("-X PATCH", body, "an existing check-run is never updated")
        self.assertIn("-X POST", body, "a first check-run is never created")
        self.assertRegex(
            body,
            r'if\s+\[\s+-n\s+"\$existing"\s+\]',
            "the publisher does not branch on whether a check-run already exists",
        )

    def test_the_lookup_is_a_GET(self):
        """`-f` alone switches gh to POST, which 404s on this endpoint.

        Verified against the live API: without `--method GET` the lookup returns
        "Not Found", so `existing` is always empty and every run POSTs a new
        check — silently restoring the stacking this replaces, with no error.
        """
        body = self._publisher()
        # On the invocation, not merely somewhere in the block: the comment above
        # it explains the flag, so a bare `in` check passes with the flag removed.
        self.assertRegex(
            body,
            r"existing=\$\(gh api --method GET",
            "the check-run lookup is not forced to GET, so gh will POST and 404",
        )
        self.assertIn("check_name=${CHECK_NAME}", body)

    def test_a_fork_falls_back_to_the_exit_code_instead_of_dying(self):
        """A fork PR's token is read-only whatever `permissions:` declares.

        Exiting 1 unconditionally would leave every fork contribution red with
        no route forward — including one whose evidence verified. The job's exit
        code carries the verdict instead, which is green only for a real pass.
        """
        run = _evidence_run()
        self.assertIn('IS_FORK', run, "the fork case is not detected")
        self.assertIn('exit "$FALLBACK_EXIT"', run, "the fork path ignores the verdict")
        fork_branch = run[run.index('if [ "$IS_FORK"') :]
        self.assertIn("::warning", fork_branch, "the fork path fails silently")

    def test_a_non_fork_that_cannot_publish_still_fails(self):
        """Fail-closed survives the fork escape hatch."""
        run = _evidence_run()
        tail = run[run.index('if [ "$IS_FORK"') :]
        after = tail[tail.index("fi") :]
        self.assertIn("::error", after)
        self.assertIn("exit 1", after, "a non-fork publish failure no longer fails")

    def _publisher(self) -> str:
        body = re.search(r"publish_check\(\)\s*\{(.*?)\n\}", _evidence_run(), re.DOTALL)
        self.assertIsNotNone(body, "the publisher is gone")
        return body.group(1)

    def test_the_publisher_does_not_swallow_its_own_failure(self):
        """`|| true` anywhere in the publisher defeats every guard above."""
        for swallow in ("|| true", "|| :", "; true"):
            with self.subTest(swallow=swallow):
                self.assertNotIn(swallow, self._publisher())

    def test_the_publisher_forwards_the_verdict_it_was_given(self):
        """The per-arm tests read call sites; this reads what actually goes out.

        Hardcoding ``conclusion=success`` inside the publisher deletes the
        whole behaviour of this change while every call site still reads
        correctly — a review caught exactly that mutation surviving.
        """
        body = self._publisher()
        self.assertIn(
            'conclusion=${conclusion}',
            body,
            "the publisher sends a literal conclusion instead of the one it was given",
        )
        self.assertIn(
            'status=${status}',
            body,
            "the publisher sends a literal status instead of the one it was given",
        )
        self.assertNotRegex(
            body,
            r'conclusion=(success|neutral|failure|skipped)\b',
            "the publisher hardcodes a conclusion",
        )
        # The conditional is what lets the waiting arm publish no conclusion at
        # all. Without it an incomplete run would carry one and complete itself.
        self.assertRegex(
            body,
            r'if\s+\[\s+-n\s+"\$conclusion"\s+\]',
            "the conclusion is no longer optional, so the waiting arm cannot stay incomplete",
        )

    def test_dispatch_runs_do_not_stamp_the_default_branch(self):
        """`workflow_dispatch` has no pull_request payload, and check-runs are permanent.

        Falling back to `$GITHUB_SHA` would write PR N's verdict onto whatever
        ref the run was dispatched from — normally the default branch.
        """
        run = _evidence_run()
        self.assertNotIn("github.sha", run, "the head falls back to the dispatched ref")
        self.assertIn("gh pr view", run, "no fallback resolves the PR's real head")
        self.assertIn("headRefOid", run)

    def test_the_gate_is_armed_and_scoped_to_the_pre_merge_phase(self):
        """Two flags carry the gate's whole meaning, and neither was pinned.

        `--require-armed`: without it an unarmed gate derives no requirements
        and *passes*, having verified nothing — indistinguishable from a real
        pass, which is this issue's defect in its purest form.

        `--phase pre-merge`: the closure comments are an s11 artifact posted
        after the merge this gate authorizes, so requiring them here is
        unsatisfiable and the gate could never go green.

        Both predate this change and were equally untested on main; removing
        either left the suite green.

        Asserted against the ``ARGS=(...)`` assignment, not the step body. Each
        flag is also named in the comment that explains it, so a plain ``in``
        check finds the comment and passes with the flag stripped from the
        command — the third time that trap has caught a test in this file.
        """
        args = re.search(r"^\s*ARGS=\((.*?)\)\s*$", _evidence_run(), re.MULTILINE)
        self.assertIsNotNone(args, "the evidence-verify invocation is gone")
        self.assertIn("--require-armed", args.group(1), "an unarmed gate would report success")
        self.assertIn(
            "--phase pre-merge", args.group(1), "the gate would demand post-merge artifacts"
        )

    def test_the_workflow_holds_the_permission_it_needs(self):
        self.assertEqual(_workflow()["permissions"].get("checks"), "write")


class TestTheDocsDescribeWhatShips(unittest.TestCase):
    """#928's third point: the table documented the ask, not the behaviour."""

    def test_the_docs_state_that_the_check_is_actually_published(self):
        """Naming the check is not enough — the prose has to assert it exists.

        A doc that merely mentions the name can be reworded to describe it as
        aspirational without failing anything, which is how #928's table came
        to document the ask rather than the behaviour.
        """
        text = DOC.read_text(encoding="utf-8")
        self.assertIn(CHECK_NAME, text)
        self.assertRegex(
            text,
            rf"publishes these as a real check-run named\s*\n?\*\*`{re.escape(CHECK_NAME)}`\*\*",
            "the docs no longer state that the check-run is actually published",
        )

    def test_the_docs_do_not_repeat_the_neutral_claim(self):
        """`neutral` satisfies a required check; saying otherwise is the bug.

        GitHub: "Required status checks must have a `successful`, `skipped`, or
        `neutral` status before collaborators can make changes to a protected
        branch." #829 assumed the opposite and this doc inherited it.
        """
        text = DOC.read_text(encoding="utf-8").lower()
        self.assertNotRegex(
            text,
            r"neutral[^.\n]{0,80}(blocks|does not satisfy|still blocked)",
            "the docs claim a neutral conclusion blocks a merge; it does not",
        )

    def test_the_docs_warn_against_requiring_the_wrong_check(self):
        """Requiring the *job* would reinstate #928 — it exits 0 while waiting.

        The workflow comment explains the split, but the page an adopter reads
        while configuring branch protection is the one that has to say it.
        """
        page = (REPO / "docs/keel/github-actions.md").read_text(encoding="utf-8")
        self.assertRegex(
            page,
            r"[Dd]o \*\*not\*\* add `keel evidence \(verify\)`",
            "the adoption page does not warn against requiring the job",
        )

    def test_the_docs_permissions_snippet_grants_what_the_gate_needs(self):
        """An adopter copies that block; without `checks: write` they get a 403.

        The workflow's own permissions are pinned above, and the doc's copy
        drifted from them — invisible to CI, and the failure it produces is a
        required check that simply never appears.
        """
        page = (REPO / "docs/keel/github-actions.md").read_text(encoding="utf-8")
        snippet = page[page.index("permissions:") :][:400]
        self.assertIn("checks: write", snippet)

    #: Where each source talks about unsticking a fork PR. The assertion has to
    #: be scoped to this text: every one of these files mentions
    #: `workflow_dispatch` elsewhere for unrelated reasons — a trigger list, a
    #: YAML snippet, a comment — so a file-wide `assertIn` passes with the
    #: recovery paragraph gutted. That is the fourth time in this file a test has
    #: found its subject in surrounding prose instead of in the thing under test.
    FORK_RECOVERY = (
        ("github-actions.md", "docs/keel/github-actions.md", "Recovery is **not**", "\n\n##"),
        ("evidence.md", "docs/keel/evidence.md", "Recovery is **Run workflow**", "\n\n"),
    )

    def _fork_paragraph(self, path: str, start: str, end: str) -> str:
        text = (REPO / path).read_text(encoding="utf-8")
        self.assertIn(start, text, f"{path} no longer explains fork recovery")
        tail = text[text.index(start) :]
        cut = tail.find(end)
        return tail if cut == -1 else tail[:cut]

    def test_the_fork_recovery_path_is_the_one_that_works(self):
        """"Re-run all jobs" replays the same read-only token and fails identically.

        All three places that tell a maintainer how to unstick a fork PR — both
        docs and the runtime warning — said to re-run. The path that actually
        works is `workflow_dispatch` from the base repository.
        """
        for name, path, start, end in self.FORK_RECOVERY:
            with self.subTest(source=name):
                para = self._fork_paragraph(path, start, end)
                self.assertIn("workflow_dispatch", para, "the working path is not named")
                self.assertIn("Re-run all jobs", para, "the path that fails is not ruled out")

        # The runtime warning is one line; slice it rather than the whole step.
        warning = next(
            line for line in _evidence_run().splitlines() if "check-run unavailable" in line
        )
        self.assertIn("workflow_dispatch", warning)
        self.assertIn("Re-run all jobs", warning)

    def test_the_fork_recovery_says_the_dispatch_comes_back_red_once(self):
        """A maintainer who is not told this reads the red as the fix failing."""
        para = self._fork_paragraph(*self.FORK_RECOVERY[0][1:])
        self.assertIn("unarmed", para)
        self.assertIn("keel:evidence-waived", para, "no auditable way out is offered")

    def test_the_docs_state_the_fail_closed_rule(self):
        text = DOC.read_text(encoding="utf-8")
        bullet = next(
            (line for line in text.splitlines() if "unable to report" in line.lower()), ""
        )
        self.assertTrue(bullet, "the fail-closed rule is no longer documented")
        self.assertRegex(
            bullet,
            r"is not a pass|never a pass",
            "the fail-closed bullet no longer says that not reporting is not a pass",
        )


class TestTheGateRunsWhenAVerdictArrives(unittest.TestCase):
    """The trigger and the job gate — both were unreachable-by-test before.

    A published incomplete check is only honest if something ever completes it.
    Nothing did: the workflow listened for pushes only, and the job's own `if:`
    silently evaluated false on every other event. Neither had a test, which is
    how both shipped.
    """

    def _on(self) -> dict:
        workflow = _workflow()
        # PyYAML resolves the bare key `on:` to the boolean True.
        return workflow.get("on") or workflow.get(True)

    def test_the_workflow_listens_for_the_event_a_verdict_actually_is(self):
        """`keel post-comment` calls POST /issues/{n}/comments — an issue comment.

        Subscribing to `pull_request_review*` instead reads tidier and fires
        never: measured over twelve merged PRs, all verdict markers were issue
        comments and none were reviews.
        """
        self.assertIn(
            "issue_comment",
            self._on(),
            "no trigger fires when a verdict is posted, so the check stays incomplete",
        )
        types = (self._on()["issue_comment"] or {}).get("types", [])
        self.assertIn("created", types)

    def test_the_evidence_job_runs_on_that_event(self):
        """`github.event.inputs` is null off `workflow_dispatch`, and GitHub

        coerces both null and '' to 0 — so `github.event.inputs.pr != ''` is
        false everywhere else and skipped the job silently. The condition has to
        name the events.
        """
        condition = _workflow()["jobs"]["evidence"]["if"]
        self.assertIn("issue_comment", condition, "the gate cannot run on a verdict")
        self.assertIn("github.event.issue.pull_request", condition,
                      "the gate would run for comments on plain issues too")
        self.assertRegex(
            condition,
            r"github\.event_name == 'workflow_dispatch' && github\.event\.inputs\.pr",
            "the dispatch clause is not guarded by its event name, so it reads as false",
        )

    def test_the_comment_path_never_checks_out_contributor_code(self):
        """`issue_comment` runs from the default branch and holds `checks: write`.

        That is the pwn-request shape if the job checks out the pull request's
        head. It does not — `actions/checkout` here takes no `ref:`, so it gets
        the default branch — and the assessment job, which reads the diff, is
        excluded from the event entirely.
        """
        steps = _workflow()["jobs"]["evidence"]["steps"]
        checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
        self.assertIsNone(
            (checkout.get("with") or {}).get("ref"),
            "the evidence job checks out an explicit ref; on issue_comment that "
            "could be contributor-authored code running with checks: write",
        )

    def test_the_pr_number_resolves_on_a_comment_event(self):
        env = next(
            s for s in _workflow()["jobs"]["evidence"]["steps"]
            if s.get("name") == "Verify posted ship evidence"
        )["env"]
        self.assertIn("github.event.issue.number", env["PR"])

    def test_the_assessment_job_does_not_rerun_for_a_comment(self):
        """A comment does not change the diff the assessment reads."""
        self.assertIn("issue_comment", _workflow()["jobs"]["ship"]["if"])

    def test_overlapping_runs_cannot_overwrite_each_other(self):
        """Two runs on one head both GET-then-PATCH; the slower one would win.

        Rare on pushes, routine once a comment can trigger a run.
        """
        workflow = _workflow()
        self.assertIn("concurrency", workflow, "no concurrency group")
        self.assertIn("cancel-in-progress", workflow["concurrency"])

    def test_a_fork_is_detected_by_where_the_head_lives(self):
        """`head.repo.fork` means "the head repo is itself a fork of something".

        True for every same-repo PR in a downstream fork of this template, which
        would hand those repos the fork fallback by accident.
        """
        env = next(
            s for s in _workflow()["jobs"]["evidence"]["steps"]
            if s.get("name") == "Verify posted ship evidence"
        )["env"]
        self.assertIn("full_name != github.repository", env["IS_FORK"])
        self.assertNotRegex(env["IS_FORK"], r"head\.repo\.fork\s*}}")

    def test_every_violation_fails_the_job_not_only_exit_one(self):
        """`evidence-verify` can exit 3+; only 0 and 2 are non-violations."""
        run = _evidence_run()
        self.assertRegex(run, r'\[ "\$RC" -ne 0 \] && \[ "\$RC" -ne 2 \]')

    def test_the_lookup_cannot_be_neutered_into_a_blind_post(self):
        """`--jq 'empty'` would always miss, silently restoring the stacking."""
        body = re.search(
            r"publish_check\(\)\s*\{(.*?)\n\}", _evidence_run(), re.DOTALL
        ).group(1)
        self.assertIn(".check_runs[0].id // empty", body)


if __name__ == "__main__":  # pragma: no cover - manual entry point
    unittest.main()
