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
  const keyOf = (e) => {
    const c = e.snapshot.current
    if (c.run_id) return `run:${c.run_id}`
    // A copy written before the PR was opened has no PR yet: the issue alone names the run.
    if (c.issue != null) return `issue:${c.issue}`
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
  return { kept, superseded: entries.length - kept.length }
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

export function bar(steps, currentStep) {
  return stepStates(steps, currentStep)
    .map((s) => (s.state === 'done' ? '▰' : s.state === 'current' ? '▶' : '▱'))
    .join('')
}

export function stepName(steps, id) {
  const found = steps.find((s) => s.id === id)
  return found ? `${id} ${found.name}` : String(id ?? '-')
}

// The band line's parts, in order. `tone` picks a colour in register.js.
export function bandParts(snapshot, steps) {
  const c = snapshot.current
  const parts = [
    { text: 'keel', tone: 'title' },
    { text: c.issue != null ? ` #${c.issue} ` : ' ', tone: 'plain' },
    { text: bar(steps, c.step), tone: 'bar' },
    { text: ` ${stepName(steps, c.step)}`, tone: 'plain' },
  ]
  if (c.wait_reason) {
    const tone = snapshot.status === 'interrupted' ? 'bad' : 'wait'
    const label = snapshot.status === 'interrupted' ? 'stopped' : 'waiting'
    parts.push({ text: ` · ${label}: ${c.wait_reason}`, tone })
  }
  if (c.pull_request != null) parts.push({ text: ` · PR #${c.pull_request}`, tone: 'dim' })
  return parts
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
      if (s.state === 'done') lines.push({ text: `  ✓ ${s.id} ${s.name}`, tone: 'dim' })
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
