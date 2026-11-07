import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import DemoGuide from '../components/DemoGuide'
import { DEMO_STEPS, DEMO_TOTAL_S } from '../demoSteps'
import OperatorPanel from '../components/OperatorPanel'
import PatientView from '../components/PatientView'
import { calibratedForCurrent } from '../mode'
import type { RoutingPlan } from '../types'
import { mockState } from './mockState'

const REAL = { kind: 'real', label: 'REAL EEG', origin: 'real', is_real_eeg: true, replay_of: null } as const
const REPLAY_OF_REAL = { kind: 'replay', label: 'REPLAY', origin: 'real', is_real_eeg: false, replay_of: 'real' } as const

const plan = (over: Partial<RoutingPlan> = {}): RoutingPlan => ({
  mode: 'adaptive', n: 5, shortcuts: ['comfort/position/turn-left'], urgent: ['breathing/suction-mouth/now'], candidates: [],
  expected: { selections: 6.2, static_selections: 8.4, time_s: 40, static_time_s: 55 }, reliability: {},
  verification: { policy: 'explicit', requested: 'explicit', reason: '', final_message: 'explicit' },
  recommended_n: { n: 5, by_n: {}, note: '' }, top_requests: [], explanation: [], computed_ms: 1, ...over,
})

let fetchMock: ReturnType<typeof vi.fn>
beforeEach(() => {
  fetchMock = vi.fn(async () => new Response(JSON.stringify([]), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => { vi.unstubAllGlobals(); try { localStorage.clear() } catch { /* ignore */ } })

const bodies = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST').map(([url, init]) => [url, JSON.parse(String(init!.body))])

describe('real vs simulated is unmistakable', () => {
  it.each([['real', REAL], ['simulated', mockState().mode], ['replay', REPLAY_OF_REAL]] as const)('frames the %s patient screen distinctly', (kind, mode) => {
    render(<PatientView s={mockState({ mode })} />)
    const scr = screen.getByTestId('patient-screen')
    expect(scr).toHaveAttribute('data-mode', kind)
    expect(scr.className).toMatch(kind === 'real' ? /mode-frame-real/ : kind === 'simulated' ? /mode-frame-sim/ : /mode-frame-replay/)
  })

  it('says simulated signals are not a person’s brain activity', () => {
    render(<PatientView s={mockState()} />)
    expect(screen.getByTestId('mode-caption')).toHaveTextContent(/Synthetic signals/)
    expect(screen.queryByTestId('calib-chip')).toBeNull()
  })

  it('a replay of REAL data is labelled as playback, never as live REAL', () => {
    render(<PatientView s={mockState({ mode: REPLAY_OF_REAL })} />)
    expect(screen.getByTestId('mode-badge')).toHaveTextContent('REPLAY · recorded REAL data')
    expect(screen.getByTestId('mode-caption')).toHaveTextContent('Not live')
    // contract v1.2: real-origin replay is gated on a real-data calibration too
    expect(screen.getByTestId('calib-chip')).toHaveTextContent('Not calibrated')
  })

  it('REAL mode shows NOT calibrated until a real-origin calibration supports this target count', () => {
    const s = mockState({ mode: REAL, decoder: { allowed_choices: [], stopping: {}, calibration: {} } })
    render(<PatientView s={s} />)
    expect(screen.getByTestId('calib-chip')).toHaveTextContent('Not calibrated for 5 targets')
    expect(screen.getByTestId('mode-caption')).toHaveTextContent(/communication is off until calibration/)
  })

  it('a simulated calibration never counts as real calibration', () => {
    const cal = { ssvep_5: { origin: 'simulated', supported: true } }
    expect(calibratedForCurrent(mockState({ mode: REAL, decoder: { allowed_choices: [5], stopping: {}, calibration: cal } }))).toBe(false)
    const realCal = { ssvep_5: { origin: 'real', supported: true } }
    expect(calibratedForCurrent(mockState({ mode: REAL, decoder: { allowed_choices: [5], stopping: {}, calibration: realCal } }))).toBe(true)
    // an explicit server flag wins over the derived value
    expect(calibratedForCurrent(mockState({ mode: { ...REAL, calibrated: false }, decoder: { allowed_choices: [5], stopping: {}, calibration: realCal } }))).toBe(false)
  })

  it('tags a sent message produced from simulated EEG', () => {
    const s = mockState({ phase: 'MESSAGE_SENT', feedback: { type: 'sent', label: 'Please turn me left.' },
      sent: [{ text: 'Please turn me left.', time: 1, data_origin: 'simulated', urgent: false, provisional_steps: 0 }] })
    render(<PatientView s={s} />)
    expect(screen.getByTestId('sent-origin')).toHaveTextContent('SIMULATED EEG')
    expect(screen.getByTestId('sent-origin')).toHaveTextContent('Not a live real-EEG message')
  })

  it('does not add the warning to a live REAL message', () => {
    const s = mockState({ mode: REAL, phase: 'MESSAGE_SENT', feedback: { type: 'sent', label: 'x' },
      sent: [{ text: 'x', time: 1, data_origin: 'real', urgent: false, provisional_steps: 0 }] })
    render(<PatientView s={s} />)
    expect(screen.queryByTestId('sent-origin')).toBeNull()
  })
})

describe('NeuroRoute on the patient screen', () => {
  it('explains why a card carries a suggestion', () => {
    const base = mockState()
    const slots = base.screen.slots.map((sl, i) => i === 3 ? { ...sl, suggest_reasons: ['Contains suggestion: Turn me left'] } : sl)
    render(<PatientView s={mockState({ screen: { ...base.screen, slots } })} />)
    expect(within(screen.getByTestId('slot-3')).getByTestId('why-3')).toHaveTextContent('Contains suggestion: Turn me left')
    expect(screen.queryByTestId('why-0')).toBeNull()
  })

  it('shows expected selections against the static menu', () => {
    render(<PatientView s={mockState({ routing: plan() })} />)
    const strip = screen.getByTestId('neuroroute-strip')
    expect(strip).toHaveTextContent('NeuroRoute · risk-aware')
    expect(strip).toHaveTextContent('6.2')
    expect(strip).toHaveTextContent('static menu 8.4, 2.2 fewer')
    expect(strip).toHaveTextContent('1 urgent routes always on')
  })

  it('labels the static baseline as such', () => {
    const s = mockState({ routing: plan({ mode: 'static', shortcuts: [] }) })
    s.config.routing_mode = 'static'
    render(<PatientView s={s} />)
    expect(screen.getByTestId('neuroroute-strip')).toHaveTextContent('Static menu (baseline)')
  })

  it('announces an adaptive target-count change and a pending route update', () => {
    render(<PatientView s={mockState({ server_time: 100, routing_pending: true,
      adaptation: { auto: true, recent: [], last_change: { from: 5, to: 3, reason: 'two abstentions in a row', time: 95 } } })} />)
    expect(screen.getByTestId('adapt-notice')).toHaveTextContent('Now 3 choices per screen (was 5): two abstentions in a row')
    expect(screen.getByTestId('adapt-notice')).toHaveAttribute('role', 'status')
    expect(screen.getByText('Route update applies when you return Home')).toBeInTheDocument()
  })

  it('hides a stale adaptation notice', () => {
    render(<PatientView s={mockState({ server_time: 1000, adaptation: { auto: true, recent: [], last_change: { from: 5, to: 3, reason: 'r', time: 10 } } })} />)
    expect(screen.queryByTestId('adapt-notice')).toBeNull()
  })
})

describe('accessibility', () => {
  it('the evidence bar is a progressbar and is not an aria-live region', () => {
    const s = mockState({ phase: 'EVIDENCE_COLLECTION', attempt: { kind: 'choose', targets: [], elapsed_s: 1, max_window_s: 4, min_window_s: 1, calibrated: false, last: null } })
    const { container } = render(<PatientView s={s} />)
    const bar = screen.getByRole('progressbar')
    expect(bar).toHaveAttribute('aria-valuenow')
    expect(container.querySelectorAll('[aria-live]')).toHaveLength(1) // only the phase sentence
  })

  it('announces the predicted choice by name', () => {
    const s = mockState({ phase: 'PREDICTION_HIGHLIGHTED', highlight: { slot_index: 0, key: 'pain', label: 'Pain', margin: 1, window_s: 1, provisional: false, provisional_remaining_s: null } })
    render(<PatientView s={s} />)
    expect(screen.getByText('Did you mean “Pain”? Look at YES or NO.')).toBeInTheDocument()
  })

  it('final review is a labelled modal dialog and HELP NOW is an alertdialog', () => {
    const s = mockState({ phase: 'MESSAGE_REVIEW', review: { text: 'x', node_id: 'x', title: 'x', urgent: false, provisional_steps: 0 } })
    const { unmount } = render(<PatientView s={s} />)
    expect(screen.getByRole('dialog', { name: 'Review your message before sending' })).toHaveAttribute('aria-modal', 'true')
    unmount()
    render(<PatientView s={mockState({ emergency: { active: true, source: 'demo_button', source_label: 'Demo button', validated: false, time: 1 } })} />)
    expect(screen.getByRole('alertdialog')).toHaveTextContent('NOT a certified alarm')
  })

  it('operator tabs follow the ARIA tabs pattern with arrow-key navigation', () => {
    render(<OperatorPanel s={mockState()} />)
    const tabs = screen.getAllByRole('tab')
    expect(tabs[0]).toHaveTextContent('Demo')
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(tabs[0], { key: 'ArrowRight' })
    expect(screen.getByRole('tab', { name: 'Session' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'optab-Session')
  })

  it('the Off source button is active when no source is connected', () => {
    render(<OperatorPanel s={mockState({ mode: { kind: 'none', label: 'NO EEG SOURCE', origin: 'none', is_real_eeg: false, replay_of: null } })} />)
    fireEvent.click(screen.getByRole('tab', { name: 'Session' }))
    expect(screen.getByRole('button', { name: 'Off' }).className).toContain('bg-sky-600')
  })
})

describe('judge demo guide', () => {
  it('fits the five-minute slot with time to spare', () => {
    const total = DEMO_STEPS.reduce((a, d) => a + d.budget_s, 0)
    expect(total).toBeLessThanOrEqual(DEMO_TOTAL_S)
    expect(DEMO_STEPS.map((d) => d.id)).toEqual(['honesty', 'home', 'select', 'confirm', 'context', 'failure', 'safety', 'hardware'])
  })

  it('step buttons send ordinary contract commands', async () => {
    render(<DemoGuide s={mockState()} onGoTab={() => {}} />)
    fireEvent.click(screen.getByText('Switch to Simulated'))
    fireEvent.click(screen.getByText('Next →'))
    fireEvent.click(screen.getByText('2 choices'))
    fireEvent.click(screen.getByText('Next →'))
    fireEvent.click(screen.getByText('Next selection: wrong target'))
    await Promise.resolve()
    expect(bodies()).toEqual([
      ['/api/mode', { mode: 'simulated' }],
      ['/api/command', { action: 'set_choices', n: 2 }],
      ['/api/command', { action: 'sim_inject_mistake' }],
    ])
  })

  it('never sends YES/SEND on the patient’s behalf', () => {
    const actions = DEMO_STEPS.flatMap((d) => d.actions.map((a) => a.label.toLowerCase()))
    expect(actions.some((l) => /\b(yes|send|confirm|accept)\b/.test(l))).toBe(false)
  })

  it('ticks a step only from the live snapshot', () => {
    render(<DemoGuide s={mockState()} onGoTab={() => {}} />)
    expect(screen.getByLabelText('live condition met')).toBeInTheDocument() // simulated mode in the mock
    const { container } = render(<DemoGuide s={mockState({ mode: REAL })} onGoTab={() => {}} />)
    expect(within(container).queryByLabelText('live condition met')).toBeNull()
  })
})
