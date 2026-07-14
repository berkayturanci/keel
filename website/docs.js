/* ============================================================
   keel — docs renderer
   Builds the sidebar, search, and article sections from
   window.KEEL.docs[]. Add an entry to KEEL.docs → it appears
   here automatically (sidebar link + searchable + section).
   Entries with `render` also embed live data (backbone,
   commands, cli, config) pulled from the same source.
   ============================================================ */
(function () {
  "use strict";
  var K = window.KEEL;
  if (!K) return;
  function el(tag, cls, html) { var n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; }
  function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

  /* ---- embedded data renders -------------------------------------- */
  function embed(kind) {
    if (kind === "backbone") {
      var rows = K.backbone.map(function (s) {
        var hooks = s.slots.map(function (h) { return '<code>' + h + "</code>"; }).join(" ");
        return "<tr><td><code>" + s.id + '</code></td><td><b>' + s.name + "</b>" + (s.agent ? " · agent" : "") + "</td><td>" + s.blurb + "</td><td>" + hooks + "</td></tr>";
      }).join("");
      return '<div class="doc-embed"><table class="doc-tbl"><thead><tr><th>step</th><th>name</th><th>does</th><th>hooks</th></tr></thead><tbody>' + rows + "</tbody></table></div>";
    }
    if (kind === "commands") {
      var order = ["Flagship", "Per-step", "Review & triage", "Daily rhythm", "Audits"], out = "";
      order.forEach(function (g) {
        var items = K.commands.filter(function (c) { return c.group === g; });
        if (!items.length) return;
        out += '<p style="margin:1rem 0 .5rem"><b>' + g + "</b></p><div class='doc-cmd-list'>";
        items.forEach(function (c) {
          var a = (window.KEEL_ARGS || {})[c.slug];
          var flags = a && a.flags && a.flags.length
            ? '<span class="doc-cmd-flags">' + a.flags.map(function (f) { return "<code>" + esc(f) + "</code>"; }).join(" ") + ' <a href="#parameter-reference">all parameter details →</a></span>'
            : '<span class="doc-cmd-flags"><a href="#parameter-reference">all parameter details →</a></span>';
          out += '<div class="doc-cmd-item"><code>' + c.name + "</code><span>" + c.detail + " " + flags + "</span></div>";
        });
        out += "</div>";
      });
      return '<div class="doc-embed">' + out + "</div>";
    }
    if (kind === "cli") {
      var list = K.cli.map(function (r) { return '<div class="doc-cmd-item"><code>' + esc(r[0]) + "</code><span>" + esc(r[1]) + "</span></div>"; }).join("");
      return '<div class="doc-embed doc-cmd-list">' + list + "</div>";
    }
    if (kind === "config") {
      var rows2 = K.config.map(function (r) { return "<tr><td><code>" + r[0] + "</code></td><td>" + r[1] + "</td></tr>"; }).join("");
      return '<div class="doc-embed"><table class="doc-tbl"><thead><tr><th>field</th><th>what it sets</th></tr></thead><tbody>' + rows2 + "</tbody></table></div>";
    }
    return "";
  }

  /* ---- group docs in declared order ------------------------------- */
  var groups = [], byGroup = {};
  K.docs.forEach(function (d) {
    if (!byGroup[d.group]) { byGroup[d.group] = []; groups.push(d.group); }
    byGroup[d.group].push(d);
  });

  /* ---- sidebar ---------------------------------------------------- */
  var nav = document.getElementById("docs-nav");
  groups.forEach(function (g) {
    var sec = el("div", "docs-nav-group");
    sec.appendChild(el("h4", null, g));
    byGroup[g].forEach(function (d) {
      var a = el("a", null, d.title);
      a.href = "#" + d.slug; a.dataset.slug = d.slug; a.dataset.text = (d.title + " " + d.summary + " " + d.group).toLowerCase();
      a.addEventListener("click", function () {
        document.querySelectorAll("#docs-nav a").forEach(function (x) {
          x.classList.remove("active");
          x.removeAttribute("aria-current");
        });
        a.classList.add("active");
        a.setAttribute("aria-current", "page");
      });
      sec.appendChild(a);
    });
    nav.appendChild(sec);
  });

  /* ---- articles (rendered in the same grouped order as the sidebar) */
  var main = document.getElementById("docs-articles");
  groups.forEach(function (g) {
    byGroup[g].forEach(function (d) {
      var art = el("article", "doc-art"); art.id = d.slug;
    art.dataset.text = (d.title + " " + d.summary + " " + d.group).toLowerCase();
    var src = d.source ? '<a class="doc-source" href="' + d.source + '" target="_blank" rel="noopener"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6M10 14L21 3"/></svg>source</a>' : "";
    art.innerHTML =
      '<div class="doc-head"><span class="doc-head-t"><h2 id="h-' + d.slug + '">' + d.title + '</h2><button class="doc-share" type="button" data-slug="' + d.slug + '" title="Copy link to this section" aria-label="Copy link to ' + d.title + '"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/></svg></button></span></div>' +
      '<p class="doc-summary">' + d.summary + "</p>" +
      '<div class="doc-body">' + d.body + (d.render ? embed(d.render) : "") + src + "</div>";
    main.appendChild(art);
    });
  });

  /* share buttons: copy a deep link to the section */
  document.querySelectorAll(".doc-share").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var slug = btn.dataset.slug;
      var url = location.origin + location.pathname + "#" + slug;
      try { history.replaceState(null, "", "#" + slug); } catch (e) {}
      (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject()).then(ok, function () {
        var ta = document.createElement("textarea"); ta.value = url; document.body.appendChild(ta); ta.select();
        try { document.execCommand("copy"); ok(); } catch (e) {} document.body.removeChild(ta);
      });
      function ok() {
        btn.classList.add("done");
        var sr = document.getElementById("sr-live-region");
        if (sr) { sr.textContent = "Copied to clipboard"; setTimeout(function() { sr.textContent = ""; }, 3000); }
        setTimeout(function () { btn.classList.remove("done"); }, 1400);
      }
    });
  });

  /* ---- search ----------------------------------------------------- */
  var search = document.getElementById("docs-search-input");
  var noRes = document.getElementById("docs-no-results");
  if (search) {
    search.addEventListener("input", function () {
      var q = search.value.trim().toLowerCase();
      var any = false;
      document.querySelectorAll(".doc-art").forEach(function (a) {
        var hit = !q || a.dataset.text.indexOf(q) > -1;
        a.style.display = hit ? "" : "none"; if (hit) any = true;
      });
      document.querySelectorAll("#docs-nav a").forEach(function (a) {
        a.classList.toggle("hidden", !!q && a.dataset.text.indexOf(q) === -1);
      });
      document.querySelectorAll(".docs-nav-group").forEach(function (g) {
        var visible = g.querySelectorAll("a:not(.hidden)").length;
        g.style.display = visible ? "" : "none";
      });
      if (noRes) noRes.classList.toggle("show", !any);
    });
  }

  /* ---- scroll-spy ------------------------------------------------- */
  if ("IntersectionObserver" in window) {
    var links = {};
    document.querySelectorAll("#docs-nav a").forEach(function (a) { links[a.dataset.slug] = a; });
    var spy = new IntersectionObserver(function (ents) {
      ents.forEach(function (en) {
        if (en.isIntersecting) {
          Object.keys(links).forEach(function (k) {
            links[k].classList.remove("active");
            links[k].removeAttribute("aria-current");
          });
          if (links[en.target.id]) {
            links[en.target.id].classList.add("active");
            links[en.target.id].setAttribute("aria-current", "page");
          }
        }
      });
    }, { rootMargin: "-12% 0px -75% 0px" });
    document.querySelectorAll(".doc-art").forEach(function (a) { spy.observe(a); });
  }
})();
