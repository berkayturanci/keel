// keel-progress: a read-only window on the keel run, inside Claude Code.
//
// It shells out to `keel status --json` (the consumer-neutral keel.progress-status.v1
// contract) on a timer and after every Bash call that mentions keel, then draws:
//   - one line in the band above the prompt while a run is active, waiting or interrupted
//   - a `/keel-progress` pane with every backbone step, history counts and the next issue
// It never writes to the checkpoint or ledger and never drives a run.

import { FALLBACK_STEPS, bandParts, isLive, paneLines, parseStatus } from './view.js'

// Relative paths resolve against the session's working directory.
const PROJECT = '.keel/project.yaml'
const PANE = 'keel-progress'
const POLL_MS = 5_000
// With no live run the timer still ticks every POLL_MS but polls only every IDLE_EVERY ticks (30 s).
const IDLE_EVERY = 6
const STATUS_TIMEOUT_MS = 10_000

const TONES = {
  title: { bold: true },
  bar: { color: 'cyan' },
  wait: { color: 'yellow' },
  bad: { color: 'red' },
  dim: { dimColor: true },
  plain: {},
}

let hasProject = false
let status = null // { snapshot, steps } from the last good `keel status --json`
let error = null // first line of the last failure, shown in the pane only
let lastStdout = ''
let inFlight = null // the running refresh; callers share it instead of starting a second `keel status`
let idleTicks = 0

// The timer's tick: every time while a run is live, every IDLE_EVERY-th tick otherwise.
function tick($) {
  if (!isLive(status?.snapshot)) {
    idleTicks = (idleTicks + 1) % IDLE_EVERY
    if (idleTicks !== 0) return undefined
  }
  return refresh($)
}

function refresh($) {
  if (!hasProject) return Promise.resolve()
  if (inFlight === null) {
    inFlight = runStatus($).finally(() => {
      inFlight = null
    })
  }
  return inFlight
}

async function runStatus($) {
  let failure = null
  try {
    const run = await $.process.run(['keel', 'status', PROJECT, '--json'], { timeoutMs: STATUS_TIMEOUT_MS })
    if (run.exitCode !== 0) {
      failure = (run.stderr || run.stdout || `exit ${run.exitCode}`).trim().split('\n')[0]
    } else if (run.stdout !== lastStdout) {
      status = parseStatus(run.stdout)
      lastStdout = run.stdout
    } else if (error === null) {
      return // nothing changed, so skip the redraw
    }
  } catch (err) {
    failure = String(err?.message ?? err)
  }
  error = failure
  $.ui.invalidate('ui.render')
}

function textProps(part) {
  return { ...TONES[part.tone], wrap: 'truncate', children: [part.text] }
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    hasProject = await $.fs.exists(PROJECT)
    if (hasProject) {
      // Off the start path: the first status lands a moment after the session opens.
      $.clock.after(0, () => refresh($))
      $.clock.every(POLL_MS, () => tick($))
    }
    await $.command.register({
      name: 'keel-progress',
      description: 'Show the keel run: every backbone step, history counts and the next issue',
      immediate: true,
    })
    return next(e)
  })

  // A keel command may have just moved the run, so refresh as soon as it returns.
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const result = await next(e)
    if (hasProject && /\bkeel\b/.test(String(e.command ?? ''))) {
      $.clock.after(0, () => refresh($))
    }
    return result
  })

  on('command.run', { command: 'keel-progress' }, async ($) => {
    await refresh($)
    await $.ui.open({ id: PANE, title: 'keel', closeOnEscape: true })
    return {}
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (!isLive(status?.snapshot)) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const line = Box({
      key: 'keel-progress-band',
      flexDirection: 'row',
      children: bandParts(status.snapshot, status.steps).map((part) => Text(textProps(part))),
    })
    // Keep what the mods after this one draw in the band.
    const theirs = await next(e)
    return Box({ flexDirection: 'column', children: theirs ? [line, theirs] : [line] })
  })

  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE) return next(e)
    const { Box, Text, Button } = $.ui.resolve(e)
    const lines = hasProject
      ? paneLines(status?.snapshot, status?.steps ?? FALLBACK_STEPS)
      : [{ text: `No ${PROJECT} in this directory, so there is no keel run to show.`, tone: 'dim' }]
    if (error !== null) lines.push({ text: `keel status failed: ${error}`, tone: 'bad' })
    return Box({
      flexDirection: 'column',
      children: [
        ...lines.map((part) => Text(textProps(part))),
        Box({
          key: 'keel-progress-actions',
          flexDirection: 'row',
          columnGap: 2,
          children: [
            Button({ key: 'refresh', label: 'Refresh', onPress: () => refresh($) }),
            Button({ key: 'close', label: 'Close', onPress: () => $.ui.close({ id: PANE }) }),
          ],
        }),
      ],
    })
  })
}
