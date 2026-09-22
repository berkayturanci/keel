/* ============================================================
   keel — Ecosystem & Integrations Catalog
   Interactive catalog of 29 AI coding agents, LLM backends,
   agent skills, and developer platforms supported out-of-the-box.
   Uses authentic brand logo assets and 100% real Keel CLI commands.
   Zero external dependencies — pure client-side vanilla JS.
   ============================================================ */

(function () {
  "use strict";

  // Below the directive, never above it: a `var` before `"use strict"`
  // ends the Directive Prologue and leaves the string an inert
  // expression, silently un-stricting this whole IIFE.
  var srTimer = null;
  // Set the first time the reader touches a filter, so the initial render is
  // silent and every change after it is announced — including clearing the box.
  var srArmed = false;
  // The query is whatever the reader typed, and the empty state puts it into markup.
  function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

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
      cmd: "keel implement .keel/project.yaml 101 --delegate cursor",
      note: "Needs a <code>knobs.delegate_profiles.cursor</code> entry naming <code>cursor-agent</code> — keel ships no profile for it. See <a href='https://github.com/berkayturanci/keel/blob/main/docs/keel/models.md#5-generic-cli-profiles' target='_blank' rel='noopener'>Generic CLI Profiles</a>.",
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
      desc: "Autonomous coding agent runs gated by Keel's merge lock, review cycles, and the review-evidence gate.",
      cmd: "keel ship .keel/project.yaml --issue 101 --live",
      logo: "logos/devin.png"
    },
    {
      id: "aider",
      name: "Aider",
      category: "assistants",
      badge: "Terminal Agent",
      desc: "Interactive terminal pair programmer mapped to Keel's s4 implement via generic CLI delegates.",
      cmd: "keel implement .keel/project.yaml 101 --delegate aider",
      note: "Needs a <code>knobs.delegate_profiles.aider</code> entry naming the <code>aider</code> binary — keel ships no profile for it. See <a href='https://github.com/berkayturanci/keel/blob/main/docs/keel/models.md#5-generic-cli-profiles' target='_blank' rel='noopener'>Generic CLI Profiles</a>.",
      logo: "logos/aider.svg"
    },
    {
      id: "opencode",
      name: "OpenCode",
      category: "assistants",
      badge: "Open Assistant",
      desc: "Open-source coding assistant integrated via standard POSIX CLI delegate profiles.",
      cmd: "keel implement .keel/project.yaml 101 --delegate opencode",
      note: "Needs a <code>knobs.delegate_profiles.opencode</code> entry naming the <code>opencode</code> binary — keel ships no profile for it. See <a href='https://github.com/berkayturanci/keel/blob/main/docs/keel/models.md#5-generic-cli-profiles' target='_blank' rel='noopener'>Generic CLI Profiles</a>.",
      logo: "logos/opencode.svg"
    },
    {
      id: "trae",
      name: "Trae",
      category: "assistants",
      badge: "AI Code Editor",
      desc: "Adaptive AI editor companion configured via delegate profiles and Keel deterministic gates.",
      cmd: "keel implement .keel/project.yaml 101 --delegate trae",
      note: "Needs a <code>knobs.delegate_profiles.trae</code> entry naming the <code>trae</code> binary — keel ships no profile for it. See <a href='https://github.com/berkayturanci/keel/blob/main/docs/keel/models.md#5-generic-cli-profiles' target='_blank' rel='noopener'>Generic CLI Profiles</a>.",
      logo: "logos/trae.jpg"
    },
    {
      id: "github-copilot",
      name: "GitHub Copilot",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Copilot workspace and coding actions verified against deterministic Keel pre-merge evidence gates.",
      cmd: "keel evidence-verify .keel/project.yaml --pr 101 --phase pre-merge",
      logo: "logos/githubcopilot.svg"
    },
    {
      id: "kimi-cli",
      name: "Kimi CLI",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Moonshot Kimi coding assistant integration for large-context codebase analysis and implementation.",
      cmd: "keel implement .keel/project.yaml 101 --delegate kimi",
      note: "Needs a <code>knobs.delegate_profiles.kimi</code> entry naming the Kimi CLI binary — keel ships no profile for it. See <a href='https://github.com/berkayturanci/keel/blob/main/docs/keel/models.md#5-generic-cli-profiles' target='_blank' rel='noopener'>Generic CLI Profiles</a>.",
      logo: "logos/kimi-cli.png"
    },
    {
      id: "hermes",
      name: "Hermes Agent",
      category: "assistants",
      badge: "Autonomous Agent",
      desc: "Lightweight autonomous agent runner. Usable as a delegate on any keel command; the swarm fan-out it was written for is experimental.",
      cmd: "keel ship .keel/project.yaml --issue 12 --delegate hermes",
      note: "Needs a <code>knobs.delegate_profiles.hermes</code> entry naming the agent's binary — keel ships no profile for it. The swarm variant of this command (<code>keel swarm-run … --delegate hermes</code>) is <b>experimental</b> and lands nothing yet — see <a href='https://github.com/berkayturanci/keel/issues/1281' target='_blank' rel='noopener'>#1281</a>. See <a href='https://github.com/berkayturanci/keel/blob/main/docs/keel/models.md#5-generic-cli-profiles' target='_blank' rel='noopener'>Generic CLI Profiles</a>.",
      logo: "logos/hermes.png"
    },

    // --- 2. Supported LLM Models & Backends (6) ---
    {
      id: "anthropic-claude",
      name: "Anthropic Claude",
      category: "backends",
      badge: "LLM Backend",
      desc: "Hosted Anthropic API for the implementer and reviewer roles. keel pins no model catalogue \u2014 keel doctor --providers reports what this machine can reach.",
      cmd: "keel ship .keel/project.yaml --delegate anthropic-api:claude-opus-5",
      logo: "logos/anthropic.svg"
    },
    {
      id: "google-gemini",
      name: "Google Gemini",
      category: "backends",
      badge: "LLM Backend",
      desc: "Hosted Gemini API with per-run token cost tracking; the Antigravity CLI reports its own model list to keel doctor --providers.",
      cmd: "keel ship .keel/project.yaml --delegate agy:gemini-3.8-flash-high",
      logo: "logos/googlegemini.svg"
    },
    {
      id: "openai",
      name: "OpenAI",
      category: "backends",
      badge: "LLM Backend",
      desc: "Hosted OpenAI API across single-issue ships and jury panels; the model id is whichever the vendor currently serves.",
      cmd: "keel ship .keel/project.yaml --delegate openai-api:<model-id>",
      logo: "logos/openai.svg"
    },
    {
      id: "deepseek",
      name: "DeepSeek V3 / R1",
      category: "backends",
      badge: "LLM Backend",
      desc: "High-reasoning, low-cost DeepSeek chat and reasoner models with exact token expenditure ledger.",
      cmd: "keel ship .keel/project.yaml --delegate deepseek",
      note: "DeepSeek is not a built-in vendor. Needs a <code>knobs.delegate_profiles.deepseek</code> entry (<code>vendor: openai-compatible</code>) pointing at DeepSeek's API — keel ships no profile for it. See <a href='https://github.com/berkayturanci/keel/blob/main/docs/keel/models.md#2-openai-compatible-profiles' target='_blank' rel='noopener'>OpenAI-Compatible Profiles</a>.",
      logo: "logos/deepseek.svg"
    },
    {
      id: "ollama-local",
      name: "Ollama (Local / Offline)",
      category: "backends",
      badge: "Local Backend",
      desc: "100% on-device, offline model execution with zero API cost and private repository isolation.",
      cmd: "keel delegate run --provider ollama:deepseek-r1 --role implement --prompt-file task.md",
      logo: "logos/ollama.svg"
    },
    {
      id: "openrouter",
      name: "OpenRouter",
      category: "backends",
      badge: "Unified Routing",
      desc: "Dynamic multi-model fallback and lowest-latency routing with token cost analytics.",
      cmd: "keel cost-report --root . --json",
      logo: "logos/openrouter.svg"
    },

    // --- 3. Agent Skills & Multi-Agent Architecture (6) ---
    {
      id: "addyosmani-skills",
      name: "Addy Osmani Agent Skills",
      category: "skills",
      badge: "Skill Library",
      desc: "Third-party skill libraries live beside keel's own keel-&lt;command&gt; skills in .agents/skills/, which every non-Claude agent reads.",
      cmd: "keel install-adapter skills --root .",
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
      badge: "Cross-Vendor Review",
      desc: "Cross-vendor review panel (e.g. Anthropic + OpenAI + Google); keel can dispatch it as the tier-3 review and gate the merge on its pinned ballots.",
      cmd: "keel ship .keel/project.yaml --jury",
      logo: "logos/jury.svg"
    },
    {
      id: "git-worktrees",
      name: "Swarm Worktrees",
      category: "skills",
      badge: "Experimental",
      desc: "Parallel multi-agent workers in isolated git worktrees. Planning runs; a live run lands nothing yet.",
      cmd: "keel swarm-plan .keel/project.yaml --issues 101,102 --tree",
      note: "<b>Experimental.</b> The planning commands work; a live swarm produces no commits and no pull requests. See <a href='https://github.com/berkayturanci/keel/issues/1281' target='_blank' rel='noopener'>#1281</a>.",
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

    // --- 4. Platforms & Environments (5) ---
    {
      id: "github-actions",
      name: "Official GitHub Action",
      category: "platforms",
      badge: "CI/CD Automation",
      desc: "Official composite action (berkayturanci/keel@v1.24.0) for gates, ship assessment, evidence verification and swarm planning.",
      cmd: "uses: berkayturanci/keel@v1.24.0",
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
      cmd: "pipx install keel-workflow",
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
        item.cmd.toLowerCase().indexOf(q) >= 0 ||
        (item.note || "").toLowerCase().indexOf(q) >= 0;
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

    // Gated on whether the reader has touched a filter, NOT on the query being
    // non-empty. `renderGrid` also runs from `init()` on DOMContentLoaded while
    // the landing view is the overview and this grid is hidden, and announcing
    // "Showing 32 integrations" there interrupts a page nobody opened. But
    // *clearing* the box is a result-set change worth announcing, and an
    // emptiness test silences exactly that.
    var sr = srArmed ? document.getElementById("sr-live-region") : null;
    // Cancelled unconditionally: a keystroke that lands while an announcement
    // is pending must not let the stale one fire after the results moved on.
    if (srTimer) { clearTimeout(srTimer); srTimer = null; }
    if (sr) {
      // "Showing 1 integrations" is the sentence a screen-reader user actually
      // hears, and searching "ollama" produces exactly one match.
      var announcement = items.length === 0
        ? 'No integrations found matching "' + searchQuery + '"'
        : 'Showing ' + items.length +
          (items.length === 1 ? ' integration' : ' integrations');
      // Cleared first, and the message set on the next tick. A live region only
      // announces a *change*: typing "cla" then "clau" can leave the same
      // "Showing 3 integrations" text in place, and a screen reader says
      // nothing while the result set actually moved. `app.js` and `docs.js`
      // avoid this by clearing after their message; a filter fires on every
      // keystroke, so it clears before instead.
      sr.textContent = "";
      srTimer = setTimeout(function () { sr.textContent = announcement; }, 0);
    }

    if (items.length === 0) {
      grid.innerHTML = '<div class="integ-empty">No integrations found matching "' + esc(searchQuery) + '".</div>';
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
        // The command alone is only half of what a reader needs when the delegate it
        // names is not a built-in: it parses, it dry-runs, and it resolves to nothing
        // until a profile exists (#1132). Cards that need one say so here.
        item.note ? '  <p class="integ-note">' + item.note + '</p>' : '',
        '</div>'
      );
    });

    grid.innerHTML = html.join("\n");
    wireCopyButtons();
  }

  // A successful copy is announced through the page's live region, as `app.js` does for its
  // copy buttons (#1212). A button's own aria-label changing is not a live-region update, so
  // whether it is spoken depends on the screen reader and on focus. It shares `srTimer` with
  // the filter announcement above, so whichever the reader did last is what they hear, and it
  // is cleared first and set on the next tick for the same reason the filter is: a second copy
  // in a row would otherwise leave identical text in place and be announced as nothing.
  function announceCopied() {
    var sr = document.getElementById("sr-live-region");
    if (!sr) return;
    if (srTimer) { clearTimeout(srTimer); srTimer = null; }
    sr.textContent = "";
    srTimer = setTimeout(function () { sr.textContent = "Copied to clipboard"; }, 0);
  }

  function wireCopyButtons() {
    document.querySelectorAll(".integ-copy-btn").forEach(function (btn) {
      var origLabel = btn.getAttribute("aria-label") || "Copy command";
      var copyTimer = null;
      btn.onclick = function () {
        var text = btn.getAttribute("data-copy");
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () {
            btn.textContent = "Copied! ✓";
            btn.setAttribute("aria-label", "Copied to clipboard");
            announceCopied();
            clearTimeout(copyTimer);
            copyTimer = setTimeout(function () {
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
        srArmed = true;
        renderGrid();
      };
    });

    var searchInput = document.getElementById("integrations-search");
    if (searchInput) {
      searchInput.oninput = function (e) {
        searchQuery = e.target.value;
        srArmed = true;
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
