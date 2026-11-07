import { act, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { STALE_AFTER_MS, useEngineState } from '../api'
import App, { ErrorBoundary } from '../App'
import PatientView, { gridClass, StageIndicator } from '../components/PatientView'
import { FlickerEngine, flicker } from '../flicker'
import { normalizeState } from '../normalize'
import type { State } from '../types'
import { homeSlots, mockState } from './mockState'

/* ---------- a controllable fake WebSocket ---------- */
class FakeWS {
  static instances: FakeWS[] = []
  static OPEN = 1
  readyState = 0
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  url: string
  constructor(url: string) { this.url = url; FakeWS.instances.push(this) }
  open() { this.readyState = 1; this.onopen?.() }
  send(state: unknown) { this.onmessage?.({ data: JSON.stringify({ type: 'state', state }) }) }
  raw(data: string) { this.onmessage?.({ data }) }
  close() { this.readyState = 3; this.onclose?.() }
}

beforeEach(() => {
  FakeWS.instances = []
  vi.stubGlobal('WebSocket', FakeWS)
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}')))
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); try { localStorage.clear() } catch { /* ignore */ } })

describe('missing or malformed backend fields', () => {
  it('an empty snapshot becomes a safe "no source" state, never REAL', () => {
    const s = normalizeState({})
    expect(s.mode.kind).toBe('none')
    expect(s.mode.is_real_eeg).toBe(false)
    expect(s._missing).toEqual(expect.arrayContaining(['mode', 'phase', 'screen', 'config', 'decoder', 'profile', 'adaptation', 'metrics', 'comm']))
    expect(() => render(<PatientView s={s} />)).not.toThrow()
    expect(screen.getByText('No EEG source')).toBeInTheDocument()
  })

  it('an unknown mode kind is never shown as real or simulated', () => {
    expect(normalizeState({ mode: { kind: 'realish', label: 'REAL EEG' } }).mode.kind).toBe('none')
    expect(normalizeState({ mode: { kind: 'simulated', label: 'SIMULATED EEG', origin: 'simulated' } }).mode.label).toBe('SIMULATED EEG')
  })

  it('an older backend without adaptation / decoder / routing still renders', () => {
    const { adaptation: _a, decoder: _d, routing: _r, ...older } = mockState() as unknown as Record<string, unknown>
    const s = normalizeState(older)
    expect(s._missing).toEqual(expect.arrayContaining(['adaptation', 'decoder']))
    render(<PatientView s={s} />)
    expect(screen.getByTestId('prompt')).toHaveTextContent('What do you need?')
  })

  it('malformed slots do not crash the screen', () => {
    const s = normalizeState({ ...mockState(), screen: { prompt: 'X', slots: [{ label: 'A' }, null, { key: 'b', items: 'oops' }] } })
    expect(s.screen.slots.every((sl) => Array.isArray(sl.items))).toBe(true)
  })

  it('a render error shows a contained message instead of a blank screen', () => {
    const Boom = () => { throw new Error('boom') }
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<ErrorBoundary area="Patient screen"><Boom /></ErrorBoundary>)
    expect(screen.getByTestId('error-Patient screen')).toHaveTextContent('Nothing is selected from this screen')
  })
})

function Probe() {
  const c = useEngineState()
  return <p data-testid="probe">{`${c.connected}|${c.stale}|${c.state?.mode.kind ?? '-'}|${c.badMessages}|${c.reconnects}`}</p>
}

describe('WebSocket connection handling', () => {
  it('ignores unreadable messages, marks stale data, and reconnects after a close', () => {
    vi.useFakeTimers()
    render(<Probe />)
    const ws = FakeWS.instances[0]
    act(() => { ws.open(); ws.send(mockState()) })
    expect(screen.getByTestId('probe')).toHaveTextContent('true|false|simulated|0|0')
    act(() => ws.raw('{not json'))
    expect(screen.getByTestId('probe')).toHaveTextContent('|1|')
    act(() => { vi.advanceTimersByTime(STALE_AFTER_MS + 600) })
    expect(screen.getByTestId('probe')).toHaveTextContent('true|true|simulated')
    act(() => ws.close())
    expect(screen.getByTestId('probe')).toHaveTextContent('false|true|simulated|1|1')
    act(() => { vi.advanceTimersByTime(600) })
    expect(FakeWS.instances.length).toBe(2)
  })

  it('a stale or lost connection hides the patient screen and suspends flicker', () => {
    vi.useFakeTimers()
    render(<App />)
    expect(flicker.suspended).toBe(true) // nothing live yet
    const ws = FakeWS.instances[0]
    act(() => { ws.open(); ws.send(mockState()) })
    expect(flicker.suspended).toBe(false)
    expect(screen.queryAllByTestId('offline-overlay')).toHaveLength(0)
    act(() => ws.close())
    expect(flicker.suspended).toBe(true)
    expect(screen.getAllByTestId('offline-overlay')[0]).toHaveTextContent('Nothing is being read or selected')
    expect(screen.getByTestId('link-banner')).toHaveTextContent('reconnecting')
  })

  it('the suspended flicker engine never drives a stimulus', () => {
    const f = new FlickerEngine()
    const el = document.createElement('div')
    f.enabled = true
    f.register('a', el, { kind: 'ssvep', freq: 10 })
    el.style.opacity = '0.8'
    f.setSuspended(true)
    expect(el.style.opacity).toBe('0')
    f.unregister('a')
  })
})

