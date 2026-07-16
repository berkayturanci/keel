// Behavioral tests for dashboard.html: the real inline script runs in the vm
// harness with a stubbed fetch('board.json') — live polling, search + all/active
// filtering, drawer wiring (including the defensive field guards), the drawer's
// 3D style picker state, and theme toggling.
import test from 'node:test';
import assert from 'node:assert/strict';

import { boot, findAll, findOne, makeRun } from './_dom.mjs';

async function bootDash(runs, opts = {}) {
  const fetchJson = typeof runs === 'function' ? runs : () => runs;
  const h = boot('dashboard.html', { fetchJson, ...opts });
  await h.flush();
  return h;
}

function metaRows(h) {
  const table = findAll(h.byId('dmeta'), 'meta')[0];
  return Object.fromEntries(
    table.children.map((tr) => [tr.children[0].textContent, tr.children[1].textContent])
  );
}

test('dashboard: polls board.json and renders cards, count and the live badge', async () => {
  const h = await bootDash([
    makeRun(),
    makeRun({ project: 'other', label: '#7', merged: true, status: 'merged' }),
  ]);
  assert.equal(h.win.__keelDashReady, true);
  assert.equal(h.fetchCalls[0].url, 'board.json');
  assert.equal(h.fetchCalls[0].opts.cache, 'no-store');
  assert.equal(h.byId('livetxt').textContent, 'live');
  const cards = findAll(h.byId('grid'), 'card');
  assert.equal(cards.length, 2);
  assert.ok(cards[1].classList.contains('done'), 'finished run ordered last and faded');
  assert.equal(h.byId('count').textContent, '2 runs across 2 projects · 1 done');
  assert.equal(findOne(cards[0], 'step').textContent, 's4 · implement', 'idn label in the card foot');
});

test('dashboard: non-array payload and fetch failure degrade gracefully', async () => {
  const h1 = await bootDash({ not: 'an array' });
  assert.equal(findOne(h1.byId('grid'), 'empty').textContent, 'no active runs found');
  assert.equal(h1.byId('livetxt').textContent, 'live');
  const h2 = await bootDash(() => {
    throw new Error('server gone');
  });
  assert.equal(h2.byId('livetxt').textContent, 'reconnecting…');
});

test('dashboard: free-text search filters cards and reports empty matches', async () => {
  const h = await bootDash([makeRun(), makeRun({ project: 'other', label: '#7' })]);
  const q = h.byId('q');
  q.value = 'OTHER';
  h.fire(q, 'input');
  let cards = findAll(h.byId('grid'), 'card');
  assert.equal(cards.length, 1);
  assert.equal(findOne(cards[0], 'proj').textContent, 'other');
  q.value = 'zzz';
  h.fire(q, 'input');
  assert.equal(findOne(h.byId('grid'), 'empty').textContent, 'no runs match “zzz”');
  q.value = '';
  h.fire(q, 'input');
  assert.equal(findAll(h.byId('grid'), 'card').length, 2);
});

test('dashboard: all/active filter toggles the body class and the count', async () => {
  const h = await bootDash([makeRun(), makeRun({ project: 'p2', merged: true })]);
  h.fire(h.byId('segf'), 'click', h.btnEvent({ f: 'active' }));
  assert.ok(h.doc.body.classList.contains('active-only'));
  assert.equal(h.byId('count').textContent, '1 run across 1 project');
  h.fire(h.byId('segf'), 'click', h.btnEvent({ f: 'all' }));
  assert.ok(!h.doc.body.classList.contains('active-only'));
});

