import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import InputsTab, { GestureCalibrationView, InputStrip } from '../components/Inputs'
import MultimodalBench from '../components/MultimodalBench'
import PatientView from '../components/PatientView'
import { MOCKS } from '../mockMultimodal'
import { answerMethods, bestFor, type InputBackend, MULTIMODAL_CONTRACT, readInputs, readMultimodalBench, type PolicyState } from '../multimodal'
import type { State } from '../types'
import { mockState } from './mockState'

let fetchMock: ReturnType<typeof vi.fn>
beforeEach(() => { fetchMock = vi.fn(async () => new Response('{}')); vi.stubGlobal('fetch', fetchMock) })
afterEach(() => vi.unstubAllGlobals())
const posted = () => fetchMock.mock.calls.filter(([, i]) => i?.method === 'POST')

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

describe('movement is never assumed', () => {
  it('ships with the v2.0 contract still PROPOSED', () => {
    expect(MULTIMODAL_CONTRACT).toBe('proposed')
  })

  it('while proposed, ignores any backend input fields and stays EEG-only', () => {
    const vm = readInputs(withInput(backendInput()))
    expect(vm.provenance).toBe('eeg_only_default')
    expect(vm.mode).toBe('eeg_only')
    expect(vm.modesAvailable).toEqual(['eeg_only'])
    for (const g of ['nod_yes', 'shake_no', 'tilt_nav'] as const) {
      expect(vm.methods[g].active).toBe(false)
      expect(vm.methods[g].status).toBe('unavailable')
    }
    expect(vm.methods.eeg_ssvep.active).toBe(true)
  })

  it('a motion sensor in the data stream does not mean the patient can move', () => {
    const vm = readInputs(mockState({ source: SOURCE_WITH_IMU }))
    expect(vm.sensorInStream).toBe(true)
    expect(vm.methods.nod_yes.active).toBe(false)
  })

  it('a gesture is active only when the backend says calibrated AND enabled', () => {
    const vm = readInputs(withInput(backendInput()), 'confirmed')
    expect(vm.provenance).toBe('backend')
    expect(vm.methods.nod_yes.active).toBe(true)
    expect(vm.methods.shake_no.active).toBe(false) // calibrated but switched off
    expect(vm.methods.tilt_nav.active).toBe(false) // enabled but not calibrated
  })

  it('the live patient screen is unchanged while the contract is proposed', () => {
    render(<PatientView s={withInput(backendInput(), { phase: 'PREDICTION_HIGHLIGHTED',
      attempt: { kind: 'confirm', targets: [{ key: 'yes', label: 'Yes', freq: 7.5 }, { key: 'no', label: 'No', freq: 12 }], elapsed_s: 0, max_window_s: 5, min_window_s: 1, calibrated: false, last: null } })} />)
    expect(screen.queryByTestId('input-strip')).toBeNull()
    expect(screen.queryByText(/nod/i)).toBeNull()
    expect(screen.getByTestId('decision-yes')).toBeInTheDocument() // EEG YES/NO unchanged
  })
})

