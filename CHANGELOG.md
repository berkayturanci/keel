# Changelog

All notable changes to keel are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); keel adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.3.0] — 2026-06-14

### Added
- **Adapter-compliance audit gaps closed.** A sweep of deterministic self-checks
  and merge-gate hardening: `keel doctor` (environment + drift self-check, #339);
  `keel review` (deterministic evidence-bundle orchestrator, #340);
  `keel scope-verify` (declared files vs actual PR diff, #343);
  `keel verify-branch` (branch-off-base + worktree isolation, #353);
  verdict provenance + optional cross-vendor distinctness (#344);
  capture-verify hardening — derived merged set, reviewer cross-check, required
  artifact (#356); attribution labels verified against the ledger implementer
  (#357); `keel guard` — deterministic blocker ruleset, hotfix needs
  host-authoritative justification (#358); `keel consent-verify` (#360);
  `keel close-reconcile` — flag closed/status-done without a merge decision
  (#361); `keel dryrun-verify` — post-hoc dry-run integrity check (#362).
- **`keel.flows` — canonical command-flow registry.** The ordered phases of all
  16 keel commands, in core (like the ship `BACKBONE`), so consumers can render
  or reason about any command's structure without re-deriving it (#369).

### Changed
- **`keel merge` is gated on a current-head gates-pass and a covering
  checkpoint.** The s10 merge now requires a recorded gates-pass for the exact
  head SHA (#342) and a current checkpoint at s10, plus status orphan detection
  (#359).

### Companion
- **`keel-visual`** (new, optional, separately installable) — an animated 2D/3D
  run visualizer that *renders* a keel run from its ledger/checkpoint (it never
  drives one). Terminal `play` (flow + wave ribbon, `--loop`, live `--follow`),
  parallel `dash` board, and web `render` (2D flow + 3D ribbon). Renders any of
  the 16 command flows via `keel.flows`. Lives under `keel-visual/`; depends on
  `keel-workflow >= 1.3.0`.

## [1.2.3] — 2026-06-13

### Fixed
- **Evidence gate now arms from workflow ship assessments.** Trusted `keel ship`
  assessment comments, including repository-owned `github-actions[bot]` comments, now
  arm the evidence gate as ship provenance without satisfying any closure, review, or jury
  evidence item. This prevents agent-created PRs from silently passing with
  `enforced=false` when review or closure evidence is missing. (#327)

## [1.2.2] — 2026-06-11

### Fixed
- **Capture redaction handles comma/semicolon-joined credential assignments.**
  The credential-assignment redactor now stops before sibling assignments joined by
  commas or semicolons, so audit counts and retained non-secret fields stay accurate
  without over-redacting a whole compact object or statement. (#291)
- **Scaffolded YAML values are rendered as safe scalars.** `keel init` / `keel setup`
  now quote generated `project.yaml` scalar values through the YAML serializer, preventing
  newline/key-shaped setup input from injecting sibling config keys. (#292)

### Security
- **Publish workflow no longer resolves runtime dependencies unhashed in the privileged
  release job.** PyYAML is now included in the hash-locked release tooling file, and SBOM
  generation installs the just-built wheel with `--no-deps`, closing the remaining
  unhashed dependency resolution path in `publish.yml`. (#293)

## [1.2.1] — 2026-06-11

### Added
- **`keel merge` — core-owned, fail-closed merge execution.** The sanctioned s10 merge
  path: acquires the merge resource claim, re-checks the merge window inside the claim,
  reads the live PR check rollup with failure-before-pending precedence, runs
  `evidence-verify` against the current PR artifacts, and only then performs the merge.
  `--hotfix` is the audited window bypass and still requires explicit consent scopes.
  Companion commands: `keel claim` / `keel release` (single-host `mkdir` resource claims)
  and `keel worktree-remove` (validates nesting + registration before removal). Raw
  adapter `gh pr merge` calls are now a spec violation for ship-style flows. (#265, #269)
- **`keel post-comment` — deterministic issue/PR artifact comments.** Validates the
  rendered body contains the marker required by `--artifact`, rejects literal `@/tmp/...`
  placeholder bodies before any public write, resolves the GitHub transport in core, and
  edits the latest same-marker/same-run-id comment instead of duplicating. Raw
  `gh issue comment` / `gh pr comment` calls are now a spec violation for ship evidence
  artifacts. (#263, #275)
- **`keel step-verify` and `keel runcontrols` — the shipped enforcement modules are now
  wired into the CLI.** `step-verify` consumes a persisted step handoff plus the evidence
  report and fail-closed checks each backbone transition; `runcontrols` appends/evaluates
  run events with hard halts on budget, step-cap, and oscillation violations, and run-control
  summaries are stamped into `ship_run` ledger records via `keel ship --run-events-file`.
  Risk/trust escalation evaluation is wired into the same fail-closed path. (#267, #271)

### Changed
- **Evidence gate now arms from ship provenance by default.** The gate previously required
  an agent-applied opt-in label — forgetting it silently disarmed the only required check.
  The arming signal is now deterministic ship provenance, and the explicit disarm path is
  the operator-applied `keel:evidence-waived` label; CLI output reports the gate reason and
  waiver state. (#266, #270)
- **Empty ship run context is now an evidence finding.** A closure comment whose Run
  context block is fully degraded (all fields unknown/default) is flagged instead of
  passing silently, so adapters that skip the `--host-agent`/`--transport` ledger flags
  degrade loudly. (#264, #276)
- **BREAKING:** configured state file paths are now constrained to the project root.
  `policy_pack.reports.run_ledger` and `policy_pack.reports.checkpoint` must be relative
  paths that resolve inside the project root; absolute paths and `..` escapes are rejected
  before keel reads or writes ledger/checkpoint state. Ledger, checkpoint, status, resume,
  capture verification/reconcile, ship, and plan now report a friendly exit 1 instead of a
  raw traceback when these paths are invalid. (#251, #259)
- **BREAKING:** evidence markers now require trusted GitHub provenance. Closure, review, and
  jury evidence must come from `OWNER`, `MEMBER`, or `COLLABORATOR` actors; explicit
  untrusted `author_association` values are rejected even for bot-authored comments, and
  enforced evidence rejects fixture payloads that omit `author_association`. (#252, #256)
- **Ship run context now uses `jury_mode` consistently.** Closure/run-context contracts and
  rendered closure comments advertise the `jury_mode` field (not `jury`) for the resolved
  `off` / `advisory` / `gating` value. (#254)

### Fixed
- **`adapter-status` no longer flags opt-in legacy wrappers as `missing`.** Legacy
  claude wrappers (`legacy-claude`) are installed only by `install-legacy-wrappers`,
  so `adapter-status all` previously reported a spurious `missing` row for every
  never-installed wrapper on a clean install. Uninstalled legacy wrappers are now
  omitted (treated as *not installed*); installed ones are still freshness-checked.
  Documented the `legacy-claude` target in the CLI reference. (#260)
- **Capture redaction — close credential leaks and stop mangling code.** The
  `credential-assignment` rule now redacts JSON-quoted keys (`"api_key": "…"`) and
  values opened with an unbalanced quote (`KEY="secret` with no close), both of which
  previously leaked. The value matcher consumes a complete quoted string or a possessive
  unquoted run and rejects function-call / subscript expressions (`token = get_token()`,
  `csrf_token = request.headers['X-CSRF']`) and `${…}` / `$(…)` references instead of
  mangling them mid-string. An 8-character floor on every value arm keeps short
  status strings (`token: "none"`, `api_key=""`) intact, and JSON keys redact
  cleanly with no orphaned quote. Compact JSON keeps its sibling fields. (#257, #261)
- **Jury gate no longer skips oversize diffs silently.** A diff over
  `MAX_DIFF_BYTES` (1 MB) still passes the jury gate (fail-soft), but now emits a
  non-blocking `nit` advisory finding (`jury:skipped-oversize`) so the skip surfaces
  in the posted jury verdict instead of letting an oversize diff dodge the jury
  stage unobserved. (#258)
- **Risk escalation keeps its side-effect context.** Operator consent escalation now
  preserves the side-effect list passed by callers instead of collapsing it during
  risk/trust evaluation. (#253)

## [1.2.0] — 2026-06-11

### Added
- **Step verification contract** — keel core now exposes a deterministic
  `keel.step-verification.v1` contract that fail-closed checks each fixed-backbone step's
  completion and proves the structured handoff between steps via `keel.step-handoff.v1`, so a
  step can no longer be marked done by adapter prose without the required evidence. A canonical
  step-handoff renderer is added to `keel.artifacts`, and `contract.step_verification` is
  exposed in the ship command contracts. (#233)
- **`keel adapter-status` surfaces orphan & unmanaged keel-like files** — the command now
  scans the managed surface directories (`commands/`, `.claude/commands/keel/`,
  `.claude/commands/`, `.agents/skills/keel-*`, `.agents/skills/source-command-*`) for files
  outside the currently-expected set, in two deliberately separated confidence classes.
  Class (a) — deterministic — reports a file carrying a `keel-generated` marker whose
  `command=` is no longer in the installed keel command set as `orphan (stale-marker)` (e.g. a
  `keel-ship-v2` skill left behind after the `ship-v2` command was removed in 1.1.0), with a
  reason code naming the unknown command. Class (b) — heuristic, behind the new
  `--include-unmanaged` flag — reports marker-less command-like surfaces as
  `unmanaged (no-marker)`, never flagging commands the project declares as project-only via
  `policy_pack`. `adapter-status --json` includes the new findings, and `keel sync` /
  `update-adapter` print a one-line heads-up when orphan/unmanaged files are present. Purely
  advisory: keel never auto-deletes and these findings never gate a run. (#234)
- **Deterministic run controls** — a pure-core `keel.run-controls.v1` guardrail bounds agentic
  loops (fixloop, reviewer/tester dispatch) with per-run work-unit budgets, per-slot step caps,
  and deterministic oscillation detection, emitting structured fail-closed halt reasons rendered
  through `keel.artifacts.render_run_control_halt`. `contract.run_controls` is exposed for ship,
  pr-loop, review-cycle, work-block, and overnight; invalid limits fall back safely and soft
  failures are preserved. (#236)
- **Work creation policy** — a shared deterministic `keel.work-creation.v1` policy governs
  signal-driven issue creation across regression, review-all-day, coverage, deps-audit, and
  flake-audit, replacing command-local logic. It yields `create`, `suppress-transient`,
  `suppress-duplicate`, and `limit-reached` decisions via occurrence/confidence transient
  filtering, open-work dedupe (explicit key, normalized title, near-text similarity), per-cycle
  creation limits, and same-cycle duplicate suppression, exposed through the scan and reporting
  contracts as `work_creation_policy`. (#237)
- **Agent-output provenance** — a pure-core agent-output provenance contract tags structured
  findings and step handoffs with source, vendor/model, and capability-scope metadata so
  untrusted agent output can be attributed and scoped downstream. The
  `contract.agent_output_provenance` block is exposed in the ship command contracts. (#238)
- **Resource claim primitive** — the existing `mkdir` merge lock is generalized into a pure-core
  single-host resource-claim primitive. Merge-lock behavior is preserved (`LockError` still
  raised for a held merge lock) while general resource claims get structured deny/release
  feedback, and the `contract.resource_claims` block is exposed in the ship command contracts. (#239)
- **Risk × trust consent escalation** — operator consent gains a deterministic risk × trust
  escalation contract that gates side-effecting actions in the escalation decision, adds
  repeated-retry, conflicting-source, and large-diff triggers, and supports deterministic
  low-risk sampling, surfaced under `contract.operator_consent.risk_trust_escalation`. (#240)
- **Ship run-context as durable PR evidence** — the s0 preflight run context (resolved
  GitHub transport `gh`|`mcp`, host agent, workflow profile, jury mode, and operator-consent
  summary) is now persisted on the `ship_run` ledger record and rendered as a deterministic
  **Run context** block in the s11 closure comment, so it is durable PR evidence rather than
  an ephemeral chat line. `keel ship --append-ledger` gains `--host-agent` and `--transport`
  (`gh`|`mcp`) inputs; `--transport` defaults to the transport keel resolved for the run, the
  profile is threaded from `--profile`, the jury mode is derived from the resolved review
  contract, and the consent summary is derived from the existing `--operator`/`--approve-scope`
  inputs. The block is additive — the `keel.closure-comment.v1` marker and every existing
  closure line stay byte-identical, so the evidence verifier is unaffected — and missing
  fields degrade gracefully (`unknown`/`off`/`none`). The `closure_comment` contract, the ship
  adapter (s0/s11), and the CLI/command-contract docs are updated to match. (#242)

## [1.1.0] — 2026-06-10

### Changed
- **BREAKING:** removed the `keel ship-v2` command (`/keel:ship-v2`). The compound-engineering
  profile is now a flag on `ship`: `keel ship --compound` (`/keel:ship --compound`), with
  `--profile compound` as the long form. It is the same backbone, the same safety gates, and
  the same s4/s7/s9/s11 step overrides — only the invocation surface changed (a removed
  command became a profile flag). `keel plan --command ship --profile compound` renders the
  same compound contract. The `ship-v2` adapter, plugin command, Claude slash command, and
  `keel-ship-v2` skill were deleted. (#223)
- **Required evidence gate is now opt-in** — `keel evidence-verify` enforces the fail-closed
  pre-merge evidence contract only when the PR carries the `evidence_gate_label` knob
  (default `keel:ship`), which `keel:ship` applies when it opens the PR. PRs without the
  label report `enforced: false`, `required: 0`, status `pass`, so hand-authored PRs that
  never went through ship are no longer blocked. New `--gate-label` and `--pr-label` flags
  override the knob and inject labels for offline harnesses; the JSON payload now carries
  `gate_label`, `enforced`, and `pr_labels` (additive — `keel.evidence.v1` is unchanged).
  References #221.

### Removed
- **Outdated forward-looking docs** — deleted `docs/keel/vision.md` and removed its
  forward-looking positioning content and links from `README.md`, `website/index.html`, and
  `docs/keel/commands.md`; dropped the file from the consumer-neutrality scan surfaces.
  Current-product positioning is unchanged.

### Fixed
- **Docs correctness** — removed a dead `docs/proposals/divergence-audit-2035.md` link from
  the README; documented `keel status` and `keel work-block` in `docs/keel/cli.md`; and
  corrected the `AGENTS.md` repo-layout label for `commands/`.

## [1.0.2] — 2026-06-09

### Fixed
- **Ship comment evidence on every path** — the `ship` adapter now explicitly requires
  operator-driven runs, delegated runs, every tier, and the TIER-1 single-reviewer path to
  post the s7 review verdict as a distinct PR review/comment. The s11 ship-outcome closure
  must also be posted as distinct issue and PR comments, never folded into the PR body or
  represented by the automated CI assessment block.
- **Closure evidence marker** — rendered ship-outcome comments now include a stable hidden
  `keel.closure-comment.v1` marker so future evidence checks can distinguish the actual s11
  closure from PR bodies, chat summaries, and CI assessment comments.

### Removed
- Removed the stale legacy `adapters/` directory; the canonical adapter source is
  `src/keel/adapters/commands/` (generated into the plugin `commands/`,
  `.claude/commands/keel/`, and `.agents/skills/keel-*`). Docs (`CLAUDE.md`, `AGENTS.md`,
  `README.md`) repointed accordingly.

## [1.0.1] — 2026-06-09

### Fixed
- **Ship learning-capture policy wiring** — `keel ship` now passes the loaded project
  config, existing ledger records, issue title, and issue labels into the ship run ledger
  record builder. Learning-quality decisions configured under
  `policy_pack.capture.learning` now take effect in the production CLI flow, duplicate
  suppression compares against existing ledger history, and the learning fingerprint uses
  the intended issue context.
- **Learning defer reason hygiene** — defer-mode learning decisions now keep the reason
  policy-owned instead of propagating raw operator capture notes.

## [1.0.0] — 2026-06-09

### Added
- **1.0 work-ownership release line** — Keel now promotes the complete v1 backbone:
  consent, issue intake, run ledger, checkpointing, status snapshots, work blocks,
  capture, redaction, reconcile hooks, learning-quality gates, capture-health visibility,
  and Claude plugin packaging.

### Changed
- **Release readiness alignment** — package metadata, plugin metadata, dogfood configs,
  examples, README, website, and docs now point at the `1.0.0` / `^1.0` line.
- **Stable command surface** — public docs now describe the full 17-command adapter set,
  including `/keel:work-block`.

## [0.9.0] — 2026-06-09

### Added
- **Daytime work-block command** — `keel work-block` / `/keel:work-block` now exposes a
  first-class daytime multi-issue work-block preflight contract. It accepts explicit issue
  numbers or a queue selector, hands each ready item to `ship`, refreshes issue readiness
  between items, preserves per-issue worktree isolation, consent, capture, run-ledger, merge
  lock, and merge-window invariants, and reports shipped / PR-open-not-merged / deferred /
  blocked / skipped / needs-input buckets.
- **Command step evidence** — every packaged `/keel:` adapter now carries a "Command step
  evidence" contract requiring observable per-step work output, so a command run leaves a
  visible trail instead of opaque prose. `/keel:ship` additionally requires a meaningful
  draft-PR body (Context / Changes Made / Testing / Docs Impact sections plus a closing issue
  reference) and public PR evidence for its review verdicts and jury summaries (posted via a
  body file). Closes #162.
- **Capture artifact redaction** — durable capture records are sanitized before persistence,
  stripping private-key blocks, bearer / GitHub tokens, credential-bearing URLs, and
  token / password assignments. Projects can extend the deny set without leaking
  project-specifics into core via `policy_pack.capture_redaction.deny_patterns`. An invalid
  redaction policy skips the durable ledger append with an `invalid-policy` reason rather than
  persisting unsanitized data, and the audit block records rule ids and match counts only —
  never the redacted secret. Closes #142.
- **First-class post-merge capture** — a consumer-neutral `keel.capture.v1` contract with the
  stable marker `compound-learning: pr=<N> status=<status>`, exposed as `contract.capture` in
  `keel plan --json` and nested under the run-ledger contract. `ship_run` ledger records now
  store the capture marker metadata, offline session-end verification is available via
  `keel capture-verify`, and the flow has a recursion guard plus fail-soft semantics (a
  capture failure after a successful merge never reverts the merge). Projects own what to
  learn and where it goes through the `capture` / `post-merge` Lego extensions or policy.
  Closes #134.
- **Progress snapshot** — `keel status --root [--json]` emits a `keel.progress-status.v1`
  last-safe-boundary snapshot. It reads the active checkpoint (current issue / step, PR,
  branch, worktree, wait reason, next queued issue) and the run ledger (shipped / blocked /
  deferred / skipped counts), reporting the last safe boundary known to keel. It is a
  checkpoint + ledger snapshot, not a live process stream, and is consumer-neutral. Closes #148.
- **Deterministic closure-comment renderer** — keel core now renders the s11 "ship outcome"
  comment from the structured `ship_run` ledger record via the pure
  `keel.closure.render_closure_comment` function, exposed under `result.closure_comment` of
  `keel ship --json` and described by the new `closure_comment` contract on `ship` /
  `ship-v2`. The comment is consumer-neutral (the project codename comes from the record's
  `target`, never a literal), deterministic (golden-tested), and a mirror of the ledger — not
  a parser source. The `ship` adapter s11 step now posts this rendered markdown verbatim
  instead of hand-written prose. See
  [`docs/keel/command-contracts.md`](docs/keel/command-contracts.md).
- **`Docs touched` line in the closure comment** — the deterministic closure-comment renderer
  (`keel.closure.render_closure_comment`) now emits a `- **Docs touched:** yes|no` line
  directly after the Changed files block, and the `closure_comment` contract lists the new
  `docs_touched` section. The value is derived deterministically and consumer-neutrally from
  the ledger's existing `changes.files`: a file counts as docs when any path component equals
  `docs` (case-insensitive) or its suffix is one of `.md`, `.mdx`, `.markdown`, `.rst`,
  `.adoc`. `.txt` is intentionally excluded (false-positive prone, e.g. `requirements.txt`);
  text docs are covered by the `docs/` directory rule. No ledger-schema or project-config
  changes.

### Changed
- **Shared work-block primitive** — `overnight` now references the same `keel.work-block.v1`
  primitive exposed by the new `work-block` command instead of owning a parallel queue
  contract.

## [0.8.0] — 2026-06-09

### Added
- **Claude Code plugin packaging** — keel now ships its `/keel:<command>` workflows as a
  Claude Code plugin. The repo is its own single-plugin marketplace, so users can add it with
  `/plugin marketplace add berkayturanci/keel` and `/plugin install keel` — no `pip install`
  required. The committed `commands/*.md` plugin bodies are generated from
  `src/keel/adapters/commands/` (the single source of truth) via `make plugin` /
  `keel install-adapter plugin`; a drift test keeps them byte-identical and a version test
  keeps `.claude-plugin/plugin.json` in lockstep with `keel.__version__`. The existing
  `pip install keel-workflow` + `keel install-adapter` flow is unchanged (additive). See
  [`docs/keel/plugin.md`](docs/keel/plugin.md).
- **Configurable consent modes** — live mutating runs can now satisfy the operator-consent
  preflight from a standing approval source in addition to the interactive `--approve-scope`
  flag: the `KEEL_APPROVE_SCOPE` / `KEEL_OPERATOR` environment variables and the typed
  `automation.approved_scopes` / `automation.operator` config keys, with precedence
  `--approve-scope` > env > config. Every consent contract and approved live record now
  records its `approval_source` (`flag` / `env` / `config` / `none`) for audit. Standing
  approval only satisfies the consent preflight — findings, CI, project gates, merge windows,
  and merge locks are unaffected, and any unlisted required scope still fails closed. Closes #136.
- **Issue intake readiness gate** — work-owning flows now classify an issue as
  `ready` / `needs-input` / `blocked` / `out-of-scope` before any code mutation, exposed as
  `issue_intake` in command contracts and ship dry-run results with `--issue-title`,
  `--issue-body`, and `--issue-label` CLI inputs. Non-ready issues block the live
  ship/implement preflight and are skipped in favour of the next ready issue. Closes #147.
- **Structured run ledger** — ship runs can append deterministic, consumer-neutral JSONL
  records (`keel.run-ledger.v1`) capturing the run outcome, so a work owner has an auditable
  history of what it shipped.
- **Resumable run checkpoints** — long ship runs persist a stable checkpoint
  (`keel.checkpoint.v1`, default `.keel/state/checkpoint.json`) with a per-step resume map, so
  an interrupted run can `resume` from the last completed backbone step instead of restarting.

### Changed
- **Agentic work-ownership positioning** — README, website, and docs now frame keel as the
  backbone for agents that take ownership of software work from issue intake through shipped
  outcome.
- **Competitive comparison module** — the website now includes a comparison view that
  distinguishes keel's issue-to-done work ownership model from adjacent agent, CI, and workflow
  automation tools.

## [0.7.0] — 2026-06-08

### Added
- **One-command onboarding** — `keel setup` creates or reuses `.keel/project.yaml`, installs
  the generated Claude and shared-skill adapter surfaces, validates the project config, and
  renders the plan in one first-run command. Existing configs are preserved unless `--force`
  is explicit.
- **Safe adapter refresh shortcut** — `keel sync` wraps the generated-adapter update flow with
  a dry-run friendly command for existing consumers. It refreshes only marker-protected
  generated adapter files, never project config, extensions, policy docs, or project-owned
  commands.
- **Claude plugin onboarding** — a local plugin manifest and `keel-onboard` skill document the
  setup path for Claude users while keeping the CLI as the source of truth.

### Changed
- **Docs and website onboarding refresh** — README, CLI docs, cutover docs, onboarding docs,
  and the website now explain `setup`, `sync`, package-upgrade boundaries, extension safety,
  and the generated-surface contract.
- **Release smoke coverage** — the package smoke test now exercises `keel setup` and `keel sync`
  so published builds verify the current onboarding and adapter-refresh path.

## [0.6.1] — 2026-06-08

### Added
- **Release verification** (#78) — `scripts/release_smoke.py` installs a local, PyPI, or
  TestPyPI package into a clean virtual environment and verifies the `keel` CLI plus generated
  adapter surfaces; `docs/keel/release.md` documents the repeatable PyPI release runbook.

### Changed
- **Public docs refresh** (#119, #120) — README, website, and the `project.yaml` reference now
  reflect the current `keel-workflow` package identity, `0.6.x` core line, every-step extension
  hooks, runtime capabilities, project commands, workflow policies, and policy-pack fields.
- **GitHub Actions maintenance** (#55) — bumped the grouped workflow actions and pinned the
  updated actions to exact commit SHAs, including checkout, setup-python, CodeQL, Pages actions,
  and the workflows that dogfood Keel's PR assessment.

## [0.6.0] — 2026-06-07

### Added
- **Consumer-neutral core** (#77) — the core carries no downstream project names or
  workflows, with a `tests/test_consumer_neutrality.py` guard enforcing it.
- **Runtime capability detection** (#68) — `keel capabilities` reports the runtime's
  detected capabilities and GitHub transport; configs declare `required_capabilities` /
  `optional_capabilities` (per-project knobs and per-gate extension fields). Missing
  required capabilities block; missing optional ones degrade.
- **GitHub transport resolver** (#62) — a normalized resolver picks `gh` (authenticated
  CLI) over host-provided MCP/API access and surfaces degraded operations explicitly.
- **Structured command contracts** (#66) — `keel plan --json` and `keel ship --json`
  emit deterministic, schema-stable contract + result records for adapters to consume.
- **Operator consent gate** (#82) — `keel ship` / `keel plan` accept
  `--live`, `--approve-scope`, `--operator`, and `--target`; live preflight emits an
  operator-consent contract and fails closed when required scopes are unapproved.
- **Safe Codex adapter** (#58) — the packaged Codex adapter runs read-only/sandboxed
  by default.
- **Generated-surface verification** (#79) — `keel adapter-status` reports generated
  adapter freshness against the packaged source bodies.
- **Adapter update/compat flow** (#80) — `keel update-adapter` safely refreshes
  generated adapters (with `--dry-run`) while respecting local markers.
- **Project policy packs** (#65) — projects can declare risk rules, test groups, docs
  requirements, and health providers via a validated policy pack.
- **Extension hooks on every backbone step** (#56) — extension slots span the full
  backbone so add-only Lego hooks can attach at each step.

### Changed
- **Parity matrix** (#63) — a command/capability parity matrix is captured and locked by
  `tests/test_parity_matrix.py`.
- **Public-repo readiness** — `LICENSE` (Apache-2.0), `SECURITY.md`, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, issue/PR templates, `CODEOWNERS`, Dependabot, and a `.pre-commit-config`.
- **PyPI packaging** — `pyproject` carries full metadata (Apache-2.0 SPDX license, classifiers,
  project URLs) and a `publish.yml` workflow: a `v*` tag builds the sdist+wheel and publishes to
  PyPI via **trusted publishing (OIDC)**, with a CycloneDX SBOM, `SHA256SUMS`, build-provenance
  attestation, and a generated GitHub Release. `pip install keel-workflow`.
- **Security workflows** — `codeql.yml` (per-push/PR + weekly) and `scorecard.yml` (OpenSSF
  Scorecard from `main`), Actions pinned to commit SHAs.
- **Brand + site polish** — an SVG **hero** (dark/light, the backbone visualization) and a
  `favicon.svg`; README badges (CI, coverage, CodeQL, PyPI, Python, license); the website embeds
  the hero + favicon, and `pages.yml` now also publishes a self-hosted `coverage-badge.json`.
- **Command reference** (`docs/keel/commands.md`) + a **Workflow commands** section on the
  website — all 16 `/keel:<command>` workflows, each with its description and which surface
  installs it.
- `make adapters` now installs **both** surfaces (`install-adapter all`), and keel dogfoods its
  own `.claude/commands/keel/` + shared `.agents/skills/keel-*` skill set.
- Retired the stale per-agent adapter stubs (`adapters/{codex,gemini,agy}/keel-ship.md`); the
  packaged bodies under `src/keel/adapters/commands/` are the single source, and
  `adapters/README.md` documents the two-surface model.
- **Consolidation gate** (#94) — restored 100% line+branch coverage on the pure core and
  raised the coverage gate to `fail_under = 100`; removed dead code (`MUTATING_CAPABILITIES`).

## [0.5.0] — 2026-06-05

### Changed
- **`install-adapter` now targets two real surfaces, not one dir per agent** (**breaking**).
  Agents don't each read their own command dir — Claude reads `.claude/commands/`, while every
  other agent (Codex, Antigravity, Gemini, …) discovers a **shared** skill set under
  `.agents/skills/`. So keel installs into exactly those two surfaces:
  - `keel install-adapter claude` → `.claude/commands/keel/<cmd>.md` (native `/keel:<cmd>`).
  - `keel install-adapter skills` → **one** shared `.agents/skills/keel-<cmd>/SKILL.md` set
    (rendered from the same adapter via `install.render_skill`), read by all non-Claude agents.
  - `keel install-adapter all` → both.
  The previous per-agent targets (`codex`/`gemini`/`agy` → their own `keel/` dirs, and the
  0.4.0 `all` fan-out over them) are **removed**: they were inert (no agent read them) and
  re-introduced the file-copy duplication keel exists to eliminate. One skill copy now serves
  Codex + Antigravity + Gemini together.

## [0.4.0] — 2026-06-05

### Added
- **`keel install-adapter all`** — install the `/keel:<command>` adapters into **every** known
  agent dir (Claude + Codex + Gemini + agents) in a single run, instead of one agent at a time.
  Per-agent install is unchanged; `all` just fans out over `AGENT_DIRS` (`install.install_all`).
- **Cutover guide** (`docs/keel/cutover.md`) — the staged, verified process for a consumer to
  retire its copied command bodies: install + `keel install-adapter` → A/B verify `/keel:ship`
  on a low-risk test issue → retire the portable bodies (keep project-only) → move project
  specifics to knobs/Lego. Rollback = revert the PR. Lose nothing.

## [0.3.0] — 2026-06-05

### Changed
- **All `/keel:<command>` adapters brought to full project-neutral parity** (#34) — ported from
  the reference workflow bodies, capturing their real operational detail while reading every
  project value from `.keel/project.yaml`: `ship` (GitHub transport abstraction, blocker
  auto-detect, attribution + model-base stripping, mkdir-mutex merge, narrowed fix-loop),
  `regression` (parallel read-only area fan-out + multi-pass dedupe), `triage` (per-issue
  classifier subagent, closed label vocabulary), `flake-audit` (across-runs-disagreement rule),
  `coverage` (base→head delta, hot-spot tiering), `deps-audit`, `ci-check`, `stale-prs`,
  `pr-loop`, `review-cycle`, `review-all-day`, `morning`, `overnight`, `wrap`, `implement`;
  `ship-v2` is a pointer to `keel:ship` (no distinct portable backbone). **Zero downstream/
  app-specific references** — verified.

## [0.2.1] — 2026-06-05

### Changed
- Reverted the experimental post-merge **branch deletion** added after 0.2.0 (#38 → #39):
  deleting a merged head branch is GitHub's *"Automatically delete head branches"* repo
  setting, not keel's job. No other functional change since 0.2.0.

## [0.2.0] — 2026-06-05

### Added
- **Agentic `/keel:<command>` adapters + `keel install-adapter`** (#34) — keel now ships a set
  of project-neutral agentic workflow commands (`ship` — the full flow: per-round review,
  inline comments, `--delegate`/`--review-delegate`/`--review-comments`/`--dry-run`, the jury
  gate — plus `regression`, `implement`, `review-cycle`, `pr-loop`, `morning`, `overnight`,
  `wrap`, `triage`, `stale-prs`, `ci-check`, `deps-audit`, `flake-audit`, `coverage`). They are
  packaged with keel; `keel install-adapter <claude|codex|gemini|agy>` drops them into the
  project's agent command dir so they appear as `/keel:<command>` (installed, never hand-copied
  → no file-copy drift). Existing files are skipped unless `--force`.
- **`jury` gate runner** (#34) — the built-in `jury` gate now invokes the ai-jury CLI on the
  change's diff when it is installed, mapping its findings (file/line/severity) into keel
  Findings (critical/major block); when `jury` is absent the gate is a fail-soft no-op, so
  the flow runs with or without jury. keel takes **no** runtime dependency on ai-jury.
  Wired into `keel run-gates` / `keel ship` via `git diff base...HEAD`.
- **AI entry points** — a canonical, cross-AI `AGENTS.md` (the durable source of truth:
  backbone + invariants, pure-core/thin-I/O split, the 100% coverage bar, the
  single-runtime-dependency rule, conventions, and keel's config-driven agent dispatch)
  with a thin `CLAUDE.md` pointer. `projects/keel.yaml` `sot_doc` now points at
  `AGENTS.md`, matching the other configs.

## [0.1.0] — 2026-06-05

### Added
- **Website + live coverage** — a static site in `website/`; `make site` builds the coverage
  HTML into `website/coverage/` and serves it locally. A manual (`workflow_dispatch`)
  `pages.yml` can publish to GitHub Pages when enabled. `keel init --wizard` interactively
  sets the base branch, **merge-window hours**, timezone, and build/lint commands (#23).
- **Enhancements from the competitive analysis** (see `docs/keel/comparison.md`):
  - `keel init` — golden-path scaffolder: detects the stack (Flutter/Python/Node/
    Android/generic) and writes a valid default `.keel/project.yaml` (#19).
  - Gate findings now carry **`path`/`line`** parsed reviewdog-style from tool
    output, so the fix-loop and inline comments get real locations (#17).
  - `merge_window_mode: freeze|pause` — `freeze` (default) blocks the merge but
    keeps gates running; `pause` halts the pipeline outside the window (#18).
  - **Hotfix bypass**: `keel ship --hotfix` (or a `hotfix` label) merges outside the
    window — never bypassing findings or CI — with an audit line (#20).
- **keel-core** Python package (`src/keel`), stdlib-first with a single runtime
  dependency (PyYAML):
  - `jsonschema_min` — dependency-free JSON-Schema (draft-07 subset) validator.
  - `config` — load + validate `project.yaml` into a typed, immutable
    `ProjectConfig` (knobs + add-only extension slots) with a deterministic
    `config_hash`.
  - `model` — the fixed backbone (steps s0–s12), named slots, and invariants
    (single source of truth; the schema's slots are asserted against it).
  - `findings` — structured `Finding` + severity→decision mapping
    (critical/major=block, minor=suggest, nit=advisory) + `summarize`.
  - `extensions` — parse + validate project Lego extensions (add-only into named
    slots; `on_fail: block` only in `pre-merge`; agentic/command contract) with a
    fail-soft loader.
  - `gates` — plan built-in (build/lint/jury) + extension gates and run them
    through an injected runner with fail-soft semantics.
  - `orchestrator` — pure `build_plan`/`render_plan` mapping a project's
    gates/extensions onto the fixed backbone (deterministic, dry-run view).
  - `agents` — dispatch resolution + #2036 attribution (model-base stripping).
  - `runner` — thin, fail-soft subprocess wrapper + `command_gate_runner` that
    executes `command` gates (build/lint/command Lego).
  - `window` — merge-window logic (the night no-merge invariant, timezone-aware).
  - `lock` — the `mkdir`-based merge lock (context manager).
  - `classify` — pure risk-tier classification from changed files vs. globs.
  - `ship` — deterministic ship decisions (reviewer count, merge/defer/block,
    fix-loop budget) + `assess` (whole decision: tier → reviewers, window, CI, merge).
  - `cli` — `keel version | validate | plan | run-gates | window | ship`
    (`keel ship` = dry assessment of the agent-free backbone slice).
- Adapter: `adapters/claude/keel-ship.md` (thin, project-neutral `keel:ship`) +
  `adapters/README.md` (the adapter model).
- CI: `keel-ship` GitHub Actions workflow — runs `keel ship` live on the free hosted
  runner for every PR (uses the runner's `git` + `gh`/`GITHUB_TOKEN`), comments the
  assessment, and fails the check on a `BLOCK` decision. Docs in
  `docs/keel/github-actions.md`.
- Bundled schema `src/keel/schema/project.schema.json`.
- Seed configs `projects/{example-android,example-flutter,keel}.yaml`.
- Docs: README, `docs/keel/{configuration,extensions,cli,comparison}.md`,
  `docs/proposals/{keel-architecture,divergence-audit-2035}.md`.
- CI: cross-OS × Python matrix running tests, ruff, and the coverage gate.
- Test suite: 105 unit tests at 100% line + branch coverage on the core.

### Changed
- Repository repositioned from **ai-infra** (one-way file-copy sync) to **keel**
  (thin-consumer: pinned install + per-project config/extensions). Direction
  reversed: changes originate centrally and propagate down to projects.

### Removed
- `scripts/sync.sh` and the `/sync-to-ai-infra` mechanism (retired; superseded by
  the thin-consumer model).
