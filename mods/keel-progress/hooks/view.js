// Pure projection of `keel status --json` onto what keel-progress draws.
// No mods API here: register.js turns these plain values into elements.

// The backbone, used only when the status payload carries no contract.
export const FALLBACK_STEPS = [
  ['s0', 'config'], ['s1', 'select'], ['s2', 'branch'], ['s3', 'guard'],
  ['s4', 'implement'], ['s5', 'classify'], ['s6', 'ci'], ['s7', 'review'],
  ['s8', 'test'], ['s9', 'fixloop'], ['s10', 'merge'], ['s11', 'capture'],
  ['s12', 'close'],
].map(([id, name]) => ({ id, name }))

// States worth a line above the prompt. `no-active-run` and `completed` draw nothing.
const LIVE_STATES = new Set(['active', 'waiting', 'interrupted'])

// Parses `keel status --json` stdout into { snapshot, steps, checkpointPath }, or throws.
// checkpointPath is the project's checkpoint, relative to its root, when the contract names it.
export function parseStatus(stdout) {
  const payload = JSON.parse(stdout)
  const snapshot = payload && payload.snapshot
  if (!snapshot || typeof snapshot.status !== 'string') {
    throw new Error('keel status --json has no snapshot.status')
  }
  const declared = payload.contract?.source?.checkpoint?.steps
  const steps = Array.isArray(declared) && declared.length > 0
    ? declared.map((s) => ({ id: String(s.step_id), name: String(s.step_name ?? s.step_id) }))
    : FALLBACK_STEPS
  const checkpoint = payload.contract?.source?.checkpoint?.path
  return { snapshot, steps, checkpointPath: typeof checkpoint === 'string' && checkpoint ? checkpoint : null }
}

// `git worktree list --porcelain` → [{ path, label }]; the label is the branch, else the folder.
export function parseWorktrees(porcelain) {
  const out = []
  for (const block of porcelain.replace(/\r\n/g, '\n').split('\n\n')) {
    let path = null
    let branch = null
    for (const line of block.split('\n')) {
      if (line.startsWith('worktree ')) path = line.slice('worktree '.length)
      else if (line.startsWith('branch ')) branch = line.slice('branch '.length).replace(/^refs\/heads\//, '')
    }
    if (path) out.push({ path, branch, label: branch ?? path.split('/').pop() })
  }
  return out
}

// Keeps one entry per run — by run id, else issue and PR, else the worktree — the one whose
// checkpoint was written last (the session's own on a tie). When the session's own folder held a
// stale copy, the winner is still the session's run: it keeps the `own` mark and goes first.
// Returns { kept, superseded }.
export function latestPerRun(entries) {
  // A run id names the run; an entry without one (an older checkpoint) joins the run that shares
  // its issue, so an activity record and an older checkpoint of the same run meet. With several
  // run ids for one issue it joins the one written last; runs written at the same moment keep the first seen.
  const runOfIssue = new Map()
  const newestOfIssue = new Map()
  for (const e of entries) {
    const c = e.snapshot.current
    if (!c.run_id || c.issue == null) continue
    if (!(newestOfIssue.get(c.issue) >= e.mtimeMs)) {
      newestOfIssue.set(c.issue, e.mtimeMs)
      runOfIssue.set(c.issue, `run:${c.run_id}`)
    }
  }
  const keyOf = (e) => {
    const c = e.snapshot.current
    if (c.run_id) return `run:${c.run_id}`
    // A copy written before the PR was opened has no PR yet: the issue alone names the run.
    if (c.issue != null) return runOfIssue.get(c.issue) ?? `issue:${c.issue}`
    if (c.pull_request != null) return `pr:${c.pull_request}`
    return `path:${e.path}`
  }
  const best = new Map()
  const ownKeys = new Set()
  for (const e of entries) {
    const key = keyOf(e)
    if (e.own) ownKeys.add(key)
    const prev = best.get(key)
    if (!prev || e.mtimeMs > prev.mtimeMs || (e.mtimeMs === prev.mtimeMs && e.own && !prev.own)) best.set(key, e)
  }
  const kept = []
  for (const e of entries) {
    const key = keyOf(e)
    if (best.get(key) !== e) continue
    const entry = ownKeys.has(key) ? { ...e, own: true } : e
    if (entry.own) kept.unshift(entry)
    else kept.push(entry)
  }
  return { kept, superseded: entries.length - kept.length, keyOf }
}

// Terminal cells a character takes: East Asian wide and fullwidth characters and emoji take
// two; combining marks, variation selectors and the zero-width joiner take none. A flag is two
// regional indicators (U+1F1E6-1F1FF) drawn in two cells, so each indicator counts as one.
// Written as escapes: invisible characters in a regex source do not survive editors.
const WIDE = /[\u{1100}-\u{115F}\u{231A}\u{231B}\u{23E9}-\u{23EC}\u{23F0}\u{23F3}\u{25FD}\u{25FE}\u{2614}\u{2615}\u{2648}-\u{2653}\u{267F}\u{2693}\u{26A1}\u{26AA}\u{26AB}\u{26BD}\u{26BE}\u{26C4}\u{26C5}\u{26CE}\u{26D4}\u{26EA}\u{26F2}\u{26F3}\u{26F5}\u{26FA}\u{26FD}\u{2705}\u{270A}\u{270B}\u{2728}\u{274C}\u{274E}\u{2753}-\u{2755}\u{2757}\u{2795}-\u{2797}\u{27B0}\u{27BF}\u{2B1B}\u{2B1C}\u{2B50}\u{2B55}\u{2E80}-\u{303E}\u{3041}-\u{33FF}\u{3400}-\u{4DBF}\u{4E00}-\u{9FFF}\u{A000}-\u{A4CF}\u{AC00}-\u{D7A3}\u{F900}-\u{FAFF}\u{FE30}-\u{FE4F}\u{FF00}-\u{FF60}\u{FFE0}-\u{FFE6}\u{1F300}-\u{1F64F}\u{1F680}-\u{1F6FF}\u{1F900}-\u{1FAFF}\u{20000}-\u{3FFFD}]/u
const ZERO = /[\u{0300}-\u{036F}\u{200B}-\u{200D}\u{FE00}-\u{FE0F}]/u
function charCells(ch) {
  return ZERO.test(ch) ? 0 : WIDE.test(ch) ? 2 : 1
}

export function cells(text) {
  let n = 0
  for (const ch of text) n += charCells(ch)
  return n
}

// The longest prefix of `text` that fits in `width` cells.
export function fitCells(text, width) {
  let out = ''
  let n = 0
  for (const ch of text) {
    const w = charCells(ch)
    if (n + w > width) break
    out += ch
    n += w
  }
  return out
}

// keel's file name for a run id (activity.run_id_slug): lowercase, runs of anything outside
// [a-z0-9._-] become '-', and leading or trailing '-' and '.' go.
export function runIdSlug(runId) {
  return String(runId).trim().toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[-.]+|[-.]+$/g, '')
}

