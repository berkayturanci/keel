// Behavioral tests for runviz.html: the real inline script runs in the vm harness —
// window.KEEL_RUN consumption, 2D node painting, gate/regression/merge/jury panels,
// scrub + playback stepping, and the graceful no-THREE fallback.
import test from 'node:test';
import assert from 'node:assert/strict';

import { boot, makeSteps } from './_dom.mjs';

function makePayload(over = {}) {
  return {
    command: 'ship',
    issue: 42,
    pr: 77,
    merged: false,
    active_index: 4,
    steps: makeSteps(),
    regression: {},
    jury: { active: false },
    ...over,
  };
}

const bootViz = (payload, opts = {}) =>
  boot('runviz.html', payload === undefined ? opts : { payloadVar: 'KEEL_RUN', payload, ...opts });

test('runviz: consumes window.KEEL_RUN — nodes, scrub range, stage panel, meta chips', () => {
  const h = bootViz(makePayload());
  assert.equal(h.win.__keelReady, true);
  assert.equal(h.byId('nodes').children.length, 13);
  assert.equal(h.byId('scrub').max, 12);
  assert.equal(h.byId('sname').textContent, 's4 · implement');
  assert.equal(h.byId('sdesc').textContent, 'agent writes the change');
  const meta = h.byId('meta').innerHTML;
  assert.ok(meta.includes('cmd <b>ship</b>'), 'command chip');
  assert.ok(meta.includes('issue <b>#42</b>'), 'issue label chip');
  assert.ok(meta.includes('PR <b>#77</b>'), 'PR label chip');
  assert.ok(meta.includes('in progress'), 'merged-state chip');
});

test('runviz: node classes — done behind the head, act at it, dim for unexercised', () => {
  const steps = makeSteps();
  steps[5].exercised = false;
  const h = bootViz(makePayload({ steps }));
  const nodes = h.byId('nodes').children;
  assert.ok(nodes[0].classList.contains('done'));
  assert.ok(nodes[3].classList.contains('done'));
  assert.ok(nodes[4].classList.contains('act'));
  assert.ok(nodes[5].classList.contains('dim'), 'unexercised step is dimmed');
  assert.ok(!nodes[6].classList.contains('done') && !nodes[6].classList.contains('act'));
});

test('runviz: missing payload degrades to the default run without crashing', () => {
  const h = bootViz(undefined);
  assert.equal(h.win.__keelReady, true);
  assert.equal(h.byId('nodes').children.length, 0);
  assert.equal(h.byId('scrub').max, 12, 'N falls back to 13 steps');
  assert.equal(h.byId('sname').textContent, 's0 · config');
});

test('runviz: failed gate paints the fail state and gate panel', () => {
  const steps = makeSteps();
  steps[8].gate = { outcome: 'fail' };
  const h = bootViz(makePayload({ steps, active_index: 8 }));
  const node = h.byId('nodes').children[8];
  assert.ok(node.classList.contains('gate') && node.classList.contains('fail'));
  assert.equal(h.byId('pill').textContent, 'blocked');
  assert.equal(h.byId('pill').className, 'pill fail');
  assert.ok(h.byId('gatewrap').classList.contains('show'));
  assert.equal(h.byId('gatemsg').textContent, 'gate blocked — blocking findings');
  assert.equal(h.byId('gatebar').style.background, '--danger');
  assert.ok(!h.byId('regwrap').classList.contains('show'), 'regression panel hidden when not reached');
});

test('runviz: passing gate + regression coverage panel (minor worst finding)', () => {
  const steps = makeSteps();
  steps[8].gate = { outcome: 'pass' };
  const h = bootViz(
    makePayload({ steps, active_index: 8, regression: { reached: true, coverage: 82.4, worst: 'minor' } })
  );
  assert.equal(h.byId('pill').textContent, 'passed');
  assert.equal(h.byId('pill').className, 'pill ok');
  assert.equal(h.byId('gatemsg').textContent, 'gate passed');
  assert.ok(h.byId('regwrap').classList.contains('show'));
  assert.equal(h.byId('regpct').textContent, '82%');
  assert.equal(h.byId('regbar').style.width, '82.4%');
  assert.equal(h.byId('regmsg').textContent, 'minor finding — folder yellow');
  assert.equal(h.byId('folder').textContent, '▣');
});

test('runviz: clean regression scan shows the green folder', () => {
  const h = bootViz(
    makePayload({ active_index: 8, regression: { reached: true, coverage: 100, worst: 'none' } })
  );
  assert.equal(h.byId('regmsg').textContent, 'all suites green');
  assert.equal(h.byId('folder').textContent, '▦');
});

