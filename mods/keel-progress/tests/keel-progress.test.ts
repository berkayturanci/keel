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

type Worktree = {
  path: string
  branch?: string
  mtime?: number | null // checkpoint mtime in ms; null = no checkpoint. Default 0 (fresh: the mock clock starts at 0)
  stdout?: () => string
  exitCode?: () => number
  activity?: Record<string, unknown>[] // keel activity --json records; absent = no .keel/activity
  activityMtime?: number
}

const HOURS = 60 * 60 * 1000

function porcelain(worktrees: Worktree[]): string {
  return worktrees
    .map((w) => `worktree ${w.path}\nHEAD abc\n${w.branch ? `branch refs/heads/${w.branch}` : 'detached'}\n`)
    .join('\n')
}

// Stubs every call the mod makes. `project` decides whether .keel/project.yaml exists; the
// session runs in /work, which is the first worktree unless `worktrees` says otherwise.
// `stdout`/`exitCode`/`stderr` are /work's `keel status` answer.
function stubEngine(
  on: any,
  opts: {
    project: boolean
    stdout?: () => string
    exitCode?: () => number
    stderr?: string
    worktrees?: Worktree[]
    openPrs?: number[] | null | (() => number[] | null) // null: gh fails
    gitFails?: boolean
    gitRejects?: boolean
    realPaths?: Record<string, string> // what fs.stat({ resolve }) answers per path
    checkpointRel?: string // where the project keeps its checkpoint
    clock?: { sleep: (ms: number) => Promise<void> }
    slowMs?: number
  },
) {
  const own: Worktree = { path: '/work', branch: 'main', stdout: opts.stdout, exitCode: opts.exitCode }
  const worktrees = opts.worktrees ?? [own]
  const calls = { status: 0, byPath: {} as Record<string, number>, gh: 0, running: 0, most: 0, opened: [] as string[], closed: [] as string[] }
  on('fs.exists', () => ({ value: opts.project }))
  on('command.register', () => ({ value: undefined }))
  on('session.start', () => ({ cwd: '/work' }))
  on('session.cwd', () => ({ value: '/work' }))
  on('fs.stat', ($: unknown, e: { path: string; resolve?: boolean }) => {
    if (e.resolve) return { value: { kind: 'dir', size: 0, mtimeMs: 0, isLink: false, realPath: opts.realPaths?.[e.path] ?? e.path } }
    const act = worktrees.find((x) => e.path === `${x.path}/.keel/activity` || e.path.startsWith(`${x.path}/.keel/activity/`))
    if (act) {
      if (!act.activity) return { deny: 'ENOENT' }
      return { value: { kind: 'file', size: 10, mtimeMs: act.activityMtime ?? 0, isLink: false } }
    }
    const rel = opts.checkpointRel ?? '.keel/state/checkpoint.json'
    const w = worktrees.find((x) => e.path === `${x.path}/${rel}`)
    const mtime = w?.mtime === undefined ? 0 : w.mtime
    if (!w || mtime === null) return { deny: 'ENOENT' }
    return { value: { kind: 'file', size: 10, mtimeMs: mtime, isLink: false } }
  })
  on('process.run', async ($: unknown, e: { argv: string[]; init?: { cwd?: string } }) => {
    if (e.argv[0] === 'git') {
      if (opts.gitRejects) return { deny: 'spawn git ENOENT' }
      expect(e.argv).toEqual(['git', 'worktree', 'list', '--porcelain'])
      return { value: { exitCode: opts.gitFails ? 128 : 0, stdout: opts.gitFails ? '' : porcelain(worktrees), stderr: '' } }
    }
    if (e.argv[0] === 'gh') {
      calls.gh += 1
      const prs = opts.openPrs === undefined ? [1027] : typeof opts.openPrs === 'function' ? opts.openPrs() : opts.openPrs
      if (prs === null) return { value: { exitCode: 1, stdout: '', stderr: 'gh: not logged in' } }
      return { value: { exitCode: 0, stdout: JSON.stringify(prs.map((number) => ({ number }))), stderr: '' } }
    }
    if (e.argv[1] === 'activity') {
      const at = e.argv[e.argv.indexOf('--root') + 1]
      const w = worktrees.find((x) => x.path === (at === '.' ? '/work' : at))
      return {
        value: {
          exitCode: 0,
          stdout: JSON.stringify({ activity: w?.activity ?? [], contract: { dir: '.keel/activity' } }),
          stderr: '',
        },
      }
    }
    const rootAt = e.argv.indexOf('--root')
    const path = rootAt < 0 ? '/work' : e.argv[rootAt + 1]
    if (rootAt < 0) expect(e.argv).toEqual(['keel', 'status', '.keel/project.yaml', '--json'])
    else expect(e.argv).toEqual(['keel', 'status', `${path}/.keel/project.yaml`, '--root', path, '--json'])
    const w = worktrees.find((x) => x.path === path) ?? own
    calls.status += 1
    calls.byPath[path] = (calls.byPath[path] ?? 0) + 1
    calls.running += 1
    calls.most = Math.max(calls.most, calls.running)
    if (opts.slowMs && opts.clock) await opts.clock.sleep(opts.slowMs)
    calls.running -= 1
    return {
      value: {
        exitCode: w.exitCode ? w.exitCode() : 0,
        stdout: w.stdout ? w.stdout() : statusJson(),
        stderr: opts.stderr ?? 'warning: ledger line 3 skipped\nkeel: boom\n',
      },
    }
  })
  on('ui.open', ($: unknown, e: { id: string }) => {
    calls.opened.push(e.id)
    return { value: { isPlaced: true } }
  })
  on('ui.close', ($: unknown, e: { id: string }) => {
    calls.closed.push(e.id)
    return { value: undefined }
  })
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
    expect(await band.find({ type: 'Button', text: '#1022' })).toBeDefined()
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
  expect(await band.find({ text: /#1022/ })).toBeUndefined()
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
  // keel status takes 8 s here: longer than the 5 s poll
  const calls = stubEngine(on, { project: true, clock, slowMs: 8_000 })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  // A keel command finishes while the first status still runs: that read may predate it,
  // so exactly one more starts once it ends — never alongside it.
  await $.tool.call({ tool: 'Bash', command: 'keel ship .keel/project.yaml --issue 1' })
  await clock.advance(6_000)
  expect(calls.most).toBe(1)
  expect(calls.status).toBe(1)
  await clock.advance(10_000)
  expect(calls.most).toBe(1)
  expect(calls.status).toBe(2)
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
  expect(await pane.find({ type: 'Text', text: '▸ this session · main' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: '/work' })).toBeDefined()
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
  expect(await band.find({ text: /#1022/ })).toBeUndefined()

  await $.command.run({ command: 'keel-progress', args: '' })
  await clock.settle()
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // The fatal last line, not the warning keel printed before it.
  expect(await pane.find({ type: 'Text', text: 'keel status failed (main): keel: boom' })).toBeDefined()
})

test('a run that was showing goes quiet in the band when keel status starts failing', async ($, on) => {
  const clock = mock.clock(on)
  let exit = 0
  stubEngine(on, { project: true, exitCode: () => exit })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  let band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', text: '#1022' })).toBeDefined()
  await band.unmount()

  exit = 1
  await clock.advance(5_000)
  band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ text: /#1022/ })).toBeUndefined()
  await band.unmount()

  exit = 0
  await clock.advance(5_000)
  band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', text: '#1022' })).toBeDefined()
})

test('unparsable keel status output is a pane error, not a crash', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, { project: true, stdout: () => 'not json' })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  await $.command.run({ command: 'keel-progress', args: '' })
  await clock.settle()
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: /^keel status failed \(main\): / })).toBeDefined()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: /keel/ })).toBeUndefined()
})

