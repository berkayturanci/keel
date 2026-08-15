# Keel Workflow Core — VS Code & Cursor Extension

Turn coding agents into work owners directly inside **VS Code** and **Cursor**.

## Features

- 🟢 **Live Merge Window Status Bar**: Real-time indicator of the nocturnal no-merge window (`Window Open` / `Night Lock Active`).
- ⚡ **Real-Time Step Tracker**: Displays currently active backbone steps (`s4 implement`, `s7 review`) and active issue numbers from `.keel/activity/`.
- ⌘ **Command Palette Integration**:
  - `Keel: Ship Issue End-to-End (/keel:ship)`
  - `Keel: Run Swarm on Backlog (/keel:swarm)`
  - `Keel: Check Merge Window Status`
  - `Keel: Run Command Gates (Test & Lint)`
  - `Keel: View Token & USD Cost Report`
  - `Keel: Open Web Visualizer`

## Configuration

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `keel.executablePath` | `string` | `"keel"` | Path to the `keel` executable. |
| `keel.autoRefreshInterval` | `number` | `30` | Refresh interval in seconds. |

## Installation

Install from the **VS Code Marketplace** or **Open VSX Registry**:
```bash
code --install-extension berkayturanci.keel-vscode
```
or inside **Cursor**:
```bash
cursor --install-extension berkayturanci.keel-vscode
```
