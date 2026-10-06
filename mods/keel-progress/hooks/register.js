// keel-progress: a read-only window on the keel runs of this repository, inside Claude Code.
//
// Parallel `keel ship` runs each live in a worktree of their own and write that worktree's
// checkpoint, so the mod scans `git worktree list` and asks `keel status --json` (the
// consumer-neutral keel.progress-status.v1 contract) about each worktree whose checkpoint
// changed recently. It draws:
//   - one line per live run in the band above the prompt (at most BAND_MAX, then "+N more")
//   - a `/keel-progress` side panel: every live run as a row, the one picked in full
// It never writes to a checkpoint or ledger and never drives a run.

import { FALLBACK_STEPS, activityRuns, ago, cells, checkpointDetails, fitCells, githubBase, isLive, latestPerRun, paneLines, parseStatus, parseWorktrees, safeHref, stepName, stepStates } from './view.js'

// Relative paths resolve against the session's working directory.
const PROJECT = '.keel/project.yaml'
// Where a checkpoint lives when the status contract does not say (policy_pack.reports.checkpoint moves it).
const DEFAULT_CHECKPOINT = '.keel/state/checkpoint.json'
// Where `keel activity` keeps one file per run (keel.activity.v1 contract `dir`).
const ACTIVITY_DIR = '.keel/activity'
// A run stamps its activity at every phase, and nothing marks an abandoned one done: a "running"
// record untouched this long is a run that stopped, not one that is waiting.
const ACTIVITY_FRESH_MS = 6 * 60 * 60 * 1000
// Where this project keeps activity, relative to a worktree: learned from the session's own
// `keel activity --json` (policy_pack.reports.activity can move it), ACTIVITY_DIR until then.
let activityRel = ACTIVITY_DIR
let activityRelKnown = false
const PANE = 'keel-progress'
// With keel status cached and activity read from its files, a scan's only process is the cheap
// `git worktree list`; keel itself starts only when something changed, so every 2 s is cheap.
const POLL_MS = 2_000
// With no live run the timer still ticks every POLL_MS but polls only every IDLE_EVERY ticks (10 s).
const IDLE_EVERY = 5
const STATUS_TIMEOUT_MS = 10_000
// Another worktree whose checkpoint is untouched this long is not scanned. It is longer than
// any merge-window wait, so a run parked at s10 overnight still shows; the session's own
// worktree is always read.
const FRESH_MS = 24 * 60 * 60 * 1000
// `keel merge` used to leave a merged run's checkpoint at s10 (#1448), so a run whose pull
// request is no longer open is hidden. The open list is read at most this often, and once more
// in a scan that meets a PR it does not hold (a PR `keel ship` just opened).
const PR_CACHE_MS = 60_000
const BAND_MAX = 3
// A live run nothing has been written for in this long is drawn as quiet, so a stuck run shows.
const QUIET_MS = 45 * 60 * 1000
// Wait reasons that mean the run is waiting for a person, worth a notification.
const NEEDS_YOU = new Set(['needs-input'])
// Branch labels in the band: at least LABEL_MIN cells, at most LABEL_MAX, else what is left of
// the band's width after BAND_LINE_COLUMNS, the step bar and the card's border and padding.
const LABEL_MIN = 12
const LABEL_MAX = 48
const BAND_LINE_COLUMNS = 64
// The card's border and padding take this many of the band's columns, the keel mark (or the
// spacer under it) and its gap this many more, and the more/less toggle on the first row these.
const CARD_CHROME = 4
const MARK_COLUMNS = 7
const TOGGLE_COLUMNS = 5
// The step bar: two cells per step on a wide band, one on a narrower one, none below that.
const WIDE_BAR_COLUMNS = 120
const NARROW_BAR_COLUMNS = 80

// Text colors are the terminal's own (they follow its theme); a chip is white on a saturated
// background, which reads on a dark and a light theme alike.
const TONES = {
  title: { bold: true },
  bar: { color: 'cyan', bold: true },
  ok: { color: 'green' },
  wait: { color: 'yellow' },
  bad: { color: 'red' },
  live: { color: 'blue' },
  runChip: { backgroundColor: '#1F6FEB', color: '#FFFFFF', bold: true },
  dim: { dimColor: true },
  plain: {},
  // the step bar: one two-cell chip per backbone step
  done: { backgroundColor: '#2D7D46' },
  current: { backgroundColor: '#1F6FEB' },
  stopped: { backgroundColor: '#B62324' },
  todo: { backgroundColor: '#6E7681' },
  // why a run is held: a chip
  waitChip: { backgroundColor: '#9A6700', color: '#FFFFFF' },
  stopChip: { backgroundColor: '#B62324', color: '#FFFFFF', bold: true },
}
const BORDER = '#6E7681'

