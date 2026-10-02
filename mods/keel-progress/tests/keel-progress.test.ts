import { expect, mock, test } from 'claude-code/testing'

const STEPS = [
  'config', 'select', 'branch', 'guard', 'implement', 'classify', 'ci',
  'review', 'test', 'fixloop', 'merge', 'capture', 'close',
].map((name, i) => ({ step_id: `s${i}`, step_name: name }))

// A trimmed `keel status --json` payload for a run parked at the merge window.
function statusJson(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    contract: { source: { checkpoint: { steps: STEPS } } },
    snapshot: {
      schema_version: 'keel.progress-status.v1',
      status: 'waiting',
      project: { repo: 'keel', base_branch: 'main', timezone: 'Europe/Istanbul' },
      current: {
        command: 'ship',
        issue: 1022,
        pull_request: 1027,
        step: 's10',
        completed_steps: ['s9'],
        wait_reason: 'merge-window',
      },
      history: { total: 2, counts: { shipped: 1, blocked: 1, deferred: 0, skipped: 0 }, items: [] },
      next: { issue: 1030, source: 'checkpoint.queue' },
      ...overrides,
    },
  })
}

const BAND = {
  plugin: 'keel-progress',
  component: 'AbovePrompt',
  viewport: { columns: 120, rows: 40 },
  props: { hasSurvey: false, isWorking: false, maxRows: 6, bodyColumns: 118, scroll: { offset: 0, bodyRows: 6 }, view: {} },
} as const

const PANE = {
  plugin: 'keel-progress',
  component: 'Pane',
  requestId: 'keel-progress',
  viewport: { columns: 120, rows: 40 },
  props: {
    title: 'keel',
    isFocused: true,
    bodyColumns: 60,
    placement: 'inline',
    scroll: { offset: 0, bodyRows: 20 },
    view: {},
  },
} as const

// Stubs every call the mod makes; `project` decides whether .keel/project.yaml exists.
function stubEngine(on: any, opts: { project: boolean; stdout?: () => string; exitCode?: () => number; stderr?: string }) {
  const calls = { status: 0, opened: [] as string[] }
  on('fs.exists', () => ({ value: opts.project }))
  on('command.register', () => ({ value: undefined }))
  on('session.start', () => ({ cwd: '/work' }))
  on('process.run', ($: unknown, e: { argv: string[] }) => {
    expect(e.argv).toEqual(['keel', 'status', '.keel/project.yaml', '--json'])
    calls.status += 1
    return {
      value: {
        exitCode: opts.exitCode ? opts.exitCode() : 0,
        stdout: opts.stdout ? opts.stdout() : statusJson(),
        stderr: opts.stderr ?? 'warning: ledger line 3 skipped\nkeel: boom\n',
      },
    }
  })
  on('ui.open', ($: unknown, e: { id: string }) => {
    calls.opened.push(e.id)
    return { value: { isPlaced: true } }
  })
  on('ui.close', () => ({ value: undefined }))
  on('tool.call', () => ({ result: 'ok' }))
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))
  return calls
}

test('no keel project: no polling and nothing in the band', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { project: false })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.advance(20_000)
  expect(calls.status).toBe(0)

  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: /keel/ })).toBeUndefined()
})

test('a waiting run draws its step bar above the prompt on both surfaces', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, { project: true })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()

  for (const surface of ['terminal', 'desktop'] as const) {
    const band = await $.ui.mount({ ...BAND, surface })
    expect(await band.find({ type: 'Text', text: ' #1022 ' })).toBeDefined()
    expect(await band.find({ type: 'Text', text: '▰▰▰▰▰▰▰▰▰▰▶▱▱' })).toBeDefined()
    expect(await band.find({ type: 'Text', text: ' s10 merge' })).toBeDefined()
    expect(await band.find({ type: 'Text', text: ' · waiting: merge-window' })).toBeDefined()
    expect(await band.find({ type: 'Text', text: ' · PR #1027' })).toBeDefined()
    // The other mods' band drawing is kept under keel's line.
    expect(await band.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
    await band.unmount()
  }
})

