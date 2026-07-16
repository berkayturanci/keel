// Shared harness for the keel-visual template JS tests (node --test, zero deps).
//
// Two techniques, no template changes:
//  1. boot(): run a template's inline <script> IIFE inside a `vm` context against a
//     tiny stub DOM (document/localStorage/fetch/timers), then assert on the stub
//     tree — payload parsing, filtering, theme toggling, drawer wiring, etc.
//  2. extractFunction()/extractConst(): slice a named pure helper out of the script
//     source and evaluate just that declaration, for direct unit tests.
//
// This file deliberately does not match node's test-file pattern (*.test.mjs), so
// `node --test tests/js` treats it as a plain module.

import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const TEMPLATE_DIR = path.resolve(HERE, '..', '..', 'src', 'keel_visual', 'templates');

export function loadTemplate(name) {
  return fs.readFileSync(path.join(TEMPLATE_DIR, name), 'utf8');
}

// All inline (non-src) <script> bodies, in document order.
export function inlineScripts(html) {
  const out = [];
  const re = /<script(\s[^>]*)?>([\s\S]*?)<\/script>/g;
  let m;
  while ((m = re.exec(html))) {
    if (/\bsrc\s*=/.test(m[1] || '')) continue;
    out.push(m[2]);
  }
  return out;
}

// The main application script is always the longest inline one (the payload
// scripts are one-liners like `window.KEEL_RUN = __KEEL_RUN__;`).
export function mainScript(html) {
  const scripts = inlineScripts(html);
  if (!scripts.length) throw new Error('no inline scripts found');
  return scripts.reduce((a, b) => (b.length > a.length ? b : a));
}

// ---- source slicing (level "a" extraction of pure helpers) ----

// `function NAME(...){...}` with balanced braces.
export function extractFunction(src, name) {
  const start = src.indexOf('function ' + name + '(');
  if (start < 0) throw new Error('function ' + name + ' not found');
  const open = src.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error('unbalanced braces extracting ' + name);
}

// `const NAME = <expression>;` — the helpers we slice contain no `;` internally.
export function extractConst(src, name) {
  const m = src.match(new RegExp('const\\s+' + name + '\\s*='));
  if (!m) throw new Error('const ' + name + ' not found');
  const start = m.index;
  const end = src.indexOf(';', start);
  if (end < 0) throw new Error('no terminating ; extracting ' + name);
  return src.slice(start, end + 1);
}

// ---- stub DOM ----

class StubElement {
  constructor(doc, tag) {
    this._doc = doc;
    this.tagName = String(tag || 'div').toUpperCase();
    this._id = '';
    this._classes = new Set();
    this.childNodes = [];
    this.style = {};
    this.dataset = {};
    this.attributes = new Map();
    this.listeners = {};
    this.innerHTML = '';
    this.value = '';
    this.title = '';
    this.hidden = false;
    this.tabIndex = -1;
    this.offsetLeft = 0;
    this.offsetTop = 0;
    this.offsetWidth = 40;
    this.offsetHeight = 20;
    this.clientWidth = 320;
    this.clientHeight = 240;
    this._qsCache = new Map();
    const cls = this._classes;
    this.classList = {
      add: (...cs) => cs.forEach((c) => cls.add(c)),
      remove: (...cs) => cs.forEach((c) => cls.delete(c)),
      contains: (c) => cls.has(c),
      toggle: (c, force) => {
        const on = force === undefined ? !cls.has(c) : !!force;
        if (on) cls.add(c);
        else cls.delete(c);
        return on;
      },
    };
  }
  get id() {
    return this._id;
  }
  set id(v) {
    this._id = String(v);
    if (this._doc) this._doc._registry.set(this._id, this);
  }
  get className() {
    return [...this._classes].join(' ');
  }
  set className(v) {
    this._classes.clear();
    String(v)
      .split(/\s+/)
      .filter(Boolean)
      .forEach((c) => this._classes.add(c));
  }
  get children() {
    return this.childNodes.filter((n) => typeof n !== 'string');
  }
  get textContent() {
    return this.childNodes.map((n) => (typeof n === 'string' ? n : n.textContent)).join('');
  }
  set textContent(v) {
    const s = String(v);
    this.childNodes = s === '' ? [] : [s];
  }
  appendChild(n) {
    this.childNodes.push(n);
    return n;
  }
  append(...ns) {
    ns.forEach((n) => this.childNodes.push(n));
  }
  setAttribute(k, v) {
    this.attributes.set(k, String(v));
    if (k === 'id') this.id = v;
  }
  getAttribute(k) {
    return this.attributes.has(k) ? this.attributes.get(k) : null;
  }
  removeAttribute(k) {
    this.attributes.delete(k);
  }
  addEventListener(type, fn) {
    (this.listeners[type] ||= []).push(fn);
  }
  removeEventListener() {}
  setPointerCapture() {}
  closest() {
    return null;
  }
  getBoundingClientRect() {
    return {
      left: this.offsetLeft,
      top: this.offsetTop,
      width: this.offsetWidth,
      height: this.offsetHeight,
      right: this.offsetLeft + this.offsetWidth,
      bottom: this.offsetTop + this.offsetHeight,
    };
  }
  querySelectorAll(sel) {
    return queryAll(this.children, sel);
  }
  // Strict match first; otherwise a cached synthetic node so template code that
  // pokes at HTML-only structure (e.g. runviz's `n.querySelector('.dot')`) keeps
  // running. Synthetic nodes are flagged for tests via `.__synthetic`.
  querySelector(sel) {
    const hit = queryAll(this.children, sel)[0];
    if (hit) return hit;
    if (!this._qsCache.has(sel)) {
      const s = new StubElement(this._doc, 'div');
      s.__synthetic = true;
      this._qsCache.set(sel, s);
    }
    return this._qsCache.get(sel);
  }
}