test('dashboard: clicking a card opens the drawer with flow rows and meta', async () => {
  const h = await bootDash([makeRun()]);
  findAll(h.byId('grid'), 'card')[0].onclick();
  const drawer = h.byId('drawer');
  assert.ok(drawer.classList.contains('show'));
  assert.ok(h.byId('scrim').classList.contains('show'));
  assert.equal(findOne(drawer, 'dtitle').textContent, 'keel #101');
  const rows = findAll(h.byId('d2d'), 'frow');
  assert.equal(rows.length, 13);
  assert.ok(rows[4].classList.contains('cur'), 'active step highlighted');
  assert.ok(rows[0].classList.contains('don'));
  assert.equal(findOne(rows[4], 'fid').textContent, 's4');
  assert.equal(findOne(rows[4], 'fnm').textContent, 'implement');
  const meta = metaRows(h);
  assert.equal(meta.command, 'ship');
  assert.equal(meta.phase, 's4 · implement  (5/13)');
  assert.equal(meta.status, 'running');
});

test('dashboard drawer: ledger fields render when present and typed correctly', async () => {
  const h = await bootDash([
    makeRun({
      tier: 3,
      window_open: false,
      bypassed_window: true,
      reviewers: ['gpt', 'gemini'],
      tester: 'codex',
      host_agent: 'claude-code',
      file_count: 7,
      merge_reason: 'window open',
      jury: { active: true, mode: 'gating' },
      gates: [
        { name: 'ruff', ok: true },
        { name: 'tests', ok: false, error: 'boom', finding_count: 2 },
        { name: 'jury', skipped: true },
      ],
    }),
  ]);
  findAll(h.byId('grid'), 'card')[0].onclick();
  const chips = findAll(h.byId('dmeta'), 'chip').map((c) => c.textContent);
  assert.deepEqual(chips, ['tier 3', 'window closed', 'window bypassed']);
  const meta = metaRows(h);
  assert.equal(meta.jury, 'gating');
  assert.equal(meta.merge, 'window open');
  assert.equal(meta.files, '7');
  assert.equal(meta.agents, 'impl claude · review gpt, gemini · test codex · host claude-code');
  assert.equal(meta['gate · ruff'], '✓ ok');
  assert.equal(meta['gate · tests'], '✗ failed · 2 findings · boom');
  assert.equal(meta['gate · jury'], '– skipped');
});

test('dashboard drawer: field guards — absent or mistyped fields are skipped, no crash', async () => {
  const h = await bootDash([
    {
      project: 'p',
      label: '#1',
      command: 'ship',
      active_index: 0,
      steps: 'not-an-array',
      tier: '3',
      window_open: 'yes',
      bypassed_window: 'yes',
      file_count: '7',
      reviewers: 'not-an-array',
      gates: 'not-an-array',
    },
  ]);
  findAll(h.byId('grid'), 'card')[0].onclick();
  assert.equal(findAll(h.byId('d2d'), 'frow').length, 0, 'non-array steps → empty flow');
  assert.equal(findAll(h.byId('dmeta'), 'chip').length, 0, 'mistyped chip fields skipped');
  const meta = metaRows(h);
  assert.deepEqual(Object.keys(meta).sort(), ['command', 'phase', 'status']);
  assert.equal(meta.phase, '?  (1/1)');
});

test('dashboard drawer: keyboard — Enter opens, Escape closes', async () => {
  const h = await bootDash([makeRun()]);
  const card = findAll(h.byId('grid'), 'card')[0];
  let prevented = false;
  card.onkeydown({ key: 'Enter', preventDefault: () => (prevented = true) });
  assert.ok(prevented);
  assert.ok(h.byId('drawer').classList.contains('show'));
  h.winFire('keydown', { key: 'Escape' });
  assert.ok(!h.byId('drawer').classList.contains('show'));
  assert.ok(!h.byId('scrim').classList.contains('show'));
});

