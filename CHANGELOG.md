# Changelog

All notable changes to keel are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); keel adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Activity Verdict CLI Flag** (#861):
  - Added `--verdict` flag (`choices: pass, blocked`) to `keel activity --write`.
  - Allowed recording phase completion verdicts through the CLI.

### Security
- **Swarm Worktree Isolation Failure Protection** (#867):
  - Verified worktree creation success in `swarm_runtime.py` `_worker_fn` and failed the cluster execution immediately on failure.
  - Prevented multiple concurrent worker threads from falling back to running `keel ship` simultaneously in the repository root upon worktree errors.
- **Cloud Metadata SSRF Protection** (#866):
  - Blocked cloud metadata hosts (`169.254.169.254`, `metadata.google.internal`, `instance-data`) and link-local IP addresses in `endpoint_issues` unconditionally, even when `KEEL_ALLOW_REMOTE_ENDPOINT` is enabled.
  - Protected cloud instances against SSRF credential extraction via delegate endpoint configurations.
- **Delegate Profile Credential Exfiltration Protection** (#865):
  - Explicitly blocked high-privilege system credential names (`GITHUB_TOKEN`, `AWS_*`, `NPM_*`, `PYPI_*`, `SSH_*`) from being declared in `knobs.delegate_profiles.<name>.api_key_env`.
  - Prevented untrusted repository configurations from exfiltrating system credentials via remote LLM endpoints.

### Fixed
- **Legacy Claude Wrapper Plan Arguments Forward** (#863):
  - Removed `"$@"` arguments forwarding to `keel plan` in `src/keel/install.py` `render_legacy_claude_wrapper()`.
  - Prevented unrecognized arguments error when preflighting legacy slash commands with targets/flags.
- **Merge Auto-Stamping Run ID Fallback** (#859):
  - Passed `gates_run_id` to `_autostamp` in `src/keel/cli.py` `_cmd_merge` when `--run-id` is omitted from CLI arguments.
  - Ensured merged status is stamped to activity records on the board when landing merges.
- **Swarm Landing Merge Failure Abort** (#857):
  - Added `git merge --abort` on merge failure in `src/keel/swarm_landing.py` `merge_cluster_branch()`.
  - Prevented leaving repositories in an uncommitted/conflicted `MERGE_HEAD` state upon merge conflicts.
- **Config YAML Error Formatting** (#855):
  - Wrapped `yaml.YAMLError` in `ConfigError` inside `src/keel/config.py` `load_config()`.
  - Prevented raw tracebacks on syntax errors in `project.yaml` files across all CLI subcommands.
- **Capture Reconciliation Invalid Marker Accounting** (#853):
  - Added `invalid-marker` finding detection in `src/keel/captureverify.py` during ledger reconciliation.
  - Ensured corrupted, duplicate, or malformed capture markers produce explicit audit findings rather than silently passing reconciliation with `ok: true`.

## [1.18.0] - 2026-08-19

This release brings a 3-way evidence status to the evidence gate so that in-flight PRs
never report false-positive failures, brings swarm cluster landings to full review parity
with the ship backbone, and improves release automation and accessibility.

### Added
- **Evidence Gate Neutral Pre-Verdict State** (#829):
  - Distinguished pre-review / waiting evidence states from invalid evidence in `keel evidence-verify`.
  - Added `status: "waiting"` (CLI exit code `2`, GitHub check-run conclusion `neutral`) when required reviewer or jury verdicts are not yet posted on in-flight PRs, removing false-positive red CI icons while keeping merge security strictly fail-closed.
  - Retained `status: "fail"` (CLI exit code `1`, GitHub check-run conclusion `failure`) for explicit evidence violations (commit SHA mismatch, closure record tampering, missing attribution labels, or unarmed gates).
- **Swarm Cluster Review Parity at Landing** (#830):
  - Held unreviewed clusters at the landing stage, bringing swarm wave landing into strict review parity with the single-issue ship backbone.
- **Semantic Versioning (SemVer) Calculation Runbook** (#850):
  - Documented standard SemVer 2.0.0 calculation rules and decision matrix in `docs/keel/release.md`.

### Fixed
- **Diff-Based Risk Tier Assessment** (#846):
  - Classified the ship assessment's risk tier directly from the git diff rather than metadata heuristics, matching gate behavior.
- **Release Pipeline Digest Verification** (#843):
  - Stopped release publish jobs from failing over unbuilt package digests during multi-phase builds.
- **Integrations Search Accessibility** (#848):
  - Added an explicit `sr-only` accessibility label for the site's integrations search box.
- **Homebrew Formula Checksum Sync** (#841):
  - Ensured Homebrew formula releases resolve matching release archive checksums.

## [1.17.0] - 2026-08-18

This release is mostly about keel's own signals telling the truth. Three gates and one
instruction were quietly wrong in ways that only showed up locally, and a check that is
always red is a check people stop reading.

### Added
- **`keel doctor` now reports whether the importable keel is the checkout you are in** (#825, #826):
  New `checkout_binding` check, reported first because it contextualises every check below it.
  `pip install -e .` registers one source tree for the whole interpreter, so installing from a
  second checkout silently repoints every other one — imports, the test suite, and coverage all
  follow the other tree while the working directory suggests otherwise. Every previous check was
  *about* the installed package; none asked whether that package was the checkout at hand. A
  mismatch names both paths and the remedy, and is a `warn` rather than a `fail`: running against
  a deliberately installed release is legitimate, so exit codes are unchanged.
- **Site moved to [keel-ship.dev](https://keel-ship.dev)** (#813, #814), with deploys announced via
  IndexNow (#816, #817), search-language targeting, and a long-tail article — both pinned by tests
  (#808, #809), and the article page added to analytics coverage (#818, #819).

### Changed
- **The `bandit` preset no longer scans tests, virtualenvs, or nested checkouts** (#834, #837):
  `bandit -r . -ll` walked everything below the working directory, including trees git is told to
  ignore. All 23 findings it reported were in test code — hardcoded `/tmp`, `urlopen`, XML parsing,
  all normal in tests — while `src/` had zero findings and there was no high severity at all. A
  local `.venv` took it to 875 high by walking installed dependencies. The preset now excludes
  those trees as prefix-independent globs. **This affects every project using `presets: ["bandit"]`**,
  and is the reason this is a minor rather than a patch release. The gate remains `suggest`, pinned
  by a test so signal-quality work cannot quietly make it blocking.

### Fixed
- **The coverage gate's `omit` pattern was prefix-bound** (#820, #821): it resolved to one absolute
  path anchored at `pyproject.toml`, while `source = ["keel"]` resolves the *importable* package,
  whose location depends on the environment. When those disagreed, an unexecuted `__main__.py` from
  another checkout was counted and the local 100% gate failed at 99% — on CI it passed, so only local
  runs looked broken, which is the corrosive kind.
- **`AGENTS.md` documented a CLI invocation that could not run your working tree** (#831, #832):
  the line above set `PYTHONPATH=src` for the tests; the CLI line did not. Bare `python3 -m keel`
  fails from a fresh checkout, and silently runs another checkout when a global editable install is
  registered. Now documented with the prefix, with the installed `keel` script called out as running
  the *released* version, and a guard so it cannot drift back.
- **The swarm simulator's copy button left its accessible name stuck** (#815, #827): the clipboard
  callback captured the `aria-label` it was about to overwrite, so a second click inside the 2s flash
  window captured the transient "Copied to clipboard" and restored *that*. Visible text recovered;
  the accessible name did not. Both labels now restore to the constants the markup ships with, and
  the pending timer is cleared per click. Follows #792/#810, which added the label and then synced it.
- **`keel-visual` rendered abandoned runs as still running** (#824).
- **Homebrew formula checksums are verified before the tap is updated** (#805, #806): 1.16.0 shipped
  carrying 1.15.0's checksum, so `brew install` downloaded the new tarball, compared it to the old
  digest and refused. The publish workflow now fails rather than syncing a formula that cannot
  install — a tap one release behind still works. The tap also pulls directly, dropping a cross-repo
  token (#807).
- **PyPI version check no longer follows redirects unguarded** (#811).
- **Documentation commands are checked against the real CLI surface** (#803, #804), and
  `.claude/launch.json` — harness-local state — is no longer left untracked (#835, #836).

## [1.16.0] - 2026-08-17

### Added
- **Diff-Based Risk Classifier & Policy Decoupling** (#786, #793, #801):
  - Added semantic diff-level risk classification (`src/keel/classify.py`) detecting sensitive changes (`permissions:`, `secrets.`, `uses:`) independently of directory globs.
  - Decoupled `tier3_globs` so routine test workflows run at Tier-2 while sensitive publishing/release workflows remain guarded at Tier-3.
- **Official Homebrew Tap Repository & Automation** (#762, #774, #776, #781, #788, #795):
  - Created and published official [`berkayturanci/homebrew-keel`](https://github.com/berkayturanci/homebrew-keel) tap with verified Apache-2.0 license, tag, and sha256 checksums.
  - Vendored PyYAML 6.0.2 resource into `Formula/keel.rb` ensuring zero-dependency `brew install keel` out of the box.
  - Added automated tap publishing to `.github/workflows/publish.yml` with cross-repo drift verification in `tests/test_distribution.py`.
- **Curated Multi-Agent Ecosystem & Authentic Brand Vectors** (#762, #768, #769, #770, #771):
  - Curated 32 pure Keel ecosystem integrations spanning 12 AI Coding Agents, 8 LLM Backends, 6 Protocols, and 6 Platforms.
  - Bundled 30+ official brand SVGs/PNGs under `website/logos/` with disk validation tests.
  - Audited and replaced all placeholder commands with 100% genuine, offline Keel CLI commands.

### Fixed
- **Swarm Conflict Resolution Empty-Block Safety** (#799): Stopped automatic resolution when one side of a conflict chunk is empty, preventing accidental code deletion and routing safely to manual/operator review.
- **Re-entrant Copy Buttons & Accessibility** (#792): Preserved original `aria-label` across repeated button interactions with automated regression tests.
- **Keel-Visual Version Drift Guard** (#797): Synchronized and guarded `keel-visual` package version markers.
- **Action Pinning & Supply Chain Verification** (#785): Pinned all remaining GitHub Actions to verified commit SHAs with automated upstream tag resolution.

## [1.15.0] - 2026-08-16

### Added
- **Keel Swarm High-Concurrency Multi-Agent Orchestration** (#714, #715, #716, #717, #718, #719, #720, #721):
  - **Deterministic Static Dependency Analysis & Clustering Engine** (#715): Static scope prediction, disjointness matrix calculation, and topological wave tier partitioning via `keel swarm-plan`.
  - **Terminal ASCII DAG Tree Visualizer & Live Dashboard** (#720): Terminal execution tree renderer (`keel swarm-plan --tree`) and live cluster status dashboard (`keel swarm-status`).
  - **Isolated Multi-Worktree Execution Runtime** (#716): Parallel cluster worker execution in dedicated git worktrees under `.keel/worktrees/swarm/` with dynamic drift rebalancing (`keel swarm-run`).
  - **Orthogonal Batch Landing & Drift Self-Healing Merge Engine** (#717): Dual-mode landing supporting Direct Orthogonal Batch Landing for disjoint trees and Adaptive Atomic Funnel Landing with automated rebase healing (`keel swarm-land`).
  - **Interactive 2D DAG Partition Graphs & 3D Multi-Wave Spatial Topology** (#721): Comprehensive visualizer additions in `keel-visual swarm` with interactive SVG DAG connectors and 3D WebGL/Canvas wave layer projection.
  - **Cross-Agent Swarm Command & Skill Adapters** (#718): Generated `/keel:swarm` command adapter for Claude Code and `keel-swarm` shared skill for Codex, Gemini, and Antigravity.
  - **Comprehensive Swarm Guide & Competitive Analysis** (#714, #719): Detailed architecture guide in `docs/keel/swarm.md`, competitive benchmark matrix in `docs/keel/comparison.md`, and proposal in `docs/proposals/keel-swarm.md`.
- **Multi-Agent Integrations & Ecosystem Catalog** (#763, #764): Interactive in-browser catalog highlighting 32+ out-of-the-box integrations across AI coding assistants (Claude Code, Cursor, Gemini CLI, Antigravity, Devin, Codex), LLM backends (Anthropic, OpenAI, Gemini, DeepSeek, Ollama local), skill libraries (Addy Osmani Skills, Compound, MCP), and platforms.
- **VS Code and Cursor Extension** (#747, #760): Native status bar merge window indicator and command palette shortcuts for VS Code and Cursor.
- **Interactive In-Browser Swarm DAG Simulator** (#746, #755): Real-time interactive canvas simulator modeling parallel multi-model worker waves, 3-vendor jury consensus, and merge lock funnel landings.
- **Token Analytics & USD Cost Estimation** (#744, #754): Pricing model ledger and CLI reporting via `keel cost-report`.
- **Canary Health Guard & Automated Rollback** (#743, #753): Post-merge regression canary monitoring and atomic git rollback guard (`keel canary` and `keel rollback`).
- **Conflict Self-Healing Rebase for Swarm Funnel Landings** (#745, #752): Declarative and AST conflict resolution for landed branches.
- **Official 1-Click GitHub Action** (#739, #751): `berkayturanci/keel-action@v1` for automated issue shipping and scheduled swarm runs.
- **Homebrew Tap Formula & Standalone POSIX Installer** (#741, #750): In-repo Homebrew tap (`brew install keel`) and curl installer script (`scripts/install.sh`).
- **`keel init --auto` Stack Auto-Detection** (#740, #749): Zero-prompt stack and gate scaffolding for Rust, Go, Python, Node, Flutter, Android, and Java projects.
- **PR Closure Viral Watermark & Dynamic SVG Badges** (#742, #748): Dynamic status badges and PR closure watermark attribution.
- **Comprehensive SEO & Structured Data** (#764): Full OpenGraph, Twitter Cards, FAQPage schema, and crawler-friendly metadata.

## [1.14.2] - 2026-08-14

### Changed
- **Modernized Documentation & Hero Visuals** (#711): Upgraded `docs/assets/hero.svg` and
  `docs/assets/hero-light.svg` with generalized multi-agent / multi-model positioning,
  isolated gradient namespaces for zero DOM collisions, crystal-clear linear step geometry,
  and dedicated `evidence locked ✓` guarantee badge.

## [1.14.1] - 2026-08-14

### Added
- **Policy Pack Presets Reference Documentation** (#708): Added full reference guide in
  `docs/keel/configuration.md` for declarative `policy_pack.presets` (`bandit`, `gitleaks`,
  `semgrep`, `trivy`), step mappings (`s3 guard` / `s8 test`), and fail-soft behavior.
- **Concurrent Gate Runner Documentation** (#708): Documented `--concurrency` and
  `knobs.concurrency` in `docs/keel/cli.md`.
- **Website & Interactive Documentation Updates** (#708): Added Security & policy presets topic
  in `website/content.js` and updated feature highlights in `README.md`.

## [1.14.0] - 2026-08-14

### Added
- **Security Policy Pack Dogfooding** (#704): Configured declarative `policy_pack.presets: ["bandit"]`
  in `projects/keel.yaml` and `.keel/project.yaml`, automatically planning and running Bandit SAST
  scans on every PR under `s8 test`.
- **Adversarial & ReDoS Security Test Suite** (#705): Added dedicated test suites covering
  exponential/dense JSON payloads, non-ASCII credential injections, deeply-nested schemas
  (depth 50+), extreme numerical bounds, and path traversal escape defenses.

### Fixed
- **Bandit B311 False Positive Suppression** (#703): Added inline `# nosec B311` annotation to
  non-cryptographic GitHub API retry delay jitter in `src/keel/github.py`, ensuring automated
  SAST pipelines report zero warnings.

## [1.13.0] - 2026-08-14

### Added
- **Concurrent Gate Execution** (#698): Added `concurrency` support to `run_gates` using
  standard library `ThreadPoolExecutor`. Independent build and lint command gates now
  execute concurrently while strictly preserving deterministic output order and fail-soft
  error handling.
- **GitHub API Jittered Exponential Backoff** (#699): Added `run_argv_retry` with
  exponential backoff and randomized jitter to fail-soft handle transient network
  interruptions, rate-limit responses (`429/403`), and upstream 5xx errors.
- **Security & SAST Policy Pack Presets** (#700): Introduced declarative `policy_pack.presets`
  (`gitleaks`, `semgrep`, `bandit`, `trivy`) automatically slotting security scans into
  blocking `s3 guard` or advisory `s8 test` steps without requiring custom scripts.
- **Compound Learning Retrieval Bridge** (#701): Added zero-dependency, pure-Python
  keyword retrieval (`retrieve_relevant_learnings` in `src/keel/capture.py`) matching past
  lessons from `.keel/learning/` directly into issue intake and implementation prompt context.
- **Vision-to-Production Positioning** (#562): Added "The Vision-to-Production Gap in Agentic
  AI" to README and website documentation, articulating Keel's role as a fixed workflow
  backbone driving code to production.

### Refactored
- **Modular CLI Command Handlers** (#697): Modularized `src/keel/cli.py` by extracting
  standalone subagent execution logic into `src/keel/standalone.py` and capability
  requirements into `src/keel/capabilities.py`, reducing monolith footprint while retaining
  100% backward-compatible test hooks.

## [1.12.0] - 2026-08-14

### Added
- **Comprehensive AI Models & Providers Guide** (#693, #692): Added `docs/keel/models.md`
  detailing how to use hosted APIs (Anthropic, OpenAI, Google Gemini via `GEMINI_API_KEY`),
  OpenAI-compatible profiles (OpenRouter, DeepSeek, Groq, Together, local vLLM/Ollama), and
  custom CLI agents. Reconciled all parameter tables, adapter help messages, and website surfaces.
- **Auditable Evidence Chain & Compliance Guide** (#563): Added `docs/keel/evidence.md`
  documenting commit-SHA binding, multi-vendor agent attribution, tamper-evident deferral records,
  and repository protection rulesets.
- **`keel verify-merge` — a post-merge guardrail against silent reverts** (#561): `keel
  merge` proved a merge *succeeded*, never that it applied the diff that was reviewed. Those
  came apart twice in one day while shipping 1.8.1/1.8.2, when an `update-branch` merge
  commit followed by a squash-merge reverted unrelated merged work; CI stayed green because
  the reverted state was internally consistent, and both were found only by reading the
  day's whole diff against a baseline. The new command asks whether a merge wrote to files
  that another pull request changed **after this one branched** — the only way that revert
  can occur — and exits non-zero naming each file and the PR it collided with. s10 now runs
  it immediately after merging.

  Validated against the actual incident rather than a synthetic: run on #543 it reports
  `drift` and names both of that day's reverts (#550's four source and test files, #546's
  four website files), while today's merges report `clean`. An earlier design that compared
  the PR's file set against the merge's file set reported the incident as **clean** — the
  `update-branch` commit had already pulled the reverting state into the branch, so the
  revert was inside the diff a reviewer reads. Scope comparison cannot see it; it survives
  as a weaker secondary signal.

### Fixed
- **A project's own review rubric now actually reaches its reviewers** (#677):
  `policy_pack.review.additions` and `required_sections` have been in the schema, in the
  docs and in the emitted contract (`review_merge_contract.reviewers.project_additions`)
  all along — and **no adapter prose read either**, so a project that configured them got
  nothing. `projects/example-flutter.yaml` ships with both set, and neither had ever
  reached a reviewer's brief. s7 now passes them verbatim, and `review-cycle.md` references
  the same source rather than restating it. This is the counterpart to #679's stance: the
  stance is project-neutral and says *how* to review, while these name the shapes *this*
  codebase keeps producing — measured to be what makes a reviewer follow a defect from the
  symptom to where it lands, rather than what makes it find more.

### Fixed
- **`keel resume` observes the live state instead of being told it** (#635): the
  `--live-pr-state` / `--live-worktree-state` flags defaulted to `unknown` and core never
  looked, so every ambiguous outcome required the agent to volunteer the damning state — a
  checkpoint pointing at a deleted worktree resumed as `ready / can_resume: true`. keel now
  reads the PR state from `gh`, probes whether the recorded worktree exists, and resolves
  the branch's real head; the flags become an explicit override for offline and fixture use
  and `--no-observe` opts out. An unreadable `gh` yields `unknown`, never `missing` —
  failing to reach GitHub is a fact about the runner, not about the PR.
- **A crash mid-merge is no longer resumable without evidence** (#635): `merge: pending`
  with unknown live state returned `pr-open / can_resume: true / next_step: s10`, which
  would re-attempt a merge that may already have landed. It is now `ambiguous`; live
  evidence either way (`merged` or `open`) still resolves it normally.
- **A branch that moved since the checkpoint now warns** (#635): the checkpoint records a
  head and nothing compared it, so a stale run resumed with stale context silently. A
  mismatch warns rather than blocks — the usual cause is a legitimate push before a crash,
  and the merge gate binds to the live head regardless.

### Changed
- **Optimized model and delegate character validation** (#691): Replaced `all()` generator
  expressions with `frozenset.issuperset(model)` checks in `agents.py` and `api_delegate.py`.
- **Documented what `step-verify` and the checkpoint gate actually prove** (#635).
  `step-verify` is per-step and stateless across steps: `--step s10` passes against a
  well-formed s10 handoff with no s7 or s8 handoff in existence, so *ordering* is enforced
  by adapter prose, not by the command — and `s11`/`s12` ordering is advisory, since
  `closeorder.reconcile` is a post-hoc reader. The merge itself is unaffected, being gated
  separately on head-bound evidence. The covering-checkpoint gate proves "some attempt under
  this run-id reached s10", not "this attempt did", because checkpoints are clock-free and
  `RUN_ID` is stable across attempts. `docs/keel/command-contracts.md` now says so rather
  than implying enforcement it does not perform.
- **A run that failed its gates no longer renders as one still working through them**
  (#636): `keel run-gates` stamped the activity board on *reach* rather than on *pass*, so
  a red gate recorded as `phase: s8, status: running` — carrying no failure signal at all —
  and keel-visual painted it as in-progress indefinitely. The record now carries a
  `verdict` (`pass` / `blocked`), stamped after the verdict exists rather than before, and
  a missing verdict stays `None` because "nothing to report" must not read as a pass.
  `status` keeps its meaning (the run *did* advance and has *not* finished), so the
  never-regress rule is untouched. keel-visual gains a `blocked` step status that outranks
  `gate`/`loop` on the active step, wired through both renderers — the terminal board
  (red `✖`) and the HTML run view — because both recompute step status from position and
  kind rather than reading `steps[].status`, so adding the field alone would have painted
  nothing.

### Added
- **`openai-compatible` delegate profiles — any OpenAI-shaped hosted API from config**
  (#666): OpenRouter, Groq, DeepSeek, Together, LiteLLM and a local vLLM become
  `knobs.delegate_profiles` entries rather than code changes, completing the "every model"
  half of the issue. A profile names an `endpoint` and an `api_key_env`, and inherits the
  no-tools contract, `secrets` scope, retry-×2-then-fall-back and no-retry-on-429 rules of
  the hardcoded hosted vendors. Because this is the first keel delegate whose URL comes
  from configuration, it carries a guard ported from ai-jury rather than reinvented:
  loopback hosts pass freely; any other host — **including cloud-metadata addresses** — is
  a `keel validate` error unless `KEEL_ALLOW_REMOTE_ENDPOINT` is set **in the environment**
  (the threat model is an attacker-influenced config, so the opt-in must sit outside the
  surface an attacker controls); non-`http(s)` schemes are refused, blocking `file://` and
  `ftp://`; and a malformed URL is a clean config error rather than a traceback out of
  `keel validate`. `api_key_env` takes a variable **name** and rejects anything shaped like
  a pasted secret, because profile config is serialised into the command contract and
  hashed into `config_hash` — a key there would be published.
- A profile field belonging to a different vendor (`endpoint` on a `cli` profile,
  `command` on an `openai-compatible` one) is now a validation error rather than a
  silently-ignored key. The schema cannot catch it, since both fields are legal somewhere.

### Added
- **`google-api:MODEL` hosted delegate** (#666): Gemini can now be an implementer or
  reviewer with only `GEMINI_API_KEY` in the environment — no agent CLI. Same no-tools
  contract, `secrets` scope, retry-×2-then-fall-back and no-retry-on-429 rules as the
  existing `anthropic-api:`/`openai-api:` delegates. Two vendor-specific details are
  handled rather than inherited, both verified against the live endpoint: Gemini puts
  the **model in the URL path**, so a model id outside `[A-Za-z0-9._-]` (or containing
  `..`) is refused as `bad-model` before any request instead of being escaped — for this
  vendor a `delegate-model:` label is untrusted input reaching a URL; and it answers an
  invalid key with **HTTP 400**, not 401, which now maps to `auth` so a mistyped key
  does not read as a generic transport error. The key travels as an `x-goog-api-key`
  header, never as a `?key=` query parameter.

### Changed
- **s7 reviewers are now briefed to refute, not to approve** (#679): the adapter told
  reviewers *where* to look (`logic correctness`, `threading`, `test coverage`, …) and never
  *how* — there was no `refute`, `adversarial` or "default to wrong" anywhere in it. A
  reviewer with a topic list and no stance reads a change sympathetically and confirms it,
  which is how a defect ships past a green CI. The brief now carries four rules together:
  refute rather than approve; **a finding you cannot demonstrate is not a finding**; finish
  the trace to where the defect lands, not where you noticed it; and "I checked X, Y and Z
  and found nothing" is a complete review. The last three are the counterweight — an
  aggressive reviewer with no way to report a clean result either goes quiet or invents. The
  stance is project-neutral prose, so it lives in the adapter; naming a project's own
  recurring failure shapes is config, tracked separately.

### Security
- **Dependabot now watches `.github/requirements/`** (#664): the `pip` entry covered only
  the repo root, so the hash-locked release tooling for `publish.yml` was scanned for
  advisories but never got a fix PR — a `setuptools` alert had been sitting open with no
  route to being resolved, and any future advisory against that file would have done the
  same. Also bumps `setuptools` 80.9.0 → 84.0.0, clearing that alert. The new entry is
  deliberately ungrouped: `publish.yml` runs only on a `v*` tag, so a bad pin there
  surfaces during a release rather than in CI, and each bump should be reviewed alone.

### Added
- **`knobs.delegate_profiles` — the generic `cli` delegate vendor** (#659): `--delegate`
  accepted a closed set of vendors, so every new provider was a code change. Installed and
  authenticated CLIs like `cursor-agent` and `gemini` simply could not be used as a keel
  implementer or reviewer. A named profile (`vendor: cli`, `command`, optional `args`,
  `prompt_mode`, `model`, `model_arg`) is now referenced as `--delegate <name>`, turning
  provider support into configuration. `prompt_mode` exists because stdin is not universal: `stdin` stays the
  default (positional-arg passing hangs some CLIs), `arg` is the opt-in for CLIs whose usage
  makes the prompt a positional argument. `args` carries the standing flags a real CLI
  needs (`cursor-agent -p --force`), since `command` is one executable rather than a shell
  line, `review_args` keeps the reviewer role off those write-enabling flags (keel cannot
  enforce read-only on an arbitrary binary, so this is the operator's lever and s7 says so
  plainly rather than promising what it cannot keep), and `model_arg` (default `--model`) says how the effective model reaches it —
  arbitrary CLIs share no model-selection syntax, so without it the documented precedence
  (per-run `--delegate <name>:<model>` > profile `model` > CLI default) would be
  unimplementable and attribution would report a model that was never selected. Name resolution is **fail-closed** — profiles
  resolve after the built-in vendors, and a profile that shadows `claude`/`codex`/`agy`/
  `ollama`/`anthropic-api`/`openai-api` is a `keel validate` error, never a silent override.
  A `cli` delegate inherits the local-model contract exactly: no tools, retry ×2 then fall
  back to the host agent, and **refused on tier-3**. Attribution records `agent:cli` plus the
  effective model and the profile name under `delegate_profile` — not `profile`, which the
  run record already uses for the workflow profile — so the closure says which CLI ran. Design (including
  the deliberately deferred `openai-compatible` / `google-api` vendors):
  `docs/proposals/generic-delegate-vendors.md`.

## [1.11.0] — 2026-07-28

Most of what follows is one defect in different clothes: **a value meaning "we could not
observe this" was spelled the same as the value meaning "we observed nothing wrong."** Every
instance failed in the safe-looking direction, which is why the suite stayed green over all
of them — and why several were found only by mutating the code and watching nothing die.

### Added
- **`knobs.docs_only_allowlist` now does something** (#632): it was declared in the schema,
  parsed into `Knobs`, folded into `config_hash` and echoed into the adapter contract —
  and read by nothing. An operator who set it got silence. It now widens the *risk-tier*
  judgement: listed paths may ride along in a docs change without forcing code-risk
  classification. Deliberately narrower than `docs_gate_paths` — an allowlisted path is
  **not** scope-creep-exempt and does **not** buy the empty-CI-check carve-out, because a
  generated site file riding along with a docs edit is exactly when a workflow should have
  run. `classify.tier_for_files` takes `allowlist_globs`; the carve-out asks the new
  `classify.is_docs_only` directly instead of inferring it from `tier == 1`.
  `docs/keel/configuration.md` now states the difference as a table.

- **`knobs.jury_timeout_s`** (integer ≥ 1, default `600`) sets the jury built-in's
  wall-clock budget, previously hardcoded and unreachable from config — #622's
  `gate_timeout_s` covers command gates and never applied to it. Kept separate on purpose:
  the jury is a cross-vendor agent CLI, not a project test command, so a panel that needs
  an hour should not force every test gate to wait an hour too. `plan_gates` now resolves
  it onto the jury `GateSpec`, so every gate that shells out has its budget decided in one
  place and visible in the plan contract.

- **`keel ship --gate-result <id>=pass|fail`** records the verdict of a gate keel cannot
  execute — an `agentic` gate, dispatched by the agent rather than by the command-only
  runner. Repeatable. This is the channel the `not_run` refusal below needs: without it a
  blocking agentic gate is `NOT-RUN` forever, `record_gates_passed` never certifies the
  run, and `keel merge` refuses every head — a permanent merge block rather than a gate.
  Found by review of this changeset, against a project config the schema and
  `docs/keel/extensions.md` both permit.

### Fixed
- **The release bump now covers every site surface, and cannot step over a stale one.**
  Two separate gaps, with one symptom. `website/docs.html`, `coverage.html` and
  `content.js` were **never registered with the tooling at all** — the runbook named them as
  a manual step instead, and the manual step was missed, leaving them at `v1.6.5` for four
  releases and then `v1.8.2` for three more. `website/index.html` *was* registered, but by
  searching for the version currently in `pyproject.toml`; had it ever drifted it would have
  matched neither the old nor the new string and been skipped forever, so the one file that
  was wired up was wired up fragilely. All four are now matched by *shape*, which registers
  the three missing ones and makes the literal-match fragility moot in the same stroke.
  `tests/test_release_docs.py` derives its list from the script's own table and fails if any
  surface falls behind; `docs/keel/release.md` drops the manual step and documents the repair
  path (`make release-bump VERSION=<current>`).

- **Re-running the ledger append no longer bricks the session** (found in review): the
  append was unconditional, so the natural retry after a crash mid-s11 wrote a *second*
  capture marker for the PR. "Exactly one marker per merged PR" was enforced nowhere at
  write time and only detected afterwards — `capture-verify` then refuses the whole
  session and `capture-reconcile` returns `blocked` with no actions, leaving hand-editing
  `run-ledger.jsonl` as the only exit. `keel ship --append-ledger` now checks the records
  it already holds (`ledger.existing_capture_marker`) and no-ops with a message naming the
  run that owns the existing marker.

- **Ship artifact comments are actually idempotent now** (found in review): `post-comment`
  matches an existing comment on marker **and** run-id, but no ship renderer emitted a
  run-id in a form the matcher recognised — the closure comment writes
  `- **Run id:** <id>`, the review and jury verdicts write none — so every resume posted a
  duplicate rather than editing. For the closure comment it was worse than an oversight: it
  was impossible, because `evidence-verify` compares the posted body against the canonical
  render, so adding the marker to the body would have failed closure fidelity. The marker
  is now stamped by the transport (`_with_run_id_marker`) and stripped by
  `evidence._normalize_closure_body` before comparison, so a body can be both idempotent
  and verbatim.

- **A checkpoint claiming `merged` no longer overrides live evidence to the contrary**
  (found in review): `resume_plan_as_dict` checked `state.merge == "merged"` before any
  ambiguity branch, so a checkpoint written optimistically *before* the merge landed sent
  every later resume straight to capture and close — closing the issue and flipping the
  status label for a merge that never happened. `closeorder` cannot catch it either; it
  attests the merge *decision*, not the merge. A `merged` checkpoint contradicted by a live
  PR state of `open`/`closed`/`missing` is now `ambiguous`. `unknown` (the default when the
  adapter volunteers nothing) is absence of evidence and leaves the jump intact.

- **`--gate-result` cannot override a gate keel executed** (found in re-review): the flag
  applied a recorded verdict to *any* outcome, so a gate keel ran and observed failing
  could be flipped to certifying — the same fail-open this series exists to close,
  arriving from the other direction. Reproduced with a `warn`-severity command gate whose
  `run:` genuinely fails: `record_gates_passed` went False → **True** under
  `--gate-result <id>=pass`, and that predicate is what authorizes `keel merge`. A recorded
  result now applies only to a `not_run` outcome, and naming an executed gate is refused
  loudly rather than silently discarded.

- **The closure comment tells the truth about an unreadable diff** (found in re-review):
  `render_closure_comment`'s defensive coercions absorbed the new `None`s, so the artifact
  actually posted to the PR still said `Changed files: 0` and `Docs touched: no` — an
  affirmative claim about a diff nobody could read, in the one place a human looks. It now
  renders `unreadable (git diff failed)` and `unknown`.

- **The assessment no longer says "clear to merge" about a run it cannot certify**
  (found in review): a `NOT-RUN` blocking gate made `record_gates_passed` refuse the
  record, but `ship.assess` did not know about it — so `keel ship` printed
  `decision: MERGE — clear to merge` immediately below `gate <id> NOT-RUN`, and
  `keel merge` then refused with nothing the operator could connect it to. `decide_merge`
  now takes the unrun blocking gates and blocks, naming them and the flag that satisfies
  them.

- **An unreadable diff is unreadable in the machine-readable surfaces too** (found in
  review): the fail-closed classification reached the console line and the tier, but
  `keel ship --json`, the ledger record, and the closure comment rendered from it all
  still said "0 changed files" — so a downstream consumer could not tell "we could not
  read the diff" from "the diff was empty", and the record claimed TIER-3 with zero files.
  The counts are now `null` with an explicit `unreadable` flag, and the rendered PR body
  says the list could not be read.

- **The closure-fidelity strip cannot launder content** (found in review): the run-id
  marker was stripped with a permissive `.*?`, and an HTML comment ends at its first
  `-->` — so a trusted author could append
  `<!-- keel.run-id: r1 --> **NOT ACTUALLY MERGED** -->`, have the whole line normalize
  away, and still render contradicting text on the page. The pattern now matches only the
  exact form the transport emits.

- **`record_gates_passed` fails closed on any `on_fail` it does not recognise** (found in
  review): a gate carrying `not_run: true` with no `on_fail` key certified as passed,
  because the strict default was applied only where keel *wrote* the record. A missing key,
  a JSON-round-tripped `None`, and an unknown severity name all mean "we cannot tell this
  was optional", and this is the certification path — only an explicit `warn`/`suggest`
  now clears it.

- **`capture-verify` refuses to certify when its transport failed** (#630): the command
  computed `transport_failed`, recorded it in the payload, and ignored it. A `gh` hiccup
  empties the *derived* merged-PR set, so the union degenerates to exactly the list the
  agent supplied and the anti-shrink defence the command exists to provide silently
  evaporates — reported as `complete`, exit 0, with an un-captured PR simply absent from the
  accounting. It is now a distinct `transport-unavailable` status with `certified: false`
  and a non-zero exit: an audit that could not observe must say so.

- **An unreadable lock owner no longer disables the ownership guard** (#631):
  `lock._holder` returned `None` for "nobody holds this", "`owner.json` is corrupt",
  "`owner.json` is missing" and "the read failed" alike, and the guard was written so a
  `None` holder made the check *vanish* rather than fail closed — letting a second run
  release a live merge claim and take the lock. Since every caller reaches `_holder` only
  with the claim directory present, there is no "unheld" answer to give: an unreadable owner
  is now `UNKNOWN_HOLDER` and refuses a named release, while `owner=None` stays the
  deliberate any-owner escape for clearing a stuck claim. The window needs no disk
  corruption — `_claim_path` creates the directory before it writes the owner file, so any
  crash in between leaves the lock held but ownerless.

- **`git` warnings on stderr no longer corrupt every parsed value** (#629): `run_argv`
  returned only `stdout + stderr` concatenated, and every `git` wrapper parsed *that*. git
  routinely warns while exiting 0 — an ambiguous refname (a tag and a branch sharing a
  name) prints `warning: refname '<x>' is ambiguous.` and still succeeds — so
  `rev_parse`/`merge_base` returned `"warning: …\n<sha>"` as a SHA, `changed_files`
  invented a phantom path from the warning line, and `diff` handed the review gate a patch
  with log noise prepended. `CommandResult` now carries `stdout` and `stderr` separately
  (`output` is unchanged, for the diagnostic uses that genuinely want both) and every
  parser reads `stdout` alone. `rev_parse`/`merge_base` additionally validate the object-name
  shape, so a stray token can never pose as a SHA even if a stream is ever re-crossed.

- **An unreadable diff no longer reads as an empty one** (#628): `git.changed_files` and
  `git.diff` collapsed failure to `[]`/`""`, which is exactly what "nothing changed" looks
  like. Three consumers drew the wrong conclusion from it, all in the safe-looking
  direction:
  - the jury gate treated "could not read the diff" as "nothing to review" and passed;
  - `keel ship` classified the change at the default TIER-2, quietly dropping a reviewer
    and turning the gating jury off;
  - and the evidence gate's docs-only carve-out counted an empty list as docs-only.

  Both wrappers now return `None` on failure, distinct from the empty value. `ship.assess`
  takes `changed_files: list[str] | None` and classifies `None` at the new
  `classify.UNKNOWN_TIER` (3) fail-closed; `jury.run_gate` raises a blocking
  `jury:unreadable-diff` finding in gating mode; and `keel ship` prints
  `changed files : UNREADABLE` so a forced tier is never mistaken for a measured one.

- **A gate nobody ran can no longer certify a merge** (#626): the command-only runner
  returns `(True, [])` for an `agentic` gate — it does not execute those, the agent-dispatch
  layer does — and that pass was recorded in the run ledger indistinguishably from a gate
  that ran clean. `record_gates_passed` then read it as a pass, so a **blocking** review
  gate that was never dispatched authorized the merge at `keel merge`'s SHA-stamped gates
  check. Gate outcomes now carry `not_run` and the declared `on_fail`; a blocking gate
  flagged `not_run` refuses to certify, advisory gates are unaffected, and ledger records
  written before these fields existed still read as passes. The operator-facing label is
  `NOT-RUN`, never `ok`.

- **An empty CI check set is no longer a pass** (#627): `_ci_rollup_state` returned
  `state: "pass"` for an empty `statusCheckRollup`, differing from a real pass only in a
  `reason` field nothing consumed — so a PR on which no workflow ever ran merged as green.
  It is now its own `no-checks` state, and `keel merge` applies the documented docs-only
  carve-out in core rather than in adapter prose: an empty check set passes only when every
  changed path is a docs path, and blocks otherwise. An unreadable or empty changed-file
  list is deliberately not docs-only.

- **A superseded green gates run no longer authorizes a merge** (found in review):
  `ledger.gates_pass_for_head` scanned for *any* passing ship_run record against the current
  head, so re-gating the same commit — a flaky suite settling, a fix-loop re-running — left
  the earlier green in place and the later red was never consulted. It is now latest-wins:
  only the most recent record for that head counts.

- **One contract shipped two different dedupe thresholds** (#633): `contracts` embeds a
  `work_creation_policy` block beside a `dedupe` block that already honours
  `policy_pack.scan.near_text_similarity`, but `workcreation.contract_as_dict()` hardcoded
  the default. The two keys — near-identical, in the same dict — agreed only while a
  project set the knob to exactly the built-in `0.6`. One resolver now feeds every copy.

- **The jury gate never actually read ai-jury's findings** (#624, found in review):
  `keel.runner.run_argv` returns `stdout + stderr` concatenated and ai-jury logs its
  progress (`[jury] …`) to stderr, so the combined text was never valid JSON and a strict
  `json.loads` discarded **every** finding. Against the real CLI a 6-finding report parsed
  as zero. `parse_report` now uses `raw_decode`, which reads the leading JSON value and
  ignores the trailing log lines; the same 6 findings now survive, anchors included.

- **A jury run that produced no verdict no longer passes the gate** (#624):
  `jury.run_gate` never consulted `result.ok`, and `parse_findings` returns `[]` for
  unparseable output — so `blocked` came out False and a hung, crashed, or (per the item
  above) simply unreadable panel reported `(True, [])`. The jury silently dropped out of
  the merge decision. This is the inverse of #622 and strictly worse: there the gate
  stayed red and only the label was wrong; here it went green on a run that produced no
  review at all.

  A run that yields no verdict is now handled like an oversize diff — blocking `major` in
  `gating` mode, `minor` in `advisory` — carrying a `jury:incomplete-run` finding naming
  the timeout limit or the exit code. The condition is *"did we parse a verdict"*, not
  *"was the exit code zero"*: ai-jury exits nonzero to signal "request changes", which is a
  completed review whose findings are honoured, while a zero exit carrying unreadable
  output is not a review. An absent `jury` CLI remains a clean no-op — keel does not
  depend on ai-jury. A jury killed by its limit also carries `timed_out`, so it renders as
  `TIMEOUT` rather than `FAIL`, consistent with #622.

### Tests
- **Five config wires that no test could break** (#633), found by a mutation sweep — the
  production code was correct in each case, but the wire could have been deleted or set to
  the wrong value with the whole suite staying green. That is exactly how #623 and #625
  reached `main` at 100 % line+branch coverage.
  - **The merge window**, keel's central safety gate, was unassertable in `keel ship` and
    `keel window`: every mutation of the config→`ship.assess` wire was green, and so was
    `is_open = True`. `keel window` asserted only that the words "merge window" and the
    timezone appeared, never OPEN vs CLOSED. Now pinned by spying on the window predicate
    rather than adding a `now` seam to production — an env-settable clock would be a way to
    walk a merge through a closed window.
  - **`extensions_dir`** was never exercised at a non-default value anywhere. `load_extensions`
    is fail-soft, so a directory that stopped being honoured would yield zero extensions and
    a green run: a silently gate-less pipeline.
  - **`evidence_gate_label` and `evidence_require_distinct_vendors`** were only ever driven
    through their CLI *flags*; the `or config.knobs.…` half never contributed in any test, so
    an operator's config override was unproven.
  - **`policy_pack.scan.large_diff_max_bytes`** had no assertion at all; its two siblings were
    asserted at values indistinguishable from the fallback.
  - **`gate_timeout_s`**'s two CLI call sites. `plan_gates` makes the runner fallback
    unreachable, so this is defence in depth rather than dead code — pinned so the redundancy
    cannot quietly become wrong.

## [1.10.0] — 2026-07-27

### Added
- **The jury downgrade works unattended** (#613): #611 made the jury mode a function of the
  panel that ran, but nothing computed the count, so `--jury-vendors` was operator-supplied
  only. The jury verdict now declares `vendors: <N>` (`render_jury_verdict`, inferred from
  `participants` when not passed), and `evidence-verify` reads it from a trusted, head-bound
  verdict when the flag is omitted. This is the only channel a hosted runner has: the run
  ledger and the jury artifact both live under the gitignored `.keel/state/`, so CI can read
  neither, while PR comments are always visible. An undeclared count leaves the mode alone —
  only a verdict that states the panel size may relax the gate.

### Changed
- **The ship adapter describes the jury downgrade instead of instructing it** (#612): the
  `s8` prose still told the agent to "count distinct participating vendors" and perform the
  sub-2-vendor downgrade itself, which #611 moved into `ship.resolve_jury()`. It now states
  that core resolves the effective mode and the agent's job is to *report* the participating
  count via `evidence-verify --jury-vendors`, with an explicit "do not re-derive or override
  that downgrade". Regenerated into the plugin `commands/`, `.claude/commands/keel/` and
  `.agents/skills/keel-ship/`; `docs/keel/parameter-reference.md` carries an independent copy
  of the same sentence and was updated alongside.

### Fixed
- **The jury gates on the panel that ran, not on the tier alone** (#610): a tier-3 PR
  required a posted gating `jury-verdict` regardless of whether a gating panel could be
  assembled, while the contract's own "a sub-2-vendor panel is downgraded to advisory" rule
  lived only in adapter prose — `minimum_vendors` was written in `resolve_jury()` and read
  nowhere. `resolve_jury()` now takes `participating_vendors` and downgrades
  `gating → advisory` below `MINIMUM_JURY_VENDORS`, so the evidence gate (which reads
  `jury.mode`) stops demanding a verdict the jury step would decline to treat as gating.
  A run where no agent returned output is simply zero vendors, so "a jury that did not
  complete cleanly never gates" needs no separate branch. Surfaced via
  `evidence-verify --jury-vendors N`; omitting it leaves today's behaviour unchanged.
- **Evidence requirements are split by phase, and the merge gate stops demanding a
  post-merge artifact** (#608): `evidence.required_items()` required the two closure
  comments whenever the gate was armed, but s11 posts those *after* the s10 merge the gate
  authorizes — so a run following the backbone step order could never merge. Items now
  declare a `phase` (`pre-merge` for review/jury verdicts, `post-merge` for closure),
  mirroring the mapping `stepverifier` already applies, and `evidence-verify` takes
  `--phase {pre-merge,post-merge,all}` (default `all`, so existing callers are unchanged).
  The committed `keel-ship.yml` gate now runs `--phase pre-merge`.
- **An unarmed evidence gate can no longer report success** (#608): with no ship provenance
  the gate derives no requirements and passed having checked nothing, indistinguishable
  from a genuine pass. New `evidence-verify --require-armed` turns that into a blocking
  `gate-unarmed` finding; the operator waiver label still disarms deliberately and passes.
  The `evidence` job in `keel-ship.yml` also gains `needs: ship`, because arming falls
  through to the ship-assessment comment for any branch outside `_SHIP_BRANCH_RE` and the
  two jobs previously raced.

### Added
- **Display settings popover is reachable again** (#606): the `[data-motion="max"]` animation
  rules in `styles.css` had no UI — `wireSeg()` queried a `.seg[data-seg]` control that no
  page rendered, so the only way to reach them was editing `localStorage` by hand. The
  titlebar gains a display button whose popover exposes the Motion segment, wired with
  correct single-select semantics: `role="radiogroup"` + `role="radio"`/`aria-checked`
  (not `aria-pressed`), roving tabindex, Arrow/Home/End, `aria-expanded` on the trigger,
  and focus returned to it on `Escape`.

### Removed
- **Dead `data-type` plumbing** (#606): every page set `data-type` on `<html>` from
  `localStorage`, but no stylesheet has ever read it. Dropped from all four pages.
- **`plan.md` is no longer tracked** (#606): a leftover agent scratch plan at the repo root.
  Because it was in version control, every agent run that rewrote its plan landed as a
  source change inside an unrelated PR. Untracked and gitignored.

### Fixed
- **Command rail is a complete ARIA tabs pattern** (#604): the website's showcase rail
  drives a detail panel but announced itself as 16 independent toggle buttons via
  `aria-pressed`. It now carries the whole pattern — roving tabindex (one Tab stop instead
  of sixteen), Arrow/Home/End navigation on both axes with wraparound, `aria-selected`, and
  `#show-detail` as a `tabpanel` wired both ways via `aria-controls`/`aria-labelledby`. The
  interleaved group headings become `role="presentation"` (a tablist may only own tabs) and
  their text moves into each tab's accessible name so the grouping survives.

## [1.9.0] — 2026-07-17

### Added
- **Codex plugin** alongside the existing Claude Code plugin (#565): keel installs as a
  Codex plugin (`.codex-plugin/`) so `/keel:<command>` works natively there too; the
  release bumper and a version-lockstep test keep its manifest in sync with the package.
- **Jury artifact saved for visualizers** (#576/#579): when the s8 jury gate runs, ship
  writes the machine-readable report to `.keel/state/jury/<run-id>.json` (fail-soft,
  state-only) so keel-visual can show the actual verdict, not just the jury mode.
- **Hosted-API implementer/reviewer delegates** (`--delegate anthropic-api:MODEL` /
  `openai-api:MODEL`, #548): drive the s4 implement and s7 review steps with only a vendor
  API key in the environment (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`) — no agent CLI
  installed. Same no-tools contract as the local-model path (the orchestrator owns every
  git/PR step; the delegate produces a diff or verdict via one stdlib HTTP call), same
  tier-3 refusal, gated by the `secrets` consent scope, never retried on HTTP 429. New
  thin I/O wrapper `keel.api_delegate` and a new `api-token` runtime capability. Design:
  `docs/proposals/api-token-delegate.md`.

## [1.8.2] — 2026-07-11

### Fixed
- **The 1.8.1 package did not actually contain the statusCheckRollup dedup fix its own
  changelog claimed.** A squash-merge of an unrelated docs-only PR (#543), whose branch had
  been updated from `main` via the GitHub "update branch" API shortly before merging, computed
  its diff against a stale pre-#550 base — silently reverting `_dedupe_rollup`/
  `_rollup_recency` in `cli.py` and the matching `jq` dedup filter in `github.py` (plus their
  tests) back to pre-fix state immediately after #550 merged, before the 1.8.1 tag was cut. The
  revert was invisible locally because the reverted state was internally consistent (old code,
  no tests for the removed behavior), so `make test`/`make coverage` stayed green throughout.
  Caught by an `ai-jury` cross-vendor review (claude + codex) of the day's merged changes,
  which flagged the changelog/source mismatch; confirmed by bisecting the merge history and
  restored by cherry-picking #550's original commit onto `main`. (#553)
- **A Google Analytics snippet reintroduced by the same squash-merge issue.** #546 removed the
  placeholder GA4 script from all four website HTML pages ("Cloudflare beacon already live").
  A later squash-merge (#540, adding CSP headers to those same pages) reintroduced it as a
  side effect of the same stale-base diff problem. Removed again. Also flagged by the `ai-jury`
  review, independently, before the #550 issue was found. (#552)

## [1.8.1] — 2026-07-10

### Fixed
- **Stale CI check runs no longer confuse merge-readiness.** `statusCheckRollup` keeps every
  historical run of a check, not just the latest — a check that failed once and was later
  rerun to green still carried its old `FAILURE` conclusion in the raw list. `ci_conclusion`
  (`github.py`) and `_ci_rollup_state` (`cli.py`) now dedupe by check identity (`context` for
  legacy statuses, `name` for check runs) and keep only the most recent entry per check before
  evaluating conclusions, so a superseded failure can't block a merge and a stale success can't
  mask a genuine later failure. (#550)
- **Blocked-issue detection missed blocker phrases split across a line break.** `_is_blocked`'s
  fast-path early return matched a compiled blocker regex against raw issue text, but the
  sentence-splitting it short-circuits normalizes newlines to spaces first. A multi-word
  blocker phrase (e.g. "blocked by", "depends on") wrapped across a line — common in
  hard-wrapped markdown — was silently missed, misclassifying a genuinely `BLOCKED` issue as
  ready to mutate. The fast path now matches against the same normalized text. (#545)

### Changed
- **CI-state and check-list validation now use `frozenset.issuperset()`** instead of a
  generator `all(...)`/`any(...)` scan (`ci_passing` in `ship.py`, adapter status checks in
  `install.py`) — no behavior change, faster on repeated validation. (#539, #542)

### Security
- **CSP headers on the docs website.** `website/index.html`, `docs.html`, `coverage.html`, and
  `404.html` now ship a `Content-Security-Policy` header restricting script/style/font/connect
  sources to `'self'` plus the specific third-party origins actually used (Google Fonts,
  Cloudflare Web Analytics), reducing the site's exposure to injected-script XSS. (#540)

## [1.8.0] — 2026-07-03

### Added
- **Deterministic Step-0 activity stamp at multi-issue dispatch.** A multi-issue `/keel:ship`
  or `/keel:work-block` now stamps every selected issue's canonical `ship-<N>` run at `s0` up
  front (in the parent, at dispatch), so each run shows on the `keel-visual` board immediately
  rather than only after a child agent happens to call `keel activity`. Each child's later
  `keel plan`/`run-gates`/`merge --run-id ship-<N>` advances the same board row; a child that
  never stamps still shows as `s0` instead of vanishing. Adapter-level change (`ship.md` s1
  select, `work-block.md` Step 1); fail-soft on keel < 1.6.0. (#501)

### Fixed
- **DoS via unbounded PyPI payload read.** `_fetch_latest_pypi_version` read the entire HTTP
  response body with no size cap; a misbehaving or maliciously oversized response could exhaust
  memory. `response.read()` is now capped at 50MB. (#515)

### Changed
- **Faster, allocation-light deduplication.** `_dedupe_ints` (`cli.py`) and `_added`
  (`dryrunverify.py`) now use `dict.fromkeys()` instead of a manual seen-set loop — same
  order-preserving behavior, fewer allocations. (#526)
- **`_aggregate_clean_areas` (`artifacts.py`) dedup rewritten with `dict.fromkeys()`** for the
  same O(n) win. (#524)
- **`Rule.matches` (`guard.py`) label matching now uses a cached `frozenset` + `isdisjoint()`**
  instead of a per-call generator/`any()` scan — no behavior change, faster on repeated rule
  evaluation. (#527)
- **`_is_doc` (`closure.py`) now uses a native `in` check** instead of an `any(...)` generator
  expression for the same result. (#509)

## [1.7.0] — 2026-06-19

### Added
- **`keel gc` — reclaim disposable runtime artifacts.** A single, auditable entry point that
  empties `.keel/scratch` and prunes `.keel/activity` (count-based retention, newest
  `--keep-activity` kept; default 50). It is **fail-soft** (a failure on one tree degrades to
  a no-op and the other still runs) and **never** touches the durable run ledger, checkpoint,
  or locks. Supports `--dry-run`, `--no-scratch`/`--no-activity`, and `--json`. The
  `/keel:ship` adapter runs it at the end of every run so scratch/activity no longer
  accumulate (#479).
- **Deterministic report renderers for the remaining commands.** Pure, host-neutral
  renderers for the review-cycle/pr-loop findings summary, the coverage/deps-audit/flake-audit
  reports, and the regression/review-all-day/triage outputs, posted as rendered markdown
  verbatim instead of hand-written prose (#480, #481, #482, #484).

### Fixed
- **Generated workflow artifacts stay out of consumer repo roots.** keel scaffolds a
  committed `.keel/.gitignore` (ignoring `state/`, `activity/`, `scratch/`, `*.tmp`) on
  `init`/`setup` and self-heals it on the first runtime write of any existing install, and
  exposes `.keel/scratch` via `keel scratch-dir` as the sanctioned home for transient
  artifacts — so a consumer checkout no longer accumulates `plan.json`, `pr_<n>.diff`,
  `issue.md` and friends at its root (#473).

## [1.6.5] — 2026-06-16

### Added
- **A real merge stamps the activity board `merged`, distinct from a soft `done`.** The
  new terminal `merged` status joins `running`/`done` in `keel.activity.STATUSES`, and
  `keel merge` auto-stamps it (s10) once the merge actually lands — so the board shows a
  confirmed green **merged** for runs that merged, not the muted **done** it uses for a
  command that merely closed out (a deferred-window ship, a non-merging `morning`/`triage`).
  `_autostamp` gained a `status` keyword and treats `merged` as terminal: a later stamp
  (e.g. a re-run's start phase) never overwrites a landed run.

## [1.6.4] — 2026-06-16

### Added
- **The backbone auto-stamps the activity board — runs show up *and advance* with no
  agent dependence.** Real `keel ship` runs were going invisible on `keel-visual`'s board
  because the agent orchestrates the backbone but reliably skips the per-phase
  `keel activity` calls (and a project without checkpoint config writes no checkpoint
  either). Now the commands the backbone *always* runs do the stamping themselves when
  given a `--run-id`: **`keel plan`** (Step 0 → first phase), **`keel run-gates`** (the s8
  test gate), and **`keel merge`** (s10). So a run appears the moment it plans and then
  advances **start → test → merge** without the agent. Fail-soft (no run-id / unknown
  command / unknown phase / write error / pre-activity core is a no-op, never an aborted
  command) and it never moves a run backward. `plan` and `run-gates` gained
  `--run-id`/`--issue`/`--pull-request` (run-gates also `--command`/`--phase`); `merge`
  already had `--run-id`. The `ship` adapter's Step 0 plan, s8 run-gates, and s10 merge
  calls now pass these; the per-phase `keel activity` calls still fill in the middle steps.

### Changed
- **`ship` now stamps the live activity channel too.** Ship was the only command
  whose adapter didn't record `keel activity` as it ran — it relied on the richer
  `keel checkpoint`/ledger records, which an agent that orchestrates the backbone by
  hand often never writes (and which vanish when the merged run's worktree is
  removed). So agent-driven ship runs never appeared on the `keel-visual` board. The
  ship adapter now stamps `keel activity --command ship --phase s0…s12` as it advances
  (using the same `--run-id` as the checkpoint), exactly like the other 15 commands,
  so every ship run shows live. keel-visual de-duplicates the activity record against
  the checkpoint by run-id, preferring the checkpoint's detail.
- **`ship` adapter hardened against review-less merges.** The s10 merge step now opens
  with an explicit, mandatory `keel evidence-verify` self-check that runs on *every*
  merge path (including a raw `gh`/REST merge) and tells the agent to **STOP, not
  merge**, when the s7 review verdict is not posted on the PR for the current head. s7
  now forbids carrying a review forward across runs/sessions and forbids closure
  attributions for verdicts not actually posted on the PR (the exact gap that let a
  `keel:ship` run merge with the review step skipped and a fabricated "reviewed in a
  prior session" closure line).

### Changed
- **`keel activity` emission is now a required, up-front adapter step.** In 1.6.0/1.6.1
  the "Live progress" stamping sat in a *best-effort* footer at the end of each stepped
  command's adapter, so agents routinely skipped it and non-ship runs never reached
  keel-visual's board. Relocate it to the top of every stepped adapter (right after the
  `# /keel:<command>` title) and reframe it as a contractual step: stamp the first phase
  **before the work**, re-stamp as you advance, `--done` at the end. The only allowed skip
  is a core too old to ship `keel activity`.

## [1.6.1] — 2026-06-15

### Fixed
- **`keel activity` adapter emission was a no-op.** The "Live progress" block added
  to every stepped command in 1.6.0 invoked `keel activity … --command … --phase …`
  **without `--write`**, so it only *read* the channel and never recorded the run —
  non-ship commands stayed invisible on keel-visual's board. Add the missing
  `--write` to the emission line in all 15 stepped adapters.

## [1.6.0] — 2026-06-15

### Added
- **Live board for non-ship commands — `keel activity`.** A new additive,
  checkpoint-free channel: per-run JSON records under `.keel/activity/` that any
  command's adapter stamps as it moves through its own `keel.flows` phases. The
  `keel activity` CLI (`--write`/`--done`/`--clear`/read) writes them; records are
  keyed by `run_id` so concurrent commands never clobber one another, and `phase`
  is validated against the command's flow. It never touches the resumable ship
  checkpoint contract (#437). Every stepped non-ship command adapter now emits it
  best-effort, so `keel-visual` shows triage / morning / pr-loop / … live on the
  board with their own phases (#439).

### Changed
- **CI now exercises the `keel-visual` companion too.** A `test-visual` job runs the
  keel-visual suite (with its own 100% coverage gate) and ruff across the full
  ubuntu/macos/windows × Python 3.11–3.13 matrix; previously only the core `tests/`
  ran in CI.

## [1.5.0] — 2026-06-15

### Added
- **Windows support, proven in CI.** The test matrix adds `windows-latest` across Python
  3.11–3.13. The merge-window logic uses the stdlib `zoneinfo`, which has no IANA database
  on Windows, so `tzdata` is now a Windows-only runtime dependency
  (`tzdata; sys_platform == 'win32'`) — Linux/macOS stay at the single PyYAML dependency.
  Config validation runs on Windows under bash so the `projects/*.yaml` glob expands; the
  make-based dogfood gate step is skipped there since `make` is unavailable on the runner.
  Two tests were made cross-OS: the state-path rejection tests build an OS-absolute path
  (a leading-slash path is not absolute on Windows), and the POSIX-shell codex deny-hook
  execution test is skipped on Windows.
- **PR description lint.** A new `pr-lint` workflow rejects PRs whose body is empty,
  left as the template, or missing an issue reference — enforcing a real **Summary**
  plus a **Related issues** line (`Closes #N` / `Relates to #N` / `no issue`). The PR
  template gains a dedicated **Related issues** section and `CONTRIBUTING.md` documents
  the rule. The check reads the PR body from the event payload via `env:` (no shell
  interpolation of untrusted PR text).

## [1.4.0] — 2026-06-15

### Added
- **Live jury mode in the run checkpoint.** `keel checkpoint` records
  `state.jury_mode` (off/advisory/gating) so an observer can read the jury's
  *live* mode mid-run from the checkpoint, not only post-run from the ledger
  `run_context`. `keel-visual --follow` uses this to surface the jury as it
  resolves (#397).

### Security
- **`urllib.request.urlopen` restricted to safe schemes.** Guard against
  `file:`/other non-HTTP(S) schemes reaching `urlopen`, closing a MEDIUM
  scheme-confusion vector (#393).

### Changed
- **Faster unique-collection helpers.** Replaced O(N²) membership scans with
  set-backed dedup in the collection utilities (#396).

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
