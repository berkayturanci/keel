"""Keel Canary & Automated Rollback Guard.

Monitors post-merge health signals (CI on main branch, health probes, test gates)
and automatically executes an atomic git revert rollback if regression is detected.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import load_config
from .swarm_runtime import SubprocessRunner, default_runner


@dataclass(frozen=True)
class CanaryResult:
    target: str
    passed: bool
    status: str
    health_output: str
    reverted: bool
    revert_commit: str | None = None
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "passed": self.passed,
            "status": self.status,
            "health_output": self.health_output,
            "reverted": self.reverted,
            "revert_commit": self.revert_commit,
            "details": self.details,
        }


@dataclass(frozen=True)
class RollbackResult:
    target_sha: str
    success: bool
    revert_sha: str | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_sha": self.target_sha,
            "success": self.success,
            "revert_sha": self.revert_sha,
            "error": self.error,
        }


def render_canary_result(result: CanaryResult) -> str:
    lines = [
        f"keel canary-guard — target: {result.target}",
        f"  status        : {result.status} {'✓' if result.passed else '❌'}",
        (
            f"  reverted      : yes ({result.revert_commit})"
            if result.reverted and result.revert_commit
            else f"  reverted      : {'yes' if result.reverted else 'no'}"
        ),
    ]
    if result.details:
        lines.append(f"  details       : {result.details}")
    if result.health_output:
        lines.append(f"  health output : {result.health_output.strip()}")
    return "\n".join(lines)


def render_rollback_result(result: RollbackResult) -> str:
    lines = [
        f"keel rollback — target: {result.target_sha}",
        f"  status        : {'success ✓' if result.success else 'failed ❌'}",
    ]
    if result.revert_sha:
        lines.append(f"  revert commit : {result.revert_sha}")
    if result.error:
        lines.append(f"  error         : {result.error}")
    return "\n".join(lines)


def execute_rollback(
    target_sha: str,
    root: str | Path = ".",
    base_branch: str = "main",
    runner: SubprocessRunner | None = None,
) -> RollbackResult:
    """Execute an atomic revert commit for target merge commit."""
    run = runner or default_runner
    root_path = Path(root).resolve()

    # Attempt git revert --no-edit -m 1 <sha> or git revert --no-edit <sha>
    cmd = ["git", "revert", "--no-edit", "-m", "1", target_sha]
    res = run(cmd, root_path)
    if not res.ok:
        # Fallback to single-parent revert
        cmd_fallback = ["git", "revert", "--no-edit", target_sha]
        res_fallback = run(cmd_fallback, root_path)
        if not res_fallback.ok:
            run(["git", "revert", "--abort"], root_path)
            return RollbackResult(
                target_sha=target_sha,
                success=False,
                revert_sha=None,
                error=res_fallback.output.strip() or res.output.strip(),
            )

    # Get newly created commit SHA
    rev_res = run(["git", "rev-parse", "HEAD"], root_path)
    revert_sha = rev_res.output.strip() if rev_res.ok else None
    return RollbackResult(
        target_sha=target_sha,
        success=True,
        revert_sha=revert_sha,
    )


def run_canary_guard(
    project_yaml: str,
    *,
    pr_number: int | None = None,
    commit_sha: str | None = None,
    root: str | Path = ".",
    duration_m: int = 1,
    health_cmd: str | None = None,
    auto_revert: bool = False,
    runner: SubprocessRunner | None = None,
) -> CanaryResult:
    """Run canary health checks and conditionally execute rollback."""
    run = runner or default_runner
    root_path = Path(root).resolve()
    target_desc = f"PR #{pr_number}" if pr_number else (commit_sha or "HEAD")

    # Load config to get default gates/knobs if needed
    cfg = load_config(project_yaml)
    cmd = health_cmd or getattr(cfg.knobs, "build_gate_cmd", None) or "make test"

    # Run health command
    health_res = run(["sh", "-c", cmd], root_path)
    if health_res.ok:
        return CanaryResult(
            target=target_desc,
            passed=True,
            status="healthy",
            health_output=health_res.output,
            reverted=False,
            details="Canary health verification passed",
        )

    # Health check failed
    revert_performed = False
    revert_sha: str | None = None
    details = f"Canary failed: health check returned exit code {health_res.code}"

    if auto_revert and commit_sha:
        rb_res = execute_rollback(commit_sha, root=root_path, runner=runner)
        if rb_res.success:
            revert_performed = True
            revert_sha = rb_res.revert_sha
            details += f"; automatically rolled back in {revert_sha}"
        else:
            details += f"; rollback attempt failed: {rb_res.error}"

    return CanaryResult(
        target=target_desc,
        passed=False,
        status="regression_detected",
        health_output=health_res.output,
        reverted=revert_performed,
        revert_commit=revert_sha,
        details=details,
    )
