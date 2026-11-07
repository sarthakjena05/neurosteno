import { render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ComparePage from '../components/ComparePage'
import { summaryRows } from '../compare'
import { overall, preflightChecks } from '../preflight'
import type { Bench } from '../compare'
import { answerMethods, readInputs, sanitizePolicy } from '../multimodal'
import type { State } from '../types'
import { mockState } from './mockState'

beforeEach(() => vi.stubGlobal('fetch', vi.fn(async () => new Response('{}'))))
afterEach(() => vi.unstubAllGlobals())

const live = { connected: true, stale: false, reconnects: 0, badMessages: 0 }
const ep = { compare: true, benchmark: true }

describe('operator preflight', () => {
  it('is GO-able only with a live backend; the history step stays manual until ticked', () => {
    const s = { ...mockState({ routing: { mode: 'adaptive', n: 5 } as State['routing'] }), _missing: [] }
    const ok = preflightChecks(s, live, ep, true)
    expect(ok.find((c) => c.id === 'link')!.level).toBe('ok')
    expect(overall(ok)).toBe('ok')
    expect(preflightChecks(s, live, ep, false).find((c) => c.id === 'history')!.level).toBe('manual')
    const down = preflightChecks(s, { ...live, connected: false, stale: true }, ep, true)
    expect(down.find((c) => c.id === 'link')!.level).toBe('fail')
    expect(overall(down)).toBe('fail')
  })

  it('reports missing fields, leftovers and non-simulated sources', () => {
    const s = { ...mockState({ emergency: { active: true, source: 'demo_button', source_label: 'Demo', validated: false, time: 1 } }), _missing: ['adaptation'] }
    const c = preflightChecks(s, live, { compare: false, benchmark: false }, true)
    expect(c.find((x) => x.id === 'fields')!.detail).toContain('adaptation')
    expect(c.find((x) => x.id === 'clean')!.detail).toContain('HELP NOW')
    expect(c.find((x) => x.id === 'compare')!.level).toBe('fail')
    const none = preflightChecks({ ...mockState({ mode: { kind: 'none', label: 'NO EEG SOURCE', origin: 'none', is_real_eeg: false, replay_of: null } }), _missing: [] }, live, ep, true)
    expect(none.find((x) => x.id === 'mode')!.level).toBe('fail')
  })
})

const BENCH: Bench = {
  available: true, conditions: [
    { key: 'A2', label: 'A: fixed menu, 2 targets', routing: 'static', n: 2, decoder: 'fixed', step_confirm: false, live_adapt: false, confirm_gate: null },
    { key: 'A5', label: 'A: fixed menu, 5 targets', routing: 'static', n: 5, decoder: 'fixed', step_confirm: false, live_adapt: false, confirm_gate: null },
    { key: 'C', label: 'C: context routing + adaptive n', routing: 'adaptive', n: null, decoder: 'fixed', step_confirm: false, live_adapt: false, confirm_gate: null },
    { key: 'D', label: 'D: full NeuroSteno', routing: 'adaptive', n: null, decoder: 'dynamic', step_confirm: true, live_adapt: true, confirm_gate: null },
  ],
  summary: { pooled: [
    { condition: 'A2', sessions: 120, cmpm_mean: 0.32, error_rate: 0.13, false_confirmations_task: 215, messages_sent: 1, idle_false_sent_per_hour: 33.2 },
    { condition: 'A5', sessions: 120, cmpm_mean: 1.06, error_rate: 0.03, false_confirmations_task: 68, messages_sent: 1, idle_false_sent_per_hour: 53.9 },
    { condition: 'C', sessions: 120, cmpm_mean: 1.22, error_rate: 0.03, false_confirmations_task: 61, messages_sent: 1, idle_false_sent_per_hour: 49.7 },
    { condition: 'D', sessions: 120, cmpm_mean: 0.76, error_rate: 0, false_confirmations_task: 0, messages_sent: 1, idle_false_sent_per_hour: 0 },
  ] },
}

describe('20-second judge summary', () => {
  it('picks the fastest fixed menu and the full NeuroSteno variants from the data', () => {
    const r = summaryRows(BENCH)
    expect(r.fixed?.condition).toBe('A5')
    expect(r.full.map((p) => p.condition)).toEqual(['D'])
    expect(summaryRows({ available: false })).toEqual({ fixed: null, full: [] })
  })

  it('shows effort (EXACT), the honest trade-off (BENCHMARK) and EEG-only inputs (NOT MEASURED benefit)', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(url === '/api/benchmark' ? BENCH : {
      scenarios: [{ id: 'B', profile: 'mobility', title: 'Mobility' }], messages: { 'turn-left': 'comfort/position/turn-left' },
      rows: [{ scenario: 'B', profile: 'mobility', n: 2, message: 'turn-left', text: 't', static: 11, adaptive: 5, delta: -6 }] }))))
    render(<ComparePage s={mockState()} ab={{ run: null, start: vi.fn(), stop: vi.fn(), active: false }} />)
    const eff = await screen.findByTestId('sum-effort')
    expect(await within(eff).findByText(/11 → 5/)).toBeInTheDocument()
    expect(within(eff).getByText('EXACT')).toBeInTheDocument()
    const tr = await screen.findByText('Fastest fixed menu*')
    const table = tr.closest('table')!
    expect(table).toHaveTextContent('1.06')
    expect(table).toHaveTextContent('53.9')
    expect(table).toHaveTextContent('full NeuroSteno')
    expect(table).toHaveTextContent('0.76') // slower: shown, not hidden
    expect(screen.getByTestId('sum-tradeoff')).toHaveTextContent('chosen in hindsight')
    expect(screen.getByTestId('sum-inputs')).toHaveTextContent('Now: EEG-only')
    expect(within(screen.getByTestId('sum-inputs')).getByText('NOT MEASURED')).toBeInTheDocument()
  })
})