let hasProject = false
let runs = [] // [{ path, label, snapshot, steps }] — live runs, the session's own first
let own = null // the session's own last good status, live or not: the pane's idle view
let ownStale = false // the last read of the session's own folder failed, so `own` is old
let failures = [] // [{ label, message }] — worktrees whose `keel status` failed
let scanned = 0 // other worktrees with a fresh checkpoint at the last scan
let superseded = 0 // stale copies of a run another worktree holds a newer checkpoint for
let closedPr = 0 // runs hidden because their pull request is no longer open
let openPrs = null // Set of open PR numbers, or null when `gh` could not say
let openPrsAt = -Infinity
// PRs a forced refetch already found closed. Timer scans do not refetch for them again (a merged run's
// PR never reappears, and refetching for it every scan would call GitHub every few seconds);
// the regular once-a-minute read still checks them.
const knownClosed = new Set()
let inFlight = null // the running scan; callers share it instead of starting a second one
// `keel status` costs a Python start (seconds), so its answer is kept per folder while that
// folder's checkpoint is unchanged, for at most STATUS_TTL_MS. A fresh request (a keel command,
// the pane, Refresh) reads every folder anew.
const statusCache = new Map() // path ('' for the session's own) -> { mtimeMs, at, result }
const STATUS_TTL_MS = 30_000
let forceNext = false
let again = false // a fresh scan was asked for while one ran: run once more after it
let idleTicks = 0
let poller = null
// The user's settings (plugin userConfig), with the defaults the manifest declares.
const settings = { pollMs: POLL_MS, bandMax: BAND_MAX, notify: true, sound: false, allSessions: false }
let scanAt = 0 // when the last scan read the runs, for "updated … ago"
let repoBase = null // https://github.com/<owner>/<repo>, once read from `git remote`
let repoBaseKnown = false
let previous = null // runKey -> { issue, step, status, wait } at the last scan; null before the first
// Set by a fresh request (a keel command, a click): the next scan rechecks PRs `knownClosed` holds,
// once, in case `gh pr list` lagged right after `gh pr create`.
let recheckClosed = false
let selected = null // the run (runKey) the pane shows in full
let expanded = false // the band lists every run, each with a second line

// The timer's tick: every time while a run is live or the session's own read is failing (so
// recovery shows at once), every IDLE_EVERY-th tick otherwise. Another worktree that keeps
// failing does not hold the whole scan on the fast cadence.
function tick($) {
  if (runs.length === 0 && !ownStale) {
    idleTicks = (idleTicks + 1) % IDLE_EVERY
    if (idleTicks !== 0) return undefined
  }
  return refresh($, false)
}

// One scan at a time. A caller that needs a read taken after something it just did
// (`fresh`: a keel command, the pane, the button) gets one more scan once the current one
// ends; a timer tick just shares the running one.
function refresh($, fresh) {
  if (!hasProject) return Promise.resolve()
  if (fresh) {
    recheckClosed = true
    forceNext = true
  }
  if (inFlight !== null) {
    if (fresh) again = true
    return inFlight
  }
  inFlight = scanUntilSettled($)
  return inFlight
}

// Clears inFlight in the same step as the last `again` check, so a request that lands
// between them starts a new scan instead of joining one that has already finished.
async function scanUntilSettled($) {
  try {
    do {
      again = false
      await scan($)
    } while (again)
  } catch (err) {
    runs = [] // what the band showed came from a scan that no longer holds
    failures = [{ label: 'scan', message: String(err?.message ?? err) }]
  } finally {
    inFlight = null
    $.ui.invalidate('ui.render')
  }
}

function lastLine(text) {
  const lines = text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
  return lines[lines.length - 1]
}

async function loadOpenPrs($, now, force) {
  if (!force && now - openPrsAt < PR_CACHE_MS) return openPrs
  openPrsAt = now
  try {
    const run = await $.process.run(['gh', 'pr', 'list', '--state', 'open', '--limit', '500', '--json', 'number'], {
      timeoutMs: STATUS_TIMEOUT_MS,
    })
    openPrs = run.exitCode === 0 ? new Set(JSON.parse(run.stdout).map((pr) => pr.number)) : null
  } catch {
    openPrs = null // no gh, no auth, no network: nothing is hidden on PR grounds
  }
  return openPrs
}

// One `keel status`: { parsed } when it answered, { failure } when it could not be read.
// `path` null is the session's own folder, read by the relative project path as before.
async function statusOf($, path) {
  const argv =
    path === null
      ? ['keel', 'status', PROJECT, '--json']
      : ['keel', 'status', `${path}/${PROJECT}`, '--root', path, '--json']
  const init = path === null ? { timeoutMs: STATUS_TIMEOUT_MS } : { cwd: path, timeoutMs: STATUS_TIMEOUT_MS }
  try {
    const run = await $.process.run(argv, init)
    if (run.exitCode !== 0) {
      // keel prints warnings before the fatal message, so the last line is the one that matters
      return { failure: lastLine(run.stderr || run.stdout || '') ?? `exit ${run.exitCode}` }
    }
    if (run.isStdoutTruncated) return { failure: 'keel status --json output is too large to read' }
    return { parsed: parseStatus(run.stdout) }
  } catch (err) {
    return { failure: String(err?.message ?? err) }
  }
}

// One `keel activity --json`: the still-running records stamped in the last ACTIVITY_FRESH_MS,
// as run entries with the time their file was written, and the directory keel read. Activity
// only adds detail, so a failure here is silent; the status read reports a broken worktree.
async function activityOf($, path, base, steps, now) {
  const argv =
    path === null
      ? ['keel', 'activity', PROJECT, '--root', '.', '--json']
      : ['keel', 'activity', `${path}/${PROJECT}`, '--root', path, '--json']
  const init = path === null ? { timeoutMs: STATUS_TIMEOUT_MS } : { cwd: path, timeoutMs: STATUS_TIMEOUT_MS }
  try {
    const run = await $.process.run(argv, init)
    if (run.exitCode !== 0 || run.isStdoutTruncated) return { dir: null, entries: [] }
    const { dir, runs } = activityRuns(run.stdout, steps)
    const where = dir ?? `${base}/${activityRel}`
    const entries = []
    for (const entry of runs) {
      const mtimeMs = await mtimeOf($, `${where}/${entry.fileName}`)
      if (mtimeMs !== null && now - mtimeMs < ACTIVITY_FRESH_MS) entries.push({ ...entry, mtimeMs })
    }
    return { dir, entries }
  } catch {
    return { dir: null, entries: [] }
  }
}

async function mtimeOf($, path) {
  try {
    return (await $.fs.stat(path)).mtimeMs
  } catch {
    return null
  }
}

