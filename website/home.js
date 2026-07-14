/* ============================================================
   keel — landing page data rendering (from window.KEEL)
   Renders: layers, backbone table, invariants, all 16 commands,
   CLI, config keys, comparison, FAQ. Add to content.js → here.
   ============================================================ */
(function () {
  "use strict";
  var K = window.KEEL;
  if (!K) return;
  function el(tag, cls, html) { var n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; }
  function mount(id) { return document.getElementById(id); }

  /* layers */
  var layers = mount("layers");
  if (layers) K.layers.forEach(function (l) {
    var c = el("div", "layer " + l.tone, '<span class="bar"></span><div class="layer-n">' + l.n + '</div><div class="layer-k">' + l.k + '</div><h3>' + l.t + "</h3><p>" + l.d + "</p>");
    layers.appendChild(c);
  });

  /* backbone table — all 28 hooks are add-only slots; amber = primary (owns/blocks the outcome), grey = passive before/after; ⊘ = may_block */
  var PRIMARY = { "guard": 1, "after-implement": 1, "reviewers": 1, "tester": 1, "test": 1, "pre-merge": 1, "capture": 1, "post-merge": 1 };
  var BLOCKING = { "guard": 1, "tester": 1, "test": 1, "pre-merge": 1 };
  var bbBody = mount("bb-tbl-body");
  if (bbBody) K.backbone.forEach(function (s) {
    var hooks = s.slots.map(function (h) {
      return '<span class="hook' + (PRIMARY[h] ? " slot" : "") + (BLOCKING[h] ? " block" : "") + '">' + h + (BLOCKING[h] ? " ⊘" : "") + "</span>";
    }).join("");
    var row = el("tr", "", '<td><span class="bb-id">' + s.id + '</span></td>' +
      '<td><span class="bb-nm">' + s.name + "</span>" + (s.agent ? ' <span class="tag-agent">agent</span>' : "") + "</td>" +
      '<td>' + s.blurb + "</td>" +
      '<td><div class="bb-hooks">' + hooks + "</div></td>");
    bbBody.appendChild(row);
  });

  /* invariants */
  var invs = mount("invs");
  if (invs) K.invariants.forEach(function (iv) { invs.appendChild(el("div", "inv", "<b>" + iv[0] + "</b><span>" + iv[1] + "</span>")); });

  /* backbone figure — structure + safety primitives + command coverage */
  var fig = mount("bb-figure");
  if (fig) {
    var st = K.backbone, N = st.length, x0 = 56, x1 = 944, y = 116;
    var xs = st.map(function (_, i) { return x0 + i * (x1 - x0) / (N - 1); });
    var mil = { 1: "select", 4: "implement", 7: "review", 10: "merge", 12: "close" };
    var BBICONS = {
      branch: '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="6" cy="6" r="2.4"/><circle cx="6" cy="18" r="2.4"/><circle cx="18" cy="8" r="2.4"/><path d="M6 8.5v7M18 10.5c0 4-5 3.5-9 5"/></svg>',
      shield: '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/></svg>',
      eye: '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M2 12s3.5-6.5 10-6.5S22 12 22 12s-3.5 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="2.6"/></svg>',
      lock: '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>',
      scrub: '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M4 12h16M4 6h16M4 18h10"/></svg>'
    };
    var SAFETY = [
      { i: 2,  icon: "branch", label: "worktree", tip: "Isolated worktree — every branch is cut from the project base branch; work never touches your checkout." },
      { i: 4,  icon: "shield", label: "scope", tip: "Branch-scope validation — the orchestrator verifies the actual diff instead of trusting a delegated agent's declared file list." },
      { i: 7,  icon: "eye",    label: "evidence", tip: "Read-only reviewers + evidence-bearing steps — the orchestrator owns all GitHub writes; each step records evidence or an explicit N/A." },
      { i: 10, icon: "lock",   label: "window + lock", tip: "Timezone-aware no-merge window + mkdir merge lock — mergeability is re-checked inside the lock; behind/dirty state aborts." },
      { i: 11, icon: "scrub",  label: "redaction", lvl: 1, tip: "Sanitized capture — secret redaction (core rules + project deny-patterns) before anything becomes durable; fail-soft after merge." }
    ];
    var stripTop = 176, rowH = 36;
    var covRows = [
      { name: "/keel:ship — flagship · full traversal", kind: "span", a: 0, b: 12, col: "var(--accent)" },
      { name: "work-block · overnight — ×N over a queue", kind: "span", a: 0, b: 12, col: "var(--accent)", dash: true },
      { name: "pr-loop — s6→s12", kind: "span", a: 6, b: 12, col: "var(--accent-2)" },
      { name: "review-cycle — s7 + s9", kind: "dots", at: [7, 9], col: "var(--brass)" },
      { name: "implement — s4", kind: "dots", at: [4], col: "var(--brass)" },
      { name: "ci-check — s6 · read-only diagnosis", kind: "dots", at: [6], col: "var(--brass)" },
      { name: "morning · wrap — report from the run ledger", kind: "span", a: 11, b: 12, col: "var(--accent-2)", dash: true, anchorEnd: true },
      { name: "triage — labels issues before selection", kind: "feed", to: 1, col: "var(--green)" },
      { name: "audits &amp; sweeps — open issues, routed to ship at s0", kind: "feed", to: 0, col: "var(--green)" }
    ];
    var H = stripTop + covRows.length * rowH + 6;
    var s = '<svg viewBox="0 0 1000 ' + H + '" preserveAspectRatio="xMidYMid meet" class="bb-svg" role="img" aria-label="The keel backbone: 13 ordered steps, safety primitives, and per-command coverage">';
    SAFETY.forEach(function (c) {
      s += '<line x1="' + xs[c.i] + '" y1="' + (c.lvl ? 76 : 44) + '" x2="' + xs[c.i] + '" y2="104" stroke="var(--brass-line)" stroke-width="1.2" stroke-dasharray="2 3"/>';
    });
    s += '<line x1="' + xs[0] + '" y1="' + y + '" x2="' + xs[N - 1] + '" y2="' + y + '" style="stroke:var(--accent);opacity:.55" stroke-width="3" stroke-linecap="round"/>';
    s += '<line x1="' + (xs[N - 2] + 20) + '" y1="' + y + '" x2="' + xs[N - 1] + '" y2="' + y + '" style="stroke:var(--green)" stroke-width="3" stroke-linecap="round"/>';
    s += '<text x="' + x0 + '" y="' + (y + 38) + '" font-size="12" fill="var(--muted)" text-anchor="middle" font-family="var(--font-mono)">work item</text>';
    st.forEach(function (b, i) {
      var col = b.block ? "var(--block)" : i >= 10 ? "var(--green)" : "var(--accent)";
      s += '<circle class="stp" data-i="' + i + '" cx="' + xs[i] + '" cy="' + y + '" r="9" fill="none" stroke="var(--brass)" stroke-width="1.3" opacity="0.3"/>';
      if (b.block) s += '<circle class="stp" data-i="' + i + '" cx="' + xs[i] + '" cy="' + y + '" r="12" fill="none" stroke="var(--block)" stroke-width="1.7" opacity="0.85"/>';
      s += '<circle class="stp" data-i="' + i + '" cx="' + xs[i] + '" cy="' + y + '" r="' + (b.block ? 6 : 5) + '" fill="' + col + '"/>';
      s += '<text x="' + xs[i] + '" y="' + (y - 20) + '" font-size="11" fill="var(--faint)" text-anchor="middle" font-family="var(--font-mono)">' + b.id + "</text>";
      if (mil[i]) s += '<text x="' + xs[i] + '" y="' + (y + 22) + '" font-size="11.5" fill="' + (b.block ? "var(--block-2)" : i >= 10 ? "var(--green-2)" : "var(--accent-2)") + '" text-anchor="middle" font-weight="600" font-family="var(--font-display)">' + b.name + "</text>";
      s += '<circle class="bbf-hit" data-i="' + i + '" cx="' + xs[i] + '" cy="' + y + '" r="15" fill="transparent"/>';
    });
    s += '<g class="covmap">';
    covRows.forEach(function (r, ri) {
      var ry = stripTop + ri * rowH + 14;
      var feedX = xs[r.to || 0];
      var startX = r.kind === "feed" ? feedX : r.kind === "dots" ? xs[r.at[0]] - 6 : xs[r.a];
      var hl = r.kind === "feed" ? [r.to || 0] : r.kind === "dots" ? r.at : (function () { var a = []; for (var k = r.a; k <= r.b; k++) a.push(k); return a; })();
      s += '<g class="cov-row" data-hl="' + hl.join(",") + '">';
      s += '<rect x="0" y="' + (ry - 24) + '" width="1000" height="' + rowH + '" fill="transparent"/>';
      s += '<text x="' + (r.anchorEnd ? xs[r.b] : startX) + '" y="' + (ry - 10) + '" font-size="10.5" fill="var(--muted)" font-family="var(--font-mono)"' + (r.anchorEnd ? ' text-anchor="end"' : '') + '>' + r.name + "</text>";
      if (r.kind === "span") {
        s += '<line x1="' + xs[r.a] + '" y1="' + ry + '" x2="' + xs[r.b] + '" y2="' + ry + '" stroke="' + r.col + '" stroke-width="6" stroke-linecap="round" opacity=".75"' + (r.dash ? ' stroke-dasharray="10 7"' : "") + "/>";
      } else if (r.kind === "dots") {
        r.at.forEach(function (i2) { s += '<circle cx="' + xs[i2] + '" cy="' + ry + '" r="6" fill="' + r.col + '" opacity=".85"/>'; });
        if (r.at.length === 2) s += '<line x1="' + xs[r.at[0]] + '" y1="' + ry + '" x2="' + xs[r.at[1]] + '" y2="' + ry + '" stroke="' + r.col + '" stroke-width="2" stroke-dasharray="3 4" opacity=".5"/>';
      } else {
        s += '<circle cx="' + (feedX + 138) + '" cy="' + ry + '" r="4.5" fill="' + r.col + '"/>';
        s += '<path d="M ' + (feedX + 130) + " " + ry + " L " + (feedX + 14) + " " + ry + '" stroke="' + r.col + '" stroke-width="2.4" fill="none" marker-end="url(#bbArr)" opacity=".8"/>';
      }
      s += "</g>";
    });
    s += "</g>";
    s += '<defs><marker id="bbArr" markerWidth="7" markerHeight="7" refX="5.5" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" fill="var(--green)"/></marker></defs>';
    s += "</svg>";
    fig.innerHTML = '<div class="bb-scroll"><div class="bb-canvas">' + s + '</div></div>' +
      '<div class="bb-legend"><span><i style="background:var(--accent)"></i> fixed step</span><span><i style="background:transparent;border:1.5px solid var(--brass)"></i> add-only slots (every step · 28 total)</span><span><i style="background:var(--block)"></i> can block</span><span><i style="background:var(--green)"></i> merged</span></div>';
    var canvas = fig.querySelector(".bb-canvas");
    /* safety primitive chips */
    var saf = el("div", "safety");
    SAFETY.forEach(function (c) {
      var d = el("div", "schip");
      d.style.left = (xs[c.i] / 1000 * 100) + "%";
      d.style.top = ((c.lvl ? 42 : 8) / H * 100) + "%";
      d.innerHTML = BBICONS[c.icon] + c.label + '<span class="tip"><b>' + st[c.i].id + " " + st[c.i].name + "</b> — " + c.tip + "</span>";
      saf.appendChild(d);
    });
    canvas.appendChild(saf);
    /* hover tooltips: step name + what it does + its slots.
       The tip is fixed-positioned on <body> so it floats above everything and is
       never clipped by the figure/section; it flips below the dot near the top. */
    var tip = el("div", "bbf-tip");
    document.body.appendChild(tip);
    fig.querySelectorAll(".bbf-hit").forEach(function (c) {
      function show() {
        var b = st[+c.dataset.i];
        var slots = b.slots && b.slots.length ? '<span class="slots">' + b.slots.map(function (h) { return '<code class="' + (BLOCKING[h] ? "block" : PRIMARY[h] ? "primary" : "") + '">' + h + (BLOCKING[h] ? " ⊘" : "") + "</code>"; }).join("") + "</span>" : "";
        tip.innerHTML = "<b>" + b.id + " " + b.name + (b.agent ? ' · agent' : '') + (b.block ? ' · can block' : '') + "</b><span>" + b.blurb + "</span>" + slots;
        tip.classList.add("show");
        var cr = c.getBoundingClientRect();
        tip.style.left = Math.min(Math.max(cr.left + cr.width / 2, 130), window.innerWidth - 130) + "px";
        if ((cr.top - 12 - tip.offsetHeight) >= 8) { tip.style.top = (cr.top - 12) + "px"; tip.style.transform = "translate(-50%, -100%)"; }
        else { tip.style.top = (cr.bottom + 12) + "px"; tip.style.transform = "translate(-50%, 0)"; }
      }
      c.addEventListener("mouseenter", show);
      c.addEventListener("click", show);
      c.addEventListener("mouseleave", function () { tip.classList.remove("show"); });
    });
    /* coverage-row hover → highlight covered steps */
    fig.querySelectorAll(".cov-row").forEach(function (g) {
      g.addEventListener("mouseenter", function () {
        fig.classList.add("dim");
        var set = {}; g.dataset.hl.split(",").forEach(function (n) { set[+n] = 1; });
        fig.querySelectorAll(".stp").forEach(function (c2) { if (set[+c2.dataset.i]) c2.classList.add("hl"); });
        fig.querySelectorAll(".cov-row").forEach(function (o) { o.style.opacity = o === g ? 1 : .3; });
      });
      g.addEventListener("mouseleave", function () {
        fig.classList.remove("dim");
        fig.querySelectorAll(".stp.hl").forEach(function (c2) { c2.classList.remove("hl"); });
        fig.querySelectorAll(".cov-row").forEach(function (o) { o.style.opacity = 1; });
      });
    });
  }

  /* all 16 commands grouped */
  var groupsHost = mount("cmd-groups");
  if (groupsHost) {
    var order = ["Flagship", "Per-step", "Review & triage", "Daily rhythm", "Audits"];
    order.forEach(function (g) {
      var inGroup = K.commands.filter(function (c) { return c.group === g; });
      if (!inGroup.length) return;
      var sec = el("div", "cmd-group");
      sec.appendChild(el("div", "cmd-group-h", g));
      var grid = el("div", "cmd-cards");
      inGroup.forEach(function (c) {
        var ex = (K.cmdExample && K.cmdExample[c.slug]) ? '<code class="cc-ex">' + K.cmdExample[c.slug] + "</code>" : "";
        var card = el("div", "cmd-card" + (c.flagship ? " flag" : "") + (c.featured ? " feat" : ""),
          '<div class="cc-name">' + c.name + (c.featured ? ' <span class="cc-dot" title="animated above"></span>' : "") + "</div>" +
          '<div class="cc-one">' + c.one + "</div>" + ex);
        grid.appendChild(card);
      });
      sec.appendChild(grid);
      groupsHost.appendChild(sec);
    });
    if (K.cmdNote) groupsHost.appendChild(el("p", "cmd-note", K.cmdNote));
  }

  /* cli */
  var cli = mount("cli-grid");
  function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  if (cli) K.cli.forEach(function (r) { cli.appendChild(el("div", "cli-row", "<code>" + esc(r[0]) + "</code><span>" + esc(r[1]) + "</span>")); });

  /* config keys */
  var cfg = mount("cfg-keys");
  if (cfg) K.config.forEach(function (r) { cfg.appendChild(el("div", "cfg-key", "<code>" + r[0] + "</code><span>" + r[1] + "</span>")); });

  /* comparison */
  var cmp = mount("cmp");
  if (cmp) {
    var thead = "<thead><tr><th>Capability</th>";
    K.compareCols.forEach(function (c, i) { thead += '<th class="' + (i === 0 ? "me" : "") + '">' + c + "</th>"; });
    thead += "</tr></thead>";
    var tbody = "<tbody>";
    K.compareRows.forEach(function (row) {
      tbody += "<tr><th>" + row[0] + "</th>";
      for (var i = 1; i < row.length; i++) {
        var v = row[i], cell;
        if (v === "y") cell = '<span class="y">✓</span>';
        else if (v === "p") cell = '<span class="p">◐</span>';
        else if (v === "n") cell = '<span class="n">✕</span>';
        else cell = '<span class="badge-ok">' + v + "</span>";
        tbody += '<td class="' + (i === 1 ? "me" : "") + '">' + cell + "</td>";
      }
      tbody += "</tr>";
    });
    tbody += "</tbody>";
    cmp.innerHTML = thead + tbody;
  }
  var cmpNote = mount("cmp-note");
  if (cmpNote && K.compareNote) cmpNote.textContent = K.compareNote;

  /* faq */
  var faq = mount("faq");
  if (faq) K.faq.forEach(function (q) {
    faq.appendChild(el("details", "qa", '<summary>' + q[0] + '<span class="chev"></span></summary><div class="qa-body"><p>' + q[1] + "</p></div>"));
  });
})();
