# keel website — handoff notes

Static site for [keel](https://github.com/berkayturanci/keel), deployed to GitHub Pages at
<https://keel-ship.dev/>. Drop this folder in as the repo's `website/`
directory (replacing the existing one) — the existing `pages.yml` workflow / `make site`
flow keeps working.

## Pages

| file | what it is |
|---|---|
| `index.html` | Landing page, workspace-style: Overview, What it is, The backbone, How it compares, Workflow commands (16, each with an animated scene + args/flags), CLI, Configuration, Dogfooding, FAQ |
| `docs.html` | Documentation — generated entirely from `content.js` (`KEEL.docs[]`) |
| `coverage.html` | Animated coverage report with a per-module table |
| `silent-revert.html` | Article: a squash merge silently reverted a release |
| `404.html` | Not-found page |

## How content updates work (important)

**Almost all text lives in `content.js`** (`window.KEEL`). The HTML pages are shells;
`home.js` / `docs.js` / `workspace.js` render from this one source.

- **Add/edit a docs article** → add an entry to `KEEL.docs[]` (group, title, slug,
  summary, body, optional `source` link). It automatically appears in the docs sidebar,
  search, and the page. No HTML edits needed.
- **Add/edit a command** → `KEEL.commands[]` (slug, name, group, one, detail, scene)
  plus its flags in `params.js` (`KEEL_ARGS`, generated from the frontmatter of
  `src/keel/adapters/commands/*.md`). The showcase tab, scene, and docs table follow.
  If the command count changes, also update the **three static "16" spots**:
  the Overview stat card and commands kicker in `index.html`, and the sidebar badge in
  `docs.html` + `coverage.html`.
- **CLI table** → `KEEL.cli[]`. Angle brackets are fine — renderers escape them.
- **Version** → fetched automatically from the GitHub releases API (fallback: PyPI),
  cached 6 h in localStorage. `KEEL.meta.version` in `content.js` is only the static
  fallback; bump it occasionally.

## Coverage data

`coverage.html` reads its table numbers from `coverage.js` (`C.modules`) and links each
file row into the published htmlcov report at `coverage/` (built by `make site` / CI;
not in this folder). The coverage badge endpoint is `coverage-badge.json`, also produced
by CI.

## Assets / SEO

- `favicon.svg` (+ `favicon-32.png`, `apple-touch-icon.png`) — the dot-spine logo
- `assets/og-banner.png` — social card (1200×630), referenced as an **absolute URL**
  by all pages' OG/Twitter tags
- `assets/hero-dark.svg` / `assets/hero-light.svg` — README hero banners (1200×300).
  Use in the repo README with:
  ```html
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://keel-ship.dev/assets/hero-dark.svg">
    <img alt="keel — drive every issue to merged" src="https://keel-ship.dev/assets/hero-light.svg">
  </picture>
  ```
- `index.html` carries JSON-LD (`SoftwareApplication`) structured data
- `sitemap.xml`, `robots.txt`, `site.webmanifest` — URLs point at
  `https://keel-ship.dev/`; update if the site moves
- Each page has full meta description / OG / Twitter tags + canonical

## Analytics

All five pages load Cloudflare Web Analytics
(`static.cloudflareinsights.com/beacon.min.js`), which is cookieless and needs no
consent banner. There is no hostname guard, so a fork or a local preview also
reports; filter by hostname in the Cloudflare dashboard rather than trusting the
raw total — the sibling project's site reports into the same bucket too, because
both sites currently share one beacon token.

There is no Google Analytics on this site. An earlier revision of this file
described a GA4 setup with Consent Mode; no page has ever carried a `gtag`
snippet, so that section was fiction and has been removed.

## Theming

Dark/light follows the OS by default; the toggle stores the override in
`localStorage["keel-theme"]` (shared across pages). All colors are CSS custom
properties in `styles.css` — three accents (indigo `--accent`, emerald `--green`,
amber `--brass`) plus neutrals. Change palette there only.

## One production cleanup to do

`index.html` ends with a **"Tweaks" block** (marked by the
`<!-- Tweaks (in-page controls; ...) -->` comment: a `#tweaks-root` div, three
unpkg React/Babel script tags, and two `text/babel` scripts) plus the file
`tweaks-panel.jsx`. This was a design-review tool from the authoring environment —
it never renders for normal visitors but does load React + Babel from a CDN.
**Delete that block and `tweaks-panel.jsx` for production.** Nothing else references
them; removing them changes nothing visually.

## Recent additions (already wired, nothing to do)

- Backbone view: structure + safety-primitive chips + per-command coverage map,
  rendered by `home.js` from `KEEL.backbone` / inline `covRows`. Slot hierarchy:
  grey = passive before/after, amber = primary, red ⊘ = may_block
  (`guard`, `tester`, `test`, `pre-merge`) — keep in sync with
  `src/keel/workflows/model.py` SLOT_DEFINITIONS if slots change.
- Version badge: stale-while-revalidate — cached value shown instantly, GitHub
  releases API re-checked on every load.
- Security view on `index.html` (+ sidebar links on docs/coverage) summarizing
  SECURITY.md and the three audit reports.

## License / attribution

Site content is part of the keel repo (Apache-2.0). Author: Berkay Turancı.