test('a finished run leaves the band alone', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, { project: true, stdout: () => statusJson({ status: 'completed' }) })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()

  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: /#1022/ })).toBeUndefined()
})

test('the status is polled on a timer and refreshed after a keel Bash call only', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { project: true })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  expect(calls.status).toBe(1)

  await clock.advance(5_000)
  expect(calls.status).toBe(2)

  await $.tool.call({ tool: 'Bash', command: 'ls -la' })
  await $.tool.call({ tool: 'Bash', command: 'cat .keel/project.yaml && keel-visual --help' })
  await clock.settle()
  expect(calls.status).toBe(2)

  await $.tool.call({ tool: 'Bash', command: 'keel ship .keel/project.yaml --issue 1022' })
  await clock.settle()
  expect(calls.status).toBe(3)
})

test('with no live run the timer polls every 30 s, not every 5 s', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { project: true, stdout: () => statusJson({ status: 'no-active-run', current: null }) })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  expect(calls.status).toBe(1)

  await clock.advance(25_000)
  expect(calls.status).toBe(1)
  await clock.advance(5_000)
  expect(calls.status).toBe(2)

  // A keel command still refreshes at once, so a run that just started shows up.
  await $.tool.call({ tool: 'Bash', command: 'keel ship .keel/project.yaml --issue 1' })
  await clock.settle()
  expect(calls.status).toBe(3)
})

test('a refresh asked for while one runs shares it instead of starting another keel status', async ($, on) => {
  const clock = mock.clock(on)
  let running = 0
  let most = 0
  let calls = 0
  on('fs.exists', () => ({ value: true }))
  on('command.register', () => ({ value: undefined }))
  on('session.start', () => ({ cwd: '/work' }))
  on('tool.call', () => ({ result: 'ok' }))
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))
  on('process.run', async () => {
    calls += 1
    running += 1
    most = Math.max(most, running)
    // keel status takes 8 s here: longer than the 5 s poll
    await clock.sleep(8_000)
    running -= 1
    return { value: { exitCode: 0, stdout: statusJson(), stderr: '' } }
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  // A keel command finishes while the first status still runs: that read may predate it,
  // so exactly one more starts once it ends — never alongside it.
  await $.tool.call({ tool: 'Bash', command: 'keel ship .keel/project.yaml --issue 1' })
  await clock.advance(6_000)
  expect(most).toBe(1)
  expect(calls).toBe(1)
  await clock.advance(10_000)
  expect(most).toBe(1)
  expect(calls).toBe(2)
})

test('/keel-progress opens a pane listing every step, history and the next issue', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { project: true })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()

  const answer = await $.command.run({ command: 'keel-progress', args: '' })
  expect(answer).toEqual({})
  expect(calls.opened).toEqual(['keel-progress'])
  await clock.settle()

  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: 'keel — waiting  (keel)' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: '  ✓ s9 fixloop' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: '  ▶ s10 merge — merge-window' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: '  · s12 close' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: /shipped 1 · blocked 1/ })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: 'next: #1030' })).toBeDefined()
})

test('a failing keel status is reported in the pane, not the band', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, { project: true, exitCode: () => 1 })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()

  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: /#1022/ })).toBeUndefined()

  await $.command.run({ command: 'keel-progress', args: '' })
  await clock.settle()
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // The fatal last line, not the warning keel printed before it.
  expect(await pane.find({ type: 'Text', text: 'keel status failed: keel: boom' })).toBeDefined()
})

test('a run that was showing goes quiet in the band when keel status starts failing', async ($, on) => {
  const clock = mock.clock(on)
  let exit = 0
  stubEngine(on, { project: true, exitCode: () => exit })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  let band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: ' #1022 ' })).toBeDefined()
  await band.unmount()

  exit = 1
  await clock.advance(5_000)
  band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: /#1022/ })).toBeUndefined()
  await band.unmount()

  exit = 0
  await clock.advance(5_000)
  band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: ' #1022 ' })).toBeDefined()
})
