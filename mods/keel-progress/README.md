# keel-progress

An optional [Claude Code mod](https://code.claude.com/docs/en/plugins/mods/overview) that
shows this repository's live keel runs inside Claude Code, so you don't need a second
terminal running `keel-visual dash`.

```text
main             keel #1444 ▰▰▰▰▰▰▰▰▰▰▶▱▱ s10 merge · waiting: merge-window · PR #1445
fix/merge-chec…  keel #1448 ▰▰▰▰▰▰▰▶▱▱▱▱▱ s7 review · waiting: review · PR #1449
```

- **Above the prompt:** one line per live run (`active`, `waiting` or `interrupted`). Each
  shows the issue, a bar over the backbone steps (`▰` done, `▶` current, `▱` pending), the
  current step, why it is waiting (or, in red, why it stopped), and the pull request. With
  more than one run each line starts with its worktree's branch, the session's own run
  first; after three, a `+N more` line points at the pane. Nothing is drawn when no run is
  live.
- **`/keel-progress`:** opens a pane listing every live run with its steps, the history counts (shipped,
  blocked, deferred, skipped) and the next queued issue, with **Refresh** and **Close**
  buttons. If `keel status` fails, the pane shows the error and the line above the prompt
  stays empty until a status succeeds again.

## How it reads the runs

Parallel `keel ship` runs each work in a worktree of their own and write that worktree's
`.keel/state/checkpoint.json`. The mod lists the repository's worktrees with
`git worktree list` and runs `keel status --json` in each one that has a live run. A run is
shown when all three hold:

- its checkpoint changed in the last six hours
- `keel status` calls it live
- its pull request, if it has one, is still open (`gh pr list --state open`, read at most
  once a minute; without `gh` nothing is hidden on these grounds)

The last rule exists because `keel merge` used to leave a merged run's checkpoint at s10
(#1448), so old checkpoints read as waiting on the merge window. Outside a git repository,
only the session's own folder is read.

It scans:

- once when the session starts
- every five seconds while a run is live (or a worktree is failing), every 30 seconds when
  there is none
- right after any Bash call that runs `keel`

Only one scan runs at a time. A read asked for after a keel command, by the pane or
by **Refresh** starts once the running one ends, so it is taken after the command returned.
A keel command run in the background returns at once; the next poll picks up what it writes.

The step names come from the status contract (`keel.progress-status.v1`), so a renamed or
added step shows up without a mod release.

It follows the same rule as keel-visual: it **only reads**. It never writes the
checkpoint or ledger and never drives a run. So it shows any run in the repository's
worktrees, whether you, Claude Code or Codex started it.

## Install

Install keel itself first ([install guide](../../docs/keel/install.md)). You also need Claude Code **2.1.287 or newer**, with mods enabled for your account. If
`claude plugin test` prints `hooks modules are turned off in this process`, mods are off
for your account and no local setting turns them on.

```text
/plugin marketplace add berkayturanci/keel
/plugin install keel-progress@keel
```

Start Claude Code in the directory that holds `.keel/project.yaml`, and make sure `keel`
is on your `PATH`.

Other hosts are not affected. Only Claude Code's plugin loader reads this directory: the
`keel` plugin, the Python package, and the Codex, Cursor and Antigravity adapters don't
change.

## Develop

```bash
claude --plugin-dir mods/keel-progress   # loads it, hot-reloads on save
claude plugin validate mods/keel-progress
cd mods/keel-progress && claude plugin test
```

`hooks/view.js` is the pure projection from status JSON to what is drawn, and
`hooks/register.js` holds the hooks. The tests in `tests/` stub `git worktree list`,
`gh`, `keel status`, the checkpoint times and the clock.

## Limits

- It shows one repository: the one the session runs in. Runs in other repositories need a
  session there.
- A worktree whose `keel status` fails is listed in the pane and left out of the band.
- It checks for `.keel/project.yaml` when the session starts. A project created later in
  the session (`keel init`) shows after `/reload-plugins` or a new session.
- A run left behind without a pull request keeps showing for six hours after its
  checkpoint last changed.
