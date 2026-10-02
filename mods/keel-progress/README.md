# keel-progress

An optional [Claude Code mod](https://code.claude.com/docs/en/plugins/mods/overview) that
shows the live keel run inside Claude Code, so you don't need a second terminal running
`keel-visual play --follow`.

```text
keel #1022 ▰▰▰▰▰▰▰▰▰▰▶▱▱ s10 merge · waiting: merge-window · PR #1027
```

- **Above the prompt:** one line while a run is `active`, `waiting` or `interrupted`. It
  shows the issue, a bar over the backbone steps (`▰` done, `▶` current, `▱` pending), the
  current step, why it is waiting, and the pull request. Nothing is drawn when no run is
  live.
- **`/keel-progress`:** opens a pane listing every step, the history counts (shipped,
  blocked, deferred, skipped) and the next queued issue, with **Refresh** and **Close**
  buttons. If `keel status` fails, the pane shows the error and the line above the prompt
  stays empty.

## How it reads the run

The mod runs `keel status .keel/project.yaml --json` from the session's working directory:

- once when the session starts
- every five seconds while a run is live, every 30 seconds when there is none
- right after any Bash call whose command mentions `keel`

Only one `keel status` runs at a time; a refresh asked for meanwhile waits for it.

The step names come from the status contract (`keel.progress-status.v1`), so a renamed or
added step shows up without a mod release.

It follows the same rule as keel-visual: it **only reads**. It never writes the
checkpoint or ledger and never drives a run. So it shows any run whose state files are in
this directory, whether you, Claude Code or Codex started it.

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
`hooks/register.js` holds the hooks. The tests in `tests/` stub `keel status` and the
clock.

## Limits

- It shows one project: the `.keel/project.yaml` in the session's working directory.
  Runs in other worktrees aren't shown yet; `keel-visual dash` still covers those.
- It shows whatever the checkpoint says. If a run left its checkpoint behind without
  closing, the line above the prompt keeps showing it until `keel` clears it.
