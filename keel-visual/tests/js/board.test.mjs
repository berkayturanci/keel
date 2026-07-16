// Behavioral tests for board.html: the template's real inline script is executed
// in a vm sandbox against the stub DOM (see _dom.mjs) — payload parsing, run
// ordering, the all/active filter, theme toggling and the 3D fallback path.
import test from 'node:test';
import assert from 'node:assert/strict';

import { boot, findAll, findOne, makeRun } from './_dom.mjs';

const bootBoard = (payload, opts = {}) =>
  boot('board.html', { payloadVar: 'KEEL_BOARD', payload, ...opts });

test('board: consumes window.KEEL_BOARD and renders one card per run', () => {
  const h = bootBoard([
    makeRun({ project: 'keel', label: '#101' }),
    makeRun({ project: 'other', label: '#7', merged: true, status: 'merged', active_index: 12 }),
  ]);
  assert.equal(h.win.__keelBoardReady, true);
  const cards = findAll(h.byId('grid'), 'card');
  assert.equal(cards.length, 2);
  assert.equal(h.byId('count').textContent, '2 runs across 2 projects · 1 done');
  // active run first, finished run last (and faded via .done)
  assert.equal(findOne(cards[0], 'proj').textContent, 'keel');
  assert.equal(findOne(cards[1], 'proj').textContent, 'other');
  assert.ok(cards[1].classList.contains('done'));
  assert.ok(!cards[0].classList.contains('done'));
  // label chip + footer step label ("s4 · implement")
  assert.equal(findOne(cards[0], 'lab').textContent, '#101');
  assert.equal(findOne(cards[0], 'step').textContent, 's4 · implement');
  // branch meta renders "branch → base"
  assert.equal(findOne(cards[0], 'br').textContent, 'feat/x → main');
});

test('board: step strip tones follow stepTone (done/active/gate/idle)', () => {
  const h = bootBoard([makeRun({ active_index: 6 })]); // s6 is a gate step
  const card = findAll(h.byId('grid'), 'card')[0];
  const dots = findAll(card, 'dot');
  assert.equal(dots.length, 13);
  assert.ok(dots[0].classList.contains('done'));
  assert.ok(dots[5].classList.contains('done'));
  assert.ok(dots[6].classList.contains('gate'), 'active gate step pulses amber');
  assert.ok(!dots[7].classList.contains('done') && !dots[7].classList.contains('active'));
});

test('board: non-array payload degrades to the empty state', () => {
  const h = bootBoard({ not: 'an array' });
  const grid = h.byId('grid');
  assert.equal(findAll(grid, 'card').length, 0);
  assert.equal(findAll(grid, 'empty')[0].textContent, 'no active runs found');
  assert.equal(h.byId('count').textContent, '0 runs across 0 projects');
});

test('board: jury pill appears only for jury-active runs, gating styled', () => {
  const h = bootBoard([
    makeRun({ label: '#1', jury: { active: true, mode: 'gating' } }),
    makeRun({ label: '#2', jury: { active: false } }),
  ]);
  const cards = findAll(h.byId('grid'), 'card');
  const pill = findOne(cards[0], 'jury');
  assert.equal(pill.textContent, 'jury · gating');
  assert.ok(pill.classList.contains('gating'));
  assert.equal(findOne(cards[1], 'jury'), null);
});

test('board: active filter hides finished runs and updates the count', () => {
  const h = bootBoard([
    makeRun(),
    makeRun({ project: 'p2', merged: true, status: 'merged' }),
  ]);
  h.fire(h.byId('segf'), 'click', h.btnEvent({ f: 'active' }));
  assert.ok(h.doc.body.classList.contains('active-only'));
  assert.equal(h.byId('count').textContent, '1 run across 1 project');
  // back to all
  h.fire(h.byId('segf'), 'click', h.btnEvent({ f: 'all' }));
  assert.ok(!h.doc.body.classList.contains('active-only'));
  assert.equal(h.byId('count').textContent, '2 runs across 2 projects · 1 done');
});

