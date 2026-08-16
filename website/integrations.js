/* ============================================================
   keel — Ecosystem & Integrations Catalog
   Interactive catalog of 32 AI coding agents, LLM backends,
   agent skills, and developer platforms supported out-of-the-box.
   Uses authentic brand logo assets and 100% real Keel CLI commands.
   Zero external dependencies — pure client-side vanilla JS.
   ============================================================ */

(function () {
  "use strict";

  var INTEGRATIONS = [
    // --- 1. AI Agents & Coding Assistants (12) ---
    {
      id: "claude-code",
      name: "Claude Code",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Native slash-command adapter and marketplace plugin (/keel:ship, /keel:swarm, /keel:wrap).",
      cmd: "/keel:ship 101",
      logo: "logos/claude.svg"
    },
    {
      id: "cursor",
      name: "Cursor",
      category: "assistants",
      badge: "AI Code Editor",
      desc: "Integrated via knobs.delegate_profiles, background task sync, and AGENTS.md rules.",
      cmd: "keel ship .keel/project.yaml --delegate cursor",
      logo: "logos/cursor.svg"
    },
    {
      id: "gemini-cli",
      name: "Gemini CLI",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Shared skill commands in .agents/skills/keel-* with native Gemini multimodal & code reasoning.",
      cmd: "keel ship .keel/project.yaml --host-agent gemini",
      logo: "logos/gemini-cli.svg"
    },
    {
      id: "antigravity",
      name: "Google Antigravity",
      category: "assistants",
      badge: "AI Assistant",
      desc: "First-class Antigravity paired programming skills, reactive message wakeups, and AGENTS.md rules.",
      cmd: "keel ship .keel/project.yaml --host-agent agy",
      logo: "logos/google-antigravity.png"
    },
    {
      id: "openai-codex",
      name: "OpenAI Codex",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Dedicated .codex-plugin/ package, Codex CLI delegates, and structured evidence contracts.",
      cmd: "keel ship .keel/project.yaml --host-agent codex",
      logo: "logos/openai.svg"
    },
    {
      id: "devin",
      name: "Devin / External Agents",
      category: "assistants",
      badge: "Autonomous Agent",
      desc: "Autonomous coding agent runs gated by Keel merge lock, review cycles, and 3-vendor jury.",
      cmd: "keel ship .keel/project.yaml --issue 101 --live",
      logo: "logos/devin.png"
    },
    {
      id: "aider",
      name: "Aider",
      category: "assistants",
      badge: "Terminal Agent",
      desc: "Interactive terminal pair programmer mapped to Keel's s4 implement via generic CLI delegates.",
      cmd: "keel ship .keel/project.yaml --delegate aider",
      logo: "logos/aider.svg"
    },
    {
      id: "opencode",
      name: "OpenCode",
      category: "assistants",
      badge: "Open Assistant",
      desc: "Open-source coding assistant integrated via standard POSIX CLI delegate profiles.",
      cmd: "keel ship .keel/project.yaml --delegate opencode",
      logo: "logos/opencode.svg"
    },
    {
      id: "trae",
      name: "Trae",
      category: "assistants",
      badge: "AI Code Editor",
      desc: "Adaptive AI editor companion configured via delegate profiles and Keel deterministic gates.",
      cmd: "keel ship .keel/project.yaml --delegate trae",
      logo: "logos/trae.jpg"
    },
    {
      id: "github-copilot",
      name: "GitHub Copilot",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Copilot workspace and coding actions verified against deterministic Keel pre-merge evidence gates.",
      cmd: "keel evidence-verify .keel/project.yaml --phase pre-merge",
      logo: "logos/githubcopilot.svg"
    },
    {
      id: "kimi-cli",
      name: "Kimi CLI",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Moonshot Kimi coding assistant integration for large-context codebase analysis and implementation.",
      cmd: "keel ship .keel/project.yaml --delegate kimi",
      logo: "logos/kimi-cli.png"
    },
    {
      id: "hermes",
      name: "Hermes Agent",
      category: "assistants",
      badge: "Autonomous Agent",
      desc: "Lightweight autonomous agent runner dispatched across parallel Swarm isolated worktrees.",
      cmd: "keel swarm-run .keel/project.yaml",
      logo: "logos/hermes.png"
    },

    // --- 2. Supported LLM Models & Backends (8) ---
    {
      id: "anthropic-claude",
      name: "Anthropic Claude",
      category: "backends",
      badge: "LLM Backend",
      desc: "Claude 3.7 Sonnet, 3.5 Sonnet, Haiku, and Opus supported for implementer and reviewer roles.",
      cmd: "keel ship .keel/project.yaml --implementer claude-3-7-sonnet",
      logo: "logos/anthropic.svg"
    },
    {
      id: "google-gemini",
      name: "Google Gemini",
      category: "backends",
      badge: "LLM Backend",
      desc: "Gemini 2.5 Flash and Pro with fast multi-token reasoning and low token cost tracking.",
      cmd: "keel ship .keel/project.yaml --implementer gemini-2.5-flash",
      logo: "logos/googlegemini.svg"
    },
    {
      id: "openai",
      name: "OpenAI GPT & o1",
      category: "backends",
      badge: "LLM Backend",
      desc: "GPT-4o, o1, o3-mini, and GPT-4o-mini supported across single-issue ships and jury panels.",
      cmd: "keel ship .keel/project.yaml --implementer gpt-4o",
      logo: "logos/openai.svg"
    },
    {
      id: "deepseek",
      name: "DeepSeek V3 / R1",
      category: "backends",
      badge: "LLM Backend",
      desc: "High-reasoning, low-cost DeepSeek chat and reasoner models with exact token expenditure ledger.",
      cmd: "keel ship .keel/project.yaml --implementer deepseek-reasoner",
      logo: "logos/deepseek.svg"
    },
    {
      id: "ollama-local",
      name: "Ollama (Local / Offline)",
      category: "backends",
      badge: "Local Backend",
      desc: "100% on-device, offline model execution with zero API cost and private repository isolation.",
      cmd: "keel ship .keel/project.yaml --delegate ollama:deepseek-r1",
      logo: "logos/ollama.svg"
    },
    {
      id: "aws-bedrock",
      name: "AWS Bedrock",
      category: "backends",
      badge: "Enterprise Cloud",
      desc: "Enterprise VPC-isolated Claude and Llama endpoints via standard AWS credentials.",
      cmd: "export ANTHROPIC_BEDROCK_AWS_REGION=us-east-1",
      logo: "logos/aws.svg"
    },
    {
      id: "azure-openai",
      name: "Azure OpenAI",
      category: "backends",
      badge: "Enterprise Cloud",
      desc: "SOC2 and HIPAA compliant OpenAI models hosted in private Microsoft Azure tenancies.",
      cmd: "export AZURE_OPENAI_ENDPOINT=https://...",
      logo: "logos/azure.svg"
    },
    {
      id: "openrouter",
      name: "OpenRouter",
      category: "backends",
      badge: "Unified Routing",
      desc: "Dynamic multi-model fallback and lowest-latency routing with token cost analytics.",
      cmd: "keel cost-report .keel/project.yaml --json",
      logo: "logos/openrouter.svg"
    },

    // --- 3. Agent Skills & Multi-Agent Architecture (6) ---
    {
      id: "addyosmani-skills",
      name: "Addy Osmani Agent Skills",
      category: "skills",
      badge: "Skill Library",
      desc: "Production-grade engineering workflows: TDD, spec-driven design, security audits & progressive disclosure.",
      cmd: "knobs.skills: ['addyosmani:tdd-workflow']",
      logo: "logos/addyosmani.png"
    },
    {
      id: "mcp-protocol",
      name: "Model Context Protocol",
      category: "skills",
      badge: "Open Standard",
      desc: "Expose Keel's deterministic backbone tools and GitHub transport via native MCP protocol.",
      cmd: "keel ship .keel/project.yaml --transport mcp",
      logo: "logos/mcp.svg"
    },
    {
      id: "compound-engineering",
      name: "Compound Engineering",
      category: "skills",
      badge: "Engineering System",
      desc: "Senior engineering reflexes and anti-rationalization patterns hooked into s4 implement and s7 review.",
      cmd: "keel ship .keel/project.yaml --compound",
      logo: "logos/compound.svg"
    },
    {
      id: "ai-jury",
      name: "Multi-Vendor AI Jury",
      category: "skills",
      badge: "Consensus Engine",
      desc: "Independent 3-vendor jury panel (Anthropic + OpenAI + Google) ensuring unanimous pre-merge verdicts.",
      cmd: "keel ship .keel/project.yaml --jury",
      logo: "logos/jury.svg"
    },
    {
      id: "git-worktrees",
      name: "Swarm Worktrees",
      category: "skills",
      badge: "Concurrency Engine",
      desc: "Zero dirty-checkout collisions: parallel multi-agent workers run in isolated git worktrees.",
      cmd: "keel swarm-plan .keel/project.yaml --issues 101,102",
      logo: "logos/swarm.svg"
    },
    {
      id: "pre-commit",
      name: "Pre-Commit Quality Gates",
      category: "skills",
      badge: "Quality Gate",
      desc: "Deterministic local gates ensuring code formatting, security, and schema validation before any commit.",
      cmd: "keel run-gates .keel/project.yaml",
      logo: "logos/precommit.svg"
    },

    // --- 4. Platforms & Environments (6) ---
    {
      id: "github-actions",
      name: "Official GitHub Action",
      category: "platforms",
      badge: "CI/CD Automation",
      desc: "Official 1-click composite action (berkayturanci/keel-action@v1) for autonomous issue shipping and swarm runs.",
      cmd: "uses: berkayturanci/keel-action@v1",
      logo: "logos/githubactions.svg"
    },
    {
      id: "homebrew",
      name: "Homebrew Tap",
      category: "platforms",
      badge: "Package Manager",
      desc: "Instant macOS and Linux installation via homebrew tap (brew install keel).",
      cmd: "brew tap berkayturanci/keel && brew install keel",
      logo: "logos/homebrew.svg"
    },
    {
      id: "vscode-ext",
      name: "VS Code & Cursor Extension",
      category: "platforms",
      badge: "Editor Extension",
      desc: "Status bar merge window indicator and command palette integration for VS Code and Cursor.",
      cmd: "code --install-extension berkayturanci.keel-vscode",
      logo: "logos/vscode.svg"
    },
    {
      id: "curl-installer",
      name: "Standalone POSIX Installer",
      category: "platforms",
      badge: "Zero-Dependency",
      desc: "One-line standalone curl installer with zero sudo or package manager requirements.",
      cmd: "curl -fsSL https://raw.githubusercontent.com/berkayturanci/keel/main/scripts/install.sh | sh",
      logo: "logos/curl.svg"
    },
    {
      id: "pypi-pipx",
      name: "PyPI & pipx",
      category: "platforms",
      badge: "Python Ecosystem",
      desc: "Standard Python distribution supporting isolated virtual environments and global CLI usage.",
      cmd: "pipx install keel",
      logo: "logos/pypi.svg"
    },
    {
      id: "cross-platform",
      name: "Linux, macOS & Windows",
      category: "platforms",
      badge: "Cross-Platform",
      desc: "Pure stdlib-first core running deterministically across POSIX shells, macOS, Linux, and Windows.",
      cmd: "keel doctor",
      logo: "logos/linux.svg"
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
        '    <div class="integ-avatar tag-' + item.category + '" aria-hidden="true">',
        '      <img src="' + item.logo + '" alt="" class="integ-icon-img" width="22" height="22" loading="lazy" />',
        '    </div>',
        '    <div class="integ-meta">',
        '      <span class="integ-name">' + item.name + '</span>',
        '      <span class="integ-badge">' + item.badge + '</span>',
        '    </div>',
        '  </div>',
        '  <p class="integ-desc">' + item.desc + '</p>',
        '  <div class="integ-cmd-box">',
        '    <code>' + item.cmd + '</code>',
        '    <button type="button" class="integ-copy-btn" data-copy="' + item.cmd.replace(/"/g, '&quot;') + '" title="Copy command" aria-label="Copy ' + item.name + ' command">Copy</button>',
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
        var origLabel = btn.getAttribute("aria-label") || "Copy command";
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () {
            btn.textContent = "Copied! ✓";
            btn.setAttribute("aria-label", "Copied to clipboard");
            setTimeout(function () {
              btn.textContent = "Copy";
              btn.setAttribute("aria-label", origLabel);
            }, 2000);
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
