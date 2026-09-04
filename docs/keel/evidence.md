# Evidence Chain & Auditability

> **Keel guarantees verifiable provenance for every merged change.**
> Every PR merged through Keel carries an unbroken, commit-bound record of who reviewed it,
> what was tested, which gates passed, and why it was safe to merge — an auditable trail
> by construction, not bolted on after the fact.

---

## The Enterprise Challenge: Agentic Trust & Auditability

As software engineering teams transition from human-only development to multi-agent automation,
a fundamental governance problem emerges:

1. **How do you prove an AI-generated change was actually reviewed and tested?**
2. **How do you prevent race conditions where a PR is approved, but modified before merging?**
3. **How do compliance, security, and release teams audit autonomous agent operations?**

In enterprise orchestration (e.g. Camunda, SOC 2, ISO 27001), process explainability and audit trails
are mandatory. Keel provides the software engineering equivalent: the **Auditable Evidence Chain**.

---

## Core Primitives of the Evidence Chain

### 1. Commit-SHA Binding (Zero Approval Drift)
In traditional PR workflows, an approval given on commit `A` remains valid even if a subsequent commit `B`
is pushed.

In Keel's evidence gate (`s10 merge`), approvals and review verdicts are strictly bound to the **exact `HEAD_SHA`**:
* Structured verdict comments carry a signed machine-readable payload:
  ```json
  <!-- keel:evidence {"contract":"keel.review-verdict.v1","head_sha":"cfe06ca8...","verdict":"approve","reviewers":2,"tier":"TIER-2"} -->
  ```
* If the head commit changes by even one byte, previous review evidence is automatically invalidated,
  and Keel halts the merge until the new commit is re-verified by the backbone.

### 2. Multi-Vendor Agent Attribution
Every agentic mutation (implementation in `s4`, review in `s7`) is permanently stamped with standard attribution:
* `agent:<vendor>` (e.g. `agent:agy`, `agent:anthropic-api`, `agent:ollama`)
* `model:<base>` (e.g. `model:claude-opus-5`, `model:gemini-3`, `model:qwen2.5-coder`)

Attribution is captured both in the git commit metadata, the PR labels, and Keel's durable run ledger.

**The labels come from core, never from prose.** `keel.agents.attribution()` is the only
definition of what a vendor/model pair is labelled, and `keel attribution` is how an
adapter asks for it:

```bash
keel attribution --vendor agy --model gemini-3.8-flash-high --json
# {"agent_label": "agent:agy", "model_label": "model:gemini-3", "system": "agy:gemini-3.8-flash-high"}
```

Re-deriving the labels by hand is the failure this rule exists to prevent. A host that
composed them itself wrote `agent:gemini` / `model:gemini` on the PR **and**
`gemini:gemini-3.8-flash-high` into the ledger; the two agreed with each other, so the
vendor cross-check passed while neither matched Keel's vocabulary. `evidence-verify`
therefore also runs an `attribution-vocabulary` check: it recomputes the expected labels
from the ledger record's `actors.implementer` and refuses (blocking) any `agent:` /
`model:` label the CLI could not have produced, naming the expected labels in the finding.
The check is skipped, never failed, when no ledger record is available. On the writing
side, `keel ship --live --append-ledger` warns when `actors.implementer` names a vendor
that is neither built in nor a configured `knobs.delegate_profiles` entry.

### 3. Gate Arming: Ship Provenance First, Branch Name Last
The gate is fail-closed: an unarmed gate derives no requirements, so it must be armed for
any PR that came out of the backbone. `keel.evidence.gate_decision()` consults these
signals **in order**, and the order is part of the contract:

| # | Reason | Signal |
|---|---|---|
| 1 | `operator-waiver-label` | The `keel:evidence-waived` label — the one sanctioned disarm, checked first so it is never shadowed. |
| 2 | `gate-label` | The legacy `evidence_gate_label` opt-in. |
| 3 | `ship-provenance-comment` | A **trusted** PR comment carrying `keel.ship-provenance.v1`. |
| 4 | `ship-branch` | **Legacy fallback**: a head ref matching `^(feature\|fix\|chore\|docs\|test)/issue-\d+`. |
| 5 | `ship-assessment-comment` | A trusted (or `github-actions`) `keel ship` assessment comment. |
| 6 | `review-verdict-marker` | A trusted comment/review carrying `keel.review-verdict.v1`. |
| 7 | `ship-run-ledger` | A readable `ship_run` ledger record for this PR. |