// `keel activity --json` → { dir, runs }: the directory keel read (absolute, or null when the
// payload does not say) and the runs it says are still running, shaped like a status snapshot
// so the band and the pane draw them the same way. Activity is written at every phase a command
// stamps, so it is often newer than the checkpoint, which keel writes only at its safe
// boundaries; and some flows stamp activity without ever writing a checkpoint. `steps` is the
// backbone from a status contract, used when the phase is one of its steps (keel ship).
export function activityRuns(stdout, steps) {
  const payload = JSON.parse(stdout)
  const dir = typeof payload?.path === 'string' && payload.path ? payload.path : null
  const records = Array.isArray(payload?.activity) ? payload.activity : []
  const runs = []
  for (const r of records) {
    if (!r || r.status !== 'running' || !r.run_id) continue
    const onBackbone = steps.some((s) => s.id === r.phase)
    const blocked = r.verdict === 'blocked'
    runs.push({
      fileName: `${runIdSlug(r.run_id)}.json`,
      steps: onBackbone ? steps : [{ id: String(r.phase ?? r.command), name: `(${r.command})` }],
      snapshot: {
        status: blocked ? 'interrupted' : 'active',
        current: {
          run_id: String(r.run_id),
          command: r.command ?? null,
          issue: r.issue ?? null,
          pull_request: r.pr ?? null,
          step: r.phase ?? null,
          wait_reason: blocked ? 'gates blocked' : null,
        },
        source: 'activity',
        note: r.note ? String(r.note) : null,
      },
      who: whoFromRecord(r),
    })
  }
  return { dir, runs }
}

// Who drives a run (#1482): the host, agent, model and effort an activity record carries, and the
// `agent:` / `model:` labels of the run's PR. Only what is known, in that order; activity wins over
// labels. `labels` is the gh label list ([{ name }]); null/undefined when there is none.
export const WHO_FIELDS = ['host', 'agent', 'model', 'effort']
const WHO_MAX = 64

function whoValue(v) {
  if (typeof v !== 'string') return null
  const t = v.trim()
  return t && t.length <= WHO_MAX && !/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(t) ? t : null
}

export function whoFromRecord(record) {
  const who = {}
  for (const f of WHO_FIELDS) {
    const v = whoValue(record?.[f])
    if (v !== null) who[f] = v
  }
  return who
}

export function whoFromLabels(labels) {
  const who = {}
  for (const l of Array.isArray(labels) ? labels : []) {
    const name = typeof l?.name === 'string' ? l.name : ''
    for (const f of ['agent', 'model']) {
      const v = name.startsWith(`${f}:`) ? whoValue(name.slice(f.length + 1)) : null
      if (v !== null && who[f] === undefined) who[f] = v
    }
  }
  return who
}

