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
* `model:<base>` (e.g. `model:claude-opus-4-5`, `model:gemini-3`, `model:gpt-4o`)

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

### 4. Phase-Separated Verification Contract
Evidence requirements are split by lifecycle phase:
* **Pre-Merge Phase (`s10`)**: Requires verified `review-verdict` (from required risk-tier reviewer count)
  and passing gate results (`build`, `lint`, optional `jury`).
* **Post-Merge Phase (`s11`)**: Records `closure-comment` and `compound-learning` markers.

The pre-merge gate strictly validates what exists before the merge, preventing cyclical dependencies
while guaranteeing full review compliance.

### 5. Transparent Deferrals & Audit Trail
In real engineering teams, legitimate exceptions occur (e.g. emergency hotfixes outside the merge window,
or temporary gate waivers).

Rather than offering an unmonitored backdoor, Keel makes exceptions **first-class and auditable**:
* **`--deferral` / `keel:evidence-waived`**: An operator can explicitly waive specific requirements.
* **Audit Record**: Every waiver requires an explicit `--operator` attribution and records a durable
  entry in `.keel/ledger/` and GitHub issue timeline, creating a tamper-evident record.

### 6. Header-Anchored Marker Classification
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

---

## Comparison: Opaque Agents vs. Keel Evidence Chain

| Dimension | Standard Coding Agents | Keel Evidence Chain |
|---|---|---|
| **Approval Validity** | Stale approvals persist across new commits | Strictly locked to commit `HEAD_SHA` |
| **Review Integrity** | Unchecked self-reviews or single model | Tier-based multi-agent / cross-vendor panels |
| **Audit Trail** | Ephemeral chat logs | Durable commit markers + JSON ledger records |
| **Exception Handling** | Undocumented force-pushes | Audited, operator-attributed deferral records |
| **Compliance Readiness** | Manual auditing required | Automated offline verification (`verify-evidence`) |
