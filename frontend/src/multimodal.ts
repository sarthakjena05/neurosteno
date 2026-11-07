import { calibratedForCurrent } from './mode'
import type { State } from './types'

/**
 * Multimodal input (EEG + optional head-movement IMU): view model + adapter.
 *
 * CONTRACT STATUS: confirmed by Agent A as contract v1.4 (issue #1, backend
 * feature/eeg-backend@331358b / d47e1e6), which publishes the `input` / `policy` shapes
 * proposed as v2.0. Verified against live responses; fixtures captured in
 * src/test/fixtures/v14_*.json. Differences from the proposal handled below: mode "auto",
 * `requested`, `durations.cue` per gesture, `gesture: null` + `step_label` during rest, no
 * "feedback" calibration sub-phase, no `feedback.type = "motion_artifact"` (motion restarts the
 * EEG window instead), one global gesture switch.
 *
 * Product rule: NEVER assume a patient can move their head. Gestures are only "active" when
 * the backend says the method is calibrated AND enabled.
 */
export const MULTIMODAL_CONTRACT: 'proposed' | 'confirmed' = 'confirmed'

/**
 * Controls that START gesture activity (movement check, gesture switch, input-mode change,
 * simulated gestures) are held in the UI. The state display is live; nothing that creates
 * gesture events can be triggered from the frontend while this hold is on.
 */
export const GESTURE_CONTROLS_HOLD = {
  held: true,
  reasons: [
    'Agent A: keep gesture input off in the demo until the nod-like cough/jerk false-acceptance fix lands.',
    'Backend v1.4 (d47e1e6): the first gesture event makes the state unserialisable (numpy.int64 in gestures.events[].sample), which stops the live feed. Reported on issue #1.',
  ],
} as const

export type MethodKey = 'eeg_ssvep' | 'nod_yes' | 'shake_no' | 'tilt_nav'
export type MethodStatus = 'available' | 'unavailable' | 'not_calibrated' | 'calibrating' | 'calibrated' | 'unreliable' | 'disabled'
export type InputMode = 'eeg_only' | 'hybrid' | 'motion_assisted' | 'auto'

export interface GestureCalib {
  origin: string; attempts: number; detected: number; rest_s: number; rest_false_triggers: number
  median_latency_s: number | null; confidence: number | null; verdict: 'pass' | 'unreliable' | 'fail'; at?: number
  /** v1.4 extras */
  claim?: string; false_per_hour_upper95?: number | null
}
export interface MethodState { status: MethodStatus; enabled: boolean; reason: string | null; calibration: GestureCalib | null }
export interface PolicyOption { method: MethodKey | 'eeg_fallback'; allowed: boolean; expected_latency_s: number | null; confidence: number | null; reason: string }
export interface PolicyState {
  action: 'choose' | 'confirm' | 'review' | 'navigate'
  selected: MethodKey | 'eeg_fallback'; options: PolicyOption[]; constraints: string[]; source: 'calibrated' | 'assumption'
}
export interface ImuState {
  present: boolean; streaming: boolean; origin: string; moving: boolean
  /** v1.4 may report null (no estimate yet): never shown as 0 */
  motion_level: number | null
  eeg_contaminated: boolean; seconds_since_last_sample: number | null
  claim?: string; reason?: string
}
export interface GestureEvent { gesture: string; confidence: number; accepted: boolean; used_for: 'yes' | 'no' | 'nav' | null; reason: string; time: number }
export interface GestureCalibSession {
  /** null during rest phases (v1.4) */
  gesture: string | null
  /** v1.4: neutral | natural | nod | shake | tilt */
  step_label?: string
  sub: 'rest' | 'cue' | 'feedback' | 'computing'; trial: number; trials: number; step: number; steps: number
  /** v1.4: {rest, natural, feedback, cue: {nod, shake, tilt}} */
  elapsed: number; durations: Record<string, number | Record<string, number>>
  last_trial: null | { detected: boolean; seen: string | null; confidence: number | null }
}

