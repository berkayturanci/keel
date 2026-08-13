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

## Offline Verification: `keel verify-evidence`

Compliance auditors and CI pipelines can independently verify the evidence chain offline without network calls:

```bash
# Verify a PR's evidence chain against the repository policy
keel verify-evidence .keel/project.yaml --root . --pr 554 --head-sha cfe06ca8...
```

The verifier confirms:
1. Presence of required reviewer verdicts matching the risk tier (`TIER-1` = 1, `TIER-2` = 2, `TIER-3` = 3).
2. Exact matching between the verdict `head_sha` and the target commit.
3. Proper agent attribution.
4. Pass status for all declared blocking quality gates.

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