test('dashboard drawer: 3D mode toggle + offline three.js fallback message', async () => {
  const h = await bootDash([makeRun()]);
  findAll(h.byId('grid'), 'card')[0].onclick();
  const drawer = h.byId('drawer');
  const seg = findOne(drawer, 'seg3');
  h.fire(seg, 'click', h.btnEvent({ m: '3d' }));
  assert.ok(h.byId('d2d').classList.contains('hide'));
  assert.ok(h.byId('d3d').classList.contains('show'));
  const segButtons = seg.children;
  assert.ok(segButtons.find((b) => b.dataset.m === '3d').classList.contains('on'));
  assert.ok(!segButtons.find((b) => b.dataset.m === '2d').classList.contains('on'));
  // ensureThree injected a pinned loader script; simulate the CDN being unreachable
  const loader = h.doc.head.childNodes[h.doc.head.childNodes.length - 1];
  assert.match(loader.src, /three\.min\.js/);
  loader.onerror();
  await h.flush();
  assert.equal(findOne(drawer, 'dhud').textContent, '3D unavailable (offline?)');
  // back to 2D
  h.fire(seg, 'click', h.btnEvent({ m: '2d' }));
  assert.ok(!h.byId('d2d').classList.contains('hide'));
  assert.ok(!h.byId('d3d').classList.contains('show'));
});

test('dashboard drawer: 3D style picker state — default, persisted, invalid, click', async () => {
  const h1 = await bootDash([makeRun()]);
  findAll(h1.byId('grid'), 'card')[0].onclick();
  const bar1 = findOne(h1.byId('drawer'), 'seg3dd');
  assert.equal(bar1.children.length, 7, 'curve/helix/ring/line/plexus/aurora/comet');
  assert.ok(bar1.children.find((b) => b.dataset.s === 'curve').classList.contains('on'));
  // clicking a style persists it and moves the .on marker
  h1.fire(bar1.children.find((b) => b.dataset.s === 'helix'), 'click');
  assert.equal(h1.store.get('keel-3dstyle'), 'helix');
  assert.ok(bar1.children.find((b) => b.dataset.s === 'helix').classList.contains('on'));
  assert.ok(!bar1.children.find((b) => b.dataset.s === 'curve').classList.contains('on'));
  // a stored style is honored on boot
  const h2 = await bootDash([makeRun()], { localStorageData: { 'keel-3dstyle': 'ring' } });
  findAll(h2.byId('grid'), 'card')[0].onclick();
  assert.ok(findOne(h2.byId('drawer'), 'seg3dd').children
    .find((b) => b.dataset.s === 'ring').classList.contains('on'));
  // an unknown stored style falls back to curve
  const h3 = await bootDash([makeRun()], { localStorageData: { 'keel-3dstyle': 'bogus' } });
  findAll(h3.byId('grid'), 'card')[0].onclick();
  assert.ok(findOne(h3.byId('drawer'), 'seg3dd').children
    .find((b) => b.dataset.s === 'curve').classList.contains('on'));
});

test('dashboard: theme cycles and persists like the board', async () => {
  const h = await bootDash([makeRun()]);
  const tgl = h.byId('tgl');
  const root = h.doc.documentElement;
  h.fire(tgl, 'click');
  assert.equal(root.getAttribute('data-theme'), 'light');
  assert.equal(h.store.get('keel-theme'), 'light');
  assert.equal(tgl.textContent, '☀');
  h.fire(tgl, 'click');
  assert.equal(root.getAttribute('data-theme'), 'dark');
  h.fire(tgl, 'click');
  assert.equal(root.getAttribute('data-theme'), null);
  assert.equal(h.store.has('keel-theme'), false);
});

test('dashboard: each poll re-renders the grid and any open drawer', async () => {
  const h = await bootDash([makeRun({ active_index: 4 })]);
  findAll(h.byId('grid'), 'card')[0].onclick();
  assert.ok(findAll(h.byId('d2d'), 'frow')[4].classList.contains('cur'));
  // the run advances two steps by the next poll
  h.fetchJson = () => [makeRun({ active_index: 6, active_id: 's6', active_name: 'ci', status: 'gate' })];
  h.intervals[0].fn();
  await h.flush();
  const rows = findAll(h.byId('d2d'), 'frow');
  assert.ok(rows[6].classList.contains('gat'), 'drawer followed the poll to the gate step');
  assert.ok(!rows[4].classList.contains('cur'));
  assert.equal(metaRows(h).phase, 's6 · ci  (7/13)');
});
