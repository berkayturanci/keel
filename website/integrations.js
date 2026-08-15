/* ============================================================
   keel — Ecosystem & Integrations Catalog
   Interactive catalog of 32+ AI agents, LLM backends,
   skill packs, and developer platforms supported out-of-the-box.
   Zero external dependencies — pure client-side vanilla JS.
   ============================================================ */

(function () {
  "use strict";

  var INTEGRATIONS = [
    // --- AI Assistants / Coding Agents (12) ---
    {
      id: "claude-code",
      name: "Claude Code",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Full slash-command adapter and marketplace plugin (/keel:ship, /keel:swarm, /keel:wrap).",
      cmd: "keel ship --delegate claude",
      iconText: "CL"
    },
    {
      id: "cursor",
      name: "Cursor",
      category: "assistants",
      badge: "AI Code Editor",
      desc: "Native status bar merge window indicator, background task sync, and command palette integration.",
      cmd: "cursor --install-extension berkayturanci.keel-vscode",
      iconText: "CR"
    },
    {
      id: "gemini-cli",
      name: "Gemini CLI",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Shared skill commands in .agents/skills/keel-* with native Gemini 2.5 multimodal integration.",
      cmd: "keel ship --delegate gemini",
      iconText: "GM"
    },
    {
      id: "antigravity",
      name: "Google Antigravity",
      category: "assistants",
      badge: "AI Assistant",
      desc: "First-class Antigravity paired programming skills, reactive message wakeups, and AGENTS.md rules.",
      cmd: "keel ship --delegate agy",
      iconText: "AG"
    },
    {
      id: "openai-codex",
      name: "OpenAI Codex",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Native ChatGPT/Codex plugins and skill adapters with structured reasoning evidence output.",
      cmd: "keel ship --delegate codex",
      iconText: "CX"
    },
    {
      id: "devin",
      name: "Devin",
      category: "assistants",
      badge: "Autonomous Agent",
      desc: "Autonomous cloud agent webhook triggers with Keel merge lock and multi-vendor jury landing.",
      cmd: "POST https://api.keel.dev/v1/ship",
      iconText: "DV"
    },
    {
      id: "aider",
      name: "Aider",
      category: "assistants",
      badge: "Terminal Agent",
      desc: "Interactive terminal pair programmer mapped to Keel's s4 implement and s8 test gates.",
      cmd: "keel ship --delegate aider",
      iconText: "AI"
    },
    {
      id: "opencode",
      name: "OpenCode",
      category: "assistants",
      badge: "Open Assistant",
      desc: "Open-source coding assistant integrated via standard POSIX CLI delegate profiles.",
      cmd: "keel ship --delegate opencode",
      iconText: "OC"
    },
    {
      id: "trae",
      name: "Trae",
      category: "assistants",
      badge: "AI Code Editor",
      desc: "Adaptive AI editor companion supported via VS Code extension manifest and Keel CLI.",
      cmd: "keel run-gates .keel/project.yaml",
      iconText: "TR"
    },
    {
      id: "github-copilot",
      name: "GitHub Copilot",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Copilot workspace and CLI actions bound to deterministic Keel pre-merge evidence gates.",
      cmd: "keel evidence-verify .keel/project.yaml",
      iconText: "CP"
    },
    {
      id: "kimi-cli",
      name: "Kimi CLI",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Moonshot Kimi coding assistant integration for large-context codebase analysis and implementation.",
      cmd: "keel ship --delegate kimi",
      iconText: "KM"
    },
    {
      id: "hermes",
      name: "Hermes Agent",
      category: "assistants",
      badge: "Autonomous Agent",
      desc: "Lightweight autonomous agent runner dispatched in isolated Swarm worktrees.",
      cmd: "keel swarm-run .keel/project.yaml",
      iconText: "HM"
    },

    // --- LLM Backends / Providers (8) ---
    {
      id: "anthropic-claude",
      name: "Anthropic Claude",
      category: "backends",
      badge: "LLM Backend",
      desc: "Claude 3.7 Sonnet, 3.5 Sonnet, Haiku, and 3 Opus supported with native tool calls and pricing tracking.",
      cmd: "knobs.implementer_agents.core: claude-3-7-sonnet",
      iconText: "AN"
    },
    {
      id: "google-gemini",
      name: "Google Gemini",
      category: "backends",
      badge: "LLM Backend",
      desc: "Gemini 2.5 Flash and Pro with high-speed multi-token context analysis and $0.15/M tier pricing.",
      cmd: "knobs.implementer_agents.core: gemini-2.5-flash",
      iconText: "GG"
    },
    {
      id: "openai",
      name: "OpenAI GPT & o1",
      category: "backends",
      badge: "LLM Backend",
      desc: "GPT-4o, o1, o3-mini, and GPT-4o-mini supported across single-issue ships and jury panels.",
      cmd: "knobs.implementer_agents.core: gpt-4o",
      iconText: "OA"
    },
    {
      id: "deepseek",
      name: "DeepSeek V3 / R1",
      category: "backends",
      badge: "LLM Backend",
      desc: "High-reasoning, low-cost DeepSeek chat and reasoner models with exact token expenditure ledger.",
      cmd: "knobs.implementer_agents.core: deepseek-reasoner",
      iconText: "DS"
    },
    {
      id: "ollama-local",
      name: "Ollama (Local / Offline)",
      category: "backends",
      badge: "Local Backend",
      desc: "100% on-device, offline model execution with zero API cost and private repository isolation.",
      cmd: "knobs.implementer_agents.core: ollama:deepseek-r1",
      iconText: "OL"
    },
    {
      id: "aws-bedrock",
      name: "AWS Bedrock",
      category: "backends",
      badge: "Enterprise Cloud",
      desc: "Enterprise VPC-isolated Claude and Llama endpoints via standard AWS credentials.",
      cmd: "export ANTHROPIC_BEDROCK_AWS_REGION=us-east-1",
      iconText: "BR"
    },
    {
      id: "azure-openai",
      name: "Azure OpenAI",
      category: "backends",
      badge: "Enterprise Cloud",
      desc: "SOC2 and HIPAA compliant OpenAI models hosted in private Microsoft Azure tenancies.",
      cmd: "export AZURE_OPENAI_ENDPOINT=https://...",
      iconText: "AZ"
    },
    {
      id: "openrouter",
      name: "OpenRouter",
      category: "backends",
      badge: "Unified Routing",
      desc: "Dynamic multi-model fallback and lowest-latency routing across 100+ open and proprietary models.",
      cmd: "keel cost-report --json",
      iconText: "OR"
    },

    // --- Engineering Skill Packs & Tooling (6) ---
    {
      id: "addyosmani-skills",
      name: "Addy Osmani Agent Skills",
      category: "skills",
      badge: "Skill Library",
      desc: "Production-grade engineering workflows: TDD, spec-driven design, security audits & progressive disclosure.",
      cmd: "keel skill add github:addyosmani/agent-skills/skills/tdd-workflow",
      iconText: "SK"
    },
    {
      id: "compound-engineering",
      name: "Compound Engineering",
      category: "skills",
      badge: "Engineering System",
      desc: "Senior engineering reflexes and anti-rationalization patterns hooked into s4 implement and s7 review.",
      cmd: "knobs.skills: ['compound:strict-verification']",
      iconText: "CE"
    },
    {
      id: "mcp-protocol",
      name: "Model Context Protocol",
      category: "skills",
      badge: "Open Standard",
      desc: "Expose Keel's deterministic backbone tools as native MCP endpoints for Cursor, Claude, and IDEs.",
      cmd: "keel mcp --port 8484",
      iconText: "MC"
    },
    {
      id: "ai-jury",
      name: "Multi-Vendor AI Jury",
      category: "skills",
      badge: "Consensus Engine",
      desc: "Independent 3-vendor jury panel (Anthropic + OpenAI + Google) ensuring unanimous pre-merge verdicts.",
      cmd: "keel evidence-verify .keel/project.yaml --phase pre-merge",
      iconText: "JR"
    },
    {
      id: "git-worktrees",
      name: "Isolated Worktrees",
      category: "skills",
      badge: "Concurrency Engine",
      desc: "Zero dirty-checkout collisions: every parallel Swarm worker runs in an isolated directory sandboxed by git.",
      cmd: "keel swarm-plan .keel/project.yaml --issues 101,102",
      iconText: "WT"
    },
    {
      id: "pre-commit",
      name: "Pre-Commit Gates",
      category: "skills",
      badge: "Quality Gate",
      desc: "Deterministic local gates ensuring code formatting, security, and schema validation before any commit.",
      cmd: "keel run-gates .keel/project.yaml",
      iconText: "PC"
    },

    // --- Platforms & CI/CD (6) ---
    {
      id: "github-actions",
      name: "Official GitHub Action",
      category: "platforms",
      badge: "CI/CD Automation",
      desc: "Official 1-click composite action (berkayturanci/keel-action@v1) for autonomous issue shipping and swarm runs.",
      cmd: "uses: berkayturanci/keel-action@v1",
      iconText: "GA"
    },
    {
      id: "homebrew",
      name: "Homebrew Tap",
      category: "platforms",
      badge: "Package Manager",
      desc: "Instant macOS and Linux installation via homebrew tap (brew install keel).",
      cmd: "brew tap berkayturanci/keel && brew install keel",
      iconText: "HB"
    },
    {
      id: "vscode-ext",
      name: "VS Code & Cursor Extension",
      category: "platforms",
      badge: "Editor Extension",
      desc: "Status bar merge window indicator and command palette integration for VS Code and Cursor.",
      cmd: "code --install-extension berkayturanci.keel-vscode",
      iconText: "VS"
    },
    {
      id: "curl-installer",
      name: "Standalone POSIX Installer",
      category: "platforms",
      badge: "Zero-Dependency",
      desc: "One-line standalone curl installer with zero sudo or package manager requirements.",
      cmd: "curl -fsSL https://raw.githubusercontent.com/berkayturanci/keel/main/scripts/install.sh | sh",
      iconText: "SH"
    },
    {
      id: "pypi-pipx",
      name: "PyPI & pipx",
      category: "platforms",
      badge: "Python Ecosystem",
      desc: "Standard Python distribution supporting isolated virtual environments and global CLI usage.",
      cmd: "pipx install keel",
      iconText: "PY"
    },
    {
      id: "cross-platform",
      name: "Linux, macOS & Windows",
      category: "platforms",
      badge: "Cross-Platform",
      desc: "Pure stdlib-first core running deterministically across POSIX shells, macOS, Linux, and Windows.",
      cmd: "keel doctor",
      iconText: "OS"
    }
  ];

  var activeCategory = "all";
  var searchQuery = "";

  function filterIntegrations() {
    return INTEGRATIONS.filter(function (item) {
      var matchesCat = activeCategory === "all" || item.category === activeCategory;
      var q = searchQuery.toLowerCase().trim();
      var matchesQuery = !q ||
        item.name.toLowerCase().indexOf(q) >= 0 ||
        item.desc.toLowerCase().indexOf(q) >= 0 ||
        item.badge.toLowerCase().indexOf(q) >= 0 ||
        item.cmd.toLowerCase().indexOf(q) >= 0;
      return matchesCat && matchesQuery;
    });
  }

  function renderGrid() {
    var grid = document.getElementById("integrations-grid");
    var countEl = document.getElementById("integrations-count");
    if (!grid) return;

    var items = filterIntegrations();
    if (countEl) {
      countEl.textContent = items.length + " of " + INTEGRATIONS.length + " integrations";
    }

    if (items.length === 0) {
      grid.innerHTML = '<div class="integ-empty">No integrations found matching "' + searchQuery + '".</div>';
      return;
    }

    var html = [];
    items.forEach(function (item) {
      html.push(
        '<div class="integ-card" data-cat="' + item.category + '">',
        '  <div class="integ-card-top">',
        '    <div class="integ-avatar tag-' + item.category + '">' + item.iconText + '</div>',
        '    <div class="integ-meta">',
        '      <span class="integ-name">' + item.name + '</span>',
        '      <span class="integ-badge">' + item.badge + '</span>',
        '    </div>',
        '  </div>',
        '  <p class="integ-desc">' + item.desc + '</p>',
        '  <div class="integ-cmd-box">',
        '    <code>' + item.cmd + '</code>',
        '    <button type="button" class="integ-copy-btn" data-copy="' + item.cmd.replace(/"/g, '&quot;') + '" title="Copy command">Copy</button>',
        '  </div>',
        '</div>'
      );
    });

    grid.innerHTML = html.join("\n");
    wireCopyButtons();
  }

  function wireCopyButtons() {
    document.querySelectorAll(".integ-copy-btn").forEach(function (btn) {
      btn.onclick = function () {
        var text = btn.getAttribute("data-copy");
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () {
            btn.textContent = "Copied! ✓";
            setTimeout(function () { btn.textContent = "Copy"; }, 2000);
          });
        }
      };
    });
  }

  function wireFilters() {
    document.querySelectorAll(".integ-pill[data-cat]").forEach(function (btn) {
      btn.onclick = function () {
        document.querySelectorAll(".integ-pill[data-cat]").forEach(function (b) {
          b.classList.remove("active");
          b.setAttribute("aria-checked", "false");
        });
        btn.classList.add("active");
        btn.setAttribute("aria-checked", "true");
        activeCategory = btn.getAttribute("data-cat");
        renderGrid();
      };
    });

    var searchInput = document.getElementById("integrations-search");
    if (searchInput) {
      searchInput.oninput = function (e) {
        searchQuery = e.target.value;
        renderGrid();
      };
    }
  }

  function init() {
    wireFilters();
    renderGrid();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

})();
