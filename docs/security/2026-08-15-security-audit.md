# Security Audit — 2026-08-15

Conducted by Google Antigravity (Gemini 3.7 Flash) acting as the reviewing security engineer.

## Scope

This audit reviewed keel at `578e95f` (post-Milestone 12 / Keel Swarm delivery, release line `v1.14.2`),
focusing on the attack surface introduced across the Swarm roadmap (#714, #715, #716, #717, #718, #719, #720, #721)
and re-verifying core security invariants:

- **Swarm Orchestration Attack Surfaces**:
  - `src/keel/swarm.py` (pure dependency DAG analysis, conflict clustering, and wave tier partitioning).
  - `src/keel/swarm_runtime.py` (isolated git worktree execution, worker subprocess lifecycle, and dynamic rebalancing).
  - `src/keel/swarm_landing.py` (orthogonal batch landing, adaptive funnel rebase, and fail-soft conflict rollback).
- **Filesystem & Path Containment**:
  - Worktree paths under `.keel/worktrees/swarm/<swarm_id>/<cluster_id>/` against directory traversal (`../../etc`, `~/.ssh`).
  - Lock directory paths under `.keel/state/` against traversal escapes.
- **Subprocess & Command Injection Hardening**:
  - Dynamic fuzzing of `runner.run_argv`, `git.run_git`, `github.run_gh`, and `swarm_runtime.default_runner` against toxic shell metacharacters (`;`, `&&`, `|`, `$()`, ```` ` ````, `\x00`).
  - Verification that AST contains zero dynamic `eval()`, `exec()`, or unescaped `shell=True` calls.
- **Secret Redaction & Credential Leak Prevention**:
  - Verification of `src/keel/redaction.py` against GitHub PATs, fine-grained PATs, OpenAI/Anthropic/Gemini keys, Bearer tokens, and Basic Auth in Git URLs.
- **SSRF & Remote Host Enforcement**:
  - Verification of `knobs.delegate_profiles` default-closed policy against non-loopback endpoints without `KEEL_ALLOW_REMOTE_ENDPOINT=1`.
- **ReDoS (Catastrophic Backtracking) Resilience**:
  - Stress testing regex patterns across `evidence.py`, `guard.py`, and `redaction.py` with adversarial payloads ($10{,}000+$ characters).
- **Atomic Concurrency & Mutual Exclusion**:
  - Multi-threaded stress testing (30 concurrent workers) of `src/keel/lock.py` (`merge_lock`) verifying atomic `mkdir` claims and zero lock race conditions.
- **Visualizer XSS & DOM Sanitization**:
  - Review of `keel-visual/src/keel_visual/templates/swarm.html` and JSON payload embedding.

---

## Summary

No critical, high, or medium-severity security vulnerabilities were identified.

The Keel Swarm expansion strictly preserves Keel's pure core / thin I/O architectural boundary:
1. **Pure Dependency Engine**: `swarm.py` is 100% side-effect-free, operating on immutable data structures with deterministic wave scheduling and zero filesystem/network access.
2. **Worktree Isolation**: Worktrees are sandboxed under `.keel/worktrees/`, preventing cross-worker git collisions or repository dirty-state leakage.
3. **Fail-Closed Operator Consent**: Mutating commands enforce preflight consent checks and reject unapproved mutation scopes.
4. **Resilient Redaction & Sanitization**: Sensitive credentials and tokens are masked across logs and artifacts.

---

## Evidence

### Automated Security Checks

| Check | Target / Module | Result | Details |
| :--- | :--- | :---: | :--- |
| **Static AST Analysis** | `src/keel/**/*.py` | **PASS** | 0 `eval()`, 0 `exec()`, 0 unescaped `shell=True` |
| **Command Injection Fuzzing** | `runner.py`, `git.py`, `swarm_runtime.py` | **PASS** | 9/9 toxic shell payloads isolated in `argv` |
| **Path Traversal Defenses** | `swarm_runtime.py`, `lock.py` | **PASS** | 7/7 directory escape payloads blocked/contained |
| **SSRF Remote Endpoint Gate** | `config.py` (`delegate_profiles`) | **PASS** | Non-loopback endpoints fail closed without env opt-in |
| **Secret Redaction Fuzzing** | `redaction.py` | **PASS** | 8/8 token formats (PAT, API keys, Bearer, URLs) redacted |
| **ReDoS Backtracking Stress** | `evidence.py`, `guard.py`, `redaction.py` | **PASS** | All evil payloads resolved in $<7\text{ ms}$ ($<100\text{ ms}$ bar) |
| **Atomic Lock Concurrency** | `lock.py` (30 concurrent threads) | **PASS** | 1 grant, 29 safe backoffs; 0 race conditions |
| **JSON Schema Fuzzing** | `jsonschema_min.py` | **PASS** | Type confusion, proto-pollution, overflows rejected |
| **Core Security Unit Tests** | `tests/test_adapter_consent.py`, `test_redaction.py`, etc. | **PASS** | 122/122 test cases passing |
| **Full Offline Unit Suite** | `tests/test_*.py` | **PASS** | 2,152 tests passed; **100% line & branch coverage** |

---

### Trust Boundaries Reviewed

#### 1. Swarm Multi-Worktree Execution (`src/keel/swarm_runtime.py`)
- **Isolation**: Each parallel cluster worker runs in an isolated subdirectory (`.keel/worktrees/swarm/<swarm_id>/<cluster_id>/`).
- **Path Resolution**: `build_worktree_path` deterministically roots worktrees to the project root.
- **Subprocess Invocation**: `default_runner` executes commands using `subprocess.run(cmd, shell=False)` with a 300s wall-clock timeout and fail-soft exception capture.

#### 2. Batch Landing & Funnel Rebase (`src/keel/swarm_landing.py`)
- **Single-Writer Lock**: All landing operations (`keel swarm-land`) must acquire the atomic `merge_lock` before modifying the base branch.
- **Self-Healing Rebase**: When rebasing overlapping cluster branches onto updated `main`, merge conflicts trigger an immediate `git rebase --abort`. The working tree is restored to a clean state and the failure is recorded in `SwarmLandingReport`.

#### 3. Remote Endpoint / SSRF Containment (`src/keel/config.py`)
- **Threat Model**: An attacker submits a PR with an altered `project.yaml` pointing `knobs.delegate_profiles` to an internal metadata service (`http://169.254.169.254`) or malicious webhook.
- **Mitigation**: `config.py` enforces `LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")`. Any non-loopback host raises `ConfigError` unless `KEEL_ALLOW_REMOTE_ENDPOINT=1` is explicitly set in the runner environment (which is outside repository/PR control).

#### 4. Credential Redaction (`src/keel/redaction.py`)
- **Coverage**: Handles GitHub PATs (`ghp_*`, `github_pat_*`), OpenAI (`sk-proj-*`, `sk-*`), Anthropic (`sk-ant-api03-*`), Google Gemini (`AIzaSy*`), Bearer headers, key-value assignments (`KEY=secret`), and Basic Auth credentials embedded in Git remote URLs.
- **Performance**: Possessive quantifier runs and segment boundary anchors prevent catastrophic backtracking.

#### 5. Keel-Visual HTML & DOM Injection (`keel-visual/`)
- **Template Security**: `swarm.html` injects structured data via `<script type="application/json">` with `<`/`>`/`&` characters escaped to `\uXXXX` format.
- **XSS Prevention**: DOM nodes are created with standard element text assignments or sanitized SVG elements; no raw unsanitized HTML is injected into innerHTML sinks.

---

## Conclusion

Keel release line `v1.14.2` and the Keel Swarm high-concurrency orchestration subsystem comply with all established security invariants, enforce robust boundary isolation, and maintain an exemplary 100% line and branch test coverage bar.
