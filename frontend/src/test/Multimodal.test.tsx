import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import InputsTab, { GestureCalibrationView, InputStrip, PolicyCard } from '../components/Inputs'
import MultimodalBench from '../components/MultimodalBench'
import PatientView from '../components/PatientView'
import { MOCKS } from '../mockMultimodal'
import {
  answerMethods, GESTURE_CONTROLS_HOLD, GESTURE_FINAL_CONFIRM_ENABLED, type GestureCalibSession, hybridVerdictCounts, type InputBackend,
  MULTIMODAL_CONTRACT, type PolicyState, readInputs, readMultimodalBench, sanitizeCalib,
} from '../multimodal'
import type { State } from '../types'
import v14bench from './fixtures/v14_multimodal_benchmark.json'
import v14 from './fixtures/v14_state.json'
import { mockState } from './mockState'

let fetchMock: ReturnType<typeof vi.fn>
beforeEach(() => { fetchMock = vi.fn(async () => new Response('{}')); vi.stubGlobal('fetch', fetchMock) })
afterEach(() => vi.unstubAllGlobals())
const posted = () => fetchMock.mock.calls.filter(([, i]) => i?.method === 'POST').map(([, i]) => JSON.parse(String(i!.body)))

/** A real v1.4 snapshot (captured live, trimmed to the multimodal keys) merged onto the UI mock. */
const live = (k: 'idle' | 'highlight' | 'review', extra: Partial<State> = {}) => mockState({ ...(v14[k] as unknown as Partial<State>), ...extra })

const SOURCE_WITH_IMU = { kind: 'simulated', sample_rate: 250, status: {} as never,
  channels: [{ index: 0, name: 'EEG 1', kind: 'eeg', unit: 'uV', label: 'Fz' }, { index: 8, name: 'Accelerometer X', kind: 'accel', unit: 'g', label: 'AccX' }] } as State['source']

const backendInput = (over: Partial<InputBackend['methods']> = {}): InputBackend => ({
  mode: 'hybrid', modes_available: ['eeg_only', 'hybrid'], mode_reason: 'nod/shake passed',
  methods: {
    nod_yes: { status: 'calibrated', enabled: true, reason: null, calibration: { origin: 'simulated', attempts: 5, detected: 5, rest_s: 30, rest_false_triggers: 0, median_latency_s: 0.9, confidence: 0.95, verdict: 'pass' } },
    shake_no: { status: 'calibrated', enabled: false, reason: 'caregiver turned off', calibration: null },
    tilt_nav: { status: 'not_calibrated', enabled: true, reason: null, calibration: null },
    ...over,
  },
  imu: { present: true, streaming: true, origin: 'simulated', moving: false, motion_level: 0.1, eeg_contaminated: false, seconds_since_last_sample: 0.01 },
  last_gesture: null, gesture_calibration: null,
})
const withInput = (input: InputBackend, extra: Partial<State> & { policy?: PolicyState } = {}) => mockState({ source: SOURCE_WITH_IMU, ...extra, ...({ input } as object) })

describe('contract status', () => {
  it('v1.4 is confirmed, gesture-starting controls are held, final SEND is EEG-only', () => {
    expect(MULTIMODAL_CONTRACT).toBe('confirmed')
    expect(GESTURE_CONTROLS_HOLD.held).toBe(true)
    expect(GESTURE_FINAL_CONFIRM_ENABLED).toBe(false)
  })

  it('the proposed-contract path still ignores backend input fields (EEG-only)', () => {
    const vm = readInputs(withInput(backendInput()), 'proposed')
    expect(vm.provenance).toBe('eeg_only_default')
    for (const g of ['nod_yes', 'shake_no', 'tilt_nav'] as const) expect(vm.methods[g].active).toBe(false)
  })
})