test('an interrupted run with no issue or PR yet still draws, marked stopped', async ($, on) => {
  const clock = mock.clock(on)
  const current = { command: 'ship', issue: null, pull_request: null, step: 's4', wait_reason: 'gate-failed' }
  stubEngine(on, { project: true, stdout: () => statusJson({ status: 'interrupted', current }) })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: ' s4 implement' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: ' · stopped: gate-failed' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: /PR #/ })).toBeUndefined()

  await $.command.run({ command: 'keel-progress', args: '' })
  await clock.settle()
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: 'issue #- · PR - · ship' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: '  ▶ s4 implement — gate-failed' })).toBeDefined()
})

test('the pane says so when there is no keel project', async ($, on) => {
  mock.clock(on)
  const calls = stubEngine(on, { project: false })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await $.command.run({ command: 'keel-progress', args: '' })
  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await pane.find({ type: 'Text', text: /No \.keel\/project\.yaml in this directory/ })).toBeDefined()
  expect(calls.status).toBe(0)
})

test('Refresh reads keel status again and Close closes the pane', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { project: true })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  await $.command.run({ command: 'keel-progress', args: '' })
  await clock.settle()
  const before = calls.status
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await pane.press({ key: 'refresh' })
  await clock.settle()
  expect(calls.status).toBe(before + 1)
  await pane.press({ key: 'close' })
  expect(calls.closed).toEqual(['keel-progress'])
})

