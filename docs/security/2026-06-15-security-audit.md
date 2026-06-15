# Security Audit — 2026-06-15

Conducted by Claude (Opus 4.8, `claude-opus-4-8`) acting as the reviewing security
engineer.

## Scope

This audit reviewed keel at `6a18da8` (current `main`, `v1.3.0` release line),
focusing on what changed since the last clean round (2026-06-11, `8d954a4`) — 61
commits — plus a re-check of the standing trust boundaries:

- Source-level vulnerability classes: command injection, path traversal, unsafe
  deserialization, code execution, SSRF, and secret leakage.
- Data-flow from untrusted GitHub inputs (issue/PR titles, branch names, review
  comments) and from on-disk ledger/checkpoint records to dangerous sinks (shell,
  file writes, deserialization, terminal, HTML/DOM).
- The **new attack surface** since the last audit: the `keel-visual` companion
  (renders a run from its ledger/checkpoint to the terminal and to web HTML) and
  the rebuilt static `website/` (marketing + coverage + docs).
- GitHub Actions workflow injection surfaces, action pinning, and token permissions.
- Python dependency exposure (`pip-audit`), static analysis (`bandit`), and the
  authenticated GitHub repository security surface.

## Summary

No critical, high, or medium-severity application security issue was found. The
core remains deterministic, uses `yaml.safe_load` exclusively, keeps every
`git`/`gh` call on argv wrappers (no shell), restricts the lone `urlopen` to
`http(s)` against a hard-coded PyPI URL, and enforces path containment for
checkpoint/ledger/worktree paths.

The new `keel-visual` and `website/` surfaces were the focus of this round, since
both turn records into rendered output. Both are written defensively:
ledger/checkpoint-derived values are coerced to integers or normalised to a fixed
vocabulary before rendering, web JSON is embedded with `<`/`>`/`&` escaped to their
`\uXXXX` forms so no payload can break out of a `<script>`, and terminal output
strips non-printable characters from the only attacker-influenceable field
(directory names). No XSS, DOM-injection, or ANSI/control-character injection path
was found.

The two standing accepted risks are unchanged: command gates execute
project-configured shell commands by design (`SECURITY.md`), and the PyPI
version-check performs one fail-soft outbound request from `keel doctor`.

## Evidence

### Automated checks