async function realPath($, path) {
  try {
    return (await $.fs.stat(path, { resolve: true })).realPath ?? path
  } catch {
    return path
  }
}

async function listWorktrees($) {
  try {
    const listed = await $.process.run(['git', 'worktree', 'list', '--porcelain'], { timeoutMs: STATUS_TIMEOUT_MS })
    return listed.exitCode === 0 ? parseWorktrees(listed.stdout) : []
  } catch {
    return [] // no git, or git too slow: the session's own folder is still read
  }
}

async function statusCached($, path, mtimeMs, now, force) {
  const key = path ?? ''
  const hit = statusCache.get(key)
  // Unchanged checkpoint: reuse the answer. The 30 s re-read applies only while a run is live, so
  // an idle session starts no keel process at all.
  // A failed read is never kept past the TTL: one timeout under load must not hide a waiting run.
  const fresh = now - hit?.at < STATUS_TTL_MS
  if (!force && hit && hit.mtimeMs === mtimeMs && (fresh || (runs.length === 0 && hit.result.failure === undefined))) return hit.result
  const result = await statusOf($, path)
  statusCache.set(key, { mtimeMs, at: now, result })
  return result
}

// A folder's activity records, read straight from their files (keel.activity.v1, one JSON
// record per run): no `keel activity` process per scan. Once the project's activity directory is
// known, this is what every scan uses.
async function activityFiles($, base, steps, now) {
  const dir = `${base}/${activityRel}`
  let listed
  try {
    listed = await $.fs.list(dir)
  } catch {
    return { dir: null, entries: [] }
  }
  const entries = []
  for (const f of listed) {
    if (f.kind !== 'file' || !f.name.endsWith('.json') || !(now - f.mtimeMs < ACTIVITY_FRESH_MS)) continue
    let record
    try {
      record = JSON.parse(await $.fs.read(`${dir}/${f.name}`))
    } catch {
      continue // being rewritten, or not a record: the next scan reads it
    }
    // Only keel's own records, as `keel activity` reads them.
    if (record?.schema_version !== 'keel.activity.v1' || record?.record_type !== 'command_activity') continue
    const { runs: found } = activityRuns(JSON.stringify({ activity: [record], path: dir }), steps)
    for (const entry of found) if (entry.fileName === f.name) entries.push({ ...entry, mtimeMs: f.mtimeMs })
  }
  return { dir, entries }
}

async function scan($) {
  const now = await $.clock.now()
  scanAt = now
  const force = forceNext
  forceNext = false
  await learnRepoBase($)
  const cwd = await $.session.cwd()
  const here = await realPath($, cwd)

  // The session's own folder first, always, whatever its checkpoint's age or location.
  const candidates = []
  const nextFailures = []
  const ownCheckpoint = own?.checkpointPath ?? DEFAULT_CHECKPOINT
  const ownCkM = ownCheckpoint.startsWith('/') ? null : await mtimeOf($, `${cwd}/${ownCheckpoint}`)
  const mine = await statusCached($, null, ownCkM, now, force)
  const worktrees = await listWorktrees($)
  let ownLabel = 'here'
  let ownBranch = null
  const others = []
  for (const w of worktrees) {
    const at = await realPath($, w.path)
    if (at === here) {
      ownLabel = w.label
      ownBranch = w.branch
    }
    // A session shows its own runs: its folder and the worktrees keel made under it (keel ship
    // puts a run's worktree inside the session's checkout). The rest belong to other sessions,
    // and are read only when the user asked to see every session's runs.
    else if (settings.allSessions || at.startsWith(`${here}/`)) others.push(w)
  }
  if (mine.failure !== undefined) nextFailures.push({ label: ownLabel, path: cwd, message: mine.failure })
  else candidates.push({ path: cwd, label: ownLabel, branch: ownBranch, own: true, mtimeMs: 0, ...mine.parsed })
  own = mine.parsed ?? own
  ownStale = mine.failure !== undefined

  // Other worktrees: only those whose checkpoint (where this project keeps it) changed lately.
  // A failed own read keeps the last known location, so other worktrees don't drop out.
  const checkpoint = mine.parsed?.checkpointPath ?? own?.checkpointPath ?? DEFAULT_CHECKPOINT
  // Defensive: keel rejects an absolute checkpoint path today. If one ever appears it is one file
  // every worktree shares, which the session's own read already covers.
  const perWorktree = !checkpoint.startsWith('/')
  if (perWorktree && candidates.length > 0) {
    // no checkpoint here: the own entry can only be a live run if keel says so; it ranks oldest
    candidates[0].mtimeMs = (await mtimeOf($, `${cwd}/${checkpoint}`)) ?? 0
  }
  const steps = mine.parsed?.steps ?? own?.steps ?? FALLBACK_STEPS
  const ownFields = { path: cwd, label: ownLabel, branch: ownBranch, own: true }
  // The own folder's activity is read when it changed lately, and once at the start to learn
  // where this project keeps it.
  const ownActM = await mtimeOf($, `${cwd}/${activityRel}`)
  if (!activityRelKnown || (ownActM !== null && now - ownActM < FRESH_MS)) {
    const mineAct = activityRelKnown ? await activityFiles($, cwd, steps, now) : await activityOf($, null, cwd, steps, now)
    if (mineAct.dir !== null && !activityRelKnown) {
      activityRelKnown = true
      if (mineAct.dir.startsWith(`${here}/`)) activityRel = mineAct.dir.slice(here.length + 1)
    }
    for (const entry of mineAct.entries) candidates.push({ ...ownFields, ...entry })
  }

  // Other worktrees: only those whose checkpoint (where this project keeps it) or activity
  // changed lately. A failed own read keeps the last known location, so others don't drop out.
  let fresh = 0
  for (const w of perWorktree ? others : []) {
    const ckM = await mtimeOf($, `${w.path}/${checkpoint}`)
    const actM = await mtimeOf($, `${w.path}/${activityRel}`)
    const ckFresh = ckM !== null && now - ckM < FRESH_MS
    const actFresh = actM !== null && now - actM < FRESH_MS
    if (!ckFresh && !actFresh) continue // nothing written lately: no run in this worktree
    fresh += 1
    const fields = { path: w.path, label: w.label, branch: w.branch, own: false }
    if (ckFresh) {
      const result = await statusCached($, w.path, ckM, now, force)
      if (result.failure !== undefined) nextFailures.push({ label: w.label, path: w.path, message: result.failure })
      else candidates.push({ ...fields, mtimeMs: ckM, ...result.parsed })
    }
    if (actFresh) {
      const act = activityRelKnown ? await activityFiles($, w.path, steps, now) : await activityOf($, w.path, w.path, steps, now)
      for (const entry of act.entries) candidates.push({ ...fields, ...entry })
    }
  }

  // `gh` is asked only when a live run has a pull request, so an idle session makes no API calls.
  // One run can leave checkpoints in several worktrees (a worktree nested in another, a
  // resumed run): the most recently written one is the run's state, the rest are stale copies.
  // Dedupe before the live filter, so a newer finished checkpoint hides an older "running"
  // activity record of the same run (a session that ended without `keel activity --done`).
  const { kept, superseded: dupes } = latestPerRun(candidates.filter((run) => run.snapshot.current))
  const live = kept.filter((run) => isLive(run.snapshot))
  const withPr = live.some((run) => run.snapshot.current.pull_request != null)
  let prs = withPr ? await loadOpenPrs($, now, false) : null
  const recheck = recheckClosed
  recheckClosed = false
  let refetched = false
  const nextRuns = []
  let hidden = 0
  for (const run of live) {
    const pr = run.snapshot.current.pull_request
    if (pr != null && prs !== null && !prs.has(pr) && !refetched && (recheck || !knownClosed.has(pr))) {
      refetched = true
      prs = await loadOpenPrs($, now, true)
      if (prs !== null && !prs.has(pr)) knownClosed.add(pr)
    }
    if (pr != null && prs !== null && prs.has(pr)) knownClosed.delete(pr)
    if (pr != null && prs !== null && !prs.has(pr)) {
      hidden += 1
      continue
    }
    nextRuns.push(run)
  }
  for (const run of nextRuns) {
    if (run.snapshot.source !== 'activity' && perWorktree) run.details = await detailsOf($, `${run.path}/${checkpoint}`)
  }
  announce($, nextRuns, new Set(nextFailures.map((f) => f.path)))
  runs = nextRuns
  if (runs.length === 0) expanded = false // the band comes back compact
  failures = nextFailures
  scanned = fresh
  closedPr = hidden
  superseded = dupes
}

