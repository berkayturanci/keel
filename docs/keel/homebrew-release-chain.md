# The Homebrew release chain

Why this document exists: between 2026-08-25 and 2026-08-27 this chain failed
three separate ways here and four in the sibling repository, and each was
diagnosed from scratch because nothing wrote down how the pieces fit. This is
that write-up. It is not a runbook — [`release.md`](release.md) is — it is the
explanation a runbook assumes you already have.

The sibling has the same chain with two deliberate differences, noted where they
matter: [`berkayturanci/ai-jury` → `docs/homebrew-release-chain.md`](https://github.com/berkayturanci/ai-jury/blob/main/docs/homebrew-release-chain.md).

## The contradiction, and how it was removed

A Homebrew formula names two things:

```ruby
url    "https://github.com/berkayturanci/keel/archive/refs/tags/v1.19.3.tar.gz"
sha256 "5ad23fe8a2510df1cf9341e24037a97252e86716d689da7aa7e06da5a94bf4cb"
```

**They cannot both be correct at the same moment.** `make release-bump` moved the
url to the version being released; the archive that url names is built by GitHub
*from the tag*, and the tag is created from the very commit the release pull
request produces. So from the release pull request until the tag, a committed
formula necessarily declared a digest belonging to the *previous* release.

For six releases the answer was to keep the copy and repair it afterwards, which
required a **second write to `main` after the tag**. That repair was built up
over five issues — #805, #842, #982, #984, #986 — and still ended in a pull
request a human had to merge, because the evidence gate has no bot exemption. The
release that nobody merged it for left the tap refusing every sync for a day
(#981).

[#990](https://github.com/berkayturanci/keel/issues/990) proposed removing the
requirement rather than the latest symptom, and
[#1023](https://github.com/berkayturanci/keel/issues/1023) is that change:

> **Nothing in this repository names a digest.**

`Formula/keel.rb` is gone. What is committed is
[`packaging/homebrew/keel.rb.template`](../../packaging/homebrew/keel.rb.template),
which names `@URL@`, `@SHA256@` and `@VERSION@` and can never be stale.
`publish.yml` renders it *after* the tag exists, downloads the archive the tag
actually produced, hashes it, refuses on an unrendered placeholder or a url that
is not this project's archive for this tag, and attaches the result to the
**GitHub Release** — permanently reachable at
`https://github.com/berkayturanci/keel/releases/latest/download/keel.rb` and
covered by that release's `SHA256SUMS`.

**A release is now exactly one write to `main`: the release pull request.**

### Why option (b), and not a pre-uploaded asset

#990 offered two routes: (a) publish the artifact the formula names *before* the
formula bump, so the release pull request can be right; or (b) keep the ordering
and make the second write unnecessary. (a) does not exist for this project. The
url is a GitHub **tag archive** — GitHub builds it when the tag is pushed, and
nothing can upload it earlier. Pointing the formula at the release's sdist asset
instead would swap one unknowable digest for another: both are produced by the
tag. The only moment the pair is knowable is after the tag, which is where the
render now happens.

### Why the vendored resource is still a committed digest

`resource "PyYAML"` keeps a literal url and sha256 in the template, and that is
not the same thing. PyYAML 6.0.3 is already published, so both values are
knowable at commit time, reviewable in a diff, and checked offline. The rule is
not "digests are bad" — it is that a value which cannot be true when it is
written should not be written.

## The chain, end to end

| # | Step | Who | Where |
|---|---|---|---|
| 1 | `make release-bump VERSION=x.y.z`, cut the changelog | human | release PR |
| 2 | Merge, then `git tag vX.Y.Z && git push origin vX.Y.Z` | human | — |
| 3 | Build, publish to PyPI, attest | automatic | `publish.yml` |
| 4 | Download the tag archive, hash it, render the formula, refuse if it disagrees | automatic | `publish.yml` |
| 5 | Attach `keel.rb` to the GitHub Release, inside `SHA256SUMS` | automatic | `publish.yml` |
| 6 | Install from PyPI and smoke it | automatic | `publish.yml` (`verify`) |
| 7 | Fetch `releases/latest/download/keel.rb`, re-hash its artifact, commit | automatic | tap's `sync-formula.yml`, every 30 min |
| 8 | Verify what the tap now serves | automatic | tap's `verify-formula.yml` |

Step 1 is the only write to `main`. Nothing after the tag commits anything here.

**Difference from the sibling:** the tap here *pulls* (step 7), and now pulls a
release asset rather than a file on `main`. Writing another repository would need
a PAT with `contents: write` on it — one repo holding write access to another, to
create, store and rotate — and reading a public release asset needs no credential
at all. The sibling pushes as a fast path and keeps the pull as a fallback. Both
work; this design has one less secret, that one has less latency.

`make release-bump` regenerates 60-odd surfaces that carry a `keel_version`
marker — `commands/`, `.claude/commands/keel/`, `.agents/skills/keel-*`, both
plugin manifests, `keel-ship.yml`, the website. Hand-editing the version is how
those go stale; the script is the only supported route. The formula is no longer
among them, and `scripts/release_surfaces.py` says why at the point where it used
to be listed.

## Every guard, and what it catches

| Guard | Where | Kind | Catches |
|---|---|---|---|
| package/plugin version lockstep | `tests/test_release_docs.py` | offline | a manifest left behind |
| no stale version on any site surface | `tests/test_release_docs.py` | offline | docs.html sitting at v1.6.5 for four releases |
| the template names no version and no digest | `tests/test_release_docs.py` | offline | a committed url/digest pair coming back |
| rendering leaves no placeholder behind | `tests/test_release_docs.py` | offline | a renamed placeholder shipping a formula `brew` cannot parse |
| every runtime dependency is vendored | `tests/test_release_docs.py` | offline | an install that dies on `import yaml` (#787) |
| the url is *this* project's archive at a tag | `tests/test_external_promises.py` | offline rule | a digest that correctly describes the wrong tarball (#990) |
| the tap's formula hashes to what it names | `tests/test_external_promises.py` | online | a tap `brew` would refuse (#805, #981) |
| the release path writes nothing to `main` | `tests/test_publish_release_chain.py` | offline | the second write being reintroduced |
| the render refuses rather than reports | `tests/test_publish_release_chain.py` | offline | a bad formula being attached with a `::notice::` (#842) |
| the tap's repointing is recorded, and true | `tests/test_external_promises.py` | offline claim + online check | the tap's sync 404ing every 30 minutes |
| the published release installs and runs | `publish.yml` `verify` | online | a green release nobody can install (#1024) |
| what the tap actually serves | tap's `tests/` | online | anything a sync produced that cannot install |

The online ones are opt-in via `KEEL_CHECK_EXTERNAL=1` so the default suite stays
hermetic. **They are wired into CI's `external promises` job** — that is not
automatic, and a guard nothing runs is not a guard.

They are also, deliberately, never compared against this branch's version. The
tap is written after the tag, so between a release and the tap's next sync it is
legitimately behind. A check that failed on that would block the only sequence
able to satisfy it.

### Why the tap has its own tests

The tap is the copy `brew` downloads. Its formula is written by a scheduled job —
no pull request, no review — so the guards here, which run on pull requests
against a template that is *rendered* there, never look at the result. Until
2026-08-27 the tap had three files and no test of any kind.

The tap's suite downloads **every** url in the formula and re-hashes it, not just
the top-level one. This formula vendors PyYAML as a `resource` with its own url
and digest — a second thing `brew` downloads and checksums, and a wrong one fails
the install exactly as hard. #787 is what that looks like from a user's side:
every command dying on `import yaml` before printing anything.

### What the tap refuses, and why it is now satisfiable

`sync-formula.yml` reads the declared digest, downloads the artifact, hashes it,
and refuses to publish when they disagree. That was the right check standing in
front of a value that could not be right: it had the correct digest in hand and
declined to use it, because the policy was *"the source repo is authoritative"* —
and the source repo could not be authoritative about a value that did not exist
yet (#990).

Nothing about the refusal changed; what changed is that the value it is handed is
now measured after the tag, so the check passes by construction and fires only on
a genuine defect. The patch in `packaging/homebrew/tap-sync-formula.patch` also
adds the guard #990 identified as the load-bearing one: the incoming url must be
a **keel tag archive**, not merely a url whose digest matches it.

## The one thing the other repository had to be told

The tap fetched `repos/berkayturanci/keel/contents/Formula/keel.rb` every thirty
minutes. That file is gone, so an un-repointed tap 404s on a schedule forever —
the shape of #981 arriving from a new direction.

`packaging/homebrew/tap-sync-formula.patch` is that change, ready to `git apply`;
[`packaging/homebrew/README.md`](../../packaging/homebrew/README.md) has the exact
commands. It also makes the fetch tolerate an asset that does not exist yet, which
matters for exactly one window: the ordering is *repoint → merge → next release*,
and it is the merge that makes the next release attach the formula. Until then the
asset 404s, the patched fetch records `found=false`, prints a `::notice::` and
skips the verify and commit steps. The tap keeps serving what it has and its cron
stays green. A patch that failed on that 404 would have traded one red cron for
another.

| Check | Where | Blocking on `main` |
|---|---|---|
| `packaging/homebrew/TAP_REPOINTED` names the tap commit as `tap-sync-formula: <40-hex>` | offline, default suite | **yes** |
| the tap's live `sync-formula.yml` no longer names the retired path | `KEEL_CHECK_EXTERNAL=1` | runs in CI's `external promises` job |

The offline half is a claim in a reviewable form, not proof: nothing offline can
read another repository. Requiring the tap's commit sha rather than a non-empty
file is what makes the claim falsifiable. Both it and the marker are single-use
and can be deleted after a release or two.

## The evidence gate keeps no bot exemption

#1023 asked for one: a narrow, audited exemption so the formula-digest pull
request could merge itself. It is not here, and that is the decision, not an
omission.

Three reasons, in order of weight.

**The author is not a reliable key.** The sibling repository measured this on
2026-09-03 ([ai-jury#676](https://github.com/berkayturanci/ai-jury/issues/676),
[#680](https://github.com/berkayturanci/ai-jury/pull/680)): several bots commit
*through the GitHub API as the repository owner*, so their commits carry a human
login and an identity check sees a human. The inverse is what an exemption cares
about, and it is just as loose — anything able to push under the bot identity
inherits the exemption. There a bot pushed on top of a reviewed head from a stale
checkout and replaced the tree with its old copy: 25 files, 2,491 deletions,
silently reverting two merged pull requests. An exemption is the mechanism that
would have let a change of that shape past review.

**The content filter is not the gate.** "Single file, only the `url`/`sha256`
lines" describes the diff at the moment the exemption is evaluated. A later push
to the same branch changes the diff, and an exemption that re-evaluates is a
race, while one that does not is a signature over the wrong bytes.

**The asymmetry with the description lint is real.** That lint exempts bots by
account type (#963) and can afford to: its worst outcome is a badly-described
pull request. The evidence gate's worst outcome is an unreviewed change on
`main`, arriving through the one job in this repository that can publish.

The reason an exemption was wanted was one machine-generated pull request per
release. There is no such pull request any more, so the gate can stay closed for
everyone. Any future formula change is a template edit, which a human writes and
three reviewers see, exactly like any other change.

## What has already gone wrong

Read this before adding a mechanism — several of these were fixed by adding one,
and the last one removed all of them.

| Failure | What actually happened |
|---|---|
| [#787](https://github.com/berkayturanci/keel/issues/787) | The formula was fixed here, the guard checked the copy here, and users kept installing the broken one until someone synced the tap by hand. |
| [#805](https://github.com/berkayturanci/keel/issues/805) | 1.16.0 shipped with 1.15.0's digest. `brew install` refused. |
| [#981](https://github.com/berkayturanci/keel/issues/981) | The workflow computed the right digest and emitted a `::notice::` asking a human to apply it. Nobody did. The tap refused every sync for a day — one failure email per hour, in a different repository, long after this one had gone green. |
| [#979](https://github.com/berkayturanci/keel/pull/979) | A security change cut from a base predating #982 merged after it, silently reverting 53 lines of tests, the CI step that ran them, and a changelog entry — while its description mentioned only a read size limit. |
| [#989](https://github.com/berkayturanci/keel/pull/989) | The follow-up pull request the workflow opened. One line, machine-generated, digest independently re-verified by CI — and it still needed three reviewer verdicts and an `agent:*` label, once per release. |
| the sibling's #641 | Its auto-PR's first live run could not push: `HEAD:${branch}` is refused from the detached HEAD a tag build checks out. |

Four patterns run through all of these:

1. **A message nobody is required to act on is not a safeguard.** A `::notice::`,
   an `::error::`, a runbook line — each was tried, each was missed.
2. **A guard that nothing runs is not a guard.**
3. **Check the three-dot diff before merging.** A branch cut from an old base
   reverts what landed after it, and the pull request's description will not
   mention it.
4. **Five mechanisms for one unsatisfiable requirement is four too many.** #805,
   #842, #982, #984 and #986 each made the repair of a file that could not be
   right more reliable. Deleting the file ended all five.

## Diagnosing the next one

| Symptom | Look here first |
|---|---|
| The tap fails on a schedule, hours after a green release | The formula asset's digest against the archive its url names. |
| The tap's sync 404s every 30 minutes | Its fetch source. It must be `releases/latest/download/keel.rb`, not a file on `main`. |
| A release published but attached no `keel.rb` | The render step's log. It refuses rather than reports: an unfetchable archive, a surviving placeholder or a foreign url all fail the job before the release is created. |
| A pull request shows **no runs at all** for its head | The head commit's message. A CI-skip marker in it — even quoted in prose — suppresses every workflow. |
| `brew upgrade` sees nothing new | The tap's own CI. It runs after every sync and asserts the tap serves the current release. |
| A guard "exists" but never fails | Whether CI sets `KEEL_CHECK_EXTERNAL=1` for it. |
| Something wants to add `Formula/keel.rb` back | Read "The contradiction" above. `tests/test_distribution.py` refuses it. |
