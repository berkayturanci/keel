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
pipx install "git+https://github.com/berkayturanci/keel@v0.6.0"   # or pin an existing release tag
keel --version
keel install-adapter all             # → both surfaces: Claude commands + the shared skill set
#   claude → .claude/commands/keel/<cmd>.md      (native /keel:<cmd>)
#   skills → .agents/skills/keel-<cmd>/SKILL.md  (every non-Claude agent — one shared copy)
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

## Step 3 — retire the old bodies (only what keel owns)

Once verified, delete the **portable** command bodies that keel now provides, and **keep the
genuinely project-only** ones:

| Retire (keel owns these) | Keep (project-only) |
|---|---|
| `ship`, `ship-v2`, `implement`, `pr-loop`, `review-cycle*`, `review-all-day`, `morning`, `overnight`, `wrap`, `triage`, `stale-prs`, `ci-check`, `deps-audit`, `flake-audit`, `coverage`, `regression` | platform builds (e.g. an app build/release command), app-specific regressions, UI/device tests, anything tied to one app's stack |

Retire the old bodies in **both** surfaces keel now owns: `.claude/commands/` (Claude) and the
shared `.agents/skills/<command>/` skills (every non-Claude agent). The retirement is a normal
PR — review the diff, merge when green.

## Step 4 — move project-specific behavior into config / Lego

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
pipx install --force "git+https://github.com/berkayturanci/keel@vX.Y.Z"
keel install-adapter claude --force      # refresh adapters (your edits are kept without --force)
```
