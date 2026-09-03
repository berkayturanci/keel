# The Homebrew formula lives in the tap, not here

`brew install berkayturanci/keel/keel` reads
[`berkayturanci/homebrew-keel`](https://github.com/berkayturanci/homebrew-keel).
That repository holds the only formula anyone installs. This repository holds the
*template* it is rendered from, and nothing else.

The split exists because a formula names two things that cannot both be correct
before a release exists:

```ruby
url    "https://github.com/berkayturanci/keel/archive/refs/tags/v1.19.3.tar.gz"
sha256 "5ad23fe8…"
```

The url is derivable from the version. The digest is not: it belongs to an
archive GitHub builds *from the tag*, and the tag is created from the very commit
the release pull request produces. So a copy of that pair committed here is stale
on every release, by construction — which is what made every release cost a
second write to `main` ([#990](https://github.com/berkayturanci/keel/issues/990),
[#1023](https://github.com/berkayturanci/keel/issues/1023)).

So the pair is never committed. `.github/workflows/publish.yml` renders
`keel.rb.template` *after* the tag exists, downloads the archive the tag actually
produced, hashes it, and attaches the result to the **GitHub Release** — where it
is permanently reachable at
`https://github.com/berkayturanci/keel/releases/latest/download/keel.rb` and
covered by that release's `SHA256SUMS`.

**A release is now exactly one write to `main`: the release pull request.**

See [`docs/keel/homebrew-release-chain.md`](../../docs/keel/homebrew-release-chain.md)
for the whole chain and what each guard catches.

## Why only `url` and `sha256` are placeholders

The vendored `resource "PyYAML"` block keeps a literal url and digest, and that
is correct: PyYAML 6.0.3 is already published, so both values are knowable at
commit time, are reviewable in a diff, and are checked offline by
`tests/test_release_docs.py` and online by the tap's own suite. Only the pair
that *cannot* be knowable at commit time is rendered.

## Before `Formula/keel.rb` could be deleted: repoint the tap

The tap's `.github/workflows/sync-formula.yml` pulled
`repos/berkayturanci/keel/contents/Formula/keel.rb` every thirty minutes. With
that file gone the `curl -fsSL` 404s, exits 22, and the tap's sync fails on a
schedule forever — the same hourly failure that produced
[#981](https://github.com/berkayturanci/keel/issues/981).

The change the tap needs ships here rather than as a request that someone
remember it:

```console
$ git -C ../homebrew-keel apply /path/to/keel/packaging/homebrew/tap-sync-formula.patch
```

It repoints the sync at `releases/latest/download/keel.rb`, which needs no
credential at either end and is never stale, and adds the guard #990 identified
as the load-bearing one: the incoming url must be a tag archive of *this*
project, not merely a url whose digest happens to match it.

### The window where the asset does not exist yet

The ordering is *repoint → merge → next release*, and it is the merge that makes
the next release attach `keel.rb`. So between the repoint and the next tag there
is **no asset to fetch**: releases up to and including v1.19.3 carry only the
wheel, sdist, SBOM and `SHA256SUMS`, and
`https://github.com/berkayturanci/keel/releases/latest/download/keel.rb` returns
404.

The patch treats that as an ordinary state rather than a fault. The fetch step
records `found=false`, prints a `::notice::`, and the verify and commit steps are
skipped. The tap keeps serving the formula it already has — which still installs
— its cron stays green, and the first release cut after this merges fills the
gap. Failing on the 404 would have replaced one red cron with another.

### What is actually enforced, and where

| Check | Where it runs | Blocking on `main`? |
|---|---|---|
| `TAP_REPOINTED` names a tap commit as `tap-sync-formula: <40-hex>` | offline, default suite | **yes** — it is in `Tests`, a required context |
| the tap's live `sync-formula.yml` no longer names the retired path | `KEEL_CHECK_EXTERNAL=1` | **no** — `external promises` is not a required status check |

"It runs in CI" is not "it blocks", and the second row is the case where the two
come apart. `main` requires the nine test-matrix jobs, `Format (ruff)`, CodeQL,
the pull-request description lint and the keel evidence check; `external
promises` is on none of those lists and no ruleset adds it, so a red run there is
visible and ignorable.

That leaves the offline marker as the **only** blocking half, and it is a **claim
in a reviewable form** rather than proof: nothing offline can read another
repository, and the test accepts any 40 hex characters. Requiring a commit sha
rather than a non-empty file is what makes it checkable — a reviewer can open the
commit it names, and a wrong sha is falsifiable rather than a shrug. The online
half is the real check, and making `external promises` a required status check on
`main` is what would turn the claim into an enforced fact (the sibling reached
the same conclusion in ai-jury#673). That is the operator's call.

Record it like this:

```console
$ echo "tap-sync-formula: $(git -C ../homebrew-keel rev-parse HEAD)" \
    > packaging/homebrew/TAP_REPOINTED
```

Both the marker and the gate are single-use, and `TAP_REPOINTED` says so in its
own text.