describe('real v1.4 responses (fixtures captured from feature/eeg-backend@d47e1e6)', () => {
  it('reads the live input / policy shape: EEG-only, gestures unreliable after the simulated check, nothing active', () => {
    const vm = readInputs(live('idle'))
    expect(vm.provenance).toBe('backend')
    expect(vm.mode).toBe('eeg_only')
    expect(vm.requested).toBe('eeg_only')
    expect(vm.methods.nod_yes.status).toBe('unreliable')
    expect(vm.methods.nod_yes.calibration?.false_per_hour_upper95).toBeGreaterThan(0)
    for (const g of ['nod_yes', 'shake_no', 'tilt_nav'] as const) expect(vm.methods[g].active).toBe(false)
    expect(vm.imu?.motion_level).toBeCloseTo(0.04)
  })

  it('during a real confirm step, only EEG is offered and the patient screen shows no gesture UI', () => {
    const s = live('highlight')
    const vm = readInputs(s)
    expect(vm.policy?.action).toBe('confirm')
    expect(answerMethods(vm, 'confirm')).toEqual(['eeg_ssvep'])
    render(<PatientView s={s} />)
    expect(screen.queryByTestId('input-strip')).toBeNull()
    expect(screen.queryByText(/nod/i)).toBeNull()
    expect(screen.getByTestId('decision-yes')).toBeInTheDocument()
  })

  it('at the real final review every gesture option is "not for final SEND" and the UI agrees', () => {
    const s = live('review')
    const vm = readInputs(s)
    expect(vm.policy?.options.filter((o) => o.method !== 'eeg_ssvep').every((o) => !o.allowed && o.reason === 'not for final SEND')).toBe(true)
    expect(answerMethods(vm, 'review')).toEqual(['eeg_ssvep'])
    render(<PolicyCard vm={vm} />)
    expect(screen.getAllByTestId('final-gesture-blocked').length).toBeGreaterThan(0)
    expect((s as State).safety_invariants).toMatchObject({ messages_sent_without_final_confirmation: 0, gesture_final_send_blocked: 0 })
  })

  it('a null motion level is shown as unknown, never as 0%', () => {
    const s = live('idle')
    ;(s as unknown as { input: InputBackend }).input.imu!.motion_level = null
    render(<InputsTab s={s} />)
    expect(screen.getByTestId('imu-status')).toHaveTextContent('motion –')
  })
})

describe('real v1.4 gesture calibration shape', () => {
  // Shapes observed live: gesture null + step_label during rest; durations.cue is per gesture; no feedback phase.
  const base = { trial: 0, trials: 9, step: 1, steps: 11, durations: { rest: 20, natural: 30, cue: { nod: 4, shake: 4, tilt: 5 }, feedback: 0 }, last_trial: null }
  const cal = (o: Partial<GestureCalibSession>) => sanitizeCalib({ ...base, ...o })!

  it('neutral rest, cued gestures with per-gesture timing, and natural movement', () => {
    const { rerender } = render(<GestureCalibrationView cal={cal({ gesture: null, sub: 'rest', step_label: 'neutral', elapsed: 5 })} speak={false} />)
    expect(screen.getByText('Keep your head still')).toBeInTheDocument()
    expect(screen.getByText('15 s')).toBeInTheDocument()
    rerender(<GestureCalibrationView cal={cal({ gesture: 'tilt', sub: 'cue', step_label: 'tilt', elapsed: 1, step: 2 })} speak={false} />)
    expect(screen.getByText('Tilt your head to the side and hold')).toBeInTheDocument()
    expect(screen.getByText('4 s')).toBeInTheDocument() // tilt cue = 5 s
    rerender(<GestureCalibrationView cal={cal({ gesture: null, sub: 'rest', step_label: 'natural', elapsed: 10, step: 11 })} speak={false} />)
    expect(screen.getByText('Move naturally (no gestures)')).toBeInTheDocument()
    expect(screen.getByText('20 s')).toBeInTheDocument()
    expect(screen.getByText(/works with brain signals alone/)).toBeInTheDocument()
  })

  it('a gesture calibration never shows SSVEP calibration targets or flicker', () => {
    const s = mockState({ phase: 'CALIBRATING', calibration: { kind: 'gesture', n: 0, freqs: [7.5, 12], trial: 0, trials: 9, sub: 'rest', cue_target: 0, rest_trial: false, elapsed: 1, durations: {} },
      ...({ input: { ...v14.idle.input, gesture_calibration: { ...base, gesture: null, sub: 'rest', step_label: 'neutral', elapsed: 1 } } } as object) })
    const { container } = render(<PatientView s={s} />)
    expect(screen.queryByText(/SSVEP calibration/)).toBeNull()
    expect(screen.getByTestId('gesture-cal-overlay')).toHaveTextContent('Keep your head still')
    expect(container.querySelectorAll('[data-testid="gesture-cal-overlay"] .stim-layer')).toHaveLength(0)
  })

  it('falls back to calibration.gesture.instruction if input.gesture_calibration is missing', () => {
    const s = mockState({ phase: 'CALIBRATING', calibration: { kind: 'gesture', n: 0, freqs: [], trial: 0, trials: 9, sub: 'rest', cue_target: null, rest_trial: false, elapsed: 1, durations: {},
      ...({ gesture: { step: 'neutral', step_s: 20, instruction: 'Keep your head still' } } as object) } as State['calibration'] })
    render(<PatientView s={s} />)
    expect(screen.getByTestId('gesture-cal-overlay')).toHaveTextContent('Keep your head still')
  })
})

