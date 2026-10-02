// keel-progress: a read-only window on the keel runs of this repository, inside Claude Code.
//
// Parallel `keel ship` runs each live in a worktree of their own and write that worktree's
// checkpoint, so the mod scans `git worktree list` and asks `keel status --json` (the
// consumer-neutral keel.progress-status.v1 contract) about each worktree whose checkpoint
// changed recently. It draws:
//   - one line per live run in the band above the prompt (at most BAND_MAX, then "+N more")
//   - a `/keel-progress` pane with every live run's steps, history counts and next issue
// It never writes to a checkpoint or ledger and never drives a run.

import { FALLBACK_STEPS, activityRuns, ago, bandParts, checkpointDetails, githubBase, cells, fitCells, latestPerRun, isLive, paneLines, parseStatus, parseWorktrees } from './view.js'

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
// A live run nothing has been written for in this long is drawn as quiet, so a stuck run shows.
const QUIET_MS = 45 * 60 * 1000
// Wait reasons that mean the run is waiting for a person, worth a notification.
const NEEDS_YOU = new Set(['needs-input'])
// Branch labels in the band: at least LABEL_MIN cells, at most LABEL_MAX, else what is left of
// the band's width after BAND_LINE_COLUMNS for the rest of the line.
const LABEL_MIN = 12
const LABEL_MAX = 48
const BAND_LINE_COLUMNS = 64

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
let superseded = 0 // stale copies of a run another worktree holds a newer checkpoint for
let closedPr = 0 // runs hidden because their pull request is no longer open
let openPrs = null // Set of open PR numbers, or null when `gh` could not say
let openPrsAt = -Infinity
// PRs a forced refetch already found closed. Timer scans do not refetch for them again (a merged run's
// PR never reappears, and refetching for it every scan would call GitHub every few seconds);
// the regular once-a-minute read still checks them.
const knownClosed = new Set()
let inFlight = null // the running scan; callers share it instead of starting a second one
let again = false // a fresh scan was asked for while one ran: run once more after it
let idleTicks = 0
let poller = null
// The user's settings (plugin userConfig), with the defaults the manifest declares.
const settings = { pollMs: POLL_MS, bandMax: BAND_MAX, notify: true, sound: false }
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
  if (fresh) recheckClosed = true
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

async function scan($) {
  const now = await $.clock.now()
  scanAt = now
  await learnRepoBase($)
  const cwd = await $.session.cwd()
  const here = await realPath($, cwd)

  // The session's own folder first, always, whatever its checkpoint's age or location.
  const candidates = []
  const nextFailures = []
  const mine = await statusOf($, null)
  const worktrees = await listWorktrees($)
  let ownLabel = 'here'
  let ownBranch = null
  const others = []
  for (const w of worktrees) {
    if ((await realPath($, w.path)) === here) {
      ownLabel = w.label
      ownBranch = w.branch
    }
    else others.push(w)
  }
  if (mine.failure !== undefined) nextFailures.push({ label: ownLabel, message: mine.failure })
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
    const mineAct = await activityOf($, null, cwd, steps, now)
    if (mineAct.dir !== null) {
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
      const result = await statusOf($, w.path)
      if (result.failure !== undefined) nextFailures.push({ label: w.label, message: result.failure })
      else candidates.push({ ...fields, mtimeMs: ckM, ...result.parsed })
    }
    if (actFresh) for (const entry of (await activityOf($, w.path, w.path, steps, now)).entries) candidates.push({ ...fields, ...entry })
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
  announce($, nextRuns)
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
function announce($, nextRuns) {
  const now = new Map(nextRuns.map((run) => [runKey(run), run]))
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
      if (!now.has(key)) notify($, `keel ${was.label} is no longer running (merged, closed or finished)`, 'done')
    }
  }
  previous = new Map(
    [...now].map(([key, run]) => {
      const c = run.snapshot.current
      return [key, { label: c.issue != null ? `#${c.issue}` : (c.run_id ?? 'run'), status: run.snapshot.status, wait: c.wait_reason }]
    }),
  )
}

