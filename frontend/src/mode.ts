import type { State } from './types'

/**
 * Presentation helpers for the EEG data origin. These only *describe* the server snapshot;
 * they never decide what the engine does (the server is authoritative).
 */

export type ModeKind = State['mode']['kind']

/** Frame colour around the whole patient screen, so the origin is visible at a glance. */
export const MODE_FRAME: Record<ModeKind, string> = {
  real: 'mode-frame-real',
  simulated: 'mode-frame-sim',
  replay: 'mode-frame-replay',
  none: 'mode-frame-none',
  assisted: 'mode-frame-assisted',
}

export function originLabel(origin: string | null | undefined): string {
  switch (origin) {
    case 'real': return 'REAL EEG'
    case 'simulated': return 'SIMULATED EEG (synthetic)'
    case 'replay': return 'REPLAYED EEG'
    case 'assisted': return 'ASSISTED EEG (real stream + simulated augmentation)'
    case 'none': case null: case undefined: return 'no EEG'
    default: return `${String(origin).toUpperCase()} data`
  }
}

/**
 * Calibration status of the current data origin for the current target count.
 * Prefers an explicit server field (requested from Agent A as `mode.calibrated`); until it
 * exists, falls back to the per-origin calibration record the decoder already publishes.
 * Returns null when there is no source.
 */
export function calibratedForCurrent(s: State): boolean | null {
  if (s.mode.kind === 'none') return null
  const explicit = (s.mode as State['mode'] & { calibrated?: boolean }).calibrated
  if (typeof explicit === 'boolean') return explicit
  const rec = s.decoder.calibration[`ssvep_${s.screen.n_choices}`] as { origin?: string; supported?: boolean } | undefined
  if (rec) return rec.origin === s.mode.origin && rec.supported === true
  if (s.attempt) return s.attempt.calibrated
  return false
}

/**
 * Whether this data must be calibrated on its own origin before it may communicate.
 * Contract v1.2 `mode.calibration_required` (true for real-origin data, live OR replayed);
 * falls back to "origin is real".
 */
export function calibrationRequired(s: State): boolean {
  if (s.mode.kind === 'none') return false
  return typeof s.mode.calibration_required === 'boolean' ? s.mode.calibration_required : s.mode.origin === 'real'
}

/** A sent message that was played back from a recording (data_origin alone is not enough). */
export function isReplayedMessage(m: State['sent'][number] | undefined, s: State): boolean {
  return m?.source != null ? m.source === 'replay' : s.mode.kind === 'replay'
}

/** Short, honest one-liner for the patient-screen header. */
export function modeCaption(s: State): string {
  switch (s.mode.kind) {
    case 'real': return calibratedForCurrent(s) ? 'Live headset · calibrated on real data' : 'Live headset · NOT calibrated · communication is off until calibration'
    case 'simulated': return 'Synthetic signals from a virtual patient. Not a person’s brain activity.'
    case 'assisted': return s.mode.assisted_base === 'real'
      ? 'Live headset EEG with SIMULATED SSVEP augmentation for the chosen target. Every selection here is ASSISTED, not independent EEG decoding.'
      : 'Dry run: simulated EEG plus simulated augmentation (no headset). Every selection here is ASSISTED.'
    case 'replay': return `Playback of a recording (${originLabel(s.mode.replay_of)}). Not live.${calibrationRequired(s) && !calibratedForCurrent(s) ? ' Needs a real-data calibration before it can communicate.' : ''}`
    case 'none': return 'No EEG source connected'
  }
}