describe('adaptive input visualization (confirmed backend state)', () => {
  const policy: PolicyState = { action: 'confirm', selected: 'nod_yes', source: 'calibrated', constraints: ['Final message: decoded YES required'],
    options: [{ method: 'nod_yes', allowed: true, expected_latency_s: 0.9, confidence: 0.95, reason: 'fastest calibrated YES' },
      { method: 'eeg_ssvep', allowed: true, expected_latency_s: 3.1, confidence: 0.9, reason: 'fallback' }] }

  it('offers only active, policy-allowed gestures next to EEG YES/NO', () => {
    const vm = readInputs(withInput(backendInput(), { policy }), 'confirmed')
    expect(answerMethods(vm, 'confirm')).toEqual(['eeg_ssvep', 'nod_yes'])
    expect(answerMethods(vm, 'choose')).toEqual(['eeg_ssvep'])
    render(<InputStrip vm={vm} action="confirm" />)
    expect(screen.getByTestId('answer-with')).toHaveTextContent('look at YES / NO')
    expect(screen.getByTestId('answer-with')).toHaveTextContent('nod = YES')
    expect(screen.getByTestId('answer-with')).not.toHaveTextContent('shake = NO')
  })

  it('a policy that disallows a gesture for the final review removes it', () => {
    const review: PolicyState = { ...policy, action: 'review', options: [{ method: 'nod_yes', allowed: false, expected_latency_s: null, confidence: null, reason: 'final message: EEG YES required' }] }
    const vm = readInputs(withInput(backendInput(), { policy: review }), 'confirmed')
    expect(answerMethods(vm, 'review')).toEqual(['eeg_ssvep'])
  })

  it('conflicting or movement-disturbed signals visibly abstain', () => {
    const vm = readInputs(withInput(backendInput(), { feedback: { type: 'conflict', label: '', reason: 'Head nodded while EEG favoured NO.' } }), 'confirmed')
    render(<InputStrip vm={vm} action="confirm" />)
    expect(screen.getByTestId('abstain-banner')).toHaveTextContent('Signals disagreed: nothing was selected')
    expect(screen.getByTestId('abstain-banner')).toHaveAttribute('role', 'status')
  })

  it('motion-artifact abstain shows even in EEG-only mode', () => {
    const vm = readInputs(mockState({ feedback: { type: 'motion_artifact', label: '' } }))
    render(<InputStrip vm={vm} action="confirm" />)
    expect(screen.getByTestId('abstain-banner')).toHaveTextContent('Head movement disturbed the brain signal')
  })

  it('a rejected gesture is shown as not used', () => {
    const inp = { ...backendInput(), last_gesture: { gesture: 'nod', confidence: 0.7, accepted: false, used_for: null, reason: 'EEG disagreed', time: 1 } }
    render(<InputStrip vm={readInputs(withInput(inp), 'confirmed')} action="confirm" />)
    expect(screen.getByTestId('last-gesture')).toHaveTextContent('nod seen but not used: EEG disagreed')
  })
})

describe('capability calibration UX', () => {
  it('default tab: EEG-only active, other modes unavailable, controls inert', () => {
    render(<InputsTab s={mockState({ source: SOURCE_WITH_IMU })} />)
    expect(screen.getByTestId('mode-eeg_only')).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByTestId('mode-hybrid')).toBeDisabled()
    expect(screen.getByTestId('mode-hybrid')).toHaveTextContent('Not available')
    expect(screen.getAllByText(/Movement is never assumed/).length).toBeGreaterThan(0)
    expect(screen.getByTestId('imu-status')).toHaveTextContent('Not evaluated')
    expect(screen.getByText('Start movement check').closest('fieldset')).toBeDisabled()
    fireEvent.click(screen.getByText('Start movement check'))
    expect(posted()).toEqual([])
  })

  it('mock preview is always under a MOCK banner and never touches the backend', () => {
    render(<InputsTab s={mockState()} />)
    fireEvent.click(screen.getByText('Patient with reliable residual head movement'))
    const prev = screen.getByTestId('mock-preview')
    expect(within(prev).getByTestId('mock-banner')).toHaveTextContent('MOCK · UI PREVIEW · NOT BACKEND STATE')
    expect(within(prev).getByTestId('policy-selected')).toHaveTextContent('Head nod')
    // the real (non-mock) section still says EEG-only
    expect(screen.getAllByTestId('mode-eeg_only')[0]).toHaveAttribute('aria-checked', 'true')
    expect(posted()).toEqual([])
  })

  it('confirmed backend: mode switch and gesture switches send contract commands; uncalibrated gestures cannot be turned on', () => {
    render(<InputsTab s={withInput(backendInput())} contract="confirmed" />)
    fireEvent.click(screen.getByTestId('mode-eeg_only'))
    expect(JSON.parse(String(posted()[0][1]!.body))).toEqual({ action: 'set_input_mode', mode: 'eeg_only' })
    fireEvent.click(screen.getByText('Head shake: turn on'))
    expect(JSON.parse(String(posted()[1][1]!.body))).toEqual({ action: 'set_gesture_enabled', gesture: 'shake_no', on: true })
    expect(screen.getByTestId('mode-hybrid')).toBeDisabled() // already active
    expect(screen.getByText('Head tilt: turn OFF')).toBeEnabled() // switching OFF is always allowed
    expect(screen.getByTestId('method-nod_yes')).toHaveAttribute('data-active', 'true')
  })

  it('guided calibration shows rest, cue and feedback, and says moving is optional', () => {
    const cal = { gesture: 'nod', sub: 'rest' as const, trial: 1, trials: 5, step: 1, steps: 6, elapsed: 2, durations: { rest: 10, cue: 4, feedback: 1.5 }, last_trial: null }
    const { rerender } = render(<GestureCalibrationView cal={cal} speak={false} />)
    expect(screen.getByText('Stay still and relax')).toBeInTheDocument()
    expect(screen.getByText('8 s')).toBeInTheDocument()
    expect(screen.getByText(/works with brain signals alone/)).toBeInTheDocument()
    rerender(<GestureCalibrationView cal={{ ...cal, sub: 'cue' }} speak={false} />)
    expect(screen.getByText('Nod your head YES, gently')).toBeInTheDocument()
    rerender(<GestureCalibrationView cal={{ ...cal, sub: 'feedback', last_trial: { detected: false, seen: null, confidence: null } }} speak={false} />)
    expect(screen.getByText('Not detected. That’s OK.')).toBeInTheDocument()
  })

  it('every mock is labelled mock and none claims a real origin', () => {
    for (const m of Object.values(MOCKS)) {
      expect(m.provenance).toBe('mock')
      expect(m.imu?.origin ?? 'simulated').not.toBe('real')
    }
  })
})

