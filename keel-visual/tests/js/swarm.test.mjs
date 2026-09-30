// Behavioral tests for swarm.html: the real main script runs in the vm harness
// against a stub DOM, and every card it builds is read back as markup. Worker
// details carry child keel ship output, which can quote issue and PR text, so a
// hostile value must arrive as text, never as markup.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { boot, loadTemplate, mainScript, extractFunction } from './_dom.mjs';

const HOSTILE = '<img src=x onerror=alert(1)>';

function makeSwarm(over = {}) {
  return {
    swarm_id: 'swarm-t',
    plan: {
      swarm_id: 'swarm-t',
      total_issues: 2,
      waves: [
        {
          wave_index: 1,
          eligible_direct_landing: true,
          clusters: [
            { cluster_id: 'c1', role: 'core', issues: [11, 12], combined_scope: ['src/a.py'] },
          ],
        },
      ],
    },
    state: {
      active_wave: 1,
      workers: [
        { issue: 11, cluster_id: 'c1', role: 'core', status: 'passed', step: 's8' },
        { issue: 12, cluster_id: 'c1', role: 'core', status: 'failed', step: 's6', details: 'CI red' },
      ],
    },
    ...over,
  };
}

function bootSwarm(payload) {
  const h = boot('swarm.html', { globals: { __KEEL_SWARM__: payload } });
  h.winFire('DOMContentLoaded');
  return h;
}

// Every markup string the script wrote, the element's own and its children's.
function allHtml(el) {
  return [el.innerHTML, el.className, ...el.children.map(allHtml)].join('\n');
}

test('swarm: esc() escapes the five HTML metacharacters', () => {
  const src = mainScript(loadTemplate('swarm.html'));
  const esc = new Function(extractFunction(src, 'esc') + '; return esc;')();
  assert.equal(esc(`<a href="x">'&'</a>`), '&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;');
  assert.equal(esc(42), '42');
});

test('swarm: statusClass() keeps one plain token and refuses anything else', () => {
  const src = mainScript(loadTemplate('swarm.html'));
  const statusClass = new Function(extractFunction(src, 'statusClass') + '; return statusClass;')();
  assert.equal(statusClass('passed'), 'passed');
  assert.equal(statusClass('in_progress'), 'in_progress');
  assert.equal(statusClass('x" onmouseover="alert(1)'), 'queued');
  assert.equal(statusClass('passed failed'), 'queued');
  assert.equal(statusClass(undefined), 'queued');
  assert.equal(statusClass(7), 'queued');
});

test('swarm: normal data renders as before', () => {
  const h = bootSwarm(makeSwarm());
  const dag = allHtml(h.byId('waves-dag-row'));
  const matrix = allHtml(h.byId('workers-matrix-grid'));
  assert.ok(dag.includes('<span class="wave-title">Wave 1</span>'), dag);
  assert.ok(dag.includes('<span class="issue-pill">#11</span><span class="issue-pill">#12</span>'));
  assert.ok(dag.includes('<span class="scope-item">src/a.py</span>'));
  assert.ok(dag.includes('<span class="cluster-id">c1</span>'));
  assert.ok(dag.includes('cluster-card passed'));
  assert.ok(matrix.includes('<span class="worker-status passed">passed</span>'));
  assert.ok(matrix.includes('<span class="worker-status failed">failed</span>'));
  assert.ok(matrix.includes('Cluster: c1 · Step: s6'));
  assert.ok(matrix.includes('>CI red</div>'));
  assert.equal(h.byId('stat-passed').textContent, '1');
});

test('swarm: a dependent wave is labelled refused, never a funnel (#1276)', () => {
  const payload = makeSwarm();
  payload.plan.waves.push({
    wave_index: 2,
    mode: 'sequential_dependent',
    eligible_direct_landing: false,
    clusters: [
      { cluster_id: 'c2', role: 'core', issues: [13], combined_scope: ['src/a.py'], depends_on_issues: [11] },
    ],
  });
  const h = bootSwarm(payload);
  const dag = allHtml(h.byId('waves-dag-row'));
  const stat = h.byId('stat-landing-mode').textContent;
  assert.ok(dag.includes('<span class="wave-mode parallel">Orthogonal Parallel</span>'), dag);
  assert.ok(dag.includes('<span class="wave-mode sequential">Dependent — Refused</span>'), dag);
  assert.equal(stat, 'Direct Batch · Dependent Refused');
  assert.ok(!/funnel/i.test(dag + '\n' + stat), 'a dependent wave was called a funnel:\n' + dag + '\n' + stat);
});

