# keel-progress

An optional [Claude Code mod](https://code.claude.com/docs/en/plugins/mods/overview) that
shows this repository's live keel runs inside Claude Code, so you don't need a second
terminal running `keel-visual dash`.

```text
▸ main                              keel 1: #1444 ▰▰▰▰▰▰▰▰▰▰▶▱▱ s10 merge · waiting: merge-window · PR #1445  more
  fix/merge-marks-checkpoint        keel 2: #1448 ▰▰▰▰▰▰▰▶▱▱▱▱▱ s7 review · waiting: review · PR #1449
```

- **Above the prompt:** one line per live run (`active`, `waiting` or `interrupted`). Each
  shows the issue, a bar over the backbone steps (`▰` done, `▶` current, `▱` pending), the
  current step, why it is waiting (or, in red, why it stopped), and the pull request. With
  more than one run each line starts with its worktree's branch, as wide as the band allows.
  The session's own run is first, marked `▸` and drawn bright. Nothing is drawn when no run
  is live.
  - **Click a run's issue** (or type its digit, `1`–`9`, into an empty prompt) to open the pane
    on that run.
  - **`more` / `less`** lists every run, not just the first three, each with a second line
    holding the whole branch name and the worktree path.
- **`/keel-progress`:** opens a pane on one run: its whole branch name and worktree, every
  step, the history counts (shipped, blocked, deferred, skipped) and the next queued issue.
  The other live runs are listed below it as buttons to switch to, with **Refresh** and
  **Close**. A worktree whose `keel status` fails is listed in the pane with its error and
  left out of the band until a read succeeds again.

## How it reads the runs

Parallel `keel ship` runs each work in a worktree of their own and write that worktree's
checkpoint. The mod always reads the session's own folder, then lists the repository's
worktrees with `git worktree list` and runs `keel status --json` in each other worktree whose
checkpoint (found where the project's status contract says it lives) changed in the last
24 hours. A run is shown when:

- `keel status` calls it live,
- it is the newest copy of its run: one run can leave checkpoints in several worktrees (a
  worktree nested in another), and only the most recently written one is shown; the pane
  counts the stale copies, and
- its pull request, if it has one, is still open (`gh pr list --state open`, read at most
  once a minute, and once more when a scan meets a PR the list doesn't hold; without `gh`
  nothing is hidden on these grounds)

The last rule exists because `keel merge` used to leave a merged run's checkpoint at s10
(#1448), so old checkpoints read as waiting on the merge window. Outside a git repository,
or when `git` can't run, only the session's own folder is read. With no live run, the pane
still shows the session's project: no active run, its history counts and next issue.

It scans:

- once when the session starts
- every five seconds while a run is live (or this session's own `keel status` is failing),
  every 30 seconds when
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
- A run in another worktree left behind without a pull request keeps showing for 24 hours
  after its checkpoint last changed.
- While no run is live it scans every 30 seconds, so a run started from another terminal
  (or by Codex) can take that long to appear. A keel command in this session scans at once.
