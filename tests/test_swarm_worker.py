"""Unit tests for the live swarm worker's pure decisions (:mod:`keel.swarm_worker`, #1400).

Consent: an approved contract becomes a delegation that records who, which scopes, which
run and clusters, and when; a worker is handed exactly the parent's scopes, and nothing for
a cluster the delegation does not name. Seat: the cluster's resolved implementer seat is
planned as ``keel delegate run --role implement`` would plan it, and a seat keel cannot run
as a worker is refused with the reason. Writing: brief, commit and pull request text.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from keel import agents, consent, delegate, swarm_worker
from keel.swarm import IssueScope, SwarmCluster

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _contract(scopes=("filesystem", "git", "github"), *, operator="ops", mode="explicit"):
    return consent.build_consent_contract(
        command="swarm-run",
        side_effects=swarm_worker.WORKER_SIDE_EFFECTS,
        dry_run=False,
        approved_scopes=scopes,
        approval_source="flag",
        mode=mode,
        operator=operator,
        target="repo",
        now=NOW,
    )


def _assignment(provider, *, model=None, effort=None, kind="provider", source="team.x"):
    return {
        "implementer": {
            "provider": provider,
            "name": provider,
            "kind": kind,
            "model": model,
            "effort": effort,
            "source": source,
        }
    }


def _cluster(*issues, scope=("src/a.py",)):
    return SwarmCluster(cluster_id="c1", issues=issues, role="core", combined_scope=scope)


def _plan():
    plan, _ = swarm_worker.plan_implementer(
        _assignment("codex"), config=None, registry=None, prompt_path="/b.md", cwd="/wt"
    )
    assert plan is not None
    return plan


class TheOperatorsConsentIsDelegatedExplicitly(unittest.TestCase):
    def test_an_approved_contract_records_who_what_and_when(self):
        delegation, reason = swarm_worker.delegate_consent(
            _contract(), swarm_id="s1", cluster_ids=["c1", "c2"]
        )
        self.assertEqual(reason, "")
        self.assertEqual(
            delegation.to_dict(),
            {
                "swarm_id": "s1",
                "clusters": ["c1", "c2"],
                "scopes": ["filesystem", "git", "github"],
                "operator": "ops",
                "source": "flag",
                "mode": "explicit",
                "delegated_at": "2026-09-30T12:00:00Z",
                "consent_record": _contract()["consent_record"],
            },
        )

    def test_a_scope_the_operator_did_not_approve_refuses_the_delegation(self):
        delegation, reason = swarm_worker.delegate_consent(
            _contract(("filesystem", "git")), swarm_id="s1", cluster_ids=["c1"]
        )
        self.assertIsNone(delegation)
        self.assertIn("operator consent required", reason)
        self.assertIn("github", reason)

    def test_an_extra_approved_scope_is_not_handed_down(self):
        """The parent's scopes are the ones this command needs; `secrets` stays behind."""
        delegation, _ = swarm_worker.delegate_consent(
            _contract(("filesystem", "git", "github", "secrets")), swarm_id="s", cluster_ids=[]
        )
        self.assertEqual(delegation.scopes, ("filesystem", "git", "github"))

    def test_agent_mode_approves_nothing_a_worker_can_be_handed(self):
        delegation, reason = swarm_worker.delegate_consent(
            _contract((), mode="agent"), swarm_id="s1", cluster_ids=["c1"]
        )
        self.assertIsNone(delegation)
        self.assertIn("'agent-delegated'", reason)
        self.assertIn("--approve-scope filesystem,git,github --operator NAME", reason)

    def test_an_anonymous_consent_is_not_delegated(self):
        delegation, reason = swarm_worker.delegate_consent(
            _contract(operator=None), swarm_id="s1", cluster_ids=["c1"]
        )
        self.assertIsNone(delegation)
        self.assertIn("--operator NAME", reason)

    def test_for_run_names_the_run_and_keeps_the_scopes(self):
        delegation, _ = swarm_worker.delegate_consent(_contract(), swarm_id="", cluster_ids=())
        run = delegation.for_run("s9", iter(["c1"]))
        self.assertEqual(
            (run.swarm_id, run.clusters, run.scopes), ("s9", ("c1",), delegation.scopes)
        )


class AWorkerHoldsExactlyWhatItWasHanded(unittest.TestCase):
    def setUp(self):
        self.delegation, _ = swarm_worker.delegate_consent(
            _contract(), swarm_id="s1", cluster_ids=["c1"]
        )

    def test_a_delegated_cluster_gets_the_parents_scopes(self):
        self.assertEqual(
            swarm_worker.worker_scopes(self.delegation, "c1"), ("filesystem", "git", "github")
        )

    def test_a_cluster_outside_the_delegation_gets_nothing(self):
        self.assertEqual(swarm_worker.worker_scopes(self.delegation, "c2"), ())

    def test_may_answers_each_mutation_from_the_held_scopes(self):
        held = ("filesystem", "git")
        self.assertEqual(swarm_worker.may(held, "git_commit"), (True, ""))
        allowed, reason = swarm_worker.may(held, "pull_request")
        self.assertFalse(allowed)
        self.assertIn("pull_request needs the github consent scope", reason)
        self.assertIn("holds: filesystem, git", reason)
        self.assertIn("holds: none", swarm_worker.may((), "git_worktree")[1])

    def test_a_worker_short_of_any_scope_may_not_start(self):
        self.assertEqual(swarm_worker.consent_refusal(("filesystem", "git", "github")), "")
        self.assertIn(
            "pull_request needs the github", swarm_worker.consent_refusal(("filesystem", "git"))
        )
        self.assertIn("git_worktree needs", swarm_worker.consent_refusal(()))

    def test_the_required_scopes_are_the_three_a_worker_uses(self):
        self.assertEqual(swarm_worker.required_scopes(), ("filesystem", "git", "github"))

    def test_the_childrens_environment_carries_no_consent(self):
        env = {"PATH": "/bin", "KEEL_APPROVE_SCOPE": "github", "KEEL_OPERATOR": "o"}
        env["KEEL_CONSENT_MODE"] = "standing"
        self.assertEqual(swarm_worker.child_env(env), {"PATH": "/bin"})


