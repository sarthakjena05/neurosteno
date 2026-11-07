import { act, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ComparePage from '../components/ComparePage'
import { benchArm, benchFor, estimatedTime, measuredFromSession, type Bench, type CompareData } from '../compare'
import type { RoutingPlan, State } from '../types'
import { AB_SEED, captureConditions, conditionsMatch, startBlocker, useLiveAB } from '../useLiveAB'
import { mockState } from './mockState'

const DATA: CompareData = {
  scenarios: [{ id: 'A', profile: 'new-patient', title: 'New patient' }, { id: 'B', profile: 'mobility', title: 'Mobility' }],
  messages: { 'turn-left': 'comfort/position/turn-left', 'suction-now': 'breathing/suction-mouth/now' },
  rows: [
    { scenario: 'B', profile: 'mobility', n: 2, message: 'turn-left', text: 'Please turn me to my left.', static: 11, adaptive: 5, delta: -6 },
    { scenario: 'A', profile: 'new-patient', n: 2, message: 'turn-left', text: 'Please turn me to my left.', static: 11, adaptive: 11, delta: 0 },
    { scenario: 'B', profile: 'mobility', n: 2, message: 'suction-now', text: 'Suction now.', static: 8, adaptive: 9, delta: 1 },
  ],
}

const plan = (): RoutingPlan => ({
  mode: 'adaptive', n: 2, shortcuts: [], urgent: ['breathing/suction-mouth/now'],
  candidates: [{ node_id: 'comfort/position/turn-left', title: 'Turn me left', score: 0.4, reasons: ['frequent in history'], included: true, why: 'lowers E[time] by 9 s', delta_selections: -0.8 }],
  expected: { selections: 6, static_selections: 8 }, reliability: {},
  verification: { policy: 'explicit', requested: 'explicit', reason: '', final_message: 'explicit' },
  recommended_n: { n: 2, note: 'assumption until calibrated', by_n: {
    2: { expected_selections: 6, expected_time_s: 60, selection_time_s: 10, reliability_source: 'assumption' },
    3: { expected_selections: 5, expected_time_s: 70, selection_time_s: 14, reliability_source: 'assumption' },
    5: { expected_selections: 4, expected_time_s: 90, selection_time_s: 22.5, reliability_source: 'assumption' } } },
  top_requests: [{ node_id: 'x', title: 'Turn me left', p: 0.12 }], explanation: [], computed_ms: 1,
})

let fetchMock: ReturnType<typeof vi.fn>
const posted = () => fetchMock.mock.calls.filter(([, i]) => i?.method === 'POST').map(([, i]) => JSON.parse(String(i!.body)))
beforeEach(() => {
  fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/routing/compare') return new Response(JSON.stringify(DATA))
    if (url === '/api/benchmark') return new Response(JSON.stringify({ available: false }))
    return new Response('{}')
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => vi.unstubAllGlobals())

const noAB = { run: null, start: vi.fn(), stop: vi.fn(), active: false }

describe('comparison data never invents numbers', () => {
  it('time estimate prefers the backend row estimate, else the routing model, else nothing', () => {
    const s = mockState({ routing: plan() })
    expect(estimatedTime({ ...DATA.rows[0], est_time_static_s: 123, time_source: 'calibrated' }, 'static', s)).toMatchObject({ value: 123, tag: 'ESTIMATE', source: 'calibrated' })
    expect(estimatedTime(DATA.rows[0], 'adaptive', s)).toMatchObject({ value: 50, tag: 'ESTIMATE' })
    expect(estimatedTime(DATA.rows[0], 'static', mockState())).toBeNull()
  })

  it('messages/min only when correctness was scored', () => {
    const s = mockState({ metrics: { recent: [], summary: [
      { origin: 'simulated', source: 'simulated', routing_mode: 'adaptive', profile: 'mobility', n: 2, messages: 2, mean_completion_s: 60, correct_final: '2/2' },
      { origin: 'simulated', source: 'simulated', routing_mode: 'static', profile: 'mobility', n: 2, messages: 1, mean_completion_s: 120, correct_final: 'unknown' },
    ] } })
    expect(measuredFromSession(s, 'mobility', 2, 'adaptive')[0]).toMatchObject({ correct_per_min: 1, origin: 'simulated' })
    expect(measuredFromSession(s, 'mobility', 2, 'static')[0].correct_per_min).toBeNull()
    expect(measuredFromSession(s, 'new-patient', 2, 'adaptive')).toEqual([])
  })

  it('benchmark rows only when published, matched on task + signal level', () => {
    expect(benchFor({ available: false }, 'B', 'turn-left', 'medium')).toEqual([])
    expect(benchFor(BENCH, 'B', 'turn-left', 'medium').map((r) => r.condition)).toEqual(['A2', 'B2', 'D'])
    expect(benchFor(BENCH, 'B', 'turn-left', 'low')).toEqual([])
    expect(benchArm(BENCH, 'B', 'turn-left', 'medium', 'static', 2)?.condition).toBe('A2')
    expect(benchArm(BENCH, 'B', 'turn-left', 'medium', 'adaptive', 2)?.condition).toBe('B2')
  })
})

const brow = (condition: string, n: number, cmpm: number, extra: object = {}) => ({
  condition, scenario: 'B', profile: 'mobility', message: 'turn-left', snr_level: 'medium', snr_uv: 1.4, n_start: n, n_start_counts: { [n]: 10 },
  runs: 10, mean_selections: n === 2 ? 30 : 2, mean_completion_s: 60, correct: 10, wrong: 0, not_completed: 0, correct_msgs_per_min: cmpm, final_sent_without_confirm: 0, origin: 'simulated', ...extra,
})
const BENCH: Bench = {
  available: true, kind: 'simulated', data_origin: 'simulated', real_eeg: false, generated_at: '2026-11-07T14:49:38',
  disclaimer: 'SIMULATION ONLY. Synthetic EEG.', limitations: ['A* is chosen in hindsight (oracle), which favours the baseline.'], command: 'python scripts/run_benchmark.py',
  conditions: [
    { key: 'A2', label: 'A: fixed menu, 2 targets', routing: 'static', n: 2, decoder: 'fixed', step_confirm: false, live_adapt: false, confirm_gate: null },
    { key: 'B2', label: 'B: context routing, 2 targets', routing: 'adaptive', n: 2, decoder: 'fixed', step_confirm: false, live_adapt: false, confirm_gate: null },
    { key: 'D', label: 'D: full NeuroSteno', routing: 'adaptive', n: null, decoder: 'dynamic', step_confirm: true, live_adapt: true, confirm_gate: null },
  ],
  per_message: { snr_levels_uv: { low: 1.0, medium: 1.4, high: 2.0 }, rows: [brow('A2', 2, 0.33, { correct: 7, not_completed: 3 }), brow('B2', 2, 1.02), brow('D', 5, 2.03)] },
  summary: { pooled: [
    { condition: 'A5', sessions: 120, cmpm_mean: 1.06, cmpm_ci95: [0.97, 1.16], error_rate: 0.0288, false_confirmations_task: 68, messages_sent: 2361, idle_false_sent_per_hour: 53.91, urgent_completed: '698/720' },
    { condition: 'D', sessions: 120, cmpm_mean: 0.76, cmpm_ci95: [0.67, 0.86], error_rate: 0, false_confirmations_task: 0, messages_sent: 2015, idle_false_sent_per_hour: 0, urgent_completed: '676/720' },
  ] },
}

describe('judge comparison view', () => {
  it('shows the same task side by side with exact counts and a dot per selection', async () => {
    render(<ComparePage s={mockState({ routing: plan() })} ab={noAB} />)
    await waitFor(() => expect(screen.getByTestId('sel-static')).toHaveTextContent('11'))
    expect(screen.getByTestId('sel-adaptive')).toHaveTextContent('5')
    expect(screen.getByTestId('dots-static').children).toHaveLength(11)
    expect(screen.getByTestId('dots-adaptive').children).toHaveLength(5)
    expect(screen.getByTestId('delta')).toHaveTextContent('6 fewer brain selections (-55%)')
    expect(screen.getByText('Traditional fixed menu')).toBeInTheDocument()
    expect(screen.getByText('NeuroSteno')).toBeInTheDocument()
    expect(screen.getByText('“Please turn me to my left.”')).toBeInTheDocument()
  })

  it('shows "not measured" instead of a number when nothing exists', async () => {
    render(<ComparePage s={mockState({ routing: plan() })} ab={noAB} />)
    await waitFor(() => screen.getByTestId('arm-static'))
    expect(within(screen.getByTestId('arm-static')).getByText(/no benchmark row/)).toBeInTheDocument()
    expect(within(screen.getByTestId('arm-adaptive')).queryByText(/MEASURED ·/)).toBeNull()
    expect(screen.getByText(/Not published yet/)).toBeInTheDocument()
  })

  it('scopes session measurements honestly (all messages, not this task)', async () => {
    const s = mockState({ routing: plan(), metrics: { recent: [], summary: [
      { origin: 'simulated', source: 'simulated', routing_mode: 'adaptive', profile: 'mobility', n: 2, messages: 1, mean_completion_s: 45, correct_final: '1/1' }] } })
    render(<ComparePage s={s} ab={noAB} />)
    await waitFor(() => screen.getByTestId('arm-adaptive'))
    const arm = screen.getByTestId('arm-adaptive')
    expect(within(arm).getByText('MEASURED · SIMULATED')).toBeInTheDocument()
    expect(within(arm).getByText(/saved runs on this machine, all Mobility messages at 2 targets/)).toBeInTheDocument()
    expect(within(arm).getByText(/correct msgs\/min/)).toHaveTextContent('1.33 correct msgs/min')
  })

  it('is honest when NeuroSteno does not help or costs a step', async () => {
    render(<ComparePage s={mockState({ routing: plan() })} ab={noAB} />)
    await waitFor(() => screen.getByTestId('scenario-table'))
    act(() => screen.getByRole('radio', { name: 'A · New patient' }).click())
    expect(screen.getByTestId('delta')).toHaveTextContent('Same count')
    act(() => screen.getByRole('radio', { name: 'B · Mobility' }).click())
    act(() => screen.getByRole('radio', { name: 'Suction now' }).click())
    expect(screen.getByTestId('delta')).toHaveTextContent('+1')
    expect(screen.getByTestId('delta')).toHaveTextContent('urgent routes')
  })

  it('explains the target count from the routing model and outlines the live phase', async () => {
    render(<ComparePage s={mockState({ routing: plan(), phase: 'PREDICTION_HIGHLIGHTED' })} ab={noAB} />)
    await waitFor(() => screen.getByTestId('why-targets-bars'))
    expect(screen.getByTestId('why-targets-bars')).toHaveTextContent('2 targets · recommended')
    expect(screen.getByTestId('targets-verdict')).toHaveTextContent('model recommends 2 · in use 5')
    expect(screen.getByTestId('why-candidates')).toHaveTextContent('shortcut Turn me left')
    const active = within(screen.getByTestId('flow')).getAllByRole('listitem').filter((li) => li.dataset.active === 'true')
    expect(active).toHaveLength(1)
    expect(active[0]).toHaveTextContent('Highlight, same screen')
  })

  it('keeps the REAL/SIMULATED badge and only allows the live demo in simulation', async () => {
    const real = mockState({ routing: plan(), mode: { kind: 'real', label: 'REAL EEG', origin: 'real', is_real_eeg: true, replay_of: null, calibrated: false, calibration_required: true } })
    render(<ComparePage s={real} ab={noAB} />)
    await waitFor(() => screen.getByText(/Run live: NeuroSteno, then fixed menu/))
    expect(screen.getByTestId('mode-badge')).toHaveTextContent('REAL EEG')
    expect(screen.getByText(/Run live: NeuroSteno, then fixed menu/).closest('button')).toBeDisabled()
    expect(screen.getByTestId('ab-blocker')).toHaveTextContent('SIMULATED mode only')
  })
})

describe('backend v1.3 integration (B1/B2/B3)', () => {
  const withV13 = () => {
    const rows = DATA.rows.map((r) => r.scenario === 'B' && r.message === 'turn-left'
      ? { ...r, est_time_static_s: 120.5, est_time_adaptive_s: 60.2, time_source: 'assumption',
          path_static: [{ label: 'Daily needs · People', kind: 'group' }, { label: 'Comfort', kind: 'option' }],
          path_adaptive: [{ label: 'Daily needs · People', kind: 'group' }, { label: 'Turn me left', kind: 'shortcut' }] }
      : r)
    fetchMock.mockImplementation(async (url: string) => {
      if (url === '/api/routing/compare') return new Response(JSON.stringify({ ...DATA, rows }))
      if (url === '/api/benchmark') return new Response(JSON.stringify(BENCH))
      return new Response('{}')
    })
  }

  it('B1: shows the actual route with the shortcut marked', async () => {
    withV13()
    render(<ComparePage s={mockState()} ab={noAB} />)
    await waitFor(() => screen.getByTestId('path-adaptive'))
    expect(screen.getByTestId('path-adaptive').querySelector('.bg-neuro')).toHaveTextContent('Turn me left')
    expect(screen.getByTestId('path-static')).toHaveTextContent('Comfort')
  })

  it('B2: uses the backend time estimate and its source', async () => {
    withV13()
    render(<ComparePage s={mockState()} ab={noAB} />)
    await waitFor(() => within(screen.getByTestId('arm-static')).getByText(/2 min 1 s/))
    expect(within(screen.getByTestId('arm-adaptive')).getByText('assumption')).toBeInTheDocument()
  })

  it('B3: like-for-like benchmark pair in the cards, all conditions + pooled speed AND safety below', async () => {
    withV13()
    render(<ComparePage s={mockState()} ab={noAB} />)
    await waitFor(() => screen.getByTestId('bench-table'))
    expect(within(screen.getByTestId('arm-static')).getByTestId('bench-line')).toHaveTextContent('0.33 correct msgs/min')
    expect(within(screen.getByTestId('arm-static')).getByTestId('bench-line')).toHaveTextContent('3 not completed')
    expect(within(screen.getByTestId('arm-adaptive')).getByTestId('bench-line')).toHaveTextContent('condition B2')
    expect(screen.getByTestId('bench-table')).toHaveTextContent('D: full NeuroSteno')
    expect(screen.getByTestId('bench-disclaimer')).toHaveTextContent('SIMULATION ONLY')
    expect(screen.getByTestId('bench-disclaimer')).toHaveTextContent('No real-EEG results')
    const pooled = screen.getByTestId('pooled-table')
    expect(pooled).toHaveTextContent('sent while idle / hour')
    expect(pooled).toHaveTextContent('53.91')
    // the slower-but-safer result is shown, not hidden
    expect(pooled).toHaveTextContent('0.76 [0.67, 0.86]')
    expect(screen.getByText(/Limitations \(1\)/)).toBeInTheDocument()
  })

  it('switching the simulated signal level never shows rows from another level', async () => {
    withV13()
    render(<ComparePage s={mockState()} ab={noAB} />)
    await waitFor(() => screen.getByTestId('bench-table'))
    act(() => screen.getByRole('radio', { name: 'low (1 µV)' }).click())
    expect(screen.queryByTestId('bench-table')).toBeNull()
    expect(within(screen.getByTestId('arm-static')).queryByTestId('bench-line')).toBeNull()
  })

  it('states the send guarantee as enforced by code, and only the blocked counter as measured', async () => {
    render(<ComparePage s={mockState({ safety_invariants: { messages_sent_without_final_confirmation: 0, blocked_unconfirmed_send_attempts: 2 } })} ab={noAB} />)
    await waitFor(() => screen.getByTestId('safety-invariant'))
    expect(screen.getByTestId('safety-invariant')).toHaveTextContent('guaranteed by code, not a measurement')
    expect(screen.getByTestId('safety-invariant')).toHaveTextContent('blocked since the backend started: 2')
  })
})

function Harness({ s, go }: { s: State; go: boolean }) {
  const ab = useLiveAB(s)
  if (go && !ab.run) void ab.start('comfort/position/turn-left', 'mobility', 2)
  return <p data-testid="arms">{ab.run?.arms.map((a) => `${a.arm}:${a.status}`).join(',')}</p>
}

describe('live A/B demonstration', () => {
  it('gives each arm an identical start: same seed, defaults, no mistakes, same settings', async () => {
    const s = mockState()
    const { rerender } = render(<Harness s={s} go />)
    rerender(<Harness s={{ ...s, version: 2 }} go={false} />)
    await waitFor(() => expect(posted().length).toBeGreaterThanOrEqual(8))
    expect(posted().slice(0, 8)).toEqual([
      { mode: 'simulated', seed: AB_SEED },
      { action: 'sim_mistake_rate', rate: 0 },
      { action: 'set_profile', id: 'mobility' },
      { action: 'set_auto_adapt', on: false },
      { action: 'set_choices', n: 2 },
      { action: 'set_intended', node_id: 'comfort/position/turn-left' },
      { action: 'set_routing', mode: 'adaptive' },
      { action: 'home' },
    ])
    expect(screen.getByTestId('arms')).toHaveTextContent('adaptive:preparing,static:waiting')
    // never presses YES/SEND for the patient
    expect(posted().some((b) => /yes|send|confirm/i.test(String(b.action)))).toBe(false)
  })

  it('refuses to start outside SIMULATED mode or during an emergency, calibration or recording', async () => {
    const sim = mockState().mode
    expect(startBlocker(mockState({ mode: { kind: 'real', label: 'REAL EEG', origin: 'real', is_real_eeg: true, replay_of: null } }))).toMatch(/SIMULATED mode only/)
    expect(startBlocker(mockState({ mode: sim, emergency: { active: true, source: 'demo_button', source_label: 'Demo', validated: false, time: 1 } }))).toMatch(/HELP NOW/)
    expect(startBlocker(mockState({ mode: sim, recording: { path: 'x', seconds: 1 } }))).toMatch(/recording/)
    expect(startBlocker(mockState())).toBeNull()
    render(<Harness s={mockState({ mode: { kind: 'real', label: 'REAL EEG', origin: 'real', is_real_eeg: true, replay_of: null } })} go />)
    await new Promise((r) => setTimeout(r, 20))
    expect(posted()).toEqual([])
  })

  it('flags arms that did not run under identical conditions', () => {
    const base = captureConditions(mockState({ sim: { gaze_key: null, gaze_hz: null, autopilot: null, pending_mistake: false, mistake_rate: 0, noise_multiplier: 1,
      bad_channels: [], disconnected: false, drop_probability: 0, ssvep_uv: 1.8 } }), AB_SEED)
    expect(conditionsMatch(base, { ...base, routing: 'static' })).toEqual({ ok: true, differences: [] })
    expect(conditionsMatch(base, { ...base, ssvep_uv: 1.0, mistake_rate: 0.1 }).differences).toEqual(['ssvep_uv', 'mistake_rate'])
    expect(conditionsMatch(base, undefined).ok).toBe(false)
  })

  it('labels the live run as a demonstration, not a controlled experiment', async () => {
    render(<ComparePage s={mockState({ routing: plan() })} ab={noAB} />)
    await waitFor(() => screen.getByTestId('live-ab'))
    const panel = screen.getByTestId('live-ab')
    expect(panel).toHaveTextContent('not a controlled experiment')
    expect(panel).toHaveTextContent('DEMO RUN · SIMULATED')
    expect(panel).toHaveTextContent('saved simulated history')
    expect(panel).toHaveTextContent(`seed ${AB_SEED}`)
  })

  it('shows recorded conditions and a mismatch against the EXACT count after a run', async () => {
    const cond = { seed: AB_SEED, routing: 'adaptive', n: 2, auto_adapt: false, policy: 'explicit', noise_multiplier: 1, ssvep_uv: 1.8, mistake_rate: 0, bad_channels: [], drop_probability: 0, disconnected: false }
    const ab = { ...noAB, run: { node_id: 'comfort/position/turn-left', profile: 'mobility', n: 2, finished: true, origin: 'simulated', seed: AB_SEED, restoreMistakeRate: 0,
      arms: [
        { arm: 'adaptive' as const, status: 'done' as const, selections: 4, completion_s: 30, correct: true, conditions: cond },
        { arm: 'static' as const, status: 'done' as const, selections: 11, completion_s: 74, correct: true, conditions: { ...cond, routing: 'static' } },
      ] } }
    render(<ComparePage s={mockState({ routing: plan() })} ab={ab} />)
    await waitFor(() => screen.getByTestId('ab-conditions'))
    expect(screen.getByTestId('ab-conditions')).toHaveTextContent('identical except routing')
    expect(screen.getByTestId('live-mismatch-adaptive')).toHaveTextContent('Differs from the EXACT seed-profile count (5)')
    expect(screen.queryByTestId('live-mismatch-static')).toBeNull()
  })
})