describe('adaptive input visualization (confirmed backend state)', () => {
  const policy: PolicyState = { action: 'confirm', selected: 'nod_yes', source: 'calibrated', constraints: ['Final message: decoded YES required'],
    options: [{ method: 'nod_yes', allowed: true, expected_latency_s: 0.9, confidence: 0.95, reason: 'fastest calibrated YES' },
      { method: 'eeg_ssvep', allowed: true, expected_latency_s: 3.1, confidence: 0.9, reason: 'fallback' }] }

  it('a gesture is active only when the backend says calibrated AND enabled', () => {
    const vm = readInputs(withInput(backendInput()))
    expect(vm.methods.nod_yes.active).toBe(true)
    expect(vm.methods.shake_no.active).toBe(false) // calibrated but switched off
    expect(vm.methods.tilt_nav.active).toBe(false) // enabled but not calibrated
  })

  it('offers only active, policy-allowed gestures next to EEG YES/NO', () => {
    const vm = readInputs(withInput(backendInput(), { policy }))
    expect(answerMethods(vm, 'confirm')).toEqual(['eeg_ssvep', 'nod_yes'])
    expect(answerMethods(vm, 'choose')).toEqual(['eeg_ssvep'])
    render(<InputStrip vm={vm} action="confirm" />)
    expect(screen.getByTestId('answer-with')).toHaveTextContent('look at YES / NO')
    expect(screen.getByTestId('answer-with')).toHaveTextContent('nod = YES')
    expect(screen.getByTestId('answer-with')).not.toHaveTextContent('shake = NO')
  })

  it('final SEND stays EEG-only even if a backend policy allows a gesture there', () => {
    const permissive: PolicyState = { ...policy, action: 'review', options: [
      { method: 'nod_yes', allowed: true, expected_latency_s: 0.9, confidence: 0.99, reason: 'backend says ok' },
      { method: 'eeg_ssvep', allowed: true, expected_latency_s: 3, confidence: 0.9, reason: 'EEG' }] }
    const vm = readInputs(withInput(backendInput({ shake_no: { status: 'calibrated', enabled: true, reason: null, calibration: null } }), { policy: permissive }))
    expect(answerMethods(vm, 'review')).toEqual(['eeg_ssvep'])
    render(<InputStrip vm={vm} action="review" />)
    expect(screen.getByTestId('answer-with')).not.toHaveTextContent('nod = YES')
    expect(screen.getByTestId('final-eeg-only')).toHaveTextContent('Head gestures are not used to send')
    render(<PolicyCard vm={vm} />)
    expect(screen.getByTestId('final-gesture-blocked')).toHaveTextContent('not for final SEND')
  })

  it('conflicts abstain visibly; motion restarts are explained; no reliance on feedback "motion_artifact"', () => {
    const conflict = readInputs(withInput(backendInput(), { feedback: { type: 'conflict', label: '', reason: 'Head nodded while EEG favoured NO.' } }))
    render(<InputStrip vm={conflict} action="confirm" />)
    expect(screen.getByTestId('abstain-banner')).toHaveTextContent('Signals disagreed: nothing was selected')
    const restart = readInputs(withInput(backendInput(), { attempt: { kind: 'choose', targets: [], elapsed_s: 1, max_window_s: 4, min_window_s: 1, calibrated: false,
      last: { target: null, scores: [], margin: 0, window_s: 1, reliable: false, reason: '', threshold: 0, ...({ motion_restart: true } as object) } } }))
    expect(restart.motionRestart).toBe(true)
    render(<InputStrip vm={restart} action="choose" />)
    expect(screen.getByTestId('motion-restart')).toHaveTextContent('listening again. Nothing was selected')
  })

  it('auto mode shows what was requested and what is in use', () => {
    const vm = readInputs(withInput({ ...backendInput(), mode: 'eeg_only', ...({ requested: 'auto' } as object) }))
    expect(vm.requested).toBe('auto')
    render(<InputsTab s={withInput({ ...backendInput(), mode: 'eeg_only', ...({ requested: 'auto' } as object) })} />)
    expect(screen.getByTestId('mode-effective')).toHaveTextContent('Requested Auto (policy) → using EEG-only')
  })
})

