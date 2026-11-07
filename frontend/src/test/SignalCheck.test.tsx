import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PatientView from '../components/PatientView'
import SignalCheck from '../components/SignalCheck'
import type { ElectrodeCheck, State } from '../types'
import { mockState } from './mockState'

let fetchMock: ReturnType<typeof vi.fn>
beforeEach(() => {
  fetchMock = vi.fn(async () => new Response('{}', { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => vi.unstubAllGlobals())

const ch = (label: string, verdict: 'good' | 'suspect' | 'bad' | 'no_data', extra: object = {}) =>
  ({ label, verdict, status: verdict, contact_score: verdict === 'good' ? 0.9 : 0.2, issues: [], advice: [], hint: null, ...extra })

const check = (over: Partial<ElectrodeCheck> = {}): ElectrodeCheck => ({
  ready: false, reference_ok: true, message: 'm',
  summary: { headline: 'Fix occipital/parietal electrodes (2/4 usable)' },
  channels: [ch('Fz', 'good'), ch('C3', 'good'), ch('Cz', 'good'), ch('C4', 'good'), ch('Pz', 'good'),
    ch('PO7', 'bad', { hint: 'Press the electrode to the scalp' }), ch('Oz', 'suspect'), ch('PO8', 'no_data')],
  ...over,
})

const REPLAY_REAL: State['mode'] = { kind: 'replay', label: 'REPLAY', origin: 'real', is_real_eeg: false, replay_of: 'real', calibrated: false, calibration_required: true }

describe('contract v1.2 calibration fields', () => {
  it('a replay of REAL data shows NOT calibrated (calibration_required && !calibrated)', () => {
    render(<PatientView s={mockState({ mode: REPLAY_REAL })} />)
    expect(screen.getByTestId('calib-chip')).toHaveTextContent('Not calibrated')
    expect(screen.getByTestId('mode-caption')).toHaveTextContent('Needs a real-data calibration')
  })

  it('trusts the server mode.calibrated flag', () => {
    render(<PatientView s={mockState({ mode: { ...REPLAY_REAL, calibrated: true } })} />)
    expect(screen.getByTestId('calib-chip')).toHaveTextContent('Calibrated for 5 targets')
  })

  it('simulated data never shows a calibration chip', () => {
    render(<PatientView s={mockState({ mode: { ...mockState().mode, calibration_required: false } })} />)
    expect(screen.queryByTestId('calib-chip')).toBeNull()
  })

  it('a replayed real-origin message is tagged as playback even though data_origin is "real"', () => {
    const s = mockState({ mode: { ...REPLAY_REAL, calibrated: true }, phase: 'MESSAGE_SENT', feedback: { type: 'sent', label: 'x' },
      sent: [{ text: 'x', time: 1, data_origin: 'real', source: 'replay', urgent: false, provisional_steps: 0 }] })
    render(<PatientView s={s} />)
    expect(screen.getByTestId('sent-origin')).toHaveTextContent('REAL EEG played back from a recording')
    expect(screen.getByTestId('sent-origin')).toHaveTextContent('Not a live real-EEG message')
  })
})

describe('electrode signal check', () => {
  it('lays out all 8 electrodes with verdicts shown by symbol, not colour alone', () => {
    render(<SignalCheck s={mockState({ source: {} as State['source'], electrode_check: check() })} />)
    expect(screen.getByTestId('electrode-PO7')).toHaveAttribute('data-verdict', 'bad')
    expect(screen.getByTestId('electrode-PO7')).toHaveTextContent('✗')
    expect(screen.getByTestId('electrode-Oz')).toHaveTextContent('?')
    expect(screen.getByTestId('electrode-PO8')).toHaveAttribute('data-verdict', 'no_data')
    expect(screen.getByTestId('electrode-Fz')).toHaveTextContent('✓')
    expect(screen.getByTestId('electrode-headline')).toHaveTextContent('Not ready: Fix occipital/parietal electrodes')
    expect(screen.getByText(/Press the electrode to the scalp/)).toBeInTheDocument()
  })

  it('never calls the contact score impedance', () => {
    render(<SignalCheck s={mockState({ source: {} as State['source'], electrode_check: check() })} />)
    expect(screen.getByText(/not impedance/)).toBeInTheDocument()
    expect(screen.queryByText(/impedance:/i)).toBeNull()
  })

  it('explains when no source is running', () => {
    render(<SignalCheck s={mockState({ electrode_check: null })} />)
    expect(screen.getByText(/No EEG source running/)).toBeInTheDocument()
  })

  it('starts the eyes open/closed check with the contract command', () => {
    render(<SignalCheck s={mockState({ source: {} as State['source'], electrode_check: check() })} />)
    fireEvent.click(screen.getByText(/Eyes open\/closed check/))
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/command')
    expect(JSON.parse(String(init.body))).toEqual({ action: 'start_alpha_check', cycles: 2 })
  })

  it('shows the alpha result with its data origin', () => {
    const s = mockState({ source: {} as State['source'], decoder: { allowed_choices: [5], stopping: {}, calibration: {
      alpha: { verdict: 'eeg_confirmed', note: 'Oz alpha x2.1', origin: 'simulated', reactive_occipital: ['Oz', 'PO8'] } } } })
    render(<SignalCheck s={s} />)
    // simulated alpha must never read as "brain activity confirmed"
    expect(screen.getByTestId('alpha-result')).not.toHaveTextContent('Brain activity confirmed')
    expect(screen.getByTestId('alpha-result')).toHaveTextContent('Pipeline check only')
    expect(screen.getByTestId('alpha-result')).toHaveTextContent('SIMULATED')
    expect(screen.getByTestId('alpha-result')).toHaveTextContent('Oz, PO8')
  })

  it('a real-origin alpha confirmation is stated as brain activity', () => {
    const s = mockState({ source: {} as State['source'], decoder: { allowed_choices: [], stopping: {}, calibration: {
      alpha: { verdict: 'eeg_confirmed', note: 'n', origin: 'real' } } } })
    render(<SignalCheck s={s} />)
    expect(screen.getByTestId('alpha-result')).toHaveTextContent('Brain activity confirmed')
  })
})

describe('eyes open/closed overlay', () => {
  const alphaCal = (sub: string): State['calibration'] =>
    ({ kind: 'alpha', n: 0, freqs: [], trial: 1, trials: 4, sub, cue_target: null, rest_trial: false, elapsed: 3, durations: { eyes_open: 10, eyes_closed: 10 } })

  it('shows a large CLOSE cue, no flicker targets, and speaks the cue', () => {
    const speak = vi.fn()
    vi.stubGlobal('speechSynthesis', { speak, cancel: vi.fn() })
    vi.stubGlobal('SpeechSynthesisUtterance', class { text: string; rate = 1; constructor(t: string) { this.text = t } })
    const { container } = render(<PatientView s={mockState({ phase: 'CALIBRATING', calibration: alphaCal('eyes_closed') })} />)
    expect(screen.getByTestId('alpha-overlay')).toHaveAttribute('data-sub', 'eyes_closed')
    expect(screen.getByText('CLOSE your eyes')).toBeInTheDocument()
    expect(screen.getByText(/7 s/)).toBeInTheDocument()
    expect(container.querySelector('[data-testid="alpha-overlay"] .stim-layer')).toBeNull()
    expect(speak).toHaveBeenCalledTimes(1)
    expect(speak.mock.calls[0][0].text).toMatch(/Close your eyes/)
  })

  it('shows the OPEN cue with a fixation cross', () => {
    render(<PatientView s={mockState({ phase: 'CALIBRATING', calibration: alphaCal('eyes_open') })} />)
    expect(screen.getByText('Keep your eyes OPEN')).toBeInTheDocument()
    expect(screen.getByText('+')).toBeInTheDocument()
  })
})