function textProps(part) {
  return { ...TONES[part.tone], wrap: 'truncate', children: [part.text] }
}

// "· 4m" after a run: how long since keel last wrote anything for it. Past QUIET_MS a live run
// is drawn as quiet, in the waiting colour, so a stuck run stands out.
function agePart(run) {
  if (!(run.mtimeMs > 0) || !(scanAt > 0)) return []
  const since = ago(scanAt - run.mtimeMs)
  if (since === null) return []
  return scanAt - run.mtimeMs >= QUIET_MS
    ? [{ text: ` · quiet ${since}`, tone: 'wait' }]
    : [{ text: ` · ${since}`, tone: 'dim' }]
}

async function learnRepoBase($) {
  if (repoBaseKnown) return
  repoBaseKnown = true
  try {
    const run = await $.process.run(['git', 'remote', 'get-url', 'origin'], { timeoutMs: STATUS_TIMEOUT_MS })
    if (run.exitCode === 0) repoBase = githubBase(run.stdout)
  } catch {
    // no git, no remote: the pane shows numbers without links
  }
}

async function detailsOf($, file) {
  try {
    return checkpointDetails(await $.fs.read(file))
  } catch {
    return []
  }
}

// Toasts (and, if the user asked, a sound) for what changed since the last scan: a run that
// stopped, one that waits for a person, one that left the board. Nothing on the first scan.
// A run is followed across scans by what names it, not by where it was read: the issue (stable
// across checkpoint and activity, and across worktrees), else the run id, else the PR.
function identity(run) {
  const c = run.snapshot.current
  if (c.issue != null) return `issue:${c.issue}`
  if (c.run_id) return `run:${c.run_id}`
  if (c.pull_request != null) return `pr:${c.pull_request}`
  return `path:${run.path}`
}

// A run whose worktree could not be read this scan has not left: it is carried over, unannounced,
// until a read says otherwise.
function announce($, nextRuns, failedPaths) {
  const now = new Map(nextRuns.map((run) => [identity(run), run]))
  const carried = new Map()
  if (previous !== null) {
    for (const [key, run] of now) {
      const c = run.snapshot.current
      const was = previous.get(key)
      const label = c.issue != null ? `#${c.issue}` : (c.run_id ?? 'run')
      if (run.snapshot.status === 'interrupted' && was?.status !== 'interrupted') {
        notify($, `keel ${label} stopped at ${c.step ?? '?'}${c.wait_reason ? `: ${c.wait_reason}` : ''}`, 'attention')
      } else if (NEEDS_YOU.has(c.wait_reason) && was?.wait !== c.wait_reason) {
        notify($, `keel ${label} is waiting for you (${c.wait_reason})`, 'attention')
      }
    }
    for (const [key, was] of previous) {
      if (now.has(key)) continue
      if (failedPaths.has(was.path)) carried.set(key, was)
      else notify($, `keel ${was.label} is no longer running (merged, closed or finished)`, 'done')
    }
  }
  previous = new Map([
    ...carried,
    ...[...now].map(([key, run]) => {
      const c = run.snapshot.current
      return [
        key,
        { label: c.issue != null ? `#${c.issue}` : (c.run_id ?? 'run'), path: run.path, status: run.snapshot.status, wait: c.wait_reason },
      ]
    }),
  ])
}

