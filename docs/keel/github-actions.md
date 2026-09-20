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

### Which run is authoritative

Two triggers are routinely in flight over one pull request at once — `keel review --live`
posts its verdict comments seconds after the push that started the assessment — so the
`concurrency` group carries the **event name**: `keel-ship-pull_request-<n>`,
`keel-ship-issue_comment-<n>`, `keel-ship-workflow_dispatch-<n>`. (A `workflow_dispatch`
with no `pr` input has no number to key on and falls back to the ref, so it groups as
`keel-ship-workflow_dispatch-refs/heads/main`.) Runs of the *same* event still cancel their
predecessor (`cancel-in-progress: true`); runs of *different* events never cancel each other.

The split matters because a cancelled run's check-runs cannot be deleted. One group across
every trigger meant a verdict comment's run cancelled the still-running `pull_request` run,
leaving `keel ship (assessment)` and `keel evidence (verify)` `cancelled` on the pull
request's head; GitHub reports that head as UNSTABLE and `keel merge` refuses on "CI
failing" with every required check green (#1037).

Cancelling *within* one event stays safe, though not for the obvious reason. Usually the
cancelled run belongs to a head SHA the newer run has superseded — but `reopened`, a
`synchronize` fired by a **base**-branch update, and a force-push back to an already-assessed
SHA all re-run `pull_request` on an unchanged head. What covers those is that the run doing
the cancelling is a run of the same event on that same head: it republishes both job
check-runs under the same names, and branch protection and keel's own rollup dedupe alike
read the most recent check-run per name. A *different* event cancelling republishes nothing,
which is why that case broke and this one does not.

The cost is paid on the default branch. `keel review --live` posts three verdicts seconds
apart, so two of those `issue_comment` runs are now cancelled inside their own group — and
that event always runs from the default branch, so those cancelled `keel evidence (verify)`
job checks land on the default branch's tip. They are visible in the Actions and commit UI
and are read by nothing that gates a pull request.

What no longer lands there is a *failure* from a closed pull request, which could only fail
and did so on `main`'s head. The job's `if:` drops a reply to an already-closed pull request
(`github.event.issue.state == 'open'`), and its first step asks the API for the state as it
is *now* — a bot that comments and then closes leaves `open` in the payload — standing the
rest of the job down on `CLOSED` or `MERGED`. See [evidence.md](evidence.md).

**The authoritative verdict is the one from the run that read the pull request last** — not
the run that finished last, and not the run for any particular event. Each run stamps the
moment it read the PR (taken immediately before `keel evidence-verify`) into the
`keel evidence (required)` check-run's `external_id`; before writing, it compares that stamp
with the newest one already published for the head and declines to overwrite a newer one,
logging a `Newer evidence verdict kept` notice and exiting 0 — the newer verdict is
authoritative, so a declining run must not replay its own exit code over it either.

That is what keeps an assessment run which started *before* a verdict was posted from putting
its "waiting" answer back over the "verified" one a later comment run published. It is a
read-then-write guard, not a compare-and-swap — the check-runs API offers none — so it does
not make a lost update impossible; it shrinks the window from the whole install-and-verify
run to the single round trip between reading the published stamp and writing over it.

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

## Official GitHub Action (`berkayturanci/keel`)

The action lives at keel's own root, and that is the file GitHub publishes as the
[Marketplace listing](https://github.com/marketplace/actions/keel-autonomous-delivery-action).
Pin it to a release tag:

```yaml
name: Keel assessment
on:
  pull_request:

jobs:
  assess:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          # keel diffs against the base branch; a shallow checkout has no such ref.
          fetch-depth: 0
      - uses: berkayturanci/keel@v1.23.1
        with:
          command: ship
          config: .keel/project.yaml
          pr: ${{ github.event.pull_request.number }}
          keel-version: "1.23.1"
          github-token: ${{ secrets.GITHUB_TOKEN }}
```

That run is a **dry assessment** — it reports a decision and changes nothing. A
mutating run names its consent scopes itself, through `args`; keel's consent gate
is not something the action talks its way past:

```yaml
        with:
          command: ship
          args: --live --approve-scope filesystem --approve-scope git --approve-scope github
```

### Inputs

| input | default | meaning |
|---|---|---|
| `command` | `ship` | `ship`, `run-gates`, `evidence-verify`, `verify-merge`, `swarm-plan`, `swarm-run`, `swarm-land`, `swarm-status`, `validate`, `plan` |
| `config` | `.keel/project.yaml` | path to the project config |
| `pr` | — | pull request number. Sent to `ship`, `evidence-verify` and `verify-merge` — the commands that assess one; **required** by the last two |
| `issue` | — | issue number recorded on the run. Sent to `ship`, `run-gates` and `plan` only: on the swarm commands the same flag *selects* which issues to plan, so pass those through `args` |
| `args` | — | extra arguments appended verbatim |
| `keel-version` | `latest` | `keel-workflow` version from PyPI — pin it |
| `python-version` | `3.12` | Python used to run keel |
| `comment` | `false` | post the output as a PR comment (needs `pull-requests: write`) |
| `fail-on-block` | `false` | fail the step when keel exits non-zero |
| `github-token` | — | token for `gh`, usually `secrets.GITHUB_TOKEN` |

Outputs: `exit-code` (keel's exit code) and `output-file` (the captured stdout).

Delegate credentials are **not** action inputs — set them as job or step `env`, which
composite steps inherit:

```yaml
    env:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
```

### Swarm planning

```yaml
      - uses: berkayturanci/keel@v1.23.1
        with:
          command: swarm-plan
          args: --issues 101,102,103 --json
          github-token: ${{ secrets.GITHUB_TOKEN }}
```

There is no `swarm` subcommand — the four real ones are `swarm-plan`, `swarm-run`,
`swarm-land` and `swarm-status`.

## Adopting it in a consumer repo

Add `.github/workflows/keel-ship.yml` with `uses: berkayturanci/keel@v1.23.1`, pin
`keel-version` to the same release, and set whichever delegate API keys your project
uses as `env`. Everything else (the runner, git, gh, quality gates, and reviewer panel)
is handled by keel itself.

A repository that copied this workflow file rather than the action needs the `concurrency`
block copied too, event name and all — an older copy keyed on the pull request number alone
carries the cancelled-check-run defect described above.

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
