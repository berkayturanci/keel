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
  runKeel(["window", ".keel/project.yaml", "--json"], root, (err, stdout) => {
    let isOpen = true;
    let windowText = "Window Open";
    if (!err && stdout) {
      try {
        const data = JSON.parse(stdout);
        isOpen = !!data.open;
        if (!isOpen) {
          windowText = "Night Lock Active";
        }
      } catch (e) {}
    }

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
    } else if (isOpen) {
      statusBarItem.text = `$(git-merge) Keel: Open`;
      statusBarItem.tooltip = `Keel Merge Window is OPEN (Merges permitted)\nClick for commands.`;
      statusBarItem.backgroundColor = undefined;
    } else {
      statusBarItem.text = `$(lock) Keel: Night Lock`;
      statusBarItem.tooltip = `Keel Merge Window is CLOSED (Night lock active)\nMerges queued until morning window opens.\nClick for commands.`;
      statusBarItem.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
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
      runKeel(["window", ".keel/project.yaml"], root, (err, stdout) => {
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
        terminal.sendText(`keel swarm-plan .keel/project.yaml --issues ${issues.trim()} && keel swarm-run .keel/project.yaml`);
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("keel.visual", () => {
      vscode.env.openExternal(vscode.Uri.parse("https://berkayturanci.github.io/keel/#swarm"));
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
  deactivate
};
