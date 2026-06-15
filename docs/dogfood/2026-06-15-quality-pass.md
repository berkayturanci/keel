# Dogfood case study — 2026-06-15 quality pass

A quality/health pass on keel that mirrors the standards used in the sibling
`ai-jury` project. Three improvements shipped, then keel's own merge machinery —
and the `ai-jury` cross-vendor jury — were turned on keel itself. This note
records what shipped and, more usefully, what dogfooding **surfaced about keel's
own ergonomics**.

## What shipped

| PR | Change | Type |
|---|---|---|
| [#423](https://github.com/berkayturanci/keel/pull/423) | `docs/security/2026-06-15-security-audit.md` — fresh clean audit round | docs |
| [#415](https://github.com/berkayturanci/keel/pull/415) | `pr-lint` workflow + PR-template "Related issues" section + CONTRIBUTING rule | ci |
| [#417](https://github.com/berkayturanci/keel/pull/417) | Windows support — `windows-latest` CI × py3.11–3.13, `tzdata` (Windows-only dep), cross-OS tests | feat |

All three reached `main` green on the required Linux/macOS test matrix; #417 is
green on Windows too.

### Windows: a real gap, found by actually running there

Adding `windows-latest` to CI was not a config one-liner — the first Windows run
turned up **33 genuine failures**, which is exactly why it was worth doing:

- **36 `ZoneInfoNotFoundError`** — Windows has no system IANA timezone database, so
  the stdlib `zoneinfo` the merge-window logic depends on fails for *real* Windows
  users. Fixed by adding `tzdata` as a `sys_platform == 'win32'`-only runtime
  dependency (Linux/macOS stay at the single PyYAML dependency; the platform
  exception is documented in `AGENTS.md` and `CONTRIBUTING.md`).
- **9 path-rejection test failures** — a leading-slash path (`/tmp/x`) is absolute
  on POSIX but not on Windows, so it tripped "escapes the project root" instead of
  "must be relative". The core was correct on both; the tests now build an
  OS-absolute path.
- **1 codex deny-hook test** — a POSIX shell script the Windows runner can't exec;
  skipped on `win32`.

## Dogfooding keel on keel — what it taught us

The plan was to merge the three PRs through keel's own review→evidence→merge
pipeline (`/keel:pr-loop` → `keel merge`). Doing so surfaced several ergonomic
findings that are more valuable than the merges themselves.

### 1. The evidence gate is issue-driven; issue-less PRs can't complete it

keel's `evidence-verify` requires six artifacts before `keel merge` will act:
`closure-comment-pr`, `closure-comment-issue`, `review-verdict-{1,2,3}`, and
`jury-verdict`. The three PRs here are deliberately **issue-less** (pure CI/docs
hygiene, body says `no issue`). With no linked issue, `closure-comment-issue` can
**never** be produced — so the enforced evidence chain is unsatisfiable for an
issue-less PR.

### 2. `keel merge` refuses a *waived* PR by design

The documented escape hatch is the operator `keel:evidence-waived` label, which
disarms the gate. But `keel merge` then fails closed with
`reason: evidence gate is not enforced` — it deliberately will not be the tool that
merges a PR whose evidence guarantee has been waived. In other words, the waiver
means *"merge this outside keel's evidence guarantee"*, and `keel merge` is not
that path.

### 3. The sanctioned path for issue-less hygiene PRs

Putting (1) and (2) together: for an issue-less, evidence-waived maintenance PR,
the intended merge is a plain squash-merge (not a ship-style flow, so it does not
hit the "`gh pr merge` is a spec violation for ship-style flows" rule). That is how
all three were merged here: `keel:evidence-waived` applied, required checks green,
squash-merge. The clean alternative — to use `keel merge` proper — would be to
drive the work **through an issue** so the full evidence chain (including
`closure-comment-issue`) can exist.

> **Takeaway for keel.** Quality work that legitimately has no issue (CI hygiene,
> dependency/security chores) currently has a slightly awkward merge story: it must
> be waived and squash-merged by hand. A small improvement would be to treat a
> `no issue` PR as a first-class shape — either dropping `closure-comment-issue`
> from the required set when there is no linked issue, or letting `keel merge`
> proceed on an explicitly-waived PR with a recorded operator justification (the
> same shape as `--hotfix`).

### 4. Local gate honesty

`keel ship --dry-run` reported `gate lint FAIL` purely because `ruff` was not on the
local `PATH` (`make lint` → `ruff check .` → command not found). CI installs the
`dev` extra, so this was a local-environment artifact, not a real lint failure —
but a reminder that the command gates surface the local toolchain's state, not just
the code's.

## The jury (ai-jury) verdict

A cross-vendor `ai-jury` review (`jury --pr 417 --rounds 1`) was run on the Windows
PR as the dogfood's review signal — genuinely cross-vendor (Anthropic + Google
reviewed; the OpenAI agent could not authenticate on this host).

| agent | vendor | status | duration |
|---|---|---|---|
| claude | anthropic | ok — no blockers | 19s |
| codex | openai | failed (`permission_prompt`) | 4s |
| agy | google | 1 blocker raised | 167s |

The `agy` reviewer raised one **critical/blocker** finding worth recording because
verifying it is the lesson:

> *"Skipping the deny-hook test on Windows drops coverage below the enforced
> `fail_under = 100` gate and breaks Windows CI; it also leaves the deny-hook
> security mechanism untested on Windows."* — `tests/test_codex_adapter.py:51`

**Verdict: verified false positive.** Two independent checks disprove it:

1. **Coverage scope.** That test executes an external `.sh` script via
   `subprocess`; it runs **no keel Python code**. Coverage is measured over
   `src/keel` (`source = ["keel"]`), so the shell script is out of scope — skipping
   the test cannot lower Python line/branch coverage.
2. **Empirical CI.** All three Windows jobs (py3.11–3.13) passed, *including* the
   `coverage report` step with `fail_under = 100`. Had coverage dropped, that step
   would have failed; it did not.

The deny hook is a POSIX shell script the Windows runner cannot exec at all, so
there is nothing for it to test on Windows. The finding was plausible but wrong —
a clean illustration of why jury findings are **adversarially verified against the
actual run**, not merged on confidence alone. Net jury outcome: **no actionable
blocker** (Claude clean; the one raised blocker refuted by CI; Codex unavailable on
this host).

## Net result

- 3 PRs merged to `main`, all green on required checks (Windows proven for #417).
- A clean security-audit round on record for 2026-06-15.
- Concrete, actionable feedback on keel's own issue-less-PR merge ergonomics —
  the most useful output of pointing keel at itself.