// A notification is a side note: one that fails never costs the scan its runs.
function notify($, text, sound) {
  if (!settings.notify) return
  try {
    Promise.resolve($.ui.toast(text, { timeoutMs: 8000 })).catch(() => {})
    // The sound goes with the toast, never on its own.
    if (settings.sound) $.audio.play({ asset: `fx/${sound}.wav` }).catch(() => {})
  } catch {
    // nothing to do: the band still shows the change
  }
}

// The branch label takes what the band can spare beside the step bar and its text.
function stepCells(bodyColumns) {
  const cols = bodyColumns ?? 0
  return cols >= WIDE_BAR_COLUMNS ? 2 : cols >= NARROW_BAR_COLUMNS ? 1 : 0
}

// One label width for every row, so the issue and bar columns line up: it leaves room for the
// widest bar on screen, the mark, and the toggle the first row carries.
function labelWidth(bodyColumns, steps, toggle) {
  const bar = stepCells(bodyColumns) * steps
  const reserve = BAND_LINE_COLUMNS + CARD_CHROME + MARK_COLUMNS + (toggle ? TOGGLE_COLUMNS : 0) + bar
  return Math.max(LABEL_MIN, Math.min(LABEL_MAX, (bodyColumns ?? 0) - reserve))
}

// With several runs on screen, the session's own one is marked `▸` and drawn bright.
function labelPart(run, width) {
  const name = `${run.own ? '▸ ' : '  '}${run.label}`
  const label = cells(name) > width ? `${fitCells(name, width - 1)}…` : name
  return { text: label + ' '.repeat(Math.max(0, width - cells(label))), tone: run.own ? 'title' : 'dim' }
}

// One worktree can hold several runs (a ship and a review-cycle stamping activity side by side),
// so a run is named by its worktree and its run id (or issue), not the worktree alone.
function runKey(run) {
  const c = run.snapshot.current
  return `${run.path}#${c.run_id ?? c.issue ?? c.pull_request ?? ''}`
}

async function openRun($, key) {
  selected = key
  // No focus: a digit typed into an empty prompt (to answer something else) also presses the
  // band's buttons, and must not take the keyboard away from the prompt.
  await $.ui.open(PANE_OPEN)
  $.ui.invalidate('ui.render')
}

// What `selected` holds after the open run's row is pressed again: no run open, none opened by itself.
const COLLAPSED = Symbol('collapsed')

function selectRun($, key) {
  selected = key
  $.ui.invalidate('ui.render')
}

function toggleExpanded($) {
  expanded = !expanded
  $.ui.invalidate('ui.render')
}

function chip(Text, text, tone) {
  return Text({ ...TONES[tone], wrap: 'truncate', children: [text] })
}

// The backbone as one segmented bar: a two-cell chip per step, done green, the current one blue
// (red when the run stopped there), the rest grey.
function stepBar(ui, run, cellsPerStep = 2) {
  const { Box, Text } = ui
  const stopped = run.snapshot.status === 'interrupted'
  const cell = ' '.repeat(cellsPerStep)
  return Box({
    flexDirection: 'row',
    flexShrink: 0,
    children: stepStates(run.steps, run.snapshot.current.step).map((st) =>
      chip(Text, cell, st.state === 'done' ? 'done' : st.state === 'current' ? (stopped ? 'stopped' : 'current') : 'todo'),
    ),
  })
}

// What a band row shows after the issue button: the step bar, the step, why it is held, the PR
// and how long since keel last wrote.
function bandRow(ui, run, cellsPerStep, found = []) {
  const { Box, Text } = ui
  const c = run.snapshot.current
  // The step name never shrinks; the bar is left out on a band too narrow for it.
  const parts = [
    ...(cellsPerStep > 0 ? [hoverPart(ui, `bar-${hoverScope(run)}`.slice(0, 64), stepBar(ui, run, cellsPerStep), stepProgress(run), found)] : []),
    Box({ flexShrink: 0, children: [chip(Text, stepName(run.steps, c.step), 'title')] }),
  ]
  if (c.wait_reason) {
    const stopped = run.snapshot.status === 'interrupted'
    parts.push(chip(Text, ` ${stopped ? 'stopped' : 'waiting'}: ${c.wait_reason} `, stopped ? 'stopChip' : 'waitChip'))
  }
  // The PR opens on GitHub when the repository is there; otherwise it is plain text.
  if (c.pull_request != null) {
    parts.push(
      prHref(c.pull_request)
        ? Box({ flexShrink: 0, children: [ui.Link({ href: prHref(c.pull_request), label: `PR #${c.pull_request}` })] })
        : chip(Text, `PR #${c.pull_request}`, 'dim'),
    )
  }
  for (const part of agePart(run)) parts.push(chip(Text, part.text.replace(/^ · /, ''), part.tone))
  return parts
}

// Links to a run's PR and issue on GitHub, or null where there is no valid one to make.
function prHref(n) {
  return repoBase !== null && Number.isInteger(n) ? safeHref(`${repoBase}/pull/${n}`) : null
}

function issueHref(n) {
  return repoBase !== null && Number.isInteger(n) ? safeHref(`${repoBase}/issues/${n}`) : null
}

