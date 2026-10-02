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
    if (path) out.push({ path, label: branch ?? path.split('/').pop() })
  }
  return out
}

// Keeps one entry per run — by run id, else issue and PR — the one whose checkpoint was written
// last (the session's own on a tie), in the input's order. Returns { kept, superseded }.
export function latestPerRun(entries) {
  const best = new Map()
  for (const e of entries) {
    const c = e.snapshot.current
    const key = c.run_id ?? `${c.issue ?? '?'}#${c.pull_request ?? '?'}`
    const prev = best.get(key)
    if (!prev || e.mtimeMs > prev.mtimeMs || (e.mtimeMs === prev.mtimeMs && e.own && !prev.own)) best.set(key, e)
  }
  const keep = new Set(best.values())
  const kept = entries.filter((e) => keep.has(e))
  return { kept, superseded: entries.length - kept.length }
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