test('runviz: merged run at the merge step goes green', () => {
  const h = bootViz(makePayload({ merged: true, active_index: 10 }));
  assert.equal(h.byId('pill').textContent, 'green');
  assert.equal(h.byId('pill').className, 'pill ok');
  assert.ok(h.byId('mergewrap').classList.contains('show'));
  assert.equal(h.byId('fill').style.background, '--ok');
  const nodes = h.byId('nodes').children;
  assert.ok(nodes[10].classList.contains('done'), 'merge node repainted done');
  assert.ok(!nodes[10].classList.contains('gate'));
});

test('runviz: unmerged merge step stays a gate; loop step shows the loop pill', () => {
  const h1 = bootViz(makePayload({ merged: false, active_index: 10 }));
  assert.equal(h1.byId('pill').textContent, 'gate');
  assert.equal(h1.byId('pill').className, 'pill warn');
  assert.ok(h1.byId('gatewrap').classList.contains('show'));
  const h2 = bootViz(makePayload({ active_index: 9 }));
  assert.equal(h2.byId('pill').textContent, 'loop ↩');
  assert.equal(h2.byId('pill').className, 'pill warn');
});

test('runviz: jury seats, verdict mode, review-step pill and play-time animation', () => {
  const h = bootViz(makePayload({ active_index: 7, jury: { active: true, mode: 'gating' } }));
  assert.equal(h.byId('jurors').children.length, 3, 'cross-vendor panel seats');
  assert.equal(h.byId('jurymode').textContent, 'gating');
  assert.ok(h.byId('jverdict').classList.contains('gating'));
  assert.equal(h.byId('jverdicttxt').textContent, 'gating verdict');
  assert.equal(h.byId('pill').textContent, 'jury · gating');
  assert.ok(h.byId('jurywrap').classList.contains('show'));
  // the jury animates only while playing at the review step
  assert.ok(!h.byId('jury2d').classList.contains('anim'));
  h.fire(h.byId('play'), 'click');
  assert.ok(h.byId('jury2d').classList.contains('anim'));
  h.fire(h.byId('play'), 'click');
  assert.ok(!h.byId('jury2d').classList.contains('anim'));
});

test('runviz: advisory jury renders the advisory verdict', () => {
  const h = bootViz(makePayload({ jury: { active: true, mode: 'advisory' } }));
  assert.equal(h.byId('jverdicttxt').textContent, 'advisory verdict');
  assert.ok(!h.byId('jverdict').classList.contains('gating'));
});

test('runviz: scrubbing repaints the stage at the chosen step', () => {
  const h = bootViz(makePayload());
  const scrub = h.byId('scrub');
  scrub.value = '2';
  h.fire(scrub, 'input');
  assert.equal(h.byId('sname').textContent, 's2 · branch');
  assert.equal(h.byId('sdesc').textContent, 'worktree off base');
});

test('runviz: play/pause toggles and stepping skips unexercised steps', () => {
  const steps = makeSteps();
  steps[5].exercised = false;
  const h = bootViz(makePayload({ steps }));
  h.fire(h.byId('play'), 'click');
  assert.equal(h.byId('playlbl').textContent, 'pause');
  assert.ok(h.timers.length >= 1, 'playback timer scheduled');
  h.runTimer(0); // one tick: s4 → skips unexercised s5 → s6
  assert.equal(h.byId('sname').textContent, 's6 · ci');
  h.fire(h.byId('play'), 'click');
  assert.equal(h.byId('playlbl').textContent, 'play');
});

test('runviz: URL step param overrides the payload head and is clamped', () => {
  const h1 = bootViz(makePayload(), { search: '?step=3' });
  assert.equal(h1.byId('sname').textContent, 's3 · guard');
  const h2 = bootViz(makePayload(), { search: '?step=99' });
  assert.equal(h2.byId('sname').textContent, 's12 · close');
});

test('runviz: 2D/3D toggle flips views; 3D is unavailable without THREE', () => {
  const h = bootViz(makePayload(), { search: '?mode=3d' });
  assert.ok(h.byId('view3d').classList.contains('show'));
  assert.ok(!h.byId('view2d').classList.contains('show'));
  assert.equal(h.byId('hud3').textContent, '3D unavailable');
  h.fire(h.byId('seg'), 'click', h.btnEvent({ m: '2d' }));
  assert.ok(h.byId('view2d').classList.contains('show'));
  assert.ok(!h.byId('view3d').classList.contains('show'));
});

test('runviz: play=1 URL param starts playback on boot', () => {
  const h = bootViz(makePayload(), { search: '?play=1' });
  assert.equal(h.byId('playlbl').textContent, 'pause');
  assert.ok(h.timers.length >= 1);
});
