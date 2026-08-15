/* ============================================================
   keel — Interactive In-Browser Swarm DAG Simulator
   Real-time conflict DAG partitioning, isolated worktrees,
   multi-model delegation, AI Jury consensus, and landing funnel.
   Zero backend dependencies — runs 100% client-side.
   ============================================================ */

(function () {
  "use strict";

  var PRESETS = {
    microservices: {
      name: "Microservices & Core Refactor",
      description: "5 issues partitioned into 2 waves across Claude, Gemini & Codex with direct batch landing.",
      issues: [
        { id: 742, title: "Viral PR watermark & SVG badges", files: ["src/keel/closure.py", "docs/badges.md"], model: "gemini-2.5-flash", vendor: "Google", wave: 1 },
        { id: 740, title: "Smart stack init auto-detector", files: ["src/keel/scaffold.py", "src/keel/cli.py"], model: "claude-3-7-sonnet", vendor: "Anthropic", wave: 1 },
        { id: 741, title: "Homebrew tap formula & curl script", files: ["Formula/keel.rb", "scripts/install.sh"], model: "codex", vendor: "OpenAI", wave: 1 },
        { id: 745, title: "Conflict self-healing rebase engine", files: ["src/keel/swarm_landing.py"], model: "claude-3-7-sonnet", vendor: "Anthropic", wave: 2, dependsOn: [740] },
        { id: 743, title: "Post-merge canary & rollback guard", files: ["src/keel/canary.py"], model: "gemini-2.5-pro", vendor: "Google", wave: 2, dependsOn: [740] }
      ]
    },
    fullstack: {
      name: "Full-Stack AI Monorepo",
      description: "4 concurrent sub-agents in parallel worktrees converging into an integration test wave.",
      issues: [
        { id: 750, title: "High-throughput API Gateway", files: ["api/gateway.py"], model: "claude-3-7-sonnet", vendor: "Anthropic", wave: 1 },
        { id: 751, title: "Vector Embedding & RAG Pipeline", files: ["core/rag.py"], model: "codex", vendor: "OpenAI", wave: 1 },
        { id: 752, title: "Spatial Canvas & Topology UI", files: ["ui/canvas.ts"], model: "gemini-2.5-flash", vendor: "Google", wave: 1 },
        { id: 753, title: "E2E Cross-Agent Test Matrix", files: ["tests/e2e.py"], model: "deepseek-r1", vendor: "DeepSeek", wave: 2, dependsOn: [750, 751, 752] }
      ]
    },
    conflict: {
      name: "Adjacent Conflict Self-Healing",
      description: "2 workers touching overlapping routes healed automatically by AST-aware rebase funnel.",
      issues: [
        { id: 760, title: "OAuth 2.0 PKCE Auth Provider", files: ["auth/routes.py"], model: "claude-3-7-sonnet", vendor: "Anthropic", wave: 1 },
        { id: 761, title: "Passkey & WebAuthn Handler", files: ["auth/routes.py"], model: "gemini-2.5-pro", vendor: "Google", wave: 1, hasConflict: true },
        { id: 762, title: "Zero-Trust Session Audit Log", files: ["audit/session.py"], model: "codex", vendor: "OpenAI", wave: 2, dependsOn: [760] }
      ]
    }
  };

  var state = {
    presetKey: "microservices",
    running: false,
    timer: null,
    step: 0,
    speed: 1,
    tokens: 0,
    costUsd: 0.0,
    savingsUsd: 0.0,
    wave: 1,
    lock: "UNLOCKED",
    issuesState: {}
  };

  function getActivePreset() {
    return PRESETS[state.presetKey] || PRESETS.microservices;
  }

  function resetSimulation() {
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
    state.running = false;
    state.step = 0;
    state.tokens = 0;
    state.costUsd = 0.0;
    state.savingsUsd = 0.0;
    state.wave = 1;
    state.lock = "UNLOCKED";
    state.issuesState = {};

    var preset = getActivePreset();
    preset.issues.forEach(function (issue) {
      state.issuesState[issue.id] = {
        progress: 0,
        status: issue.wave === 1 ? "queued" : "blocked",
        juryVotes: { anthropic: null, openai: null, google: null },
        log: "Worktree pending..."
      };
    });

    renderUI();
  }

  function stepSimulation() {
    state.step += 1;
    var preset = getActivePreset();
    var allWave1Done = true;
    var allWave2Done = true;

    preset.issues.forEach(function (issue) {
      var st = state.issuesState[issue.id];
      if (issue.wave === 1) {
        if (st.status !== "merged") allWave1Done = false;
      } else {
        if (st.status !== "merged") allWave2Done = false;
      }
    });

    if (allWave1Done) {
      state.wave = 2;
    }

    preset.issues.forEach(function (issue) {
      var st = state.issuesState[issue.id];
      var isReady = issue.wave === 1 || allWave1Done;

      if (!isReady && st.status === "blocked") {
        st.log = "Waiting for Wave 1 landing...";
        return;
      }

      if (isReady && (st.status === "blocked" || st.status === "queued")) {
        st.status = "running";
        st.log = "Worktree spawned · " + issue.model + " implementing...";
      }

      if (st.status === "running") {
        st.progress += Math.floor(Math.random() * 18 + 12);
        state.tokens += Math.floor(Math.random() * 3200 + 1500);
        state.costUsd += 0.0028;
        state.savingsUsd += 0.0165;

        if (st.progress >= 100) {
          st.progress = 100;
          st.status = "jury";
          st.log = "PR opened · AI Jury 3-vendor deliberation...";
        } else {
          st.log = "Coding & running local test gates (" + st.progress + "%)...";
        }
      } else if (st.status === "jury") {
        if (!st.juryVotes.anthropic) {
          st.juryVotes.anthropic = "PASS";
          st.log = "Anthropic Reviewer: PASS ✓";
        } else if (!st.juryVotes.openai) {
          st.juryVotes.openai = "PASS";
          st.log = "OpenAI Reviewer: PASS ✓";
        } else if (!st.juryVotes.google) {
          st.juryVotes.google = "PASS";
          st.status = "landing";
          st.log = "AI Jury Consensus: UNANIMOUS PASS ✓";
        }
      } else if (st.status === "landing") {
        state.lock = "LOCKED (" + issue.id + ")";
        if (issue.hasConflict) {
          st.log = "Conflict detected in " + issue.files[0] + " · AST self-healing rebase applied ✓";
        } else {
          st.log = "Direct orthogonal batch landing into main...";
        }
        st.status = "merged";
      } else if (st.status === "merged") {
        state.lock = "UNLOCKED";
        st.log = "Merged into main ✓ (PR closed & stamped)";
      }
    });

    if (allWave1Done && allWave2Done) {
      state.running = false;
      if (state.timer) {
        clearInterval(state.timer);
        state.timer = null;
      }
      state.lock = "UNLOCKED";
    }

    renderUI();
  }

  function startSimulation() {
    if (state.running) return;
    state.running = true;
    var intervalMs = Math.max(150, Math.floor(650 / state.speed));
    state.timer = setInterval(stepSimulation, intervalMs);
    renderUI();
  }

  function pauseSimulation() {
    state.running = false;
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
    renderUI();
  }

  function renderUI() {
    var container = document.getElementById("swarm-simulator-container");
    if (!container) return;

    var preset = getActivePreset();
    var issueList = preset.issues;

    // Header & stats
    var btnLabel = state.running ? "⏸ Pause" : (state.step > 0 ? "▶ Resume" : "▶ Run Swarm Simulation");
    var lockClass = state.lock.indexOf("LOCKED") >= 0 ? "lock-active" : "lock-idle";

    var html = [
      '<div class="sim-card">',
      '  <div class="sim-header">',
      '    <div class="sim-presets">',
      '      <span class="sim-label">Backlog Scenario:</span>',
      '      <div class="sim-pills" role="radiogroup" aria-label="Backlog Scenario">'
    ];

    Object.keys(PRESETS).forEach(function (k) {
      var p = PRESETS[k];
      var isSel = k === state.presetKey;
      html.push(
        '<button type="button" class="sim-pill ' + (isSel ? 'active' : '') + '" data-preset="' + k + '" role="radio" aria-checked="' + isSel + '">' +
        p.name +
        '</button>'
      );
    });

    html.push(
      '      </div>',
      '    </div>',
      '    <div class="sim-controls">',
      '      <button type="button" class="sim-btn sim-btn-primary" id="sim-toggle-btn">' + btnLabel + '</button>',
      '      <button type="button" class="sim-btn sim-btn-secondary" id="sim-reset-btn">⟳ Reset</button>',
      '      <div class="sim-speed-box">',
      '        <span>Speed:</span>',
      '        <button type="button" class="sim-speed-btn ' + (state.speed === 1 ? 'active' : '') + '" data-speed="1">1x</button>',
      '        <button type="button" class="sim-speed-btn ' + (state.speed === 2 ? 'active' : '') + '" data-speed="2">2x</button>',
      '        <button type="button" class="sim-speed-btn ' + (state.speed === 4 ? 'active' : '') + '" data-speed="4">4x</button>',
      '      </div>',
      '    </div>',
      '  </div>',
      '  <p class="sim-desc">' + preset.description + '</p>',
      '  <div class="sim-metrics-bar">',
      '    <div class="sim-metric"><span class="m-val">' + state.tokens.toLocaleString() + '</span><span class="m-lbl">Tokens Processed</span></div>',
      '    <div class="sim-metric"><span class="m-val">$' + state.costUsd.toFixed(4) + '</span><span class="m-lbl">Estimated Spend</span></div>',
      '    <div class="sim-metric green"><span class="m-val">$' + state.savingsUsd.toFixed(4) + '</span><span class="m-lbl">Routing Savings</span></div>',
      '    <div class="sim-metric"><span class="m-val ' + lockClass + '">' + state.lock + '</span><span class="m-lbl">Merge Lock State</span></div>',
      '    <div class="sim-metric"><span class="m-val">Wave ' + state.wave + ' of 2</span><span class="m-lbl">DAG Wave Phase</span></div>',
      '  </div>',
      '  <div class="sim-dag-layout">'
    );

    // Wave 1 Column
    html.push(
      '    <div class="sim-wave-col">',
      '      <div class="sim-wave-title"><span class="wave-badge">WAVE 1</span> Orthogonal Parallel Clusters</div>',
      '      <div class="sim-cluster-list">'
    );

    issueList.filter(function (i) { return i.wave === 1; }).forEach(function (issue) {
      html.push(renderIssueCard(issue));
    });

    html.push(
      '      </div>',
      '    </div>',
      '    <div class="sim-dag-arrow">➔</div>',
      '    <div class="sim-wave-col">',
      '      <div class="sim-wave-title"><span class="wave-badge">WAVE 2</span> Dependent &amp; Funnel Landing</div>',
      '      <div class="sim-cluster-list">'
    );

    issueList.filter(function (i) { return i.wave === 2; }).forEach(function (issue) {
      html.push(renderIssueCard(issue));
    });

    html.push(
      '      </div>',
      '    </div>',
      '  </div>',
      '  <div class="sim-footer">',
      '    <div class="sim-cli-cta">',
      '      <span class="cta-label">Run in your repo:</span>',
      '      <code>keel swarm-plan .keel/project.yaml --issues ' + issueList.map(function (i) { return i.id; }).join(',') + ' && keel swarm-run .keel/project.yaml</code>',
      '      <button type="button" class="sim-copy-btn" id="sim-copy-cli" title="Copy CLI Command">Copy</button>',
      '    </div>',
      '  </div>',
      '</div>'
    );

    container.innerHTML = html.join("\n");
    wireEvents();
  }

  function renderIssueCard(issue) {
    var st = state.issuesState[issue.id] || { progress: 0, status: "queued", juryVotes: {}, log: "" };
    var statusClass = "st-" + st.status;
    var vendorBadge = '<span class="vendor-tag tag-' + issue.vendor.toLowerCase() + '">' + issue.model + '</span>';

    var juryBadges = [];
    if (st.status === "jury" || st.status === "landing" || st.status === "merged") {
      juryBadges.push(
        '<div class="jury-badges">',
        '  <span class="j-badge ' + (st.juryVotes.anthropic ? 'ok' : '') + '">Claude</span>',
        '  <span class="j-badge ' + (st.juryVotes.openai ? 'ok' : '') + '">OpenAI</span>',
        '  <span class="j-badge ' + (st.juryVotes.google ? 'ok' : '') + '">Google</span>',
        '</div>'
      );
    }

    return [
      '<div class="sim-issue-card ' + statusClass + '">',
      '  <div class="issue-card-top">',
      '    <span class="issue-id">#' + issue.id + '</span>',
      '    <span class="issue-title">' + issue.title + '</span>',
      '    ' + vendorBadge,
      '  </div>',
      '  <div class="issue-files">📁 ' + issue.files.join(", ") + '</div>',
      '  <div class="issue-progress-bg">',
      '    <div class="issue-progress-fill" style="width:' + st.progress + '%"></div>',
      '  </div>',
      '  <div class="issue-card-bottom">',
      '    <span class="issue-status-pill ' + statusClass + '">' + st.status.toUpperCase() + '</span>',
      '    ' + juryBadges.join(""),
      '  </div>',
      '  <div class="issue-log">' + st.log + '</div>',
      '</div>'
    ].join("\n");
  }

  function wireEvents() {
    var toggleBtn = document.getElementById("sim-toggle-btn");
    if (toggleBtn) {
      toggleBtn.onclick = function () {
        if (state.running) pauseSimulation();
        else startSimulation();
      };
    }

    var resetBtn = document.getElementById("sim-reset-btn");
    if (resetBtn) {
      resetBtn.onclick = resetSimulation;
    }

    document.querySelectorAll(".sim-pill[data-preset]").forEach(function (btn) {
      btn.onclick = function () {
        state.presetKey = btn.dataset.preset;
        resetSimulation();
      };
    });

    document.querySelectorAll(".sim-speed-btn[data-speed]").forEach(function (btn) {
      btn.onclick = function () {
        state.speed = parseInt(btn.dataset.speed, 10) || 1;
        if (state.running) {
          pauseSimulation();
          startSimulation();
        } else {
          renderUI();
        }
      };
    });

    var copyBtn = document.getElementById("sim-copy-cli");
    if (copyBtn) {
      copyBtn.onclick = function () {
        var preset = getActivePreset();
        var cmd = "keel swarm-plan .keel/project.yaml --issues " + preset.issues.map(function (i) { return i.id; }).join(',') + " && keel swarm-run .keel/project.yaml";
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(cmd).then(function () {
            copyBtn.textContent = "Copied! ✓";
            setTimeout(function () { copyBtn.textContent = "Copy"; }, 2000);
          });
        }
      };
    }
  }

  // Initialize on DOM ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      resetSimulation();
    });
  } else {
    resetSimulation();
  }

})();
