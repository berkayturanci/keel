/* ============================================================
   keel — coverage report (animated), from window.KEEL.coverage
   ============================================================ */
(function () {
  "use strict";
  var K = window.KEEL;
  if (!K) return;
  var C = K.coverage;
  var REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;
  function el(tag, cls, html) { var n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; }

  /* summary cards */
  var sum = document.getElementById("cov-summary");
  if (sum) {
    var cards = [
      ["line coverage", C.overall.line + "%", "ok"],
      ["branch coverage", C.overall.branch + "%", "ok"],
      ["statements", C.overall.statements.toLocaleString(), ""],
      ["branches", C.overall.branches.toLocaleString(), ""],
    ];
    cards.forEach(function (c) {
      sum.appendChild(el("div", "cov-card" + (c[2] ? " ok" : ""), '<div class="cc-v">' + c[1] + '</div><div class="cc-k">' + c[0] + "</div>"));
    });
  }

  /* module table */
  var tb = document.getElementById("cov-tbody");
  var bars = [];
  if (tb) {
    C.modules.forEach(function (m) {
      var name = m[0], line = m[1], branch = m[2], stmts = m[3];
      var tr = el("tr", "cov-r");
      tr.innerHTML =
        '<td><a class="cov-file" href="coverage/" data-file="' + name + '" target="_blank" rel="noopener" title="Open this file\'s coverage page"><code>' + name + "</code></a></td>" +
        '<td class="cov-stm" data-n="' + stmts + '">0</td><td>0</td>' +
        '<td class="cov-pct"><span class="pct-num" data-n="' + branch + '">0%</span><span class="cov-mini"><span class="cov-mini-fill" data-pct="' + branch + '"></span></span></td>' +
        '<td class="cov-pct"><span class="pct-num" data-n="' + line + '">0%</span><span class="cov-mini"><span class="cov-mini-fill" data-pct="' + line + '"></span></span></td>';
      tb.appendChild(tr);
    });
    bars = [].slice.call(tb.querySelectorAll(".cov-mini-fill"));
  }

  /* clicking a module opens its own page in the published coverage report:
     the per-file HTML names are hashed, so resolve them from the report index
     (same-origin on the deployed site), falling back to the index. */
  if (tb) tb.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest(".cov-file");
    if (!a) return;
    e.preventDefault();
    var file = a.dataset.file;
    fetch("coverage/").then(function (r) { if (!r.ok) throw 0; return r.text(); }).then(function (html) {
      var doc = new DOMParser().parseFromString(html, "text/html");
      var hit = [].slice.call(doc.querySelectorAll("a[href]")).find(function (x) {
        return x.textContent.trim() === file || x.textContent.trim() === file.replace(/\//g, "\\");
      });
      window.open(hit ? "coverage/" + hit.getAttribute("href") : "coverage/", "_blank", "noopener");
    }).catch(function () { window.open("coverage/", "_blank", "noopener"); });
  });

  /* headline ring + count-up */
  var ringNum = document.getElementById("cov-ring-num");
  var ringArc = document.getElementById("cov-ring-arc");
  var CIRC = 2 * Math.PI * 52;

  var did = false;
  function animate() {
    if (did) return; did = true;
    // staggered row entrance + per-row count-ups
    var rows = [].slice.call(document.querySelectorAll("tr.cov-r"));
    rows.forEach(function (r, i) {
      setTimeout(function () {
        r.classList.add("in");
        r.querySelectorAll("[data-n]").forEach(function (cell) {
          var target = +cell.dataset.n, isPct = cell.classList.contains("pct-num");
          if (REDUCE) { cell.textContent = isPct ? target + "%" : target.toLocaleString(); return; }
          var t0 = null;
          function tick(ts) {
            if (t0 == null) t0 = ts;
            var p = Math.min(1, (ts - t0) / 800);
            var v = Math.round(target * (p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2));
            cell.textContent = isPct ? v + "%" : v.toLocaleString();
            if (p < 1) requestAnimationFrame(tick);
          }
          requestAnimationFrame(tick);
          setTimeout(function () { cell.textContent = isPct ? target + "%" : target.toLocaleString(); }, 950);
        });
      }, REDUCE ? 0 : i * 130);
    });
    bars.forEach(function (b, i) { setTimeout(function () { b.style.width = b.dataset.pct + "%"; }, REDUCE ? 0 : i * 65 + 250); });
    if (ringArc) ringArc.style.strokeDashoffset = "0";
    if (ringNum) {
      if (REDUCE) { ringNum.textContent = "100%"; return; }
      var t0 = null;
      function step(ts) { if (t0 == null) t0 = ts; var p = Math.min(1, (ts - t0) / 1100); ringNum.textContent = Math.round(p * 100) + "%"; if (p < 1) requestAnimationFrame(step); }
      requestAnimationFrame(step);
      setTimeout(function () { ringNum.textContent = "100%"; }, 1300); // guarantee final value
    }
  }
  if (ringArc) { ringArc.style.strokeDasharray = CIRC; ringArc.style.strokeDashoffset = REDUCE ? "0" : CIRC; }

  var host = document.getElementById("cov-report");
  if ("IntersectionObserver" in window && host && !REDUCE) {
    var io = new IntersectionObserver(function (e) { if (e[0].isIntersecting) { animate(); io.disconnect(); } }, { threshold: 0.2 });
    io.observe(host);
    setTimeout(animate, 1500); // fallback: guarantee it runs
  } else { animate(); }

  /* stamp date / gate */
  var gd = document.getElementById("cov-date");
  if (gd) gd.textContent = new Date().toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
})();
