import { act, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ComparePage from '../components/ComparePage'
import { benchFor, estimatedTime, measuredFromSession, type CompareData } from '../compare'
import type { RoutingPlan, State } from '../types'
import { useLiveAB } from '../useLiveAB'
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

  it('benchmark rows only when published', () => {
    expect(benchFor({ available: false }, 'B', 'turn-left', 2)).toEqual([])
    expect(benchFor({ available: true, rows: [{ condition: 'A', scenario: 'B', profile: 'mobility', message: 'turn-left', n_start: 2, runs: 10, mean_selections: 11, mean_completion_s: 100, correct: 10, correct_msgs_per_min: 0.6 }] }, 'B', 'turn-left', 2)).toHaveLength(1)
  })
})

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

  it('says "not measured yet" instead of a number when nothing was measured', async () => {
    render(<ComparePage s={mockState({ routing: plan() })} ab={noAB} />)
    await waitFor(() => screen.getByTestId('arm-static'))
    expect(within(screen.getByTestId('arm-static')).getByText(/not measured yet/)).toBeInTheDocument()
    expect(within(screen.getByTestId('arm-adaptive')).queryByText(/MEASURED ·/)).toBeNull()
    expect(screen.getByText(/Not published yet/)).toBeInTheDocument()
  })

  it('labels measured session results with their data origin', async () => {
    const s = mockState({ routing: plan(), metrics: { recent: [], summary: [
      { origin: 'simulated', source: 'simulated', routing_mode: 'adaptive', profile: 'mobility', n: 2, messages: 1, mean_completion_s: 45, correct_final: '1/1' }] } })
    render(<ComparePage s={s} ab={noAB} />)
    await waitFor(() => screen.getByTestId('arm-adaptive'))
    const arm = screen.getByTestId('arm-adaptive')
    expect(within(arm).getByText('MEASURED · SIMULATED')).toBeInTheDocument()
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
    expect(screen.getByTestId('why-candidates')).toHaveTextContent('✓ shortcut Turn me left')
    const active = within(screen.getByTestId('flow')).getAllByRole('listitem').filter((li) => li.dataset.active === 'true')
    expect(active).toHaveLength(1)
    expect(active[0]).toHaveTextContent('Highlight, same screen')
  })

  it('keeps the REAL/SIMULATED badge and only allows the live A/B in simulation', async () => {
    const real = mockState({ routing: plan(), mode: { kind: 'real', label: 'REAL EEG', origin: 'real', is_real_eeg: true, replay_of: null, calibrated: false, calibration_required: true } })
    render(<ComparePage s={real} ab={noAB} />)
    await waitFor(() => screen.getByText(/Run live A\/B/))
    expect(screen.getByTestId('mode-badge')).toHaveTextContent('REAL EEG')
    expect(screen.getByText(/Run live A\/B/).closest('button')).toBeDisabled()
    expect(screen.getByText(/Available in SIMULATED mode only/)).toBeInTheDocument()
  })
})

function Harness({ s, go }: { s: State; go: boolean }) {
  const ab = useLiveAB(s)
  if (go && !ab.run) void ab.start('comfort/position/turn-left', 'mobility', 2)
  return <p data-testid="arms">{ab.run?.arms.map((a) => `${a.arm}:${a.status}`).join(',')}</p>
}

describe('live A/B runner', () => {
  it('runs NeuroSteno first, then the fixed menu, using only contract commands', async () => {
    const s = mockState()
    const { rerender } = render(<Harness s={s} go />)
    await waitFor(() => expect(posted().length).toBeGreaterThanOrEqual(5))
    expect(posted().slice(0, 5)).toEqual([
      { action: 'sim_autopilot', node_id: null }, { action: 'set_profile', id: 'mobility' },
      { action: 'set_auto_adapt', on: false }, { action: 'set_choices', n: 2 },
      { action: 'set_intended', node_id: 'comfort/position/turn-left' },
    ])
    rerender(<Harness s={{ ...s, version: 2 }} go={false} />)
    await waitFor(() => expect(posted()).toContainEqual({ action: 'set_routing', mode: 'adaptive' }))
    expect(screen.getByTestId('arms')).toHaveTextContent('adaptive:preparing,static:waiting')
    // never presses YES/SEND for the patient
    expect(posted().some((b) => /yes|send|confirm/i.test(String(b.action)))).toBe(false)
  })

  it('refuses to start outside SIMULATED mode', async () => {
    const real = mockState({ mode: { kind: 'real', label: 'REAL EEG', origin: 'real', is_real_eeg: true, replay_of: null } })
    render(<Harness s={real} go />)
    await new Promise((r) => setTimeout(r, 20))
    expect(posted()).toEqual([])
  })
})
