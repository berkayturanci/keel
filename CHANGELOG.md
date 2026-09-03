# Changelog

All notable changes to keel are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); keel adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **The jury is the tier-3 review panel: one dispatch, mapped onto the s7 verdicts** (#1015): s7 (N reviewers) and s8 (the `jury` gate) were separate mechanisms and `evidence.required_items` demanded *both* at tier-3 — three distinct reviewer verdicts **and** a jury verdict. A tier-3 pull request therefore paid for three host reviewers plus a four-agent panel reading the same diff, while the panel's per-reviewer ballots (which *are* the cross-vendor review) reached no gate at all: ai-jury posted one consensus comment and kept the ballots in its JSON report. On a tier whose `knobs.team` policy is `review: jury`, s7 now dispatches the panel **once** and its ballots become the review evidence.
  - **`keel review --from-jury <report.json>`** reads an ai-jury JSON report's per-reviewer ballots (`jury --format json`, report schema 1.1+) and posts one head-pinned `keel.review-verdict.v1` per panelist — carrying the `vendor:` and `model:` that actually produced that ballot — plus the panel's `keel.jury-verdict.v1` consensus record, in the same call, so both are pinned to the same head SHA by construction. `--reviews` and `--from-jury` are mutually exclusive: the bundle is the host's or the panel's, never both. A report carrying no ballots is refused with the command that produces one, rather than posting a thinner review.
  - **`scope` and `testing` are derived from the ballot itself** — the files it named, and what ai-jury's verification round upheld — because the JSON report carries no per-ballot prose. They are written to satisfy `evidence.verdict_substance` *by construction*, so a clean ballot is still a postable verdict rather than a receipt the gate refuses.
  - **The findings flow into s9 unchanged in meaning.** `keel review --from-jury --json` returns a `panel` block: the ballots, the distinct vendors, and the panel's **verified** consensus findings already mapped to keel severities and decisions (`critical`/`major` ⇒ `block`). Only what the verification round upheld gates a merge — an unsupported claim is reported, never merged against.
  - **The panel sizes its own bench, and `min_vendors` is the floor under it.** `evidence.required_items` derives the reviewer count from the panel: the posted jury verdict declares `panelists: <N>` beside `vendors: <N>`, and `evidence.jury_panel_size()` reads it back — the same channel, for the same reason (the ledger and the jury artifact live under the gitignored `.keel/state/`, unreadable from a hosted runner; PR comments are always visible). The required count is `max(declared, jury.min_vendors)`, so a declared count may only ever **raise** it: an unmeasured panel cannot pass by being unmeasured, and a verdict declaring a short panel cannot shrink what the tier owes — the one shape that means "the panel came back short" must not be the shape that relaxes the gate. The separate `jury-verdict` requirement stays, as the consensus record. `plan`, `ship` and `step-verify` publish that floor while `review`/`evidence-verify`/`merge` require the panel that actually sat — convergent, never contradictory, and pinned in `tests/test_review_contract_agreement.py`. `plan` is offline by construction and has no pull request to read a verdict from; `ship --pr N` does and could read the count beside its CI reads, but deliberately does not, because the floor is provably conservative (the count only ever rises, so a planning surface can under-state what will be required and never over-state it) and because `ship` without `--pr`, and every dry run, must resolve the same contract with no verdict in reach anyway.
  - **Vendor distinctness asks a panel the panel's own question.** `evidence.panel_vendor_check` requires every ballot to declare a vendor and the panel to span at least `jury.min_vendors` distinct ones. Three ballots from two vendors is a legitimate cross-vendor review; the per-slot rule keel applies to a bench *it* staffs would have refused it. Three ballots from one vendor is one opinion three times and still fails with `review-vendor-distinctness`.
  - **A panel tier neither restaffs nor downgrades.** Below `MINIMUM_JURY_VENDORS` participating vendors a jury sitting *beside* a host bench is still downgraded `gating → advisory`, exactly as before — sound there, because the bench still reviewed the change. Where the panel **is** the review, both routes out are shut: the bench does not move (`reviewers.source` stays `jury`), and the verdict does not stop gating either, so a short panel cannot excuse itself from the consensus record that says it was short. Neither the jury flags nor the vendor count may move a bench, because they reach the six review-aware commands unevenly — `keel review` has no `--no-jury`, and only `evidence-verify` and `keel merge` can read a posted verdict — so a bench that followed either would have `keel plan` requiring the panel's ballots while `evidence-verify` demanded a host bench of the same PR, the disagreement #1014 exists to prevent along a new axis. The shortfall is reported instead, by `panel_vendor_check`, as `review-vendor-distinctness`.
  - **s7/s8 adapter prose is one path, read off the contract.** `reviewers.panel: jury` means dispatch ai-jury once here and map the ballots; s8 must not run the panel again for the same head, because that buys a second opinion from the first opinion and pays for it twice. The adapter is told not to fall back to host reviewers on its own: a tier's reviewers are what its config says they are.
  - **keel does not adopt the policy in the change that ships it.** `projects/keel.yaml` and its `.keel/project.yaml` twin are unchanged apart from a comment: tier-3 keeps its three explicit seats (`claude`, `agy`, `subagent:opus-reviewer`) with `jury.mode: advisory` and `evidence_require_distinct_vendors: false`. On a panel tier nothing can take the panel back off (#1014), so the switch commits every tier-3 keel change to a jury run; it is made deliberately, in its own change, once `keel review --from-jury` has been exercised on a real pull request. `"3": jury` with a gating panel remains the documented example in `configuration.md#team` and the fixture the cross-surface tests run.
- **`keel fixloop brief`: s9 routes review findings back to a named fixer** (#1016): `ship.md` s9 said *"aggregate findings → hand to the implementer → fix → push"* and there was nothing behind the arrow. When the implementer was a delegate there was no command, no prompt shape and no ownership rule, so in the live run two blocking majors sat with nobody assigned and the host — whose quota the delegation existed to protect — wrote the fix itself. `keel fixloop brief --pr <N> --findings <json> --round <k>` now renders the round's brief and names the seat that fixes it, both deterministically, and hands back the ready-made `keel delegate run --role fix` argv in `dispatch` (`null` for a `kind: subagent` seat the host runs itself).
  - **The brief is byte-stable for identical findings** and snapshot-tested: findings grouped by severity with `file:line` anchors, each reviewer's own `reproduction`, the round and its budget, and the re-review the push will get — a blocking finding means a full re-review, a suggestion-only round means the narrowed one, whose instruction ("verify only the applied fix in commit `<sha>`; do not re-review what you already approved") is rendered verbatim for the next reviewer's prompt instead of being improvised per host. `findings.Finding` gained an optional `reproduction` field so the reviewer's own reproduction survives the trip; severity semantics stay `keel.findings`' (`critical`/`major` block, `minor` is a gated suggestion, `nit` is advisory) — the fix loop has no vocabulary of its own.
  - **The escalation ladder is `implementer → gate → host`, a pure function of (round, provider availability, budget)** with a test for every hop. Round 1 goes to `assignment.fix` — by default the `implementer` alias #1014 introduced, resolved to the seat that actually implemented, which is the whole point: a delegated implementation gets its own findings back. A failed round escalates one rung; a provider named by `--unavailable` (a `rate-limit` from s4 being the usual reason) is skipped rather than dispatched to; a rung repeating an earlier one is dropped, because escalating to the seat that just failed the round is not an escalation; and a round past the last rung stays with the last usable fixer rather than fabricating one. Every hop is recorded in `hops` with its reason (`start`, `round-failed`, `provider-unavailable`, `ladder-exhausted`).
  - **The ≤3-round budget is unchanged** — the ladder decides *who* fixes, never *how often*. Past the budget there is no fixer (`status: budget-exhausted`) and every rung unavailable is `status: no-fixer`; both **exit non-zero**, so a spent loop cannot be mistaken for a round to run. That is the blocked-issue path s9 already described, now with an exit code behind it.
  - **`keel runcontrols` records who fixed which round.** Events may carry `--provider`, `--attribution` (the label `keel delegate run` computed, never one the host composed), `--stage` and `--round`, and every result carries `fix_attribution` (`keel.fix-attribution.v1`): the implementation actor, one record per fix round, and the deterministic sentence the s11 closure comment embeds — *"implemented by agy, fixed by opus in round 2"*. An escalated round was not fixed by the implementer, and until now the closure said it was.
  - **Reviewer text in the brief is quoted data, never instructions.** The brief becomes the fixer's `--prompt-file`, and findings are the one part of it keel did not write — rendered raw, a finding whose message carried its own `## Rules for this round` section, a second `keel.fixloop-brief.v1` marker and a forged `blocking: no` trailer would render as brief structure, giving the fixer orders keel never issued. Every reviewer-supplied string (message, source, path, reproduction) is now a blockquote: one `> ` per line so nothing a reviewer wrote can sit at the start of a line, the HTML-comment opener defanged, a leading `#` escaped, a line reading as one of the brief's trailer keys rendered as inline code, and the field capped — a prompt has a budget. The message keeps a one-line scannable headline with the rest quoted beneath it, the same split `cli._cmd_run_gates` makes for a multi-line gate message.
  - **An unreadable project config is a refusal, not a default.** `knobs.team.fix` is what decides whether a round goes back to the delegate that implemented or to the host, so resolving it against an empty policy answers "the host fixes" — silently, identically to a project that really has no policy, and reached by running the command one directory too high. `keel fixloop brief` now exits non-zero with `status: no-config` when the config cannot be read; `--no-project` is the deliberate opt-out, and the s9 adapter snippet passes `--project .keel/project.yaml --root .` like every other keel command.
  - The escalation walk is bounded at `len(ladder) + 1` rounds — no later round reaches a rung or a hop an earlier one did not, and `--round 1000000` was a million identical entries in a document an adapter has to read. The duplicate-rung check compares seat `provider`, not the bare name, so `subagent:opus-reviewer` and `opus-reviewer` stay two seats. The spent-budget message names `keel fixloop brief --budget` rather than `keel ship --max-rounds`, which is the run budget and a different knob.
  - **The closure comment can finally say it.** `keel ship --run-events-file` folds the same document into the `ship_run` ledger record as `actors.fixers` and `actors.attribution_sentence`, and the rendered closure comment gains a **Fix rounds** line (`round 2: opus (gate)`) — omitted entirely on a run that spent no fix round, so every existing closure comment stays byte-identical.
  - Adapter s9 prose is rewritten to reference the command and the ladder only, and s11 is told to read the attribution sentence rather than repeat the implementer's labels. Docs: `docs/keel/cli.md` (both commands), `configuration.md#team` (`fix`), `command-contracts.md` (the new fix-loop block), `models.md` (who fixes a review finding).
- **`knobs.implement_mode: tdd` — a test-first s4, with the commit order verified rather than promised** (#1020): some implementers skip parts of an issue, and nothing catches it until a reviewer reads the diff. keel had no way to ask for TDD. `implement_mode: tdd` (or `--tdd` for one run) makes s4 run **two phases against the same provider**, one `keel delegate run` call and one commit each — phase A commits the failing tests derived from the issue's acceptance criteria as a test-only diff, with the gates expected red; phase B implements until they are green — and adds one blocking gate at s8 that checks it actually happened.
  - **The `tdd-order` gate is a pure function of the commit list and the path policy.** The new `keel.tdd` module parses a `git log` into commit records and decides: the branch history is readable at all (an unreadable one blocks — it is not an empty branch), the first non-merge commit touches at least one path and **only** test paths, a later commit touches an implementation path, and the rest of the gate run is green. Merge commits are skipped rather than judged, because a merge from the base carries every path the base moved. The only I/O is one `git.commit_log` read behind the existing seam, so the whole gate is unit-tested offline against commit lists at 100 % line + branch.
  - **A project says where its tests live, or the gate fails closed.** The paths come from `policy_pack.test_groups.*.test_paths` — a new optional field — falling back to a group's `paths`, and **once any group declares `test_paths`, only the declared ones count**. Group `paths` are *selectors* (keel's own `unit` group selects `src/**` as well as `tests/**`); read as test paths they would make the check vacuous, and the implementation commit that must follow would have nowhere to land. A project declaring nothing at all blocks with the key to add, rather than passing on a check nothing performed.
  - **The gate is evaluated after every other gate**, because its verdict includes theirs ("the last gate run is green"). `gates.split_deferred` holds it back explicitly rather than relying on list order, so a future concurrent runner cannot silently invalidate it. It is `on_fail: block` and deliberately **not** a name a project may put in `gates:` — it is the gate the mode brings with it, not one a project can ask for against a commit order it never requested.
  - **Both phases are recorded.** `keel plan`/`keel ship --json` publish `contract.implement_mode` (`mode`, `source`, `phases`, `gate`) as a sibling of `workflow_profile` — `tdd` is an s4 profile the way `compound` is a workflow profile, and the backbone step ids are unchanged. The ledger's `run_context` gains `implement_mode` and one `implement_phases` entry per phase with its commit, and the closure comment renders `Implement: TDD (tests <sha> → implementation <sha>)` — a line emitted only for a TDD run, so every closure comment keel has already posted reads identically.
  - There is no `--no-tdd`: a project that configured the contract has said the contract is the policy, and a flag that switched it off from a command line would make it advisory. `implement_mode: default` is omitted from the canonical config, so adopting the knob does not rotate `config_hash` for a project that has not.
- **`keel doctor` verifies the labels a project declares exist on its repository** (#1021): `projects/keel.yaml` has declared `status:*`, `priority:*` and `role:core` since the day it was written, and not one of those labels existed on `berkayturanci/keel` until they were created by hand while opening this issue set. `ship` and `triage` apply labels **by name**; GitHub rejects a label that was never created, so the failure surfaced as a failed `gh` call mid-run — never as a diagnosis. The new `policy_labels` check compares the declared set against one `gh label list` and names the gap.
  - **The declared set is every label keel itself writes**, not just one config key: `policy_pack.labels.*` (a bare entry is qualified with its group, so `role: ["core"]` means `role:core` — both spellings appear in real configs and mean the same GitHub label), `policy_pack.scan.issue_labels.*` (what regression and review-all-day stamp on the issues they open), and the attribution vocabulary from the new `agents.attribution_labels()` — `agent:<vendor>` for every built-in vendor plus each `knobs.delegate_profiles` entry's *vendor* (`agent:cli`, the label `profile_attribution` writes; the profile name is never a label), and `model:<base>` for a model a profile pins. A `model:*` minted from `--delegate vendor:model` or a `delegate-model:` issue label is unbounded, so the check does not pretend to enumerate it. Run against this repository the check found six real gaps, `agent:agy` and `source:review-all-day` among them.
  - **It never fails a run, and it never guesses.** The check is `warn` for missing labels and `skipped` — a new status, ranked with `ok` so it cannot move the roll-up — for every case where keel could not look: no config path, a config naming no `owner`/`repo`, `--offline`, no `gh` on PATH, an unauthenticated or unreachable GitHub, or a `gh` that answered with something other than the JSON it was asked for. Each carries the reason on the line, because a check that silently reports `ok` when it could not look is worse than one that says nothing. `counts` gains a `skipped` key and `render_report` a `SKIP` state.
  - **The warning prints the fix as runnable lines** (`$ gh label create <name> --repo <owner/repo>`) under the check rather than burying them in `--json`, and `keel doctor <config> --fix` runs those same commands — built once in `github.label_create_argv`, so the command an operator pastes and the command `--fix` executes cannot drift apart. `--fix` is the only mutation `doctor` performs and is gated like every other live keel mutation: the `labels` side effect needs the `github` consent scope (`--approve-scope github --operator <name>`, a standing `KEEL_APPROVE_SCOPE`, or `automation.approved_scopes`), and without it nothing is created. A label that fails to create is named and makes the command exit non-zero without stopping the rest.
  - The label read costs one `gh label list` (`--limit 500`: GitHub's default page of 30 is smaller than a real policy pack, and a truncated listing would report existing labels as missing), runs only when a config is given, and is skipped by `--offline` like every other network read. `_which` and `_run` are injectable, so the whole path — including `--fix` — is unit-tested offline against a stubbed `gh`.
- **`knobs.team`: the implementer / gate / reviewer / jury policy a real team actually runs** (#1014): keel could name *an* implementer (`--delegate`, `knobs.implementer_agents`) and *one* reviewer vendor for all N reviewers (`--review-delegate`). It could not say *this role implements with provider X at effort E, every implementation gets one gate review from a different vendor, tier-2 gets two reviewers from two vendors, tier-3 convenes the jury as the review panel.* `knobs.team` says it, `keel validate` checks it, and `keel plan --command ship --json` / `keel ship --json` render it as one resolved `assignment` — `implementer`, `gate`, `reviewers[]` (per-slot `provider`/`model`/`effort`), `jury`, `fix`, `warnings` — so every host runs the same team instead of re-deriving four independent guesses from prose.
- **A provider picker that only offers providers you actually have** (#1018): `ship.md` has promised an interactive `--wizard` "built from a best-effort tool/model probe" since the adapter was written, but no core code existed for it, so every host improvised the questions — different defaults, and options naming CLIs the operator has never installed. `keel ship --wizard` / `keel work-block --wizard` now run a real picker whose single data source is the same probe as `keel doctor --providers`: implementer provider, its model and reasoning effort, the gate seat, the jury mode, the reviewer bench and the review-comments mode. **A provider the probe did not mark available is never offered and cannot be selected**, whether it is typed at the prompt or handed in with `--wizard-answer`. Defaults come from `knobs.team` (#1014) and from the flags already on the command line, so answering nothing reproduces exactly what the command would have done; a configured seat this machine cannot reach is named once and degrades to one it can.
  - **The interactivity guard is now code, not prose.** With no terminal and no recorded answers the wizard prints `wizard: non-interactive context — logged no-op` and the command proceeds with the literal flags as parsed — never a hang waiting on a stdin nobody is typing into, never a rejection of a run that was fully specified on the command line. A machine where the probe finds nothing usable is the same logged no-op. Both are asserted through an injected `isatty` seam rather than described.
  - **`--wizard-answer KEY=VALUE`** (repeatable, or `;`-separated) replays a wizard run without prompting, on a terminal or not — which is what makes a run reproducible, and what lets keel's own tests drive every question offline. A malformed pair, or one naming a choice the wizard does not offer, exits 1 **before any gate runs**: silently ignoring a misspelled answer would run a team the operator did not ask for.
  - **`keel init --wizard` / `keel setup --wizard` gained a team step** that writes `knobs.team` from the same probe, asking the reviewer bench per risk tier (a config names one per tier; a run has one bench, because `--reviewers`/`--review-delegate` are per-slot flags and the tier is not known until s1 classifies the diff). Nothing usable on the machine ⇒ the step is skipped and **no `team` block is written at all** — an absent block is not an empty one, and leaves `config_hash` where it was.
  - **The wizard cannot write a policy `keel validate` then refuses.** The config step offers only what a committed policy may name — built-in vendors and the project's own `delegate_profiles`, never a machine-level `~/.keel/providers.yaml` entry, for the same reason validation does not consult one; the gate seat is offered from every provider *except* the implementer and written with `distinct_from: implementer`; and a reasoning effort is only asked for once a model is chosen on a vendor that spells effort as a model suffix. That last rule now has one home: `team.effort_needs_model`, read by both the validator and the wizard instead of being re-derived.
  - Output is the resolved **flag set**, echoed in the worked-example shape so the adapter passes literal flags on (`--delegate`, `--reviewers`, `--review-delegate`, `--review-comments`, one of `--jury`/`--jury-advisory`/`--no-jury`), plus the seats behind them. Under `--json` the echo goes to stderr so stdout still carries only the contract document. `keel work-block` has no implementer or jury flag of its own, so those choices are echoed for the adapter to hand to each child `keel ship` rather than written onto a namespace with nowhere to put them.
  - **A default is not a decision.** Only a question the operator actually answered becomes a flag. Every option also resolves to a default, and writing those back is not neutral — it overrides the very policy they were read from: the run wizard's reviewer bench is derived at a nominal tier (the real one is not classified until s5) and the jury question opens on whatever the flags and `knobs.team` already say, so a quick-start run on a **tier-3** change passed `--reviewers 2 --no-jury` and quietly dropped a reviewer and the gating jury from the strictest tier keel has. An unanswered question now emits nothing and the command resolves it exactly as it would have without `--wizard`; pressing Enter keeps the default, typing a value is an explicit override. `tests/test_wizard.py` pins the tier-3 quick-start contract against the real `cli._review_assignment`.
  - **A jury panel is a review only when the jury gates.** Offering the `jury` bench beside an `advisory` jury let the wizard write `jury.mode: advisory` together with `review.by_tier."3": jury` — the pair `team._review_issues` refuses, because that tier then has no host reviewers and an advisory verdict requires nothing. The offer and the resolution both enforce it now, and a 72-case sweep over the config-scope answer space asserts every reachable answer set produces a block `keel validate` accepts.
  - **`--wizard-answer` implies `mode=customize`.** The first question's own default (quick-start) ended the walk before the second question existed, so every answer but `mode` was rejected as "not a question this wizard asks" — a flag that could only ever set `mode`. An explicit `mode=quick-start` still means "ignore the rest", and a key that is real but unreachable in this run (`review.3` in a run, `implement.model` for a provider that lists none) now says which rather than reading as a misspelling.
  - The catalogue guard covers **every** seat, not just the implementer: a gate or reviewer answer seated directly on the state — bypassing `normalize`, as an injected seam can — falls back to the offered default instead of naming a provider the probe never found.
  - **A run is asked only what a run can carry.** `keel ship` has no `--gate` and no `--effort` (`--delegate` splits `provider:model` and stops), and `--reviewers` takes `1|2|3` with no spelling for "the panel *is* the review". Asking those three in a run produced answers that changed nothing the command published — the echo named a gate while `assignment.gate` still held the policy's, the same two-documents-disagree defect #1014 fixed for reviewers. The gate seat, the reasoning effort and the jury panel are now `keel init --wizard` questions only, where they land in `knobs.team`; a run refuses them via `--wizard-answer` with a message saying which flag would have had to carry them. A test walks every question the run scope asks and asserts each one moves the flag the command acts on, and the two it no longer asks are asserted to be refused rather than silently dropped.
  - The planner (`keel.wizard`) is pure and deterministic — no I/O, no clock, no randomness, and one traversal that emits the questions *and* the values they resolve to, so a question's default and the value used when it is unanswered can never drift. The probe, the terminal and the parsed namespace are the thin half (`keel.wizardrun`), all three injectable.
- **`knobs.team`: the implementer / gate / reviewer / jury policy a real team actually runs** (#1014): keel could name *an* implementer (`--delegate`, `knobs.implementer_agents`) and *one* reviewer vendor for all N reviewers (`--review-delegate`). It could not say *this role implements with provider X at effort E, every implementation gets one gate review from a different vendor, tier-2 gets two reviewers from two vendors, tier-3 convenes the jury as the review panel.* (The reviewer count and the jury mode are enforced by the evidence gate. The **gate review is emit-only**: core publishes the seat, the adapter dispatches it, and no evidence item proves it ran — the same boundary `docs/keel/operator-consent.md` describes.) `knobs.team` says it, `keel validate` checks it, and `keel plan --command ship --json` / `keel ship --json` render it as one resolved `assignment` — `implementer`, `gate`, `reviewers[]` (per-slot `provider`/`model`/`effort`), `jury`, `fix`, `warnings` — so every host runs the same team instead of re-deriving four independent guesses from prose.
  - **A provider is a provider, and a subagent says so.** `implementer_agents` values were documented as vendor strings in `docs/keel/models.md` and as Claude subagent names in `ship.md` s4, with the schema silent on which. A `team` seat's `provider` resolves through the same registry `keel delegate run` uses (built-in vendor > `knobs.delegate_profiles` > `~/.keel/providers.yaml`), and `subagent:<name>` is the explicit spelling for the host-subagent meaning. `implementer_agents` keeps working — it is mapped onto `team.implement.by_role`, reading a value that names a resolvable provider as that provider and anything else as `subagent:<value>` — and is documented as deprecated.
  - **Validation refuses a policy keel cannot execute**, rather than discovering it mid-run: an unknown provider name; an `effort` on a provider with no spelling for reasoning effort (`claude`, `ollama`, a generic `cli` profile) or on `agy` without the model its suffix-based effort needs; a `gate.provider` equal to a configured implementer when `gate.distinct_from: implementer` (a second opinion from the vendor that wrote the change is not a second opinion); more than three reviewer seats for one tier; a `review` value that is neither seats nor `jury`. The machine-level registry is deliberately *not* consulted — validation has to give the same answer on every machine.
  - **`review: jury` makes the panel the review.** A tier whose policy is `jury` yields `review_merge_contract.reviewers.count == 0` and a gating jury, so the evidence gate requires the panel's verdict instead of N host review verdicts. `--reviewers N` on such a tier is reported in `assignment.warnings` rather than silently replacing the panel with host reviewers.
  - **`--delegate` and `--review-delegate` stay per-run overrides of the policy.** `--review-delegate` is now **repeatable and positional per slot** (first occurrence is slot A, second slot B), so a two-vendor panel needs no config change; a value past the last staffed slot lands in `assignment.warnings` instead of being dropped or silently growing the panel. `keel ship` and `keel plan` also gained `--role` (which `team.implement.by_role` seat), and `keel plan` gained `--tier` so the assignment can be rendered before a diff exists.
  - `projects/keel.yaml` (and its `.keel/project.yaml` twin) adopts `team` for keel itself, replacing its `implementer_agents` entry. It describes the team that actually reviews keel today rather than the reference example: `subagent:backend-developer` implements, `agy` gives the gate review, tier-1 is `claude`, tier-2 is `claude` + `agy`, and tier-3 adds a second Opus reviewer as `subagent:opus-reviewer`. The jury stays `advisory` and `evidence_require_distinct_vendors` is set to `false` explicitly, because `distinct_vendor_check` requires every required verdict to carry a *pairwise-distinct* vendor and that tier-3 panel is anthropic + google + anthropic — tier-2 still gets two vendors by construction of the seats. `"3": jury` is the intended future (it waits on the jury being dispatched from s7) and stays documented in `configuration.md#team` as the reference shape.
  - `team` is omitted from the canonical config when absent, so adopting the knob does not rotate `config_hash` for a project that has not — and `config_hash` changes whenever `team` does.
  - **One resolution, six readers.** `keel ship`, `keel plan`, `keel review`, `keel step-verify`, `keel evidence-verify` and `keel merge`'s pre-merge check each derived the reviewer bench themselves — survivable while it came from the risk tier alone, fatal with `knobs.team`: on a `review.by_tier."3": jury` project, ship published zero reviewer slots while evidence-verify demanded `review-verdict-1..3`, a gate no run of that project could ever satisfy. They now share one resolver, `cli._review_assignment`, and an AST sweep (`tests/test_review_contract_agreement.py`) fails the build if a *new* call site resolves a review contract without it. `keel step-verify` gained an optional `--project` / `--tier` for the same reason.
  - `keel ship` also recomputes `evidence` and `step_verification` from the resolved contract. Both are *derived* from the review contract at the unresolved tier, so overwriting only `review_merge_contract` published one document carrying `reviewers.count: 0` beside an evidence block demanding two review verdicts — and the adapters read the evidence block.
  - **The reviewer bench is a pure function of config + tier + role + `--reviewers` + `--review-delegate`, and of nothing else.** It cannot depend on the jury flags: the six commands that resolve this contract do not all receive them — `keel review` has no `--no-jury` at all, and keel's CI passes it to `evidence-verify` on every run and to `ship`/`plan` on none. So on a tier whose review policy is the panel, `--no-jury` and `--jury-advisory` are **recorded in `assignment.warnings` and not applied**: the panel is that tier's only review, `reviewers.count` stays 0, and the jury verdict stays required. Below a panel tier both flags keep their existing meaning, `--no-jury` still beating the tier-3 auto-jury. Jury precedence is now: a `knobs.team` jury-panel tier > `--no-jury` > `--jury` > tier-3 auto > off.
  - Relatedly, `keel validate` refuses `jury.mode: advisory` on a project that also makes the jury a tier's review panel: "the panel is the review" and "the panel does not gate" together describe a tier that requires nothing, which is a stricter-looking policy with a weaker gate.
  - A deprecated `implementer_agents` value splits `vendor:model` before it is read as a subagent name, so the documented `frontend: anthropic-api:claude-3-7-sonnet-20250219` resolves to that vendor and model instead of a host subagent named `anthropic-api:claude-3-7-…` that does not exist. The `gate.distinct_from: implementer` rule is checked against those legacy seats too, so `implementer_agents: {core: codex}` beside `gate: {provider: codex}` is refused rather than quietly passing.
  - A bench padded out to `--reviewers N` with the host agent that is already seated now warns instead of returning silently, because those reviewers cannot produce distinct vendor provenance; and an out-of-range reviewer count raises the documented `ValueError` rather than an `IndexError` from inside the resolver.
- **`keel delegate run`: one executor for every transport, with a detachable wait primitive** (#1012): nothing in keel dispatched a delegate. `api_delegate.generate()` had no caller in core, and the `claude`/`codex`/`agy` argv shapes, the stdin framing, the Ollama endpoint, the timeouts and the JSON return contract lived only as prose in `ship.md` s4/s7 — so every host agent re-implemented them and the copies drifted. `keel delegate run --provider <name|vendor:model> --role implement|fix|review|gate|chair --prompt-file <p>` now performs the dispatch for all four transports (`cli`, `profile`, `api`, `ollama`) and prints one JSON document: `ok`, `provider`, `vendor`, `model`, `role`, `transport`, `text`, `exit_code`, `duration_s`, `timed_out`, `error_code`, `error`, `attribution`, `effort_applied`, `warnings`.
  - **The role picks the invocation, not just a label.** `review`/`gate`/`chair` run read-only; `implement`/`fix` run tool-enabled. For the three built-in CLIs the read-only invocation carries no write-enabling flag, and that is asserted **per vendor** in `tests/test_delegate.py` rather than written down in a markdown file nobody can execute. keel cannot *enforce* read-only for an arbitrary binary — a profile or registry entry with no `review_args` runs with its own defaults and the result carries a warning saying so, which is the honest answer rather than a promise nothing backs.
  - **`attribution` is computed by core** (`agents.attribution` / `profile_attribution`), so the labels a host writes can no longer drift from the vendor that actually ran — the drift that put `agent:gemini`/`model:gemini` on a PR while keel's vocabulary says `agy` / `gemini-3`.
  - **`--effort low|medium|high` is translated per vendor**: an `agy` model suffix, Anthropic `thinking.budget_tokens` with `max_tokens` raised above the budget, `reasoning_effort` for OpenAI and OpenAI-compatible endpoints, `generationConfig.thinkingConfig.thinkingBudget` for Gemini. A vendor that cannot express effort returns `effort_applied: false` **with a warning** instead of silently running at its default.
  - **The prompt never reaches an argv.** It is delivered on the delegate's stdin (NDJSON for `agy`, whose `--print` takes the prompt as a flag *value*): a prompt carries the diff and the brief, an argv is world-readable in `ps` for the life of the process, and a large diff can exceed `ARG_MAX` outright. The one exception is a profile that declares `prompt_mode: arg`, where the operator has said the CLI requires it.
  - **The role's promise is reported, not merely asserted.** The result carries `read_only` (the role that was asked for) **and** `read_only_backed` (whether anything enforces it). They differ in exactly the dangerous case: `DelegateProfile.role_args` falls back to `args` when `review_args` is unset, so a profile carrying `aider`'s `--yes-always` or `cursor-agent`'s `--force` plans a *reviewer* holding the implementer's write flags. That run now reports `read_only_backed: false` with a warning naming the provider, and ship s4/s7 tell the orchestrator to branch on that field rather than on `read_only`.
  - **`claude` runs read-only under an allow-list**, `--allowed-tools Read,Grep,Glob`, and carries no permission bypass at all. The alternative — a four-name denylist plus `--dangerously-skip-permissions` — hands the bypass to every tool nobody remembered to enumerate, and the tool surface grows with each release. An allow-list refuses a new tool on the day it appears. `agy` still pairs `--sandbox` with the non-interactive flag because that is the only read-only mechanism it documents, which is precisely why `read_only_backed` reports what backs the promise instead of claiming writes are impossible.
  - **Model tokens are validated against where the model lands.** The strict `[A-Za-z0-9._-]` rule still guards a subprocess argv and `google-api`'s URL path, the two places a stray character can become another flag or retarget a request. A model that is only a JSON body field accepts `[A-Za-z0-9._:/-]` (no leading dash, no `..`) — because applying the argv rule everywhere refused `ollama:qwen2.5-coder:32b` and `openrouter:deepseek/deepseek-r1`, ids this repository's own documentation tells operators to use.
  - **Built-in vendors always win.** Resolution is **built-in > project profile > machine registry**, matching the invariant `keel validate` enforces for `delegate_profiles` and `keel doctor --providers` reports for the registry. Dispatch must not be the one place where `claude` means whatever a file in `$HOME` said it meant. The order had been restated in its own words in six places and was inverted in four of them; `providers.plan_probes`, `configuration.md` and the adapters now agree with the resolver, and a test pins every one of them so the next edit cannot re-split them.
  - A provider entry's own `effort:` — carried through the registry since #1011 and until now never read — is the per-seat default when `--effort` is absent; a per-run value still wins, and an unrecognised configured one is a warning rather than a failed run. `codex` joins the vendors that can express effort, via `-c model_reasoning_effort=<level>` (verified by round-tripping a real and a bogus key through `--strict-config`).
  - **Fail-soft, always.** A missing binary, a nonzero exit, a quota refusal, a timeout or an unparseable answer becomes `ok: false` with a machine-readable `error_code` — never a traceback. The policy that reads those codes (never retry `rate-limit`, refuse a non-tool provider on tier-3, fall back to the host agent) stays with the caller, because it is policy and this is a transport.
- **`keel delegate run --detach` + `keel delegate wait` + `keel delegate status`** (#1012): a delegated implementation runs for tens of minutes and a host LLM's turn does not, so a long run used to end with the orchestrator dead and the subprocess still going. `--detach` starts the run as a background child in its own session and returns a run id; `.keel/state/delegate/<run-id>.json` (with the child's output beside it) is **authoritative**, so the result survives the caller exiting, the session ending and a reboot. `keel delegate wait <run-id> [--timeout S]` prints the same JSON contract and **fails closed** on an unknown id (`unknown-run`) rather than waiting out a timeout on a typo. This is the primitive that replaces sleep-and-poll advice in the adapters — a polling loop burns the host's context window and cannot outlive its turn, which is how a live run finished three reviewers and posted none of their verdicts.
  - A run id becomes a file name, so it is validated (`[A-Za-z0-9._-]`, no `..`) before it becomes a path; `wait` and `status` on anything else are refused rather than normalized. State lives inside the already-gitignored `.keel/state/` tree.
  - **A killed child is reported, not waited on forever.** A `SIGKILL`, an OOM kill or a reboot leaves the record at `running` because the only writer that would have changed it is the process that just died — so `wait` polls the recorded pid's liveness and honours a `deadline_at` stamped from the run's own `--timeout`. Either signal marks the run `crashed` and returns `error_code: lost`, naming the `.out` file that holds whatever the child managed to print. A dead pid is re-checked against the state file first, because a delegate writes its result *before* the process ends and the two are observed in the other order often enough to matter.
  - **The record is written by the child alone.** Everything else about a detached run is a sidecar: `<run-id>.pid` written by the parent, `<run-id>.crashed` by whoever concludes the run is gone, `<run-id>.out` by the child's own stdout. Each of those started as a field in the record, and each was a read-check-write on a file another process can replace at any instant — a guard does not fix that, because the child's terminal record lands between the read and the write and `running` goes back over the result the caller is waiting for. Reads compose the files, and a `done` record always wins over a crash marker: a marker says "this looked abandoned", a `done` says "the delegate answered". One writer per file, no locks, no windows.
  - **A reused `--run-id` inherits nothing.** Naming a run after the issue it implements means the retry reuses the id, and the previous run's pid file then paired the *new* record with a dead pid — so the next `keel delegate status`, which reaps, marked a run that started milliseconds ago as crashed. The markers are cleared as the new record is written, alongside the truncation of the log file that was already happening for the same reason.
  - `keel delegate status` applies the same liveness and deadline test as `wait` before listing, so a killed run stops reporting `running` even when nobody waited on it — the view an operator opens *because* they are not waiting was otherwise the one that never noticed. An unwritable root (a read-only checkout, a root owned by someone else) comes back as the same `spawn-failed` contract as any other failure rather than a `PermissionError` traceback.
- **`api_delegate.generate(..., extra_payload=…)`** (#1012): an optional vendor-shaped fragment merged into the request body, which is how `--effort` reaches a hosted vendor. The merge is recursive by one level for a reason that matters exactly once: Gemini spells effort inside `generationConfig`, and a shallow merge would have dropped the `maxOutputTokens` already there — a request that still succeeds, with an unbounded answer. Keyword-only with a `None` default, so every existing caller builds the same body it did before.
- **Rollback covers Homebrew and partial publishes** (#1025): `docs/keel/release.md`'s "Rollback And Re-Run Notes" is now a decision table — bad PyPI release only, bad tag or GitHub Release, bad Homebrew formula, and partial publish (PyPI succeeded, a later step failed) — each with exact commands. The Homebrew row spells out what a PyPI yank does *not* do: `brew install keel` builds from the GitHub tag archive, so yanking PyPI leaves it serving the bad tag; retargeting/deleting the tag or shipping a corrective release and waiting out the tap's 30-minute sync are what actually stop it.
  - `scripts/release_bump.py` gained `visual_divergence()`: a core version bump never touches keel-visual, so if its own two version markers (`keel-visual/pyproject.toml` vs. `keel_visual/__init__.py`) had already drifted apart — the #796 shape — the release used to ship past it unnoticed. It now warns by default and, under a new `--strict` flag, refuses; `make release-bump` passes `--strict`.
  - Each rollback row still needs one end-to-end rehearsal on a scratch tag with the transcript linked from the doc — marked "rehearsal pending" there and tracked as a follow-up rather than claimed.

### Removed
- **The committed Homebrew formula, and with it the second write to `main`** (#1023, implementing #990): `Formula/keel.rb` named a `url` and a `sha256` that cannot both be correct at once — the url moves to the release being cut, and the digest belongs to the archive GitHub builds *from the tag*, which is created from the very commit the release pull request produces. The file was therefore stale on every release by construction, and each release ended with a follow-up pull request to repair it: one line, machine-generated, and still requiring three reviewer verdicts and an `agent:*` label (#989). When one was forgotten, the tap refused every sync for a day (#981). Five mechanisms had been built for that one unsatisfiable requirement (#805, #842, #982, #984, #986); deleting the file ends all five.
  - `packaging/homebrew/keel.rb.template` replaces it, naming `@URL@`, `@SHA256@` and `@VERSION@`. `publish.yml` renders it **after** the tag exists, from the archive the tag actually produced, and attaches the result to the GitHub Release — permanently at `releases/latest/download/keel.rb` and listed in that release's `SHA256SUMS`. **A release is now exactly one write to `main`: the release pull request.**
  - The render **refuses rather than reports**, before the release is created: an archive it cannot fetch within a bounded wait, a placeholder that survived `sed` (which reports success either way), a url that is not this repository's archive for this tag — #990's load-bearing guard, since a digest is a correct description of whatever it was taken over — or a missing top-level digest. The `::notice::`-and-hope shape is what left the tap failing hourly (#981).
  - `pull-requests: write` is gone from `publish.yml`, and with it the branch push, `gh pr create` and `gh pr merge --auto`. What remains of that job is `tap-status`: `contents: read`, no token, no path that can fail — it reports whether the tap is already serving this tag.
  - **The evidence gate keeps no bot exemption**, which #1023 also asked for. The author is not a reliable key: several bots commit through the GitHub API *as the repository owner*, so an identity check sees a human — measured in the sibling repository, where a bot's push from a stale checkout silently reverted two merged pull requests (ai-jury#676, #680). A content filter ("one file, only these two lines") describes the diff at the moment it is evaluated and not after the next push. The reason an exemption was wanted was one machine-generated pull request per release; there is no such pull request any more. Recorded in `docs/keel/evidence.md`.
  - **Companion change, required before this can merge:** the tap pulled `contents/Formula/keel.rb` every 30 minutes and that file is gone, so an un-repointed tap 404s on a schedule forever. `packaging/homebrew/tap-sync-formula.patch` repoints it at the release asset (and tolerates the asset not existing yet, which is the state until the first release after this lands); `packaging/homebrew/TAP_REPOINTED` records the tap commit that applied it, and `tests/test_external_promises.py` requires that sha offline and checks the live tap under `KEEL_CHECK_EXTERNAL=1`.
  - The render sits between `Generate checksums` and the attestation, i.e. **before** the PyPI upload rather than after it. Nothing in it needs the upload — its inputs are the tag, the repository, the committed template and `release/SHA256SUMS` — and PyPI files are immutable, so a render that failed after the upload would leave a published version whose formula could only be repaired by cutting another release. (The sibling renders *after* its upload because it must: its url and digest come from what PyPI reports about the sdist it just accepted.) The archive wait is 60s to the sibling's 300s for the same reason — this waits on GitHub serving an archive of a ref its own API just accepted, not on a third party indexing an upload.
  - `scripts/release_surfaces.py` no longer lists the formula — the template names no version, so `make release-bump` has nothing to bump. `tier3_globs` gains `packaging/homebrew/*.template` and `packaging/homebrew/*.patch` rather than the directory: `packaging/homebrew/**` would also sweep in the README and the tap marker, and `classify.DIFF_CLASSIFIED_GLOBS` covers workflow YAML only, so a README typo there would be permanently tier-3 with no diff able to downgrade it.
  - **What the tap gate does *not* enforce, stated where it is claimed**: `external promises` is not among `main`'s required status checks, so only the offline `TAP_REPOINTED` marker blocks a merge — and that marker is a claim in a reviewable form (any 40-hex passes; a reviewer can open the commit it names), not proof. Making `external promises` required is what would turn it into an enforced fact, and it is an operator decision. Recorded in the chain doc, `packaging/homebrew/README.md` and the marker's own text, all three of which previously answered that row with "it runs in CI's `external promises` job" — which reads as *yes*.
  - **The residual risk is written down rather than assumed away**: the url is a GitHub auto-generated tag archive, and GitHub does not guarantee those are byte-stable (it changed their compression in January 2023, moving every digest at once), so a formula that verified when rendered can stop verifying with no change here. Rarer and smaller than the per-release staleness this removes, and caught downstream by the tap's re-hash and `test_the_tap_serves_an_installable_formula`.

### Tests
- **The release chain's tests now assert the absence of the mechanism they used to assert** (#1023): `tests/test_publish_formula_followup.py` pinned the shape of the follow-up pull request — that it edited the formula, opened a PR and armed auto-merge. `tests/test_publish_release_chain.py` replaces it and requires the opposite: no `gh pr create`, `gh pr merge`, `git commit`, `git push`, `git checkout -b` or `HEAD:main` in any `run:` body, and no `pull-requests` permission on any job — plus the render step's shape (bounded wait, refusal on every failure mode, the formula inside `SHA256SUMS`, attached to the release, rendered *before* the release is created). Asserted over each step's code with comment lines stripped first, because the workflow's own prose discusses the pull request it no longer opens; vacuity-guarded, since an empty mapping satisfies every `assertNotIn`.
- **The offline formula guards moved to the template, and the online ones to the tap** (#1023): `tests/test_release_docs.py` now requires the template to carry placeholders rather than a version and a digest, and re-runs `publish.yml`'s rendering on a fixture so "no placeholder survives" is checked on every push rather than once per tag. `tests/test_external_promises.py` checks what the tap serves — the copy `brew` downloads — never comparing it against this branch's version, because the tap is written after the tag and is legitimately behind between a release and its next sync; a check that failed on that lag would block the only sequence able to satisfy it. #990's load-bearing guard (the url is *this* project's archive, at a tag) is a pure function with four offline cases.
- **The review-contract agreement suite is offline again** (#1014): `tests/test_review_contract_agreement.py` drives five CLI commands through `cli.main`, and three of them shell out on a real host — `plan`, `ship` and `review` each probe `gh auth status` through `runtime.detect`, so the module validated a live GitHub token on every command and *failed* on a runner without `gh` credentials (`keel review` exits 1 on the missing capability and prints nothing, so the JSON parse raised `JSONDecodeError`). All the seams are now filled in `setUpModule` — `runtime.detect` answers from memory with `gh`/`gh-auth` available, and `run_argv` is patched where each module binds it (`cli`, `git`, `github`) with a recorder that answers `git` as "not a repository" and **raises** on anything else. A dedicated test drives every leg and asserts the only tool any of them reached for is `git`; another checks the guard itself still raises; a third pins the regression by re-running the review leg with the probe reporting "not logged in". Module runtime went from ~13 s to ~0.3 s, which is what 12 token validations cost.
- **A CHANGELOG committed with merge-conflict markers now fails `make test`** (#1010): a merge of `origin/main` into a PR branch was pushed with the `CHANGELOG.md` conflict unresolved (commit eb7f2ec) — `tests/test_changelog_sections.py` only parsed `## [` and `### ` lines, so the `<<<<<<<` / `=======` / `>>>>>>>` markers matched neither regex and CI stayed green. That file now fails on any line matching `^(<{7}|={7}|>{7})( |$)`, pinned by a fixture reconstructing eb7f2ec's three-marker body, and a new tree-wide `tests/test_no_conflict_markers.py` walks every `git ls-files` entry (skipping binaries by a null-byte sniff) so any tracked file fails the same way — `tests/test_swarm_landing.py`'s deliberate marker-shaped fixture is excluded by name.
- **`keel doctor --providers`: which delegates are actually usable on this machine** (#1011): keel advertised agent CLIs, hosted APIs, OpenAI-compatible endpoints, generic CLI profiles and local Ollama models, and nothing in core could tell an operator which of them worked here — `doctor` ran six checks and none looked at an agent. `keel doctor --providers [--json]` now probes every built-in vendor, every `knobs.delegate_profiles` entry and every machine-level registry entry, reporting `available` / `reason` / transport (`cli` · `api` · `local`) / capabilities (`tools`, `read_only_mode`, `model_selection`) and the model list a provider exposes for itself (`agy models`, Ollama `/api/tags`). Probes are time-boxed (5 s per subprocess, 3 s for the one loopback HTTP call) and fail-soft: a missing binary, a broken install, a stopped server or a malformed answer is a row with a reason, never an exception.
  - **Secrets stay names.** A hosted-API row says `ANTHROPIC_API_KEY is not set`, never a value, and makes no request — whether a key *works* is a question only the vendor can answer, and asking would bill the operator on every `doctor` run.
  - **One URL is dialed**, the hardcoded loopback `http://127.0.0.1:11434/api/tags`, through the shared non-redirecting opener. An endpoint named by config or by the registry is checked for key presence only: `doctor` does not become a request-forgery primitive because a file mentioned an address.
  - A CLI must be on `PATH` **and** answer `--version`. A present-but-broken binary reported as a usable implementer fails at s4 instead, which is the expensive place to find out.
- **A machine-level provider registry, `~/.keel/providers.yaml`** (#1011, `KEEL_PROVIDERS` overrides the path): which providers exist is a property of the machine and the person — one operator has `claude`/`codex`/`agy` logged in and no API key, another has only `XAI_API_KEY` — so those facts no longer have to be committed to `project.yaml`. Entries carry name, transport, command/endpoint, `api_key_env`, model, effort and `review_args`. Precedence is **built-in > project profile > registry**; a missing file means no machine-level providers, and a malformed one degrades to warnings rather than raising.
  - A registry `api` endpoint keeps the loopback-only default, so a remote one of the operator's own is skipped with a warning **naming `KEEL_ALLOW_REMOTE_ENDPOINT`** and where to export it — a file must not be able to widen its own reach, so no registry entry can grant itself a remote endpoint.
  - A registry name that shadows a built-in vendor or one of the project's own profiles is refused with an error **naming both sources**, mirroring the existing built-in shadowing rule — the entry is dropped rather than silently overriding, and `--strict` turns it into a non-zero exit. The check lives in `doctor`, not `keel validate`: validate must stay a function of the committed config alone, so its result cannot depend on whose home directory it runs in. `config_hash` is untouched.
- **`providers` and `review-vendors` runtime capabilities** (#1011): a command can now declare "needs at least one tool-capable implementer" and "needs ≥ 2 distinct review vendors". Both are detected cheaply — `PATH` lookups and env-var *names*, no subprocess and no socket — because `keel capabilities` runs on every command; the deep probe stays behind `keel doctor --providers`.
- **The release verifies itself, before and after the upload** (#1024): every step after `publish.yml` was prose for a human. `scripts/release_smoke.py` was documented in the runbook and wired into no workflow — `grep -rn release_smoke .github/workflows/` found nothing. "Confirm the PyPI wheel/sdist SHA256 match the GitHub Release digests" was a sentence. Nothing compared `CHANGELOG.md`'s top `## [x.y.z]` to `__version__` (#979 was caught by a review, not by CI). The `keel-visual` marker guard ran on pull requests only, which is not the phase its drift shipped in (#796).
  - **Before anything is uploaded.** `make release-check` — and the same command in `publish.yml`'s build job, with `--tag "$GITHUB_REF_NAME"` — refuses a release that does not agree with itself: `pyproject.toml` and `src/keel/__init__.py` in lockstep, the top *released* CHANGELOG section naming the declared version, every release surface on that version, `keel-visual`'s two markers agreeing. A tag whose CHANGELOG was never renamed from `## [Unreleased]` now fails there, where it is still fixable; PyPI files are immutable, so afterwards it is not.
  - **One table, both directions.** `scripts/release_surfaces.py` holds `RELEASE_SURFACES`; `release_bump.py` writes through it and `release_check.py` reads it. A surface registered for the bump and guarded by nothing is no longer expressible — which is the shape of the failure that left `docs.html`, `coverage.html` and `content.js` seven releases behind. The keel-visual rule is read from `release_bump.VISUAL_EDITS` rather than restated, so there is still exactly one list.
  - **After the upload.** A `verify` job on the tag waits until PyPI's release document lists *both* distributions — bounded at five minutes, then it fails — checks each artifact's bytes against the `digests.sha256` PyPI computed on upload, installs `keel-workflow==<tag>` into a clean virtualenv and requires `keel version` to print the tag, runs `keel doctor` (log-only) and `release_smoke.py`, and finally compares the same artifacts against the GitHub Release's `SHA256SUMS`. Both digests are printed per artifact, so the log shows the comparison instead of asserting it.
  - **PyPI is the primary record, and the build is now reproducible — including the sdist.** `python -m build` stamped the current time into the archives, so two runs over the same tree produced different digests. PyPI keeps the first upload (`skip-existing: true`) while the release upload overwrote by default — a re-run at the same tag therefore published a `SHA256SUMS` describing artifacts PyPI never served, and the verify job would have filed a `release-broken` issue against a healthy release. The build job now exports `SOURCE_DATE_EPOCH` from the tagged commit's own commit time, the release upload carries `overwrite_files: false`, and — since the epoch alone is *not* sufficient — `scripts/normalize_sdist.py` rewrites the sdist envelope before anything reads it. With the pinned setuptools 84.0.0 the wheel rebuilds byte-identically and the sdist does not: its root directory member takes wall clock through a PAX record (`1788437700.5630772` vs `1788437703.3743458` in two builds of one tree), the gzip header carries its own mtime, and every member carries the runner's uid/gid. The script sorts members, writes `USTAR` (which cannot express a PAX record), pins every mtime, zeroes ownership and writes gzip with `mtime=0` — stdlib only, and it touches no byte a consumer unpacks. The build job then builds a second time and fails the release if either digest differs, because a reproducibility claim that lives only in a comment is how this gap survived a review round in the first place. And the digest comparison is primarily against PyPI's own record with the release's checksum file as a secondary check that tolerates that history. The tolerance asks whether the asset was *replaced* (`createdAt` vs `updatedAt`), not whether it is newer than the PyPI upload — measured on the healthy 1.19.3 release, the assets land 11 seconds after the PyPI upload, so the obvious rule would have downgraded every genuine mismatch to a warning. Fetching from the document's `urls[]` also drops `pip download --no-binary=:all:`, which pulled an unpinned setuptools into an isolated build env on the verification path.
  - **A failure files a report.** The job fails *and* opens — or comments on, deduped by title — `release-broken: <tag>` with the last 100 lines of the verify log. A red job in a workflow nobody reopens after a green publish is the same notice-nobody-acts-on that left the tap failing hourly for a day (#981).
  - `docs/keel/release.md`'s "Current Release State" no longer names a version. The line was hand-maintained and enforced by nothing: `1.8.2` for three releases, then `1.19.0` for three more, while the tree was at `1.19.3`. What replaces it is the three commands that answer the question, and a test that fails if a pinned version reappears in that file.
- **The docs' claims are now checked against keel, not against prose** (#1019): `tests/test_docs_claims.py`. `tests/test_documented_commands.py` asks whether a documented `keel <cmd>` is a real subcommand and stops there, so a real subcommand with invented flags — `keel swarm-land … --mode auto`, `keel window … --root .` — sailed through, including inside the adapter bodies an agent executes. The new module compares each claim to the thing it claims about: every top-level subparser must have a `## \`keel <name>\`` section in `cli.md` (and no section may name a non-command); every `knobs.properties` key in `project.schema.json` must have both a table row and a `#### \`<knob>\`` detail in `configuration.md`; every `keel …` line in a shell-tagged fence across `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/`, `keel-visual/` **and `src/keel/adapters/commands/`** must survive `build_parser().parse_args` (273 of them today; lines carrying a `<placeholder>`, a `$VAR` or a glob are deliberately skipped); every `keel …` card in `website/integrations.js` must parse; and every stated `/keel` command count on the site must equal `len(src/keel/adapters/commands/*.md)`. Nothing is retyped as a list in the test — a list in a test drifts exactly the way the prose did. Six further checks land alongside: the site's `13 steps` / `28 extension slots` hero numbers are read off `model.BACKBONE` and `model.SLOTS`, and `website/params.js` is compared field-by-field against the adapter frontmatter it claims to be generated from — including that no flag chip names a flag its own `argument-hint` omits. The count register was then widened from the website to every prose surface that states it (21 spots across `README.md`, `docs/`, `keel-visual/` and `website/`), and a companion check compares the two *enumerated* command lists against the shipped set rather than against their own length — the half a count check cannot see.

### Changed
- **The release runbook's "Complete The Homebrew Formula" step is gone** (#1023): `docs/keel/release.md` said the step was "required on every release, and it can only be done after the tag exists", and gave the commands for doing it by hand. It is now "The Homebrew Formula (Nothing To Do)", describing what the release does instead and what to check if `brew upgrade` sees nothing. `docs/keel/homebrew-release-chain.md` is rewritten around "The contradiction, and how it was removed" — its "Still manual, and why" section and its interim cost (one hand-opened, hand-merged pull request per release) are deleted, not reworded, and a new section records the decision on the bot exemption.
- **`evidence_require_distinct_vendors` is tri-state and defaults on from TIER-2** (#1014): the knob existed and was off, so a "multi-agent" review could be three runs of the same model and the evidence gate would still pass it. Unset now resolves from the resolved risk tier — on from TIER-2 up, off below — while an explicit `false` is still honoured, which keeps the opt-out in a file a reviewer can read rather than in a default nobody sees. Reviewers already carry `vendor:` provenance on their verdicts per `ship.md` s7; a verdict without it now blocks the pre-merge evidence gate on a tier-2 change.
- **BREAKING for consumers who never set it: `evidence_require_distinct_vendors` is tri-state and now defaults *on* from TIER-2** (#1014). The knob existed and was off, so a "multi-agent" review could be three runs of the same model and the evidence gate would still pass it. Unset now resolves from the resolved risk tier — on from TIER-2 up, off below — so **a project that never touched this key gets a stricter pre-merge gate on its next upgrade**: every required review verdict must carry `vendor:` provenance and no two may share a vendor, or `keel evidence-verify` blocks with a `review-vendor-distinctness` finding. `ship.md` s7 has always instructed reviewers to carry that field, so a keel-driven run already satisfies it; a hand-posted verdict may not. **The escape hatch is one line** — `knobs.evidence_require_distinct_vendors: false` — which is still honoured exactly as before and keeps the opt-out in a file a reviewer can read rather than in a default nobody sees. keel's own `projects/keel.yaml` sets it, and says why.
- **Adapter prose stopped carrying vendor argv** (#1012): `ship.md` s4/s7, `implement.md` and `review-cycle.md` described the `codex exec` / `agy --print` / `claude -p` invocations, the prompt-delivery modes and the hosted-API call shape in prose. They now call `keel delegate run` and consume its JSON, and the sleep-and-poll advice is replaced by `keel delegate wait`. What stays in the prose is what only the orchestrator can decide — retry counts, the tier-3 refusal, the `secrets` scope, fall-back to the host agent. `tests/test_adapter_delegate_dispatch.py` sweeps every adapter source **and every generated surface** and fails if a vendor flag reappears: prose that restates a mechanic is a second source of truth the day it lands.
- **`keel.runner.CommandResult` carries `spawn_failed`** (#1012): the `OSError` path and a command that ran and exited 127 both report code 127, so classifying on the code alone could not tell "that binary is not installed" from "the tool ran and said 127" — which made the delegate's `missing-binary` branch unreachable. The runner now says which happened.
- **A delegate's answer is its stdout alone** (#1012). Reading `stdout or output` fell back to the stdout+stderr concatenation, so a CLI that exited 0 having printed only a login notice came back `ok: true` with the notice as its output — downstream, a diff to apply or a verdict to post. The concatenation is kept for the diagnostic tail of a *failure*, where both halves help.
- **`keel.runner.run_argv` accepts `stdin_text`** (#1012), so a delegate's prompt can be piped to a CLI instead of appended to its argv. The default stays `stdin=DEVNULL` — a gate left waiting for input in an unattended run is a hang, not a prompt — and #879's AST sweep now covers `subprocess.Popen` and the `_popen` seam too, so the detached delegate's spawn site is held to the same rule.

### Fixed
- **A review verdict that quoted the ship-assessment heading was not counted** (#1035): #1026 anchored *marker* classification to a comment's header line, but `evidence._is_ship_assessment` was left as a whole-body substring test — `SHIP_ASSESSMENT_HEADING in body or "keel ship —" in body`. It is consulted as an **exclusion** by both `_is_review_verdict_body` and `_is_jury_verdict`, so a genuine verdict — correct `keel.review-verdict.v1` header, correct `head:`, real substance — was silently dropped whenever its prose quoted `### 🚢 keel ship` or `keel ship —` while describing what was reviewed, and `evidence-verify` reported `missing: review-verdict-N` for a comment sitting on the pull request. That is the #1026 failure one layer over.
  - The assessment is now header-anchored too: the comment's first non-empty line must **lead with** the heading or with the CLI's own `keel ship —` banner (now named `evidence.SHIP_ASSESSMENT_BANNER`). The heading is a Markdown heading rather than a versioned `keel.*.v1` marker, so it cannot join `CLASSIFICATION_MARKERS`; it gets the same anchoring instead. The arming signal (`_has_trusted_ship_assessment` → `ship-assessment-comment`) and both exclusion sites keep sharing the one test, so they cannot drift apart.
  - Arming is unchanged for every real assessment: the workflow writes `### 🚢 keel ship` as the comment's first line, and a raw paste of the CLI summary leads with the banner. What stops arming the gate is a comment that only *mentions* the heading mid-sentence — which was never an assessment.
- **A verdict comment no longer cancels the assessment run** (#1037): `.github/workflows/keel-ship.yml` ran `pull_request` and `issue_comment` under one `concurrency` group keyed on the pull request number, with `cancel-in-progress`. `keel review --live` posts its verdict comments seconds after the push that started the assessment, so the comment run cancelled the still-running `pull_request` run — and a cancelled run's `keel ship (assessment)` / `keel evidence (verify)` check-runs stay `cancelled` on that head and cannot be deleted. GitHub reports the head as UNSTABLE and `keel merge` refuses on "CI failing" with every required check green; the standing workaround was `gh run rerun` on every pull request, dozens of times in one day (keel #1030/#1033, ai-jury #668/#670/#671/#672/#674).
  - The group now carries the event name — `keel-ship-pull_request-<n>`, `keel-ship-issue_comment-<n>`, `keel-ship-workflow_dispatch-<n>` (a dispatch with no `pr` input falls back to the ref: `keel-ship-workflow_dispatch-refs/heads/main`) — so no trigger can cancel another's run. Cancelling *within* one event stays on, though not because the cancelled run is always on a superseded head: `reopened`, a `synchronize` fired by a **base**-branch update and a force-push back to an already-assessed SHA all re-run `pull_request` on an unchanged head. What covers those is that the canceller is a run of the same event on that same head, so it republishes both job check-runs under the same names, and branch protection and `cli._dedupe_rollup` alike read the most recent check-run per name. A different event cancelling republishes nothing, which is exactly why that case broke.
  - The cost, paid knowingly: `keel review --live` posts three verdicts seconds apart, so two of those `issue_comment` runs are now cancelled inside their own group, and that event always runs from the default branch — so those cancelled `keel evidence (verify)` checks land on the default branch's tip. Visible in the UI, read by nothing that gates a pull request.
  - **Splitting the groups reintroduces the race the shared group prevented**, so the guard moved into `publish_check`: each run stamps the moment it read the pull request — taken immediately *before* `keel evidence-verify`, never after, since a run that started before a verdict was posted can still finish after the run that saw it, and in nanoseconds, since two racing runs are seconds apart and whole seconds leave the common case unordered — into the check-run's `external_id`, and declines to overwrite a check-run stamped later, logging a `Newer evidence verdict kept` notice instead. The stamp is read off the newest run under the gating name (`max_by`), not off whichever row the list endpoint returns first. This is a read-then-write guard, not a compare-and-swap — the check-runs API offers none — so it shrinks the window from the whole install-and-verify run to a single API round trip rather than closing it. The authoritative verdict is the one from the run that read the pull request last, which `docs/keel/github-actions.md` now states outright. A check-run carrying no stamp is still overwritten: freezing the gate on whatever it last said is the wrong direction for a run that has read newer state.
  - **A declining run exits 0 and does not replay its own verdict.** `publish_check` returns 3 rather than 0 when it declines, because "a newer run holds the check" and "this run published" are different outcomes. Conflating them meant an older `pull_request` run that correctly declined to overwrite a newer success still exited 1 on its own stale violation code, marking `keel evidence (verify)` FAILURE on the live head — and `cli._ci_rollup_state` scores FAILURE exactly like CANCELLED, so `keel merge` would have refused on "CI failing" all over again. The two reads can genuinely disagree: this workflow subscribes to comment `edited` as well as `created`.
  - `tests/test_evidence_gate_workflow.py` renders the group expression for a `pull_request`, an `issue_comment` and a `workflow_dispatch` context and fails if any two collide. Asserting that the expression *mentions* `github.event_name` would pass for a group that still renders one string per pull request — which is the bug. The declined path's exit code, the stamp's resolution and the `max_by` lookup are each pinned separately.
- **`make test` picks an interpreter instead of assuming one** (#1022): `PY ?= python3` handed the suite to whatever `python3` was on PATH. On macOS that is Xcode's 3.9, where every `X | None` annotation is a syntax error — about 110 import failures that read like a regression in the tree, and a `keel ship` build gate red for the same reason. The fix was tribal knowledge (`PY=<venv>/bin/python make test`).
  - `scripts/find_python.sh` now resolves the interpreter: the repository venv, then `python3.14 … python3.11`, then `python3`, taking the first that satisfies `import sys, yaml; assert sys.version_info >= (3, 11)`. The candidate answers that question itself — a resolver that parsed `--version` could still hand `make test` an interpreter without PyYAML. Nothing found is one line on stderr naming what to install, and exit 2.
  - `PY=` still wins, from the command line or the environment: the resolver only runs when `PY` is unset, so CI's `setup-python` interpreter is unaffected. `make doctor-python` prints the resolution. A machine with no usable interpreter fails on the first target that needs one, not at parse time, so `make clean` still works.
  - `keel doctor` grew a `python_toolchain` check reporting the interpreter the build gate will actually run on, its version, and whether PyYAML imports there. Always advisory: keel cannot know a red gate is *this* problem, only that the interpreter behind it would produce one.
- **`keel attribution` — one place the `agent:`/`model:` labels come from** (#1013): `keel attribution --vendor <v> [--model <m>] [--profile <name>] [--config <file>] [--json]` prints `agent_label`, `model_label` and `system` straight out of `agents.attribution()` / `agents.profile_attribution()`. `--json` emits the record itself, the same shape a delegate result's `attribution` block carries, so an adapter can consume it directly. With `--config` it refuses a vendor that is neither built in nor a configured `knobs.delegate_profiles` entry; without one it accepts anything, because the ledger carries values written by earlier runs.
- **A ship run now stamps its own provenance on the PR** (#1013): the new `keel.ship-provenance.v1` artifact — rendered by `artifacts.render_ship_provenance()`, exposed as `keel ship --json` → `result.artifact_bodies.ship_provenance`, posted with `keel post-comment --artifact ship-provenance` — records the run id, issue, head SHA and the implementer's attribution labels.
- **A review verdict that quoted the jury marker was counted as a jury verdict** (#1026): `evidence` classified a comment by testing `MARKER in body` against the *whole* body, so a `keel.review-verdict.v1` comment whose scope text mentioned the string `keel.jury-verdict.v1` — a reviewer saying what they checked — read as a jury verdict. Observed live: two review verdicts counted as `jury_verdict: 2, review_verdict: 0`, `evidence-verify` reported `missing: review-verdict-1, review-verdict-2`, and the review that happened was invisible to the gate.
  - Classification is now **header-anchored**: one pure `evidence.marker_in_header()` reads the marker off the comment's first non-empty line — bare or wrapped in an HTML comment, the only two shapes the renderers emit — and every classifier uses it (review verdict, jury verdict, closure, ship provenance, the review-marker and provenance arming signals, and the `keel.deferral.v1` comment the adapter posts). A marker further down is prose. This extends #868's top-block field anchoring from the `reviewer:`/`head:`/`vendor:` fields to the marker itself.
  - A header naming two different markers does not say which artifact it is: the comment is excluded from every count and reported as an advisory `malformed-evidence-comment` finding (`minor`, never blocking) instead of being counted for both or silently dropped.
- **The evidence gate disarmed itself exactly when review had not happened** (#1013): `evidence.gate_decision` recognised a keel run by its *branch name* first. A live run whose implementer named the branch `fix/2467-slug`, whose ledger lived in a per-run worktree CI could not read, and whose reviewer verdicts were never posted read as a non-keel PR — `enforced: false (no-ship-provenance)` — so the gate that exists to require review waved it through.
  - The gate now arms on a **trusted** comment carrying `keel.ship-provenance.v1` **before** the branch regex, and the arming order is documented in `gate_decision`'s docstring and in `docs/keel/evidence.md`. The branch pattern stays as a legacy fallback; every path that armed the gate before still arms it. Nothing was removed, and only a trusted author's comment counts, so an outside contributor cannot manufacture provenance.
- **Attribution labels were composed in prose, so the cross-check compared a guess to itself** (#1013): the adapter told the host to derive `agent:`/`model:` itself. On a live run it wrote `agent:gemini` / `model:gemini` on the PR **and** `gemini:gemini-3.8-flash-high` into the ledger; the two agreed, the vendor cross-check passed, and neither matched keel's own vocabulary (`agent:agy` / `model:gemini-3`).
  - `evidence-verify` gained a blocking `attribution-vocabulary` finding: it recomputes the expected labels from the ledger record's `actors.implementer` and refuses any `agent:`/`model:` label `agents.attribution()` could not have produced, naming the expected labels. It skips — never fails — when no ledger record is available.
  - `keel ship --live --append-ledger` warns when `actors.implementer` names a vendor that is neither built in nor a configured delegate profile. A warning rather than a refusal: the live ledger already carries such records, and a missing record is worse evidence than a flagged one.
  - `src/keel/adapters/commands/ship.md` (s4, s7, s11) no longer carries a label-composition rule — it calls `keel attribution` or consumes the delegate result's `attribution` block and uses the labels verbatim. A test greps the adapter to keep it that way.
- **"BLOCK — blocking findings present" now says what blocked** (#1007): a failed `on_fail: block` gate becomes a blocking finding, and the decision line reported it with a fixed string that named nothing. Read under a reviewer verdict saying "none blocking", it looked like a contradiction or a bug. The reason now names the gate(s) whose findings block — `blocking findings from gate(s): lint` — and the jury template's `remaining_risks` renders the same wording. The built-in jury gate's `jury:<reviewer>` sources collapse to the one gate they belong to, so a gating jury reads as one failed gate, not one per reviewer. A blocked verdict with no attributable source keeps the old text.
- **The Simulator's Play/Pause Glyphs Were Read Aloud** (#997): the toggle button's `⏸` / `▶` glyphs are decorative, and a screen reader announced them ("black right-pointing triangle, Run Swarm Simulation"). They are now wrapped in `<span aria-hidden="true">`, the same pattern the sibling reset button uses (#983), so the accessible name is the verb: Pause, Resume, Run Swarm Simulation.
- **The reference documented eleven commands that were not there, and three that do not parse** (#1019): `docs/keel/cli.md` had no section at all for `release`, `close-reconcile`, `dryrun-verify`, `scratch-dir`, `gc`, `canary`, `rollback`, `cost-report`, `adapter-status`, `update-adapter` or `sync` — a reference where reaching a command means reading `cli.py`. Each now has its signature, its flags, its exit codes and a worked example. Two of them document a behaviour that was previously discoverable only from the source: `keel release` exits 0 on `missing` as well as `released`, because releasing a lock nobody holds is the state the caller wanted; `keel canary --duration` is accepted and inert, so the doc says so instead of describing monitoring that does not happen.
  - The `swarm-*` signatures described flags that never existed. `keel swarm-plan <project.yaml> [issues...] … [--landing …] [--rebalance]`, `keel swarm-run … [issues...] [--rebalance]` and `keel swarm-land … --mode auto` are all rejected by argparse: issues are named with `--issues`/`--issue`, `swarm-plan` takes no `--root` (planning reads no repository state), and the landing mode is *derived* from the wave's diff map by `evaluate_wave_landing_mode` — there is no `--mode` to pass. `keel window` takes no `--root` either, which four adapter bodies told an agent to run verbatim. Corrected in `cli.md`, `swarm.md`, the four adapter sources and their generated surfaces.
  - `knobs.swarm_review_evidence` — the knob that decides whether swarm landings enforce the ship s10 review-evidence contract at all (#828) — existed in `project.schema.json` and in no configuration table. It is now documented in `configuration.md` (row + field detail), in `parameter-reference.md` under a new `keel swarm-land` section, and in `swarm.md` §4. `parameter-reference.md`'s `keel gc` row also claimed `--keep-activity` defaults to 200; `cli.DEFAULT_GC_KEEP_ACTIVITY` is 50.
  - `CHANGELOG.md`'s #1011 entry stated the provider-registry precedence as *project profile > registry > built-in*, inverted against `delegate.py`, `configuration.md` and its own #1012 entry three paragraphs above, all of which say **built-in > project profile > registry**.
  - The website said **16** `/keel` commands in `llms.txt`, `docs.html` (four places) and `content.js` (four places) while `index.html`, `home.js` and `docs/keel/commands.md` said 17. `swarm` made it 17. And `website/integrations.js`, whose own header promises "100% real Keel CLI commands", showed `keel ship … --delegate <name>` on six cards (`--delegate` is a `keel implement` / `/keel:ship` flag, not a `keel ship` one), a `keel evidence-verify` missing its required `--pr`, `keel cost-report .keel/project.yaml` (the command takes no config path — it reads `.keel/activity`), `knobs.skills:` (not a schema key; `knobs` is `additionalProperties: false`) and `pipx install keel`, which installs an unrelated PyPI project — the distribution is `keel-workflow`.
  - **A second pass over the surfaces the audit had marked accurate found four more.** `website/coverage.html` (×2) still said 16 `/keel` commands — it was outside the first sweep's file list. `docs/keel/models.md`, `cli.md`, `evidence.md`, `github-actions.md`, `integrations.js` and `swarm-simulator.js` cited retired model ids (`claude-3-7-sonnet-20250219`, `claude-opus-4-5`, `gpt-4o`, `o1`, `o3-mini`, `gemini-2.5-*`). The hosted-Anthropic examples move to current ids; OpenAI and Google get `<model-id>` and a pointer to `keel doctor --providers` instead of a reprinted vendor catalogue, which is what went stale in place. The backbone numbers (13 steps / 28 slots), the version pins and every `pipx`/`pip`/`brew` install command were re-checked against `model.py`, `__version__` and `pyproject.toml` and are correct.
  - **A third pass found the count wrong in three more places and short by one command in two lists.** `website/coverage.html`'s sidebar badge still said 16 (the first sweep pinned the badge pattern for `docs.html` and `index.html` only), as did `README.md`'s "**16 shipped commands**", `docs/keel/keel-visual.md`'s see-also line, `website/README.md`'s page table, and `keel-visual/README.md`'s "all 16 keel commands". Worse than the number: the README and keel-visual sentences each *enumerate* the commands and both omitted `swarm`, and a reader takes an enumeration as exhaustive. `website/README.md` also carried a hand-kept list of "the three static `16` spots" — stale on both halves — which is now replaced by a pointer to the test that knows all of them.
  - **`/keel:swarm` advertised two flags its own body never reads.** `--rebalance` and `--landing {batch,funnel,auto}` were in the adapter's `argument-hint`, on the site's command card and on its flag chips; nothing in `swarm.md` branches on either, and the landing mode is derived from the wave's diff map. Both are dropped, `--visual` is now bound to the dashboard step it was presumably meant to gate, and `website/params.js` — whose first line claims it is "generated from src/keel/adapters/commands frontmatter", with no generator in the tree — is back in sync with that frontmatter.
  - `berkayturanci/keel-action@v1` was re-verified rather than rewritten: the repository, the `v1` tag and its `action.yml` all exist, so the references in `integrations.js`, `docs/keel/github-actions.md` and the #763 entry are correct. `KEEL_CHECK_EXTERNAL=1 python -m unittest tests.test_external_promises` passes against them.

### Performance
- **The swarm conflict graph stops paying for path normalization O(N²) times** (#1000): `build_swarm_plan` now normalizes each issue's predicted paths once and asks a boolean `scopes_have_conflict` that returns at the first overlap, instead of collecting and sorting every overlap per pair. 200 issues × 10 paths: 3.3 s → 0.11 s when a shared file lets the set fast path answer most pairs; about 2× on fully disjoint scopes, where every pair still runs the loop. Identical plan either way.
  - One matcher. The rules `paths_intersect` applies now live in a single normalized helper that both it and the plan call, so the boolean cannot drift from the tuple it short-circuits — the first cut of this change carried its own copy of the rules without the normalization step, and disagreed with `scopes_intersect` on un-normalized paths and on the empty string. Pinned by a test that asserts the two agree across those cases.

## [1.19.3] - 2026-09-02

### Added
- **The Homebrew release chain, written down** (#991): `docs/keel/homebrew-release-chain.md`. The runbook now records the tag-to-tap sequence, every guard, the known failure modes, and the remaining repository settings that require attention.

### Fixed
- **Merge Evidence Is Verified in the Wrong Phase** (#999): `keel merge` now verifies only pre-merge evidence before landing, while `keel review --verify` continues to enforce post-merge closure requirements. The packaged Claude/plugin/shared-skill adapters and regression tests use the same phase contract.
- **The v1.19.2 Homebrew Formula Checksum Was Updated After Tagging** (#989): synchronized `Formula/keel.rb` with the published v1.19.2 archive so the source tree and release artifact agree.

## [1.19.2] - 2026-08-27

### Fixed
- **The Formula Named 1.19.1 While Carrying 1.19.0's Digest** (#982): the tap downloads the url, hashes it, compares — and refused to publish, so `brew upgrade` could not see the release. Restored to these notes: the entry was deleted by #979, a security change cut from a base predating #982, which silently reverted part of it while its own description mentioned only a read size limit.
  - `make release-bump` moves the url and cannot know the digest — the tag archive does not exist until the tag is pushed — so the two are correct at different moments by construction.
  - The guards survive in `tests/test_release_docs.py` (offline: the url must name the current `__version__`) and `tests/test_external_promises.py` (online, wired with `KEEL_CHECK_EXTERNAL=1`: download the artifact and re-hash it). #979 removed a duplicate of these, not the guards themselves — checked before writing this down rather than assumed from the diff's shape.
- **The Last Step In The Formula Chain Was Still A Person** (#986): #984 made the release open a pull request with the post-tag digest instead of printing it. Opening it is not the goal — the tap only recovers when it *merges*, and until then it keeps failing on a schedule with the release itself long since green.
  - The pull request is now armed to land on its own once CI has re-verified the digest against the published artifact. `main` requires status checks and no approvals, so nothing is bypassed: the wait removed is the one between "green" and "someone noticed".
  - Best-effort. If the repository has auto-merge disabled the step prints a notice and the pull request waits for a person, exactly as before.
  - Asserted separately from `gh pr create`, since the two are one line apart and deleting the second leaves a change that still reads as complete.
- **A Release Left Its Own Formula Stale And Asked Someone To Fix It** (#984): the Homebrew formula names the sdist url and sha256 of the release being cut, and neither is knowable until the tag exists. `publish.yml` computed the correct digest after tagging and then emitted a `::notice::` with it, leaving the edit to a human.
  - Nobody made that edit for 1.19.1. The tap pulls from `main` on a schedule, found a 1.19.0 digest under a 1.19.1 url, and refused every sync for a day — one failure email per hour, in a different repository, long after the release itself had gone green.
  - The workflow now applies the digest and opens a pull request with it. A pull request is the only write to a protected `main` that can succeed, and unlike a notice it is a thing on a list rather than a line in a log nobody reads after a successful release.
  - The sibling repository failed the same week from the opposite direction — it *did* try to commit, with `git push origin HEAD:main || true`, and branch protection swallowed the refusal. Both repos now take the same route.
  - `tests/test_publish_formula_followup.py` pins the shape: the step must edit the formula, must open a pull request, must not push to `main`, and its job must carry `pull-requests: write` — a permission that sits forty lines from the step that needs it and fails only after the tag has been cut. Asserted over the step's code with comment lines removed first: the surrounding prose discusses the direct push it no longer performs, and a plain grep matches that discussion happily.

## [1.19.1] - 2026-08-25

### Performance
- **Tuple Startswith for Blocked Env Prefixes** (#924):
  - Used native tuple overload in `config.py` `_validate_delegate_profiles` (`startswith(BLOCKED_ENV_PREFIXES)`) instead of generator allocation.
- **Pricing Table Sorted Once, Not Per Call** (#930):
  - Hoisted `sorted(MODEL_PRICING, key=len, reverse=True)` out of `normalize_model_name`'s match loop into `_PRICING_KEYS_BY_LENGTH`. The order is a property of the table, not of the string being matched.
  - **Honest sizing:** 1.14 → 0.46 µs per call, but `keel cost-report` on this repo's real ledger (10 records; it normalizes twice per record, not once) goes from 0.032 ms to 0.018 ms inside a command whose wall clock is ~200 ms. That is 0.006% — a rounding error. It is worth merging as a loop-invariant cleanup, not as a throughput fix, and nobody should plan around the µs figure. The break-even against one Python interpreter start is roughly 300,000 ledger records.
  - The order is load-bearing rather than cosmetic: eleven keys contain a shorter one, so matching shortest-first would price `gpt-4o-mini` as `gpt-4o` — a cheap model billed at the expensive rate. The guard is derived from the table, so a new nested key is covered the day it lands.
  - The regression guard is behavioural, not textual. Its first version walked the AST for a call named `sorted`, which three ways of re-sorting per call slipped past — a helper, `list.sort`, and `builtins.sorted`, one of them measured at 3.2× the hoisted cost with the test still green.

### Security
- **A Hostname Reached What Its Literal Spelling Could Not** (#969): `endpoint_issues` classifies the host *as written*, so a name carries no address and both the metadata and the private checks answered "no".
  - With `KEEL_ALLOW_REMOTE_ENDPOINT=1` and the internal opt-in unset: `http://10.0.0.5/` refused, a name resolving to `10.0.0.5` allowed. That is exactly the boundary the refusal text draws — *"permits reaching out, not reaching in"* — and a name collapsed the two opt-ins into the first.
  - Found by the ai-jury panel run on #958 (codex, `[major]`). Worth recording that the panel was **1 of 3** — `claude`'s session had expired and `agy`'s launcher broke headless invocation — so one working reviewer found what three careful readings had not.
  - The policy lives in `config.resolved_address_refusal` and the enforcement at the connection edge, which is the split `AGENTS.md` asks for: resolving inside `endpoint_issues` would have made `keel validate` require DNS and still missed rebinding.
  - **The connection is pinned to the address that was checked.** Re-resolving afterwards is the vulnerability rather than a detail of it: a name answering a public address to the check and a private one to `connect` passes both. Implemented by substituting `HTTPConnection._create_connection` — an instance attribute assigned in `__init__`, so a class-level override silently does nothing, which the first cut of this did.
  - Loopback is still allowed for a literal `localhost`, because that is the default local-model-server case a blanket refusal would break; a *name* resolving to loopback is held to the internal opt-in like any other reach-in.
  - Mutation-tested six ways. The rebinding test needed rewriting: asserting the resolver **call count** passes against no pinning at all, because an unpinned `connect` re-resolves through the OS resolver a fake never sees. It now asserts on where the socket went, using an RFC 2606 `.invalid` host and a real listener.
- **Two Hardenings Each Shipped Half, And One Half Was Bypassable** (#929, residuals of #865/#866):
  - **A live bypass of the guard that did ship.** `http://2852039166/latest/meta-data/` — the decimal form of `169.254.169.254` — was **allowed**. `ipaddress.ip_address` accepts only the dotted-quad spelling, so the metadata check raised, fell through, and an HTTP client resolved it straight to the metadata service. Octal (`0251.0376.0251.0376`) and hex (`0xA9FEA9FE`) did the same. The host is now normalised through `socket.inet_aton` — the forms a C resolver accepts, which is what the client ultimately calls — so the check asks the same question the request will.
  - **RFC1918 blocking, specified in #866 and never implemented.** With `KEEL_ALLOW_REMOTE_ENDPOINT=1`, `10.0.0.5`, `172.16.5.9` and `192.168.1.10` were all reachable from a config-supplied endpoint. They now need `KEEL_ALLOW_INTERNAL_ENDPOINT=1`: permitting keel to reach *out* is not the same decision as permitting it to reach *in*, and the error names the narrower opt-out rather than the one already set. Metadata addresses stay refused under **both** opt-ins.
  - **The `api_key_env` allowlist, specified in #865 and never implemented.** Only the denylist shipped, so `VAULT_TOKEN`, `AZURE_CLIENT_SECRET`, `KUBECONFIG`, `GITLAB_TOKEN`, `DATABASE_URL` and `STRIPE_SECRET_KEY` were all accepted — each would have travelled as `Authorization: Bearer` to a config-named endpoint. A profile may now name only a provider key (`OPENAI_API_KEY`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `TOGETHER_API_KEY`, `OPENROUTER_API_KEY`, `LITELLM_API_KEY`, `VLLM_API_KEY`) or its own `KEEL_DELEGATE_KEY_*`.
  - The allowlist is the measure that matters and #865 said so: eleven names and six prefixes cannot enumerate every credential a runner holds, and the next secret to appear in CI is one nobody added. The denylist stays as defence in depth, checked first so a sensitive name still fails with a message about *that* credential.
  - Both issues are pinned as **tables** of accepted/refused names and hosts — every row measured against the code as it stood, each `ACCEPTED` row a hole — so a future partial implementation fails rather than closes. Existing tests that accepted an arbitrary well-formed variable name are updated with the reason.

### Fixed
- **Decorative Reset Glyph Announced To Screen Readers** (keel#980, reapplied): the simulator's reset button rendered `⟳ Reset`, so assistive technology read the arrow as an unnamed symbol before the useful word.
  - Now `<span aria-hidden="true">⟳</span> Reset` — the glyph is suppressed and the accessible name survives. Hiding the button itself, rather than the inner span, would have removed the control from assistive technology entirely.
  - Reapplied from current `main` rather than merged: the original branch had been cut from an older base and, against `main`, its diff was 64 lines of pure deletion — it would have removed #982's Homebrew formula guard without touching the glyph at all.
- **Duplicate Changelog Sections In Three Released Versions** (#975): `1.12.0`, `1.10.0` and `1.6.4` each repeated a section, shipped that way across seven releases to PyPI's description and the GitHub Release notes.
  - Nothing was lost — entries were inserted above the previous top section, so those blocks grew alternating headings and a reader looking for "what changed" found two lists of the same kind.
  - Consolidated across every version block. The entry count is identical before and after (283 lines beginning `- `), which is the check that separates a merge from a deletion; the diff alone does not.
  - `tests/test_changelog_sections.py` asserts no version repeats a section, that headings come from a known vocabulary — `### Fixes` is silently a different bucket from `### Fixed` — and that `Unreleased` is non-empty, since a release cut from an empty block is blank.
  - The vocabulary is fixed rather than derived from the file, which would assert only that the file contains what it contains. It covers Keep a Changelog's six plus the four this project uses (`Performance`, `Refactored`, `Tests`, `Companion`).
  - Found while checking release readiness; `## [Unreleased]` was already clean, so this fixes history and adds the guard rather than unblocking anything. Same defect and fix as ai-jury#627.
- **A Failing Gate Collected 20 Lines And Printed One** (#973): `runner._tail` deliberately captures the last 20 lines of a failing command; `cli.py`'s renderer took `.splitlines()[0]` and dropped nineteen of them at the last step.
  - What an operator saw when `make test` failed in CI: `[major] build: build failed (exit 2):     self.cleanup()` — a source line from `tempfile`'s finaliser, a stray `ResourceWarning`, not the failure. The `FAIL:` line and its traceback were gathered on purpose and deleted a line before reaching the reader.
  - The exit code cannot stand in for it either: GNU make returns **2** whenever a recipe fails, whatever the recipe returned, so `exit 2` does not even distinguish "a test failed" from anything else. The discarded output was the only diagnostic there was.
  - Same failure this repository has been fixing all week, one layer up (#927, #953, #961, #614): a red that cannot be read teaches the reader to re-run rather than investigate. It cost a real investigation — #972 could not be diagnosed from its own CI output, and the first hypothesis drawn from that single line was wrong.
  - The headline still leads on one scannable line; the rest is printed beneath it.
  - Tested on a line that is **neither the first nor the last** of the output. "Shows more than one line" is satisfied by an off-by-one no better than the bug, and a single-line message passes against old and new alike — so neither is the property. A counterweight pins that the single-line case gains nothing. Both mutations, including the last-line-only variant, fail.
- **`ruff-format` Rewrote 127 Files Under Anyone Who Installed The Hooks** (#966): the tree was never ruff-format-clean while `.pre-commit-config.yaml` declared `ruff-format` as a **rewriting** hook.
  - A contributor who installed the hooks and committed a one-line change got 127 unrelated files rewritten into it — not a failure they could read and fix, a silent rewrite already staged by the time they looked. CI stayed green throughout, having only ever run `ruff check`.
  - The tree is now formatted and `ruff format --check .` runs in CI, in its own job rather than nine times across the test matrix: formatting is a property of the tree, not of the interpreter or the OS.
  - **CI's formatter is pinned to the hook's version**, and `tests/test_ruff_pin.py` asserts they match. The dev extra is an unpinned `ruff`, which is right for `check` — new rules are worth picking up — and wrong behind a format gate, which would then go red the day ruff changes its style. This nearly bit at once: the first cut formatted with 0.16.3 while #918 had already moved the hook to 0.16.4. They agree on this tree; nothing said so.
  - **Verified at the syntax-tree level, not by the suite passing.** Of 127 changed files, 125 parse to byte-identical trees. The two that differ each gain a leading space in a docstring that begins with `"`, which `""""` would otherwise make ambiguous. That is the complete list of semantic differences.
  - The formatter and the linter disagreed once: reflowing pushed a string literal past `line-length = 100`, which `ruff format` cannot break. `tests/test_orchestrator.py` splits it by hand — the only non-mechanical edit here, which matters because everything else is verifiable by re-running the tool.
- **Tier-3's Jury Is Advisory; Reviewers Are Not** (#965): tier-3 auto-enabled a *gating* jury verdict — a paid cross-vendor panel run — and that requirement blocked four PRs at once.
  - Three of the four were a two-line hash bump (#920), a 352-hash bump (#919), and a mechanical reformat whose correctness argument is a syntax-tree diff no panel can check better (#967). Only #958, new parsing logic on untrusted input, was a change where three independent cross-vendor readings differ informatively.
  - Any repository-wide change is tier-3 by construction, since `tier3_globs` lists `orchestrator.py`, `config.py` and `extensions.py`. So the rule as written required a paid run for changes that cannot avoid touching those files however little they do to them — which is not what #787/#779 were deciding when they raised those paths.
  - `--no-jury` at the call site drops **only** the jury verdict. Tier-3 still requires three distinct reviewer verdicts, and a jury is still the right call where cross-vendor disagreement is informative — run deliberately, per PR, as it was for #958.
  - The risk is not the decision but the next edit: `--no-jury` sits one word from `--reviewers 1`, `--dry-run` or `--deferral all`, each of which would hollow the gate out while the line still reads like a gate. `tests/test_jury_is_advisory.py` pins the disarm at exactly one flag wide, asserting over comment-stripped lines because every flag is also described in a comment directly above it.
  - Mutation-tested five ways: removing `--no-jury`, removing `--require-armed`, and adding `--reviewers 1`, `--dry-run` or `--deferral all` each fail.
- **Every dependabot PR Was Unmergeable** (#963): `PR has a real description + linked issue` had no bot exemption, so three dependency bumps — `ruff-pre-commit`, `lxml`, `idna` — sat blocked with no route forward.
  - The lint accepts a real `#N` **or** the explicit `no issue` opt-out. dependabot writes neither: its body is a changelog excerpt generated by GitHub, and it cannot be taught to. The opt-out existed but was unreachable for the one author that needed it, which turns a supply-chain update into a manual chore and a manual chore into a backlog. `idna` and `lxml` releases are frequently security fixes.
  - The exemption keys on `user.type == "Bot"` — GitHub's own classification — not on the login. A list of known bot names goes stale on the second bot, and the reserved `[bot]` suffix is a naming convention standing in for the fact it encodes.
  - Worth recording for anyone checking this by hand: `gh pr view --json author` renders the account as `app/dependabot`, while the event payload the workflow reads carries `dependabot[bot]` with `type: Bot`. A first cut keyed on the login looked wrong when tested through the CLI and was right; keying on the type makes the discrepancy irrelevant.
  - The 65 lines of lint logic moved out of the workflow heredoc into `.github/scripts/pr_description_lint.py`, which is what makes the exemption testable at all — and matches the pure-core/thin-I/O split `AGENTS.md` already requires. The rules had no tests before; they have sixteen now.
  - Asserted **in both directions**, as the issue required, against the real PR #918 payload: the bot passes, and the identical body from a human is still refused. Plus the wiring the unit tests cannot see — that the workflow actually passes `user.type` through, without which the exemption is dead code.
- **The `nosec` Gate Scanned The Working Tree** (#961): any locally-built artefact directory turned it red, so a contributor who had run a build saw 49 failures with nothing to do with their change.
  - Every entry was under `./build/lib/keel/`, a gitignored pre-cleanup snapshot carrying `# nosec B105` on lines the current source no longer has. CI never has `build/`, so the test was green there and red only for some contributors — the worst shape, because it reads as *their* change having caused it.
  - The test already computed the tracked source list for its vacuity guard and then did not use it, running `bandit -r .` over the whole tree. The gap had been papered over one directory at a time — `tests`, `.venv`, `venv`, `node_modules`, `site-packages`, plus a post-filter for `.claude/worktrees` — and `build/` was simply the next one nobody had listed. bandit is now handed `sources`, which makes those exclusions and the post-filter unnecessary rather than longer, and drops the run from 27 s to 1.7 s.
  - Verified with `build/` **present**, which is the condition; a clean checkout proves nothing here.
  - Detection is unchanged, and pinning that took three attempts: bandit does not register a `# nosec` on a line it never flagged, so two plausible mutations passed and were not evidence. The faithful one is the historical regression — restoring `# nosec B105` on `consent.py`'s three `"secret_*"` entries — and it fails as it should.
- **Every Hosted-API Run Was Labelled With Its Transport** (#955): `agents.model_base` read the first colon by position, so `anthropic-api:claude-opus-4-5`, `openai-api:gpt-5` and `google-api:gemini-2.5-pro` all landed on their PRs as `model:<vendor>` — identically, whichever model ran.
  - The `model:<base>` label is the durable per-run attribution an audit reads back, so what it recorded was the transport. Nothing looked broken: the label was well-formed, non-empty and stable. The only signal was that `cost.normalize_model_name` resolved the same ids correctly — two readings of one string, disagreeing.
  - `agents.strip_transport` is now the single definition of which prefixes name a transport, derived from `API_VENDORS`/`LOCAL_VENDORS` rather than hand-listed, and shared with the pricing path so the two cannot drift apart again. A colon *not* preceded by a transport belongs to the model — an Ollama `:tag` or a Bedrock `:0` revision — and is left for the caller.
  - The Ollama forms are the ones the old positional rule was **right** about, so they are pinned in the same file: `qwen2.5:7b` and `ollama:qwen2.5:7b` both still label `model:qwen`. A fix that traded one wrong label for another would not be one.
  - Pricing is deliberately *not* unified here. `ollama:`/`local:` still collapse to the transport for cost, because `MODEL_PRICING` prices local inference at `0.00` and has no entry for the model — the one case where the label and the price answer different questions and both answers are right. The alternative was pricing an open-weight family at zero everywhere, which is false since the same families are also sold hosted. A new test asserts a local run still costs zero, on the raw id the report actually reads.
  - This let `tests/test_cost_vocabulary.py` drop the `-api` exemption it carried with a comment naming #955 as the reason.
  - Mutation-tested three ways: restoring the positional split fails 9 tests, dropping the API vendors from the transport set fails 13, and removing the local free-tier shortcut fails 5.
- **A Busy Window Read As Clean Because The Page Never Reached It** (#937):
  - `github.prs_merged_between` returns `None` — the same signal an outright failure gives — when the page it read came back **truncated**. `gh pr list` returns the *newest* N merges and the window filter ran afterwards inside `--jq`, so on a repository where more than N pull requests merged since the window closed, none of the window's merges were in the page, the filter matched nothing, and an empty list read as "nothing overtook this merge".
  - Same rule as #933 — *a gate that could not perform its check must not report a pass* — reached through a read that **succeeded** while seeing only part of the answer. #936 closed the door where a failed read read as clean; this is the other one.
  - Live rather than latent: this repo has ~500 merged pull requests, and `docs/keel/cli.md` documents exactly the shape that trips it — a retrospective `keel verify-merge --pr 543` on a pull request from a previous month.
  - Truncation is detectable because the page is newest-first: a **full** page whose oldest entry still merged at or after the window opened never reached back far enough. A short page saw everything there was, and a full page that does span the window is trusted — both pinned by tests, since "full" alone is not truncation.
  - Not paginated, deliberately. On a repo this size the retrospective case would walk hundreds of pull requests to answer one question; saying "I could not see that far" is honest and cheap, and raising `MERGED_PAGE_LIMIT` stays a separate tuning decision.
  - The window filter moves from `--jq` into Python, because the truncation check needs the raw `mergedAt` values that `--jq` had already discarded. `_ints` goes with it: `prs_merged_between` was its only caller, and a helper nothing calls is dead weight in a module about reading carefully.
- **The Evidence Gate Counted Verdicts Without Reading Them** (#926):
  - `evidence.verdict_substance` refuses a review verdict that names nothing concrete, and the gate reports it as a **hold with a reason** rather than a silent pass.
  - The record it answers: across 41 PRs merged in one week, 34 carried verdicts, three reviewers posted exactly 25 each, **75 of 75 passed**, all opening `Reviewed <PR title>: <affirmation>`, and 37 of 41 PRs were single-commit — no review led to a change. The gate verified that verdicts *existed* with the right marker, head SHA and distinct reviewer ids; a verdict engaging with nothing was indistinguishable from one that caught a blocker.
  - Two mechanical requirements, both content-agnostic beyond structure: an **anchor** (a path, a `path:line`, a backticked symbol, a called identifier) or an explicit "checked …" clause; and **novelty** against the PR title, since prose that is the title restated survives the anchor test whenever the title happens to contain a path.
  - **A genuinely clean review stays expressible.** "Checked X, Y and Z; found nothing" passes. Forcing a clean review to invent a file reference would make the check worse than nothing — it would train reviewers to paste a path.
  - The check says nothing about whether a review was *good*. It cannot, and trying would make the gate a critic. It distinguishes a review from a receipt.
  - **The finding behind the finding:** keel's own `render_review_verdict` defaults to "Full changed-file diff and relevant contracts" and "Findings: none", which names nothing — so the template every reviewer was handed *was* the receipt shape. Its docstring now says so, and the default no longer satisfies the gate.
  - A rejected verdict is named in the findings with the reviewer key and the reason. Dropping it silently would have surfaced as "missing required evidence: review-verdict-2", sending the operator to look for a comment sitting right there.
  - A reviewer who posts a thin verdict and then a real one is accepted: the later comment is the review, and holding on the earlier one would make correcting yourself impossible.
  - `pr_title` reaches the gate from the PR object the live fetch already reads — no extra API call — and defaults to empty for fixture-driven runs, where the anchor half still applies.
  - The three verdicts quoted verbatim in #926 are the test calibration: a check that does not refuse those refuses nothing.
- **The Windows Test Job Reported Success While Seven Tests Failed** (#953):
  - `Test + coverage gate` now runs under `shell: bash` on every runner. The Windows default is PowerShell, whose script exit status is that of its **last** statement — so a failing `coverage run` was masked by a succeeding `coverage report`, and the job reported success with `FAILED (failures=6, errors=1)` in its own log. It had been doing that long enough for seven real failures to accumulate behind it.
  - The same masking was in `test-visual`, which also runs the Windows matrix and also pairs `coverage run` with `coverage report`. keel-visual's own 100 % gate had the same hole.
  - **A configured path is now root-anchored on every platform.** `Path("/tmp/x").is_absolute()` is `False` on Windows, so `/tmp/…` slipped past "must be relative to the project root" in `checkpoint`, `activity` and `ledger` — three copies of the same check, one shared `workspace.is_root_anchored` now. The config is the same text wherever it is read; `/tmp/x`, `C:\tmp\x` and `\\server\share` are all anchored.
  - The action-pin guard labels paths with `/` rather than `str()` of a relative path, which yields `\` on Windows and labelled the same file two ways depending on the runner.
  - The Codex hook's executability is asked of `git ls-files -s` (`100755`) instead of `stat().st_mode & S_IXUSR`, which Windows does not model. What matters is the mode git records and clones, and that answer is platform-independent.
  - `test_reports_extension_problem` uses a real temporary directory instead of the literal `/tmp`; the installer test invokes its script through `bash` rather than executing a `.sh` as a program (`WinError 193`).
  - A `--live` consent test cleared the whole environment, which is survivable on POSIX and not on Windows, where losing `SYSTEMROOT`/`PATH` breaks subprocess creation. Platform essentials are preserved while every `KEEL_*` is still removed.
  - Found on the way: `test_activity` and `test_checkpoint` used `unittest.mock.patch` without importing `unittest.mock`, so they only passed when some earlier module had imported it — an order dependency that surfaced the moment those files were run alone.
  - **keel-visual crashed on a Windows console.** Removing the mask from `test-visual` surfaced it: every renderer emits box-drawing characters and status glyphs, the Windows console default is cp1252, and `keel-visual dash` died with `UnicodeEncodeError` before printing anything. The CLI now asks its output stream for UTF-8 with `errors="replace"` — a terminal that cannot draw a glyph should show a placeholder, not take the command down — and leaves streams that cannot be reconfigured alone.
  - `tests/test_workflow_shells.py` asserts the general form: a multi-command `run:` block in a job that can run on Windows must declare its shell. Scoped to Windows-capable jobs deliberately — 13 of the repo's 18 blocks are `ubuntu-latest`, where bash with `-e` is the default and the rule would be ceremony.
- **`keel cost-report` Priced keel's Own Runs At 5 % And Called The Difference Savings** (#941, #942, #943, #944):
  - Four findings in one function, fixed together because each one alone leaves the report wrong in a different direction.
  - **The pricing table and the attribution convention now share one vocabulary** (#942). keel writes a versionless `model:<base>` label — `opus-4-8`, `sonnet-4-5` — and none of those appeared in `MODEL_PRICING`, whose keys are 2024-era product names. Every keel run therefore priced at the `DEFAULT_FALLBACK_PRICE`. `normalize_model_name` now resolves through `agents.model_base`, the same function that writes the label, plus a `MODEL_ALIASES` map from those bases onto tiers the table already prices.
  - **The aliases add no new prices.** An alias says "this label names that tier" — a naming fact, and checkable. Whether a tier's *numbers* are current is a separate, operator-owned question (#942's "refresh the keys"), and inventing figures would bury it under a plausible-looking table. Guessy cross-tier aliases were tried and removed: the first draft resolved `gemini-2.5-flash-lite` to the *pro* price, 8× its own.
  - **Re-hosted ids are unwrapped** (#941). The old code split on the first `:` and kept the right side, so a Bedrock id `anthropic.claude-3-opus-20240229-v1:0` normalised to `0` — a total loss of the model name, not an approximation. Bedrock, Vertex and OpenRouter forms all resolve now.
  - **Matching is exact, not substring** (#943). `o1-mini` resolved to `o1`: a 13.6× overcharge on a widely used model, and one longest-first ordering could never fix, since `o1` is a key colliding with a model string that is not one. A model the table does not name now reports as unpriced rather than borrowing a sibling's price.
  - **An unpriced run is reported as unpriced** (#944). A missing `model` used to default to `gemini-2.5-flash`, one of the cheapest entries — so *missing attribution read as maximum savings*, on a repo whose own ledger has `model: None` for every record. Unpriced runs are counted, excluded from the savings figure, and named in the rendered report.
  - **Savings compare the same set on both sides.** `benchmark − actual` now covers exactly the priced runs; benchmarking a subset against a total would understate rather than inflate, which is safer and still wrong.
  - #930's `_PRICING_KEYS_BY_LENGTH` is removed with the substring scan it ordered. Exact resolution makes the property it protected hold by construction, and its tests are re-pointed at the outcome — a constant nothing reads is a comment pretending to be a guard.
  - Found on the way and filed as #955: `agents.model_base` labels every hosted-API run with the *vendor* (`model:anthropic-api`), not the model, because it reads the colon as an Ollama tag.
- **Four Fixes Shipped With Nothing To Notice Their Removal** (#931):
  - From a mutation audit of the 2026-08-19..24 batch: each landed fix was reverted in a scratch copy and the relevant tests re-run. These four survived, which means they would leave silently — not hypothetical, since #934 documents exactly that happening to #811.
  - **`swarm_runtime.default_runner` stdin** (#879 → #899): pinned directly, and as a rule over the whole package — every place keel starts a subprocess must pass an explicit `stdin`, in either form it is written (`subprocess.run(...)` or the injected `_run` seam). Matching only the first form covers exactly one site, which the vacuity guard caught on the first attempt.
  - **`GateRunner` alias** (#876 → #896): the duplication is removed rather than pinned. `runner` now re-exports the definition `gates` owns, so there is nothing left to drift; a second assertion reads the source and refuses a re-declaration outright.
  - **Copy-button aria-label** (#916 → #917): pinned by the mechanism that makes it work — the label is restored from a captured original, and the pending timer is cleared *before* the next is scheduled. These assertions are **source-level, not behavioural**: there is no DOM harness and no JS runtime among this project's dependencies, so the docstring says so rather than letting a green tick imply otherwise. #917's body claimed "100% test coverage" for a `.js` file the Python coverage run does not measure at all.
  - **`swarm-land` dry-run exit contract** (#871 → #891): decided rather than documented around. The dry-run arm hardcodes `failed_clusters=()`, so a held cluster is the only route to `partial_failure` there — and it returned 0 while `--live` returned 1 for the same wave. Both modes now share one rule: 0 only when the wave would land clean. A preview exists so a caller can gate on it, and "this wave would be held" is not a pass.
  - The CHANGELOG line that claimed non-zero for `swarm-land` is corrected in place rather than left beside the fix, and a test asserts the old wording is gone.
- **Evidence Gate Reported Green While Waiting** (#928):
  - `keel-ship.yml` now publishes the gate's verdict as a `keel evidence (required)` check-run against the PR head, instead of leaning on the job's exit code — a job that exits 0 concludes green, which is how a check named for the evidence chain reported success with zero verdicts posted.
  - The waiting state is published as an **incomplete** run, not as a conclusion. #829 specified `neutral` on the belief that it still blocks; GitHub's branch protection accepts `successful`, `skipped` *and* `neutral` as satisfying a required check, so neutral would have reproduced the same defect. An incomplete run blocks the merge and still renders yellow rather than red.
  - Renamed the verification job to `keel evidence (verify)`. Actions names a job's own check after the job, so leaving it as `keel evidence (required)` would have put two same-named checks on one commit, indistinguishable to branch protection.
  - Publishing failures now fail the step in every state rather than only the waiting one — with one deliberate exception for pull requests from a fork, below, where publishing is impossible rather than broken: on a pull request from a fork the token is read-only whatever `permissions:` declares, and a gate that could not deliver its verdict has not delivered a pass.
  - `workflow_dispatch` runs resolve the PR's real head instead of falling back to the dispatched ref, which would have stamped a verdict onto the default branch permanently.
  - The check is **upserted**, not re-created: `POST /check-runs` has no upsert on (name, head SHA), so every run would stack another check under the gating name with at least one permanently incomplete. The workflow looks it up first (`--method GET`, since `-f` alone switches `gh` to POST and 404s) and `PATCH`es an existing one.
  - The workflow re-runs on `issue_comment`, which is what a verdict *is*: `keel post-comment` calls `POST /issues/{n}/comments`. Without a retrigger, posting a verdict fired no event and the incomplete check was never revisited — and the only self-service alternative, a new commit, changes the head SHA and invalidates the verdicts that would have resolved it. (`pull_request_review*` reads tidier and fires never: across twelve merged PRs here, all 33 verdict markers were issue comments.)
  - The evidence job's `if:` names the events it runs on. `github.event.inputs.pr != ''` is `null != ''` off `workflow_dispatch`, and GitHub coerces both to 0 — so the job was silently skipped on every other event.
  - A `concurrency` group, since two runs on one head would both GET-then-PATCH the same check and the slower one would win — routine once a comment can trigger a run.
  - Fork detection compares `head.repo.full_name` to the repository. `head.repo.fork` means "the head repo is itself a fork of something", which is true for every same-repo PR in a downstream fork of this template.
  - A fork pull request cannot publish at all (its token is read-only whatever `permissions:` declares). Rather than leave every fork contribution red with no route forward, the verify job's exit code carries the verdict there — green only for a real pass.
  - `docs/keel/evidence.md` and `docs/keel/github-actions.md` describe the shipped behaviour, and `tests/test_evidence_gate_workflow.py` asserts each `case` arm's own status and conclusion so a swapped or hardcoded verdict cannot pass.
- **A Rebased PR Could Never Be Merged Again** (#945):
  - `keel ship --live --append-ledger` accepts `--capture-status not-run` for a run that never reached capture. It records gates with **no** capture marker, which is what a rebased PR needs: gates are keyed on `(pr, head_sha)` so a new head requires a new record, but `ledger.existing_capture_marker` refuses a second record carrying a marker for the same PR — and every previously accepted status produced one. Both guards are correct; the record simply had no way to satisfy one without violating the other, so `keel merge` reported `gates-sha: no-match` with no CLI path out.
  - The sentinel is deliberately **not** a member of `capture.STATUSES`. Those are outcomes of a capture that happened; a fourth would let `capture.parse_marker` accept a marker asserting a capture nobody performed. It resolves to `None` at the CLI boundary, which `capture.record_marker` already renders as `marker: None`.
  - The flag stays **required** under `--live --append-ledger`. The operator still states explicitly that this run did not capture, rather than the requirement being dropped and a silent omission becoming indistinguishable from a forgotten one.
  - The record carries `capture.not_run: True`, without which the fix traded one defect for another. `ledger._is_merged_ship_run` reads an assessment *recommending* a merge as proof the PR merged, so the re-record counted as a merged PR whose capture marker had gone missing — `keel ledger` reported a capture gap for a PR that had not merged and whose real marker was sitting in the first record. The flag settles the question before the assessment is consulted.
  - `--capture-artifact` with `not-run` is refused. An artifact is the proof an `applied` capture produced something, so the pair asserted both that no capture happened and that here is its output; the ledger is evidence, and a self-contradicting record is worse than a quieter one.
  - The exemption must be **declared**, never inferred from the null marker. Inferring it would reclassify every genuinely lost marker as "never attempted" — a fail-open in the capture audit. A test pins that a record with the same shape but no declaration is still counted and still reported as `missing_marker`.
- **A Merged Security Change Was Reverted On Main And Nothing Noticed For Six Days** (#934):
  - Restored #811's non-redirecting opener in `keel doctor`'s PyPI version check. #810 — an accessibility change scoped to "add aria-label to a copy button" — merged 17 minutes later carrying a stale base, and its squash put plain `urlopen` back. It stayed on main for six days with CI green throughout.
  - `api_delegate._build_opener` is now the public, shared `build_http_only_opener`, used by both callers. Two hand-rolled openers with independently drifting handler sets is how the *next* one of these happens; a test now pins that exactly one module in the package assembles an `OpenerDirector`.
  - The construction moved out from behind `pragma: no cover`. #811 shipped with no test at all, and the one line that mattered sat in a block marked as a live-network boundary — so no test *could* have caught the revert. The injection seam is now the opener rather than an open callable, which puts the default path inside reach of a test.
  - **`keel verify-merge` is wired to something.** It has been documented as running after s10 since #561 — in `docs/keel/cli.md` and in `ship.md`'s prompt — and was invoked by no code path and no workflow. `keel merge` now runs it once the merge lands, reports it as `merge_verification`, and exits **3** when it finds drift: distinct from **1**, because "fail" on a merge that succeeded reads as "retry it". The overtaking PR is named in full, since the merge is already irreversible.
  - A clean result still prints its own line. A check that says nothing when it passes cannot be told apart from one that never ran, which is the whole shape of this issue.
  - Both halves are asserted at the call site rather than against the docs, and mutation-tested: re-creating #810's revert fails the opener test, and severing the post-merge call fails the wiring tests.
- **The Retry Was Written, Tested, And Called By Nothing** (#938):
  - `github._lines` — and through it `pr_files`, `commit_files` and `prs_merged_between` — now goes through `run_argv_retry` instead of plain `run_argv`. The retry, its transient-error detection and its four tests had zero callers in `src/keel/`, so every `gh` read failed on the first blip. These are the reads it was written for.
  - It matters more since #936. One `keel verify-merge` run makes `4 + N` reads (N = pull requests merged in the window; 5–25 in this repo), and an unreadable input now exits 2 rather than quietly passing — so a 1% per-call failure rate meant an 8.6–25% chance of a loud wrong answer, worst on the busiest days. A gate that cries wolf gets bypassed, which is #933's defect arrived at from the other side.
  - A **persistent** failure still returns `None` and still becomes `unknown`. The retry must not become a slower way to fail open, and a fatal error (404) is not retried at all — it is an answer, not a blip.
  - `pr_merge_window` briefly polls for `mergeCommit.oid` instead of reading its absence as "not merged". `ship.md` instructs running the drift check "immediately after a successful merge" — the one moment the field is least likely to be populated — so the runbook's own timing was the most frequent trigger of the loud path, and the shipped remedy was a sentence of prose asking an agent to retry.
  - The poll is bounded and giving up still yields `None`: it runs right after an irreversible merge, so it must give up rather than hang. Only the settling case waits — an unmerged PR or an unreadable `gh` is a real answer, and sleeping on either would make every such call three seconds slower.
  - Found while writing it: `str.strip()` also eats the trailing tab, so an empty `mergeCommit.oid` arrived as three fields and read as malformed rather than as "not settled yet" — the state the poll exists to recognise. Newlines only are trimmed now.
- **The Bandit Gate Was Red Because Nothing Scanned** (#927):
  - `bandit` is now in the `dev` extras. It never was, while `.keel/project.yaml` enabled the `bandit` preset — so where the gate actually runs it reported `bandit failed (exit 127): bandit: command not found`. That is the real cause of `gate bandit FAIL` on all 41 PRs merged in one week; the stale-suppression diagnosis in the issue describes the *local* symptom, on a machine where bandit is installed.
  - Worth stating plainly: a security gate can be red because it found something, or red because it never ran, and those looked identical in the assessment. The preset is `suggest`-severity so it never blocked — which means a scanner that was not installed for months was indistinguishable from one that was merely noisy.
  - Five stale `# nosec B105` suppressions removed (`consent.py` ×3, `consentverify.py`, `contracts.py`), each sitting on a boolean or a value B105 never flagged. Bandit warns about an unused suppression and exits non-zero on it, so these would have kept the gate red even once the tool was installed. Each was verified individually — removed, then re-scanned to confirm no finding appears in its place.
  - `tests/test_gate_tools_are_installable.py` derives the required tools from the *enabled* presets and asserts each is declared, so enabling a preset without its tool now fails a test instead of producing a red column readers learn to skip. The knob commands (`build_gate_cmd`, `lint_cmd`) are held to the same rule, and a counterweight keeps the check from passing on an empty preset list.
  - A companion test re-runs the gate's own bandit invocation and fails on any surviving unused suppression, so the next one is caught at the point it is added rather than months later.
- **Three Security Hardenings Shipped Only Their First Requirement** (#932, residuals of #868/#870/#872):
  - None of these was an open hole — each issue's primary attack was closed and mutation-killed. What was missing is the second layer each issue explicitly asked for, and the shape they share: a multi-requirement issue where requirement 1 shipped and the rest read as optional.
  - **#868 req 2** — `evidence._fields` now stops at the first non-header or blank line, whether or not a header has been seen. It previously skipped past prose and kept scanning, so `"Some prose.\n\nhead: 0000000\nvendor: spoofed\n"` yielded both fields. Reachable via `_reviewer_key`, which calls it with no marker requirement: a comment whose *prose* said `reviewer: someone` was keyed to that reviewer. Leading blank lines are still skipped — a comment body routinely begins with a newline, and breaking there would reject legitimate verdicts.
  - **#870 req 2** — legacy wrapper destinations are resolved and required to sit under the surface's own write root (`.claude/commands` or `.agents/skills`), not merely under the project. Checked after `resolve()`, so `..` segments and symlinked parents both count. Both inputs are gated today; this is the layer that keeps that true when a third arrives.
  - **#872** — one shared `workspace.write_text_atomic` replaces three copies of the temp-file-and-rename dance. `os.replace` makes the swap atomic but not durable: after a power loss the rename can survive with the contents still in the page cache, which is the scenario #872's own Impact section named. The data is now fsynced before the rename and the directory entry after it, best-effort for the directory since Windows cannot open one with `os.open`.
  - The third writer was the point: `swarm.save_swarm_state` was still a bare `write_text`, and it was missed precisely because each writer carried its own copy instead of sharing one.
- **Swarm Worktrees Left Untracked** (#877):
  - Added `worktrees/` to `RUNTIME_IGNORE_ENTRIES`. `keel swarm` checks out one isolated tree per cluster under `.keel/worktrees/` (`swarm_runtime.build_worktree_path`), so a parallel run turned `git status` into hundreds of untracked files — and an operator's habit of trusting a clean status is what notices a genuinely stray file.
  - keel's own committed `.keel/.gitignore` is regenerated, not topped up. The top-up is append-only and dedupes on the exact string, so the first time an entry's *spelling* changed this repo ended up carrying both `worktrees/` and `/worktrees/` — and gitignore's looser pattern wins, leaving the fix inert in the one install that mattered. A test now pins the committed file to `runtime_gitignore_body()` and forbids two spellings of one entry coexisting.
  - The entry is **anchored** (`/worktrees/`), unlike its neighbours: unanchored it matches at any depth, including `.keel/extensions/<ext>/worktrees/` — and `extensions/` is committed config the tuple's own comment calls "intentionally absent". A third-party extension is far likelier to hold a directory called `worktrees` than one called `scratch`.
  - `docs/keel/artifacts.md` reproduces the generated gitignore verbatim and had fallen behind, so the page told a consumer an entry keel had just added should not be there. A test now pins the printed block to `runtime_gitignore_body()` and the directory table to every ignored subtree.
  - A new test derives `.keel/<name>` constructions from the package source and requires each to be either ignored at runtime or declared committed — the structural fix for this class, since an unclassified subtree now fails rather than going unnoticed.
  - The existing assertions iterated `RUNTIME_IGNORE_ENTRIES` and checked each member was in the generated file, which is true for whatever the tuple happens to contain and therefore blind to a runtime directory missing from it. The new tests derive the path from the writer and put the question to `git status` itself, with a counterweight pinning that `.keel/project.yaml` and a stray root file stay visible.
- **Gates That Reported a Pass Without Performing Their Check** (#933):
  - `keel verify-merge` no longer reports `clean` when GitHub is unreadable. `_overtaking_prs` turned a failed lookup into an empty overtaking set via `or []`, so a rate-limited `gh` produced "nothing overtook this merge" — the check announcing a pass for a question it never asked. Every input it depends on is now gathered before any is judged, and a single unreadable one makes the verdict `unknown` naming what could not be read.
  - `unknown` now exits **2** instead of 0, mirroring `keel evidence-verify`. The status line always said "not a pass"; the exit code, which is what a caller wiring this in after s10 reads, said otherwise. `out-of-scope` keeps exiting 0 — it is a real answer to the question asked.
  - The action-pin guard now covers `action.yml` at the repo root, not only `.github/workflows/*.yml`. That file is the composite action published to the Marketplace and runs inside consumers' workflows, and it was the one place carrying an unpinned `actions/setup-python@v7` — precisely because the guard's glob could not see it. Now pinned to `5fda3b95…` (`v7.0.0`), the same SHA the workflows already use.
  - A collision already found survives an unreadable sibling: `_overtaking_prs` collects partial results instead of aborting on the first unreadable pull request, so a named `drift` is not downgraded to an anonymous `unknown` because some unrelated PR in the window was rate-limited.
  - The pin guard now *discovers* the files it checks — every YAML in the tree containing `uses:`, minus consumer-facing docs snippets — rather than enumerating known locations, which is how it came to miss `action.yml` in the first place.
  - `src/keel/adapters/commands/ship.md` documents all three exit codes; it still told the operator that non-zero meant drift.
  - `incomplete` exits 2 alongside `unknown`. A kept `out-of-scope` finding answers the *scope* question and says nothing about drift, which is exactly what an unreadable overtaking list costs — exiting 0 there would block a clean merge at 2 while waving through one that also has a second, weaker problem.
  - A finding survives an unrelated unreadable input, for `out-of-scope` as well as `drift`: replacing the report would reset `unexpected` and `landed_count` to empty, deleting the evidence for a question that *was* answered. The report carries `incomplete` instead.
  - The `unknown` reason names only the questions it could not answer. Saying "no conclusion about drift is possible" when only the PR's own file list was missing is simply false — drift was checked, and found nothing.
  - The pin guard's scope is what `git ls-files` tracks, and its one exclusion (`docs/`) matches a path *component*. The substring filter it replaced dropped `.github/actions/rebuild/action.yml` because the path contains `build/` — this issue's own silent-blind-spot defect, re-created inside the guard that fixes it.
  - `tests/test_external_promises.py` no longer skips on an I/O failure. Its Homebrew-checksum, published-tap and PyPI-version checks run in the same CI job as the pin guard and used the same reasoning; under a simulated outage `main` skips 5 and goes green, this branch fails 5. A formula shipping the wrong digest could have passed during any transient network failure.
  - An unreachable GitHub no longer makes the online pin check `skipTest`. A skipped test does not fail CI, so a wrong pin could merge during any API blip. Unreachable repositories are collected and reported, and the comparison moved into a pure `judge_pins` so that branch is exercised by the default hermetic suite.
- **Integration Copy Button Accessible Label Recapture** (#916):
  - Captured `origLabel` at bind time in `website/integrations.js` and cleared pending reset timers on rapid clicks.
  - Prevented transient "Copied to clipboard" state from permanently overwriting the accessible name.
- **CodeQL Empty Except Handlers** (#912):
  - Replaced empty `except ...: pass` blocks in `config.py` `_is_cloud_metadata_or_link_local` and `scaffold.py` `detect_base_branch` with explicit return statements.
  - Resolved static analysis alerts while preserving deterministic fail-soft fallback behavior.

## [1.19.0] - 2026-08-20

### Added
- **Activity Verdict CLI Flag** (#861):
  - Added `--verdict` flag (`choices: pass, blocked`) to `keel activity --write`.
  - Allowed recording phase completion verdicts through the CLI.

### Security
- **Legacy Wrapper Directory Traversal Protection** (#870):
  - Validated legacy command names against safe identifier characters (`^[A-Za-z0-9_-]+$`) in `install.py` `_validate_legacy_mappings`.
  - Prevented path traversal when generating legacy wrapper files for Claude and skills surfaces.
- **Evidence Header Field Injection Protection** (#868):
  - Anchored header parsing in `evidence.py` `_fields` strictly to the top header block of review and jury comments.
  - Prevented field injection in comment bodies from overriding authentic reviewer, head, vendor, model, and jury panel metadata.
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
- **Session Contract Report Status Optimization** (#907):
  - Replaced generator expression in `contracts.py` `session_contract_as_dict` with explicit chained `or` condition.
  - Avoided generator allocation overhead during contract status resolution.
- **Workspace Scratch Content Cleanup** (#884):
  - Cleaned contents of `.keel/scratch` without deleting the top-level directory node in `workspace.py` `clean_scratch`.
  - Avoided directory recreation races and preserved directory watcher references during workspace garbage collection.
- **JSON Schema Non-String Key Validation** (#883):
  - Rejected non-string object property keys in `jsonschema_min.py` `_validate_object`.
  - Prevented schema consumers and sort operations from crashing when validating YAML mappings with non-string keys.
- **Oscillation Action and Fingerprint Check** (#882):
  - Checked that events carry non-empty `action` or `output_fingerprint` before counting towards repeated action oscillation in `runcontrols.py`.
  - Prevented false-positive oscillation halts during standard multi-event step progressions.
- **Ledger Capture Health Merged Runs Filter** (#881):
  - Filtered `capture_health_summary` in `ledger.py` to evaluate only merged PR ship runs and valid capture records.
  - Prevented incomplete, held, or unmerged pipeline runs from triggering false `missing-marker` reconcile alerts.
- **Checkpoint Identifier and Record Type Guards** (#880):
  - Guarded against non-dict `identifiers` and ledger records in `checkpoint.py` `_known_references`.
  - Prevented orphan scanning from crashing when processing malformed or partial checkpoint/ledger states.
- **Subprocess Runner Devnull Stdin** (#879):
  - Passed `stdin=subprocess.DEVNULL` to `subprocess.run` across command, argv, and swarm runner executions in `runner.py` and `swarm_runtime.py`.
  - Prevented background tasks from hanging indefinitely on interactive prompts (e.g. `sudo`, `npm login`, or git authentication prompts).
- **Model Normalization Prefix and Key Matching** (#878):
  - Sorted model pricing keys by length descending during normalization to prevent shorter model prefixes (e.g. `gpt-4o`) from shadowing longer models (`gpt-4o-mini`).
  - Preserved `ollama:` and `local:` prefixes before vendor stripping so local inference is accurately assigned zero cost.
- **Install Module Target Set Cleanup** (#877):
  - Removed redundant `_STATUS_TARGETS_SET` and `_TARGETS_SET` constants in `install.py`.
  - Simplified membership checking in `adapter_status` and `update_adapters`.
- **GateRunner Type Alias 4-Tuple Variant** (#876):
  - Added 4-tuple return variant `(ok, findings, timed_out, not_run)` to `GateRunner` type alias in `runner.py`.
  - Harmonized gate runner type annotations between `runner.py` and `gates.py`.
- **Swarm Landing Unified Merge Lock Path** (#875):
  - Unified the merge lock directory path in `swarm_landing.py` with CLI lock root (`.keel/state/locks/merge-<digest>.lock` via `resource_path`).
  - Ensured swarm landing and `keel merge` CLI commands synchronize on the exact same atomic lock path.
- **Swarm Landing Local Base Branch Rebase** (#874):
  - Rebased cluster branches onto the local `base_branch` instead of `origin/{base_branch}` in `swarm_landing.py` `rebase_and_heal_cluster_branch`.
  - Ensured offline, local, and sequential funnel merges correctly incorporate locally landed base branch commits.
- **Swarm Runtime Dynamic Rebalance Iteration** (#873):
  - Updated wave iteration loop in `swarm_runtime.py` to index into the dynamically rebalanced `current_plan.waves`.
  - Ensured failed worker issues properly prune dependent clusters and waves during orchestration.
- **GitHub Comment Raw Field Posting** (#872):
  - Used `-F` raw-field parameter in `gh api` calls in `github.py` when posting and editing issue comments.
  - Prevented unexpected file read expansion when comment bodies start with `@` (such as reviewer mentions).
- **Swarm CLI Partial Failure Exit Code** (#871):
  - Returned non-zero exit code (1) on `partial_failure` status in the `swarm-run` CLI command, and in `swarm-land --live`. *(Corrected in #931: as shipped this did not hold for `swarm-land` without `--live`, where the only route to `partial_failure` is a held cluster and the command returned 0. The dry run now shares the live contract.)*
  - Ensured automation pipelines detect partial worker failures correctly.
- **Atomic Checkpoint and Activity Writing** (#869):
  - Used atomic temporary file replacement (`tempfile.mkstemp` + `os.replace`) when saving checkpoint and activity state in `checkpoint.py` and `activity.py`.
  - Prevented corrupted or partial state files if a process is terminated during a write operation.
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

- **Display settings popover is reachable again** (#606): the `[data-motion="max"]` animation
  rules in `styles.css` had no UI — `wireSeg()` queried a `.seg[data-seg]` control that no
  page rendered, so the only way to reach them was editing `localStorage` by hand. The
  titlebar gains a display button whose popover exposes the Motion segment, wired with
  correct single-select semantics: `role="radiogroup"` + `role="radio"`/`aria-checked`
  (not `aria-pressed`), roving tabindex, Arrow/Home/End, `aria-expanded` on the trigger,
  and focus returned to it on `Escape`.

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

- **Command rail is a complete ARIA tabs pattern** (#604): the website's showcase rail
  drives a detail panel but announced itself as 16 independent toggle buttons via
  `aria-pressed`. It now carries the whole pattern — roving tabindex (one Tab stop instead
  of sixteen), Arrow/Home/End navigation on both axes with wraparound, `aria-selected`, and
  `#show-detail` as a `tabpanel` wired both ways via `aria-controls`/`aria-labelledby`. The
  interleaved group headings become `role="presentation"` (a tablist may only own tabs) and
  their text moves into each tab's accessible name so the grouping survives.

### Removed
- **Dead `data-type` plumbing** (#606): every page set `data-type` on `<html>` from
  `localStorage`, but no stylesheet has ever read it. Dropped from all four pages.
- **`plan.md` is no longer tracked** (#606): a leftover agent scratch plan at the repo root.
  Because it was in version control, every agent run that rewrote its plan landed as a
  source change inside an unrelated PR. Untracked and gitignored.

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
