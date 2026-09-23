// Behavioral tests for swarm.html: the real main script runs in the vm harness
// against a stub DOM, and every card it builds is read back as markup. Worker
// details carry child keel ship output, which can quote issue and PR text, so a
// hostile value must arrive as text, never as markup.
import test from 'node:test';
import assert from 'node:assert/strict';

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
