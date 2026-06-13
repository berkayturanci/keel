# Running keel on GitHub's free runner

keel needs an environment with `git` and an authenticated `gh` to assess a real PR. A
**GitHub-hosted runner already provides both** — `git` is configured and `gh`
authenticates from the workflow's `GITHUB_TOKEN` — and public repositories get free
Actions minutes. The packaged workflow therefore runs the deterministic assessment slice
on GitHub's free runner, while the full agentic ship loop still runs in an agent host that
can delegate implementation and reviews.

## The `keel-ship` workflow

[`.github/workflows/keel-ship.yml`](../../.github/workflows/keel-ship.yml) runs on every PR
(and on `workflow_dispatch`). On the hosted runner it:

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

## Adopting it in a consumer repo

Copy the workflow and change two things:

- **Install keel from a controlled ref** instead of the local checkout:
  ```yaml
  - run: pip install "git+https://github.com/berkayturanci/keel@v1.2.2"
  ```
- **Point at your config**: `keel ship .keel/project.yaml --root . --pr <N>`.

Everything else (the runner, `git`, `gh`, the free minutes) comes from GitHub.

## Branch protection

To make evidence mandatory, add the workflow job **`keel evidence (required)`** to the
protected branch's required status checks. The check is intentionally fail-closed: a fresh
PR can be red until the ship adapter posts the public evidence artifacts. Rerun the workflow
after the reviewer, jury, and closure comments are present. If an operator intentionally
defers evidence, record that deferral in the PR/issue conversation and rerun the command with
the matching `--deferral <id|kind|all>` flag in the project workflow.

## Boundary

This workflow runs the **agent-free** slice (classify → CI → gates → decision) and the
deterministic public-evidence verifier. The live *merge* and the *agentic* steps
(implement / multi-agent review) intentionally remain in the installed `/keel:<command>`
adapters and their agent host, where operator consent, delegation, model attribution,
worktree isolation, and project extensions are available.