export function whoText(...sources) {
  const who = Object.assign({}, ...sources)
  return WHO_FIELDS.map((f) => who[f]).filter(Boolean).join(' · ')
}

// "now", "4m", "2h", "3d": how long ago `ms` milliseconds is, for the band and the pane.
export function ago(ms) {
  if (!(ms >= 0)) return null
  const m = Math.floor(ms / 60_000)
  if (m < 1) return 'now'
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h}h`
  return `${Math.floor(h / 24)}d`
}

// https://github.com/<owner>/<repo> from a git remote URL (https or ssh), else null.
// Only github.com itself (an ssh host alias such as `github.com-work` included), and only names
// GitHub allows, so a link built from it is always a valid href (a refused href would make the
// engine refuse the whole band).
const GH_NAME = '[A-Za-z0-9_.-]+'
const GH_REMOTES = [
  new RegExp(`^(?:ssh://)?[A-Za-z0-9_.-]+@github\\.com(?:-[A-Za-z0-9_.-]+)?[:/](${GH_NAME})/(${GH_NAME}?)(?:\\.git)?/?$`),
  new RegExp(`^https?://(?:[^@/\\s]+@)?github\\.com/(${GH_NAME})/(${GH_NAME}?)(?:\\.git)?/?$`),
]

export function githubBase(remote) {
  const text = String(remote ?? '').trim()
  for (const re of GH_REMOTES) {
    const m = text.match(re)
    if (m && !['.', '..'].includes(m[1]) && !['.', '..', ''].includes(m[2])) return `https://github.com/${m[1]}/${m[2]}`
  }
  return null
}

// An href the mod API accepts: https, printable ASCII, no '@', spelled exactly as URL spells it.
export function safeHref(h) {
  if (typeof h !== 'string' || !/^https:\/\/[\x21-\x7e]+$/.test(h) || h.includes('@')) return null
  try {
    return typeof URL === 'function' && new URL(h).href === h ? h : null
  } catch {
    return null
  }
}

// What a checkpoint says about the run's last gate, review and check, for the pane.
export function checkpointDetails(text) {
  try {
    const state = JSON.parse(text)?.state ?? {}
    const out = []
    if (state.last_gate) out.push(['last gate', String(state.last_gate)])
    if (state.last_review) out.push(['last review', String(state.last_review)])
    if (state.last_check) out.push(['last check', String(state.last_check)])
    return out
  } catch {
    return []
  }
}

export function isLive(snapshot) {
  return Boolean(snapshot && LIVE_STATES.has(snapshot.status) && snapshot.current)
}

// One entry per backbone step: 'done' before the current step, 'current' on it, 'pending' after.
export function stepStates(steps, currentStep) {
  const at = steps.findIndex((s) => s.id === currentStep)
  return steps.map((s, i) => ({
    ...s,
    state: at < 0 ? 'pending' : i < at ? 'done' : i === at ? 'current' : 'pending',
  }))
}

export function stepName(steps, id) {
  const found = steps.find((s) => s.id === id)
  return found ? `${id} ${found.name}` : String(id ?? '-')
}

// The pane's lines, top to bottom, each { text, tone }.
export function paneLines(snapshot, steps) {
  if (!snapshot) return [{ text: 'No keel status yet.', tone: 'dim' }]
  const lines = [{ text: `keel — ${snapshot.status}  (${snapshot.project?.repo ?? '?'})`, tone: 'title' }]
  const c = snapshot.current
  if (c) {
    lines.push({
      text: `issue #${c.issue ?? '-'} · PR ${c.pull_request != null ? '#' + c.pull_request : '-'} · ${c.command ?? 'run'}`,
      tone: 'plain',
    })
    for (const s of stepStates(steps, c.step)) {
      if (s.state === 'done') lines.push({ text: `  ✓ ${s.id} ${s.name}`, tone: 'ok' })
      else if (s.state === 'current') {
        const why = c.wait_reason ? ` — ${c.wait_reason}` : ''
        lines.push({ text: `  ▶ ${s.id} ${s.name}${why}`, tone: snapshot.status === 'interrupted' ? 'bad' : 'bar' })
      } else lines.push({ text: `  · ${s.id} ${s.name}`, tone: 'plain' })
    }
  } else {
    lines.push({ text: 'No active run.', tone: 'dim' })
  }
  const n = snapshot.history?.counts ?? {}
  lines.push({
    text: `history: shipped ${n.shipped ?? 0} · blocked ${n.blocked ?? 0} · deferred ${n.deferred ?? 0} · skipped ${n.skipped ?? 0}`,
    tone: 'plain',
  })
  lines.push({ text: `next: ${snapshot.next?.issue != null ? '#' + snapshot.next.issue : '-'}`, tone: 'plain' })
  return lines
}