| Check | Result |
|---|---|
| `pip-audit` over the project environment | No known vulnerabilities found |
| `bandit -r src scripts keel-visual/src` (16,292 LOC) | 0 high; 2 medium (both known/mitigated); rest low/false-positive |
| Bandit `B604` medium | `src/keel/runner.py:57` — command gates intentionally use `shell=True` (documented accepted risk) |
| Bandit `B310` medium | `src/keel/cli.py` `urlopen` — mitigated: scheme restricted to `http(s)` (#393) against a hard-coded PyPI URL; no caller override (no SSRF / `file://`) |
| Bandit `B105` low (×5) | False positives — string constants in the consent/runtime vocabulary (`"secrets"`, `"pass"`, `"False"`, `"True"`), not credentials |
| Dangerous-pattern grep (`yaml.load(`, `pickle`, `eval(`, `exec(`, `os.system`, `marshal`) over `src/`, `scripts/`, `keel-visual/` | No matches |
| `shell=True` usages | Exactly one, `src/keel/runner.py:57` (the documented command-gate boundary) |
| Committed-secret grep over `src/`, `keel-visual/`, `website/`, `scripts/`, `.github/` | No secrets — only identifier/vocabulary matches |
| GitHub code-scanning / Dependabot / secret-scanning open alerts | 0 / 0 / 0 |
| Workflow `uses:` pin check | All 12 distinct refs pinned to exact 40-character commit SHAs with trailing `# vX.Y.Z` |

### Repository and workflow controls

- `gh api repos/berkayturanci/keel --jq '.security_and_analysis'` reports
  `secret_scanning=enabled`, `secret_scanning_push_protection=enabled`, and
  `dependabot_security_updates=enabled`. Non-provider patterns and validity checks
  remain disabled (plan/settings-surface dependent; core protections active).
- `main` branch protection requires the Linux/macOS test matrix (py3.11–3.13 ×
  ubuntu/macos) and disables force pushes and branch deletions.
- No workflow uses `pull_request_target`. Attacker-influenced context values are
  passed through `env:` and referenced as quoted shell variables — e.g.
  `keel-ship.yml` puts `github.event.inputs.deferral` into `$DEFERRAL` and appends
  it to a bash array as `"$DEFERRAL"`, never re-interpolating `${{ }}` of untrusted
  strings into a `run:` block. PR numbers are integers.

### Trust boundaries reviewed

- **Subprocess/shell.** The only `shell=True` call is `run_command` in
  `src/keel/runner.py:57`; its `cmd` comes from operator-controlled `project.yaml`
  knobs and in-repo extension YAML, never from issue/PR content. All other
  subprocess calls (`run_argv`, `scripts/release_smoke.py`,
  `keel-visual` theater spawn) pass argv lists; the theater spawn is
  `["jury", "--pr", str(pr), "--theater"]` with an int-cast PR number.
- **GitHub transport.** `src/keel/git.py` and `src/keel/github.py` invoke
  `git`/`gh` through argv wrappers, so titles/bodies/refs cannot break out into
  command injection.
- **Deserialization.** Every YAML load uses `yaml.safe_load`
  (`config.py`, `extensions.py`, `install.py`). No `pickle`, `eval`, `exec`, or
  `marshal` anywhere in `src/`, `scripts/`, or `keel-visual/`.
- **SSRF / outbound network.** The sole `urlopen` (`cli.py`, PyPI version check in
  `keel doctor`) rejects any URL whose scheme is not `http(s)` and is only ever
  called with the hard-coded `https://pypi.org/pypi/keel-workflow/json`; it is
  fail-soft and skipped under `--offline`.
- **Path containment.** `checkpoint.py`, `ledger.py`, and the worktree validator in
  `cli.py` reject absolute paths (`is_absolute()`) and `..` escapes (`relative_to`)
  before any read/write.
- **keel-visual rendering (new).** `runstate.py` coerces `issue`/`pr` to `int` (or
  `None`, never free text), normalises the jury mode to a fixed vocabulary, and
  falls an unknown `command` back to `ship` — so every value reaching the web
  `innerHTML` chips and node labels is typed or enumerated, not attacker free text.
  `render.py._embed` additionally escapes `<`/`>`/`&` to `\uXXXX` so no JSON string
  can close the enclosing `<script>`; the page `<title>` is HTML-escaped. The
  terminal board (`dash.py._safe_label`) drops non-printable characters (ANSI
  `\x1b`, control, and `Cf` bidi/zero-width) from directory-derived project names.
- **website/ (new).** A static site. Its many `innerHTML` writes render only
  build-time constants (the backbone model, fixed demo arrays). The single runtime
  input, `location.hash`, is used solely as an object-key lookup
  (`views[hash]`) and to call `scrollIntoView` — it never reaches `innerHTML`. No
  reflected DOM-XSS path was found.

## Findings

No new findings at any severity. The 2026-06-11 round was already clean; the
intervening 61 commits (predominantly the `keel-visual` companion and the rebuilt
`website/`) introduce no new vulnerability.

## Accepted Risk

### Command gates execute configured shell commands

`src/keel/runner.py` uses `shell=True` for command gates. This is intentional:
projects configure build/lint/command-extension bodies as shell snippets, and the
risk is equivalent to running a repository's Makefile or CI commands. The boundary
is documented in `SECURITY.md`.

### PyPI version check makes one outbound request

`keel doctor` fetches the latest published version from a hard-coded PyPI URL over
`http(s)` only. It is fail-soft (degrades to `latest: unknown`) and is skipped with
`--offline`.

## No Issue Opened For

- Bandit `B604` (`runner.py`) — the documented command-gate boundary.
- Bandit `B310` (`cli.py` `urlopen`) — already mitigated by the `http(s)` scheme
  restriction (#393) against a hard-coded URL.
- Bandit `B105` low matches — false positives on vocabulary string constants.
- Secret-scanning non-provider patterns and validity checks remaining disabled —
  plan/settings-surface dependent; the core provider patterns + push protection are
  active.

## Recommended Next Steps

1. Consider enabling secret-scanning non-provider patterns and validity checks if
   the repository plan supports them.
2. Re-run this audit at the next minor release, or sooner if the command-gate,
   redaction, GitHub-transport, `urlopen`, or `keel-visual`/website rendering
   boundaries change.
