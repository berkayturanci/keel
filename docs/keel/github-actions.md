# Running keel on GitHub's free runner

keel needs an environment with `git` and an authenticated `gh` to assess a real PR. A
**GitHub-hosted runner already provides both** — `git` is configured and `gh`
authenticates from the workflow's `GITHUB_TOKEN` — and public repositories get free
Actions minutes. The packaged workflow therefore runs the deterministic assessment slice
on GitHub's free runner, while the full agentic ship loop still runs in an agent host that
can delegate implementation and reviews.

## The `keel-ship` workflow

[`.github/workflows/keel-ship.yml`](../../.github/workflows/keel-ship.yml) runs on every PR,
on `workflow_dispatch`, and on `issue_comment` — that last one because a verdict *is* an
issue comment, and without it the evidence check would never be re-evaluated when one
arrives.

Note `issue_comment` always runs the workflow file from the **default branch**, never a pull
request's copy. So a change to this workflow's comment handling cannot be tested on the pull
request that makes it; it starts working once merged. (That rule is also why the path is
safe: the comment event never checks out contributor-authored code.)

On the hosted runner it:

1. checks out full history (so keel can diff against the base branch);
2. installs keel (`pip install -e .` in this repo);
3. fetches the base branch locally;
4. runs **`keel ship .keel/project.yaml --root . --pr <N>`** — which reads the changed
   files (git), the project gates, and the PR's CI rollup (`gh`), then prints the
   assessment (risk tier → reviewers, merge window, gates, decision);
5. posts that assessment as a **PR comment**;
6. runs **`keel evidence-verify .keel/project.yaml --root . --pr <N>`**, which reads the
   live PR changed files/head SHA and fails until the PR and linked issue have the
   required closure, reviewer-verdict, and optional jury-verdict comments for the current
   tier and head.

```yaml
permissions:
  contents: read
  issues: write          # to read issue comments and post assessment comments
  pull-requests: write   # to read PR reviews/files and comment
  checks: write          # to publish the `keel evidence (required)` check-run
env:
  GH_TOKEN: ${{ github.token }}   # gh authenticates from this
```

Manual `workflow_dispatch` runs also accept an optional `deferral` input. Use it only for
an explicitly recorded evidence deferral (`review`, `jury`, a concrete evidence id, or
`all`); normal PR runs remain fail-closed.

## Gate arming and the operator waiver

The evidence gate **arms from deterministic ship provenance by default** — when the PR
carries ship-run signals (a ship-style head branch name, a trusted review-verdict marker,
trusted `keel ship` assessment comment, or a `ship_run` ledger record), the gate is active
without any label. The assessment comment only arms the gate; it never satisfies closure,
review, or jury evidence. The legacy gate label remains an additional arming signal for
already-installed workflows. The only disarm path is the **operator-applied**
`keel:evidence-waived` label; agents must never apply it.
Hand-authored PRs with no ship provenance are legitimately ungated. `keel evidence-verify`
reports which arming rule fired (or which waiver applied) in its human and `--json` output,
so a skipped gate is always attributable to an explicit operator decision rather than a
forgotten label.

## Official 1-Click GitHub Action (`berkayturanci/keel-action`)

Instead of writing manual pip install steps, use the official composite action:

```yaml
name: Keel Autonomous Delivery
on:
  issues:
    types: [labeled]

jobs:
  ship:
    if: github.event.label.name == 'keel:ship'
    runs-on: ubuntu-latest
    permissions:
      contents: write
      issues: write
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: berkayturanci/keel-action@v1
        with:
          command: ship
          issue: ${{ github.event.issue.number }}
          delegate: "google-api:gemini-2.5-pro"
          github-token: ${{ secrets.GITHUB_TOKEN }}
          gemini-api-key: ${{ secrets.GEMINI_API_KEY }}
```

