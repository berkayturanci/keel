/* ============================================================
   keel — animation engine
   1) Hero backbone: an issue travels s0 → s12, slots glow brass
   2) Command showcase: 8 featured commands, each its own scene
   3) Final reveal: keel ships itself → MERGE
   ============================================================ */
(function () {
  "use strict";
  var K = window.KEEL;
  if (!K) return;
  var REDUCE = window.__keelReduce;

  /* tiny DOM helper */
  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  /* cancelable, self-looping scene runner. SPEED > 1 = slower; ÷ user speed. */
  var SPEED = 1.7;
  window.__sceneSpeed = 1;
  function scene(stage, builder) {
    var timers = [], alive = true;
    function at(ms, fn) { var t = setTimeout(function () { if (alive) fn(); }, ms * SPEED / window.__sceneSpeed); timers.push(t); return t; }
    function run() { timers.forEach(clearTimeout); timers = []; stage.innerHTML = ""; builder(stage, at, run); }
    run();
    return function () { alive = false; timers.forEach(clearTimeout); };
  }

  /* ============================================================
     1) HERO BACKBONE
     ============================================================ */
  function buildHero() {
    var rail = document.getElementById("ht-rail");
    var fillBar = document.getElementById("ht-railfill");
    var log = document.getElementById("ht-log");
    var statusLine = document.getElementById("ht-statusline");
    var verdict = document.getElementById("ht-verdict");
    var replay = document.getElementById("ht-replay");
    if (!rail || !log) return;
    var steps = K.backbone, N = steps.length;

    // build the backbone strip
    steps.forEach(function (s, i) {
      var n = el("div", "ht-node" + (s.slot ? " slot" : ""), '<span class="ht-dot"></span><span class="ht-id">' + s.id + "</span>");
      n.id = "ht-n" + i; rail.appendChild(n);
    });
    function nodeState(upto) {
      for (var i = 0; i < N; i++) {
        var n = document.getElementById("ht-n" + i);
        n.classList.remove("on", "done");
        if (i < upto) n.classList.add("done");
        else if (i === upto) n.classList.add("on");
      }
      fillBar.style.width = (upto < 0 ? 0 : upto / (N - 1) * 100) + "%";
    }

    // build-log timeline: [html, uptoNode, statusText, gap]
    var LINES = [
      ['<span class="c">$ keel ship projects/keel.yaml --root .</span>', -1, "resolving config…", 420],
      ['<span class="d">keel-core ^1.0 · base main · TZ Europe/Istanbul</span>', 0, "s0 config — schema ok", 360],
      ['<span class="t">s1</span> select    issue #128 · auth retry', 1, "s1 select — #128", 320],
      ['<span class="t">s2</span> branch    feat/128-auth-retry · <span class="t">s3</span> guard <span class="g">ok</span>', 3, "s2 branch · s3 guard", 380],
      ['<span class="t">s4</span> implement <span class="a">[agent]</span> patch written', 4, "s4 implement — agent", 380],
      ['<span class="t">s5</span> classify  risk <span class="a">TIER-3</span> → 3 reviewer(s)', 5, "s5 classify — TIER-3", 360],
      ['<span class="t">s6</span> ci <span class="g">green</span> · <span class="t">s7</span> review jury <span class="g">pass</span>', 7, "s6 ci · s7 review", 400],
      ['<span class="t">s8</span> test      build <span class="g">ok</span> · lint <span class="g">ok</span>', 8, "s8 test — gates ok", 360],
      ['<span class="t">s9</span> fixloop   0 fixes · <span class="t">s10</span> merge window <span class="g">OPEN</span>, lock held', 10, "s10 merge — OPEN", 400],
      ['<span class="t">s11</span> capture  report · <span class="t">s12</span> close issue #128 <span class="g">closed</span>', 12, "s12 close", 380],
      ['<span class="d">────────────────────────────</span>', 12, "", 220],
      ['<span class="g">merged + closed — issue #142 ✓</span>', 12, "__done__", 0],
    ];

    var lineEls = LINES.map(function (l) { var e = el("span", "ln", l[0]); log.appendChild(e); return e; });

    if (REDUCE) {
      lineEls.forEach(function (e) { e.classList.add("show"); });
      nodeState(N - 1); statusLine.textContent = "merged + closed"; verdict.classList.add("show");
      return;
    }

    var timers = [], started = false;
    function clear() { timers.forEach(clearTimeout); timers = []; }
    function run() {
      clear();
      lineEls.forEach(function (e) { e.classList.remove("show"); });
      verdict.classList.remove("show"); statusLine.style.opacity = "1";
      nodeState(-1); statusLine.textContent = "running the backbone…";
      var t = 0;
      LINES.forEach(function (l, i) {
        t += l[3];
        (function (i, l) {
          timers.push(setTimeout(function () {
            lineEls[i].classList.add("show");
            nodeState(l[1]);
            if (l[2] === "__done__") {
              statusLine.style.opacity = "0"; verdict.classList.add("show");
              document.getElementById("ht-n" + (N - 1)).classList.remove("on");
              document.getElementById("ht-n" + (N - 1)).classList.add("done");
            } else if (l[2]) { statusLine.textContent = l[2]; }
          }, t));
        })(i, l);
      });
      timers.push(setTimeout(run, t + 2600));
    }
    function go() { if (started) return; started = true; run(); }
    if (replay) replay.addEventListener("click", function () { started = true; run(); });

    var hostEl = document.querySelector(".ht");
    if ("IntersectionObserver" in window && hostEl) {
      var io = new IntersectionObserver(function (e) { if (e[0].isIntersecting) { go(); io.disconnect(); } }, { threshold: 0.2 });
      io.observe(hostEl);
      setTimeout(go, 1300);
    } else go();
  }

  /* ============================================================
     2) COMMAND SHOWCASE
     ============================================================ */
  var SCENES = {
    ship: function (stage, at, loop) {
      var steps = K.backbone;
      var SLOT_PRIMARY = { "guard": 1, "after-implement": 1, "reviewers": 1, "tester": 1, "test": 1, "pre-merge": 1, "capture": 1, "post-merge": 1 };
      var SLOT_BLOCK = { "guard": 1, "tester": 1, "test": 1, "pre-merge": 1 };
      stage.appendChild(el("div", "sc-lab", "flagship · drives the whole backbone end-to-end"));
      var row = el("div", "sc-bb"); var nodes = [];
      steps.forEach(function (s, i) {
        var n = el("div", "sc-bnode" + (s.block ? " block" : "") + (i >= 10 ? " merge" : ""), '<span class="d"></span><span class="l">' + s.id + "</span>");
        nodes.push(n); row.appendChild(n);
      });
      stage.appendChild(row);
      var jline = el("div", "sc-jline");
      row.appendChild(jline);
      var jdot = el("div", "sc-jdot");
      row.appendChild(jdot);
      function dotX(i2) { return nodes[i2].offsetLeft + nodes[i2].offsetWidth / 2; }
      requestAnimationFrame(function () { if (nodes[0]) { var x = dotX(0); jdot.style.left = x + "px"; jline.style.width = Math.max(0, x - 7) + "px"; } });
      var read = el("div", "sc-read", '<span class="rid">s0</span><span class="rname">config</span><span class="rblurb">' + steps[0].blurb + "</span>");
      stage.appendChild(read);
      var cap = el("div", "sc-cap", '<span class="blink"></span><span>$ /keel:ship --issue 128</span>');
      stage.appendChild(cap); var capt = cap.querySelector("span:last-child");
      steps.forEach(function (s, i) {
        at(500 + i * 470, function () {
          for (var j = 0; j < i; j++) { nodes[j].classList.remove("on"); nodes[j].classList.add("done"); }
          nodes[i].classList.add("on");
          var x = dotX(i);
          jdot.style.left = x + "px";
          jline.style.width = Math.max(0, x - 7) + "px";
          read.className = "sc-read" + (s.block ? " gate" : "") + (i >= 10 ? " merge" : "");
          var tag = (s.block ? ' <span class="rt gate">can block</span>' : "") + (s.agent ? ' <span class="rt agent">agent</span>' : "");
          var rslots = s.slots && s.slots.length ? '<span class="rslots">slots:' + s.slots.map(function (h) { return '<code class="' + (SLOT_BLOCK[h] ? "block" : SLOT_PRIMARY[h] ? "primary" : "") + '">' + h + (SLOT_BLOCK[h] ? " ⊘" : "") + "</code>"; }).join("") + "</span>" : "";
          read.innerHTML = '<span class="rid">' + s.id + '</span><span class="rname">' + s.name + '</span><span class="rblurb">' + s.blurb + "</span>" + tag + rslots;
          if (i === steps.length - 1) { nodes[i].classList.add("done"); jdot.classList.add("merged"); jline.classList.add("merged"); cap.classList.add("ok"); capt.textContent = "✓ issue #128 merged + closed"; }
          else capt.textContent = "$ /keel:ship --issue 128 · running…";
        });
      });
      at(500 + steps.length * 470 + 2200, loop);
    },

    implement: function (stage, at, loop) {
      stage.appendChild(el("div", "sc-lab", "s4 implement · delegate to the right agent"));
      var chips = el("div", "lanes");
      var c1 = el("div", "chip", '<span class="dot"></span>issue #128 · auth retry<span class="meta">role: bug</span>');
      var c2 = el("div", "chip muted", '<span class="dot"></span>implementer · claude-code<span class="meta">selected</span>');
      chips.appendChild(c1); chips.appendChild(c2);
      stage.appendChild(chips);
      var ed = el("div", "editor", '<div class="editor-bar"><span class="ed-dot" style="background:var(--accent)"></span>src/auth/retry.py · patch</div>');
      var lines = [
        '<span class="kw">def</span> with_retry(call, attempts=<span class="st">3</span>):',
        '    <span class="kw">for</span> i <span class="kw">in</span> range(attempts):',
        '        ok, err = call()',
        '        <span class="kw">if</span> ok: <span class="kw">return</span> ok',
        '    <span class="kw">raise</span> RetryExhausted(err)',
      ];
      var body = el("div", "", "");
      lines.forEach(function (l, i) { var ln = el("span", "code-line" + (i >= 1 ? " add" : ""), l); body.appendChild(ln); });
      ed.appendChild(body); stage.appendChild(ed);
      var cap = el("div", "sc-cap", '<span class="blink"></span><span>picking implementer for issue #128…</span>');
      stage.appendChild(cap); var capt = cap.querySelector("span:last-child");
      at(500, function () { c2.classList.remove("muted"); c2.classList.add("work"); capt.textContent = "claude-code writing the change…"; });
      body.querySelectorAll(".code-line").forEach(function (ln, i) { at(900 + i * 320, function () { ln.classList.add("show"); }); });
      at(900 + lines.length * 320 + 300, function () { c2.classList.remove("work"); c2.classList.add("done"); c2.querySelector(".meta").textContent = "patch ready"; cap.classList.add("ok"); capt.textContent = "s4 complete — handed back to the orchestrator"; });
      at(900 + lines.length * 320 + 2200, loop);
    },

    review: function (stage, at, loop) {
      stage.appendChild(el("div", "sc-lab", "s7 review → s9 fixloop · does not merge"));
      var revs = [["claude", 70], ["codex", 92], ["gemini", 55]];
      var lanes = el("div", "lanes"); var fills = [];
      revs.forEach(function (r) {
        var lane = el("div", "lane");
        lane.appendChild(el("div", "chip", '<span class="dot"></span>reviewer · ' + r[0]));
        var track = el("div", "lane-track"); var f = el("div", "lane-fill"); track.appendChild(f); lane.appendChild(track);
        var out = el("div", "lane-out", "…"); lane.appendChild(out);
        fills.push([f, out, lane, r[1]]); lanes.appendChild(lane);
      });
      stage.appendChild(lanes);
      var finds = el("div", "finds");
      finds.appendChild(el("div", "find", '<span class="sev major">major</span><code>retry.py:5</code> error swallowed on last attempt'));
      finds.appendChild(el("div", "find", '<span class="sev minor">nit</span><code>retry.py:1</code> missing type hints'));
      finds.appendChild(el("div", "find", '<span class="sev ok">fixed</span>round 1 patch applied · re-reviewed'));
      stage.appendChild(finds);
      var cap = el("div", "sc-cap", '<span class="blink"></span><span>3 reviewers reviewing the diff in parallel…</span>');
      stage.appendChild(cap); var capt = cap.querySelector("span:last-child");
      at(350, function () { fills.forEach(function (a) { a[0].style.width = a[3] + "%"; }); });
      at(1250, function () { fills.forEach(function (a) { a[1].textContent = "done"; a[2].classList.add("done"); a[0].style.width = "100%"; }); capt.textContent = "structured findings merged…"; });
      [0, 1].forEach(function (i) { at(1600 + i * 500, function () { finds.children[i].classList.add("show"); }); });
      at(2700, function () { capt.textContent = "fix round 1 → bounded retry"; });
      at(3300, function () { finds.children[2].classList.add("show"); cap.classList.add("ok"); capt.textContent = "satisfied — back to the loop (no merge here)"; });
      at(5400, loop);
    },

    prloop: function (stage, at, loop) {
      stage.appendChild(el("div", "sc-lab", "iterate PR #214 · comments + CI → green → merge"));
      var checks = el("div", "checks");
      var rows = [["build", "pass"], ["lint", "pass"], ["unit tests", "fail"], ["coverage", "pass"]];
      var elr = rows.map(function (r) {
        var row = el("div", "check"); var ic = el("span", "ci-ic pend"); row.appendChild(ic);
        row.appendChild(el("span", "ci-name", r[0])); var t = el("span", "ci-time", "queued"); row.appendChild(t);
        checks.appendChild(row); return { row: row, ic: ic, t: t, want: r[1] };
      });
      stage.appendChild(checks);
      var note = el("div", "fix-note", '<span>⟳</span><span><b>round 1</b> — addressed 2 review comments, pushed, re-ran checks.</span>');
      stage.appendChild(note);
      var cap = el("div", "sc-cap", '<span class="blink"></span><span>waiting on CI for PR #214…</span>');
      stage.appendChild(cap); var capt = cap.querySelector("span:last-child");
      elr.forEach(function (r, i) {
        at(500 + i * 350, function () {
          r.ic.className = "ci-ic " + r.want; r.t.textContent = r.want === "fail" ? "failed" : "12s";
          if (r.want === "fail") r.row.classList.add("failed");
        });
      });
      at(2200, function () { capt.textContent = "1 check red → fixing review comments"; note.classList.add("show"); });
      at(3200, function () { elr[2].ic.className = "ci-ic pass"; elr[2].t.textContent = "14s"; elr[2].row.classList.remove("failed"); capt.textContent = "all green → handing off to windowed merge"; cap.classList.add("ok"); });
      at(5200, loop);
    },

    regression: function (stage, at, loop) {
      stage.appendChild(el("div", "sc-lab", "fan-out subagents · scan every area in parallel"));
      var names = ["config", "model", "gates", "cli", "orchestrator", "findings", "extensions", "adapters"];
      var grid = el("div", "areas"); var cells = names.map(function (n) { var c = el("div", "area", n); grid.appendChild(c); return c; });
      stage.appendChild(grid);
      var iss = el("div", "lanes"); stage.appendChild(iss);
      var cap = el("div", "sc-cap", '<span class="blink"></span><span>spawning per-area review subagents…</span>');
      stage.appendChild(cap); var capt = cap.querySelector("span:last-child");
      cells.forEach(function (c, i) { at(300 + i * 180, function () { c.classList.add("scan"); }); });
      var hits = { 2: "gates.py:88 off-by-one in fixloop cap", 4: "orchestrator.py:140 unguarded merge retry" };
      cells.forEach(function (c, i) {
        at(1900 + i * 120, function () {
          c.classList.remove("scan");
          if (hits[i]) c.classList.add("hit"); else c.classList.add("clear");
        });
      });
      at(3100, function () { capt.textContent = "2 high-confidence findings · deduped against open issues"; });
      [["#241", hits[2]], ["#242", hits[4]]].forEach(function (h, i) {
        at(3400 + i * 500, function () {
          var row = el("div", "issue", '<span class="ino">' + h[0] + '</span><span class="ititle">' + h[1] + '</span><span class="labels"><span class="ilabel status show">routed → ship</span></span>');
          iss.appendChild(row);
        });
      });
      at(4600, function () { cap.classList.add("ok"); capt.textContent = "fix issues opened → each handed to /keel:ship"; });
      at(6400, loop);
    },

    triage: function (stage, at, loop) {
      stage.appendChild(el("div", "sc-lab", "classifier subagent · label every unlabelled issue"));
      var data = [
        ["#131", "auth retry swallows last error", [["bug", "role"], ["P1", "prio"], ["triaged", "status"]]],
        ["#132", "add --dry-run to keel ship", [["feature", "role"], ["P2", "prio"], ["triaged", "status"]]],
        ["#133", "test_window flaky on CI", [["flake", "role"], ["P3", "prio"], ["triaged", "status"]]],
      ];
      var list = el("div", "lanes"); var rows = [];
      data.forEach(function (d) {
        var labs = d[2].map(function (l) { return '<span class="ilabel ' + l[1] + '">' + l[0] + "</span>"; }).join("");
        var row = el("div", "issue", '<span class="ino">' + d[0] + '</span><span class="ititle">' + d[1] + '</span><span class="labels">' + labs + "</span>");
        list.appendChild(row); rows.push(row);
      });
      stage.appendChild(list);
      var cap = el("div", "sc-cap", '<span class="blink"></span><span>3 issues missing a status label…</span>');
      stage.appendChild(cap); var capt = cap.querySelector("span:last-child");
      var k = 0;
      data.forEach(function (d, ri) {
        d[2].forEach(function (_, li) {
          at(500 + (k++) * 320, function () {
            rows[ri].querySelectorAll(".ilabel")[li].classList.add("show");
            capt.textContent = "applying " + ["role", "priority", "status"][li] + " → " + rows[ri].querySelector(".ino").textContent;
          });
        });
      });
      at(500 + k * 320 + 200, function () { cap.classList.add("ok"); capt.textContent = "labels applied from the existing label set"; });
      at(500 + k * 320 + 2100, loop);
    },

    cicheck: function (stage, at, loop) {
      stage.appendChild(el("div", "sc-lab", "latest CI run · diagnose, propose ONE fix"));
      var checks = el("div", "checks");
      var rows = [["lint (ruff)", "pass"], ["unit suite", "fail"], ["coverage gate", "pass"]];
      var elr = rows.map(function (r) {
        var row = el("div", "check"); var ic = el("span", "ci-ic pend"); row.appendChild(ic);
        row.appendChild(el("span", "ci-name", r[0])); var t = el("span", "ci-time", "running"); row.appendChild(t);
        checks.appendChild(row); return { row: row, ic: ic, t: t, want: r[1] };
      });
      stage.appendChild(checks);
      var note = el("div", "fix-note", '<span>◎</span><span><b>root cause</b> — <code style="font-family:var(--font-mono)">window.py:88</code> DST edge: window flips an hour early.<br><b>Proposed fix:</b> use zoneinfo-aware compare. <i>Never auto-applied.</i></span>');
      stage.appendChild(note);
      var cap = el("div", "sc-cap", '<span class="blink"></span><span>reading the latest CI run…</span>');
      stage.appendChild(cap); var capt = cap.querySelector("span:last-child");
      elr.forEach(function (r, i) { at(500 + i * 400, function () { r.ic.className = "ci-ic " + r.want; r.t.textContent = r.want === "fail" ? "failed" : "ok"; if (r.want === "fail") r.row.classList.add("failed"); }); });
      at(2100, function () { capt.textContent = "located failing job → unit suite · test_window"; });
      at(3000, function () { note.classList.add("show"); capt.textContent = "one fix proposed for you to review"; cap.classList.add("ok"); });
      at(5400, loop);
    },

    coverage: function (stage, at, loop) {
      stage.appendChild(el("div", "sc-lab", "per-PR coverage delta · base → head"));
      var data = [["orchestrator.py", 94, 100], ["gates.py", 88, 100], ["cli.py", 97, 100]];
      var rowsBox = el("div", "cov-rows"); var heads = [];
      data.forEach(function (d) {
        var row = el("div", "cov-row");
        row.appendChild(el("div", "cov-name", d[0]));
        var track = el("div", "cov-track"); var base = el("div", "cov-base"); var head = el("div", "cov-head");
        base.style.width = d[1] + "%"; track.appendChild(base); track.appendChild(head); row.appendChild(track);
        var delta = el("div", "cov-delta", '<span class="from">' + d[1] + "%</span> → " + d[2] + "%"); row.appendChild(delta);
        heads.push([head, d[2]]); rowsBox.appendChild(row);
      });
      stage.appendChild(rowsBox);
      var note = el("div", "fix-note", '<span>▲</span><span><b>hot spot</b> — <code style="font-family:var(--font-mono)">gates.py</code> fixloop branch was low × high-risk. Issue opened to close the gap → routed to ship.</span>');
      stage.appendChild(note);
      var cap = el("div", "sc-cap", '<span class="blink"></span><span>computing coverage on the PR diff…</span>');
      stage.appendChild(cap); var capt = cap.querySelector("span:last-child");
      at(400, function () { heads.forEach(function (h) { h[0].style.width = h[1] + "%"; }); capt.textContent = "delta posted to PR #214"; });
      at(1700, function () { note.classList.add("show"); capt.textContent = "low-coverage × high-risk hot spot flagged"; cap.classList.add("ok"); });
      at(4000, loop);
    },

    /* morning / wrap — a briefing report assembles */
    report: function (stage, at, loop) {
      stage.appendChild(el("div", "sc-lab", "session report · assembled from the run ledger"));
      var rows = [
        ["shipped since last brief", "3 issues · #128 #131 #140", "ok"],
        ["deferrals carried over", "1 · capture deferred on #126", ""],
        ["health signals", "CI green · coverage 100% · 0 flakes", "ok"],
        ["ranked focus list", "1. #142 stale-prs guard · 2. #145 docs", ""],
      ];
      var list = el("div", "lanes");
      var made = rows.map(function (r) {
        var row = el("div", "check", '<span class="ci-ic pend"></span><span class="ci-name">' + r[0] + '</span><span class="ci-time"></span>');
        list.appendChild(row); return row;
      });
      stage.appendChild(list);
      var cap = el("div", "sc-cap", '<span class="blink"></span><span>reading checkpoint + run ledger…</span>');
      stage.appendChild(cap); var capt = cap.querySelector("span:last-child");
      rows.forEach(function (r, i) {
        at(600 + i * 700, function () {
          made[i].querySelector(".ci-ic").className = "ci-ic pass";
          made[i].querySelector(".ci-time").textContent = r[1];
          capt.textContent = r[0] + " ✓";
        });
      });
      at(600 + rows.length * 700 + 400, function () { cap.classList.add("ok"); capt.textContent = "report posted — ranked focus list ready"; });
      at(600 + rows.length * 700 + 2600, loop);
    },

    /* work-block / overnight — a queue of issues through ship */
    queue: function (stage, at, loop) {
      stage.appendChild(el("div", "sc-lab", "multi-issue work block · each item through /keel:ship"));
      var iss = [["#128", "auth retry"], ["#131", "--dry-run flag"], ["#140", "fixloop coverage"]];
      var lanes = el("div", "lanes"); var made = [];
      iss.forEach(function (d) {
        var lane = el("div", "lane");
        lane.appendChild(el("div", "chip", '<span class="dot"></span>' + d[0] + ' · ' + d[1]));
        var track = el("div", "lane-track"); var f = el("div", "lane-fill"); track.appendChild(f); lane.appendChild(track);
        var out = el("div", "lane-out", "queued"); lane.appendChild(out);
        lanes.appendChild(lane); made.push({ lane: lane, f: f, out: out });
      });
      stage.appendChild(lanes);
      var cap = el("div", "sc-cap", '<span class="blink"></span><span>queue snapshot · 3 ready items · worktrees isolated</span>');
      stage.appendChild(cap); var capt = cap.querySelector("span:last-child");
      made.forEach(function (m, i) {
        at(500 + i * 1500, function () {
          m.out.textContent = "shipping"; m.f.style.width = "60%";
          capt.textContent = iss[i][0] + " — readiness refresh → ship → merge";
        });
        at(500 + i * 1500 + 1100, function () {
          m.lane.classList.add("done"); m.f.style.width = "100%"; m.out.textContent = "merged";
        });
      });
      at(500 + made.length * 1500 + 600, function () { cap.classList.add("ok"); capt.textContent = "final report — 3 merged · 0 blocked · ledger written"; });
      at(500 + made.length * 1500 + 2600, loop);
    },

    /* review-all-day — sweep commits in a time window */
    sweep: function (stage, at, loop) {
      stage.appendChild(el("div", "sc-lab", "time-window sweep · every commit reviewed · read-only"));
      var commits = ["a41f2c", "b82e90", "c19d44", "d73a1b", "e05c67", "f64b28"];
      var grid = el("div", "areas");
      var cells = commits.map(function (c) { var d = el("div", "area", c); grid.appendChild(d); return d; });
      stage.appendChild(grid);
      var finds = el("div", "finds");
      finds.appendChild(el("div", "find", '<span class="sev major">finding</span><code>c19d44</code> unguarded retry in merge path → issue #151'));
      stage.appendChild(finds);
      var cap = el("div", "sc-cap", '<span class="blink"></span><span>scanning 6 commits since 07:00…</span>');
      stage.appendChild(cap); var capt = cap.querySelector("span:last-child");
      cells.forEach(function (c, i) {
        at(500 + i * 420, function () { c.classList.add("scan"); });
        at(500 + i * 420 + 380, function () { c.classList.remove("scan"); c.classList.add(i === 2 ? "hit" : "clear"); });
      });
      at(500 + commits.length * 420 + 400, function () { finds.children[0].classList.add("show"); capt.textContent = "1 serious finding → one issue opened"; cap.classList.add("ok"); });
      at(500 + commits.length * 420 + 2600, loop);
    },

    /* deps-audit / flake-audit / stale-prs — classify a scanned list */
    audit: function (stage, at, loop) {
      stage.appendChild(el("div", "sc-lab", "audit sweep · classify → dedupe → open tracking issues"));
      var rows = [
        ["pyyaml 6.0.1", "routine · minor bump", "minor"],
        ["test_window.py::dst", "flake · 2/40 runs failed", "major"],
        ["PR #198 · 14d quiet", "stale · drifted 22 commits", "minor"],
      ];
      var finds = el("div", "finds");
      var made = rows.map(function (r) {
        var d = el("div", "find", '<span class="sev ' + r[2] + '">' + (r[2] === "major" ? "flag" : "note") + '</span><code>' + r[0] + "</code> " + r[1]);
        finds.appendChild(d); return d;
      });
      stage.appendChild(finds);
      var iss = el("div", "lanes"); stage.appendChild(iss);
      var cap = el("div", "sc-cap", '<span class="blink"></span><span>scanning ecosystems / CI history / open PRs…</span>');
      stage.appendChild(cap); var capt = cap.querySelector("span:last-child");
      made.forEach(function (m, i) { at(600 + i * 800, function () { m.classList.add("show"); capt.textContent = rows[i][0] + " — classified"; }); });
      at(600 + rows.length * 800 + 400, function () {
        iss.appendChild(el("div", "issue", '<span class="ino">#152</span><span class="ititle">deduped against tracked items · 1 new tracking issue</span><span class="labels"><span class="ilabel status show">routed → ship</span></span>'));
        cap.classList.add("ok"); capt.textContent = "findings appended · fixes routed to /keel:ship";
      });
      at(600 + rows.length * 800 + 2800, loop);
    },
  };

  function buildShowcase() {
    var rail = document.getElementById("show-rail");
    var stage = document.getElementById("show-scene");
    var cmdEl = document.getElementById("sc-cmd");
    var grpEl = document.getElementById("sc-group");
    var detail = document.getElementById("show-detail");
    if (!rail || !stage) return;
    var featured = K.commands.slice();
    var ARGS = window.KEEL_ARGS || {};
    var stop = null, active = -1;

    function activate(i) {
      if (i === active) return;
      active = i;
      var c = featured[i];
      rail.querySelectorAll(".show-tab").forEach(function (b, j) { b.classList.toggle("on", j === i); });
      if (cmdEl) cmdEl.textContent = c.name;
      if (grpEl) grpEl.textContent = c.group;
      if (detail) {
        var a = ARGS[c.slug];
        var params = "";
        if (a && a.flags && a.flags.length) {
          params = '<p class="det-params-h">Arguments &amp; flags</p><div class="det-params">' +
            a.flags.map(function (f) { return '<code class="det-flag">' + f.replace(/</g, "&lt;").replace(/>/g, "&gt;") + "</code>"; }).join("") + "</div>";
        }
        detail.innerHTML = "<p>" + c.detail + "</p>" +
          '<p><span class="det-cmd"><span class="gp">/</span>' + (K.cmdExample && K.cmdExample[c.slug] ? K.cmdExample[c.slug].replace("/", "") : c.name.replace("/keel:", "keel:")) + "</span></p>" + params +
          '<p><a class="det-link" href="docs.html#commands">full command reference →</a></p>';
      }
      if (stop) stop();
      var builder = SCENES[c.scene] || SCENES.ship;
      stop = scene(stage, builder);
    }

    var lastGroup = null;
    var scSpeed = document.getElementById("sc-speed");
    var SC_SPEEDS = [1, 2, 0.5];
    if (scSpeed) scSpeed.addEventListener("click", function () {
      var ix = SC_SPEEDS.indexOf(window.__sceneSpeed);
      window.__sceneSpeed = SC_SPEEDS[(ix + 1) % SC_SPEEDS.length];
      scSpeed.textContent = window.__sceneSpeed + "×";
      var i = active; active = -1; activate(i); // restart the scene at the new speed
    });
    featured.forEach(function (c, i) {
      if (c.group !== lastGroup) { rail.appendChild(el("div", "st-group", c.group)); lastGroup = c.group; }
      var b = el("button", "show-tab", '<span class="st-name">' + c.name + (c.flagship ? ' <span class="st-flag">flagship</span>' : "") + '</span><span class="st-one">' + c.one + "</span>");
      b.type = "button";
      b.addEventListener("click", function () { activate(i); });
      rail.appendChild(b);
    });
    activate(0);
  }

  /* ============================================================
     3) FINAL REVEAL — keel ships itself
     ============================================================ */
  /* four example runs, looped */
  var RUNS = [
    { n: 142, title: "stale-prs rebase guard", tier: 3, fix: false },
    { n: 155, title: "keel status --json progress snapshot", tier: 2, fix: false },
    { n: 149, title: "stabilize flaky test_window", tier: 1, fix: true },
    { n: 163, title: "capture redaction deny patterns", tier: 2, fix: false },
  ];
  function termFor(s) {
    var lines = [
      ['<span class="cmd">$ keel ship --issue ' + s.n + ' .keel/project.yaml --root .</span>', 0],
      ['<span class="dim">keel ship — keel-core  (base main)</span>', 460],
      ['<span class="head">  resolving config…</span> <span class="ok">ok</span>  <span class="dim">core ^1.0 · TZ Europe/Istanbul</span>', 700],
      ['<span class="head">  s1 select</span>   issue #' + s.n + ' · ' + s.title, 520],
      ['<span class="head">  s3 guard</span>    preflight rules <span class="ok">ok</span>', 460],
      ['<span class="head">  s4 implement</span> <span class="brass">[agent]</span> patch written', 500],
      ['<span class="head">  s5 classify</span>  risk tier <span class="brass">TIER-' + s.tier + '</span> → ' + s.tier + ' reviewer(s)', 500],
      ['<span class="head">  s6 ci</span>        workflows green', 460],
      ['<span class="head">  s7 review</span>   ' + s.tier + ' reviewer(s) · jury gate <span class="ok">pass</span>', 500],
      ['<span class="head">  s8 test</span>     gate build <span class="ok">ok</span> · gate lint ' + (s.fix ? '<span class="bad">fail</span>' : '<span class="ok">ok</span>'), 500],
    ];
    if (s.fix) lines.push(['<span class="head">  s9 fixloop</span>  round 1 → gate lint <span class="ok">ok</span>', 560]);
    lines.push(['<span class="head">  s10 merge</span>   window <span class="ok">OPEN</span> · lock held', 500]);
    lines.push(['<span class="dim">  ───────────────────────────────</span>', 420]);
    lines.push(['<span class="ok">  merged + closed — issue #' + s.n + ' ✓</span>', 480]);
    return lines;
  }
  function assessFor(s) {
    return [
      ['risk tier', '<span class="pill-tier">TIER-' + s.tier + '</span>', 0],
      ['reviewers', '<span class="assess-v">' + s.tier + ' <span class="dim" style="color:var(--faint)">required</span></span>', 1],
      ['merge window', '<span class="pill-open">OPEN</span>', 2],
      ['gate · build', '<span class="assess-v"><span class="chk">✓</span> ok</span>', 3],
      ['gate · lint', s.fix ? '<span class="assess-v"><span class="chk">✓</span> ok <span class="dim" style="color:var(--faint)">after round 1</span></span>' : '<span class="assess-v"><span class="chk">✓</span> ok</span>', 4],
      ['jury gate', '<span class="assess-v"><span class="chk">✓</span> pass</span>', 5],
    ];
  }

  function buildReveal() {
    var body = document.getElementById("term-body");
    var assess = document.getElementById("assess");
    var decision = document.getElementById("decision");
    var replay = document.getElementById("replay");
    var title = document.querySelector("#view-ship .term-title") || document.querySelector(".term-title");
    if (!body) return;
    var dsvg = decision ? (decision.querySelector("svg") ? decision.querySelector("svg").outerHTML : "") : "";
    var runIdx = 0, timers = [];

    function render() {
      timers.forEach(clearTimeout); timers = [];
      var s = RUNS[runIdx % RUNS.length];
      var TERM = termFor(s), ASSESS = assessFor(s);
      if (title) title.textContent = "keel ship --issue " + s.n + " · keel-core";
      if (decision) decision.innerHTML = dsvg + " MERGED — issue #" + s.n + " closed";
      body.innerHTML = ""; assess.innerHTML = ""; decision.classList.remove("show", "pulse");
      var lineEls = TERM.map(function (tl) { var l = el("span", "cl", tl[0]); body.appendChild(l); return l; });
      var assessEls = ASSESS.map(function (a) {
        var r = el("div", "assess-row", '<span class="assess-k">' + a[0] + "</span>" + a[1]); assess.appendChild(r); return r;
      });
      if (REDUCE) {
        lineEls.forEach(function (l) { l.classList.add("show"); });
        assessEls.forEach(function (r) { r.classList.add("show"); });
        decision.classList.add("show");
        return;
      }
      var t = 0;
      TERM.forEach(function (tl, i) { t += tl[1]; (function (i) { timers.push(setTimeout(function () { lineEls[i].classList.add("show"); body.scrollTop = body.scrollHeight; }, t)); })(i); });
      ASSESS.forEach(function (a) { timers.push(setTimeout(function () { assessEls[a[2]].classList.add("show"); }, 1200 + a[2] * 760)); });
      timers.push(setTimeout(function () { decision.classList.add("show", "pulse"); }, t + 500));
      // infinite loop: next example after a pause
      timers.push(setTimeout(function () { runIdx++; render(); }, t + 4200));
    }
    render._restart = function () { render(); };
    // start when visible
    var host = document.getElementById("reveal-band");
    var started = false;
    function go() { if (started) return; started = true; render(); }
    if ("IntersectionObserver" in window && host) {
      var io = new IntersectionObserver(function (e) { if (e[0].isIntersecting) { go(); io.disconnect(); } }, { threshold: 0.25 });
      io.observe(host);
      setTimeout(go, 1700);
    } else go();
    if (replay) replay.addEventListener("click", function () { render(); });
    // pause the loop when the tab is hidden
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { timers.forEach(clearTimeout); }
      else if (started && !REDUCE) render();
    });
  }

  /* ---- boot -------------------------------------------------------- */
  buildHero();
  buildShowcase();
  buildReveal();
})();
