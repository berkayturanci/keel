# Editor Integration — VS Code & Cursor

Keel provides official companion extensions for **Visual Studio Code** and **Cursor**.

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