Nothing was removed when the marker was added — every path that armed the gate before
still arms it. What changed is that the **run stamps itself** instead of being recognised
by the name someone gave its branch. A live run posts the provenance comment as soon as the
PR exists:

```bash
keel post-comment .keel/project.yaml --root . --target pr:<PR> \
  --artifact ship-provenance --body-file <rendered.md> --run-id "$RUN_ID"
```

with the body rendered from `keel ship --json` → `result.artifact_bodies.ship_provenance`
(see [artifacts.md](artifacts.md)). Signals 4–7 all depend on something outside the run's
control — a branch-naming convention, a CI comment, a posted verdict, a ledger file that
may live in a per-run worktree the verifier cannot read. A ship run that named its branch
`fix/2467-slug`, whose verdicts were never posted, and whose ledger was out of reach read
as `enforced: false (no-ship-provenance)`: the gate that exists to require review disarmed
itself exactly when review had not happened. The marker closes that hole.

Only a **trusted** comment arms the gate — the same fail-closed `author_association` rule
every other evidence source uses — so an outside contributor cannot manufacture provenance.

### 4. Who the Reviewers Are: the Bench, or the Panel

The required `review-verdict` count comes from the review contract's `reviewers` block, and
`knobs.team` decides what that block describes (see
[configuration.md](configuration.md#team)):

| `reviewers.panel` | `reviewers.source` | What must be posted |
|---|---|---|
| `reviewers` | `team.review.by_tier.<n>` / `risk-tier` / `override` | One verdict per staffed host seat. |
| `jury` | `jury` | One verdict per **ai-jury panelist ballot**, plus the `jury-verdict` consensus record. |

The bench is a pure function of **config + tier + role + `--reviewers` / `--review-delegate`**,
and of nothing else — not a jury flag, and not the measured participating-vendor count. It
has to be: the six commands that resolve this contract are not *given* those inputs
uniformly (all six accept the jury flags since #1043, but keel's CI passes `--no-jury` to
`evidence-verify` on every run and to `ship`/`plan` on none; only `evidence-verify` and
`keel merge` can read a posted vendor count), so a bench that moved with either would have
one surface requiring the panel's ballots while another demanded a host bench of the same
pull request.

On a `review.by_tier.<n>: jury` tier the panel *is* the review: `s7` dispatches ai-jury once
and `keel review --from-jury <report.json>` maps each ballot onto a head-pinned
`keel.review-verdict.v1` carrying the vendor and model that produced it. Running host
reviewers **and** the panel over the same diff is what this replaced — it paid twice for the
same reading while the panel's per-reviewer ballots reached no gate at all.

**How the requirement is sized.** The panel decides how many ballots there are, so the
posted `keel.jury-verdict.v1` declares `panelists: <N>` beside `vendors: <N>`, and
`evidence.jury_panel_size()` reads it back — the same channel, and for the same reason: the
run ledger and the jury artifact live under the gitignored `.keel/state/`, so a hosted
runner can read neither, while PR comments are always visible.

`min_vendors` is a **floor, not a fallback**: the required count is
`max(declared, min_vendors)`, so a declared count can only ever *raise* it. Before any
verdict declares one, the floor stands on its own — an unmeasured panel cannot satisfy the
gate by being unmeasured — and a verdict declaring fewer ballots than the floor does not
lower it either. That asymmetry is deliberate: the count is read off a PR comment, and the
one shape that means "the panel came back short" must not be the shape that relaxes the
gate.

It also means the surfaces size the requirement differently, convergently and on purpose.
`keel plan`, `keel ship` and `keel step-verify` resolve the contract with no pull request in
reach, so on a panel tier they publish the floor; `keel review`, `keel evidence-verify` and
`keel merge` read the posted verdict and require the panel that actually sat. Because the
count only rises, a planning surface can understate what will be required but never
overstate it, and no two surfaces that can both see the panel disagree.

**Vendor distinctness asks a panel the panel's own question.**
`evidence_require_distinct_vendors` normally wants one distinct vendor per required verdict,
which is the right question for a bench keel staffs. For a panel, keel did not pick the
seats: every ballot must still declare a vendor, but the panel as a whole must span at least
`jury.min_vendors` distinct ones. Three ballots from two vendors is a legitimate cross-vendor
review; three from one vendor is one opinion three times and fails with
`review-vendor-distinctness`.

**A panel tier never downgrades.** Where the jury sits *beside* a host bench, a panel with
fewer than `jury.min_vendors` participating vendors is downgraded `gating → advisory` and
its verdict stops being required (see
[cli.md](cli.md#--jury-vendors-n--the-panel-decides-whether-the-jury-gates)) — sound there,
because the bench still reviewed the change. Where the panel **is** the review there is no
bench behind it, so the downgrade is suppressed: the ballots stay required, the jury verdict
stays required, and `jury.downgraded` reports `false`. A short panel does not get to excuse
itself from the consensus record that says it was short; the shortfall is what the vendor
check above reports. The requirement shrinks in no direction at all, which is what a moving
bench could never have promised.

### 5. Phase-Separated Verification Contract
Evidence requirements are split by lifecycle phase:
* **Pre-Merge Phase (`s10`)**: Requires verified `review-verdict` (from required risk-tier reviewer count)
  and passing gate results (`build`, `lint`, optional `jury`).
* **Post-Merge Phase (`s11`)**: Records `closure-comment` and `compound-learning` markers.

The pre-merge gate strictly validates what exists before the merge, preventing cyclical dependencies
while guaranteeing full review compliance.

### 6. Transparent Deferrals & Audit Trail
In real engineering teams, legitimate exceptions occur (e.g. emergency hotfixes outside the merge window,
or temporary gate waivers).

Rather than offering an unmonitored backdoor, Keel makes exceptions **first-class and auditable**:
* **`--deferral` / `keel:evidence-waived`**: An operator can explicitly waive specific requirements.
* **Audit Record**: Every waiver requires an explicit `--operator` attribution and records a durable
  entry in `.keel/ledger/` and GitHub issue timeline, creating a tamper-evident record.

### 7. Header-Anchored Marker Classification
What a comment *is* — a review verdict, a jury verdict, a closure comment, a ship-provenance stamp,
a deferral — is decided by its **header line** and nothing else: the first non-empty line, which every
Keel renderer emits as the marker alone, either bare (`keel.review-verdict.v1`) or wrapped in an HTML
comment so it renders invisibly (`<!-- keel.closure-comment.v1 -->`). A marker further down the body is
prose, not a classification signal — a reviewer writing "I checked the `keel.jury-verdict.v1` handling"
is quoting a string, not filing a jury verdict. Testing for the marker anywhere in the body could not
tell the two apart: two `keel.review-verdict.v1` comments whose scope mentioned the literal string
`keel.jury-verdict.v1` were counted as `jury_verdict: 2, review_verdict: 0`, and the review that
actually happened was invisible to the gate (#1026). This is the same anchoring the `reviewer:` /
`head:` / `vendor:` header fields already use (#868), extended from the fields to the marker itself.
A header naming **two different markers** does not say which artifact it is: that comment is excluded
from every count and reported as an advisory `malformed-evidence-comment` finding (`minor` — it never
blocks on its own), rather than being counted for both or silently dropped.

The **ship-assessment comment** is anchored the same way (#1035). Its heading, `### 🚢 keel ship`, is a
Markdown heading rather than a versioned `keel.*.v1` marker, so it cannot join the marker set — but it is read as
an *exclusion* by both verdict classifiers, and as a whole-body test it let a reviewer disarm their own verdict by
quoting the heading while describing what they reviewed ("the `### 🚢 keel ship` comment claims the gates
passed, but…"). The verdict was then uncounted and `evidence-verify` reported `missing: review-verdict-N` for a
comment sitting right there on the pull request. A comment is now an assessment only when its header line **leads
with** that heading or with the CLI's own `keel ship —` banner. Both forms are what a real assessment starts
with — the workflow writes the heading first, a raw paste of the summary leads with the banner — so the
`ship-assessment-comment` arming signal and the two exclusions keep sharing one test and nothing that armed the
gate through a genuine assessment stops arming it.

The wrapper is matched **literally, never with a regex**. Keel does not parse HTML — it recognises the
one wrapper `closure.render_closure_comment` writes and refuses everything else. A pattern that treats
`-->` as *the* comment terminator is wrong about HTML, because a browser also ends a comment at `--!>`,
and a classifier that disagrees with the renderer about where a comment ends would let a body render
invisibly on the page while still counting as evidence. So `<!-- keel.review-verdict.v1 --!>`, an
unterminated `<!--`, two wrappers on one line, or anything trailing the close is left intact and
therefore does not classify. The cost is that a hand-rolled wrapper loses its classification; the
alternative is guessing, which is the failure mode.

---

## Offline Verification: `keel evidence-verify`

Compliance auditors and CI pipelines can independently verify the evidence chain offline without network calls:

```bash
# Verify a PR's evidence chain against the repository policy
keel evidence-verify .keel/project.yaml --root . --pr 554 --head-sha cfe06ca8...
```

The verifier confirms:
1. Presence of required reviewer verdicts matching the risk tier (`TIER-1` = 1, `TIER-2` = 2, `TIER-3` = 3).
2. Exact matching between the verdict `head_sha` and the target commit.
3. Proper agent attribution.
4. Pass status for all declared blocking quality gates.

### Three-State Verification Lifecycle

To provide honest optics during in-flight pull requests while strictly preserving fail-closed merge security, `keel evidence-verify` distinguishes between missing evidence and invalid evidence:

| Status | CLI Exit | GitHub Check-Run | Meaning | Merge Allowed? |
|---|---|---|---|---|
| **`waiting`** | `2` | *incomplete* (🟡 yellow dot) | Required evidence for the active phase is not posted yet, or a verdict is pinned to a commit that is not the head. | ❌ Blocked |
| **`pass`** | `0` | `success` (✅ green check) | All required evidence items for the active phase are verified and match `HEAD_SHA`. | ✅ Allowed |
| **`fail`** | `1` | `failure` (❌ red mark) | Explicit violations detected: closure comment mismatch with the ledger record, a missing attribution label, an attribution label outside Keel's vocabulary (`attribution-vocabulary`), or an unarmed gate. | ❌ Blocked |

The `keel-ship` workflow publishes these as a real check-run named
**`keel evidence (required)`**, against the PR head, on every run. That check —
not the workflow job's own exit code — is the one to put in branch protection.
The job that runs the verification is deliberately named something else
(`keel evidence (verify)`), because Actions names a job's own check after the
job and that check can only ever report "the job ran": two same-named checks on
one commit would be indistinguishable to branch protection, which matches by
name.

Two decisions are worth stating outright, because both are easy to get wrong:

* **The waiting state is published as an *incomplete* check, not as a
  conclusion.** GitHub's branch protection accepts three conclusions as
  satisfying a required check: *"Required status checks must have a
  `successful`, `skipped`, or `neutral` status before collaborators can make
  changes to a protected branch."* So concluding the waiting state as `neutral`
  — the obvious reading of "grey, not red" — would let a merge through with no
  evidence at all. An incomplete run blocks the merge and still renders as a
  yellow dot rather than a red X, which is the signal actually wanted. It
  completes to `success` or `failure` once the evidence resolves.
* **Being unable to report is not a pass.** If the check-run cannot be
  published the step fails rather than exiting 0, in every state including the
  passing one — with one deliberate exception, below, for pull requests from a
  fork, where publishing is impossible rather than broken.

Two consequences of how GitHub's check-runs API works, both load-bearing:

* **The check is upserted, not re-created.** `POST /check-runs` has no upsert on
  (name, head SHA), so publishing blindly would leave one check per run stacked
  under the gating name — at least one of them permanently incomplete. Branch
  protection matches by name, which is the same ambiguity the separate job name
  avoids. The workflow looks the check up first (`--method GET`; `-f` alone
  switches `gh` to POST, which 404s) and `PATCH`es it when it exists.
* **A fork PR cannot publish at all.** Its token is read-only whatever
  `permissions:` declares. Failing the step unconditionally there would leave
  every fork contribution red with no route forward, including one whose
  evidence verified — so on a fork the job's own exit code carries the verdict
  instead: green only for a real pass, red while waiting or on a violation.

  Recovery is **Run workflow** (`workflow_dispatch`) from the base repository
  with the pull request's number — not "Re-run all jobs", which replays the same
  read-only token. Expect it to come back red once: a fork PR carries no
  ship-provenance comment, does not match the legacy ship-branch pattern, and has
  no assessment comment or ledger, so the
  gate is unarmed and refuses to report success for a check that verified
  nothing. Generate real provenance by running the ship adapter against the pull
  request, or apply the operator waiver label. See
  [github-actions.md](github-actions.md) for the full path, and do not make this
  check *required* until you have it.

The workflow re-runs on `issue_comment` as well as on pushes, because that is
what a verdict *is*: `keel post-comment` calls `POST /issues/{n}/comments`.
Without it, posting a verdict fires no event, so the incomplete check on that
SHA would never be revisited — and the only self-service retrigger, a new
commit, changes the head SHA and invalidates the very verdicts that would have
let it pass. Subscribing to `pull_request_review` instead reads tidier and fires
never: across the last twelve merged pull requests here, all 33 verdict markers
were issue comments and none were reviews.

One consequence worth knowing before you try to verify this: like `schedule`,
`issue_comment` always runs the workflow file from the **default branch**, never
the pull request's copy. (`workflow_dispatch` is not in that group — it runs the
file from whichever ref you dispatch.) So a change to this
trigger cannot be exercised on the pull request that makes it — it starts working
once merged. The same rule is why the path is safe: the comment event never checks
out contributor-authored code, even though it holds `checks: write`.

---

## Complete Enforcement: How to Prevent Agent Bypasses

Keel enforces the evidence chain programmatically, but complete end-to-end enforcement requires pairing Keel's deterministic core with standard repository protections:

### 1. Keel Core Deterministic Enforcement
* **Fail-Closed Evidence Gate**: `keel merge` blocks unconditionally if required reviewer verdicts or quality gates are missing or mismatched against the head commit SHA.
* **Independent Diff Verification**: The orchestrator inspects actual git filesystem diffs rather than trusting agent-declared file lists.
* **Merge Lock & Window**: Simultaneous agent merges and out-of-window merges are blocked at the filesystem and clock level.

### 2. GitHub Repository Protection (Recommended Setup)
To prevent autonomous agents from bypassing the Keel backbone via direct `git push origin main`:
* **Require Pull Requests**: Disable direct pushes to the default branch (`main` / `develop`).
* **Require Status Checks**: Require the `keel-ship` / CI workflow checks to pass before merging.
* **Restrict Bypass Permissions**: Only designated human administrators may bypass rulesets.

### 3. No Bot Exemption — A Decision, Not An Omission

The gate has one requirement for every pull request: three reviewer verdicts bound to the head
SHA, plus an `agent:<vendor>` label. It exempts no account, and
[#1023](https://github.com/berkayturanci/keel/issues/1023) — which asked for a narrow exemption
so one machine-generated release pull request could merge itself — was answered by removing the
pull request instead.

Three reasons the exemption was refused, in order of weight:

1. **The author is not a reliable key.** Several bots commit through the GitHub API *as the
   repository owner*, so their commits carry a human login; an identity check sees a human.
   Measured in the sibling repository on 2026-09-03
   ([ai-jury#676](https://github.com/berkayturanci/ai-jury/issues/676),
   [#680](https://github.com/berkayturanci/ai-jury/pull/680)), where a bot pushed on top of a
   reviewed head from a stale checkout and silently reverted two merged pull requests — 25
   files, 2,491 deletions. An exemption is precisely the mechanism that would let a change of
   that shape past review.
2. **A content filter is not a gate.** "One file, only these two lines" describes the diff at
   the moment it is evaluated. A later push changes the diff; an exemption that re-evaluates is
   a race, and one that does not is a signature over the wrong bytes.
3. **The asymmetry with the PR-description lint is real.** That lint exempts bots by GitHub's
   account classification (#963) and can afford to — its worst outcome is a badly-described
   pull request. This gate's worst outcome is an unreviewed change on `main`.

The general form: when a gate blocks the only sequence able to satisfy it, fix the sequence.
An exemption records that the requirement was unsatisfiable and keeps it anyway. See
[The Homebrew release chain](homebrew-release-chain.md) for the case that made this concrete.

---

## Comparison: Opaque Agents vs. Keel Evidence Chain

| Dimension | Standard Coding Agents | Keel Evidence Chain |
|---|---|---|
| **Approval Validity** | Stale approvals persist across new commits | Strictly locked to commit `HEAD_SHA` |
| **Review Integrity** | Unchecked self-reviews or single model | Tier-based multi-agent / cross-vendor panels |
| **Audit Trail** | Ephemeral chat logs | Durable commit markers + JSON ledger records |
| **Exception Handling** | Undocumented force-pushes | Audited, operator-attributed deferral records |
| **Compliance Readiness** | Manual auditing required | Automated offline verification (`verify-evidence`) |