test('swarm: every run-derived value reaches the page as text, not markup', () => {
  const bad = `x" onmouseover="alert(1)`;
  const payload = makeSwarm({
    plan: {
      waves: [
        {
          wave_index: HOSTILE,
          clusters: [
            { cluster_id: HOSTILE, role: HOSTILE, issues: [HOSTILE], combined_scope: [HOSTILE] },
          ],
        },
      ],
    },
    state: {
      workers: [
        { issue: HOSTILE, cluster_id: HOSTILE, role: HOSTILE, status: bad, step: HOSTILE, details: HOSTILE },
      ],
    },
  });
  const h = bootSwarm(payload);
  const html = allHtml(h.byId('waves-dag-row')) + allHtml(h.byId('workers-matrix-grid'));
  assert.ok(!html.includes('<img'), 'a hostile value was written as markup:\n' + html);
  assert.ok(!html.includes('onmouseover="'), 'a hostile status broke out of its attribute:\n' + html);
  // five values in the DAG and five in the matrix carry HOSTILE; each arrives escaped.
  assert.equal(html.split('&lt;img src=x onerror=alert(1)&gt;').length - 1, 10);
  // the status still shows, escaped, as the badge text; its class falls back
  assert.ok(html.includes('<span class="worker-status queued">x&quot; onmouseover=&quot;alert(1)</span>'));
});

// ---- the persisted plan (keel #1275, #1280 item 1) ----
//
// tests/fixtures/swarm-plan.json is a plan keel core built and serialised
// (swarm_plan_payload), and test_swarm_visual.py holds that core still reads it back
// unchanged: issues 11 and 13 both predict src/pkg/parser.py, so 13 lands in a second,
// dependent wave; 12 is docs-only and shares wave 1.
const FIXTURE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'fixtures', 'swarm-plan.json');

function persistedSwarm() {
  const envelope = JSON.parse(fs.readFileSync(FIXTURE, 'utf8'));
  return {
    swarm_id: 'swarm-fixture',
    plan: envelope.plan,
    plan_status: 'persisted',
    plan_detail: '.keel/state/swarm/swarm-fixture.plan.json',
    state: {
      active_wave: 1,
      workers: [
        { issue: 11, cluster_id: 'cluster-1-11', role: 'core', status: 'passed', step: 's10' },
        { issue: 12, cluster_id: 'cluster-1-12', role: 'docs', status: 'running', step: 's4' },
      ],
    },
  };
}

function waveColumns(h) {
  return h.byId('waves-dag-row').children;
}

test('swarm: the persisted plan draws its real waves, scopes and dependencies', () => {
  const h = bootSwarm(persistedSwarm());
  const cols = waveColumns(h);
  assert.equal(cols.length, 2, 'the persisted plan has two waves; a rebuilt plan had one');
  const [w1, w2] = cols.map(allHtml);

  assert.ok(w1.includes('<span class="wave-mode parallel">Orthogonal Parallel</span>'), w1);
  assert.ok(w1.includes('<span class="cluster-id">cluster-1-11</span>'), w1);
  assert.ok(w1.includes('<span class="cluster-id">cluster-1-12</span>'), w1);
  assert.ok(w2.includes('<span class="wave-title">Wave 2</span>'), w2);
  assert.ok(w2.includes('<span class="wave-mode sequential">Dependent — Refused</span>'), w2);
  assert.ok(w2.includes('<span class="cluster-id">cluster-2-13</span>'), w2);

  // predicted files, each issue's title and where its scope came from
  assert.ok(w1.includes('<span class="scope-item">src/pkg/parser.py</span><span class="scope-item">src/pkg/lexer.py</span>'), w1);
  assert.ok(w1.includes('<span class="scope-item">docs/flags.md</span>'), w1);
  assert.ok(w1.includes('#11 Parser rewrite</span><span class="scope-src">declared-file</span>'), w1);
  assert.ok(w1.includes('#12 Document the flags</span><span class="scope-src">issue-body</span>'), w1);

  // the dependency names the issue and the wave it waits on; wave 1 depends on nothing
  assert.ok(w2.includes('<div class="deps-row"><span>depends on</span><span class="dep-pill">#11 · wave 1</span></div>'), w2);
  assert.ok(!w1.includes('deps-row'), w1);

  // difficulty and staffing ride along
  assert.ok(w1.includes('<span class="band-badge">'), w1);
  assert.ok(w1.includes('<div class="impl-line">implementer claude</div>'), w1);

  // worker state still colours the cards; the plan-less notice stays hidden
  assert.ok(w1.includes('cluster-card passed'), w1);
  assert.ok(w2.includes('cluster-card queued'), w2);
  assert.equal(h.byId('plan-notice').hidden, true);
  assert.equal(h.byId('plan-notice').textContent, '');
  assert.equal(h.byId('stat-waves').textContent, '2');
  assert.equal(h.byId('stat-issues').textContent, '3');
  assert.equal(h.byId('stat-landing-mode').textContent, 'Direct Batch · Dependent Refused');
});