// A run in full, one element per line (a card is these plus its two border lines): its branch,
// worktree, links, the step bar and step, what keel last wrote, and the checkpoint's details.
function runCardRows(ui, e, focus) {
  const { Box, Text, Link } = ui
  const rows = []
  const cline = (part) => rows.push(Text(textProps(part)))
  cline({ text: `${focus.own ? '▸ this session · ' : ''}${focus.branch ?? focus.label}`, tone: 'title' })
  cline({ text: focus.path, tone: 'dim' })
  const c = focus.snapshot.current
  // Links to the PR and the issue, when the repository is on GitHub.
  if (repoBase !== null && (c.pull_request != null || c.issue != null)) {
    rows.push(
      Box({
        key: 'keel-progress-links',
        flexDirection: 'row',
        columnGap: 2,
        children: [
          ...(prHref(c.pull_request) ? [Link({ href: prHref(c.pull_request), label: `PR #${c.pull_request}` })] : []),
          ...(issueHref(c.issue) ? [Link({ href: issueHref(c.issue), label: `issue #${c.issue}` })] : []),
        ],
      }),
    )
  }
  // The card sizes the bar to the panel's width, as the band does: the bar, the card's chrome and
  // a step name must fit.
  const paneCols = (e.props.bodyColumns ?? 0) - 6
  const paneCells = paneCols >= 50 ? 2 : paneCols >= 36 ? 1 : 0
  rows.push(
    Box({
      flexDirection: 'row',
      columnGap: 1,
      children: [
        ...(paneCells > 0 ? [stepBar(ui, focus, paneCells)] : []),
        Box({ flexShrink: 0, children: [chip(Text, stepName(focus.steps, c.step), 'title')] }),
      ],
    }),
  )
  for (const part of paneLines(focus.snapshot, focus.steps).slice(1)) cline(part)
  const since = focus.mtimeMs > 0 && scanAt > 0 ? ago(scanAt - focus.mtimeMs) : null
  if (since !== null) {
    const from = focus.snapshot.source === 'activity' ? 'activity record' : 'checkpoint'
    const quiet = scanAt - focus.mtimeMs >= QUIET_MS
    cline({ text: `last written ${since === 'now' ? 'just now' : `${since} ago`} (${from})${quiet ? ' · quiet' : ''}`, tone: quiet ? 'wait' : 'dim' })
  }
  for (const [name, value] of focus.details ?? []) cline({ text: `${name}: ${value}`, tone: 'plain' })
  if (focus.snapshot.note) cline({ text: `note: ${focus.snapshot.note}`, tone: 'plain' })
  return rows
}

// A part that says more while the pointer is on it. The part sits in a Box that joins a hover
// group of its own; the detail is a hidden Box in the same group, drawn by `reveals` as the band
// row's last child, at the row's right end (absolute: nothing moves, the band keeps its height).
// Drawn last, it paints over what is under it rather than under the parts after it. Inverse
// text reads on a dark and a light theme alike. No hook runs as the pointer moves.
function hoverPart(ui, scope, shown, detail, found) {
  const { Box } = ui
  if (!detail) return shown
  found.push({ scope, detail })
  return Box({ flexShrink: 0, hover: { scope }, children: [shown] })
}

// The hidden details of a band row's parts, each shown while its part is pointed at.
function reveals(ui, list) {
  const { Box, Text } = ui
  return list.map((r) =>
    Box({
      position: 'absolute',
      top: 0,
      right: 0,
      display: 'none',
      hover: { scope: r.scope, display: 'flex' },
      children: [Text({ inverse: true, wrap: 'truncate', children: [` ${r.detail} `] })],
    }),
  )
}

// Where a run is on the backbone: "5 of 13 steps · next s5 classify", for the bar's hover and the panel.
function stepProgress(run) {
  const at = run.steps.findIndex((st) => st.id === run.snapshot.current.step)
  if (at < 0) return null
  const after = run.steps[at + 1]
  return `${at + 1} of ${run.steps.length} steps${after ? ` · next ${stepName(run.steps, after.id)}` : ''}`
}

// The hover group a run's band row and panel row share (1-64 characters).
function hoverScope(run) {
  const c = run.snapshot.current
  return `run-${c.run_id ?? c.issue ?? c.pull_request ?? run.label}`.slice(0, 64)
}

// The pane: the side panel's width when it docks beside a fullscreen transcript.
const PANE_OPEN = { id: PANE, title: 'keel', closeOnEscape: true, columns: 64 }

// Whether a run needs a person: it stopped, or it waits for input.
function needsYou(run) {
  return run.snapshot.status === 'interrupted' || NEEDS_YOU.has(run.snapshot.current.wait_reason)
}

// A run's dot: red when it stopped, yellow when it waits or has gone quiet, blue while it runs.
function runDot(run) {
  if (run.snapshot.status === 'interrupted') return 'bad'
  if (run.snapshot.current.wait_reason || (run.mtimeMs > 0 && scanAt - run.mtimeMs >= QUIET_MS)) return 'wait'
  return 'live'
}

// The right-hand side of a panel row: the step and how long since keel wrote, or why it is held.
function runStatus(ui, run) {
  const { Text } = ui
  const c = run.snapshot.current
  if (run.snapshot.status === 'interrupted') return chip(Text, ` stopped${c.wait_reason ? `: ${c.wait_reason}` : ''} `, 'stopChip')
  if (c.wait_reason) return chip(Text, ` waiting: ${c.wait_reason} `, 'waitChip')
  const age = agePart(run)[0]?.text.replace(/^ · /, '')
  return chip(Text, `◌ ${stepName(run.steps, c.step)}${age ? ` · ${age}` : ''}`, 'runChip')
}