test('board: active filter with nothing active shows the all-done empty card', () => {
  const h = bootBoard([makeRun({ merged: true, status: 'merged' })]);
  const empties = findAll(h.byId('grid'), 'empty');
  const emptyActive = empties[empties.length - 1];
  assert.equal(emptyActive.style.display, 'none');
  h.fire(h.byId('segf'), 'click', h.btnEvent({ f: 'active' }));
  assert.equal(emptyActive.style.display, 'block');
  assert.equal(emptyActive.textContent, 'no active runs — all done');
});

test('board: 3D mode toggles views and degrades without THREE', () => {
  const h = bootBoard([makeRun()]);
  h.fire(h.byId('seg3'), 'click', h.btnEvent({ m: '3d' }));
  assert.ok(h.byId('view3d').classList.contains('show'));
  assert.ok(!h.byId('view2d').classList.contains('show'));
  assert.equal(h.qs('.hud3').textContent, '3D unavailable');
  h.fire(h.byId('seg3'), 'click', h.btnEvent({ m: '2d' }));
  assert.ok(h.byId('view2d').classList.contains('show'));
  assert.ok(!h.byId('view3d').classList.contains('show'));
});

test('board: URL params preselect 3D mode and the active filter', () => {
  const h = bootBoard([makeRun()], { search: '?mode=3d&filter=active' });
  assert.ok(h.byId('view3d').classList.contains('show'));
  assert.ok(h.doc.body.classList.contains('active-only'));
});

test('board: theme cycles system → light → dark → system and persists', () => {
  const h = bootBoard([makeRun()]);
  const tgl = h.byId('tgl');
  const root = h.doc.documentElement;
  assert.equal(root.getAttribute('data-theme'), null);
  h.fire(tgl, 'click');
  assert.equal(root.getAttribute('data-theme'), 'light');
  assert.equal(h.store.get('keel-theme'), 'light');
  assert.equal(tgl.textContent, '☀');
  h.fire(tgl, 'click');
  assert.equal(root.getAttribute('data-theme'), 'dark');
  assert.equal(h.store.get('keel-theme'), 'dark');
  assert.equal(tgl.textContent, '☾');
  h.fire(tgl, 'click');
  assert.equal(root.getAttribute('data-theme'), null);
  assert.equal(h.store.has('keel-theme'), false);
  assert.equal(tgl.textContent, '◐');
});

test('board: stored manual theme is applied before first paint', () => {
  const h = bootBoard([makeRun()], { localStorageData: { 'keel-theme': 'dark' } });
  assert.equal(h.doc.documentElement.getAttribute('data-theme'), 'dark');
  assert.equal(h.byId('tgl').textContent, '☾');
});

test('board: hovering a step dot builds the rich tooltip from SHIP_DESC + tone', () => {
  const h = bootBoard([makeRun({ active_index: 4 })]);
  const card = findAll(h.byId('grid'), 'card')[0];
  const dots = findAll(card, 'dot');
  h.fire(dots[6], 'mouseenter'); // s6, ahead of the head → idle
  const tip = h.byId('tip');
  assert.ok(tip.classList.contains('show'));
  assert.equal(findOne(tip, 'td').textContent, 'CI — poll the configured workflows.');
  assert.equal(findOne(tip, 'tst').textContent, 'not reached yet');
  assert.equal(findOne(tip, 'tkind').textContent, 'gate', 'gate kind chip');
  h.fire(dots[6], 'mouseleave');
  assert.ok(!tip.classList.contains('show'));
});

test('board: tooltip falls back to a generic phase line for non-ship flows', () => {
  const h = bootBoard([
    makeRun({
      command: 'health',
      steps: [{ id: 'health', name: 'health', kind: 'normal', exercised: true }],
      active_index: 0,
      active_id: 'health',
      active_name: 'health',
    }),
  ]);
  const dots = findAll(findAll(h.byId('grid'), 'card')[0], 'dot');
  h.fire(dots[0], 'mouseenter');
  assert.equal(findOne(h.byId('tip'), 'td').textContent, 'Phase 1 of 1 in the health flow.');
});
