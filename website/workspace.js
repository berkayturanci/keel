/* ============================================================
   keel — workspace runtime
   View router (sidebar → animated view switch, hash-linked)
   + the live ship-queue board on Overview.
   ============================================================ */
(function () {
  "use strict";
  var K = window.KEEL;

  /* ---- view router: click scrolls, scroll spies, views reveal ------ */
  var items = [].slice.call(document.querySelectorAll(".sb-item[data-view]"));
  var views = {};
  document.querySelectorAll(".view[data-view]").forEach(function (v) { views[v.dataset.view] = v; });
  var main = document.getElementById("main");

  function setActive(view) {
    items.forEach(function (b) { b.classList.toggle("on", b.dataset.view === view); });
    try { history.replaceState(null, "", "#" + view); } catch (e) {}
    if (window.__keelCenterNav) window.__keelCenterNav();
  }
  function reveal(v) { if (v) { v.classList.remove("pre"); v.classList.add("seen"); } }
  // entrance only if JS runs: hide all but overview, then reveal on scroll/click
  Object.keys(views).forEach(function (k) { if (k !== "overview") views[k].classList.add("pre"); });

  items.forEach(function (b) {
    b.addEventListener("click", function () {
      var v = views[b.dataset.view]; if (!v) return;
      reveal(v); setActive(b.dataset.view);
      v.scrollIntoView({ behavior: window.__keelReduce ? "auto" : "smooth", block: "start" });
      if (b.dataset.view === "ship") { var rp = document.getElementById("replay"); if (rp) setTimeout(function () { rp.click(); }, 200); }
    });
  });

  // reveal on scroll + spy
  if ("IntersectionObserver" in window) {
    var seenObs = new IntersectionObserver(function (ents) {
      ents.forEach(function (en) {
        if (en.isIntersecting) {
          reveal(en.target);
          if (en.target.dataset.view === "ship") { var rp = document.getElementById("replay"); if (rp) rp.click(); }
        }
      });
    }, { root: main, rootMargin: "0px 0px -20% 0px", threshold: 0.12 });
    Object.keys(views).forEach(function (k) { seenObs.observe(views[k]); });

    var spy = new IntersectionObserver(function (ents) {
      ents.forEach(function (en) { if (en.isIntersecting) setActive(en.target.dataset.view); });
    }, { root: main, rootMargin: "-12% 0px -70% 0px" });
    Object.keys(views).forEach(function (k) { spy.observe(views[k]); });
  } else {
    Object.keys(views).forEach(function (k) { views[k].classList.add("seen"); });
  }

  // belt-and-suspenders: reveal any view scrolled into the main viewport
  function revealInView() {
    if (!main) return;
    var mt = main.getBoundingClientRect();
    Object.keys(views).forEach(function (k) {
      var v = views[k]; if (v.classList.contains("seen")) return;
      var rt = v.getBoundingClientRect();
      if (rt.top < mt.top + main.clientHeight * 0.88) reveal(v);
    });
  }
  if (main) {
    var revealTicking = false;
    main.addEventListener("scroll", function () {
      if (revealTicking) return;
      revealTicking = true;
      requestAnimationFrame(function () { revealInView(); revealTicking = false; });
    }, { passive: true });
  }
  setTimeout(revealInView, 500);
  // final safety: never leave a view invisible, even if observers don't fire
  setTimeout(function () { Object.keys(views).forEach(function (k) { reveal(views[k]); }); }, 2600);

  // overview is visible immediately
  if (views.overview) reveal(views.overview);
  // deep link
  var initial = (location.hash || "").replace("#", "");
  if (initial && views[initial]) {
    Object.keys(views).forEach(function (k) { reveal(views[k]); });
    setTimeout(function () { views[initial].scrollIntoView({ block: "start" }); }, 60);
  }
  window.addEventListener("hashchange", function () {
    var v = (location.hash || "").replace("#", "");
    if (views[v]) { views[v].classList.add("seen"); views[v].scrollIntoView({ behavior: "smooth", block: "start" }); }
  });

  /* ---- live ship-queue board (Overview) ---------------------------- */
  function board() {
    if (!K) return;
    var prog = document.getElementById("q-prog");
    var status = document.getElementById("q-status");
    var merged = document.getElementById("q-merged");
    var count = document.getElementById("q-count");
    var issueEl = document.getElementById("q-issue");
    var tierEl = document.getElementById("q-tier");
    if (!prog) return;
    var BB = K.backbone;
    var ISS = [
      ["#128", "auth retry swallows last error", "TIER-3"],
      ["#131", "add --dry-run to keel ship", "TIER-2"],
      ["#126", "merge window flips an hour early on DST", "TIER-3"],
      ["#119", "triage: auto-label stale issues", "TIER-1"],
      ["#133", "flaky test_window on CI", "TIER-2"],
      ["#140", "coverage gap in fixloop branch", "TIER-3"],
    ];
    BB.forEach(function (s) { var d = document.createElement("div"); d.className = "q-seg" + (s.slot ? " slot" : ""); prog.appendChild(d); });
    var segs = prog.children;
    var REDUCE = window.__keelReduce;
    var idx = 0, backlog = 6;

    function setIssue() { var d = ISS[idx % ISS.length]; issueEl.textContent = d[0] + " · " + d[1]; tierEl.textContent = d[2]; }
    setIssue();

    if (REDUCE) {
      for (var i = 0; i < segs.length; i++) segs[i].className = "q-seg" + (BB[i].slot ? " slot" : "") + " done";
      status.innerHTML = 's12 close <span class="g">→ merged</span>';
      ["#127", "#125", "#124"].forEach(function (n, k) {
        var row = document.createElement("div"); row.className = "q-mrow";
        row.innerHTML = '<span class="chk">✓</span><span class="no">' + n + '</span><span class="nm">shipped</span>';
        merged.appendChild(row);
      });
      count.textContent = "3";
      return;
    }

    var timers = [];
    var SPEEDS = [1, 2, 0.5];
    var speedIx = 0, speed = 1, speedBtn = document.getElementById("q-speed");
    if (speedBtn) speedBtn.addEventListener("click", function () {
      speedIx = (speedIx + 1) % SPEEDS.length; speed = SPEEDS[speedIx];
      speedBtn.textContent = speed + "\u00d7";
      run();
    });
    function run() {
      timers.forEach(clearTimeout); timers = [];
      for (var i = 0; i < segs.length; i++) segs[i].className = "q-seg" + (BB[i].slot ? " slot" : "");
      BB.forEach(function (b, i) {
        timers.push(setTimeout(function () {
          for (var j = 0; j < i; j++) segs[j].className = "q-seg" + (BB[j].slot ? " slot" : "") + " done";
          segs[i].className = "q-seg" + (b.slot ? " slot" : "") + " on";
          var tag = b.agent ? ' <span class="a">[agent]</span>' : b.slot ? ' <span class="a">[slot]</span>' : "";
          status.innerHTML = b.id + " " + b.name + tag + (i === BB.length - 1 ? ' <span class="g">→ merged</span>' : "…");
        }, (260 + i * 430) / speed));
      });
      timers.push(setTimeout(function () {
        var d = ISS[idx % ISS.length];
        var row = document.createElement("div"); row.className = "q-mrow";
        row.innerHTML = '<span class="chk">✓</span><span class="no">' + d[0] + '</span><span class="nm">' + d[1] + "</span>";
        merged.insertBefore(row, merged.firstChild);
        while (merged.children.length > 4) merged.removeChild(merged.lastChild);
        backlog = Math.max(0, backlog - 1); count.textContent = backlog;
        idx++; setIssue();
        if (backlog === 0) { timers.push(setTimeout(function () { backlog = 6; count.textContent = 6; merged.innerHTML = ""; run(); }, 1800 / speed)); }
        else { timers.push(setTimeout(run, 1500 / speed)); }
      }, (260 + BB.length * 430 + 1000) / speed));
    }
    run();
  }
  board();
})();