test('a keel binary run by path or quoted still refreshes', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { project: true })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  await $.tool.call({ tool: 'Bash', command: '.venv/bin/keel ship .keel/project.yaml' })
  await clock.settle()
  await $.tool.call({ tool: 'Bash', command: '"keel" status .keel/project.yaml' })
  await clock.settle()
  expect(calls.status).toBe(3)
})

// A live run in another worktree: its own issue and PR.
function runAt(issue: number, pr: number, step = 's7', wait = 'review'): () => string {
  return () => statusJson({ current: { command: 'ship', issue, pull_request: pr, step, wait_reason: wait } })
}

test('parallel runs in other worktrees each get a labelled line, the session’s own first', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, {
    project: true,
    openPrs: [1027, 2001, 2002],
    worktrees: [
      { path: '/wt/a', branch: 'fix/a', stdout: runAt(11, 2001) },
      { path: '/work', branch: 'main' },
      { path: '/wt/b', branch: 'feat/b', stdout: runAt(12, 2002, 's4', '') },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  const labels = await band.findAll({ type: 'Text', text: /^(▸ main|  fix\/a|  feat\/b)\s+$/ })
  expect(labels.map((t: any) => String(t.children[0]).trim())).toEqual(['▸ main', 'fix/a', 'feat/b'])
  expect(await band.find({ type: 'Button', text: '#11' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: ' s4 implement' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: ' · PR #2002' })).toBeDefined()
})

test('a stale checkpoint, a worktree without one, and a run whose PR closed are not shown', async ($, on) => {
  const clock = mock.clock(on, { now: 30 * HOURS })
  const calls = stubEngine(on, {
    project: true,
    openPrs: [2001],
    worktrees: [
      { path: '/work', branch: 'main', mtime: 0 }, // read whatever its age; its PR 1027 is closed
      { path: '/wt/live', branch: 'live', mtime: 30 * HOURS, stdout: runAt(11, 2001) },
      { path: '/wt/stale', branch: 'stale', mtime: 5 * HOURS, stdout: runAt(12, 2001) }, // 25 h old
      { path: '/wt/none', branch: 'none', mtime: null },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  expect(calls.byPath['/work']).toBe(1)
  expect(calls.byPath['/wt/stale']).toBeUndefined()
  expect(calls.byPath['/wt/none']).toBeUndefined()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', text: '#11' })).toBeDefined()
  expect(await band.find({ text: /#1022/ })).toBeUndefined()

  await $.command.run({ command: 'keel-progress', args: '' })
  await clock.settle()
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(
    await pane.find({
      type: 'Text',
      text: '1 live keel run(s) · 1 other worktree(s) with a recent checkpoint · 1 hidden (PR closed)',
    }),
  ).toBeDefined()
})

test('the band shows three runs and counts the rest', async ($, on) => {
  const clock = mock.clock(on)
  const prs = [2001, 2002, 2003, 2004, 2005]
  stubEngine(on, {
    project: true,
    openPrs: prs,
    worktrees: prs.map((pr, i) => ({ path: `/wt/${i}`, branch: `b${i}`, stdout: runAt(10 + i, pr) })),
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', text: '#12' })).toBeDefined()
  expect(await band.find({ type: 'Button', text: '#13' })).toBeUndefined()
  expect(await band.find({ type: 'Text', text: '+2 more keel runs · more, or /keel-progress' })).toBeDefined()
})

test('without gh nothing is hidden on PR grounds, and the open list is cached', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { project: true, openPrs: null })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', text: '#1022' })).toBeDefined()
  await clock.advance(30_000)
  expect(calls.gh).toBe(1)
  await clock.advance(30_000)
  expect(calls.gh).toBe(2)
})

test('outside git the session’s own folder is still read', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { project: true, gitFails: true })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  expect(calls.byPath['/work']).toBe(1)
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', text: '#1022' })).toBeDefined()
  // a single run carries no worktree label
  expect(await band.find({ type: 'Text', text: /^main\s+$/ })).toBeUndefined()
})

test('one failing worktree does not hide the others', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, {
    project: true,
    openPrs: [1027, 2001],
    worktrees: [
      { path: '/work', branch: 'main' },
      { path: '/wt/broken', branch: 'broken', exitCode: () => 1 },
      { path: '/wt/ok', branch: 'ok', stdout: runAt(11, 2001) },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', text: '#1022' })).toBeDefined()
  expect(await band.find({ type: 'Button', text: '#11' })).toBeDefined()
  await $.command.run({ command: 'keel-progress', args: '' })
  await clock.settle()
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: 'keel status failed (broken): keel: boom' })).toBeDefined()
})

test('git that cannot start still leaves the session’s own run, and a throwing scan clears the band', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { project: true, gitRejects: true })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  expect(calls.byPath['/work']).toBe(1)
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', text: '#1022' })).toBeDefined()
})

test('the session’s folder spelled differently by git is not shown twice', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, {
    project: true,
    realPaths: { '/work': '/private/work' },
    worktrees: [{ path: '/private/work', branch: 'main' }],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  expect(calls.status).toBe(1)
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect((await band.findAll({ type: 'Button', text: '#1022' })).length).toBe(1)
})

test('a PR missing from the open list is looked up once more, then on the regular read', async ($, on) => {
  const clock = mock.clock(on)
  let open = [1027]
  const calls = stubEngine(on, {
    project: true,
    openPrs: () => open,
    worktrees: [
      { path: '/work', branch: 'main' },
      { path: '/wt/new', branch: 'new', stdout: runAt(31, 3001) },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  // first scan: 3001 is not open yet, looked up once more, still hidden
  expect(calls.gh).toBe(2)
  // 3001 opens. Timer scans leave it to the regular minute read, but a keel command asks for a
  // fresh scan, which rechecks a PR marked closed once (gh can lag right after gh pr create).
  open = [1027, 3001]
  await clock.advance(5_000)
  expect(calls.gh).toBe(2)
  await $.tool.call({ tool: 'Bash', command: 'keel ship .keel/project.yaml --issue 31' })
  await clock.settle()
  expect(calls.gh).toBe(3)
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', text: '#31' })).toBeDefined()
})

test('other worktrees are found where this project keeps its checkpoint', async ($, on) => {
  const clock = mock.clock(on)
  const withPath = () =>
    JSON.stringify({ ...JSON.parse(statusJson()), contract: { source: { checkpoint: { steps: STEPS, path: 'var/ck.json' } } } })
  const calls = stubEngine(on, {
    project: true,
    openPrs: [1027, 2001],
    checkpointRel: 'var/ck.json',
    worktrees: [
      { path: '/work', branch: 'main', stdout: withPath },
      { path: '/wt/a', branch: 'a', stdout: runAt(11, 2001) },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  expect(calls.byPath['/wt/a']).toBe(1)
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', text: '#11' })).toBeDefined()
})

test('with no live run the pane still shows the project’s history and next issue', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, { project: true, stdout: () => statusJson({ status: 'no-active-run', current: null }) })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  await $.command.run({ command: 'keel-progress', args: '' })
  await clock.settle()
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: 'No active run.' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: /shipped 1 · blocked 1/ })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: 'next: #1030' })).toBeDefined()
})

