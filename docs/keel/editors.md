# Editor Integration — VS Code & Cursor

Keel provides official companion extensions for **Visual Studio Code** and **Cursor**.

> **This is not the agent plugin.** The extension on this page adds a status bar,
> a run tracker and command-palette entries to the *editor*. It does not install
> keel's skills or the `/keel:<command>` set into an agent — for that, including
> Cursor, see [`install.md`](install.md). The two share the word "Cursor" and
> nothing else, which is the confusion this note exists to stop.

## Features

- 🟢 **Status Bar Merge Window Indicator**: Live countdown and state of the configured merge window (`Europe/Istanbul` or project timezone).
- ⚡ **Live Run Tracker**: Observes `.keel/activity/` to show active step progress (e.g. `$(gear~spin) Keel: s4 implement (#747)`).
- ⌘ **Command Palette Shortcuts**:
  - `Keel: Ship Issue End-to-End (/keel:ship)`
  - `Keel: Run Swarm on Backlog (/keel:swarm)`
  - `Keel: Check Merge Window Status`
  - `Keel: Run Command Gates (Test & Lint)`
  - `Keel: View Token & USD Cost Report`
  - `Keel: Open Web Visualizer`

## Installation

```bash
# Visual Studio Code
code --install-extension berkayturanci.keel-vscode

# Cursor AI Editor
cursor --install-extension berkayturanci.keel-vscode
```
