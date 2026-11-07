import type { InputsVM, MethodState } from './multimodal'

/**
 * MOCK UI states for the operator "Inputs" tab preview ONLY.
 * Illustrative values, not measurements, not backend state. Every component that renders
 * these shows "MOCK · UI PREVIEW · NOT BACKEND STATE". They are never passed to the live patient screen.
 */

export type MockKey = 'no_movement' | 'reliable_movement' | 'unreliable_movement' | 'conflict' | 'calibrating'

export const MOCK_LABEL: Record<MockKey, string> = {
  no_movement: 'Patient with no reliable head movement',
  reliable_movement: 'Patient with reliable residual head movement',
  unreliable_movement: 'Patient whose movement calibration was unreliable',
  conflict: 'Conflicting signals (EEG says NO, head nods)',
  calibrating: 'Guided head-nod calibration in progress',
}

const eeg: MethodState = { status: 'available', enabled: true, reason: 'Default (synthetic-derived) thresholds', calibration: null }
const off = (reason: string): MethodState & { active: boolean } => ({ status: 'unavailable', enabled: false, reason, calibration: null, active: false })
const cal = (detected: number, falses: number, verdict: 'pass' | 'unreliable' | 'fail', lat: number | null) =>
  ({ origin: 'simulated', attempts: 5, detected, rest_s: 30, rest_false_triggers: falses, median_latency_s: lat, confidence: verdict === 'pass' ? 0.95 : 0.6, verdict })

const base: Omit<InputsVM, 'methods' | 'mode' | 'modesAvailable' | 'modeReason' | 'policy'> = {
  provenance: 'mock', requested: 'eeg_only', motionRestart: false, sensorInStream: true, imu: { present: true, streaming: true, origin: 'simulated', moving: false, motion_level: 0.05, eeg_contaminated: false, seconds_since_last_sample: 0.01 },
  lastGesture: null, gestureCal: null, abstain: null,
}

export const MOCKS: Record<MockKey, InputsVM> = {
  no_movement: {
    ...base, mode: 'eeg_only', modesAvailable: ['eeg_only'], modeReason: 'Head-movement check failed: no reliable nod or shake. EEG-only.',
    methods: {
      eeg_ssvep: { ...eeg, active: true },
      nod_yes: { status: 'unreliable', enabled: false, reason: 'Detected 0 of 5 requested nods', calibration: cal(0, 0, 'fail', null), active: false },
      shake_no: { status: 'unreliable', enabled: false, reason: 'Detected 1 of 5 requested shakes', calibration: cal(1, 0, 'fail', null), active: false },
      tilt_nav: off('Not calibrated'),
    },
    policy: { action: 'confirm', selected: 'eeg_ssvep', source: 'assumption', constraints: ['Gestures unreliable for this patient: EEG only'],
      options: [{ method: 'eeg_ssvep', allowed: true, expected_latency_s: 3.1, confidence: 0.9, reason: 'Only reliable input' },
        { method: 'nod_yes', allowed: false, expected_latency_s: null, confidence: null, reason: 'Calibration failed' }] },
  },
  reliable_movement: {
    ...base, mode: 'hybrid', modesAvailable: ['eeg_only', 'hybrid'], modeReason: 'Nod and shake passed calibration; caregiver enabled them.',
    methods: {
      eeg_ssvep: { ...eeg, active: true },
      nod_yes: { status: 'calibrated', enabled: true, reason: null, calibration: cal(5, 0, 'pass', 0.9), active: true },
      shake_no: { status: 'calibrated', enabled: true, reason: null, calibration: cal(5, 0, 'pass', 1.0), active: true },
      tilt_nav: { status: 'not_calibrated', enabled: false, reason: 'Optional; not calibrated', calibration: null, active: false },
    },
    policy: { action: 'confirm', selected: 'nod_yes', source: 'calibrated', constraints: ['EEG still selects the choice', 'Conflict between EEG and head ⇒ abstain', 'Final message: decoded YES required'],
      options: [{ method: 'nod_yes', allowed: true, expected_latency_s: 0.9, confidence: 0.95, reason: 'Fastest calibrated YES' },
        { method: 'eeg_ssvep', allowed: true, expected_latency_s: 3.1, confidence: 0.9, reason: 'Always available as fallback' },
        { method: 'tilt_nav', allowed: false, expected_latency_s: null, confidence: null, reason: 'Not calibrated' }] },
    lastGesture: { gesture: 'nod', confidence: 0.96, accepted: true, used_for: 'yes', reason: 'calibrated nod', time: 0 },
  },
  unreliable_movement: {
    ...base, mode: 'eeg_only', modesAvailable: ['eeg_only'], modeReason: 'Movement detected but with false triggers at rest: kept EEG-only.',
    methods: {
      eeg_ssvep: { ...eeg, active: true },
      nod_yes: { status: 'unreliable', enabled: false, reason: '4 of 5 nods but 3 false triggers in 30 s rest', calibration: cal(4, 3, 'unreliable', 1.2), active: false },
      shake_no: { status: 'unreliable', enabled: false, reason: '3 of 5 shakes, 2 false triggers', calibration: cal(3, 2, 'unreliable', 1.4), active: false },
      tilt_nav: off('Not calibrated'),
    },
    policy: { action: 'confirm', selected: 'eeg_fallback', source: 'calibrated', constraints: ['Gesture false-trigger rate too high: EEG fallback'],
      options: [{ method: 'nod_yes', allowed: false, expected_latency_s: 1.2, confidence: 0.6, reason: 'False triggers at rest' },
        { method: 'eeg_ssvep', allowed: true, expected_latency_s: 3.1, confidence: 0.9, reason: 'Reliable' }] },
  },
  conflict: {
    ...base, mode: 'hybrid', modesAvailable: ['eeg_only', 'hybrid'], modeReason: 'Hybrid enabled.',
    methods: {
      eeg_ssvep: { ...eeg, active: true },
      nod_yes: { status: 'calibrated', enabled: true, reason: null, calibration: cal(5, 0, 'pass', 0.9), active: true },
      shake_no: { status: 'calibrated', enabled: true, reason: null, calibration: cal(5, 0, 'pass', 1.0), active: true },
      tilt_nav: off('Not calibrated'),
    },
    imu: { present: true, streaming: true, origin: 'simulated', moving: true, motion_level: 0.7, eeg_contaminated: true, seconds_since_last_sample: 0.01 },
    policy: null,
    lastGesture: { gesture: 'nod', confidence: 0.8, accepted: false, used_for: null, reason: 'EEG favoured NO at the same time', time: 0 },
    abstain: { type: 'conflict', text: 'Head nodded (YES) while the brain signal favoured NO. Nothing was selected.' },
  },
  calibrating: {
    ...base, mode: 'eeg_only', modesAvailable: ['eeg_only'], modeReason: 'Calibrating head movement. EEG-only until it passes.',
    methods: {
      eeg_ssvep: { ...eeg, active: true },
      nod_yes: { status: 'calibrating', enabled: false, reason: null, calibration: null, active: false },
      shake_no: { status: 'not_calibrated', enabled: false, reason: null, calibration: null, active: false },
      tilt_nav: off('Optional'),
    },
    policy: null,
    gestureCal: { gesture: 'nod', sub: 'cue', trial: 2, trials: 5, step: 2, steps: 7, elapsed: 1.2, durations: { rest: 10, cue: 4, feedback: 1.5 },
      last_trial: { detected: true, seen: 'nod', confidence: 0.93 } },
  },
}
