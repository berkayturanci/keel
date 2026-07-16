// Level (a) tests: pure helpers sliced out of the template scripts and evaluated
// directly — payload-independent logic seams (step tone mapping, label derivation,
// free-text filtering, done/ordering predicates, deterministic seeding).
import test from 'node:test';
import assert from 'node:assert/strict';

import { loadTemplate, mainScript, extractFunction, extractConst } from './_dom.mjs';

const board = mainScript(loadTemplate('board.html'));
const dash = mainScript(loadTemplate('dashboard.html'));
const runviz = mainScript(loadTemplate('runviz.html'));

function buildStepTone(src) {
  const body = 'const GATE_KINDS={gate:1,merge:1};' + extractFunction(src, 'stepTone') + ';return stepTone;';
  return new Function(body)();
}

test('stepTone: full tone matrix (board)', () => {
  const stepTone = buildStepTone(board);
  // merged run: everything up to the active index is done
  assert.equal(stepTone(3, 5, true, { kind: 'normal' }), 'done');
  assert.equal(stepTone(5, 5, true, { kind: 'merge' }), 'done');
  // behind the head → done, ahead → idle
  assert.equal(stepTone(2, 4, false, {}), 'done');
  assert.equal(stepTone(9, 4, false, {}), 'idle');
  // at the head: normal → active, gate/merge kinds → gate
  assert.equal(stepTone(4, 4, false, { kind: 'normal' }), 'active');
  assert.equal(stepTone(4, 4, false, { kind: 'gate' }), 'gate');
  assert.equal(stepTone(4, 4, false, { kind: 'merge' }), 'gate');
  // failed gate outcome wins over the kind
  assert.equal(stepTone(4, 4, false, { kind: 'gate', gate: { outcome: 'fail' } }), 'fail');
  assert.equal(stepTone(4, 4, false, { kind: 'normal', gate: { outcome: 'fail' } }), 'fail');
  // pending/pass outcomes do not trip the fail branch
  assert.equal(stepTone(4, 4, false, { kind: 'gate', gate: { outcome: 'pass' } }), 'gate');
  // missing step object is tolerated
  assert.equal(stepTone(4, 4, false, undefined), 'active');
});

test('stepTone: board and dashboard copies agree on every combination', () => {
  const a = buildStepTone(board);
  const b = buildStepTone(dash);
  const stepVariants = [
    undefined,
    {},
    { kind: 'normal' },
    { kind: 'gate' },
    { kind: 'merge' },
    { kind: 'loop' },
    { kind: 'gate', gate: { outcome: 'fail' } },
    { kind: 'gate', gate: { outcome: 'pass' } },
    { kind: 'normal', gate: { outcome: 'fail' } },
  ];
  for (let idx = 0; idx < 4; idx++) {
    for (let active = 0; active < 4; active++) {
      for (const merged of [false, true]) {
        for (const s of stepVariants) {
          assert.equal(
            a(idx, active, merged, s),
            b(idx, active, merged, s),
            `divergence at idx=${idx} active=${active} merged=${merged} s=${JSON.stringify(s)}`
          );
        }
      }
    }
  }
});

test('idn (dashboard): label derivation collapses id===name', () => {
  const idn = new Function(extractConst(dash, 'idn') + ';return idn;')();
  assert.equal(idn('s4', 'implement'), 's4 · implement');
  assert.equal(idn('health', 'health'), 'health');
  assert.equal(idn(null, 'implement'), 'implement');
  assert.equal(idn('s4', null), 's4');
  assert.equal(idn(null, null), '?');
  assert.equal(idn('', ''), '?');
});

test('matchesQuery (dashboard): free-text filter over project/label/command/branch/title', () => {
  const factory = new Function('query', extractConst(dash, 'matchesQuery') + ';return matchesQuery;');
  const run = {
    project: 'Keel',
    label: '#101',
    command: 'ship',
    branch: 'feat/board-Tests',
    title: 'Add JS coverage',
  };
  assert.equal(factory('')(run), true, 'empty query matches everything');
  assert.equal(factory('keel')(run), true, 'project, case-insensitive');
  assert.equal(factory('#101')(run), true, 'label');
  assert.equal(factory('ship')(run), true, 'command');
  assert.equal(factory('board-tests')(run), true, 'branch, case-insensitive');
  assert.equal(factory('js coverage')(run), true, 'title substring');
  assert.equal(factory('nomatch')(run), false);
  assert.equal(factory('keel')({}), false, 'all fields absent → no match');
  assert.equal(factory('')({}), true);
});

test('isDone: merged OR done means finished (board + dashboard parity)', () => {
  const a = new Function(extractConst(board, 'isDone') + ';return isDone;')();
  const b = new Function(extractConst(dash, 'isDone') + ';return isDone;')();
  const cases = [
    [{}, false],
    [{ merged: true }, true],
    [{ done: true }, true],
    [{ merged: true, done: true }, true],
    [{ merged: false, done: false }, false],
    [{ merged: 0, done: '' }, false],
  ];
  for (const [run, want] of cases) {
    assert.equal(a(run), want, 'board isDone ' + JSON.stringify(run));
    assert.equal(b(run), want, 'dashboard isDone ' + JSON.stringify(run));
  }
});

test('keyOf (dashboard): stable identity key, tolerant of missing fields', () => {
  const keyOf = new Function(extractConst(dash, 'keyOf') + ';return keyOf;')();
  assert.equal(keyOf({ project: 'keel', label: '#9', command: 'ship' }), 'keel|#9|ship');
  assert.equal(keyOf({}), '||');
  assert.notEqual(
    keyOf({ project: 'keel', label: '#9', command: 'ship' }),
    keyOf({ project: 'keel', label: '#9', command: 'wrap' })
  );
});

test('seed3: deterministic, in [0,1), identical in runviz and dashboard', () => {
  const a = new Function(extractConst(runviz, 'seed3') + ';return seed3;')();
  const b = new Function(extractConst(dash, 'seed3') + ';return seed3;')();
  for (let i = 0; i < 64; i++) {
    const v = a(i);
    assert.ok(v >= 0 && v < 1, `seed3(${i})=${v} out of range`);
    assert.equal(v, a(i), 'not deterministic');
    assert.equal(v, b(i), 'runviz/dashboard seed divergence');
  }
});

test('gate outcome readers: runviz gateOutcome vs dashboard gOut defaults', () => {
  const gateOutcome = new Function(extractFunction(runviz, 'gateOutcome') + ';return gateOutcome;')();
  const gOut = new Function(extractFunction(dash, 'gOut') + ';return gOut;')();
  assert.equal(gateOutcome({}), null, 'runviz: no gate → null');
  assert.equal(gOut({}), 'pending', 'dashboard: no gate → pending');
  assert.equal(gateOutcome({ gate: { outcome: 'fail' } }), 'fail');
  assert.equal(gOut({ gate: { outcome: 'fail' } }), 'fail');
  assert.equal(gateOutcome({ gate: { outcome: 'pass' } }), 'pass');
  assert.equal(gOut({ gate: { outcome: 'pass' } }), 'pass');
});
