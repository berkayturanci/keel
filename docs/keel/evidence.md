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
* `agent:<vendor>` (e.g. `agent:anthropic`, `agent:google`, `agent:openai`, `agent:ollama`)
* `model:<base>` (e.g. `model:claude-3-7-sonnet`, `model:gemini-2.5-pro`, `model:gpt-4o`)

Attribution is captured both in the git commit metadata, the PR labels, and Keel's durable run ledger.

### 3. Phase-Separated Verification Contract
Evidence requirements are split by lifecycle phase:
* **Pre-Merge Phase (`s10`)**: Requires verified `review-verdict` (from required risk-tier reviewer count)
  and passing gate results (`build`, `lint`, optional `jury`).
* **Post-Merge Phase (`s11`)**: Records `closure-comment` and `compound-learning` markers.

The pre-merge gate strictly validates what exists before the merge, preventing cyclical dependencies
while guaranteeing full review compliance.

### 4. Transparent Deferrals & Audit Trail
In real engineering teams, legitimate exceptions occur (e.g. emergency hotfixes outside the merge window,
or temporary gate waivers).

Rather than offering an unmonitored backdoor, Keel makes exceptions **first-class and auditable**:
* **`--deferral` / `keel:evidence-waived`**: An operator can explicitly waive specific requirements.
* **Audit Record**: Every waiver requires an explicit `--operator` attribution and records a durable
  entry in `.keel/ledger/` and GitHub issue timeline, creating a tamper-evident record.

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
| **`fail`** | `1` | `failure` (❌ red mark) | Explicit violations detected: closure comment mismatch with the ledger record, missing attribution label, or an unarmed gate. | ❌ Blocked |

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
  read-only token. Expect it to come back red once: a fork branch does not match
  the ship-branch pattern and there is no assessment comment or ledger, so the
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