// A notification is a side note: one that fails never costs the scan its runs.
function notify($, text, sound) {
  try {
    if (settings.notify) $.ui.toast(text, { timeoutMs: 8000 })
    if (settings.sound) $.audio.play({ asset: `fx/${sound}.wav` }).catch(() => {})
  } catch {
    // nothing to do: the band still shows the change
  }
}

// The branch label takes what the band can spare beside the step bar and its text.
function labelWidth(bodyColumns) {
  return Math.max(LABEL_MIN, Math.min(LABEL_MAX, (bodyColumns ?? 0) - BAND_LINE_COLUMNS))
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
  await $.ui.open({ id: PANE, title: 'keel', closeOnEscape: true })
  $.ui.invalidate('ui.render')
}

function selectRun($, key) {
  selected = key
  $.ui.invalidate('ui.render')
}

function toggleExpanded($) {
  expanded = !expanded
  $.ui.invalidate('ui.render')
}

export function register(on, options = {}) {
  if (Number.isFinite(options.poll_seconds)) settings.pollMs = Math.max(2, options.poll_seconds) * 1000
  if (Number.isFinite(options.band_rows)) settings.bandMax = Math.max(1, Math.min(9, options.band_rows))
  if (typeof options.notify === 'boolean') settings.notify = options.notify
  if (typeof options.sound === 'boolean') settings.sound = options.sound
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

  on('command.run', { command: 'keel-progress' }, async ($) => {
    // Open first: a slow scan must not delay the pane; the scan redraws it.
    selected = null
    await $.ui.open({ id: PANE, title: 'keel', closeOnEscape: true })
    $.clock.after(0, () => refresh($, true))
    return {}
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (runs.length === 0) return next(e)
    const { Box, Text, Button } = $.ui.resolve(e)
    const labelled = runs.length > 1
    const shown = expanded ? runs : runs.slice(0, settings.bandMax)
    // As wide as the longest label on screen, within what the band can spare.
    const longest = Math.max(...shown.map((run) => cells(run.label) + 2))
    const width = Math.min(labelWidth(e.props.bodyColumns), Math.max(LABEL_MIN, longest))
    const lines = []
    shown.forEach((run, i) => {
      // The issue is a button: click it, or type its digit into an empty prompt, to open the
      // pane on this run.
      const issue = run.snapshot.current.issue
      const open = Button({
        key: `keel-progress-open-${runKey(run)}`,
        label: issue != null ? `#${issue}` : run.snapshot.current.step ?? 'run',
        plain: true,
        ...(i < 9 ? { hotkey: String(i + 1) } : {}),
        onPress: () => openRun($, runKey(run)),
      })
      const toggle =
        i === 0 && (runs.length > 1 || expanded)
          ? [
              Button({
                key: 'keel-progress-toggle',
                label: expanded ? 'less' : 'more',
                plain: true,
                onPress: () => toggleExpanded($),
              }),
            ]
          : []
      lines.push(
        Box({
          key: `keel-progress-${runKey(run)}`,
          flexDirection: 'row',
          columnGap: 1,
          children: [
            ...(labelled ? [Text(textProps(labelPart(run, width)))] : []),
            Text(textProps({ text: 'keel', tone: 'title' })),
            open,
            ...bandParts(run.snapshot, run.steps).slice(2).map((part) => Text(textProps(part))),
            ...agePart(run).map((part) => Text(textProps(part))),
            ...toggle,
          ],
        }),
      )
      if (expanded) {
        // The second line carries what the first had to cut: the whole branch and where it runs.
        lines.push(
          Text({
            ...textProps({ text: `    ${run.branch ?? run.label} · ${run.path}`, tone: 'dim' }),
            wrap: 'truncate-middle',
          }),
        )
      }
    })
    if (!expanded && runs.length > settings.bandMax) {
      lines.push(Text(textProps({ text: `+${runs.length - settings.bandMax} more keel runs · more, or /keel-progress`, tone: 'dim' })))
    }
    // Keep what the mods after this one draw in the band.
    const theirs = await next(e)
    return Box({ flexDirection: 'column', children: theirs ? [...lines, theirs] : lines })
  })

  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE) return next(e)
    const { Box, Text, Button, Link } = $.ui.resolve(e)
    const children = []
    const line = (part) => children.push(Text(textProps(part)))
    if (!hasProject) {
      line({ text: `No ${PROJECT} in this directory, so there is no keel run to show.`, tone: 'dim' })
    } else {
      const hiddenNote =
        (closedPr > 0 ? ` · ${closedPr} hidden (PR closed)` : '') +
        (superseded > 0 ? ` · ${superseded} stale copy(ies) of a run` : '')
      line({
        text: `${runs.length} live keel run(s) · ${scanned} other worktree(s) with recent keel state${hiddenNote}`,
        tone: 'title',
      })
      // The run picked in the band (or the first) in full; the others as buttons to switch to.
      const focus = runs.find((run) => runKey(run) === selected) ?? runs[0]
      if (focus) {
        line({ text: ' ', tone: 'plain' })
        line({ text: `${focus.own ? '▸ this session · ' : ''}${focus.branch ?? focus.label}`, tone: 'title' })
        line({ text: focus.path, tone: 'dim' })
        const c = focus.snapshot.current
        // Links to the PR and the issue, when the repository is on GitHub.
        if (repoBase !== null && (c.pull_request != null || c.issue != null)) {
          children.push(
            Box({
              key: 'keel-progress-links',
              flexDirection: 'row',
              columnGap: 2,
              children: [
                ...(c.pull_request != null ? [Link({ href: `${repoBase}/pull/${c.pull_request}`, label: `PR #${c.pull_request}` })] : []),
                ...(c.issue != null ? [Link({ href: `${repoBase}/issues/${c.issue}`, label: `issue #${c.issue}` })] : []),
              ],
            }),
          )
        }
        for (const part of paneLines(focus.snapshot, focus.steps).slice(1)) line(part)
        const since = focus.mtimeMs > 0 && scanAt > 0 ? ago(scanAt - focus.mtimeMs) : null
        if (since !== null) {
          const from = focus.snapshot.source === 'activity' ? 'activity record' : 'checkpoint'
          const quiet = scanAt - focus.mtimeMs >= QUIET_MS
          line({ text: `last written ${since === 'now' ? 'just now' : `${since} ago`} (${from})${quiet ? ' · quiet' : ''}`, tone: quiet ? 'wait' : 'dim' })
        }
        for (const [name, value] of focus.details ?? []) line({ text: `${name}: ${value}`, tone: 'plain' })
        if (focus.snapshot.note) line({ text: `note: ${focus.snapshot.note}`, tone: 'plain' })
      }
      const others = runs.filter((run) => run !== focus)
      if (others.length > 0) {
        line({ text: ' ', tone: 'plain' })
        line({ text: 'Other runs:', tone: 'dim' })
        for (const run of others) {
          const c = run.snapshot.current
          children.push(
            Button({
              key: `keel-progress-pick-${runKey(run)}`,
              label: `${run.own ? '▸ ' : ''}${run.branch ?? run.label} · #${c.issue ?? '-'} ${c.step ?? ''}`,
              plain: true,
              onPress: () => selectRun($, runKey(run)),
            }),
          )
        }
      }
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
        flexDirection: 'row',
        columnGap: 2,
        children: [
          Button({ key: 'refresh', label: 'Refresh', onPress: () => refresh($, true) }),
          Button({ key: 'close', label: 'Close', onPress: () => $.ui.close({ id: PANE }) }),
        ],
      }),
    )
    return Box({ flexDirection: 'column', children })
  })
}