### Nightly Swarm Delivery Workflow
```yaml
name: Nightly Swarm
on:
  schedule:
    - cron: "0 2 * * *" # Every night at 02:00
  workflow_dispatch:

jobs:
  swarm:
    runs-on: ubuntu-latest
    permissions:
      contents: write
      issues: write
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: berkayturanci/keel-action@v1
        with:
          command: swarm
          github-token: ${{ secrets.GITHUB_TOKEN }}
          gemini-api-key: ${{ secrets.GEMINI_API_KEY }}
```

## Adopting it in a consumer repo

Add `.github/workflows/keel-ship.yml` with `uses: berkayturanci/keel-action@v1` and supply your project's API key secrets (`GEMINI_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`). Everything else (the runner, git, gh, quality gates, and reviewer panel) is handled automatically.

## Branch protection

To make evidence mandatory, add **`keel evidence (required)`** to the protected branch's
required status checks. That is a check-run the workflow publishes, not a job — the job
that runs the verification is called `keel evidence (verify)`, because a job's own check is
driven by its exit code and can only ever report that the job ran. Two same-named checks on
one commit would be indistinguishable to branch protection, which matches by name.

Do **not** add `keel evidence (verify)` — the job — to required checks instead. It exits 0
while the gate is waiting, on purpose, because the published check is what blocks; requiring
the job would reinstate exactly the green-with-no-evidence signal this design removes.

The check is fail-closed. While required evidence is missing it stays **incomplete** (a
yellow dot), which blocks a merge without painting a fresh PR red; a `neutral` conclusion
would *not* block, since branch protection accepts `successful`, `skipped` and `neutral`
alike. It completes to green or red once the evidence resolves. Posting a reviewer or jury
verdict re-runs the workflow on its own, so the check updates in place rather than needing a
manual rerun.

If an operator intentionally defers evidence, record that deferral in the PR/issue
conversation and rerun the command with the matching `--deferral <id|kind|all>` flag in the
project workflow.

**Fork pull requests.** A fork's token is read-only whatever `permissions:` declares, so the
workflow cannot publish this check for one — the verdict rides on the `keel evidence (verify)`
job's exit code instead, and the check itself is *absent* rather than red. If you accept fork
contributions, have the recovery path below in place before you make this check required.

Recovery is **not** "Re-run all jobs": that replays the same `pull_request` event with the same
read-only token and fails identically. Use **Run workflow** (`workflow_dispatch`) from the base
repository with the pull request's number in the `pr` input. That runs from the default branch
with a writable token, and the step resolves the fork's head through
`gh pr view --json headRefOid`, which the base repo can see at `refs/pull/<N>/head`.

Expect the dispatch alone to come back red the first time. A fork branch will not match keel's
ship-branch pattern (`^(feature|fix|chore|docs|test)/issue-\d+`), there is no assessment
comment (that step's `gh pr comment` also 403s on a fork), and there is no ledger — so the gate
is *unarmed*, and `--require-armed` correctly refuses to report success for a gate that checked
nothing. Two ways forward, both auditable:

* run the ship adapter against the fork PR so real provenance exists, or
* apply the operator waiver label (`keel:evidence-waived`), which is recorded as a deliberate,
  attributed exception rather than a silent bypass.

## Boundary

This workflow runs the **agent-free** slice (classify → CI → gates → decision) and the
deterministic public-evidence verifier. The live *merge* and the *agentic* steps
(implement / multi-agent review) intentionally remain in the installed `/keel:<command>`
adapters and their agent host, where operator consent, delegation, model attribution,
worktree isolation, and project extensions are available.

The agent-host requirement for the agentic steps is narrower than it used to be: with the
hosted-API delegates (`--delegate anthropic-api:MODEL` / `openai-api:MODEL` / `google-api:MODEL`, or configured profiles) a
runner needs only a vendor API key as a secret — no installed, authenticated agent CLI —
to drive implement/review through the same adapter contract. The orchestration itself
(the adapter prose, consent, attribution, worktrees) still runs in an agent host; the
delegate only replaces the code-generation engine.