// The dim line under a panel row: how far along, the PR, and where it runs.
function runSummary(run) {
  const c = run.snapshot.current
  return [stepProgress(run), c.pull_request != null ? `PR #${c.pull_request}` : null, run.path].filter(Boolean).join(' · ')
}

// One run in the side panel, as the agents panel draws an agent: a colored dot, the run's name
// (a button that opens it in full below), its step or why it is held on the right, and under it,
// dim, how far along it is.
function paneRow($, ui, run, open) {
  const { Box, Text, Button } = ui
  const c = run.snapshot.current
  return Box({
    key: `keel-progress-row-${runKey(run)}`,
    flexDirection: 'column',
    paddingX: 1,
    hover: { scope: hoverScope(run) },
    children: [
      Box({
        flexDirection: 'row',
        justifyContent: 'space-between',
        columnGap: 1,
        children: [
          Box({
            flexDirection: 'row',
            columnGap: 1,
            flexShrink: 1,
            children: [
              chip(Text, '●', runDot(run)),
              Button({
                key: `keel-progress-pick-${runKey(run)}`,
                label: `${run.own ? '▸ ' : ''}${run.branch ?? run.label}${c.issue != null ? ` · #${c.issue}` : ''}`,
                plain: true,
                // Lit (inverse) while this run is pointed at, here or in the band.
                hover: { scope: hoverScope(run), inverse: true },
                onPress: () => selectRun($, open ? COLLAPSED : runKey(run)),
              }),
              chip(Text, open ? '⌄' : '›', 'dim'),
            ],
          }),
          Box({ flexShrink: 0, children: [runStatus(ui, run)] }),
        ],
      }),
      Box({ paddingLeft: 2, children: [chip(Text, runSummary(run), 'dim')] }),
    ],
  })
}