describe('capability calibration UX under the hold', () => {
  it('without backend input: EEG-only, controls inert, nothing sent', () => {
    render(<InputsTab s={mockState({ source: SOURCE_WITH_IMU })} />)
    expect(screen.getByTestId('mode-eeg_only')).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByTestId('imu-status')).toHaveTextContent('Not evaluated')
    expect(screen.getByText('Start movement check')).toBeDisabled()
    fireEvent.click(screen.getByText('Start movement check'))
    expect(posted()).toEqual([])
  })

  it('with live v1.4 state: status is shown, but nothing that starts gesture activity can be sent', () => {
    render(<InputsTab s={live('idle', { sim: mockState().sim ?? { gaze_key: null, gaze_hz: null, autopilot: null, pending_mistake: false, mistake_rate: 0, noise_multiplier: 1, bad_channels: [], disconnected: false, drop_probability: 0, ssvep_uv: 1.8 } })} />)
    expect(screen.getByTestId('gesture-hold')).toHaveTextContent('numpy.int64')
    expect(screen.getByTestId('gesture-hold')).toHaveTextContent('cough/jerk')
    expect(screen.getByTestId('method-nod_yes')).toHaveAttribute('data-status', 'unreliable')
    for (const name of ['Start movement check', 'Gestures OFF: turn on', 'reliable movement', 'high motion']) {
      const b = screen.getByText(name)
      expect(b).toBeDisabled()
      fireEvent.click(b)
    }
    expect(screen.getByTestId('mode-hybrid')).toBeDisabled()
    expect(screen.getByTestId('mode-auto')).toBeDisabled()
    expect(posted()).toEqual([])
  })

  it('turning gestures OFF and returning to EEG-only stay possible under the hold', () => {
    render(<InputsTab s={withInput(backendInput())} />)
    fireEvent.click(screen.getByText('Gestures ON: turn OFF'))
    fireEvent.click(screen.getByTestId('mode-eeg_only'))
    expect(posted()).toEqual([{ action: 'set_gesture_enabled', on: false }, { action: 'set_input_mode', mode: 'eeg_only' }])
  })

  it('mock preview stays under a MOCK banner and never touches the backend', () => {
    render(<InputsTab s={mockState()} />)
    fireEvent.click(screen.getByText('Patient with reliable residual head movement'))
    expect(within(screen.getByTestId('mock-preview')).getByTestId('mock-banner')).toHaveTextContent('MOCK · UI PREVIEW · NOT BACKEND STATE')
    expect(posted()).toEqual([])
    for (const m of Object.values(MOCKS)) expect(m.provenance).toBe('mock')
  })
})

describe('real multimodal benchmark (v14 fixture)', () => {
  const mm = readMultimodalBench(v14bench)!

  it('reads 12 cells and 12 CI-based verdicts; nothing when unpublished or unconfirmed', () => {
    expect(mm.cells).toHaveLength(12)
    expect(mm.verdicts).toHaveLength(12)
    expect(mm.real_eeg).toBe(false)
    expect(mm.real_imu).toBe(false)
    expect(readMultimodalBench({ available: false })).toBeNull()
    expect(readMultimodalBench(v14bench, 'proposed')).toBeNull()
    const counts = hybridVerdictCounts(mm)
    const raw = (v14bench as { summary: { hybrid_verdicts: { always_on_hybrid_vs_adaptive_eeg: string }[] } }).summary.hybrid_verdicts
    expect(counts.better).toBe(raw.filter((v) => v.always_on_hybrid_vs_adaptive_eeg === 'better').length)
    expect(counts.worse).toBe(raw.filter((v) => v.always_on_hybrid_vs_adaptive_eeg === 'worse').length)
  })

  it('shows per-patient verdicts (including where hybrid is worse) and withholds M1/M2 idle sends', () => {
    render(<MultimodalBench mm={mm} />)
    expect(screen.getByText('BENCHMARK · SIMULATED EEG + SIMULATED HEAD MOTION')).toBeInTheDocument()
    expect(screen.getByTestId('mm-disclaimer')).toHaveTextContent('No real-patient head-motion data')
    const none = screen.getByTestId('mm-no_head_control')
    expect(within(none).getAllByTestId('mm-verdict')[0]).toHaveTextContent('Always-on hybrid vs adaptive EEG-only: worse')
    expect(screen.getAllByTestId('idle-withheld').length).toBe(8) // M1 + M2 x 4 profiles
    expect(screen.getAllByTestId('idle-withheld').every((td) => td.textContent === 'under investigation')).toBe(true)
    fireEvent.click(screen.getByRole('radio', { name: /low/ }))
    expect(within(screen.getByTestId('mm-accurate')).getAllByTestId('mm-verdict')[0]).toHaveTextContent('better')
  })

  it('shows NOT MEASURED when the report is unavailable', () => {
    render(<MultimodalBench mm={null} />)
    expect(screen.getByTestId('mm-pending')).toBeInTheDocument()
    expect(screen.getAllByTestId('mm-bar').every((b) => b.dataset.measured === 'false')).toBe(true)
  })
})
