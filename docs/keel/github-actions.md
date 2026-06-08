# Running keel on GitHub's free runner

keel needs an environment with `git` and an authenticated `gh` to assess (and eventually
ship) a real PR. A **GitHub-hosted runner already provides both** — `git` is configured and
`gh` authenticates from the workflow's `GITHUB_TOKEN` — and public repositories get free
Actions minutes. So the cheapest place to run keel live is a GitHub Actions workflow.

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
6. **fails the check when the decision is `BLOCK`** (failing gates / CI / blocking findings),
   so keel gates the merge.

```yaml
permissions:
  contents: read
  pull-requests: write   # to comment
env:
  GH_TOKEN: ${{ github.token }}   # gh authenticates from this
```

## Adopting it in a consumer repo

Copy the workflow and change two things:

- **Install keel from a controlled ref** instead of the local checkout:
  ```yaml
  - run: pip install "git+https://github.com/berkayturanci/keel@v0.7.0"
  ```
- **Point at your config**: `keel ship .keel/project.yaml --root . --pr <N>`.

Everything else (the runner, `git`, `gh`, the free minutes) comes from GitHub.

## What this does *not* do yet

This runs the **agent-free** slice (classify → CI → gates → decision) and comments it. The
live *merge* and the *agentic* steps (implement / multi-agent review) still need a runner
that can dispatch a coding agent; the workflow is the place that will host them once
`keel ship --execute` lands.
