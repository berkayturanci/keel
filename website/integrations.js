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
      iconSvg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 257" width="22" height="22"><path fill="#D97757" d="m50.228 170.321 50.357-28.257.843-2.463-.843-1.361h-2.462l-8.426-.518-28.775-.778-24.952-1.037-24.175-1.296-6.092-1.297L0 125.796l.583-3.759 5.12-3.434 7.324.648 16.202 1.101 24.304 1.685 17.629 1.037 26.118 2.722h4.148l.583-1.685-1.426-1.037-1.101-1.037-25.147-17.045-27.22-18.017-14.258-10.37-7.713-5.25-3.888-4.925-1.685-10.758 7-7.713 9.397.649 2.398.648 9.527 7.323 20.35 15.75L94.817 91.9l3.889 3.24 1.555-1.102.195-.777-1.75-2.917-14.453-26.118-15.425-26.572-6.87-11.018-1.814-6.61c-.648-2.723-1.102-4.991-1.102-7.778l7.972-10.823L71.42 0 82.05 1.426l4.472 3.888 6.61 15.101 10.694 23.786 16.591 32.34 4.861 9.592 2.592 8.879.973 2.722h1.685v-1.556l1.36-18.211 2.528-22.36 2.463-28.776.843-8.1 4.018-9.722 7.971-5.25 6.222 2.981 5.12 7.324-.713 4.73-3.046 19.768-5.962 30.98-3.889 20.739h2.268l2.593-2.593 10.499-13.934 17.628-22.036 7.778-8.749 9.073-9.657 5.833-4.601h11.018l8.1 12.055-3.628 12.443-11.342 14.388-9.398 12.184-13.48 18.147-8.426 14.518.778 1.166 2.01-.194 30.46-6.481 16.462-2.982 19.637-3.37 8.88 4.148.971 4.213-3.5 8.62-20.998 5.184-24.628 4.926-36.682 8.685-.454.324.519.648 16.526 1.555 7.065.389h17.304l32.21 2.398 8.426 5.574 5.055 6.805-.843 5.184-12.962 6.611-17.498-4.148-40.83-9.721-14-3.5h-1.944v1.167l11.666 11.406 21.387 19.314 26.767 24.887 1.36 6.157-3.434 4.86-3.63-.518-23.526-17.693-9.073-7.972-20.545-17.304h-1.36v1.814l4.73 6.935 25.017 37.59 1.296 11.536-1.814 3.76-6.481 2.268-7.13-1.297-14.647-20.544-15.1-23.138-12.185-20.739-1.49.843-7.194 77.448-3.37 3.953-7.778 2.981-6.48-4.925-3.436-7.972 3.435-15.749 4.148-20.544 3.37-16.333 3.046-20.285 1.815-6.74-.13-.454-1.49.194-15.295 20.999-23.267 31.433-18.406 19.702-4.407 1.75-7.648-3.954.713-7.064 4.277-6.286 25.47-32.405 15.36-20.092 9.917-11.6-.065-1.686h-.583L44.07 198.125l-12.055 1.555-5.185-4.86.648-7.972 2.463-2.593 20.35-13.999-.064.065Z"/></svg>'
    },
    {
      id: "cursor",
      name: "Cursor",
      category: "assistants",
      badge: "AI Code Editor",
      desc: "Native status bar merge window indicator, background task sync, and command palette integration.",
      cmd: "cursor --install-extension berkayturanci.keel-vscode",
      iconSvg: '<svg viewBox="0 0 466.73 532.09" width="22" height="22" fill="currentColor"><path d="M457.43,125.94L244.42,2.96c-6.84-3.95-15.28-3.95-22.12,0L9.3,125.94c-5.75,3.32-9.3,9.46-9.3,16.11v247.99c0,6.65,3.55,12.79,9.3,16.11l213.01,122.98c6.84,3.95,15.28,3.95,22.12,0l213.01-122.98c5.75-3.32,9.3-9.46,9.3-16.11v-247.99c0-6.65-3.55-12.79-9.3-16.11h-.01ZM444.05,151.99l-205.63,356.16c-1.39,2.4-5.06,1.42-5.06-1.36v-233.21c0-4.66-2.49-8.97-6.53-11.31L24.87,145.67c-2.4-1.39-1.42-5.06,1.36-5.06h411.26c5.84,0,9.49,6.33,6.57,11.39h-.01Z"/></svg>'
    },
    {
      id: "gemini-cli",
      name: "Gemini CLI",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Shared skill commands in .agents/skills/keel-* with native Gemini 2.5 multimodal integration.",
      cmd: "keel ship --delegate gemini",
      iconSvg: '<svg width="22" height="22" viewBox="0 0 512 512" fill="none"><rect width="512" height="512" rx="93" fill="#1E1E2E"/><path d="M357.926 223.129V301.274L154.754 398.949V342.297L321.199 262.199L154.754 182.106V125.453L357.926 223.129Z" fill="url(#gcli_grad)"/><defs><linearGradient id="gcli_grad" x1="119.591" y1="257.869" x2="393.343" y2="257.869" gradientUnits="userSpaceOnUse"><stop offset="0.019" stop-color="#406AFB"/><stop offset="0.22" stop-color="#078EFB"/><stop offset="0.41" stop-color="#939AFF"/><stop offset="0.58" stop-color="#D698FC"/><stop offset="0.77" stop-color="#FA6178"/><stop offset="0.97" stop-color="#F2554F"/></linearGradient></defs></svg>'
    },
    {
      id: "antigravity",
      name: "Google Antigravity",
      category: "assistants",
      badge: "AI Assistant",
      desc: "First-class Antigravity paired programming skills, reactive message wakeups, and AGENTS.md rules.",
      cmd: "keel ship --delegate agy",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M12 2L2 20h20L12 2z"/><polygon points="12,7 17,17 7,17" fill="currentColor" opacity="0.25"/><circle cx="12" cy="13.5" r="2" fill="currentColor"/></svg>'
    },
    {
      id: "openai-codex",
      name: "OpenAI Codex",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Native ChatGPT/Codex plugins and skill adapters with structured reasoning evidence output.",
      cmd: "keel ship --delegate codex",
      iconSvg: '<svg viewBox="0 0 256 260" width="22" height="22" fill="#10a37f"><path d="M239.184 106.203a64.716 64.716 0 0 0-5.576-53.103C219.452 28.459 191 15.784 163.213 21.74A65.586 65.586 0 0 0 52.096 45.22a64.716 64.716 0 0 0-43.23 31.36c-14.31 24.602-11.061 55.634 8.033 76.74a64.665 64.665 0 0 0 5.525 53.102c14.174 24.65 42.644 37.324 70.446 31.36a64.72 64.72 0 0 0 48.754 21.744c28.481.025 53.714-18.361 62.414-45.481a64.767 64.767 0 0 0 43.229-31.36c14.137-24.558 10.875-55.423-8.083-76.483Zm-97.56 136.338a48.397 48.397 0 0 1-31.105-11.255l1.535-.87 51.67-29.825a8.595 8.595 0 0 0 4.247-7.367v-72.85l21.845 12.636c.218.111.37.32.409.563v60.367c-.056 26.818-21.783 48.545-48.601 48.601Zm-104.466-44.61a48.345 48.345 0 0 1-5.781-32.589l1.534.921 51.722 29.826a8.339 8.339 0 0 0 8.441 0l63.181-36.425v25.221a.87.87 0 0 1-.358.665l-52.335 30.184c-23.257 13.398-52.97 5.431-66.404-17.803ZM23.549 85.38a48.499 48.499 0 0 1 25.58-21.333v61.39a8.288 8.288 0 0 0 4.195 7.316l62.874 36.272-21.845 12.636a.819.819 0 0 1-.767 0L41.353 151.53c-23.211-13.454-31.171-43.144-17.804-66.405v.256Zm179.466 41.695-63.08-36.63L161.73 77.86a.819.819 0 0 1 .768 0l52.233 30.184a48.6 48.6 0 0 1-7.316 87.635v-61.391a8.544 8.544 0 0 0-4.4-7.213Zm21.742-32.69-1.535-.922-51.619-30.081a8.39 8.39 0 0 0-8.492 0L99.98 99.808V74.587a.716.716 0 0 1 .307-.665l52.233-30.133a48.652 48.652 0 0 1 72.236 50.391v.205ZM88.061 139.097l-21.845-12.585a.87.87 0 0 1-.41-.614V65.685a48.652 48.652 0 0 1 79.757-37.346l-1.535.87-51.67 29.825a8.595 8.595 0 0 0-4.246 7.367l-.051 72.697Zm11.868-25.58 28.138-16.217 28.188 16.218v32.434l-28.086 16.218-28.188-16.218-.052-32.434Z"/></svg>'
    },
    {
      id: "devin",
      name: "Devin",
      category: "assistants",
      badge: "Autonomous Agent",
      desc: "Autonomous cloud agent webhook triggers with Keel merge lock and multi-vendor jury landing.",
      cmd: "POST https://api.keel.dev/v1/ship",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none"><rect width="24" height="24" rx="5" fill="#0A0A0A"/><path d="M7 8l4 4-4 4M13 16h4" stroke="#10B981" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    },
    {
      id: "aider",
      name: "Aider",
      category: "assistants",
      badge: "Terminal Agent",
      desc: "Interactive terminal pair programmer mapped to Keel's s4 implement and s8 test gates.",
      cmd: "keel ship --delegate aider",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#14b014" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>'
    },
    {
      id: "opencode",
      name: "OpenCode",
      category: "assistants",
      badge: "Open Assistant",
      desc: "Open-source coding assistant integrated via standard POSIX CLI delegate profiles.",
      cmd: "keel ship --delegate opencode",
      iconSvg: '<svg viewBox="0 0 240 300" width="20" height="22" fill="none"><path d="M180 240H60V120H180V240Z" fill="#CFCECD"/><path d="M180 60H60V240H180V60ZM240 300H0V0H240V300Z" fill="currentColor"/></svg>'
    },
    {
      id: "trae",
      name: "Trae",
      category: "assistants",
      badge: "AI Code Editor",
      desc: "Adaptive AI editor companion supported via VS Code extension manifest and Keel CLI.",
      cmd: "keel run-gates .keel/project.yaml",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#8B5CF6" stroke-width="2"><rect x="3" y="3" width="8" height="8" rx="2" fill="#8B5CF6" fill-opacity="0.2"/><rect x="13" y="13" width="8" height="8" rx="2" fill="#8B5CF6" fill-opacity="0.2"/><path d="M11 7h6a2 2 0 0 1 2 2v4M13 17H7a2 2 0 0 1-2-2v-4" stroke-linecap="round"/></svg>'
    },
    {
      id: "github-copilot",
      name: "GitHub Copilot",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Copilot workspace and CLI actions bound to deterministic Keel pre-merge evidence gates.",
      cmd: "keel evidence-verify .keel/project.yaml",
      iconSvg: '<svg viewBox="0 0 256 208" width="22" height="20" fill="currentColor"><path d="M205.3 31.4c14 14.8 20 35.2 22.5 63.6 6.6 0 12.8 1.5 17 7.2l7.8 10.6c2.2 3 3.4 6.6 3.4 10.4v28.7a12 12 0 0 1-4.8 9.5C215.9 187.2 172.3 208 128 208c-49 0-98.2-28.3-123.2-46.6a12 12 0 0 1-4.8-9.5v-28.7c0-3.8 1.2-7.4 3.4-10.5l7.8-10.5c4.2-5.7 10.4-7.2 17-7.2 2.5-28.4 8.4-48.8 22.5-63.6C77.3 3.2 112.6 0 127.6 0h.4c14.7 0 50.4 2.9 77.3 31.4ZM128 78.7c-3 0-6.5.2-10.3.6a27.1 27.1 0 0 1-6 12.1 45 45 0 0 1-32 13c-6.8 0-13.9-1.5-19.7-5.2-5.5 1.9-10.8 4.5-11.2 11-.5 12.2-.6 24.5-.6 36.8 0 6.1 0 12.3-.2 18.5 0 3.6 2.2 6.9 5.5 8.4C79.9 185.9 105 192 128 192s48-6 74.5-18.1a9.4 9.4 0 0 0 5.5-8.4c.3-18.4 0-37-.8-55.3-.4-6.6-5.7-9.1-11.2-11-5.8 3.7-13 5.1-19.7 5.1a45 45 0 0 1-32-12.9 27.1 27.1 0 0 1-6-12.1c-3.4-.4-6.9-.5-10.3-.6Zm-27 44c5.8 0 10.5 4.6 10.5 10.4v19.2a10.4 10.4 0 0 1-20.8 0V133c0-5.8 4.6-10.4 10.4-10.4Zm53.4 0c5.8 0 10.4 4.6 10.4 10.4v19.2a10.4 10.4 0 0 1-20.8 0V133c0-5.8 4.7-10.4 10.4-10.4Zm-73-94.4c-11.2 1.1-20.6 4.8-25.4 10-10.4 11.3-8.2 40.1-2.2 46.2A31.2 31.2 0 0 0 75 91.7c6.8 0 19.6-1.5 30.1-12.2 4.7-4.5 7.5-15.7 7.2-27-.3-9.1-2.9-16.7-6.7-19.9-4.2-3.6-13.6-5.2-24.2-4.3Zm69 4.3c-3.8 3.2-6.4 10.8-6.7 19.9-.3 11.3 2.5 22.5 7.2 27a41.7 41.7 0 0 0 30 12.2c8.9 0 17-2.9 21.3-7.2 6-6.1 8.2-34.9-2.2-46.3-4.8-5-14.2-8.8-25.4-9.9-10.6-1-20 .7-24.2 4.3ZM128 56c-2.6 0-5.6.2-9 .5.4 1.7.5 3.7.7 5.7 0 1.5 0 3-.2 4.5 3.2-.3 6-.3 8.5-.3 2.6 0 5.3 0 8.5.3-.2-1.6-.2-3-.2-4.5.2-2 .3-4 .7-5.7-3.4-.3-6.4-.5-9-.5Z"/></svg>'
    },
    {
      id: "kimi-cli",
      name: "Kimi CLI",
      category: "assistants",
      badge: "AI Assistant",
      desc: "Moonshot Kimi coding assistant integration for large-context codebase analysis and implementation.",
      cmd: "keel ship --delegate kimi",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none"><rect width="24" height="24" rx="5" fill="#1C64F2"/><path d="M6 5h3.5v6.5L14.5 5H19l-6.5 8 7.5 7h-4.5L9.5 13.5V20H6V5z" fill="#FFFFFF"/></svg>'
    },
    {
      id: "hermes",
      name: "Hermes Agent",
      category: "assistants",
      badge: "Autonomous Agent",
      desc: "Lightweight autonomous agent runner dispatched in isolated Swarm worktrees.",
      cmd: "keel swarm-run .keel/project.yaml",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#EAB308" stroke-width="2" stroke-linecap="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" fill="#EAB308" fill-opacity="0.25"/></svg>'
    },

    // --- LLM Backends / Providers (8) ---
    {
      id: "anthropic-claude",
      name: "Anthropic Claude",
      category: "backends",
      badge: "LLM Backend",
      desc: "Claude 3.7 Sonnet, 3.5 Sonnet, Haiku, and 3 Opus supported with native tool calls and pricing tracking.",
      cmd: "knobs.implementer_agents.core: claude-3-7-sonnet",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="#D97757"><path d="M13.827 3.52h3.603L24 20h-3.603l-6.57-16.48zm-7.258 0h3.767L16.906 20h-3.674l-1.343-3.461H5.017l-1.344 3.46H0L6.57 3.522zm4.132 9.959L8.453 7.687 6.205 13.48H10.7z"/></svg>'
    },
    {
      id: "google-gemini",
      name: "Google Gemini",
      category: "backends",
      badge: "LLM Backend",
      desc: "Gemini 2.5 Flash and Pro with high-speed multi-token context analysis and $0.15/M tier pricing.",
      cmd: "knobs.implementer_agents.core: gemini-2.5-flash",
      iconSvg: '<svg viewBox="0 0 296 298" width="22" height="22" fill="none"><mask id="gem_bg_mask" width="296" height="298" x="0" y="0" maskUnits="userSpaceOnUse"><path fill="#3186FF" d="M141.201 4.886c2.282-6.17 11.042-6.071 13.184.148l5.985 17.37a184.004 184.004 0 0 0 111.257 113.049l19.304 6.997c6.143 2.227 6.156 10.91.02 13.155l-19.35 7.082a184.001 184.001 0 0 0-109.495 109.385l-7.573 20.629c-2.241 6.105-10.869 6.121-13.133.025l-7.908-21.296a184 184 0 0 0-109.02-108.658l-19.698-7.239c-6.102-2.243-6.118-10.867-.025-13.132l20.083-7.467A183.998 183.998 0 0 0 133.291 26.28l7.91-21.394Z"/></mask><g mask="url(#gem_bg_mask)"><ellipse cx="163" cy="149" fill="#3689FF" rx="196" ry="159"/><ellipse cx="33.5" cy="142.5" fill="#F6C013" rx="68.5" ry="72.5"/><path fill="#FA4340" d="M194 10.5C172 82.5 65.5 134.333 22.5 135L144-66l50 76.5Z"/><path fill="#14BB69" d="M194.5 279.5C172.5 207.5 66 155.667 23 155l121.5 201 50-76.5Z"/></g></svg>'
    },
    {
      id: "openai",
      name: "OpenAI GPT & o1",
      category: "backends",
      badge: "LLM Backend",
      desc: "GPT-4o, o1, o3-mini, and GPT-4o-mini supported across single-issue ships and jury panels.",
      cmd: "knobs.implementer_agents.core: gpt-4o",
      iconSvg: '<svg viewBox="0 0 256 260" width="22" height="22" fill="#10a37f"><path d="M239.184 106.203a64.716 64.716 0 0 0-5.576-53.103C219.452 28.459 191 15.784 163.213 21.74A65.586 65.586 0 0 0 52.096 45.22a64.716 64.716 0 0 0-43.23 31.36c-14.31 24.602-11.061 55.634 8.033 76.74a64.665 64.665 0 0 0 5.525 53.102c14.174 24.65 42.644 37.324 70.446 31.36a64.72 64.72 0 0 0 48.754 21.744c28.481.025 53.714-18.361 62.414-45.481a64.767 64.767 0 0 0 43.229-31.36c14.137-24.558 10.875-55.423-8.083-76.483Zm-97.56 136.338a48.397 48.397 0 0 1-31.105-11.255l1.535-.87 51.67-29.825a8.595 8.595 0 0 0 4.247-7.367v-72.85l21.845 12.636c.218.111.37.32.409.563v60.367c-.056 26.818-21.783 48.545-48.601 48.601Zm-104.466-44.61a48.345 48.345 0 0 1-5.781-32.589l1.534.921 51.722 29.826a8.339 8.339 0 0 0 8.441 0l63.181-36.425v25.221a.87.87 0 0 1-.358.665l-52.335 30.184c-23.257 13.398-52.97 5.431-66.404-17.803ZM23.549 85.38a48.499 48.499 0 0 1 25.58-21.333v61.39a8.288 8.288 0 0 0 4.195 7.316l62.874 36.272-21.845 12.636a.819.819 0 0 1-.767 0L41.353 151.53c-23.211-13.454-31.171-43.144-17.804-66.405v.256Zm179.466 41.695-63.08-36.63L161.73 77.86a.819.819 0 0 1 .768 0l52.233 30.184a48.6 48.6 0 0 1-7.316 87.635v-61.391a8.544 8.544 0 0 0-4.4-7.213Zm21.742-32.69-1.535-.922-51.619-30.081a8.39 8.39 0 0 0-8.492 0L99.98 99.808V74.587a.716.716 0 0 1 .307-.665l52.233-30.133a48.652 48.652 0 0 1 72.236 50.391v.205ZM88.061 139.097l-21.845-12.585a.87.87 0 0 1-.41-.614V65.685a48.652 48.652 0 0 1 79.757-37.346l-1.535.87-51.67 29.825a8.595 8.595 0 0 0-4.246 7.367l-.051 72.697Zm11.868-25.58 28.138-16.217 28.188 16.218v32.434l-28.086 16.218-28.188-16.218-.052-32.434Z"/></svg>'
    },
    {
      id: "deepseek",
      name: "DeepSeek V3 / R1",
      category: "backends",
      badge: "LLM Backend",
      desc: "High-reasoning, low-cost DeepSeek chat and reasoner models with exact token expenditure ledger.",
      cmd: "knobs.implementer_agents.core: deepseek-reasoner",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22"><path fill="#4D6BFE" d="M23.748 4.482c-.254-.124-.364.113-.512.234-.051.039-.094.09-.137.136-.372.397-.806.657-1.373.626-.829-.046-1.537.214-2.163.848-.133-.782-.575-1.248-1.247-1.548-.352-.156-.708-.311-.955-.65-.172-.241-.219-.51-.305-.774-.055-.16-.11-.323-.293-.35-.2-.031-.278.136-.356.276-.313.572-.434 1.202-.422 1.84.027 1.436.633 2.58 1.838 3.393.137.093.172.187.129.323-.082.28-.18.552-.266.833-.055.179-.137.217-.329.14a5.526 5.526 0 0 1-1.736-1.18c-.857-.828-1.631-1.742-2.597-2.458a11.365 11.365 0 0 0-.689-.471c-.985-.957.13-1.743.388-1.836.27-.098.093-.432-.779-.428-.872.004-1.67.295-2.687.684a3.055 3.055 0 0 1-.465.137 9.597 9.597 0 0 0-2.883-.102c-1.885.21-3.39 1.102-4.497 2.623C.082 8.606-.231 10.684.152 12.85c.403 2.284 1.569 4.175 3.36 5.653 1.858 1.533 3.997 2.284 6.438 2.14 1.482-.085 3.133-.284 4.994-1.86.47.234.962.327 1.78.397.63.059 1.236-.03 1.705-.128.735-.156.684-.837.419-.961-2.155-1.004-1.682-.595-2.113-.926 1.096-1.296 2.746-2.642 3.392-7.003.05-.347.007-.565 0-.845-.004-.17.035-.237.23-.256a4.173 4.173 0 0 0 1.545-.475c1.396-.763 1.96-2.015 2.093-3.517.02-.23-.004-.467-.247-.588zM11.581 18c-2.089-1.642-3.102-2.183-3.52-2.16-.392.024-.321.471-.235.763.09.288.207.486.371.739.114.167.192.416-.113.603-.673.416-1.842-.14-1.897-.167-1.361-.802-2.5-1.86-3.301-3.307-.774-1.393-1.224-2.887-1.298-4.482-.02-.386.093-.522.477-.592a4.696 4.696 0 0 1 1.529-.039c2.132.312 3.946 1.265 5.468 2.774.868.86 1.525 1.887 2.202 2.891.72 1.066 1.494 2.082 2.48 2.914.348.292.625.514.891.677-.802.09-2.14.11-3.054-.614zm1-6.44a.306.306 0 0 1 .415-.287.302.302 0 0 1 .2.288.306.306 0 0 1-.31.307.303.303 0 0 1-.304-.308zm3.11 1.596c-.2.081-.399.151-.59.16a1.245 1.245 0 0 1-.798-.254c-.274-.23-.47-.358-.552-.758a1.73 1.73 0 0 1 .016-.588c.07-.327-.008-.537-.239-.727-.187-.156-.426-.199-.688-.199a.559.559 0 0 1-.254-.078.253.253 0 0 1-.114-.358c.028-.054.16-.186.192-.21.356-.202.767-.136 1.146.016.352.144.618.408 1.001.782.391.451.462.576.685.914.176.265.336.537.445.848.067.195-.019.354-.25.452z"/></svg>'
    },
    {
      id: "ollama-local",
      name: "Ollama (Local / Offline)",
      category: "backends",
      badge: "Local Backend",
      desc: "100% on-device, offline model execution with zero API cost and private repository isolation.",
      cmd: "knobs.implementer_agents.core: ollama:deepseek-r1",
      iconSvg: '<svg viewBox="0 0 646 854" width="18" height="22" fill="currentColor"><path d="M140.629 0.239929C132.66 1.52725 123.097 5.69568 116.354 10.845C95.941 26.3541 80.1253 59.2728 73.4435 100.283C70.9302 115.792 69.2138 137.309 69.2138 153.738C69.2138 173.109 71.4819 197.874 74.7309 214.977C75.4665 218.778 75.8343 222.15 75.5278 222.395C75.2826 222.64 72.2788 225.092 68.9072 227.789C57.3827 236.984 44.2029 251.145 35.1304 264.08C17.7209 288.784 6.44151 316.86 1.72133 347.265C-0.117698 359.28 -0.608106 383.555 0.863118 395.57C4.11207 423.278 12.449 446.695 26.7321 468.151L31.391 475.078L30.0424 477.346C20.4794 493.407 12.3264 516.64 8.52575 538.953C5.522 556.608 5.15419 561.328 5.15419 584.99C5.15419 608.837 5.4607 613.557 8.28054 630.047C11.6521 649.786 18.5178 670.689 26.1804 684.605C28.6938 689.141 34.8239 698.581 35.5595 699.072C35.8047 699.194 35.0691 701.462 33.9044 704.098C25.077 723.408 17.537 749.093 14.4106 770.733C12.2038 785.567 11.8973 790.349 11.8973 805.981C11.8973 825.903 13.0007 835.589 17.1692 851.466L17.7822 853.795H44.019H70.3172L68.6007 850.546C57.9957 830.93 57.0149 794.517 66.1487 758.166C70.3172 741.369 75.0374 729.048 83.8647 712.067L89.1366 701.769V695.455C89.1366 689.57 89.014 688.896 87.1137 685.034C85.6424 682.091 83.6808 679.578 80.1866 676.145C74.2404 670.383 69.9494 664.314 66.5165 656.835C51.4365 624.1 48.494 575.489 59.0991 534.049C63.5128 516.762 70.8076 501.376 78.4702 492.978C83.6808 487.215 86.378 480.779 86.378 474.097C86.378 467.17 83.926 461.469 78.4089 455.523C62.5932 438.604 52.8464 418.006 49.3522 394.038C44.3868 359.893 53.3981 322.683 73.8726 293.198C93.9181 264.263 122.055 245.689 153.503 240.724C160.552 239.559 173.732 239.743 181.088 241.092C189.119 242.502 194.145 242.072 199.295 239.62C205.67 236.617 208.858 232.877 212.597 224.295C215.907 216.633 218.482 212.464 225.409 203.821C233.746 193.461 241.776 186.411 254.649 177.89C269.362 168.266 286.097 161.278 302.771 157.906C308.839 156.68 311.659 156.496 323 156.496C334.341 156.496 337.161 156.68 343.229 157.906C367.688 162.872 391.964 175.5 411.335 193.399C415.503 197.261 425.495 209.644 428.683 214.794C429.909 216.816 432.055 221.108 433.403 224.295C437.142 232.877 440.33 236.617 446.705 239.62C451.671 242.011 456.881 242.502 464.605 241.214C476.804 239.13 486.183 239.314 498.137 241.766C538.841 249.98 574.273 283.512 589.966 328.446C603.636 367.862 599.774 409.118 579.422 440.626C575.989 445.96 572.556 450.251 567.591 455.523C556.863 466.986 556.863 481.208 567.53 492.978C585.062 512.165 596.035 559.367 592.724 600.99C590.518 628.453 583.468 653.035 573.782 666.95C572.066 669.402 568.511 673.57 565.813 676.145C562.319 679.578 560.358 682.091 558.886 685.034C556.986 688.896 556.863 689.57 556.863 695.455V701.769L562.135 712.067C570.963 729.048 575.683 741.369 579.851 758.166C588.863 794.027 588.066 829.704 577.767 849.995C576.909 851.711 576.173 853.305 576.173 853.489C576.173 853.673 587.882 853.795 602.226 853.795H628.218L628.892 851.159C629.26 849.75 629.873 847.604 630.179 846.378C630.854 843.681 632.202 835.712 633.306 828.049C634.348 820.325 634.348 791.881 633.306 783.299C629.383 752.158 622.823 727.454 612.096 704.098C610.931 701.462 610.195 699.194 610.44 699.072C610.747 698.888 612.463 696.436 614.302 693.677C627.666 673.448 635.88 648.008 640.049 614.415C641.152 605.158 641.152 565.374 640.049 556.485C637.106 533.559 633.551 517.988 627.666 502.234C625.214 495.675 618.716 481.821 615.958 477.346L614.609 475.078L619.268 468.151C633.551 446.695 641.888 423.278 645.137 395.57C646.608 383.555 646.118 359.28 644.279 347.265C639.497 316.798 628.279 288.845 610.87 264.08C601.797 251.145 588.617 236.984 577.093 227.789C573.721 225.092 570.717 222.64 570.472 222.395C570.166 222.15 570.534 218.778 571.269 214.977C578.687 176.296 578.441 128.053 570.656 90.3524C563.913 57.4951 551.653 31.3808 535.837 16.3008C523.209 4.28578 510.336 -0.863507 494.888 0.11731C459.456 2.20154 430.89 42.9667 419.61 107.21C417.771 117.57 416.178 129.708 416.178 133.018C416.178 134.305 415.932 135.347 415.626 135.347C415.319 135.347 412.929 134.121 410.354 132.589C383.014 116.405 352.608 107.762 323 107.762C293.392 107.762 262.986 116.405 235.646 132.589C233.071 134.121 230.681 135.347 230.374 135.347C230.068 135.347 229.822 134.305 229.822 133.018C229.822 129.585 228.167 117.08 226.39 107.21C216.152 49.5259 192.674 11.3354 161.472 1.71112C157.181 0.423799 144.982 -0.434382 140.629 0.239929ZM151.051 50.139C159.878 57.1273 169.686 77.1114 175.326 99.4863C176.368 103.532 177.471 108.191 177.778 109.907C178.023 111.563 178.697 115.302 179.249 118.183C181.64 131.179 182.743 145.217 182.866 162.32L182.927 179.178L178.697 185.43L174.468 191.744H164.598C153.074 191.744 141.61 193.216 130.637 196.158C126.714 197.139 122.913 198.12 122.178 198.304C121.013 198.549 120.829 198.181 120.155 193.154C116.538 165.875 116.722 135.654 120.707 110.52C125.12 82.5059 135.419 57.1273 145.472 49.6486C147.863 47.8708 148.292 47.9321 151.051 50.139ZM500.589 49.7098C506.658 54.1848 513.34 66.0772 518.305 81.2798C528.297 111.685 531.117 153.431 525.845 193.154C525.171 198.181 524.987 198.549 523.822 198.304C523.087 198.12 519.286 197.139 515.363 196.158C504.39 193.216 492.926 191.744 481.402 191.744H471.532L467.303 185.43L463.073 179.178L463.134 162.32C463.257 138.535 465.464 119.961 470.735 99.3024C476.314 77.1114 486.183 57.1273 494.949 50.139C497.708 47.9321 498.137 47.8708 500.589 49.7098Z"/></svg>'
    },
    {
      id: "aws-bedrock",
      name: "AWS Bedrock",
      category: "backends",
      badge: "Enterprise Cloud",
      desc: "Enterprise VPC-isolated Claude and Llama endpoints via standard AWS credentials.",
      cmd: "export ANTHROPIC_BEDROCK_AWS_REGION=us-east-1",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none"><rect width="24" height="24" rx="5" fill="#232F3E"/><path d="M6 14.5c3.5 2.2 8.5 2.2 12 0M15.5 13.5l2.5 1.5-2.5 1.5" stroke="#FF9900" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    },
    {
      id: "azure-openai",
      name: "Azure OpenAI",
      category: "backends",
      badge: "Enterprise Cloud",
      desc: "SOC2 and HIPAA compliant OpenAI models hosted in private Microsoft Azure tenancies.",
      cmd: "export AZURE_OPENAI_ENDPOINT=https://...",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none"><path d="M12.5 2.5L2 19h7l3.5-6 3.5 6h6L12.5 2.5z" fill="#0078D4"/><path d="M9 19l3.5-6 3.5 6H9z" fill="#50E6FF"/></svg>'
    },
    {
      id: "openrouter",
      name: "OpenRouter",
      category: "backends",
      badge: "Unified Routing",
      desc: "Dynamic multi-model fallback and lowest-latency routing across 100+ open and proprietary models.",
      cmd: "keel cost-report --json",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#6366F1" stroke-width="2"><circle cx="6" cy="6" r="2.5" fill="#6366F1"/><circle cx="18" cy="6" r="2.5" fill="#6366F1"/><circle cx="6" cy="18" r="2.5" fill="#6366F1"/><circle cx="18" cy="18" r="2.5" fill="#6366F1"/><line x1="8.5" y1="6" x2="15.5" y2="6"/><line x1="6" y1="8.5" x2="6" y2="15.5"/><line x1="18" y1="8.5" x2="18" y2="15.5"/><line x1="8.5" y1="18" x2="15.5" y2="18"/><line x1="8" y1="8" x2="16" y2="16"/></svg>'
    },

    // --- Engineering Skill Packs & Tooling (6) ---
    {
      id: "addyosmani-skills",
      name: "Addy Osmani Agent Skills",
      category: "skills",
      badge: "Skill Library",
      desc: "Production-grade engineering workflows: TDD, spec-driven design, security audits & progressive disclosure.",
      cmd: "keel skill add github:addyosmani/agent-skills/skills/tdd-workflow",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none"><rect width="24" height="24" rx="5" fill="#0F172A"/><path d="M12 4l2 5.5 5.5 2-5.5 2-2 5.5-2-5.5-5.5-2 5.5-2 2-5.5z" fill="#38BDF8"/><circle cx="12" cy="11.5" r="1.5" fill="#FFFFFF"/></svg>'
    },
    {
      id: "compound-engineering",
      name: "Compound Engineering",
      category: "skills",
      badge: "Engineering System",
      desc: "Senior engineering reflexes and anti-rationalization patterns hooked into s4 implement and s7 review.",
      cmd: "knobs.skills: ['compound:strict-verification']",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#EC4899" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1.5" fill="#EC4899" fill-opacity="0.2"/><rect x="14" y="3" width="7" height="7" rx="1.5" fill="#EC4899" fill-opacity="0.2"/><rect x="8.5" y="14" width="7" height="7" rx="1.5" fill="#EC4899" fill-opacity="0.2"/><line x1="6.5" y1="10" x2="12" y2="14"/><line x1="17.5" y1="10" x2="12" y2="14"/></svg>'
    },
    {
      id: "mcp-protocol",
      name: "Model Context Protocol",
      category: "skills",
      badge: "Open Standard",
      desc: "Expose Keel's deterministic backbone tools as native MCP endpoints for Cursor, Claude, and IDEs.",
      cmd: "keel mcp --port 8484",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#10B981" stroke-width="2"><rect x="2" y="6" width="20" height="12" rx="3"/><circle cx="7" cy="12" r="1.5" fill="#10B981"/><circle cx="12" cy="12" r="1.5" fill="#10B981"/><circle cx="17" cy="12" r="1.5" fill="#10B981"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4"/></svg>'
    },
    {
      id: "ai-jury",
      name: "Multi-Vendor AI Jury",
      category: "skills",
      badge: "Consensus Engine",
      desc: "Independent 3-vendor jury panel (Anthropic + OpenAI + Google) ensuring unanimous pre-merge verdicts.",
      cmd: "keel evidence-verify .keel/project.yaml --phase pre-merge",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#F59E0B" stroke-width="2"><path d="M12 3v18M5 8l-3 6h6zM19 8l-3 6h6zM5 8V6M19 8V6M5 6h14"/></svg>'
    },
    {
      id: "git-worktrees",
      name: "Isolated Worktrees",
      category: "skills",
      badge: "Concurrency Engine",
      desc: "Zero dirty-checkout collisions: every parallel Swarm worker runs in an isolated directory sandboxed by git.",
      cmd: "keel swarm-plan .keel/project.yaml --issues 101,102",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#6366F1" stroke-width="2"><circle cx="18" cy="6" r="3" fill="#6366F1"/><circle cx="6" cy="12" r="3" fill="#6366F1"/><circle cx="18" cy="18" r="3" fill="#6366F1"/><path d="M9 12h3a3 3 0 0 0 3-3V6M12 12a3 3 0 0 1 3 3v3"/></svg>'
    },
    {
      id: "pre-commit",
      name: "Pre-Commit Gates",
      category: "skills",
      badge: "Quality Gate",
      desc: "Deterministic local gates ensuring code formatting, security, and schema validation before any commit.",
      cmd: "keel run-gates .keel/project.yaml",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#10B981" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" fill="#10B981" fill-opacity="0.15"/><polyline points="9 12 11 14 15 10"/></svg>'
    },

    // --- Platforms & CI/CD (6) ---
    {
      id: "github-actions",
      name: "Official GitHub Action",
      category: "platforms",
      badge: "CI/CD Automation",
      desc: "Official 1-click composite action (berkayturanci/keel-action@v1) for autonomous issue shipping and swarm runs.",
      cmd: "uses: berkayturanci/keel-action@v1",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M12 .5C5.7.5.5 5.7.5 12c0 5.1 3.3 9.4 7.9 10.9.6.1.8-.2.8-.5v-1.7c-3.2.7-3.9-1.5-3.9-1.5-.5-1.3-1.3-1.7-1.3-1.7-1.1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1 1.8 2.7 1.3 3.4 1 .1-.8.4-1.3.8-1.6-2.6-.3-5.3-1.3-5.3-5.8 0-1.3.5-2.3 1.2-3.1-.1-.3-.5-1.5.1-3.1 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0C17.3 4.7 18.3 5 18.3 5c.6 1.6.2 2.8.1 3.1.8.8 1.2 1.8 1.2 3.1 0 4.5-2.7 5.5-5.3 5.8.4.4.8 1.1.8 2.2v3.3c0 .3.2.6.8.5 4.6-1.5 7.9-5.8 7.9-10.9C23.5 5.7 18.3.5 12 .5z"/></svg>'
    },
    {
      id: "homebrew",
      name: "Homebrew Tap",
      category: "platforms",
      badge: "Package Manager",
      desc: "Instant macOS and Linux installation via homebrew tap (brew install keel).",
      cmd: "brew tap berkayturanci/keel && brew install keel",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="#FBB040"><path d="M4 3h13a2 2 0 0 1 2 2v1h1a3 3 0 0 1 3 3v4a3 3 0 0 1-3 3h-1v4a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zm15 5v3a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1V9a1 1 0 0 0-1-1h-2zM5 5v14h11V5H5z"/></svg>'
    },
    {
      id: "vscode-ext",
      name: "VS Code & Cursor Extension",
      category: "platforms",
      badge: "Editor Extension",
      desc: "Status bar merge window indicator and command palette integration for VS Code and Cursor.",
      cmd: "code --install-extension berkayturanci.keel-vscode",
      iconSvg: '<svg viewBox="0 0 100 100" width="22" height="22" fill="none"><path fill="#0065A9" d="M96.461 10.796 75.857.876a6.23 6.23 0 0 0-7.107 1.207l-67.451 61.5a4.167 4.167 0 0 0 .004 6.162l5.51 5.009a4.167 4.167 0 0 0 5.32.236l81.228-61.62c2.725-2.067 6.639-.124 6.639 3.297v-.24a6.25 6.25 0 0 0-3.539-5.63Z"/><path fill="#007ACC" d="m96.461 89.204-20.604 9.92a6.229 6.229 0 0 1-7.107-1.207l-67.451-61.5a4.167 4.167 0 0 1 .004-6.162l5.51-5.009a4.167 4.167 0 0 1 5.32-.236l81.228 61.62c2.725 2.067 6.639.124 6.639-3.297v.24a6.25 6.25 0 0 1-3.539 5.63Z"/><path fill="#1F9CF0" d="M75.858 99.126a6.232 6.232 0 0 1-7.108-1.21c2.306 2.307 6.25.674 6.25-2.588V4.672c0-3.262-3.944-4.895-6.25-2.589a6.232 6.232 0 0 1 7.108-1.21l20.6 9.908A6.25 6.25 0 0 1 100 16.413v67.174a6.25 6.25 0 0 1-3.541 5.633l-20.601 9.906Z"/></svg>'
    },
    {
      id: "curl-installer",
      name: "Standalone POSIX Installer",
      category: "platforms",
      badge: "Zero-Dependency",
      desc: "One-line standalone curl installer with zero sudo or package manager requirements.",
      cmd: "curl -fsSL https://raw.githubusercontent.com/berkayturanci/keel/main/scripts/install.sh | sh",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#22C55E" stroke-width="2"><polyline points="4 17 10 12 4 7"/><line x1="12" y1="19" x2="20" y2="19"/></svg>'
    },
    {
      id: "pypi-pipx",
      name: "PyPI & pipx",
      category: "platforms",
      badge: "Python Ecosystem",
      desc: "Standard Python distribution supporting isolated virtual environments and global CLI usage.",
      cmd: "pipx install keel",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22"><path fill="#3776AB" d="M11.9 2c-5.2 0-4.9 2.3-4.9 2.3l.01 2.3h5v.7H4.5S2 7 2 12.2s2.2 5 2.2 5h1.3v-2.4s-.1-2.9 2.8-2.9h4.8s2.7.05 2.7-2.6V4.6S16.2 2 11.9 2zM9.5 3.8a.9.9 0 1 1 0 1.8.9.9 0 0 1 0-1.8z"/><path fill="#FFD43B" d="M12.1 22c5.2 0 4.9-2.3 4.9-2.3l-.01-2.3h-5v-.7h7.5s2.5.3 2.5-4.9-2.2-5-2.2-5h-1.3v2.4s.1 2.9-2.8 2.9H11s-2.7-.05-2.7 2.6v4.7s-.4 2.6 3.9 2.6zm2.4-1.8a.9.9 0 1 1 0-1.8.9.9 0 0 1 0 1.8z"/></svg>'
    },
    {
      id: "cross-platform",
      name: "Linux, macOS & Windows",
      category: "platforms",
      badge: "Cross-Platform",
      desc: "Pure stdlib-first core running deterministically across POSIX shells, macOS, Linux, and Windows.",
      cmd: "keel doctor",
      iconSvg: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#06B6D4" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>'
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
