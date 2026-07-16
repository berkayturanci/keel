// Level (b) floor: structural invariants of the three templates — every inline
// script parses, the payload placeholders and DOM hooks the Python side depends on
// exist, and the recently-added drawer field guards are present.
import test from 'node:test';
import assert from 'node:assert/strict';

import { loadTemplate, inlineScripts, mainScript, extractFunction } from './_dom.mjs';

const TEMPLATES = ['runviz.html', 'board.html', 'dashboard.html'];
const html = Object.fromEntries(TEMPLATES.map((t) => [t, loadTemplate(t)]));

test('every inline script in every template is syntactically valid JS', () => {
  for (const t of TEMPLATES) {
    const scripts = inlineScripts(html[t]);
    assert.ok(scripts.length >= 1, t + ': no inline scripts');
    scripts.forEach((src, i) => {
      // The payload scripts reference bare __KEEL_*__ identifiers — valid syntax,
      // substituted by render.py/serve.py before the browser ever runs them.
      assert.doesNotThrow(() => new Function(src), t + ' script #' + i + ' does not parse');
    });
  }
});

test('payload substitution seams are intact', () => {
  assert.match(html['runviz.html'], /window\.KEEL_RUN\s*=\s*__KEEL_RUN__/);
  assert.match(html['board.html'], /window\.KEEL_BOARD\s*=\s*__KEEL_BOARD__/);
  assert.ok(html['runviz.html'].includes('__TITLE__'), 'runviz title placeholder');
  assert.ok(html['board.html'].includes('__TITLE__'), 'board title placeholder');
  // the dashboard polls the served JSON endpoint instead of a baked payload
  assert.match(mainScript(html['dashboard.html']), /fetch\('board\.json'/);
});

test('required element ids exist in each template', () => {
  const need = {
    'runviz.html': [
      'play', 'playlbl', 'scrub', 'seg', 'meta', 'nodes', 'fill', 'spark', 'sname', 'sdesc',
      'badge', 'pill', 'gatewrap', 'gatebar', 'gatemsg', 'regwrap', 'regbar', 'regpct', 'regmsg',
      'folder', 'jurywrap', 'jurors', 'jurymode', 'jverdict', 'jverdicttxt', 'mergewrap',
      'view2d', 'view3d', 'scene', 'c3', 'seg3d', 'hud3', 'd3step', 'd3desc', 'd3jury',
    ],
    'board.html': ['seg3', 'segf', 'tgl', 'count', 'grid', 'view2d', 'view3d', 'scene3', 'c3', 'tip'],
    'dashboard.html': ['grid', 'segf', 'tgl', 'count', 'q', 'scrim', 'drawer', 'tip', 'livetxt'],
  };
  for (const [t, ids] of Object.entries(need)) {
    for (const id of ids) {
      assert.ok(html[t].includes('id="' + id + '"'), t + ' is missing id="' + id + '"');
    }
  }
});

test('dashboard drawer keeps its defensive field guards', () => {
  const src = mainScript(html['dashboard.html']);
  assert.match(src, /typeof\s+run\.tier\s*===\s*'number'/, 'tier guard');
  assert.match(src, /typeof\s+run\.window_open\s*===\s*'boolean'/, 'window_open guard');
  assert.match(src, /run\.bypassed_window\s*===\s*true/, 'bypassed_window guard');
  assert.match(src, /typeof\s+run\.file_count\s*===\s*'number'/, 'file_count guard');
  assert.match(src, /Array\.isArray\(run\.reviewers\)/, 'reviewers guard');
  assert.match(src, /Array\.isArray\(run\.gates\)/, 'gates guard');
  assert.match(src, /Array\.isArray\(run\.steps\)/, 'steps guard');
});

test('payload consumption is type-guarded in every template', () => {
  assert.match(mainScript(html['board.html']), /Array\.isArray\(window\.KEEL_BOARD\)/);
  assert.match(mainScript(html['runviz.html']), /window\.KEEL_RUN\s*\|\|/);
  assert.match(mainScript(html['dashboard.html']), /if\(!Array\.isArray\(BOARD\)\)\s*BOARD=\[\]/);
});

test('ready flags for the Python-side smoke hooks are still set', () => {
  assert.ok(mainScript(html['runviz.html']).includes('window.__keelReady'), 'runviz ready flag');
  assert.ok(mainScript(html['board.html']).includes('window.__keelBoardReady'), 'board ready flag');
  assert.ok(mainScript(html['dashboard.html']).includes('window.__keelDashReady'), 'dashboard ready flag');
});

test('theme + 3D-style persistence keys are stable and localStorage is try-guarded', () => {
  for (const t of ['board.html', 'dashboard.html']) {
    const src = mainScript(html[t]);
    assert.ok(src.includes("'keel-theme'"), t + ' theme key');
    assert.match(src, /try\{[^}]*localStorage/, t + ' localStorage access is try-guarded');
  }
  assert.ok(mainScript(html['dashboard.html']).includes("'keel-3dstyle'"), '3D style key');
});

test('3D style pickers list the expected styles', () => {
  const dash = mainScript(html['dashboard.html']);
  assert.match(dash, /D3_STYLES=\['curve','helix','ring','line','plexus','aurora','comet'\]/);
  const rv = mainScript(html['runviz.html']);
  assert.match(rv, /STYLE_ORDER=\['plexus','comet','aurora','combined','line'\]/);
});

test('stepTone is byte-identical between board and dashboard (anti-drift)', () => {
  const norm = (s) => s.replace(/\s+/g, ' ').trim();
  assert.equal(
    norm(extractFunction(mainScript(html['board.html']), 'stepTone')),
    norm(extractFunction(mainScript(html['dashboard.html']), 'stepTone'))
  );
});

test('three.js CDN loads stay integrity-pinned', () => {
  for (const t of ['runviz.html', 'board.html']) {
    const m = html[t].match(/<script src="https:\/\/cdnjs[^>]*>/);
    assert.ok(m, t + ': three.js script tag');
    assert.match(m[0], /integrity="sha512-/, t + ': integrity attribute');
    assert.match(m[0], /crossorigin="anonymous"/, t + ': crossorigin attribute');
  }
  // the dashboard lazy-loads three.js with the same pinning
  const dash = mainScript(html['dashboard.html']);
  assert.match(dash, /s\.integrity='sha512-/, 'dashboard lazy loader integrity');
});
