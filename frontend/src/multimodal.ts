import { calibratedForCurrent } from './mode'
import type { State } from './types'

/**
 * Multimodal input (EEG + optional head-movement IMU): view model + adapter.
 *
 * CONTRACT STATUS: the backend fields below are a PROPOSAL (contract v2.0, GitHub issue #1),
 * not yet confirmed or published by Agent A. While MULTIMODAL_CONTRACT === 'proposed' the
 * adapter ignores any `input` / `policy` keys and reports EEG-only, so the live demo cannot
 * show a gesture the backend has not confirmed. Integration = verify against real backend
 * output, adjust names, then flip this to 'confirmed'.
 *
 * Product rule: NEVER assume a patient can move their head. Gestures are only "active" when
 * the backend says the method is calibrated AND enabled.
 */
export const MULTIMODAL_CONTRACT: 'proposed' | 'confirmed' = 'proposed'

export type MethodKey = 'eeg_ssvep' | 'nod_yes' | 'shake_no' | 'tilt_nav'
export type MethodStatus = 'available' | 'unavailable' | 'not_calibrated' | 'calibrating' | 'calibrated' | 'unreliable' | 'disabled'
export type InputMode = 'eeg_only' | 'hybrid' | 'motion_assisted'

export interface GestureCalib {
  origin: string; attempts: number; detected: number; rest_s: number; rest_false_triggers: number
  median_latency_s: number | null; confidence: number | null; verdict: 'pass' | 'unreliable' | 'fail'; at?: number
}
export interface MethodState { status: MethodStatus; enabled: boolean; reason: string | null; calibration: GestureCalib | null }
export interface PolicyOption { method: MethodKey | 'eeg_fallback'; allowed: boolean; expected_latency_s: number | null; confidence: number | null; reason: string }
export interface PolicyState {
  action: 'choose' | 'confirm' | 'review' | 'navigate'
  selected: MethodKey | 'eeg_fallback'; options: PolicyOption[]; constraints: string[]; source: 'calibrated' | 'assumption'
}
export interface ImuState { present: boolean; streaming: boolean; origin: string; moving: boolean; motion_level: number; eeg_contaminated: boolean; seconds_since_last_sample: number | null }
export interface GestureEvent { gesture: string; confidence: number; accepted: boolean; used_for: 'yes' | 'no' | 'nav' | null; reason: string; time: number }
export interface GestureCalibSession {
  gesture: string; sub: 'rest' | 'cue' | 'feedback' | 'computing'; trial: number; trials: number; step: number; steps: number
  elapsed: number; durations: Record<string, number>; last_trial: null | { detected: boolean; seen: string | null; confidence: number | null }
}
/** Proposed backend shape (contract v2.0, unconfirmed). */
export interface InputBackend {
  mode: InputMode; modes_available: InputMode[]; mode_reason: string
  methods: Partial<Record<MethodKey, MethodState>>
  imu: ImuState | null; last_gesture: GestureEvent | null; gesture_calibration: GestureCalibSession | null
}

export const METHOD_INFO: Record<MethodKey | 'eeg_fallback', { label: string; purpose: string; icon: string }> = {
  eeg_ssvep: { label: 'EEG target selection', purpose: 'look at a flickering choice', icon: '👁' },
  nod_yes: { label: 'Head nod', purpose: 'YES', icon: '↕' },
  shake_no: { label: 'Head shake', purpose: 'NO', icon: '↔' },
  tilt_nav: { label: 'Head tilt', purpose: 'navigate (optional)', icon: '⤢' },
  eeg_fallback: { label: 'EEG-only fallback', purpose: 'gesture not usable now', icon: '👁' },
}
export const MODE_INFO: Record<InputMode, { label: string; text: string }> = {
  eeg_only: { label: 'EEG-only', text: 'Every choice and every YES/NO by brain signal (SSVEP). Works with no movement at all.' },
  hybrid: { label: 'Hybrid EEG + head', text: 'EEG picks the choice; a calibrated nod / shake can answer YES / NO.' },
  motion_assisted: { label: 'Motion-assisted', text: 'Adds calibrated head tilt for navigation. EEG still confirms the final message.' },
}
export const STATUS_INFO: Record<MethodStatus, { text: string; cls: string; mark: string }> = {
  available: { text: 'available (default thresholds)', cls: 'bg-sky-900', mark: '○' },
  unavailable: { text: 'not available', cls: 'bg-zinc-800 text-muted', mark: '–' },
  not_calibrated: { text: 'not calibrated', cls: 'bg-amber-900', mark: '?' },
  calibrating: { text: 'calibrating…', cls: 'bg-sky-800', mark: '…' },
  calibrated: { text: 'calibrated', cls: 'bg-green-900', mark: '✓' },
  unreliable: { text: 'unreliable for this patient', cls: 'bg-red-950', mark: '✗' },
  disabled: { text: 'disabled by caregiver', cls: 'bg-zinc-700', mark: '⏻' },
}