#: The operator's environment as a live worker's parent sees it: consent, every forge
#: token, an inherited git config channel, and the model providers' own keys.
_OPERATOR_ENV = {
    "PATH": "/bin",
    "HOME": "/home/ops",
    "KEEL_APPROVE_SCOPE": "filesystem,git,github",
    "KEEL_OPERATOR": "ops",
    "KEEL_CONSENT_MODE": "standing",
    "GH_TOKEN": "gho_operator_gh_token",
    "GITHUB_TOKEN": "ghp_operator_github_token",
    "GH_ENTERPRISE_TOKEN": "ghe_operator_enterprise_token",
    "GITHUB_ENTERPRISE_TOKEN": "ghe_operator_github_enterprise_token",
    "GH_CONFIG_DIR": "/home/ops/.config/gh",
    "GIT_ASKPASS": "/Applications/Editor.app/askpass.sh",
    "GIT_CONFIG_PARAMETERS": "'protocol.https.allow'='always'",
    "GIT_CONFIG_COUNT": "9",
    "GIT_CONFIG_KEY_8": "credential.helper",
    "GIT_CONFIG_VALUE_8": "store",
    "ANTHROPIC_API_KEY": "sk-ant-provider",
    "OPENAI_API_KEY": "sk-openai-provider",
    "GEMINI_API_KEY": "gemini-provider",
    "CLAUDE_CODE_OAUTH_TOKEN": "claude-oauth-provider",
}

_OPERATOR_SECRETS = (
    "gho_operator_gh_token",
    "ghp_operator_github_token",
    "ghe_operator_enterprise_token",
    "ghe_operator_github_enterprise_token",
)