describe('benchmark: strategies x patient capability', () => {
  const rows = [
    { capability_profile: 'no_movement', condition: 'adaptive_eeg', runs: 10, correct_msgs_per_min: 1.2, final_sent_without_confirm: 0 },
    { capability_profile: 'no_movement', condition: 'hybrid', runs: 10, correct_msgs_per_min: 1.1, final_sent_without_confirm: 0 },
    { capability_profile: 'reliable_movement', condition: 'hybrid', runs: 10, correct_msgs_per_min: 2.4, ci95: [2.0, 2.8] as [number, number], final_sent_without_confirm: 0 },
    { capability_profile: 'reliable_movement', condition: 'optimized', runs: 10, correct_msgs_per_min: 3.0, final_sent_without_confirm: 1 },
  ]
  const bench = { available: true, data_origin: 'simulated', real_eeg: false, multimodal: { rows } }

  it('reads nothing while the contract is proposed', () => {
    expect(readMultimodalBench(bench)).toBeNull()
    render(<MultimodalBench mm={null} />)
    expect(screen.getByTestId('mm-pending')).toHaveTextContent('not published yet')
    expect(screen.getAllByTestId('mm-bar').every((b) => b.dataset.measured === 'false')).toBe(true)
    expect(screen.getAllByText('NOT MEASURED').length).toBeGreaterThanOrEqual(12)
  })

  it('best-for-patient comes from data and excludes rows that sent without a final YES', () => {
    expect(bestFor(rows, 'no_movement')?.condition).toBe('adaptive_eeg')
    expect(bestFor(rows, 'reliable_movement')?.condition).toBe('hybrid')
    expect(bestFor(rows, 'unreliable_movement')).toBeNull()
  })

  it('renders the patient-specific answer, labelled simulated', () => {
    render(<MultimodalBench mm={readMultimodalBench(bench, 'confirmed')} />)
    expect(screen.getByText('BENCHMARK · SIMULATED')).toBeInTheDocument()
    expect(within(screen.getByTestId('mm-no_movement')).getByText(/Adaptive EEG-only · best for this patient/)).toBeInTheDocument()
    expect(within(screen.getByTestId('mm-reliable_movement')).getByText(/Hybrid EEG \+ IMU · best for this patient/)).toBeInTheDocument()
    expect(screen.getByTestId('mm-table')).toHaveTextContent('sent w/o final YES')
  })
})