export type Provenance = 'backend' | 'eeg_only_default' | 'mock'
export interface InputsVM {
  provenance: Provenance
  mode: InputMode; modesAvailable: InputMode[]; modeReason: string
  methods: Record<MethodKey, MethodState & { active: boolean }>
  /** accel/gyro channels exist in the data stream (existing contract field); says nothing about the patient */
  sensorInStream: boolean
  imu: ImuState | null; policy: PolicyState | null; lastGesture: GestureEvent | null; gestureCal: GestureCalibSession | null
  /** visible abstain caused by conflicting or motion-contaminated signals (backend feedback) */
  abstain: null | { type: 'conflict' | 'motion_artifact'; text: string }
}

const GESTURES: MethodKey[] = ['nod_yes', 'shake_no', 'tilt_nav']

function eegState(s: State): MethodState {
  if (s.mode.kind === 'none') return { status: 'unavailable', enabled: false, reason: 'No EEG source connected', calibration: null }
  const cal = calibratedForCurrent(s)
  if (cal) return { status: 'calibrated', enabled: true, reason: null, calibration: null }
  if (s.mode.calibration_required ?? s.mode.origin === 'real') return { status: 'not_calibrated', enabled: true, reason: 'Real-origin EEG must be calibrated before it can communicate', calibration: null }
  return { status: 'available', enabled: true, reason: 'Default (synthetic-derived) thresholds', calibration: null }
}

/** A gesture only counts as active when the backend says calibrated AND enabled. */
export function isActive(m: MethodState | undefined): boolean {
  return !!m && m.status === 'calibrated' && m.enabled === true
}

function withActive(methods: Partial<Record<MethodKey, MethodState>>, eeg: MethodState, fallbackReason: string): InputsVM['methods'] {
  const out = {} as InputsVM['methods']
  const eegM = methods.eeg_ssvep ?? eeg
  out.eeg_ssvep = { ...eegM, active: eegM.status !== 'unavailable' && eegM.status !== 'disabled' }
  for (const g of GESTURES) {
    const m = methods[g] ?? { status: 'unavailable' as const, enabled: false, reason: fallbackReason, calibration: null }
    out[g] = { ...m, active: isActive(m) }
  }
  return out
}

function abstainFrom(s: State): InputsVM['abstain'] {
  const t = s.feedback?.type
  if (t === 'conflict') return { type: 'conflict', text: s.feedback?.reason ?? 'Brain signal and head movement disagreed' }
  if (t === 'motion_artifact') return { type: 'motion_artifact', text: s.feedback?.reason ?? 'Head movement disturbed the EEG' }
  return null
}

export function readInputs(s: State, contract: 'proposed' | 'confirmed' = MULTIMODAL_CONTRACT): InputsVM {
  const sensorInStream = !!s.source?.channels?.some((c) => c.kind === 'accel' || c.kind === 'gyro')
  const raw = (s as State & { input?: InputBackend; policy?: PolicyState }).input
  const policy = (s as State & { policy?: PolicyState }).policy ?? null
  if (contract === 'confirmed' && raw && typeof raw === 'object' && raw.methods) {
    return {
      provenance: 'backend', mode: raw.mode ?? 'eeg_only', modesAvailable: raw.modes_available ?? ['eeg_only'], modeReason: raw.mode_reason ?? '',
      methods: withActive(raw.methods, eegState(s), 'Not reported by the backend'),
      sensorInStream, imu: raw.imu ?? null, policy, lastGesture: raw.last_gesture ?? null, gestureCal: raw.gesture_calibration ?? null,
      abstain: abstainFrom(s),
    }
  }
  return {
    provenance: 'eeg_only_default', mode: 'eeg_only', modesAvailable: ['eeg_only'],
    modeReason: 'Head-movement input is not published by the backend yet, so NeuroSteno runs EEG-only. Movement is never assumed.',
    methods: withActive({}, eegState(s), 'Not available: the backend does not publish gesture input yet'),
    sensorInStream, imu: null, policy: null, lastGesture: null, gestureCal: null, abstain: abstainFrom(s),
  }
}