export function register(on, options = {}) {
  if (Number.isFinite(options.poll_seconds)) settings.pollMs = Math.max(2, Math.min(60, options.poll_seconds)) * 1000
  if (Number.isFinite(options.band_rows)) settings.bandMax = Math.max(1, Math.min(9, options.band_rows))
  if (typeof options.notify === 'boolean') settings.notify = options.notify
  if (typeof options.sound === 'boolean') settings.sound = options.sound
  if (typeof options.all_sessions === 'boolean') settings.allSessions = options.all_sessions
  on('session.start', async ($, e, next) => {
    hasProject = await $.fs.exists(PROJECT)
    if (hasProject) {
      // Off the start path: the first scan lands a moment after the session opens.
      $.clock.after(0, () => refresh($, true))
      // session.start fires once per module load and a reload stops the old timer, so this is
      // belt and braces: never two pollers.
      poller?.cancel()
      poller = $.clock.every(settings.pollMs, () => tick($))
    }
    await $.command.register({
      name: 'keel-progress',
      description: "Show this repository's live keel runs: every step, history counts and the next issue",
      immediate: true,
    })
    return next(e)
  })

  // A keel command may have just moved a run, so scan as soon as it returns. A command run
  // in the background returns at once; the next poll catches what it writes.
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const result = await next(e)
    // A keel invocation (bare, by path, quoted), not `.keel/` or `keel-visual`. An argument
    // that happens to be the word keel costs one extra scan; a miss would leave the band stale.
    if (hasProject && /(^|[\s;&|(/`'"])keel[`'"]?(\s|$)/.test(String(e.command ?? ''))) {
      $.clock.after(0, () => refresh($, true))
    }
    return result
  })

  // The command toggles the side panel: it opens it, or closes it when it is open.
  on('command.run', { command: 'keel-progress' }, async ($) => {
    // A panel already shown closes; one behind another tab is brought forward by the open below.
    let panes = []
    try {
      // An engine without panes() throws here too, and the panel just opens.
      panes = await $.ui.panes()
    } catch {
      panes = []
    }
    if (panes.some((p) => p.id === PANE && p.isShown)) {
      await $.ui.close({ id: PANE })
      return {}
    }
    // Open first: a slow scan must not delay the pane; the scan redraws it.
    selected = null
    await $.ui.open(PANE_OPEN)
    $.clock.after(0, () => refresh($, true))
    return {}
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (runs.length === 0) return next(e)
    const ui = $.ui.resolve(e)
    const { Box, Text, Button } = ui
    const labelled = runs.length > 1
    const shown = expanded ? runs : runs.slice(0, settings.bandMax)
    // As wide as the longest label on screen, within what the band can spare.
    const longest = Math.max(...shown.map((run) => cells(run.label) + 2))
    const barCells = stepCells(e.props.bodyColumns)
    const toggle = runs.length > 1 || expanded
    const widestBar = Math.max(...shown.map((run) => run.steps.length))
    const width = Math.min(labelWidth(e.props.bodyColumns, widestBar, toggle), Math.max(LABEL_MIN, longest))
    const rows = []
    shown.forEach((run, i) => {
      const issue = run.snapshot.current.issue
      const found = []
      rows.push(
        Box({
          key: `keel-progress-${runKey(run)}`,
          // Pointing at a run here lights its name in the side panel, and the other way round.
          hover: { scope: hoverScope(run) },
          flexDirection: 'row',
          columnGap: 1,
          children: [
            // The keel mark heads the first row (no header row of its own: the card is short).
            Box({ flexShrink: 0, children: [chip(Text, i === 0 ? '◆ keel' : '      ', 'title')] }),
            // The label keeps its padded width, so the issue and bar columns line up row to row;
            // only the trailing chips give way on a narrow band.
            ...(labelled ? [Box({ flexShrink: 0, children: [hoverPart(ui, `label-${hoverScope(run)}`.slice(0, 64), Text(textProps(labelPart(run, width))), `${run.branch ?? run.label} · ${run.path}`, found)] })] : []),
            // The issue is a button: click it to open the pane on this run. No digit hotkey: a
            // passive band must not take the first key of a prompt (#1466).
            Box({
              flexShrink: 0,
              children: [
                Button({
                  key: `keel-progress-open-${runKey(run)}`,
                  label: issue != null ? `#${issue}` : run.snapshot.current.step ?? 'run',
                  plain: true,
                  hover: { scope: hoverScope(run), inverse: true },
                  onPress: () => openRun($, runKey(run)),
                }),
              ],
            }),
            ...bandRow(ui, run, barCells, found),
            ...(i === 0 && toggle
              ? [Button({ key: 'keel-progress-toggle', label: expanded ? 'less' : 'more', plain: true, onPress: () => toggleExpanded($) })]
              : []),
            // Last, so each detail paints over the row rather than under the parts after it.
            ...reveals(ui, found),
          ],
        }),
      )
      if (expanded) {
        // The second line carries what the first had to cut: the whole branch and where it runs.
        rows.push(Text({ ...textProps({ text: `       ${run.branch ?? run.label} · ${run.path}`, tone: 'dim' }), wrap: 'truncate-middle' }))
      }
    })
    if (!expanded && runs.length > settings.bandMax) {
      rows.push(Text(textProps({ text: `+${runs.length - settings.bandMax} more keel runs · more, or /keel-progress`, tone: 'dim' })))
    }
    const card = Box({ flexDirection: 'column', borderStyle: 'round', borderColor: BORDER, paddingX: 1, children: rows })
    // Keep what the mods after this one draw in the band.
    const theirs = await next(e)
    return Box({ flexDirection: 'column', children: theirs ? [card, theirs] : [card] })
  })

  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE) return next(e)
    const ui = $.ui.resolve(e)
    const { Box, Text, Button, Link } = ui
    const children = []
    const line = (part) => children.push(Text(textProps(part)))
    if (!hasProject) {
      line({ text: `No ${PROJECT} in this directory, so there is no keel run to show.`, tone: 'dim' })
    } else {
      const held = runs.filter(needsYou)
      const moving = runs.filter((run) => !needsYou(run))
      // The header, as the agents panel heads its list: what this is, and how many are running.
      const count = [moving.length > 0 ? `◌ ${moving.length} running` : null, held.length > 0 ? `${held.length} need you` : null].filter(Boolean).join(' · ')
      children.push(
        Box({
          flexDirection: 'row',
          justifyContent: 'space-between',
          children: [
            Box({ flexDirection: 'row', columnGap: 1, children: [chip(Text, '✦ Keel', 'title'), chip(Text, settings.allSessions ? 'in this repository' : 'in this session', 'dim')] }),
            chip(Text, count || 'idle', held.length > 0 ? 'wait' : runs.length > 0 ? 'live' : 'dim'),
          ],
        }),
      )
      // The run picked is open in full; with none picked, the first one listed (one that needs you
      // before one that runs) is, when its card fits in the rows the panel shows (the engine owns
      // the scroll, so an overflow would push the header out of sight). A row of slack for a long
      // branch name that wraps beside a wide chip.
      const picked = runs.find((run) => runKey(run) === selected)
      const first = held[0] ?? moving[0]
      const listRows = 1 + failures.length + [held, moving].filter((g) => g.length > 0).length + runs.length * 2 + 3 + 1
      const room = e.props.scroll?.bodyRows ?? Infinity
      const focus = picked ?? (selected !== COLLAPSED && first && listRows + runCardRows(ui, e, first).length + 2 <= room ? first : undefined)
      for (const [title, group] of [['NEEDS YOU', held], ['RUNNING', moving]]) {
        if (group.length === 0) continue
        children.push(Box({ flexDirection: 'row', columnGap: 1, children: [chip(Text, title, 'title'), chip(Text, `· ${group.length}`, 'dim')] }))
        for (const run of group) {
          children.push(paneRow($, ui, run, run === focus))
          if (run === focus) {
            children.push(
              Box({
                paddingLeft: 2,
                children: [Box({ flexDirection: 'column', borderStyle: 'round', borderColor: BORDER, paddingX: 1, flexGrow: 1, children: runCardRows(ui, e, run) })],
              }),
            )
          }
        }
      }
      const hiddenNote =
        (closedPr > 0 ? ` · ${closedPr} hidden (PR closed)` : '') +
        (superseded > 0 ? ` · ${superseded} stale copy(ies) of a run` : '')
      line({ text: `${scanned} other worktree(s) with recent keel state${hiddenNote}`, tone: 'dim' })
      if (runs.length === 0 && own !== null) {
        // No live run: the session's own project, as it is (no active run, history, next issue).
        line({ text: ' ', tone: 'plain' })
        // After a failed read this is the last status that worked, and says so (#1446).
        if (ownStale) line({ text: 'last good status:', tone: 'dim' })
        for (const part of paneLines(own.snapshot, own.steps)) line(part)
      }
      for (const failure of failures) {
        line({ text: `keel status failed (${failure.label}): ${failure.message}`, tone: 'bad' })
      }
    }
    children.push(
      Box({
        key: 'keel-progress-actions',
        flexDirection: 'column',
        children: [
          ...(runs.length > 0 ? [chip(Text, 'click a run for its steps · /keel-progress to hide', 'dim')] : []),
          Box({
            flexDirection: 'row',
            columnGap: 2,
            children: [
              Button({ key: 'refresh', label: 'Refresh', onPress: () => refresh($, true) }),
              Button({ key: 'close', label: 'Close', onPress: () => $.ui.close({ id: PANE }) }),
            ],
          }),
        ],
      }),
    )
    return Box({ flexDirection: 'column', children })
  })
}
