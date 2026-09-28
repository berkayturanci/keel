/* ============================================================
   keel — coverage report (animated)

   Every figure on this page comes from coverage-summary.json, which
   scripts/coverage_page_data.py writes from the coverage run when the
   site is built (make site, .github/workflows/pages.yml). None is typed
   here or in content.js (#1320): the page used to print hand-kept numbers
   about 9x smaller than the report it links to.

   Without that file (a plain static server over website/), the page keeps
   the enforced gate and the link to the full report, and shows no numbers.
   ============================================================ */
(function () {
  "use strict";
  var REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;
  var SCHEMA = 1;
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function $(id) { return document.getElementById(id); }
  function count(n) { return Math.round(n).toLocaleString("en-US"); }
  // The generator already floors to two decimals, so a figure is shown as given. Flooring it
  // again here dropped some by 0.01 through float error: Math.floor(0.29 * 100) is 28.
  function pct(n) { return n.toFixed(2).replace(/\.?0+$/, "") + "%"; }
  function isCount(n) { return typeof n === "number" && isFinite(n) && n >= 0; }

  /* the one shape this page reads; anything else is treated as "no data" */
  function valid(d) {
    if (!d || d.schema !== SCHEMA || !d.totals || !Array.isArray(d.files) || !d.files.length) return false;
    var t = d.totals;
    if (!["statements", "missing", "branches", "partial", "line", "branch", "total", "files"].every(function (k) { return isCount(t[k]); })) return false;
    return d.files.every(function (r) {
      return Array.isArray(r) && r.length === 7 && typeof r[0] === "string" && r.slice(1).every(isCount);
    });
  }

  var CIRC = 2 * Math.PI * 52;
  var ringNum = $("cov-ring-num"), ringArc = $("cov-ring-arc"), ringCap = $("cov-ring-cap");
  if (ringArc) { ringArc.style.strokeDasharray = CIRC; ringArc.style.strokeDashoffset = REDUCE ? "0" : CIRC; }

  function ring(target) {
    if (ringArc) ringArc.style.strokeDashoffset = String(CIRC * (1 - Math.min(100, target) / 100));
    if (!ringNum) return;
    if (REDUCE) { ringNum.textContent = pct(target); return; }
    var t0 = null;
    function step(ts) { if (t0 == null) t0 = ts; var p = Math.min(1, (ts - t0) / 1100); ringNum.textContent = pct(p * target); if (p < 1) requestAnimationFrame(step); }
    requestAnimationFrame(step);
    setTimeout(function () { ringNum.textContent = pct(target); }, 1300); // guarantee final value
  }

  /* no data: the gate is the only figure the page can stand behind */
  function noData() {
    var nodata = $("cov-nodata"); if (nodata) nodata.hidden = false;
    if (ringCap) ringCap.textContent = "gate";
    ring(100);
  }

  function render(d) {
    var t = d.totals;
    if (ringCap) ringCap.textContent = "measured";
    var state = $("cov-gate-state");
    if (state) state.textContent = t.total >= 100 ? " · passing" : " · below the gate";
    var date = $("cov-date"), dateWrap = $("cov-date-wrap");
    var ymd = /^(\d{4})-(\d{2})-(\d{2})/.exec(d.generated || "");
    if (date && dateWrap && ymd) {
      date.textContent = new Date(+ymd[1], +ymd[2] - 1, +ymd[3]).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
      dateWrap.hidden = false;
    }

    var sum = $("cov-summary");
    if (sum) {
      [
        ["line coverage", pct(t.line), t.line >= 100],
        ["branch coverage", pct(t.branch), t.branch >= 100],
        ["statements", count(t.statements), false],
        ["branches", count(t.branches), false],
      ].forEach(function (c) {
        var card = el("div", "cov-card" + (c[2] ? " ok" : ""));
        card.appendChild(el("div", "cc-v", c[1]));
        card.appendChild(el("div", "cc-k", c[0]));
        sum.appendChild(card);
      });
      sum.hidden = false;
    }
    var scope = $("cov-scope");
    if (scope) scope.textContent = count(t.files) + " files · line + branch";

    var tb = $("cov-tbody");
    if (!tb) return;
    d.files.forEach(function (r) {
      var tr = el("tr", "cov-r");
      var file = el("td");
      var a = el("a", "cov-file");
      a.href = "coverage/"; a.target = "_blank"; a.rel = "noopener";
      a.title = "Open this file's coverage page";
      a.dataset.file = r[0];
      // the table head already says src/keel/; the full path stays in data-file
      a.appendChild(el("code", null, r[0].replace(/^src\/keel\//, "")));
      file.appendChild(a);
      tr.appendChild(file);
      var stm = el("td", "cov-stm", "0"); stm.dataset.n = r[1]; tr.appendChild(stm);
      var miss = el("td", null, "0"); miss.dataset.n = r[2]; tr.appendChild(miss);
      [r[6], r[5]].forEach(function (v) { // branch, then line
        var td = el("td", "cov-pct");
        var num = el("span", "pct-num", "0%"); num.dataset.n = v; td.appendChild(num);
        var mini = el("span", "cov-mini"); var fill = el("span", "cov-mini-fill"); fill.dataset.pct = v;
        mini.appendChild(fill); td.appendChild(mini); tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
    var wrap = $("cov-tbl-wrap"); if (wrap) wrap.hidden = false;

    /* clicking a file opens its own page in the published coverage report:
       the per-file HTML names are hashed, so resolve them from the report index
       (same-origin on the deployed site), falling back to the index. */
    tb.addEventListener("click", function (e) {
      var link = e.target.closest && e.target.closest(".cov-file");
      if (!link) return;
      e.preventDefault();
      var name = link.dataset.file;
      fetch("coverage/").then(function (r) { if (!r.ok) throw 0; return r.text(); }).then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var hit = [].slice.call(doc.querySelectorAll("a[href]")).find(function (x) {
          return x.textContent.trim() === name || x.textContent.trim() === name.replace(/\//g, "\\");
        });
        window.open(hit ? "coverage/" + hit.getAttribute("href") : "coverage/", "_blank", "noopener");
      }).catch(function () { window.open("coverage/", "_blank", "noopener"); });
    });

    var rows = [].slice.call(tb.querySelectorAll("tr.cov-r"));
    var bars = [].slice.call(tb.querySelectorAll(".cov-mini-fill"));
    var did = false;
    function animate() {
      if (did) return; did = true;
      // the stagger is capped so a long file list still lands in about a second
      var gap = REDUCE ? 0 : Math.min(130, 1000 / rows.length);
      rows.forEach(function (row, i) {
        setTimeout(function () {
          row.classList.add("in");
          row.querySelectorAll("[data-n]").forEach(function (cell) {
            var target = +cell.dataset.n, isPct = cell.classList.contains("pct-num");
            var show = isPct ? pct : count;
            if (REDUCE) { cell.textContent = show(target); return; }
            var t0 = null;
            function tick(ts) {
              if (t0 == null) t0 = ts;
              var p = Math.min(1, (ts - t0) / 800);
              cell.textContent = show(target * (p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2));
              if (p < 1) requestAnimationFrame(tick);
            }
            requestAnimationFrame(tick);
            setTimeout(function () { cell.textContent = show(target); }, 950);
          });
        }, i * gap);
      });
      bars.forEach(function (b, i) { setTimeout(function () { b.style.width = b.dataset.pct + "%"; }, REDUCE ? 0 : i * gap / 2 + 250); });
      ring(t.total);
    }
    var host = $("cov-report");
    if ("IntersectionObserver" in window && host && !REDUCE) {
      var io = new IntersectionObserver(function (e) { if (e[0].isIntersecting) { animate(); io.disconnect(); } }, { threshold: 0.2 });
      io.observe(host);
      setTimeout(animate, 1500); // fallback: guarantee it runs
    } else { animate(); }
  }

  fetch("coverage-summary.json", { cache: "no-cache" })
    .then(function (r) { if (!r.ok) throw 0; return r.json(); })
    .then(function (d) { return valid(d) ? d : null; }, function () { return null; })
    .then(function (d) { if (d) render(d); else noData(); });
})();