/**
 * Team confirmation policy (2026-11-07), enforced here regardless of what any backend policy says:
 *  - EEG confirmation is ALWAYS available.
 *  - A calibrated AND explicitly enabled head nod may answer YES for normal navigation (action "confirm").
 *  - Final SEND (action "review") stays a separate, deliberate EEG confirmation. Gesture-based final
 *    confirmation is disabled until real-world false-activation testing supports it.
 */
export const GESTURE_FINAL_CONFIRM_ENABLED = false

/** What the patient can use to answer the current YES/NO, from backend state only. */
export function answerMethods(vm: InputsVM, action: 'confirm' | 'review' | 'choose' | null): MethodKey[] {
  const out: MethodKey[] = ['eeg_ssvep']
  if (action === 'review' && !GESTURE_FINAL_CONFIRM_ENABLED) return out
  if (action !== 'confirm' && action !== 'review') return out
  if (vm.policy && vm.policy.action !== action) return out
  const allowed = (k: MethodKey) => vm.methods[k].active && (!vm.policy || vm.policy.options.some((o) => o.method === k && o.allowed))
  if (allowed('nod_yes')) out.push('nod_yes')
  if (allowed('shake_no')) out.push('shake_no')
  return out
}

/* ---------------- Benchmark: strategies x patient capability (contract v2.0 PROPOSED) ---------------- */

export const STRATEGIES = [
  { key: 'fixed_eeg', label: 'Fixed EEG-only', color: 'bg-fixed' },
  { key: 'adaptive_eeg', label: 'Adaptive EEG-only', color: 'bg-neuro' },
  { key: 'hybrid', label: 'Hybrid EEG + IMU', color: 'bg-hybrid' },
  { key: 'optimized', label: 'Optimized multimodal', color: 'bg-optimized' },
] as const
export const CAPABILITIES = [
  { key: 'no_movement', label: 'No reliable head movement' },
  { key: 'reliable_movement', label: 'Reliable residual head movement' },
  { key: 'unreliable_movement', label: 'Unreliable head movement' },
] as const

export interface MMBenchRow {
  capability_profile: string; condition: string; runs: number
  correct_msgs_per_min: number; ci95?: [number, number]
  mean_selection_s?: number; corrections?: number; false_activations_per_hour?: number
  idle_false_sent_per_hour?: number; error_rate?: number; final_sent_without_confirm?: number; origin?: string
}
export interface MMBench { origin: string; real_eeg: boolean; generated_at?: string; note?: string; rows: MMBenchRow[] }

/** Reads multimodal benchmark rows only once the contract is confirmed; otherwise null (UI shows NOT MEASURED). */
export function readMultimodalBench(bench: unknown, contract: 'proposed' | 'confirmed' = MULTIMODAL_CONTRACT): MMBench | null {
  if (contract !== 'confirmed' || !bench || typeof bench !== 'object') return null
  const b = bench as { available?: boolean; data_origin?: string; real_eeg?: boolean; generated_at?: string; multimodal?: { rows?: MMBenchRow[]; note?: string; origin?: string } }
  const rows = b.multimodal?.rows
  if (!b.available || !Array.isArray(rows) || rows.length === 0) return null
  return { origin: b.multimodal?.origin ?? b.data_origin ?? 'simulated', real_eeg: b.real_eeg === true, generated_at: b.generated_at, note: b.multimodal?.note, rows }
}

/** Best strategy for one capability profile, by measured correct msgs/min, only among rows with no unconfirmed sends. */
export function bestFor(rows: MMBenchRow[], capability: string): MMBenchRow | null {
  const r = rows.filter((x) => x.capability_profile === capability && (x.final_sent_without_confirm ?? 0) === 0)
  return r.length ? r.reduce((a, b) => (b.correct_msgs_per_min > a.correct_msgs_per_min ? b : a)) : null
}