test('a long branch name is cut with an ellipsis', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, {
    project: true,
    openPrs: [1027, 2001],
    worktrees: [
      { path: '/work', branch: 'main' },
      { path: '/wt/long', branch: 'fix/merge-marks-checkpoint', stdout: runAt(11, 2001) },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  // A narrow band leaves the label its minimum of 12 cells.
  const band = await $.ui.mount({ ...BAND, props: { ...BAND.props, bodyColumns: 70 }, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: '  fix/merge…' })).toBeDefined()
})

test('after a failed read the idle pane marks the project’s status as the last good one', async ($, on) => {
  const clock = mock.clock(on)
  let exit = 0
  stubEngine(on, {
    project: true,
    exitCode: () => exit,
    stdout: () => statusJson({ status: 'no-active-run', current: null }),
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  exit = 1
  await $.command.run({ command: 'keel-progress', args: '' })
  await clock.settle()
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: 'last good status:' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: 'keel status failed (main): keel: boom' })).toBeDefined()
})

test('a step the contract names without a step name shows its id', async ($, on) => {
  const clock = mock.clock(on)
  const steps = STEPS.map((s) => (s.step_id === 's10' ? { step_id: 's10' } : s))
  stubEngine(on, {
    project: true,
    stdout: () => JSON.stringify({ ...JSON.parse(statusJson()), contract: { source: { checkpoint: { steps } } } }),
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: ' s10 s10' })).toBeDefined()
})

