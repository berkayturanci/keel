# Cutover — retiring a project's copied command bodies

A consumer that still has its own copied workflow command bodies (the old "file-copy" model)
moves to keel in **staged, verified** steps. The rule throughout: **lose nothing** — never
retire an old body until its keel replacement is proven on a real issue.

## The model you're moving to

```
keel core (pip, pinned)     the deterministic CLI + backbone + invariants
/keel:<command> adapters     the agentic flows, installed via `keel install-adapter`
.keel/project.yaml           this project's values (branch, build/lint, agents, globs, window)
.keel/extensions/*           this project's own Lego (project-specific gates/steps)
```

The project holds **only** config + Lego. The backbone and the portable commands come from
the installed keel — never copied, so the drift/overwrite class of bug is gone.

## Step 1 — install + pin keel

```bash
pipx install "git+https://github.com/berkayturanci/keel@v1.0.2"   # or pin an existing release tag
keel --version
keel setup --root .                  # → config + both adapter surfaces + validate + plan
#   config → .keel/project.yaml
#   claude → .claude/commands/keel/<cmd>.md
#   skills → .agents/skills/keel-<cmd>/SKILL.md
```

Now both the old `/<command>` and the new `/keel:<command>` exist side by side — so you can
**A/B test** before deleting anything.

## Step 2 — verify parity on a low-risk test issue

Pick a small, low-risk issue and run the new flow against the old one:

```bash
keel validate .keel/project.yaml --root .
keel plan     .keel/project.yaml --root .
/keel:ship <issue>            # the new flow
```

Check that the new `/keel:ship` reproduces what the old `/ship` did, for **this project**:

- [ ] correct base branch, build/lint commands, implementer agent (from `.keel/project.yaml`)
- [ ] risk tier → reviewer count (from `tier3_globs`)
- [ ] **inline** review comments anchored on `file:line` (per `--review-comments`)
- [ ] gates run (build / lint / **jury** if in `gates:` and the `jury` CLI is installed)
- [ ] merge **window + lock** honored (no night merges; `--hotfix` audited)
- [ ] effective vendor+model attribution recorded
- [ ] worktree handling + close behave as before

Repeat for any command you rely on (`/keel:regression`, `/keel:review-cycle`, `/keel:morning`,
…). If something is missing, **don't retire yet** — open an issue against keel to close the gap
(the adapter, or a knob/Lego), fix it, re-verify.

## Step 3 — replace proven old bodies with thin wrappers

After the row in `docs/keel/parity-matrix.md` is `parity-proven` or intentionally `deferred`,
generate a compatibility wrapper for each legacy command name you still want agents to
recognize:

```bash
keel install-legacy-wrappers all --command ship=ship
keel install-legacy-wrappers all --command ship=ship --command morning=morning
```

This writes:

- `.claude/commands/<legacy>.md` for native legacy slash commands.
- `.agents/skills/source-command-<legacy>/SKILL.md` for non-Claude agents that discover
  shared skills.

The wrapper preserves the user's original target and flags, including `--dry-run`,
jury/no-jury, review-comment mode, merge behavior, issue targeting, and PR targeting, then
delegates to `/keel:<command>` / `keel-<command>`. It also runs the structured live plan before
mutating state so missing consent or capabilities stop the run early.

Existing files are skipped unless `--force`. That makes the migration staged: review the
generated wrapper in a PR, compare it against the old body, and only force-overwrite or delete
the old copy once the parity row and PR review both agree that nothing is being lost.

## Step 4 — retire the old bodies (only what keel owns)

Once verified, delete the **portable** command bodies that keel now provides, and **keep the
genuinely project-only** ones:

| Retire (keel owns these) | Keep (project-only) |
|---|---|
| `ship` (incl. `ship --compound`), `implement`, `pr-loop`, `review-cycle*`, `review-all-day`, `morning`, `overnight`, `wrap`, `triage`, `stale-prs`, `ci-check`, `deps-audit`, `flake-audit`, `coverage`, `regression` | platform builds (e.g. an app build/release command), app-specific regressions, UI/device tests, anything tied to one app's stack |

Retire the old bodies in **both** surfaces keel now owns: `.claude/commands/` (Claude) and the
shared `.agents/skills/<command>/` skills (every non-Claude agent). The retirement is a normal
PR — review the diff, merge when green.

## Step 5 — move project-specific behavior into config / Lego

Anything project-specific that lived inside a retired body goes to:

- a **knob** in `.keel/project.yaml` (a value: command, path glob, agent, timezone, window), or
- a **Lego extension** in `.keel/extensions/` snapped into a named hook such as `guard`,
  `tester`, `pre-merge`, `reviewers`, or `after-implement` — for a project-specific
  gate/step, or
- a project-only command that simply stays in the project.

## Rollback

The cutover is a PR. If `/keel:*` misses something after merge, `git revert` the retirement PR
— the old bodies come straight back. Nothing is lost.

## Updating later

```bash
pipx upgrade keel-workflow
# or: python -m pip install --upgrade keel-workflow
# or: pipx install --force "git+https://github.com/berkayturanci/keel@vX.Y.Z"

keel adapter-status all --root .
keel sync --root . --dry-run
keel sync --root .
keel validate .keel/project.yaml --root .
keel plan .keel/project.yaml --root .
```

Only generated adapter files are candidates for automatic refresh:
`.claude/commands/keel/*.md` and `.agents/skills/keel-*/SKILL.md`. Project-owned config,
`.keel/extensions/*`, project-only commands, and policy docs are never touched by
`sync`.

Adapter files carry a `keel-generated` marker. `sync` updates files reported as `missing` or
`outdated`; it refuses to overwrite `locally-modified` or `unknown` files. Treat those as
normal PR review work and merge them by hand if needed.

`sync` uses the keel version already installed in the active Python environment. It does not
download the latest PyPI package by itself; keep package upgrades explicit so the upgrade PR
shows both the package-version change and the generated adapter diff.

Extension compatibility is checked after every upgrade with strict validation and plan
rendering. If an extension references a removed/renamed slot or has malformed frontmatter,
`keel validate .keel/project.yaml --root .` fails before any live adapter mutates repository
or GitHub state.