/* ---------- frontend contract tests for the (unconfirmed) multimodal backend ---------- */
const withInput = (input: unknown, extra: Partial<State> = {}) => mockState({ ...extra, ...({ input } as object) })

describe('multimodal contract: defensive handling', () => {
  it('missing IMU fields → no IMU state; gestures stay inactive', () => {
    const vm = readInputs(withInput({ mode: 'hybrid', modes_available: ['eeg_only', 'hybrid'], methods: { nod_yes: { status: 'calibrated', enabled: true } } }), 'confirmed')
    expect(vm.imu).toBeNull()
    expect(vm.methods.nod_yes.active).toBe(true)
    expect(vm.methods.shake_no.active).toBe(false)
    expect(vm.methods.shake_no.reason).toBe('Not reported by the backend')
  })

  it('uncalibrated, unknown-status or truthy-but-not-true gestures are never active', () => {
    const vm = readInputs(withInput({ mode: 'hybrid', modes_available: ['hybrid'], methods: {
      nod_yes: { status: 'not_calibrated', enabled: true },
      shake_no: { status: 'super_calibrated', enabled: true },
      tilt_nav: { status: 'calibrated', enabled: 'true' } } }), 'confirmed')
    expect(vm.methods.nod_yes.active).toBe(false)
    expect(vm.methods.shake_no.status).toBe('unavailable')
    expect(vm.methods.tilt_nav.active).toBe(false)
    expect(answerMethods(vm, 'confirm')).toEqual(['eeg_ssvep'])
  })

  it('EEG-only fallback: unknown mode, no methods, or no input at all', () => {
    const unknownMode = readInputs(withInput({ mode: 'telepathy', modes_available: ['telepathy'], methods: {} }), 'confirmed')
    expect(unknownMode.mode).toBe('eeg_only')
    expect(unknownMode.modesAvailable).toEqual(['eeg_only'])
    expect(readInputs(withInput({ mode: 'hybrid' }), 'confirmed').provenance).toBe('eeg_only_default') // no methods object
    expect(readInputs(mockState(), 'confirmed').provenance).toBe('eeg_only_default')
    expect(readInputs(withInput({ mode: 'hybrid', modes_available: ['hybrid'], methods: {} }), 'confirmed').modesAvailable).toContain('eeg_only') // EEG-only always available
  })

  it('conflicting inputs abstain visibly and offer no gesture shortcut', () => {
    const vm = readInputs(withInput({ mode: 'hybrid', modes_available: ['eeg_only', 'hybrid'], methods: { nod_yes: { status: 'calibrated', enabled: true } },
      last_gesture: { gesture: 'nod', confidence: 0.8, accepted: 'yes', used_for: 'send' } }, { feedback: { type: 'conflict', label: '' } }), 'confirmed')
    expect(vm.abstain?.type).toBe('conflict')
    expect(vm.lastGesture?.accepted).toBe(false) // only literal true counts
    expect(vm.lastGesture?.used_for).toBeNull() // "send" is not a valid use
  })

  it('a malformed policy is dropped or reduced to known methods, and final SEND stays EEG-only', () => {
    expect(sanitizePolicy({ action: 'review' })).toBeNull()
    const p = sanitizePolicy({ action: 'review', selected: 'mind_reading', options: [{ method: 'nod_yes', allowed: true }, { method: 'blink_twice', allowed: true }, 'x'] })!
    expect(p.selected).toBe('eeg_fallback')
    expect(p.options.map((o) => o.method)).toEqual(['nod_yes'])
    const vm = readInputs(withInput({ mode: 'hybrid', modes_available: ['hybrid'], methods: { nod_yes: { status: 'calibrated', enabled: true } } }, { ...({ policy: p } as object) }), 'confirmed')
    expect(answerMethods(vm, 'review')).toEqual(['eeg_ssvep'])
  })
})
