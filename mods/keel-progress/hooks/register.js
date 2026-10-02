// keel-progress: a read-only window on the keel runs of this repository, inside Claude Code.
//
// Parallel `keel ship` runs each live in a worktree of their own and write that worktree's
// checkpoint, so the mod scans `git worktree list` and asks `keel status --json` (the
// consumer-neutral keel.progress-status.v1 contract) about each worktree whose checkpoint
// changed recently. It draws:
//   - one line per live run in the band above the prompt (at most BAND_MAX, then "+N more")
//   - a `/keel-progress` pane with every live run's steps, history counts and next issue
// It never writes to a checkpoint or ledger and never drives a run.

import { bandParts, isLive, paneLines, parseStatus, parseWorktrees } from './view.js'

// Relative paths resolve against the session's working directory.
const PROJECT = '.keel/project.yaml'
// Where a checkpoint lives when the status contract does not say (policy_pack.reports.checkpoint moves it).
const DEFAULT_CHECKPOINT = '.keel/state/checkpoint.json'
const PANE = 'keel-progress'
const POLL_MS = 5_000
// With no live run the timer still ticks every POLL_MS but polls only every IDLE_EVERY ticks (30 s).
const IDLE_EVERY = 6
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
const LABEL_WIDTH = 16

const TONES = {
  title: { bold: true },
  bar: { color: 'cyan' },
  wait: { color: 'yellow' },
  bad: { color: 'red' },
  dim: { dimColor: true },
  plain: {},
}

let hasProject = false
let runs = [] // [{ path, label, snapshot, steps }] — live runs, the session's own first
let own = null // the session's own last good status, live or not: the pane's idle view
let ownStale = false // the last read of the session's own folder failed, so `own` is old
let failures = [] // [{ label, message }] — worktrees whose `keel status` failed
let scanned = 0 // other worktrees with a fresh checkpoint at the last scan
let closedPr = 0 // runs hidden because their pull request is no longer open
let openPrs = null // Set of open PR numbers, or null when `gh` could not say
let openPrsAt = -Infinity
let inFlight = null // the running scan; callers share it instead of starting a second one
let again = false // a fresh scan was asked for while one ran: run once more after it
let idleTicks = 0
let poller = null

