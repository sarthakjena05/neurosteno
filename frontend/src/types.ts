// Mirrors backend/neurosteno/state.py snapshot (server is authoritative).

export type Phase =
  | 'OFF' | 'IDLE' | 'EVIDENCE_COLLECTION' | 'PREDICTION_HIGHLIGHTED' | 'ACCEPTED' | 'REJECTED'
  | 'ABSTAINED' | 'MESSAGE_REVIEW' | 'MESSAGE_SENT' | 'COMM_UNAVAILABLE' | 'CALIBRATING'

export interface SlotItem {
  key: string; label: string; kind: string; urgent: boolean; group: string; icon: string
  suggested: boolean; urgent_route: boolean
}
export interface Slot {
  index: number; key: string; label: string; kind: 'option' | 'group'; freq: number | null
  items: SlotItem[]; is_back: boolean; suggest_reasons: string[]
}
export interface Target { key: string; label: string; freq: number }
export interface DecisionInfo {
  target: number | null; scores: number[]; margin: number; window_s: number; reliable: boolean
  reason: string; threshold: number
}
export interface Attempt {
  kind: 'choose' | 'confirm' | 'review'; targets: Target[]; elapsed_s: number
  max_window_s: number; min_window_s: number; calibrated: boolean; last: DecisionInfo | null
}
export interface ChannelQuality { label: string; status: string; std_uv: number | null; line_ratio: number | null }
export interface RoutingPlan {
  mode: string; n: number; shortcuts: string[]; urgent: string[]
  candidates: { node_id: string; title?: string; score: number; reasons: string[]; included: boolean; why: string; delta_selections?: number; delta_time_s?: number }[]
  expected: Record<string, number>
  reliability: Record<string, { n: number; accuracy: number; abstain: number; decision_s: number; source: string }>
  verification: { policy: string; requested: string; reason: string; final_message: string }
  recommended_n: { n: number; by_n: Record<string, { expected_selections: number; expected_time_s: number; selection_time_s: number; reliability_source: string }>; note: string }
  top_requests: { node_id: string; title: string; p: number }[]
  explanation: string[]
  computed_ms: number
}
export interface Metric {
  profile: string; origin: string; routing_mode: string; n_start: number; selections: number
  rejected_predictions: number; abstentions: number; completion_s: number; final_text: string
  correct: boolean | null; intended: string | null; injected_mistakes: number
}
export interface LogEvent { time: number; kind: string; text: string }