describe('patient accessibility', () => {
  it('shows live headset status and quality from backend state, including loss of the connection', () => {
    render(<App />)
    const s = mockState({ mode: { kind: 'real', label: 'REAL EEG', origin: 'real', is_real_eeg: true, replay_of: null },
      source: { status: { connected: true, streaming: true } } as State['source'], quality: { signal_ok: false, problem: 'Oz flat' } })
    act(() => { FakeWS.instances[0].open(); FakeWS.instances[0].send(s) })
    expect(screen.getByTestId('live-eeg-status')).toHaveTextContent('Streaming')
    expect(screen.getByTestId('signal-quality-status')).toHaveTextContent('Signal needs attention')
    expect(screen.getByTestId('signal-quality-status')).toHaveAttribute('title', 'Oz flat')
    act(() => FakeWS.instances[0].close())
    expect(screen.getByTestId('live-eeg-status')).toHaveTextContent('Connection unverified')
  })

  it('reflects backend-driven 5, 3 and 2 choices while keeping controls collapsed', () => {
    render(<App />)
    act(() => FakeWS.instances[0].open())
    for (const n of [5, 3, 2]) {
      act(() => FakeWS.instances[0].send(mockState({}, n)))
      expect(screen.getByLabelText('Session status')).toHaveTextContent('Choices ' + n)
    }
    expect(screen.queryByLabelText('Operator and caregiver panel')).not.toBeInTheDocument()
    act(() => screen.getByRole('button', { name: /^Controls$/ }).click())
    expect(screen.getByLabelText('Operator and caregiver panel')).toBeInTheDocument()
    expect(screen.getByLabelText('Injected-noise controls')).toHaveTextContent('No control connected')
  })

  it('shows exactly one current stage for each phase of the flow', () => {
    const cases: [State['phase'], string][] = [['EVIDENCE_COLLECTION', '1. Choose'], ['PREDICTION_HIGHLIGHTED', '2. Confirm YES / NO'], ['MESSAGE_REVIEW', '3. Check & SEND'], ['MESSAGE_SENT', '4. Sent']]
    for (const [phase, label] of cases) {
      const { unmount } = render(<StageIndicator s={mockState({ phase })} />)
      const cur = within(screen.getByTestId('stage-indicator')).getAllByRole('listitem').filter((li) => li.getAttribute('aria-current') === 'step')
      expect(cur).toHaveLength(1)
      expect(cur[0]).toHaveTextContent(label)
      unmount()
    }
    const { container } = render(<StageIndicator s={mockState({ phase: 'COMM_UNAVAILABLE' })} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('the predicted target is emphasised statically: no pulse, scaling or transition near SSVEP targets', () => {
    const s = mockState({ phase: 'PREDICTION_HIGHLIGHTED', highlight: { slot_index: 1, key: 'breathing', label: 'Breathing', margin: 1, window_s: 1, provisional: false, provisional_remaining_s: null } })
    render(<PatientView s={s} />)
    const hl = screen.getByTestId('slot-1')
    expect(hl.className).toContain('predicted-target')
    for (const el of [hl, ...screen.getAllByTestId(/^slot-/)]) expect(el.className).not.toMatch(/scale-|highlight-ring|transition|animate-/)
  })

  it('signal problems are visible before they pause communication', () => {
    render(<PatientView s={mockState({ quality: { signal_ok: false, problem: 'Oz flat' } })} />)
    expect(screen.getByTestId('signal-warning')).toHaveTextContent('Signal quality: Oz flat')
  })

  it.each([2, 3, 5])('works with %i targets', (n) => {
    const slots = n === 3 ? homeSlots(5).slice(0, 3).map((sl, i) => ({ ...sl, index: i })) : homeSlots(n)
    render(<PatientView s={mockState({ screen: { ...mockState().screen, slots, n_choices: n } })} />)
    expect(screen.getAllByTestId(/^slot-\d+$/)).toHaveLength(n)
    expect(screen.getByTestId('slots').className).toContain(gridClass(n).split(' ')[0])
  })

  it('high contrast and patient full-screen mode are one click away and remembered', () => {
    const store = new Map<string, string>()
    vi.stubGlobal('localStorage', { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => store.set(k, v), clear: () => store.clear() })
    vi.useFakeTimers()
    render(<App />)
    act(() => { FakeWS.instances[0].open(); FakeWS.instances[0].send(mockState()) })
    act(() => screen.getByTestId('toggle-hc').click())
    expect(document.querySelector('main')?.className).toContain('hc')
    expect(localStorage.getItem('ns.hc')).toBe('1')
    act(() => screen.getByTestId('enter-patient-mode').click())
    expect(location.hash).toBe('#patient')
    expect(screen.queryByText('Operator panel')).toBeNull()
    expect(screen.getByTestId('mode-badge')).toHaveTextContent('SIMULATED EEG') // label survives full screen
    act(() => screen.getByTestId('exit-patient-mode').click())
    expect(location.hash).toBe('')
  })
})
