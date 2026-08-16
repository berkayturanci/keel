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
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M13.8 2.2a1.2 1.2 0 0 0-1.6.4L7 11.5l-1.9-3.2a1.2 1.2 0 0 0-2.1 1.2l2.4 4.1L2.6 15a1.2 1.2 0 0 0 .9 2.1h13.2a1.2 1.2 0 0 0 1.1-1.7l-2.4-4.8 3.5-5.9a1.2 1.2 0 0 0-.4-1.6l-4.7-.9z"/></svg>'
    },
    {
      id: "cursor",
      name: "Cursor",
      category: "assistants",
      badge: "AI Code Editor",
      desc: "Native status bar merge window indicator, background task sync, and command palette integration.",
      cmd: "cursor --install-extension berkayturanci.keel-vscode",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 2L2 7.5v9L12 22l10-5.5v-9L12 2zm0 2.3l7.6 4.2-7.6 4.2-7.6-4.2L12 4.3zM4 9.4l7 3.8v7.2l-7-3.8V9.4zm9 11v-7.2l7-3.8v7.2l-7 3.8z"/></svg>'
    },
    {
      id: "gemini-cli",
      name: "Gemini CLI",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Shared skill commands in .agents/skills/keel-* with native Gemini 2.5 multimodal integration.",
      cmd: "keel ship --delegate gemini",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 0C12 6.627 6.627 12 0 12c6.627 0 12 5.373 12 12 0-6.627 5.373-12 12-12-6.627 0-12-5.373-12-12z"/></svg>'
    },
    {
      id: "antigravity",
      name: "Google Antigravity",
      category: "assistants",
      badge: "AI Assistant",
      desc: "First-class Antigravity paired programming skills, reactive message wakeups, and AGENTS.md rules.",
      cmd: "keel ship --delegate agy",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 2L1 21h22L12 2zm0 4.5l7 12.5H5L12 6.5z"/><circle cx="12" cy="14" r="2.5"/></svg>'
    },
    {
      id: "openai-codex",
      name: "OpenAI Codex",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Native ChatGPT/Codex plugins and skill adapters with structured reasoning evidence output.",
      cmd: "keel ship --delegate codex",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M22.28 9.37a5.99 5.99 0 0 0-.52-4.94 6.07 6.07 0 0 0-6.49-2.91 6.04 6.04 0 0 0-4.6-2.02 6.07 6.07 0 0 0-5.78 4.2A6.02 6.02 0 0 0 1.7 6.64a6.07 6.07 0 0 0 .97 7.07 5.99 5.99 0 0 0 .52 4.94 6.07 6.07 0 0 0 6.49 2.91 6.04 6.04 0 0 0 4.6 2.02 6.07 6.07 0 0 0 5.78-4.2 6.02 6.02 0 0 0 3.19-2.94 6.07 6.07 0 0 0-.97-7.07zm-7.6 11.23a4.57 4.57 0 0 1-2.98.05l.38-2.15 3.39-1.96.88.51a4.57 4.57 0 0 1-1.67 3.55zm4.84-2.82a4.57 4.57 0 0 1-2.31 1.9l-2.03-1.17v-3.92l.88-.51a4.56 4.56 0 0 1 3.46 3.7zm1.18-5.36a4.57 4.57 0 0 1-.68 2.9l-2.03-1.17v-3.92l.88-.51a4.57 4.57 0 0 1 1.83 2.7zm-4.73-5.26l-3.39 1.96-.88-.51a4.56 4.56 0 0 1 1.67-3.55 4.57 4.57 0 0 1 2.98-.05l-.38 2.15zm-6.02 2.37l2.03 1.17v3.92l-.88.51a4.56 4.56 0 0 1-3.46-3.7 4.57 4.57 0 0 1 2.31-1.9zm-3.01 6.26a4.57 4.57 0 0 1 .68-2.9l2.03 1.17v3.92l-.88.51a4.57 4.57 0 0 1-1.83-2.7zm3.89-1.37l2.4-1.39 2.4 1.39v2.77l-2.4 1.39-2.4-1.39v-2.77z"/></svg>'
    },
    {
      id: "devin",
      name: "Devin",
      category: "assistants",
      badge: "Autonomous Agent",
      desc: "Autonomous cloud agent webhook triggers with Keel merge lock and multi-vendor jury landing.",
      cmd: "POST https://api.keel.dev/v1/ship",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><rect x="3" y="3" width="18" height="18" rx="4"/><path d="M7 8l4 4-4 4M13 16h4" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>'
    },
    {
      id: "aider",
      name: "Aider",
      category: "assistants",
      badge: "Terminal Agent",
      desc: "Interactive terminal pair programmer mapped to Keel's s4 implement and s8 test gates.",
      cmd: "keel ship --delegate aider",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>'
    },
    {
      id: "opencode",
      name: "OpenCode",
      category: "assistants",
      badge: "Open Assistant",
      desc: "Open-source coding assistant integrated via standard POSIX CLI delegate profiles.",
      cmd: "keel ship --delegate opencode",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2l8 4.5v9L12 20l-8-4.5v-9L12 2z"/><polyline points="9 9 6 12 9 15"/><polyline points="15 9 18 12 15 15"/></svg>'
    },
    {
      id: "trae",
      name: "Trae",
      category: "assistants",
      badge: "AI Code Editor",
      desc: "Adaptive AI editor companion supported via VS Code extension manifest and Keel CLI.",
      cmd: "keel run-gates .keel/project.yaml",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 2l5 5-5 5-5-5 5-5zm0 10l5 5-5 5-5-5 5-5z"/></svg>'
    },
    {
      id: "github-copilot",
      name: "GitHub Copilot",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Copilot workspace and CLI actions bound to deterministic Keel pre-merge evidence gates.",
      cmd: "keel evidence-verify .keel/project.yaml",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 .5C5.7.5.5 5.7.5 12c0 5.1 3.3 9.4 7.9 10.9.6.1.8-.2.8-.5v-1.7c-3.2.7-3.9-1.5-3.9-1.5-.5-1.3-1.3-1.7-1.3-1.7-1.1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1 1.8 2.7 1.3 3.4 1 .1-.8.4-1.3.8-1.6-2.6-.3-5.3-1.3-5.3-5.8 0-1.3.5-2.3 1.2-3.1-.1-.3-.5-1.5.1-3.1 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0C17.3 4.7 18.3 5 18.3 5c.6 1.6.2 2.8.1 3.1.8.8 1.2 1.8 1.2 3.1 0 4.5-2.7 5.5-5.3 5.8.4.4.8 1.1.8 2.2v3.3c0 .3.2.6.8.5 4.6-1.5 7.9-5.8 7.9-10.9C23.5 5.7 18.3.5 12 .5z"/></svg>'
    },
    {
      id: "kimi-cli",
      name: "Kimi CLI",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Moonshot Kimi coding assistant integration for large-context codebase analysis and implementation.",
      cmd: "keel ship --delegate kimi",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M4 3h4v7.5L14.5 3H20l-7.5 9 8 9h-5.5L8 13.5V21H4V3z"/></svg>'
    },
    {
      id: "hermes",
      name: "Hermes Agent",
      category: "assistants",
      badge: "Autonomous Agent",
      desc: "Lightweight autonomous agent runner dispatched in isolated Swarm worktrees.",
      cmd: "keel swarm-run .keel/project.yaml",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>'
    },

    // --- LLM Backends / Providers (8) ---
    {
      id: "anthropic-claude",
      name: "Anthropic Claude",
      category: "backends",
      badge: "LLM Backend",
      desc: "Claude 3.7 Sonnet, 3.5 Sonnet, Haiku, and 3 Opus supported with native tool calls and pricing tracking.",
      cmd: "knobs.implementer_agents.core: claude-3-7-sonnet",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M13.8 2.2a1.2 1.2 0 0 0-1.6.4L7 11.5l-1.9-3.2a1.2 1.2 0 0 0-2.1 1.2l2.4 4.1L2.6 15a1.2 1.2 0 0 0 .9 2.1h13.2a1.2 1.2 0 0 0 1.1-1.7l-2.4-4.8 3.5-5.9a1.2 1.2 0 0 0-.4-1.6l-4.7-.9z"/></svg>'
    },
    {
      id: "google-gemini",
      name: "Google Gemini",
      category: "backends",
      badge: "LLM Backend",
      desc: "Gemini 2.5 Flash and Pro with high-speed multi-token context analysis and $0.15/M tier pricing.",
      cmd: "knobs.implementer_agents.core: gemini-2.5-flash",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 0C12 6.627 6.627 12 0 12c6.627 0 12 5.373 12 12 0-6.627 5.373-12 12-12-6.627 0-12-5.373-12-12z"/></svg>'
    },
    {
      id: "openai",
      name: "OpenAI GPT & o1",
      category: "backends",
      badge: "LLM Backend",
      desc: "GPT-4o, o1, o3-mini, and GPT-4o-mini supported across single-issue ships and jury panels.",
      cmd: "knobs.implementer_agents.core: gpt-4o",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M22.28 9.37a5.99 5.99 0 0 0-.52-4.94 6.07 6.07 0 0 0-6.49-2.91 6.04 6.04 0 0 0-4.6-2.02 6.07 6.07 0 0 0-5.78 4.2A6.02 6.02 0 0 0 1.7 6.64a6.07 6.07 0 0 0 .97 7.07 5.99 5.99 0 0 0 .52 4.94 6.07 6.07 0 0 0 6.49 2.91 6.04 6.04 0 0 0 4.6 2.02 6.07 6.07 0 0 0 5.78-4.2 6.02 6.02 0 0 0 3.19-2.94 6.07 6.07 0 0 0-.97-7.07zm-7.6 11.23a4.57 4.57 0 0 1-2.98.05l.38-2.15 3.39-1.96.88.51a4.57 4.57 0 0 1-1.67 3.55zm4.84-2.82a4.57 4.57 0 0 1-2.31 1.9l-2.03-1.17v-3.92l.88-.51a4.56 4.56 0 0 1 3.46 3.7zm1.18-5.36a4.57 4.57 0 0 1-.68 2.9l-2.03-1.17v-3.92l.88-.51a4.57 4.57 0 0 1 1.83 2.7zm-4.73-5.26l-3.39 1.96-.88-.51a4.56 4.56 0 0 1 1.67-3.55 4.57 4.57 0 0 1 2.98-.05l-.38 2.15zm-6.02 2.37l2.03 1.17v3.92l-.88.51a4.56 4.56 0 0 1-3.46-3.7 4.57 4.57 0 0 1 2.31-1.9zm-3.01 6.26a4.57 4.57 0 0 1 .68-2.9l2.03 1.17v3.92l-.88.51a4.57 4.57 0 0 1-1.83-2.7zm3.89-1.37l2.4-1.39 2.4 1.39v2.77l-2.4 1.39-2.4-1.39v-2.77z"/></svg>'
    },
    {
      id: "deepseek",
      name: "DeepSeek V3 / R1",
      category: "backends",
      badge: "LLM Backend",
      desc: "High-reasoning, low-cost DeepSeek chat and reasoner models with exact token expenditure ledger.",
      cmd: "knobs.implementer_agents.core: deepseek-reasoner",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 14.5c-2.48 0-4.5-2.02-4.5-4.5S10.52 7.5 13 7.5c1.8 0 3.35 1.06 4.07 2.6l-2.04.85C14.65 10.12 13.88 9.5 13 9.5c-1.38 0-2.5 1.12-2.5 2.5s1.12 2.5 2.5 2.5c.88 0 1.65-.62 2.03-1.45l2.04.85c-.72 1.54-2.27 2.6-4.07 2.6z"/></svg>'
    },
    {
      id: "ollama-local",
      name: "Ollama (Local / Offline)",
      category: "backends",
      badge: "Local Backend",
      desc: "100% on-device, offline model execution with zero API cost and private repository isolation.",
      cmd: "knobs.implementer_agents.core: ollama:deepseek-r1",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M17 2h-3v4h-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v6H4a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-3V2zm-9 6V4h2v4H8zm-2 6a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm12 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3z"/></svg>'
    },
    {
      id: "aws-bedrock",
      name: "AWS Bedrock",
      category: "backends",
      badge: "Enterprise Cloud",
      desc: "Enterprise VPC-isolated Claude and Llama endpoints via standard AWS credentials.",
      cmd: "export ANTHROPIC_BEDROCK_AWS_REGION=us-east-1",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z"/></svg>'
    },
    {
      id: "azure-openai",
      name: "Azure OpenAI",
      category: "backends",
      badge: "Enterprise Cloud",
      desc: "SOC2 and HIPAA compliant OpenAI models hosted in private Microsoft Azure tenancies.",
      cmd: "export AZURE_OPENAI_ENDPOINT=https://...",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M13.05 2.5a1.2 1.2 0 0 0-2.1 0L2.1 19.3a1.2 1.2 0 0 0 1.05 1.7h17.7a1.2 1.2 0 0 0 1.05-1.7L13.05 2.5zm-1.05 3.8l5.8 11.7H6.2l5.8-11.7z"/></svg>'
    },
    {
      id: "openrouter",
      name: "OpenRouter",
      category: "backends",
      badge: "Unified Routing",
      desc: "Dynamic multi-model fallback and lowest-latency routing across 100+ open and proprietary models.",
      cmd: "keel cost-report --json",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><circle cx="6" cy="6" r="3"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="18" r="3"/><line x1="9" y1="6" x2="15" y2="6"/><line x1="6" y1="9" x2="6" y2="15"/><line x1="18" y1="9" x2="18" y2="15"/><line x1="9" y1="18" x2="15" y2="18"/><line x1="8" y1="8" x2="16" y2="16"/></svg>'
    },

    // --- Engineering Skill Packs & Tooling (6) ---
    {
      id: "addyosmani-skills",
      name: "Addy Osmani Agent Skills",
      category: "skills",
      badge: "Skill Library",
      desc: "Production-grade engineering workflows: TDD, spec-driven design, security audits & progressive disclosure.",
      cmd: "keel skill add github:addyosmani/agent-skills/skills/tdd-workflow",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 2l2.4 6.6 6.6 2.4-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2zm0 3.5L10.7 9.3 6.9 10.6l3.8 1.3L12 15.7l1.3-3.8 3.8-1.3-3.8-1.3L12 5.5z"/><circle cx="12" cy="11" r="1.5"/></svg>'
    },
    {
      id: "compound-engineering",
      name: "Compound Engineering",
      category: "skills",
      badge: "Engineering System",
      desc: "Senior engineering reflexes and anti-rationalization patterns hooked into s4 implement and s7 review.",
      cmd: "knobs.skills: ['compound:strict-verification']",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="8.5" y="14" width="7" height="7" rx="1.5"/><line x1="6.5" y1="10" x2="12" y2="14"/><line x1="17.5" y1="10" x2="12" y2="14"/></svg>'
    },
    {
      id: "mcp-protocol",
      name: "Model Context Protocol",
      category: "skills",
      badge: "Open Standard",
      desc: "Expose Keel's deterministic backbone tools as native MCP endpoints for Cursor, Claude, and IDEs.",
      cmd: "keel mcp --port 8484",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="6" width="20" height="12" rx="3"/><circle cx="7" cy="12" r="1.5" fill="currentColor"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/><circle cx="17" cy="12" r="1.5" fill="currentColor"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4"/></svg>'
    },
    {
      id: "ai-jury",
      name: "Multi-Vendor AI Jury",
      category: "skills",
      badge: "Consensus Engine",
      desc: "Independent 3-vendor jury panel (Anthropic + OpenAI + Google) ensuring unanimous pre-merge verdicts.",
      cmd: "keel evidence-verify .keel/project.yaml --phase pre-merge",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v18M5 8l-3 6h6zM19 8l-3 6h6zM5 8V6M19 8V6M5 6h14"/></svg>'
    },
    {
      id: "git-worktrees",
      name: "Isolated Worktrees",
      category: "skills",
      badge: "Concurrency Engine",
      desc: "Zero dirty-checkout collisions: every parallel Swarm worker runs in an isolated directory sandboxed by git.",
      cmd: "keel swarm-plan .keel/project.yaml --issues 101,102",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><circle cx="18" cy="6" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="18" r="3"/><path d="M9 12h3a3 3 0 0 0 3-3V6M12 12a3 3 0 0 1 3 3v3"/></svg>'
    },
    {
      id: "pre-commit",
      name: "Pre-Commit Gates",
      category: "skills",
      badge: "Quality Gate",
      desc: "Deterministic local gates ensuring code formatting, security, and schema validation before any commit.",
      cmd: "keel run-gates .keel/project.yaml",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><polyline points="9 12 11 14 15 10"/></svg>'
    },

    // --- Platforms & CI/CD (6) ---
    {
      id: "github-actions",
      name: "Official GitHub Action",
      category: "platforms",
      badge: "CI/CD Automation",
      desc: "Official 1-click composite action (berkayturanci/keel-action@v1) for autonomous issue shipping and swarm runs.",
      cmd: "uses: berkayturanci/keel-action@v1",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 .5C5.7.5.5 5.7.5 12c0 5.1 3.3 9.4 7.9 10.9.6.1.8-.2.8-.5v-1.7c-3.2.7-3.9-1.5-3.9-1.5-.5-1.3-1.3-1.7-1.3-1.7-1.1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1 1.8 2.7 1.3 3.4 1 .1-.8.4-1.3.8-1.6-2.6-.3-5.3-1.3-5.3-5.8 0-1.3.5-2.3 1.2-3.1-.1-.3-.5-1.5.1-3.1 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0C17.3 4.7 18.3 5 18.3 5c.6 1.6.2 2.8.1 3.1.8.8 1.2 1.8 1.2 3.1 0 4.5-2.7 5.5-5.3 5.8.4.4.8 1.1.8 2.2v3.3c0 .3.2.6.8.5 4.6-1.5 7.9-5.8 7.9-10.9C23.5 5.7 18.3.5 12 .5z"/></svg>'
    },
    {
      id: "homebrew",
      name: "Homebrew Tap",
      category: "platforms",
      badge: "Package Manager",
      desc: "Instant macOS and Linux installation via homebrew tap (brew install keel).",
      cmd: "brew tap berkayturanci/keel && brew install keel",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M4 3h13a2 2 0 0 1 2 2v1h1a3 3 0 0 1 3 3v4a3 3 0 0 1-3 3h-1v4a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zm15 5v3a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1V9a1 1 0 0 0-1-1h-2zM5 5v14h11V5H5z"/></svg>'
    },
    {
      id: "vscode-ext",
      name: "VS Code & Cursor Extension",
      category: "platforms",
      badge: "Editor Extension",
      desc: "Status bar merge window indicator and command palette integration for VS Code and Cursor.",
      cmd: "code --install-extension berkayturanci.keel-vscode",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M17.5 2.1a1.2 1.2 0 0 0-1.1.3L7.7 9.8 4.2 7.1a1 1 0 0 0-1.4.3l-1.5 2.2a1 1 0 0 0 .2 1.4L5.2 14l-3.7 3a1 1 0 0 0-.2 1.4l1.5 2.2a1 1 0 0 0 1.4.3l3.5-2.7 8.7 7.4a1.2 1.2 0 0 0 2-.8V3a1.2 1.2 0 0 0-.9-.9zM16 17.5l-6-4.5 6-4.5v9z"/></svg>'
    },
    {
      id: "curl-installer",
      name: "Standalone POSIX Installer",
      category: "platforms",
      badge: "Zero-Dependency",
      desc: "One-line standalone curl installer with zero sudo or package manager requirements.",
      cmd: "curl -fsSL https://raw.githubusercontent.com/berkayturanci/keel/main/scripts/install.sh | sh",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 12 4 7"/><line x1="12" y1="19" x2="20" y2="19"/></svg>'
    },
    {
      id: "pypi-pipx",
      name: "PyPI & pipx",
      category: "platforms",
      badge: "Python Ecosystem",
      desc: "Standard Python distribution supporting isolated virtual environments and global CLI usage.",
      cmd: "pipx install keel",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M11.9 2c-5.2 0-4.9 2.3-4.9 2.3l.01 2.3h5v.7H4.5S2 7 2 12.2s2.2 5 2.2 5h1.3v-2.4s-.1-2.9 2.8-2.9h4.8s2.7.05 2.7-2.6V4.6S16.2 2 11.9 2zM9.5 3.8a.9.9 0 1 1 0 1.8.9.9 0 0 1 0-1.8zm2.6 18.2c5.2 0 4.9-2.3 4.9-2.3l-.01-2.3h-5v-.7h7.5s2.5.3 2.5-4.9-2.2-5-2.2-5h-1.3v2.4s.1 2.9-2.8 2.9H11s-2.7-.05-2.7 2.6v4.7s-.4 2.6 3.9 2.6zm2.4-1.8a.9.9 0 1 1 0-1.8.9.9 0 0 1 0 1.8z"/></svg>'
    },
    {
      id: "cross-platform",
      name: "Linux, macOS & Windows",
      category: "platforms",
      badge: "Cross-Platform",
      desc: "Pure stdlib-first core running deterministically across POSIX shells, macOS, Linux, and Windows.",
      cmd: "keel doctor",
      iconSvg: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>'
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
        '    <div class="integ-avatar tag-' + item.category + '" aria-hidden="true">' + item.iconSvg + '</div>',
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
