# The Homebrew release chain

Why this document exists: between 2026-08-25 and 2026-08-27 this chain failed
three separate ways here and four in the sibling repository, and each was
diagnosed from scratch because nothing wrote down how the pieces fit. This is
that write-up. It is not a runbook — [`release.md`](release.md) is — it is the
explanation a runbook assumes you already have.

The sibling has the same chain with two deliberate differences, noted where they
matter: [`berkayturanci/ai-jury` → `docs/homebrew-release-chain.md`](https://github.com/berkayturanci/ai-jury/blob/main/docs/homebrew-release-chain.md).

## The contradiction everything else is arranged around

`Formula/keel.rb` names two things:

```ruby
url "https://github.com/berkayturanci/keel/archive/refs/tags/v1.19.2.tar.gz"
sha256 "726f1bf11bd58f512b0e1bfbcfe99c72a5190681c852859496ec748000c1c444"
```

**They cannot both be correct at the same moment.** `make release-bump` moves the
url to the version being released; that tag archive does not exist until the tag
is pushed. So from the release pull request until the tag, the formula
necessarily declares a digest belonging to the *previous* release.

Every mechanism below exists because of that gap. None of them removes it —
[#990](https://github.com/berkayturanci/keel/issues/990) proposes doing that.

## The chain, end to end

| # | Step | Who | Where |
|---|---|---|---|
| 1 | `make release-bump VERSION=x.y.z`, cut the changelog | human | release PR |
| 2 | Merge, then `git tag vX.Y.Z && git push origin vX.Y.Z` | human | — |
| 3 | Build, publish to PyPI, create the GitHub Release, attest | automatic | `publish.yml` |
| 4 | Download the tag archive, hash it, rewrite the formula | automatic | `publish.yml` |
| 5 | Push `chore/formula-digest-<tag>`, open a PR, arm auto-merge | automatic | `publish.yml` |
| 6 | Fetch this formula, re-hash the artifact, commit if it differs | automatic | tap's `sync-formula.yml`, every 30 min |
| 7 | Verify what the tap now serves | automatic | tap's `verify-formula.yml` |

Steps 4–5 are the repair for the contradiction: the digest becomes knowable
exactly at step 3, so the release fixes its own formula immediately afterwards
instead of leaving a note.

**Difference from the sibling:** the tap here *pulls* (step 6). Writing another
repository would need a PAT with `contents: write` on it — one repo holding write
access to another, to create, store and rotate — and reading a public repo needs
no credential at all. The sibling pushes instead. Both work; this design has one
less secret, that one has no latency.

`make release-bump` also regenerates 60-odd surfaces that carry a `keel_version`
marker — `commands/`, `.claude/commands/keel/`, `.agents/skills/keel-*`, both
plugin manifests, `keel-ship.yml`, the website. Hand-editing the version is how
those go stale; the script is the only supported route.

## Every guard, and what it catches

| Guard | Where | Kind | Catches |
|---|---|---|---|
| package/plugin version lockstep | `tests/test_release_docs.py` | offline | a manifest left behind |
| no stale version on any site surface | `tests/test_release_docs.py` | offline | docs.html sitting at v1.6.5 for four releases |
| formula url names the current version | `tests/test_release_docs.py` | offline | a formula left behind by a release entirely |
| url downloads and hashes to its digest | `tests/test_external_promises.py` | online | the stale digest (#805, #981) |
| the publish step's shape | `tests/test_publish_formula_followup.py` | offline | the repair mechanism being silently undone |
| what the tap actually serves | tap's `tests/` | online | anything a sync produced that cannot install |

The online ones are opt-in via `KEEL_CHECK_EXTERNAL=1` so the default suite stays
hermetic. **They are wired into CI's `external promises` job** — that is not
automatic, and a guard nothing runs is not a guard.

### Why the tap has its own tests

The tap is the copy `brew` downloads. Its formula is written by a scheduled job —
no pull request, no review — so the guards here, which run on pull requests
against a file that is *copied* there, never look at the result. Until
2026-08-27 the tap had three files and no test of any kind.

The tap's suite downloads **every** url in the formula and re-hashes it, not just
the top-level one. This formula vendors PyYAML as a `resource` with its own url
and digest — a second thing `brew` downloads and checksums, and a wrong one fails
the install exactly as hard. #787 is what that looks like from a user's side:
every command dying on `import yaml` before printing anything. Nothing checked
that pair, in any repository, until then.

### What the tap refuses, and why it matters

`sync-formula.yml` reads the declared digest, downloads the artifact, hashes it,
and **refuses to publish when they disagree**:

```
::error::incoming formula's sha256 does not match its own url; not publishing
```

So it has the correct value in hand and declines to use it, because the policy is
*"this repository is authoritative."* That policy is why a stale digest here stops
`brew upgrade` for everyone rather than being silently corrected — and it is the
thing [#990](https://github.com/berkayturanci/keel/issues/990) questions.

## What has already gone wrong

Read this before adding a mechanism — several of these were fixed by adding one.

| Failure | What actually happened |
|---|---|
| [#787](https://github.com/berkayturanci/keel/issues/787) | The formula was fixed here, the guard checked the copy here, and users kept installing the broken one until someone synced the tap by hand. |
| [#805](https://github.com/berkayturanci/keel/issues/805) | 1.16.0 shipped with 1.15.0's digest. `brew install` refused. |
| [#981](https://github.com/berkayturanci/keel/issues/981) | The workflow computed the right digest and emitted a `::notice::` asking a human to apply it. Nobody did. The tap refused every sync for a day — one failure email per hour, in a different repository, long after this one had gone green. |
| [#979](https://github.com/berkayturanci/keel/pull/979) | A security change cut from a base predating #982 merged after it, silently reverting 53 lines of tests, the CI step that ran them, and a changelog entry — while its description mentioned only a read size limit. The guards survived elsewhere; the release note did not, and 1.19.2 nearly shipped notes omitting a fix. |
| the sibling's #641 | Its auto-PR's first live run could not push: `HEAD:${branch}` is refused from the detached HEAD a tag build checks out. This repo is not exposed — it runs `git checkout -b` first, so the refspec's source is a branch. |

Three patterns run through all of these:

1. **A message nobody is required to act on is not a safeguard.** A `::notice::`,
   an `::error::`, a runbook line — each was tried, each was missed.
2. **A guard that nothing runs is not a guard.**
3. **Check the three-dot diff before merging.** A branch cut from an old base
   reverts what landed after it, and the pull request's description will not
   mention it.

## Still manual, and why

Two repository settings stand between step 5 and a hands-off chain. Neither is a
code defect; both are decisions.

- **Actions may not create pull requests.** *Settings → Actions → General →
  "Allow GitHub Actions to create and approve pull requests"*. With it off, step 5
  pushes the branch, fails at `gh pr create`, falls through to its notice, and
  someone opens the pull request by hand. Measured on v1.19.2.
- **The evidence gate has no bot exemption.** Measured on
  [#989](https://github.com/berkayturanci/keel/pull/989), a one-line
  machine-generated digest change: it required three reviewer verdicts and an
  `agent:<vendor>` label. The PR-description lint *does* exempt bots by account
  type (#963); the evidence gate does not. Exempting a review gate is a larger
  decision than exempting a description lint, which is why it has not simply been
  done.

Interim cost: one hand-opened, hand-merged pull request per release, with the
digest already computed, pushed and independently verified. Materially better
than before #984, where the value existed only as a line in a log.

## Diagnosing the next one

| Symptom | Look here first |
|---|---|
| The tap fails on a schedule, hours after a green release | The formula's digest against the artifact its url names. |
| A pull request shows **no runs at all** for its head | The head commit's message. A CI-skip marker in it — even quoted in prose — suppresses every workflow. |
| The release PR fails on a 404 for its own tag archive | Expected before the tag exists; the guard exempts exactly that window (#839). If it *fails* rather than skips, that rule regressed. |
| `publish.yml` pushed nothing after the tag | The refspec, and whether a local branch was created first. A tag build is on a detached HEAD. |
| `brew upgrade` sees nothing new | The tap's own CI. It runs after every sync and asserts the tap serves the current release. |
| A guard "exists" but never fails | Whether CI sets `KEEL_CHECK_EXTERNAL=1` for it. |
