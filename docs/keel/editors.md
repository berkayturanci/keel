# Editor Integration — VS Code & Cursor

Keel ships the source of a companion extension for **Visual Studio Code** and **Cursor**
(`editors/vscode`); it is not published to a marketplace, so you build it from source (see
[Installation](#installation)).

> **This is not the agent plugin.** The extension on this page adds a status bar,
> a run tracker and command-palette entries to the *editor*. It does not install
> keel's skills or the `/keel:<command>` set into an agent — for that, including
> Cursor, see [`install.md`](install.md). The two share the word "Cursor" and
> nothing else, which is the confusion this note exists to stop.

## Features

- 🟢 **Status Bar Merge Window Indicator**: The state of the merge window configured in
  `.keel/project.yaml` (its `timezone` + `merge_window`), read from `keel window` every 30 s
  and on any change under `.keel/`: `Keel: Open`, `Keel: Night Lock`, `Keel: No Window` when
  none is configured, or `Keel: Window ?` when `keel window` could not answer. It shows the
  state only — there is no countdown.
- ⚡ **Live Run Tracker**: Observes `.keel/activity/` to show active step progress (e.g. `$(gear~spin) Keel: s4 implement (#747)`).
- ⌘ **Command Palette Shortcuts**:
  - `Keel: Ship Issue End-to-End (/keel:ship)` — despite the title, this opens a terminal
    running `keel ship .keel/project.yaml --issue N`, keel's dry assessment of the issue; it
    ships nothing. The end-to-end drive is `/keel:ship` in an agent host.
  - `Keel: Plan a Swarm over the Backlog (experimental)` — runs `swarm-plan --tree` only, because a
    live swarm lands nothing yet ([#1281](https://github.com/berkayturanci/keel/issues/1281))
  - `Keel: Check Merge Window Status`
  - `Keel: Run Command Gates (Test & Lint)`
  - `Keel: View Token Counts & Estimated USD Cost`
  - `Keel: Open Web Visualizer` — opens the swarm section of the keel website
    (`https://keel-ship.dev/#swarm`), not a live view of this workspace.

## Installation

The extension is **not published to the VS Code Marketplace or Open VSX yet**, so there is
no `--install-extension <id>` command to run. Build and install it from source instead:

```bash
cd editors/vscode && npm install && npx @vscode/vsce package
code --install-extension keel-vscode-*.vsix     # or: cursor --install-extension keel-vscode-*.vsix
```