function* allNodes(el) {
  yield el;
  for (const c of el.childNodes) if (typeof c !== 'string') yield* allNodes(c);
}

function matchesTok(el, tok) {
  if (tok.startsWith('#')) return el.id === tok.slice(1);
  if (tok.startsWith('.')) return el.classList.contains(tok.slice(1));
  return el.tagName === tok.toUpperCase();
}

// Descendant-combinator subset of CSS ('.cls', 'tag', '#id', space-separated).
function queryAll(roots, sel) {
  const tokens = sel.trim().split(/\s+/);
  let current = [];
  const seen = new Set();
  for (const r of roots) {
    for (const n of allNodes(r)) {
      if (!seen.has(n)) {
        seen.add(n);
        if (matchesTok(n, tokens[0])) current.push(n);
      }
    }
  }
  for (let i = 1; i < tokens.length; i++) {
    const next = [];
    const s2 = new Set();
    for (const base of current) {
      for (const n of allNodes(base)) {
        if (n !== base && !s2.has(n) && matchesTok(n, tokens[i])) {
          s2.add(n);
          next.push(n);
        }
      }
    }
    current = next;
  }
  return current;
}

class StubDocument {
  constructor(html) {
    this._registry = new Map();
    this._qsCache = new Map();
    this.documentElement = new StubElement(this, 'html');
    this.body = new StubElement(this, 'body');
    this.head = new StubElement(this, 'head');
    // Seed every static id="..." from the template HTML so getElementById works
    // exactly like the browser: known ids resolve, unknown ids return null.
    for (const m of html.matchAll(/\bid="([^"]+)"/g)) {
      if (!this._registry.has(m[1])) {
        const el = new StubElement(this, 'div');
        el._id = m[1];
        this._registry.set(m[1], el);
      }
    }
  }
  createElement(tag) {
    return new StubElement(this, tag);
  }
  getElementById(id) {
    return this._registry.get(id) || null;
  }
  _roots() {
    return [this.documentElement, this.body, this.head, ...this._registry.values()];
  }
  querySelectorAll(sel) {
    return queryAll(this._roots(), sel);
  }
  querySelector(sel) {
    const hit = queryAll(this._roots(), sel)[0];
    if (hit) return hit;
    if (!this._qsCache.has(sel)) {
      const s = new StubElement(this, 'div');
      s.__synthetic = true;
      this._qsCache.set(sel, s);
    }
    return this._qsCache.get(sel);
  }
}

// ---- boot harness ----