export interface State {
  version: number
  server_time?: number
  /** Contract v1.3. messages_sent_without_final_confirmation is 0 by construction (the engine refuses
   *  such sends); blocked_unconfirmed_send_attempts is the measured counter. */
  safety_invariants?: { messages_sent_without_final_confirmation: number; blocked_unconfirmed_send_attempts?: number }
  mode: { kind: 'real' | 'simulated' | 'replay' | 'none'; label: string; origin: string; is_real_eeg: boolean; replay_of: string | null
    /** Contract v1.2: calibrated thresholds exist for this origin AND the current n_choices. */
    calibrated?: boolean
    /** Contract v1.2: true for real-origin data, live or replayed. */
    calibration_required?: boolean }
  source: null | {
    kind: string; sample_rate: number
    channels: { index: number; name: string; kind: string; unit: string; label: string }[]
    status: { connected: boolean; streaming: boolean; device: string; message: string; error: string | null
      samples_received: number; dropped_samples: number; battery_percent: number | null
      seconds_since_last_sample: number | null; extra: Record<string, unknown> }
  }
  phase: Phase
  phase_elapsed: number
  screen: {
    prompt: string; location: string[]; at_home: boolean
    crumbs: { label: string; provisional: boolean }[]
    slots: Slot[]; n_choices: number; can_undo: boolean
    home_badges: Record<string, { title: string; urgent: boolean }[]>
  }
  attempt: Attempt | null
  highlight: null | { slot_index: number; key: string; label: string; margin: number; window_s: number; provisional: boolean; provisional_remaining_s: number | null }
  feedback: null | { type: string; label: string; reason?: string; provisional?: boolean }
  review: null | { text: string; node_id: string; title: string; urgent: boolean; provisional_steps: number }
  /** `source` (contract v1.2): "replay" means played back, even when data_origin is "real". */
  sent: { text: string; time: number; data_origin: string; source?: string; urgent: boolean; provisional_steps: number }[]
  comm: { available: boolean; reason: string; needs_caregiver: boolean }
  quality: {
    channels?: ChannelQuality[]; common_mode?: number | null; stalled?: boolean; problem?: string | null
    signal_ok?: boolean; good_decoding_channels?: number; required_decoding_channels?: number; decoding_labels?: string[]
    seconds_since_last_sample?: number | null
  }
  decoder: { allowed_choices: number[]; stopping: Record<string, unknown>; calibration: Record<string, Record<string, unknown>> }
  config: Record<string, unknown> & { n_choices: number; policy: string; effective_policy: string; routing_mode: string; auto_adapt: boolean }
  profile: { id: string; display_name: string; summary: string; synthetic: boolean; notes: string[]; session_tags: string[]
    suggestions: { node_id: string; title: string; score: number; reasons: string[]; category: string }[] }
  routing: RoutingPlan | null
  routing_pending: boolean
  task: null | (Record<string, unknown> & { elapsed_s: number; selections: number; rejected_predictions: number })
  intended: null | { node_id: string; text: string }
  metrics: { recent: Metric[]; summary: Record<string, unknown>[] }
  adaptation: { auto: boolean; recent: string[]; last_change: null | { from: number; to: number; reason: string; time?: number } }
  sim: null | { gaze_key: string | null; gaze_hz: number | null; autopilot: null | { node_id: string; text: string }
    pending_mistake: boolean; mistake_rate: number; noise_multiplier: number; bad_channels: string[]
    disconnected: boolean; drop_probability: number; ssvep_uv: number }
  calibration: null | { kind: string; n: number; freqs: number[]; trial: number; trials: number; sub: string
    cue_target: number | null; rest_trial: boolean; elapsed: number; durations: Record<string, number> }
  blink: { config: Record<string, unknown> & { enabled: boolean; calibrated: boolean; threshold_uv: number }
    events: { amplitude_uv: number; accepted: boolean; reason: string; time: number }[] }
  /** Contract v1.1/1.2: signal-derived contact check (NOT impedance). null when no source runs. */
  electrode_check?: ElectrodeCheck | null
  emergency: null | { active: boolean; source: string; source_label: string; validated: boolean; time: number }
  recording: null | { path: string; seconds: number }
  events: LogEvent[]
  vocabulary: { phrases: number; unique_sentences: number; nodes: number; max_depth: number; per_category: Record<string, number> }
}

export type ElectrodeVerdict = 'good' | 'suspect' | 'bad' | 'no_data'
export interface ElectrodeChannel {
  label: string; verdict: ElectrodeVerdict; status?: ElectrodeVerdict
  /** 0..1 heuristic from the signal. NOT impedance (UnicornPy exposes none). */
  contact_score: number | null
  issues: string[]; advice: string[]; hint?: string | null
  metrics?: Record<string, number | null>
}
export interface ElectrodeCheck {
  window_s?: number; note?: string
  ready?: boolean; reference_ok?: boolean; message?: string
  summary?: { counts?: Partial<Record<ElectrodeVerdict, number>>; decoding_usable?: string[]; reference_off?: boolean; headline?: string }
  channels: ElectrodeChannel[]
  spectra?: { freqs: number[]; channels: Record<string, number[]> }
}
export interface AlphaResult {
  verdict: 'eeg_confirmed' | 'weak' | 'not_detected' | 'invalid'; note: string; origin: string
  channels?: { label: string; ratio: number; reactive: boolean; occipital: boolean; artifact?: boolean; peak_hz?: number }[]
  reactive_occipital?: string[]
}
