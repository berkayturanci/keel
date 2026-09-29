/* ============================================================
   Keel Workflow Core — VS Code & Cursor Extension
   Provides status bar indicators for the night merge window,
   active issue tracking, and command palette shortcuts.
   ============================================================ */

const vscode = require("vscode");
const cp = require("child_process");
const path = require("path");
const fs = require("fs");

let statusBarItem;
let refreshTimer;

function getKeelCmd() {
  const config = vscode.workspace.getConfiguration("keel");
  return config.get("executablePath") || "keel";
}

function getWorkspaceRoot() {
  const folders = vscode.workspace.workspaceFolders;
  return folders && folders.length > 0 ? folders[0].uri.fsPath : null;
}

function runKeel(args, cwd, callback) {
  const cmd = getKeelCmd();
  const fullCmd = `${cmd} ${args.join(" ")}`;
  cp.exec(fullCmd, { cwd: cwd || process.cwd() }, (err, stdout, stderr) => {
    callback(err, stdout ? stdout.trim() : "", stderr ? stderr.trim() : "");
  });
}

// `keel window` takes the config path and nothing else: it has no `--json`. The
// extension used to pass one, argparse rejected it (exit 2), and the error branch
// fell back to "open" — so the bar said "Keel: Open" through every night lock.
const WINDOW_ARGS = ["window", ".keel/project.yaml"];

// What `keel window` prints, one line each: `merge window OPEN  [tz HH:MM-HH:MM]`,
// `merge window CLOSED (night no-merge)  [...]`, or `no merge window configured (...)`.
// Anything else — a missing `keel`, a config error — is "unknown", never "open".
function parseWindowOutput(err, stdout, stderr) {
  const line = (stdout || "").trim();
  if (!err) {
    if (/^merge window OPEN\b/.test(line)) return { state: "open", detail: line };
    if (/^merge window CLOSED\b/.test(line)) return { state: "closed", detail: line };
    if (/^no merge window configured\b/.test(line)) return { state: "none", detail: line };
  }
  const reason = (stderr || "").trim() || (err && err.message) || line || "no output";
  return { state: "unknown", detail: reason };
}

const WINDOW_TEXT = {
  open: "Window Open",
  closed: "Night Lock Active",
  none: "No merge window configured",
  unknown: "Unknown",
};

function updateStatusBar() {
  const root = getWorkspaceRoot();
  if (!root) {
    statusBarItem.hide();
    return;
  }

  const projYaml = path.join(root, ".keel", "project.yaml");
  if (!fs.existsSync(projYaml)) {
    statusBarItem.hide();
    return;
  }

  // Query window status
  runKeel(WINDOW_ARGS, root, (err, stdout, stderr) => {
    const windowState = parseWindowOutput(err, stdout, stderr);
    const windowText = WINDOW_TEXT[windowState.state];

    // Check for active activity records in .keel/activity/
    const actDir = path.join(root, ".keel", "activity");
    let activePhase = null;
    let activeIssue = null;

    if (fs.existsSync(actDir)) {
      try {
        const files = fs.readdirSync(actDir).filter(f => f.endsWith(".json"));
        for (const file of files) {
          const rec = JSON.parse(fs.readFileSync(path.join(actDir, file), "utf-8"));
          if (rec.status === "running") {
            activePhase = rec.phase;
            activeIssue = rec.issue;
            break;
          }
        }
      } catch (e) {}
    }

    if (activePhase) {
      statusBarItem.text = `$(gear~spin) Keel: ${activePhase}${activeIssue ? ` (#${activeIssue})` : ""}`;
      statusBarItem.tooltip = `Keel Run Active (${activePhase})\nMerge Window: ${windowText}\nClick to view options.`;
      statusBarItem.backgroundColor = undefined;
    } else if (windowState.state === "open") {
      statusBarItem.text = `$(git-merge) Keel: Open`;
      statusBarItem.tooltip = `Keel Merge Window is OPEN (Merges permitted)\n${windowState.detail}\nClick for commands.`;
      statusBarItem.backgroundColor = undefined;
    } else if (windowState.state === "closed") {
      statusBarItem.text = `$(lock) Keel: Night Lock`;
      statusBarItem.tooltip = `Keel Merge Window is CLOSED (Night lock active)\n${windowState.detail}\nMerges queued until morning window opens.\nClick for commands.`;
      statusBarItem.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
    } else if (windowState.state === "none") {
      statusBarItem.text = `$(git-merge) Keel: No Window`;
      statusBarItem.tooltip = `No merge window configured (needs timezone + merge_window) — merges are not time-gated.\nClick for commands.`;
      statusBarItem.backgroundColor = undefined;
    } else {
      statusBarItem.text = `$(question) Keel: Window ?`;
      statusBarItem.tooltip = `Could not read the merge window: ${windowState.detail}\nClick for commands.`;
      statusBarItem.backgroundColor = undefined;
    }

    statusBarItem.show();
  });
}

function activate(context) {
  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusBarItem.command = "keel.window";
  context.subscriptions.push(statusBarItem);

  // Register commands
  context.subscriptions.push(
    vscode.commands.registerCommand("keel.window", () => {
      const root = getWorkspaceRoot();
      runKeel(WINDOW_ARGS, root, (err, stdout) => {
        vscode.window.showInformationMessage(stdout || "Keel window checked.");
        updateStatusBar();
      });
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("keel.gates", () => {
      const root = getWorkspaceRoot();
      const terminal = vscode.window.createTerminal("Keel Gates");
      terminal.show();
      terminal.sendText("keel run-gates .keel/project.yaml");
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("keel.cost", () => {
      const root = getWorkspaceRoot();
      runKeel(["cost-report"], root, (err, stdout) => {
        if (!err && stdout) {
          vscode.window.showInformationMessage(stdout);
        } else {
          vscode.window.showErrorMessage("Failed to compute cost report: " + (err ? err.message : "unknown"));
        }
      });
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("keel.ship", async () => {
      const issue = await vscode.window.showInputBox({
        prompt: "Enter GitHub Issue number to ship (e.g. 747)",
        placeHolder: "747"
      });
      if (issue) {
        const terminal = vscode.window.createTerminal("Keel Ship");
        terminal.show();
        terminal.sendText(`keel ship .keel/project.yaml --issue ${issue.trim()}`);
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("keel.swarm", async () => {
      const issues = await vscode.window.showInputBox({
        prompt: "Enter comma-separated issue numbers for Swarm DAG (e.g. 740,741,742)",
        placeHolder: "740,741,742"
      });
      if (issues) {
        const terminal = vscode.window.createTerminal("Keel Swarm");
        terminal.show();
        // Swarm is experimental: a live run produces no commits and no PRs (see #1281), so the
        // palette stops at the planning half rather than starting workers that land nothing.
        terminal.sendText(`keel swarm-plan .keel/project.yaml --issues ${issues.trim()} --tree`);
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("keel.visual", () => {
      vscode.env.openExternal(vscode.Uri.parse("https://keel-ship.dev/#swarm"));
    })
  );

  // File watcher on .keel/
  const watcher = vscode.workspace.createFileSystemWatcher("**/.keel/**");
  watcher.onDidChange(updateStatusBar);
  watcher.onDidCreate(updateStatusBar);
  watcher.onDidDelete(updateStatusBar);
  context.subscriptions.push(watcher);

  // Periodic timer
  updateStatusBar();
  refreshTimer = setInterval(updateStatusBar, 30000);
}

function deactivate() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

module.exports = {
  activate,
  deactivate,
  // Exported for tests/test_editor_extension.py, which drives them under node.
  WINDOW_ARGS,
  parseWindowOutput
};