test('swarm: with no persisted plan the page says so and draws no DAG', () => {
  const payload = persistedSwarm();
  payload.plan = null;
  payload.plan_status = 'missing';
  payload.plan_detail = 'no persisted plan at .keel/state/swarm/swarm-fixture.plan.json';
  const h = bootSwarm(payload);
  assert.equal(waveColumns(h).length, 0, 'a plan-less run drew a DAG');
  const notice = h.byId('plan-notice');
  assert.equal(notice.hidden, false);
  assert.match(notice.textContent, /persisted no plan, so no DAG is drawn/);
  assert.ok(notice.textContent.includes('.keel/state/swarm/swarm-fixture.plan.json'), notice.textContent);
  assert.equal(h.byId('stat-waves').textContent, '0');
  assert.equal(h.byId('stat-landing-mode').textContent, 'No plan');
  // the workers are real run state and still show
  const matrix = allHtml(h.byId('workers-matrix-grid'));
  assert.ok(matrix.includes('<span class="worker-status passed">passed</span>'), matrix);
  assert.equal(h.byId('stat-issues').textContent, '2');
});

test('swarm: an unreadable plan is reported, and nothing it holds is drawn', () => {
  const payload = persistedSwarm();
  payload.plan_status = 'unreadable';
  payload.plan_detail = '.keel/state/swarm/swarm-fixture.plan.json: schema version 2 is not one keel-visual reads (1)';
  const h = bootSwarm(payload);
  assert.equal(waveColumns(h).length, 0);
  const notice = h.byId('plan-notice');
  assert.equal(notice.hidden, false);
  assert.match(notice.textContent, /could not be read, so no DAG is drawn/);
  assert.ok(notice.textContent.includes('schema version 2'), notice.textContent);
});

test('swarm: a plan of the wrong shape draws what it can instead of throwing', () => {
  const payload = makeSwarm();
  payload.plan.waves = [null, 'x', { wave_index: 1, eligible_direct_landing: true, clusters: 'nope' },
    { wave_index: 2, clusters: [null, { cluster_id: 'c9', issues: 'x', combined_scope: 7, depends_on_issues: {} }] }];
  payload.plan.issue_scopes = 'x';
  const h = bootSwarm(payload);
  const dag = allHtml(h.byId('waves-dag-row'));
  assert.equal(waveColumns(h).length, 2);
  assert.ok(dag.includes('<span class="cluster-id">c9</span>'), dag);
  assert.ok(dag.includes('<span class="scope-item none">no predicted files</span>'), dag);
});

test('swarm: a long scope is cut to six files and says how many more', () => {
  const payload = makeSwarm();
  payload.plan.waves[0].clusters[0].combined_scope = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
  const dag = allHtml(bootSwarm(payload).byId('waves-dag-row'));
  assert.ok(dag.includes('<span class="scope-item">f</span><span class="scope-item">+2 more</span>'), dag);
  assert.ok(!dag.includes('<span class="scope-item">g</span>'), dag);
});

test('swarm: every plan value the DAG shows reaches the page as text, not markup', () => {
  const payload = persistedSwarm();
  const c = payload.plan.waves[1].clusters[0];
  c.depends_on_issues = [HOSTILE];
  c.difficulty.band = HOSTILE;
  c.assignment.implementer.name = HOSTILE;
  payload.plan.issue_scopes['13'].title = HOSTILE;
  payload.plan.issue_scopes['13'].scope_source = HOSTILE;
  const h = bootSwarm(payload);
  const html = allHtml(h.byId('waves-dag-row'));
  assert.ok(!html.includes('<img'), 'a hostile plan value was written as markup:\n' + html);
  // dependency, band, implementer, title, scope source: five, each escaped
  assert.equal(html.split('&lt;img src=x onerror=alert(1)&gt;').length - 1, 5, html);

  const bare = persistedSwarm();
  bare.plan = null;
  bare.plan_status = 'unreadable';
  bare.plan_detail = HOSTILE;
  const notice = bootSwarm(bare).byId('plan-notice');
  // the notice is set as text: it stays one text node and never becomes markup
  assert.equal(notice.innerHTML, '');
  assert.ok(notice.textContent.includes(HOSTILE));
});
