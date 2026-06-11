# Security Audit — 2026-06-11

Conducted by Claude (Fable 5, `claude-fable-5`) acting as the reviewing security engineer.

## Scope

This audit reviewed keel at `8d954a411bf965213a2c2b8225114278439b5a60`
(`v1.2.1` release line), with focus on:

- Source-level vulnerability classes: command injection, path traversal, unsafe
  deserialization, code execution, and secret leakage through capture artifacts.
- Data-flow from untrusted GitHub inputs (issue titles/bodies, PR titles, branch names,
  review comments) to dangerous sinks (shell, file writes, deserialization).
- Python package dependency exposure (`pip-audit`) and static analysis (`bandit`).
- GitHub Actions workflow injection surfaces, action pinning, and token permissions.
- GitHub repository security settings visible through the authenticated GitHub API.

## Summary

No critical, high, or medium-severity application security issue was found in keel's
source tree. The core remains deterministic, uses `yaml.safe_load` exclusively, keeps
all `git`/`gh` calls on argv wrappers (no shell), enforces path containment for
checkpoint/ledger/worktree paths, and redacts capture artifacts before durability.

Both follow-ups from the 2026-06-09 audit are confirmed resolved: GitHub secret scanning
and push protection are enabled, and required PR approvals are now enforced by branch
protection (an improvement over the previous audit, where review policy was
process-enforced only).

The single accepted risk remains unchanged and documented: command gates execute
project-configured shell commands by design (`SECURITY.md`).

## Evidence

### Automated checks

| Check | Result |
|---|---|
| `pip-audit` over the project environment | No known vulnerabilities found |
| `bandit -r src scripts -f txt` (11,261 LOC scanned) | 0 high, 1 medium, 7 low |
| Bandit medium finding | `B604` on `src/keel/runner.py:57` — command gates intentionally use `shell=True` (documented accepted risk) |
| Bandit low findings | Subprocess import/call reminders; no actionable issues |
| Dangerous-pattern grep (`yaml.load(`, `pickle`, `eval(`, `exec(`) over `src/` and `scripts/` | No matches |
| `shell=True` usages | Exactly one, `src/keel/runner.py:57` (the documented command-gate boundary) |
| GitHub code-scanning open alerts | 0 |
| GitHub Dependabot open alerts | 0 |
| GitHub secret-scanning open alerts | 0 |
| Workflow `uses:` pin check | All refs pinned to exact 40-character commit SHAs |
| Latest `main` runs (`72d9198`) | CI, CodeQL, OpenSSF Scorecard, and Pages all successful |

### Repository and workflow controls

- `gh api repos/berkayturanci/keel --jq '.security_and_analysis'` reports
  `secret_scanning.status=enabled`, `secret_scanning_push_protection.status=enabled`,
  and `dependabot_security_updates.status=enabled`. Non-provider patterns and validity
  checks remain disabled in the current repository settings surface.
- Branch protection for `main` requires the full Python version/OS status-check matrix
  (py3.11–3.13 × ubuntu/macos), requires pull request reviews, and disables force
  pushes and branch deletions.
- No workflow uses `pull_request_target`. Attacker-influenced context values
  (`github.base_ref`, PR numbers) are passed through `env:` and referenced as quoted
  shell variables in `run:` blocks — no direct `${{ }}` interpolation of untrusted
  strings into shell.

### Trust boundaries reviewed

- **Subprocess/shell.** The only `shell=True` call is `run_command` in
  `src/keel/runner.py:57`. Its `cmd` comes exclusively from operator-controlled
  `project.yaml` knobs (`build_gate_cmd`, `lint_cmd` via `src/keel/gates.py`) or
  in-repo extension YAML `run:` fields (`src/keel/extensions.py`) — never from
  issue/PR content or agent output.
- **GitHub transport.** `src/keel/git.py` and `src/keel/github.py` invoke `git`/`gh`
  through argv wrappers (`run_argv`), so issue/PR titles, bodies, and branch names
  passed as `--title`/`--body`/refs cannot break out into command injection.
- **Shell scripts.** `scripts/compound-learning.sh` validates `PR_NUMBER` against
  `^[1-9][0-9]*$` before any `gh` use; all attacker-influenced fields (PR title,
  branch names, comments, diff) flow through `jq --arg`/`--argjson` or into heredocs
  where they are string-expanded only, never re-evaluated.
- **Deserialization.** Every YAML load uses `yaml.safe_load`
  (`src/keel/config.py`, `src/keel/extensions.py`, `src/keel/install.py`). No
  `pickle`, `eval`, or `exec` anywhere in `src/` or `scripts/`.
- **Path containment.** `src/keel/checkpoint.py`, `src/keel/ledger.py`, and
  `_validated_worktree_path` in `src/keel/cli.py` resolve candidate paths and enforce
  containment under the project root (rejecting absolute paths and `..` escapes via
  `relative_to`/`parents` checks); worktree paths are additionally cross-checked
  against `git worktree list`.
- **Capture redaction.** `src/keel/redaction.py` covers private-key blocks,
  bearer/GitHub/LLM tokens, credential-bearing URLs, and credential assignment
  segments (including the split-segment handling from #294), with project-owned
  deny patterns compiled with error handling before durable capture artifacts are
  accepted. No exploitable bypass or plaintext-secret-logging path was found.

## Findings

No new findings at any severity. Both findings from the 2026-06-09 audit are verified
resolved in the live repository settings:

1. **Secret scanning and push protection** — now enabled (was Medium, #190).
2. **Consumer-name literals in neutrality guards** — replaced with generic sentinels
   (was Low, #191).

## Accepted Risk

### Command gates execute configured shell commands

`src/keel/runner.py` uses `shell=True` for command gates. This is intentional: projects
configure build, lint, and command extension bodies as shell snippets. The risk is
equivalent to running a repository's Makefile, package scripts, or CI commands. The
boundary is documented in `SECURITY.md`, and keel should continue treating untrusted
project configs as untrusted executable input.

## No Issue Opened For

- Bandit `B604` medium on `src/keel/runner.py` — the documented command-gate boundary.
- Bandit low subprocess import/call reminders where commands are passed as argv.
- Secret-scanning non-provider patterns and validity checks remaining disabled — these
  are GitHub plan/settings-surface dependent and the core protections (provider
  patterns + push protection) are active.
- The deliberate de-noising choices in `src/keel/redaction.py` (8-character floor and
  space exclusion on the unquoted-value arm) — redaction is defense-in-depth on capture
  artifacts, and no concrete bypass-to-leak path exists.

## Recommended Next Steps

1. Consider enabling secret-scanning non-provider patterns and validity checks if the
   repository plan supports them.
2. Re-run this audit at the next minor release, or sooner if the command-gate,
   redaction, or GitHub-transport boundaries change.