class TheImplementerCannotReachTheForge(unittest.TestCase):
    """#1400: the implementer seat runs without the operator's forge credentials, and
    with git and ``gh`` locked out of the remote; keel's own children keep them."""

    def _env(self):
        return swarm_worker.implementer_env(_OPERATOR_ENV, gh_config_dir="/state/c1.no-gh-login")

    def test_no_forge_token_reaches_the_implementer(self):
        env = self._env()
        for name in swarm_worker.FORGE_TOKEN_ENV_VARS:
            self.assertNotIn(name, env)
        for secret in _OPERATOR_SECRETS:
            self.assertNotIn(secret, env.values())
        self.assertEqual(
            set(swarm_worker.FORGE_TOKEN_ENV_VARS),
            {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"},
        )

    def test_nor_does_the_parents_consent(self):
        env = self._env()
        for name in swarm_worker.CONSENT_ENV_VARS:
            self.assertNotIn(name, env)

    def test_gh_runs_with_a_config_directory_holding_no_login(self):
        self.assertEqual(self._env()["GH_CONFIG_DIR"], "/state/c1.no-gh-login")

    def test_git_has_no_one_to_ask_for_a_password(self):
        env = self._env()
        self.assertEqual(env.get("GIT_TERMINAL_PROMPT"), "0")
        self.assertEqual(env.get("GIT_ASKPASS"), "")

    def test_git_runs_with_the_lockdown_config_and_only_that(self):
        env = self._env()
        self.assertNotIn("GIT_CONFIG_PARAMETERS", env)
        config = [
            (env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"])
            for i in range(int(env["GIT_CONFIG_COUNT"]))
        ]
        self.assertEqual(
            config,
            [
                ("credential.helper", ""),
                ("protocol.allow", "never"),
                ("protocol.http.allow", "never"),
                ("protocol.https.allow", "never"),
                ("protocol.ssh.allow", "never"),
                ("protocol.git.allow", "never"),
                ("protocol.file.allow", "user"),
            ],
        )
        # The parent's own entry past the new count is gone, not merely out of range.
        self.assertNotIn("GIT_CONFIG_KEY_8", env)
        self.assertNotIn("GIT_CONFIG_VALUE_8", env)

    def test_the_model_providers_keys_pass_through(self):
        env = self._env()
        for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
            self.assertEqual(env[name], _OPERATOR_ENV[name])
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "claude-oauth-provider")
        self.assertEqual((env["PATH"], env["HOME"]), ("/bin", "/home/ops"))

    def test_keels_own_git_and_gates_hold_no_forge_token_but_keep_the_operators_git(self):
        """keel's local git steps and the gates run under ``worker_env``: no consent and no
        forge token — only keel's push and pull request hold those — but the operator's own
        git setup, not the implementer's lockdown."""
        env = swarm_worker.worker_env(_OPERATOR_ENV)
        for name in swarm_worker.FORGE_TOKEN_ENV_VARS + swarm_worker.CONSENT_ENV_VARS:
            self.assertNotIn(name, env)
        self.assertEqual(env["GH_CONFIG_DIR"], "/home/ops/.config/gh")
        self.assertEqual(env["GIT_CONFIG_KEY_8"], "credential.helper")
        self.assertNotIn("GIT_TERMINAL_PROMPT", env)
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-ant-provider")
        # `child_env` itself only removes consent; the forge goes one level up.
        self.assertEqual(swarm_worker.child_env(_OPERATOR_ENV)["GH_TOKEN"], "gho_operator_gh_token")

    def test_the_brief_says_the_remote_is_out_of_reach(self):
        brief = swarm_worker.render_brief(
            _cluster(7), {}, swarm_id="s", branch="swarm/s/c", base_branch="main"
        )
        self.assertIn("You have no GitHub credentials", brief)
        self.assertIn("Do not change git's configuration or hooks", brief)


def _snapshot(config=("local\tfile:.git/config\tcore.bare=false",), hooks=None, **paths):
    where = {
        "git_dir": "/r/.git/worktrees/w",
        "common_dir": "/r/.git",
        "hooks_dir": "/r/.git/hooks",
    }
    return swarm_worker.GitSnapshot(**{**where, **paths}, config=tuple(config), hooks=hooks or {})


class KeelsOwnGitStepsDistrustTheRepository(unittest.TestCase):
    """#1400: the worktree shares the operator's repository, so keel's own git steps after
    the implementer run with no hooks, fsmonitor or signing program, and a change to the
    git setup while the implementer (or the gates) ran is named — without its values."""

    def test_keels_git_runs_no_hook_fsmonitor_or_signing_program(self):
        self.assertEqual(
            swarm_worker.keel_git(["commit", "--no-verify", "-m", "m"], hooks_dir="/empty"),
            [
                "git",
                "-c",
                "core.hooksPath=/empty",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--no-verify",
                "-m",
                "m",
            ],
        )

    def test_an_unchanged_setup_is_no_finding(self):
        self.assertEqual(swarm_worker.tamper_findings(_snapshot(), _snapshot()), ())

    def test_a_moved_git_directory_is_named(self):
        moved = _snapshot(git_dir="/elsewhere", common_dir="/e", hooks_dir="/e/h")
        self.assertEqual(
            swarm_worker.tamper_findings(_snapshot(), moved),
            (
                "the git directory moved",
                "the common git directory moved",
                "the hooks directory moved",
            ),
        )

    def test_a_config_change_names_scope_and_key_but_never_the_value(self):
        before = _snapshot()
        after = _snapshot(
            config=(
                "local\tfile:.git/config\tcore.bare=false",
                "local\tfile:.git/config\tremote.origin.pushurl=https://evil.invalid/x.git",
                "global\tfile:/h/.gitconfig\thttp.extraheader=AUTHORIZATION: secret-value",
            )
        )
        (finding,) = swarm_worker.tamper_findings(before, after)
        self.assertEqual(
            finding, "git config changed: global http.extraheader, local remote.origin.pushurl"
        )
        self.assertNotIn("evil", finding)
        self.assertNotIn("secret-value", finding)
        removed = swarm_worker.tamper_findings(after, before)
        self.assertEqual(removed, (finding,))

    def test_a_key_whose_subsection_is_a_url_is_named_without_it(self):
        after = _snapshot(
            config=(
                "local\tfile:.git/config\tcore.bare=false",
                "local\tfile:.git/config\turl.https://u:tok@evil.invalid/.pushinsteadof=x",
                "local\tfile:.git/config\thttp.https://h.invalid/.extraheader=x",
                "global\tfile:/h/.gitconfig\tcredential.https://h.invalid.helper=x",
                "local\tfile:.git/config\tincludeif.gitdir:/tok/.path=x",
                "local\tfile:.git/config\turl.bare=x",
            )
        )
        (finding,) = swarm_worker.tamper_findings(_snapshot(), after)
        self.assertEqual(
            finding,
            "git config changed: global credential.<credential>.helper, "
            "local http.<http>.extraheader, local includeif.<includeif>.path, "
            "local url.<url>.pushinsteadof, local url.bare",
        )
        self.assertNotIn("tok", finding)

    def test_a_reordered_config_is_a_change(self):
        lines = ("local\tfile:.git/config\ta.b=1", "local\tfile:.git/config\ta.b=2")
        self.assertEqual(
            swarm_worker.tamper_findings(
                _snapshot(config=lines), _snapshot(config=tuple(reversed(lines)))
            ),
            ("git config was reordered",),
        )

    def test_branch_config_git_writes_itself_is_not_compared(self):
        after = _snapshot(
            config=(
                "local\tfile:.git/config\tcore.bare=false",
                "local\tfile:.git/config\tbranch.swarm/s/c2.remote=origin",
            )
        )
        self.assertEqual(swarm_worker.tamper_findings(_snapshot(), after), ())

    def test_hooks_added_removed_and_changed_are_named(self):
        before = _snapshot(hooks={"pre-push": "a", "post-commit": "b"})
        after = _snapshot(hooks={"pre-push": "a2", "post-merge": "c"})
        self.assertEqual(
            swarm_worker.tamper_findings(before, after),
            ("hook post-commit removed", "hook post-merge added", "hook pre-push changed"),
        )

    def test_the_reason_says_what_changed_and_that_nothing_is_pushed(self):
        reason = swarm_worker.tamper_reason(("hook x added", "y"), during="the gates ran")
        self.assertIn("changed while the gates ran (hook x added; y)", reason)
        self.assertIn("pushes nothing", reason)

    def test_a_line_without_scope_or_origin_is_still_compared(self):
        self.assertEqual(
            swarm_worker.tamper_findings(_snapshot(config=("x",)), _snapshot(config=("y",))),
            ("git config changed: x, y",),
        )


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class GitItselfHonoursTheLockdown(unittest.TestCase):
    """The lockdown measured against a real git, not only as a dictionary: under the
    implementer's environment git calls no credential helper and opens no network
    transport, while a repository on disk still works. Offline — nothing is dialled."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        global_config = self.tmp / "empty-gitconfig"
        global_config.write_text("", encoding="utf-8")
        # Nothing of the machine's own git setup — or a hook's GIT_DIR — reaches the test.
        self.base = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.base.update(GIT_CONFIG_GLOBAL=str(global_config), GIT_CONFIG_NOSYSTEM="1")
        self.locked = swarm_worker.implementer_env(
            self.base, gh_config_dir=str(self.tmp / "no-gh-login")
        )
        self.remote = self.tmp / "remote.git"
        self.work = self.tmp / "work"
        self._git(["init", "-q", "--bare", str(self.remote)], self.tmp)
        self._git(["init", "-q", str(self.work)], self.tmp)
        ident = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        self._git([*ident, "commit", "-q", "--allow-empty", "-m", "x"], self.work)
        # The repository's own config asks for the very things the lockdown refuses: a
        # stored password, and HTTPS allowed outright.
        helper = "!f() { echo called >> helper-called; echo username=u; echo password=p; }; f"
        self._git(["config", "credential.helper", helper], self.work)
        self._git(["config", "protocol.https.allow", "always"], self.work)

    def _git(self, args, cwd, env=None, stdin=None):
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=self.base if env is None else env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if env is None:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def _fill(self, env):
        return self._git(
            ["credential", "fill"],
            self.work,
            env=env,
            stdin="protocol=https\nhost=forge.example.invalid\n\n",
        )

    def test_no_credential_helper_is_asked(self):
        marker = self.work / "helper-called"
        control = self._fill(self.base)
        self.assertIn("password=p", control.stdout)
        self.assertTrue(marker.exists(), "the fixture's helper must answer without the lockdown")
        marker.unlink()

        locked = self._fill(self.locked)
        self.assertNotEqual(locked.returncode, 0)
        self.assertNotIn("password=p", locked.stdout)
        self.assertFalse(marker.exists())

    def test_nor_one_scoped_to_the_forges_url(self):
        """The form ``gh auth setup-git`` writes: ``credential.<url>.helper``."""
        marker = self.work / "helper-called"
        helper = self._git(["config", "credential.helper"], self.work).stdout.strip()
        self._git(["config", "--unset", "credential.helper"], self.work)
        self._git(["config", "credential.https://forge.example.invalid.helper", helper], self.work)
        self.assertIn("password=p", self._fill(self.base).stdout)
        marker.unlink()

        locked = self._fill(self.locked)
        self.assertNotEqual(locked.returncode, 0)
        self.assertNotIn("password=p", locked.stdout)
        self.assertFalse(marker.exists())

    def test_no_network_transport_opens(self):
        for url in ("https://127.0.0.1:9/nope.git", "ssh://git@127.0.0.1:9/nope.git"):
            pushed = self._git(["push", url, "HEAD:refs/heads/x"], self.work, env=self.locked)
            self.assertNotEqual(pushed.returncode, 0)
            self.assertIn("not allowed", pushed.stderr)

    def test_a_repository_on_disk_still_works(self):
        pushed = self._git(
            ["push", str(self.remote), "HEAD:refs/heads/x"], self.work, env=self.locked
        )
        self.assertEqual(pushed.returncode, 0, pushed.stderr)


class TheResolvedSeatIsWhatIsPlanned(unittest.TestCase):
    def test_a_provider_seat_is_planned_as_delegate_run_plans_it(self):
        plan, reason = swarm_worker.plan_implementer(
            _assignment("codex", model="gpt-5", effort="high"),
            config=None,
            registry=None,
            prompt_path="/b.md",
            cwd="/wt",
            timeout=99,
        )
        self.assertEqual(reason, "")
        expected = delegate.plan_run(
            delegate.resolve_provider(None, None, "codex").provider,
            "implement",
            "/b.md",
            "/wt",
            99,
            "high",
            "gpt-5",
        )
        self.assertEqual(plan, expected)
        self.assertEqual((plan.provider, plan.role, plan.cwd), ("codex", "implement", "/wt"))

    def test_no_assignment_is_refused(self):
        plan, reason = swarm_worker.plan_implementer(
            None, config=None, registry=None, prompt_path="/b", cwd="/wt"
        )
        self.assertIsNone(plan)
        self.assertIn("no resolved assignment", reason)

    def test_a_host_subagent_seat_is_refused(self):
        plan, reason = swarm_worker.plan_implementer(
            _assignment("subagent:backend-developer", kind="subagent", source="team.implement"),
            config=None,
            registry=None,
            prompt_path="/b",
            cwd="/wt",
        )
        self.assertIsNone(plan)
        self.assertIn("host subagent", reason)
        self.assertIn("--delegate", reason)

    def test_an_unknown_provider_is_refused_with_the_delegate_error(self):
        plan, reason = swarm_worker.plan_implementer(
            _assignment("nope"), config=None, registry=None, prompt_path="/b", cwd="/wt"
        )
        self.assertIsNone(plan)
        self.assertIn("(unknown-provider)", reason)

    def test_a_transport_that_cannot_edit_a_worktree_is_refused(self):
        plan, reason = swarm_worker.plan_implementer(
            _assignment("ollama", model="qwen2.5"),
            config=None,
            registry=None,
            prompt_path="/b",
            cwd="/wt",
        )
        self.assertIsNone(plan)
        self.assertIn("'ollama' transport", reason)


class WhatAWorkerWrites(unittest.TestCase):
    scopes = {
        7: IssueScope(issue=7, title="Fix the thing", body="It is broken."),
        8: IssueScope(issue=8),
    }

    def test_the_brief_names_the_issues_the_scope_and_the_limits(self):
        brief = swarm_worker.render_brief(
            _cluster(7, 8, 9), self.scopes, swarm_id="s1", branch="swarm/s1/c1", base_branch="dev"
        )
        self.assertIn("cluster c1 of keel swarm s1", brief)
        self.assertIn("on branch swarm/s1/c1, cut from dev", brief)
        self.assertIn("within the cluster's scope: src/a.py.", brief)
        self.assertIn("Do not commit, push, open a pull request", brief)
        self.assertIn("## Issue #7: Fix the thing\n\nIt is broken.", brief)
        self.assertIn("## Issue #8: (title unavailable)", brief)
        self.assertIn("## Issue #9: (title unavailable)", brief)
        self.assertIn("could not be read", brief)

    def test_a_cluster_without_a_scope_is_briefed_as_anywhere(self):
        brief = swarm_worker.render_brief(
            _cluster(7, scope=()), {}, swarm_id="s", branch="b", base_branch="main"
        )
        self.assertIn("scope: *.", brief)

    def test_the_commit_names_the_issues_and_the_seat(self):
        message = swarm_worker.commit_message(_cluster(7, 8), swarm_id="s1", plan=_plan())
        self.assertTrue(message.startswith("feat(swarm): implement #7, #8 (s1/c1)\n"))
        self.assertIn("implementer seat codex through keel swarm-run --live", message)
        self.assertIn("Refs #7\nRefs #8", message)

    def test_a_one_issue_cluster_is_titled_after_its_issue(self):
        title = swarm_worker.pull_request_title(_cluster(7), self.scopes, swarm_id="s1")
        self.assertEqual(title, "Fix the thing (#7, swarm s1/c1)")

    def test_other_clusters_are_titled_after_the_cluster(self):
        for cluster in (_cluster(8), _cluster(7, 8)):
            with self.subTest(issues=cluster.issues):
                title = swarm_worker.pull_request_title(cluster, self.scopes, swarm_id="s1")
                self.assertTrue(title.startswith("swarm s1/c1: implement #"), title)

    def test_the_body_refs_never_closes_and_records_the_consent(self):
        delegation, _ = swarm_worker.delegate_consent(
            _contract(), swarm_id="s1", cluster_ids=["c1"]
        )
        body = swarm_worker.pull_request_body(
            _cluster(7),
            swarm_id="s1",
            branch="swarm/s1/c1",
            base_branch="main",
            commit="abc123",
            plan=_plan(),
            seat_source=None,
            delegation=delegation,
        )
        self.assertIn("Refs #7", body)
        self.assertNotIn("Closes", body)
        self.assertIn("`swarm/s1/c1`, cut from `main`, at `abc123`", body)
        self.assertIn("seat from `unknown`", body)
        self.assertIn("delegated by `ops` (flag, 2026-09-30T12:00:00Z)", body)
        self.assertIn("no review evidence yet", body)


class AWorkerLeavesBehindOnlyWhatIsWorthKeeping(unittest.TestCase):
    """#1278: what becomes of a live worker's worktree and branch when it ends."""

    def _decide(self, ok, created, ran):
        d = swarm_worker.worktree_disposal(ok=ok, worktree_created=created, implementer_ran=ran)
        return d.worktree, d.delete_branch

    def test_a_worker_that_created_nothing_touches_nothing(self):
        # What is at its path — a previous run's kept worktree — is not its own.
        self.assertEqual(self._decide(False, False, False), ("none", False))
        self.assertEqual(self._decide(True, False, False), ("none", False))

    def test_a_successful_worker_removes_its_worktree_and_keeps_its_branch(self):
        self.assertEqual(self._decide(True, True, True), ("remove", False))

    def test_a_worker_that_failed_after_its_seat_ran_keeps_both(self):
        self.assertEqual(self._decide(False, True, True), ("keep", False))

    def test_a_worker_that_failed_before_its_seat_ran_removes_both(self):
        self.assertEqual(self._decide(False, True, False), ("remove", True))


class LeftoversAreOnlyKeelsOwn(unittest.TestCase):
    """#1278: parsing git's listings, and deciding what `swarm-status --clean` may remove."""

    def test_the_worktree_list_is_parsed_with_its_prunable_verdict(self):
        porcelain = (
            "worktree /r\r\nHEAD abc\r\nbranch refs/heads/main\r\n\r\n"
            "worktree /r/.keel/worktrees/s/c\nHEAD abc\nbranch refs/heads/swarm/s/c\n"
            "prunable gitdir file points to non-existent location\n\n"
            "worktree /d\nHEAD abc\ndetached\nprunable\n\n"
            "junk that is no block\n\n"
        )
        entries = swarm_worker.parse_worktree_list(porcelain)
        self.assertEqual(
            [(e.path, e.branch, e.prunable) for e in entries],
            [
                ("/r", "refs/heads/main", False),
                ("/r/.keel/worktrees/s/c", "refs/heads/swarm/s/c", True),
                ("/d", None, True),
            ],
        )

    def test_only_the_exact_swarm_branch_shape_is_keels(self):
        self.assertEqual(swarm_worker.swarm_branch_ids("swarm/s1/c1"), ("s1", "c1"))
        self.assertEqual(swarm_worker.swarm_branch_ids("refs/heads/swarm/s1/c1"), ("s1", "c1"))
        for other in ("main", "swarm/s1", "swarm/s1/c1/x", "swarm//c1", "feature/swarm/s/c"):
            with self.subTest(branch=other):
                self.assertIsNone(swarm_worker.swarm_branch_ids(other))

    def _classify(self, runs, named=None):
        return {
            (x.kind, x.target): (x.action, x.reason)
            for x in swarm_worker.classify_leftovers(
                worktrees=[
                    ("done", "c1", "/w/done/c1", False),
                    ("done", "c9", "/w/done/c9", True),
                    ("live", "c1", "/w/live/c1", False),
                ],
                directories=[("done", "c2", "/w/done/c2"), ("gone", "", "/w/gone")],
                branches=[
                    "refs/heads/swarm/done/c1",
                    "swarm/done/c3",
                    "swarm/done/c4",
                    "swarm/live/c1",
                    "swarm/nostate/c1",
                    "feature/x",
                ],
                runs=runs,
                named=named,
            )
        }

    RUNS = {
        "done": swarm_worker.SwarmRunRecord(
            unfinished=False, pull_requests={"c3": 12}, pushed=frozenset({"c4"})
        ),
        "live": swarm_worker.SwarmRunRecord(unfinished=True),
    }

    def test_a_finished_runs_leftovers_are_removed_and_its_pushed_branches_kept(self):
        got = self._classify(self.RUNS)
        self.assertEqual(got[("worktree", "/w/done/c1")][0], "remove")
        self.assertEqual(got[("registration", "/w/done/c9")][0], "remove")
        self.assertEqual(got[("directory", "/w/done/c2")], ("remove", "not a registered worktree"))
        self.assertEqual(got[("directory", "/w/gone")], ("remove", "an empty run directory"))
        self.assertEqual(
            got[("branch", "swarm/done/c1")],
            ("remove", "nothing was pushed and no pull request was opened"),
        )
        self.assertEqual(got[("branch", "swarm/done/c3")], ("keep", "it heads pull request #12"))
        self.assertEqual(got[("branch", "swarm/done/c4")], ("keep", "it was pushed"))
        self.assertEqual(got[("branch", "swarm/nostate/c1")][0], "keep")
        self.assertIn("no run state", got[("branch", "swarm/nostate/c1")][1])
        self.assertNotIn(("branch", "feature/x"), got)

    def test_an_unfinished_run_is_kept_unless_the_operator_names_it(self):
        got = self._classify(self.RUNS)
        for key in (("worktree", "/w/live/c1"), ("branch", "swarm/live/c1")):
            with self.subTest(key=key):
                self.assertEqual(got[key][0], "keep")
                self.assertIn("--swarm-id live", got[key][1])
        named = self._classify(self.RUNS, named="live")
        self.assertEqual(named[("worktree", "/w/live/c1")][0], "remove")
        self.assertEqual(named[("branch", "swarm/live/c1")][0], "remove")
        # An unfinished run's directory is kept the same way.
        runs = {"done": swarm_worker.SwarmRunRecord(unfinished=True)}
        self.assertEqual(self._classify(runs)[("directory", "/w/done/c2")][0], "keep")

    def test_the_listing_is_ordered_and_serialisable(self):
        found = swarm_worker.classify_leftovers(
            worktrees=[("s", "c", "/w/s/c", False)],
            directories=[],
            branches=["swarm/s/c", "swarm/a/c"],
            runs={},
        )
        self.assertEqual(
            [(x.swarm_id, x.kind) for x in found],
            [("a", "branch"), ("s", "worktree"), ("s", "branch")],
        )
        self.assertEqual(
            found[1].to_dict(),
            {
                "kind": "worktree",
                "swarm_id": "s",
                "cluster_id": "c",
                "target": "/w/s/c",
                "action": "remove",
                "reason": "no running swarm run owns it",
            },
        )


def _report(*entries, schema="keel.run-gates.v1"):
    return json.dumps({"schema_version": schema, "gate_outcomes": list(entries)}, indent=2)


class TheAttributionLabelsAreKeels(unittest.TestCase):
    """#1420: a cluster pull request carries the labels `keel attribution` prints for the
    seat that ran — read off the seat's attribution record, never composed."""

    def test_the_agent_and_model_labels_of_the_seats_attribution(self):
        plan = delegate.RunPlan(
            provider="codex", vendor="codex", role="implement", transport="cli",
            prompt_path="/b", cwd="/w", attribution=agents.attribution("codex", "gpt-5.5"),
        )  # fmt: skip
        expected = agents.attribution("codex", "gpt-5.5")
        self.assertEqual(
            swarm_worker.attribution_labels(plan),
            (expected["agent_label"], expected["model_label"]),
        )

    def test_a_seat_with_no_model_carries_the_agent_label_alone(self):
        self.assertEqual(swarm_worker.attribution_labels(_plan()), ("agent:codex",))

    def test_an_attribution_with_no_label_gives_none(self):
        plan = delegate.RunPlan(
            provider="x", vendor="x", role="implement", transport="cli",
            prompt_path="/b", cwd="/w", attribution={"system": "x", "agent_label": " "},
        )  # fmt: skip
        self.assertEqual(swarm_worker.attribution_labels(plan), ())

    def test_label_names_are_read_off_gh_label_list(self):
        listing = json.dumps([{"name": "agent:codex"}, {"name": 3}, "x", {"other": 1}])
        self.assertEqual(swarm_worker.label_names(listing), ("agent:codex",))
        self.assertEqual(swarm_worker.label_names("not json"), ())
        self.assertEqual(swarm_worker.label_names('{"name": "a"}'), ())

    def test_the_warning_names_the_labels_and_the_hold(self):
        warning = swarm_worker.labels_warning("PR 1", ("agent:codex",), "HTTP 403")
        self.assertIn("(agent:codex) were not applied to PR 1 (HTTP 403)", warning)
        self.assertIn("hold it on attribution-label", warning)
        self.assertIn("`keel attribution`", warning)
        self.assertIn("(agent:<vendor>) were not", swarm_worker.labels_warning("p", (), "w"))


class TheGateReportIsReadWhole(unittest.TestCase):
    """#1420: the gates-pass a worker records is the gates its run reported, gate by gate —
    an unreadable report is no report, never a partial one."""

    def test_each_outcome_is_restored_with_what_the_record_judges(self):
        build = {
            "gate": "build", "ok": False, "skipped": False, "timed_out": True,
            "not_run": False, "on_fail": "block", "unconfigured": False, "error": "boom",
            "findings": [
                {"severity": "major", "message": "red", "source": "build"},
                {"severity": "nit", "message": "n"},
            ],
        }  # fmt: skip
        jury = {"gate": "jury", "ok": True, "not_run": True, "skipped": True}
        (b, j) = swarm_worker.parse_gate_report(_report(build, jury))
        self.assertEqual(
            (b.gate, b.ok, b.timed_out, b.error, b.on_fail), ("build", False, True, "boom", "block")
        )
        self.assertEqual(
            [(f.severity, f.message, f.source) for f in b.findings],
            [("major", "red", "build"), ("nit", "n", "build")],
        )
        # A missing severity is the strict one, as `record_gates_passed` reads it.
        self.assertEqual(
            (j.not_run, j.skipped, j.on_fail, j.error, j.findings), (True, True, "block", None, ())
        )

    def test_the_report_is_found_after_what_the_runner_folded_in(self):
        notice = '  ! extension not loaded: x\n{\nnot json\n{\n  "other": 1\n}\n'
        report = _report({"gate": "build", "ok": True})
        (outcome,) = swarm_worker.parse_gate_report(notice + report + "\ntrailing\n")
        self.assertEqual((outcome.gate, outcome.ok), ("build", True))

    def test_anything_unreadable_is_no_report(self):
        for output in (
            "BLOCKED - build",
            "",
            _report({"gate": "build"}, schema="keel.other.v1"),
            json.dumps({"schema_version": "keel.run-gates.v1", "gate_outcomes": {}}, indent=2),
            _report({"ok": True}),
            _report("build"),
            _report({"gate": "build", "findings": [{"severity": "fatal", "message": "m"}]}),
            _report({"gate": "build", "findings": ["red"]}),
        ):
            with self.subTest(output=output):
                self.assertIsNone(swarm_worker.parse_gate_report(output))

    def test_a_red_run_is_summarised_as_run_gates_prints_it(self):
        from keel.findings import Finding
        from keel.gates import GateOutcome

        summary = swarm_worker.gate_report_summary(
            (
                GateOutcome("build", False, (Finding("major", "tests failed", "build"),)),
                GateOutcome("lint", False, timed_out=True),
                GateOutcome("jury", True, not_run=True),
                GateOutcome("docs", True, skipped=True),
                GateOutcome("guard", True),
            )
        )
        self.assertEqual(
            summary.splitlines(),
            [
                "     FAIL  build",
                "  TIMEOUT  lint",
                "  NOT-RUN  jury",
                "  SKIPPED  docs",
                "       ok  guard",
                "    [major] build: tests failed",
            ],
        )


class TheGatesRecordIsAShipRunThatNeverMerged(unittest.TestCase):
    """#1420: the record a worker appends is a real `ship_run` that `keel merge`'s
    gates-pass lookup matches, and that no reader takes for a completed ship."""

    def _record(self, *outcomes, changed=("src/a.py",)):
        from keel.gates import GateOutcome

        plan = delegate.RunPlan(
            provider="codex", vendor="codex", role="implement", transport="cli",
            prompt_path="/b", cwd="/w", attribution=agents.attribution("codex", "gpt-5"),
        )  # fmt: skip
        return swarm_worker.gates_record(
            _cluster(41, 42),
            swarm_id="s",
            plan=plan,
            branch="swarm/s/c1",
            base_branch="main",
            head="abc123",
            pull_request=7,
            outcomes=outcomes or (GateOutcome("build", True),),
            changed_files=None if changed is None else list(changed),
        )

    def test_its_gates_pass_for_the_pull_request_and_the_pushed_head(self):
        from keel import closeorder, ledger, status

        record = self._record()
        self.assertEqual(ledger.gates_pass_for_head([record], 7, "abc123"), (True, record))
        self.assertEqual(ledger.gates_pass_for_head([record], 7, "other"), (False, None))
        self.assertEqual((record["record_type"], record["command"]), ("ship_run", "swarm-run"))
        self.assertEqual(record["run_id"], swarm_worker.provenance_run_id("s", "c1"))
        self.assertEqual((record["issue"], record["pull_request"]), ({"number": 41}, {"number": 7}))
        self.assertEqual(
            record["git"], {"base_branch": "main", "branch": "swarm/s/c1", "head_sha": "abc123"}
        )
        self.assertEqual(record["changes"]["files"], ["src/a.py"])
        self.assertEqual(record["actors"]["implementer"], "codex:gpt-5")
        self.assertEqual(record["verdict"]["blocked"], False)
        # Never reached capture, and never assessed a merge.
        self.assertEqual((record["capture"]["not_run"], record["capture"]["marker"]), (True, None))
        self.assertEqual(
            record["assessment"]["merge"],
            {"action": "defer", "reason": swarm_worker.WORKER_MERGE_REASON},
        )
        self.assertEqual(
            [record["assessment"][k] for k in ("tier", "reviewers", "window_open", "ci_ok")],
            [None] * 4,
        )
        # So no reader counts it as a merge or a ship...
        self.assertFalse(ledger._is_merged_ship_run(record))
        self.assertFalse(closeorder.record_attests_merge(record))
        self.assertEqual(ledger.capture_health_summary([record])["record_count"], 0)
        self.assertEqual(status._item_state(record), "deferred")
        # ...and its consent is the run's delegation, not a status of its own.
        self.assertIsNone(record["run_context"]["consent"]["status"])
        self.assertEqual(ledger.parse_records(ledger.encode_record(record)), [record])

    def test_its_implementer_agrees_with_the_labels_the_worker_applies(self):
        from keel import evidence

        record = self._record()
        labels = list(swarm_worker.attribution_labels(
            delegate.RunPlan(
                provider="codex", vendor="codex", role="implement", transport="cli",
                prompt_path="/b", cwd="/w", attribution=agents.attribution("codex", "gpt-5"),
            )
        ))  # fmt: skip
        vendor = evidence.ledger_implementer_vendor(record)
        self.assertTrue(evidence.attribution_check(labels, implementer_vendor=vendor)["ok"])
        vocabulary = evidence.attribution_vocabulary_check(
            labels, implementer=evidence.ledger_implementer(record)
        )
        self.assertEqual((vocabulary["ok"], vocabulary["checked"]), (True, True))

    def test_a_blocking_gate_the_worker_did_not_run_is_not_recorded_as_a_pass(self):
        from keel import ledger
        from keel.gates import GateOutcome

        record = self._record(
            GateOutcome("build", True), GateOutcome("jury", True, not_run=True, on_fail="block")
        )
        self.assertEqual([g["not_run"] for g in record["gates"]], [False, True])
        self.assertEqual(ledger.gates_pass_for_head([record], 7, "abc123"), (False, None))
        self.assertEqual(swarm_worker.gates_not_a_pass(record), ("jury",))
        # A soft one is not a reason to hold it.
        soft = self._record(
            GateOutcome("build", True), GateOutcome("docs", True, not_run=True, on_fail="warn")
        )
        self.assertEqual(swarm_worker.gates_not_a_pass(soft), ())

    def test_a_failed_errored_or_empty_run_names_what_held_it(self):
        from keel.gates import GateOutcome

        record = self._record(
            GateOutcome("build", True, error="crashed"),
            GateOutcome("lint", False, on_fail="warn"),
            GateOutcome("docs", True, skipped=True),
        )
        self.assertEqual(swarm_worker.gates_not_a_pass(record), ("build", "lint"))
        empty = {**record, "gates": []}
        self.assertEqual(swarm_worker.gates_not_a_pass(empty), ("(no gate)",))
        self.assertEqual(swarm_worker.gates_not_a_pass({**record, "gates": None}), ("(no gate)",))

    def test_an_unreadable_diff_is_recorded_as_unreadable(self):
        record = self._record(changed=None)
        self.assertEqual(
            (record["changes"]["files"], record["changes"]["unreadable"]), (None, True)
        )

    def test_the_warnings_name_the_hold_and_the_way_out(self):
        not_a_pass = swarm_worker.gates_not_a_pass_warning("PR 7", ("jury", "lint"))
        self.assertIn("(jury, lint did not pass or did not run in the worker)", not_a_pass)
        missing = swarm_worker.gates_record_warning("PR 7", "disk full")
        self.assertIn("gates-pass for PR 7 is not in the run ledger (disk full)", missing)
        for warning in (not_a_pass, missing):
            self.assertIn("hold it on no gates-pass", warning)
            self.assertIn("--capture-status not-run", warning)


if __name__ == "__main__":
    unittest.main()