// Runs the template's main inline script in a vm context. The payload <script>
// (`window.KEEL_RUN = __KEEL_RUN__;` etc.) is replaced by setting the payload
// variable directly, so templates are exercised exactly as shipped.
export function boot(templateName, opts = {}) {
  const { payloadVar, payload, search = '', localStorageData = {}, fetchJson = null } = opts;
  const html = loadTemplate(templateName);
  const doc = new StubDocument(html);
  const store = new Map(Object.entries(localStorageData));
  const localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
  const timers = [];
  const intervals = [];
  let timerId = 1;
  const win = {
    innerWidth: 1400,
    innerHeight: 900,
    devicePixelRatio: 1,
    _listeners: {},
    addEventListener(type, fn) {
      (this._listeners[type] ||= []).push(fn);
    },
    removeEventListener() {},
    // no matchMedia / no THREE: every template use of both is guarded.
  };
  if (payloadVar !== undefined) win[payloadVar] = payload;

  const harness = {
    doc,
    win,
    store,
    timers,
    intervals,
    fetchJson,
    fetchCalls: [],
    byId: (id) => doc.getElementById(id),
    qs: (sel) => doc.querySelector(sel),
    fire(el, type, event) {
      (el.listeners[type] || []).forEach((fn) => fn(event || {}));
    },
    winFire(type, event) {
      (win._listeners[type] || []).forEach((fn) => fn(event || {}));
    },
    // A fake click event whose target.closest() yields a button-like object.
    btnEvent(dataset) {
      const btn = { dataset, classList: { toggle() {}, contains: () => false } };
      return { target: { closest: () => btn } };
    },
    runTimer(i = 0) {
      const t = timers[i];
      if (t && !t.cleared) t.fn();
    },
    async flush() {
      for (let i = 0; i < 20; i++) await Promise.resolve();
      await new Promise((r) => setTimeout(r, 0));
    },
  };

  const location = {
    search,
    reloaded: false,
    reload() {
      this.reloaded = true;
    },
  };
  const sandbox = {
    window: win,
    document: doc,
    localStorage,
    location,
    URLSearchParams,
    requestAnimationFrame: () => 0,
    cancelAnimationFrame: () => {},
    setTimeout: (fn, ms) => {
      const id = timerId++;
      timers.push({ fn, ms, id, cleared: false });
      return id;
    },
    clearTimeout: (id) => {
      timers.forEach((t) => {
        if (t.id === id) t.cleared = true;
      });
    },
    setInterval: (fn, ms) => {
      intervals.push({ fn, ms });
      return timerId++;
    },
    clearInterval: () => {},
    getComputedStyle: () => ({ getPropertyValue: (k) => k }),
    fetch: (url, fetchOpts) => {
      harness.fetchCalls.push({ url, opts: fetchOpts });
      return Promise.resolve().then(() => {
        if (typeof harness.fetchJson !== 'function') throw new Error('fetch not stubbed');
        const v = harness.fetchJson();
        return { json: async () => v };
      });
    },
    console,
  };
  harness.location = location;
  vm.createContext(sandbox);
  vm.runInContext(mainScript(html), sandbox, { filename: templateName + ':main' });
  return harness;
}

// ---- element tree helpers for assertions ----

export function findAll(root, cls) {
  const out = [];
  for (const n of allNodes(root)) if (n !== root && n.classList.contains(cls)) out.push(n);
  return out;
}

export function findOne(root, cls) {
  return findAll(root, cls)[0] || null;
}

// ---- payload fixtures (shape mirrors keel_visual.runstate / dash payloads) ----

export const SHIP_NAMES = {
  s0: 'init',
  s1: 'select',
  s2: 'branch',
  s3: 'guard',
  s4: 'implement',
  s5: 'classify',
  s6: 'ci',
  s7: 'review',
  s8: 'test',
  s9: 'fixloop',
  s10: 'merge',
  s11: 'capture',
  s12: 'close',
};

export function makeSteps() {
  return Object.entries(SHIP_NAMES).map(([id, name]) => ({
    id,
    name,
    kind: id === 's10' ? 'merge' : id === 's6' || id === 's8' ? 'gate' : id === 's9' ? 'loop' : 'normal',
    exercised: true,
  }));
}

export function makeRun(over = {}) {
  return {
    project: 'keel',
    label: '#101',
    command: 'ship',
    title: 'Fix the thing',
    branch: 'feat/x',
    base: 'main',
    author: 'claude',
    status: 'running',
    active_index: 4,
    active_id: 's4',
    active_name: 'implement',
    merged: false,
    done: false,
    steps: makeSteps(),
    jury: { active: false },
    ...over,
  };
}
