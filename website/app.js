/* ============================================================
   keel — core site behaviour
   Display controls (theme · motion · type), scroll reveal,
   nav, copy buttons, version stamping.
   Theme/type/motion are pre-set in <head> to avoid a flash;
   this file wires the controls that change them at runtime.
   ============================================================ */
(function () {
  "use strict";
  var root = document.documentElement;
  var store = {
    get: function (k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
  };

  /* ---- Display popover: theme / motion / type ---------------------- */
  function setAttr(name, key, val) {
    root.setAttribute(name, val);
    store.set(key, val);
    // reflect into any matching segmented control
    document.querySelectorAll('[data-seg="' + key + '"] button').forEach(function (b) {
      var isOn = b.dataset.val === val;
      b.classList.toggle("on", isOn);
      b.setAttribute("aria-pressed", isOn ? "true" : "false");
    });
  }
  function wireSeg() {
    document.querySelectorAll(".seg[data-seg]").forEach(function (seg) {
      var key = seg.dataset.seg;
      var attr = key === "theme" ? "data-theme" : key === "motion" ? "data-motion" : "data-type";
      seg.querySelectorAll("button").forEach(function (b) {
        var isOn = b.dataset.val === root.getAttribute(attr);
        b.classList.toggle("on", isOn);
        b.setAttribute("aria-pressed", isOn ? "true" : "false");
        b.addEventListener("click", function () { setAttr(attr, key, b.dataset.val); });
      });
    });
  }
  var dispBtn = document.getElementById("disp-btn");
  var dispPop = document.getElementById("disp-pop");
  if (dispBtn && dispPop) {
    dispBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      dispPop.classList.toggle("open");
    });
    document.addEventListener("click", function (e) {
      if (!dispPop.contains(e.target) && e.target !== dispBtn) dispPop.classList.remove("open");
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") dispPop.classList.remove("open"); });
  }
  wireSeg();

  /* simple light/dark toggle — wires every .theme-toggle (titlebar + mobile FAB) */
  document.querySelectorAll(".theme-toggle").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
      root.setAttribute("data-theme", next); store.set("theme", next);
      try { var tc = JSON.parse(localStorage.getItem("keel-colors-v2") || "null"); if (tc && window.__keelApplyColors) window.__keelApplyColors(tc, false); } catch (e) {}
    });
  });

  /* ---- Scroll reveal ----------------------------------------------- */
  var reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  var reveals = document.querySelectorAll(".reveal");
  if (reduce || !("IntersectionObserver" in window)) {
    reveals.forEach(function (el) { el.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function (ents) {
      ents.forEach(function (en) { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    reveals.forEach(function (el) { io.observe(el); });
  }

  /* ---- Nav: scrolled + hide-on-scroll-down + burger ---------------- */
  var nav = document.getElementById("nav");
  if (nav) {
    var last = 0, navTicking = false;
    addEventListener("scroll", function () {
      if (navTicking) return;
      navTicking = true;
      requestAnimationFrame(function () {
        var y = scrollY || pageYOffset;
        nav.classList.toggle("scrolled", y > 8);
        if (y > 420 && y > last + 4) nav.classList.add("nav-hidden");
        else if (y < last - 4 || y < 120) nav.classList.remove("nav-hidden");
        last = y;
        navTicking = false;
      });
    }, { passive: true });
  }
  var burger = document.getElementById("nav-burger");
  var mobile = document.getElementById("nav-mobile");
  if (burger && mobile) {
    burger.addEventListener("click", function () {
      var open = burger.classList.toggle("open");
      mobile.classList.toggle("open", open);
      burger.setAttribute("aria-expanded", open ? "true" : "false");
    });
    mobile.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function () { burger.classList.remove("open"); mobile.classList.remove("open"); burger.setAttribute("aria-expanded", "false"); });
    });
  }

  /* ---- Active section highlight (index only) ----------------------- */
  var navLinks = [].slice.call(document.querySelectorAll('.nav-link[href^="#"]'));
  if (navLinks.length && "IntersectionObserver" in window) {
    var map = {};
    navLinks.forEach(function (l) { var id = l.getAttribute("href").slice(1); if (id) map[id] = l; });
    var so = new IntersectionObserver(function (ents) {
      ents.forEach(function (en) {
        if (en.isIntersecting) {
          navLinks.forEach(function (l) {
            l.classList.remove("active");
            l.removeAttribute("aria-current");
          });
          if (map[en.target.id]) {
            map[en.target.id].classList.add("active");
            map[en.target.id].setAttribute("aria-current", "page");
          }
        }
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    Object.keys(map).forEach(function (id) { var s = document.getElementById(id); if (s) so.observe(s); });
  }

  /* ---- Copy buttons ------------------------------------------------ */
  function flashCopy(btn) {
    var label = btn.querySelector("span");
    var prev = label ? label.textContent : "";
    btn.classList.add("done");
    if (label) label.textContent = "copied";
    var sr = document.getElementById("sr-live-region");
    if (sr) { sr.textContent = "Copied to clipboard"; setTimeout(function() { sr.textContent = ""; }, 3000); }
    setTimeout(function () { btn.classList.remove("done"); if (label) label.textContent = prev; }, 1400);
  }
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var txt = btn.getAttribute("data-copy");
      if (btn.dataset.copyTarget) { var t = document.querySelector(btn.dataset.copyTarget); if (t) txt = t.textContent; }
      (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject()).then(function () { flashCopy(btn); }, function () {
        var ta = document.createElement("textarea"); ta.value = txt; document.body.appendChild(ta); ta.select();
        try { document.execCommand("copy"); flashCopy(btn); } catch (e) {} document.body.removeChild(ta);
      });
    });
  });

  /* ---- Stamp version / year --------------------------------------- */
  function stampVersion(v) {
    document.querySelectorAll("[data-version]").forEach(function (el) { el.textContent = v; });
  }
  if (window.KEEL) stampVersion(KEEL.meta.version);
  document.querySelectorAll("[data-year]").forEach(function (el) { el.textContent = new Date().getFullYear(); });

  /* ---- Keep the version fresh between deploys ----------------------
     The build-time KEEL.meta.version (stamped above) is the reliable
     baseline. We re-fetch only the **core** release line (the v1.x tags;
     the keel-visual-v* tags are a separate series and are ignored) and
     update if found. No localStorage cache — a stale cache used to revert
     a correct build version, and a keel-visual tag could mask the core one. */
  (function () {
    try { localStorage.removeItem("keel-version"); } catch (e) {}
    function apply(tag) {
      if (!tag || !/^v\d/.test(tag)) return;        // core tags only (v1.6.2 …)
      var v = tag[0] === "v" ? tag : "v" + tag;
      stampVersion(v);
      if (window.KEEL) KEEL.meta.version = v;
    }
    fetch("https://api.github.com/repos/berkayturanci/keel/releases?per_page=30")
      .then(function (r) { if (!r.ok) throw 0; return r.json(); })
      .then(function (list) {
        var rel = (list || []).find(function (x) {
          return x && !x.draft && !x.prerelease && /^v\d/.test(x.tag_name || "");
        });
        if (rel) apply(rel.tag_name); else throw 0;
      })
      .catch(function () {
        fetch("https://pypi.org/pypi/keel-workflow/json")
          .then(function (r) { if (!r.ok) throw 0; return r.json(); })
          .then(function (j) { apply(j.info && j.info.version); })
          .catch(function () {});
      });
  })();

  /* center the active nav item in the strip (mobile horizontal nav) */
  function centerNav() {
    var sb = document.querySelector(".sidebar");
    if (!sb || sb.scrollWidth <= sb.clientWidth + 4) return;
    var on = sb.querySelector(".sb-item.on");
    if (!on) return;
    var sbr = sb.getBoundingClientRect(), br = on.getBoundingClientRect();
    var left = br.left - sbr.left + sb.scrollLeft;
    sb.scrollTo({ left: Math.max(0, left - (sb.clientWidth - br.width) / 2), behavior: reduce ? "auto" : "smooth" });
  }
  window.__keelCenterNav = centerNav;

  /* ---- mobile: move the nav strip into the titlebar ----------------- */
  (function () {
    var sidebar = document.querySelector(".sidebar");
    var titlebar = document.querySelector(".titlebar");
    var appBody = document.querySelector(".app-body");
    if (!sidebar || !titlebar || !appBody) return;
    var tbRight = titlebar.querySelector(".tb-right");
    var mq = matchMedia("(max-width: 760px)");
    function place() {
      if (mq.matches) {
        if (sidebar.parentNode !== titlebar) titlebar.insertBefore(sidebar, tbRight);
        appBody.classList.add("nav-moved");
      } else {
        if (sidebar.parentNode !== appBody) appBody.insertBefore(sidebar, appBody.firstChild);
        appBody.classList.remove("nav-moved");
      }
    }
    if (mq.addEventListener) mq.addEventListener("change", place); else mq.addListener(place);
    place();
    setTimeout(centerNav, 80);
  })();

  /* ---- mobile: hide the titlebar on scroll down, show on scroll up -- */
  (function () {
    var mainEl = document.querySelector(".main");
    var tb = document.querySelector(".titlebar");
    if (!mainEl || !tb) return;
    var mqm = matchMedia("(max-width: 760px)");
    /* Accumulate per-direction travel: mobile browsers fire many small-delta
       scroll events, so a per-event threshold never sees an upward move. */
    var last = 0, acc = 0, tbTicking = false;
    mainEl.addEventListener("scroll", function () {
      if (tbTicking) return;
      tbTicking = true;
      requestAnimationFrame(function () {
        tbTicking = false;
        if (!mqm.matches) { tb.style.marginTop = ""; return; }
        var y = mainEl.scrollTop;
        var dy = y - last;
        last = y;
        if ((dy < 0) !== (acc < 0)) acc = 0;
        acc += dy;
        if (y < 140 || acc < -10) tb.style.marginTop = "0px";
        else if (acc > 24) tb.style.marginTop = -tb.offsetHeight + "px";
      });
    }, { passive: true });
    if (mqm.addEventListener) mqm.addEventListener("change", function () { tb.style.marginTop = ""; });
  })();

  /* ---- shared color theming (set via Tweaks on the main page) ------- */
  window.__keelApplyColors = function (t, save) {
    var r = document.documentElement;
    var light = r.getAttribute("data-theme") === "light";
    function shade(c, pct) { return light ? "color-mix(in srgb, " + c + " " + pct + "%, #07314f)" : c; }
    if (t.primary) {
      r.style.setProperty("--accent", shade(t.primary[0], 80));
      r.style.setProperty("--accent-2", shade(t.primary[1], 50));
      r.style.setProperty("--accent-3", shade(t.primary[2] || t.primary[1], 38));
      r.style.setProperty("--accent-soft", "color-mix(in srgb, " + t.primary[0] + " " + (light ? 11 : 14) + "%, transparent)");
      r.style.setProperty("--accent-line", "color-mix(in srgb, " + t.primary[1] + " 42%, transparent)");
    }
    if (t.merged) {
      r.style.setProperty("--green", shade(t.merged[0], 75));
      r.style.setProperty("--green-2", shade(t.merged[1], 55));
    }
    if (t.bg) {
      var d = document.querySelector(".desk");
      if (d) d.style.background =
        "radial-gradient(62% 80% at 100% 0%, " + t.bg[0] + "33, transparent 60%)," +
        "radial-gradient(62% 80% at 0% 100%, " + t.bg[1] + "24, transparent 60%)," +
        "linear-gradient(135deg, " + t.bg[0] + "1f, var(--bg) 50%, " + t.bg[1] + "1a)";
    }
    if (save !== false) { try { localStorage.setItem("keel-colors-v2", JSON.stringify(t)); } catch (e) {} }
  };
  try {
    var savedColors = JSON.parse(localStorage.getItem("keel-colors-v2") || "null");
    if (savedColors) window.__keelApplyColors(savedColors, false);
  } catch (e) {}

  /* expose helper for reduced-motion checks in scenes */
  window.__keelReduce = reduce;
})();