// The timer's tick: every time while a run is live or a worktree is failing (so recovery shows
// at once), every IDLE_EVERY-th tick otherwise.
function tick($) {
  if (runs.length === 0 && failures.length === 0) {
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

async function scan($) {
  const now = await $.clock.now()
  const cwd = await $.session.cwd()
  const here = await realPath($, cwd)

  // The session's own folder first, always, whatever its checkpoint's age or location.
  const candidates = []
  const nextFailures = []
  const mine = await statusOf($, null)
  const worktrees = await listWorktrees($)
  let ownLabel = 'here'
  const others = []
  for (const w of worktrees) {
    if ((await realPath($, w.path)) === here) ownLabel = w.label
    else others.push(w)
  }
  if (mine.failure !== undefined) nextFailures.push({ label: ownLabel, message: mine.failure })
  else candidates.push({ path: cwd, label: ownLabel, ...mine.parsed })
  own = mine.parsed ?? own
  ownStale = mine.failure !== undefined

  // Other worktrees: only those whose checkpoint (where this project keeps it) changed lately.
  const checkpoint = mine.parsed?.checkpointPath ?? DEFAULT_CHECKPOINT
  // An absolute checkpoint path is one file every worktree shares: the session's own read covers it.
  const perWorktree = !checkpoint.startsWith('/')
  let fresh = 0
  for (const w of perWorktree ? others : []) {
    try {
      const st = await $.fs.stat(`${w.path}/${checkpoint}`)
      if (now - st.mtimeMs >= FRESH_MS) continue
    } catch {
      continue // no checkpoint: no run in this worktree
    }
    fresh += 1
    const result = await statusOf($, w.path)
    if (result.failure !== undefined) nextFailures.push({ label: w.label, message: result.failure })
    else candidates.push({ path: w.path, label: w.label, ...result.parsed })
  }

  let prs = await loadOpenPrs($, now, false)
  let refetched = false
  const nextRuns = []
  let hidden = 0
  for (const run of candidates) {
    if (!isLive(run.snapshot)) continue
    const pr = run.snapshot.current.pull_request
    if (pr != null && prs !== null && !prs.has(pr) && !refetched) {
      refetched = true
      prs = await loadOpenPrs($, now, true)
    }
    if (pr != null && prs !== null && !prs.has(pr)) {
      hidden += 1
      continue
    }
    nextRuns.push(run)
  }
  runs = nextRuns
  failures = nextFailures
  scanned = fresh
  closedPr = hidden
}

function textProps(part) {
  return { ...TONES[part.tone], wrap: 'truncate', children: [part.text] }
}

function labelPart(run) {
  const label = run.label.length > LABEL_WIDTH ? `${run.label.slice(0, LABEL_WIDTH - 1)}…` : run.label
  return { text: `${label.padEnd(LABEL_WIDTH)} `, tone: 'dim' }
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    hasProject = await $.fs.exists(PROJECT)
    if (hasProject) {
      // Off the start path: the first scan lands a moment after the session opens.
      $.clock.after(0, () => refresh($, true))
      poller?.cancel()
      poller = $.clock.every(POLL_MS, () => tick($))
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

  on('command.run', { command: 'keel-progress' }, async ($) => {
    // Open first: a slow scan must not delay the pane; the scan redraws it.
    await $.ui.open({ id: PANE, title: 'keel', closeOnEscape: true })
    $.clock.after(0, () => refresh($, true))
    return {}
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (runs.length === 0) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const labelled = runs.length > 1
    const lines = runs.slice(0, BAND_MAX).map((run) =>
      Box({
        key: `keel-progress-${run.path}`,
        flexDirection: 'row',
        children: [...(labelled ? [labelPart(run)] : []), ...bandParts(run.snapshot, run.steps)].map((part) =>
          Text(textProps(part)),
        ),
      }),
    )
    if (runs.length > BAND_MAX) {
      lines.push(Text(textProps({ text: `+${runs.length - BAND_MAX} more keel runs · /keel-progress`, tone: 'dim' })))
    }
    // Keep what the mods after this one draw in the band.
    const theirs = await next(e)
    return Box({ flexDirection: 'column', children: theirs ? [...lines, theirs] : lines })
  })

  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE) return next(e)
    const { Box, Text, Button } = $.ui.resolve(e)
    const lines = []
    if (!hasProject) {
      lines.push({ text: `No ${PROJECT} in this directory, so there is no keel run to show.`, tone: 'dim' })
    } else {
      const hiddenNote = closedPr > 0 ? ` · ${closedPr} hidden (PR closed)` : ''
      lines.push({
        text: `${runs.length} live keel run(s) · ${scanned} other worktree(s) with a recent checkpoint${hiddenNote}`,
        tone: 'title',
      })
      for (const run of runs) {
        lines.push({ text: ' ', tone: 'plain' })
        lines.push({ text: `${run.label}  ${run.path}`, tone: 'dim' })
        lines.push(...paneLines(run.snapshot, run.steps))
      }
      if (runs.length === 0 && own !== null) {
        // No live run: the session's own project, as it is (no active run, history, next issue).
        lines.push({ text: ' ', tone: 'plain' })
        // After a failed read this is the last status that worked, and says so (#1446).
        if (ownStale) lines.push({ text: 'last good status:', tone: 'dim' })
        lines.push(...paneLines(own.snapshot, own.steps))
      }
      for (const failure of failures) {
        lines.push({ text: `keel status failed (${failure.label}): ${failure.message}`, tone: 'bad' })
      }
    }
    return Box({
      flexDirection: 'column',
      children: [
        ...lines.map((part) => Text(textProps(part))),
        Box({
          key: 'keel-progress-actions',
          flexDirection: 'row',
          columnGap: 2,
          children: [
            Button({ key: 'refresh', label: 'Refresh', onPress: () => refresh($, true) }),
            Button({ key: 'close', label: 'Close', onPress: () => $.ui.close({ id: PANE }) }),
          ],
        }),
      ],
    })
  })
}