/** Seconds of the current calibration sub-phase, handling v1.4's per-gesture cue durations. */
export function calibPhaseSeconds(cal: GestureCalibSession): number | null {
  const key = cal.sub === 'rest' && cal.step_label === 'natural' ? 'natural' : cal.sub
  const d = cal.durations?.[key]
  if (typeof d === 'number') return d
  if (d && typeof d === 'object' && cal.gesture && typeof d[cal.gesture] === 'number') return d[cal.gesture]
  return null
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
  auto: { label: 'Auto (policy)', text: 'The policy picks the fastest mode that meets every safety constraint; otherwise EEG-only.' },
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
  /** effective mode in use */
  mode: InputMode
  /** what the caregiver asked for (e.g. "auto"); v1.4 `input.requested` */
  requested: InputMode
  modesAvailable: InputMode[]; modeReason: string
  /** v1.4: EEG window restarted because head movement contaminated it (attempt.last.motion_restart) */
  motionRestart: boolean
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

/* ---- defensive parsing of the (future) backend payload: unknown or malformed ⇒ the safe value ---- */
const STATUSES: MethodStatus[] = ['available', 'unavailable', 'not_calibrated', 'calibrating', 'calibrated', 'unreliable', 'disabled']
const MODES: InputMode[] = ['eeg_only', 'hybrid', 'motion_assisted', 'auto']
const POLICY_METHODS = ['eeg_ssvep', 'nod_yes', 'shake_no', 'tilt_nav', 'eeg_fallback']
function isObj(v: unknown): v is Record<string, unknown> { return !!v && typeof v === 'object' && !Array.isArray(v) }
function isMode(v: unknown): v is InputMode { return MODES.includes(v as InputMode) }
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

export function sanitizeMethod(v: unknown): MethodState | null {
  if (!isObj(v)) return null
  const status = STATUSES.includes(v.status as MethodStatus) ? v.status as MethodStatus : 'unavailable'
  return { status, enabled: v.enabled === true, reason: typeof v.reason === 'string' ? v.reason : null, calibration: isObj(v.calibration) ? v.calibration as unknown as GestureCalib : null }
}
export function sanitizeImu(v: unknown): ImuState | null {
  if (!isObj(v)) return null
  return { present: v.present === true, streaming: v.streaming === true, origin: typeof v.origin === 'string' ? v.origin : 'unknown',
    moving: v.moving === true, motion_level: num(v.motion_level) == null ? null : Math.max(0, Math.min(1, num(v.motion_level)!)), eeg_contaminated: v.eeg_contaminated === true,
    claim: typeof v.claim === 'string' ? v.claim : undefined, reason: typeof v.reason === 'string' ? v.reason : undefined,
    seconds_since_last_sample: num(v.seconds_since_last_sample) }
}
export function sanitizeCalib(v: unknown): GestureCalibSession | null {
  if (!isObj(v) || !['rest', 'cue', 'feedback', 'computing'].includes(String(v.sub))) return null
  return { gesture: typeof v.gesture === 'string' ? v.gesture : null, step_label: typeof v.step_label === 'string' ? v.step_label : undefined,
    sub: v.sub as GestureCalibSession['sub'], trial: num(v.trial) ?? 0, trials: num(v.trials) ?? 0, step: num(v.step) ?? 0, steps: num(v.steps) ?? 0,
    elapsed: num(v.elapsed) ?? 0, durations: isObj(v.durations) ? v.durations as GestureCalibSession['durations'] : {},
    last_trial: isObj(v.last_trial) ? v.last_trial as GestureCalibSession['last_trial'] : null }
}
export function sanitizeGesture(v: unknown): GestureEvent | null {
  if (!isObj(v) || typeof v.gesture !== 'string') return null
  const used = v.used_for === 'yes' || v.used_for === 'no' || v.used_for === 'nav' ? v.used_for : null
  return { gesture: v.gesture, confidence: num(v.confidence) ?? 0, accepted: v.accepted === true, used_for: used, reason: typeof v.reason === 'string' ? v.reason : '', time: num(v.time) ?? 0 }
}
export function sanitizePolicy(v: unknown): PolicyState | null {
  if (!isObj(v) || !Array.isArray(v.options)) return null
  const options = (v.options as unknown[]).filter(isObj).filter((o) => POLICY_METHODS.includes(String(o.method))).map((o) => ({
    method: o.method as PolicyOption['method'], allowed: o.allowed === true, expected_latency_s: num(o.expected_latency_s), confidence: num(o.confidence), reason: typeof o.reason === 'string' ? o.reason : '' }))
  const action = ['choose', 'confirm', 'review', 'navigate'].includes(String(v.action)) ? v.action as PolicyState['action'] : 'choose'
  const selected = POLICY_METHODS.includes(String(v.selected)) ? v.selected as PolicyState['selected'] : 'eeg_fallback'
  return { action, selected, options, constraints: Array.isArray(v.constraints) ? v.constraints.filter((c): c is string => typeof c === 'string') : [], source: v.source === 'calibrated' ? 'calibrated' : 'assumption' }
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
  if (contract === 'confirmed' && isObj(raw) && isObj(raw.methods)) {
    const modesAvailable = Array.from(new Set<InputMode>(['eeg_only', ...(Array.isArray(raw.modes_available) ? raw.modes_available.filter(isMode) : [])]))
    const mode: InputMode = isMode(raw.mode) && (raw.mode === 'auto' || modesAvailable.includes(raw.mode)) ? raw.mode : 'eeg_only'
    const req = (raw as { requested?: unknown }).requested
    const requested: InputMode = isMode(req) ? req : mode
    const imu = sanitizeImu(raw.imu)
    const motionRestart = (s.attempt?.last as { motion_restart?: unknown } | null | undefined)?.motion_restart === true || imu?.eeg_contaminated === true
    const methods: Partial<Record<MethodKey, MethodState>> = {}
    for (const k of ['eeg_ssvep', ...GESTURES] as MethodKey[]) {
      const m = sanitizeMethod((raw.methods as Record<string, unknown>)[k])
      if (m) methods[k] = m
    }
    return {
      provenance: 'backend', mode, requested, motionRestart, modesAvailable, modeReason: typeof raw.mode_reason === 'string' ? raw.mode_reason : '',
      methods: withActive(methods, eegState(s), 'Not reported by the backend'),
      sensorInStream, imu, policy: sanitizePolicy(policy), lastGesture: sanitizeGesture(raw.last_gesture),
      gestureCal: sanitizeCalib(raw.gesture_calibration),
      abstain: abstainFrom(s),
    }
  }
  return {
    provenance: 'eeg_only_default', mode: 'eeg_only', requested: 'eeg_only', motionRestart: false, modesAvailable: ['eeg_only'],
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

/* ---------------- Multimodal benchmark (contract v1.4: GET /api/benchmark/multimodal, SIMULATED) ---------------- */

/** The four strategies of the judge comparison, mapped to the backend's conditions (Agent A v1.4). M2 appears in the table only. */
export const STRATEGIES = [
  { key: 'M1', role: 'Fixed EEG-only', color: 'bg-fixed' },
  { key: 'M3', role: 'Adaptive EEG-only', color: 'bg-neuro' },
  { key: 'M4', role: 'Hybrid EEG + IMU (always on)', color: 'bg-hybrid' },
  { key: 'M5', role: 'Optimised multimodal', color: 'bg-optimized' },
] as const
export const CAPABILITIES = [
  { key: 'no_head_control', label: 'No reliable head movement' },
  { key: 'accurate', label: 'Reliable residual head movement' },
  { key: 'unreliable', label: 'Unreliable head movement' },
  { key: 'high_motion', label: 'High incidental head motion' },
] as const
/** Agent A (v1.4): the M1/M2 idle false-send numbers are under investigation; do not present them yet. */
export const WITHHELD_IDLE = new Set(['M1', 'M2'])

export interface Stat { mean: number; ci95?: [number, number] }
export interface MMCond {
  label: string; replicates?: number; cmpm: Stat
  mean_task_s?: Stat; error_rate?: Stat; accidental_activations_per_hour?: Stat; corrections_per_message?: Stat
  idle_false_sent_per_hour?: Stat; calibration_overhead_s?: Stat; cmpm_30min_incl_calibration?: Stat
}
export interface MMCell { motion_profile: string; snr_level: string; snr_uv?: number; replicates?: number; conditions: Record<string, MMCond> }
export type Verdict = 'better' | 'worse' | 'no significant difference'
export interface MMVerdict {
  motion_profile: string; snr_level: string
  always_on_hybrid_vs_adaptive_eeg: Verdict; always_on_hybrid_ratio: number
  optimised_vs_adaptive_eeg: Verdict; optimised_ratio: number
  best_fixed_multimodal_vs_best_fixed_eeg?: Verdict; best_fixed_ratio?: number
}
export interface MMBench {
  origin: string; real_eeg: boolean; real_imu: boolean; generated_at?: string; disclaimer?: string; limitations: string[]
  conditions: { key: string; label: string }[]; cells: MMCell[]; verdicts: MMVerdict[]
}

const isStat = (v: unknown): v is Stat => isObj(v) && typeof v.mean === 'number'

/** Reads the separate multimodal report; null unless the contract is confirmed and the report is published. */
export function readMultimodalBench(raw: unknown, contract: 'proposed' | 'confirmed' = MULTIMODAL_CONTRACT): MMBench | null {
  if (contract !== 'confirmed' || !isObj(raw) || raw.available !== true || !isObj(raw.summary)) return null
  const sum = raw.summary as Record<string, unknown>
  const cells = (Array.isArray(sum.cells) ? sum.cells : []).filter(isObj).flatMap((c): MMCell[] => {
    if (typeof c.motion_profile !== 'string' || typeof c.snr_level !== 'string' || !isObj(c.conditions)) return []
    const conditions: Record<string, MMCond> = {}
    for (const [k, v] of Object.entries(c.conditions)) if (isObj(v) && isStat(v.cmpm)) conditions[k] = v as unknown as MMCond
    return [{ motion_profile: c.motion_profile, snr_level: c.snr_level, snr_uv: num(c.snr_uv) ?? undefined, replicates: num(c.replicates) ?? undefined, conditions }]
  })
  if (!cells.length) return null
  const VERDICTS = ['better', 'worse', 'no significant difference']
  const verdicts = (Array.isArray(sum.hybrid_verdicts) ? sum.hybrid_verdicts : []).filter(isObj)
    .filter((v) => VERDICTS.includes(String(v.always_on_hybrid_vs_adaptive_eeg)) && VERDICTS.includes(String(v.optimised_vs_adaptive_eeg))) as unknown as MMVerdict[]
  return {
    origin: typeof raw.data_origin === 'string' ? raw.data_origin : 'simulated', real_eeg: raw.real_eeg === true, real_imu: raw.real_imu === true,
    generated_at: typeof raw.generated_at === 'string' ? raw.generated_at : undefined, disclaimer: typeof raw.disclaimer === 'string' ? raw.disclaimer : undefined,
    limitations: Array.isArray(raw.limitations) ? raw.limitations.filter((x): x is string => typeof x === 'string') : [],
    conditions: (Array.isArray(raw.conditions) ? raw.conditions : []).filter(isObj).map((c) => ({ key: String(c.key), label: String(c.label ?? c.key) })),
    cells, verdicts,
  }
}

export function cellFor(mm: MMBench, profile: string, snr: string): MMCell | undefined {
  return mm.cells.find((c) => c.motion_profile === profile && c.snr_level === snr)
}
export function verdictFor(mm: MMBench, profile: string, snr: string): MMVerdict | undefined {
  return mm.verdicts.find((v) => v.motion_profile === profile && v.snr_level === snr)
}
/** Counts of the always-on-hybrid verdict across all cells (CI-based, from the report). */
export function hybridVerdictCounts(mm: MMBench | null): Record<Verdict, number> & { total: number } {
  const out = { better: 0, worse: 0, 'no significant difference': 0, total: 0 }
  for (const v of mm?.verdicts ?? []) { out[v.always_on_hybrid_vs_adaptive_eeg]++; out.total++ }
  return out
}