test('a run whose PR closed costs no extra gh call per scan', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, {
    project: true,
    openPrs: [2001],
    worktrees: [
      { path: '/work', branch: 'main' }, // live, PR 1027 closed
      { path: '/wt/live', branch: 'live', stdout: runAt(11, 2001) },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  // first scan: the regular read, then one forced refetch for 1027
  expect(calls.gh).toBe(2)
  // a live run keeps the 5 s cadence; 1027 is known closed, so only the minute read calls gh
  await clock.advance(55_000)
  expect(calls.gh).toBe(2)
  await clock.advance(10_000)
  expect(calls.gh).toBe(3)
})

test('one run checkpointed in two worktrees shows once, from the newer checkpoint', async ($, on) => {
  // #3436 on smartinventory: the parent worktree still held s7 while the nested one was at s9.
  const clock = mock.clock(on, { now: 2 * HOURS })
  const run = (step: string) => () =>
    statusJson({ current: { run_id: 'ship-3436', command: 'ship', issue: 3436, pull_request: 2001, step, wait_reason: '' } })
  stubEngine(on, {
    project: true,
    openPrs: [1027, 2001],
    worktrees: [
      { path: '/work', branch: 'main' },
      { path: '/wt/parent', branch: 'claude/workflow', mtime: 1 * HOURS, stdout: run('s7') },
      { path: '/wt/parent/worktrees/issue-3436', branch: 'fix/issue-3436', mtime: 1.5 * HOURS, stdout: run('s9') },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect((await band.findAll({ type: 'Button', text: '#3436' })).length).toBe(1)
  expect(await band.find({ type: 'Text', text: ' s9 fixloop' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: ' s7 review' })).toBeUndefined()
  await $.command.run({ command: 'keel-progress', args: '' })
  await clock.settle()
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: /1 stale copy\(ies\) of a run/ })).toBeDefined()
})

test('when the session’s own folder holds the stale copy, the newer one elsewhere wins', async ($, on) => {
  const clock = mock.clock(on, { now: 2 * HOURS })
  const run = (step: string) => () =>
    statusJson({ current: { run_id: 'ship-9', command: 'ship', issue: 9, pull_request: 1027, step, wait_reason: '' } })
  stubEngine(on, {
    project: true,
    worktrees: [
      { path: '/work', branch: 'main', mtime: 1 * HOURS, stdout: run('s7') },
      { path: '/wt/nested', branch: 'fix/9', mtime: 1.5 * HOURS, stdout: run('s9') },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: ' s9 fixloop' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: ' s7 review' })).toBeUndefined()
})

test('clicking a run in the band opens the pane on that run, with the others to switch to', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, {
    project: true,
    openPrs: [1027, 2001],
    worktrees: [
      { path: '/work', branch: 'main' },
      { path: '/wt/a', branch: 'fix/a-really-long-branch-name-that-the-band-cuts', stdout: runAt(11, 2001) },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  await band.press({ key: 'keel-progress-open-/wt/a' })
  expect(calls.opened).toEqual(['keel-progress'])
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: 'fix/a-really-long-branch-name-that-the-band-cuts' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: '/wt/a' })).toBeDefined()
  const back = await pane.find({ type: 'Button', text: /▸ main · #1022 s10/ })
  expect(back).toBeDefined()
  await pane.press({ key: 'keel-progress-pick-/work' })
  expect(await pane.find({ type: 'Text', text: '▸ this session · main' })).toBeDefined()
})

test('"more" lists every run with its whole branch; "less" folds it back', async ($, on) => {
  const clock = mock.clock(on)
  const prs = [2001, 2002, 2003, 2004]
  stubEngine(on, {
    project: true,
    openPrs: prs,
    worktrees: prs.map((pr, i) => ({ path: `/wt/${i}`, branch: `feature/branch-number-${i}`, stdout: runAt(10 + i, pr) })),
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', text: '#13' })).toBeUndefined()
  await band.press({ key: 'keel-progress-toggle' })
  expect(await band.find({ type: 'Button', text: '#13' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: '    feature/branch-number-3 · /wt/3' })).toBeDefined()
  expect(await band.find({ type: 'Button', text: 'less' })).toBeDefined()
  await band.press({ key: 'keel-progress-toggle' })
  expect(await band.find({ type: 'Button', text: '#13' })).toBeUndefined()
})

test('the session’s run keeps its mark when its own folder held the stale copy', async ($, on) => {
  const clock = mock.clock(on, { now: 2 * HOURS })
  const run = (step: string) => () =>
    statusJson({ current: { run_id: 'ship-9', command: 'ship', issue: 9, pull_request: 1027, step, wait_reason: '' } })
  stubEngine(on, {
    project: true,
    openPrs: [1027, 2001],
    worktrees: [
      { path: '/wt/other', branch: 'other', stdout: runAt(11, 2001) },
      { path: '/work', branch: 'main', mtime: 1 * HOURS, stdout: run('s7') },
      { path: '/wt/nested', branch: 'fix/9', mtime: 1.5 * HOURS, stdout: run('s9') },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  const labels = await band.findAll({ type: 'Text', text: /^(▸ fix\/9|  other)\s+$/ })
  expect(labels.map((t: any) => String(t.children[0]).trim())).toEqual(['▸ fix/9', 'other'])
})

test('a stale copy written before the PR opened is the same run as the newer one with the PR', async ($, on) => {
  const clock = mock.clock(on, { now: 2 * HOURS })
  const copy = (pr: number | null, step: string) => () =>
    statusJson({ current: { command: 'ship', issue: 9, pull_request: pr, step, wait_reason: '' } })
  stubEngine(on, {
    project: true,
    openPrs: [1027, 2001],
    worktrees: [
      { path: '/work', branch: 'main', stdout: () => statusJson({ status: 'no-active-run', current: null }) },
      { path: '/wt/early', branch: 'early', mtime: 1 * HOURS, stdout: copy(null, 's4') },
      { path: '/wt/late', branch: 'late', mtime: 1.5 * HOURS, stdout: copy(2001, 's7') },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect((await band.findAll({ type: 'Button', text: '#9' })).length).toBe(1)
  expect(await band.find({ type: 'Text', text: ' s7 review' })).toBeDefined()
})

test('the band comes back compact after every run is gone', async ($, on) => {
  const clock = mock.clock(on)
  let live = true
  const prs = [2001, 2002, 2003, 2004]
  stubEngine(on, {
    project: true,
    openPrs: prs,
    worktrees: prs.map((pr, i) => ({
      path: `/wt/${i}`,
      branch: `b${i}`,
      stdout: () => (live ? runAt(10 + i, pr)() : statusJson({ status: 'completed', current: null })),
    })),
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  let band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  await band.press({ key: 'keel-progress-toggle' })
  expect(await band.find({ type: 'Button', text: '#13' })).toBeDefined()
  await band.unmount()
  live = false
  await clock.advance(5_000)
  live = true
  await $.tool.call({ tool: 'Bash', command: 'keel ship .keel/project.yaml' })
  await clock.settle()
  band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', text: '#13' })).toBeUndefined()
  expect(await band.find({ type: 'Button', text: 'more' })).toBeDefined()
})

test('a wide-character branch name is cut by terminal cells, not characters', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, {
    project: true,
    openPrs: [1027, 2001],
    worktrees: [
      { path: '/work', branch: 'main' },
      { path: '/wt/cjk', branch: '機能/進捗表示の改善と修正', stdout: runAt(11, 2001) },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  // 12 cells: 2 spaces + 機能 (4) + / (1) + 進捗 (4) = 11, then "…" makes 12.
  const band = await $.ui.mount({ ...BAND, props: { ...BAND.props, bodyColumns: 70 }, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: '  機能/進捗…' })).toBeDefined()
})

test('emoji, flags and zero-width marks are measured in cells too', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, {
    project: true,
    openPrs: [1027, 2001],
    worktrees: [
      { path: '/work', branch: 'main' },
      // flag (two regional indicators, 2) + rocket (2) + "fix" (3) + e + U+0301 combining accent (1)
      // = 8 cells: with the two-space prefix 10, padded to 12
      { path: '/wt/emoji', branch: '\u{1F1F9}\u{1F1F7}\u{1F680}fixe\u0301', stdout: runAt(11, 2001) },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, props: { ...BAND.props, bodyColumns: 70 }, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: '  \u{1F1F9}\u{1F1F7}\u{1F680}fixe\u0301  ' })).toBeDefined()
})

// One keel.activity.v1 record, as `keel activity --json` lists it.
function act(run_id: string, issue: number, phase: string, extra: Record<string, unknown> = {}) {
  return { run_id, command: 'ship', issue, phase, pr: null, status: 'running', verdict: null, ...extra }
}

test('a run that only stamps activity, never a checkpoint, still shows', async ($, on) => {
  // smartinventory's ship-3289: blocked gates at s8, no checkpoint in its worktree.
  const clock = mock.clock(on, { now: 2 * HOURS })
  stubEngine(on, {
    project: true,
    openPrs: [1027, 3312],
    worktrees: [
      { path: '/work', branch: 'main', stdout: () => statusJson({ status: 'no-active-run', current: null }) },
      { path: '/wt/v120', branch: 'v120', mtime: null, activityMtime: 1.5 * HOURS, activity: [act('ship-3289', 3289, 's8', { pr: 3312, verdict: 'blocked' })] },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', text: '#3289' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: ' s8 test' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: ' · stopped: gates blocked' })).toBeDefined()
})

test('activity newer than the checkpoint moves the run on; an older one does not', async ($, on) => {
  const clock = mock.clock(on, { now: 3 * HOURS })
  const ck = () => statusJson({ current: { run_id: 'ship-7', command: 'ship', issue: 7, pull_request: 1027, step: 's6', wait_reason: '' } })
  stubEngine(on, {
    project: true,
    worktrees: [
      { path: '/work', branch: 'main', mtime: 1 * HOURS, stdout: ck, activityMtime: 2 * HOURS, activity: [act('ship-7', 7, 's8', { pr: 1027 })] },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: ' s8 test' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: ' s6 ci' })).toBeUndefined()
  expect((await band.findAll({ type: 'Button', text: '#7' })).length).toBe(1)
})

test('a "running" activity record nobody touched for six hours is not a live run', async ($, on) => {
  const clock = mock.clock(on, { now: 10 * HOURS })
  stubEngine(on, {
    project: true,
    worktrees: [
      { path: '/work', branch: 'main', stdout: () => statusJson({ status: 'no-active-run', current: null }), activityMtime: 3 * HOURS, activity: [act('ship-3306', 3306, 's0')] },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ text: /#3306/ })).toBeUndefined()
})

test('another command\u2019s activity shows with its own phase name', async ($, on) => {
  const clock = mock.clock(on, { now: 2 * HOURS })
  stubEngine(on, {
    project: true,
    openPrs: [1027, 2473],
    worktrees: [
      { path: '/work', branch: 'main' },
      { path: '/wt/pr', branch: 'pr-2473', mtime: null, activityMtime: 1.9 * HOURS, activity: [act('review-cycle-2473', 2473, 'review', { command: 'review-cycle', pr: 2473 })] },
    ],
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: ' review (review-cycle)' })).toBeDefined()
})
