import type { Phase, State } from './types'

/**
 * Defensive normalisation of the server snapshot so a missing or older backend field can never
 * crash the patient screen. Defaults are EMPTY or SAFE values, never invented data:
 *  - an unknown / missing mode becomes "none" (never "real"),
 *  - missing collections become empty, missing objects become null,
 *  - the names of missing fields are kept in `_missing` for the operator preflight.
 */

const PHASES: Phase[] = ['OFF', 'IDLE', 'EVIDENCE_COLLECTION', 'PREDICTION_HIGHLIGHTED', 'ACCEPTED', 'REJECTED', 'ABSTAINED', 'MESSAGE_REVIEW', 'MESSAGE_SENT', 'COMM_UNAVAILABLE', 'CALIBRATING']
const KINDS = ['real', 'simulated', 'replay', 'none'] as const

type Raw = Record<string, unknown>
const isObj = (v: unknown): v is Raw => !!v && typeof v === 'object' && !Array.isArray(v)
const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : [])

export type NormalizedState = State & { _missing: string[] }

export function normalizeState(input: unknown): NormalizedState {
  const raw: Raw = isObj(input) ? input : {}
  const missing: string[] = []
  const need = <T,>(key: string, ok: boolean, fallback: T, value: unknown): T => {
    if (!ok) { missing.push(key); return fallback }
    return value as T
  }

  // mode: never default to real
  const m = raw.mode
  const kindOk = isObj(m) && (KINDS as readonly string[]).includes(String(m.kind))
  const mode: State['mode'] = kindOk
    ? { label: String(m.label ?? String(m.kind).toUpperCase()), origin: String(m.origin ?? m.kind), is_real_eeg: m.kind === 'real' && m.is_real_eeg !== false,
        replay_of: (m.replay_of as string | null) ?? null, ...(m as object), kind: m.kind as State['mode']['kind'] }
    : { kind: 'none', label: 'UNKNOWN EEG SOURCE', origin: 'none', is_real_eeg: false, replay_of: null }
  if (!kindOk) missing.push('mode')

  const phase = need<Phase>('phase', PHASES.includes(raw.phase as Phase), 'OFF', raw.phase)

  const sc = isObj(raw.screen) ? raw.screen : null
  if (!sc) missing.push('screen')
  const slots = arr<State['screen']['slots'][number]>(sc?.slots).map((sl, i) => ({
    ...sl, index: typeof sl?.index === 'number' ? sl.index : i, items: arr<State['screen']['slots'][number]['items'][number]>(sl?.items), suggest_reasons: arr<string>(sl?.suggest_reasons),
    is_back: !!sl?.is_back, kind: sl?.kind === 'group' ? 'group' as const : 'option' as const, label: String(sl?.label ?? ''), key: String(sl?.key ?? i),
  }))
  const screen: State['screen'] = {
    prompt: String(sc?.prompt ?? ''), location: arr<string>(sc?.location), at_home: sc?.at_home !== false,
    crumbs: arr(sc?.crumbs), slots, n_choices: typeof sc?.n_choices === 'number' ? sc.n_choices : slots.length || 5,
    can_undo: !!sc?.can_undo, home_badges: isObj(sc?.home_badges) ? (sc!.home_badges as State['screen']['home_badges']) : {},
  }

  const cfgRaw = isObj(raw.config) ? raw.config : (missing.push('config'), {})
  const config = { n_choices: screen.n_choices, policy: 'explicit', effective_policy: 'explicit', routing_mode: 'adaptive', auto_adapt: false, ...cfgRaw } as State['config']

  const decRaw = isObj(raw.decoder) ? raw.decoder : (missing.push('decoder'), {})
  const decoder = { allowed_choices: arr<number>(decRaw.allowed_choices), stopping: isObj(decRaw.stopping) ? decRaw.stopping : {}, calibration: isObj(decRaw.calibration) ? decRaw.calibration : {} } as State['decoder']

  const prof = isObj(raw.profile) ? raw.profile : (missing.push('profile'), {})
  const profile = { id: String(prof.id ?? 'unknown'), display_name: String(prof.display_name ?? 'Unknown profile'), summary: String(prof.summary ?? ''),
    synthetic: prof.synthetic !== false, notes: arr<string>(prof.notes), session_tags: arr<string>(prof.session_tags), suggestions: arr(prof.suggestions) } as State['profile']

  const ad = isObj(raw.adaptation) ? raw.adaptation : (missing.push('adaptation'), {})
  const met = isObj(raw.metrics) ? raw.metrics : (missing.push('metrics'), {})
  const bl = isObj(raw.blink) ? raw.blink : null
  const voc = isObj(raw.vocabulary) ? raw.vocabulary : null
  const comm = isObj(raw.comm) ? raw.comm : (missing.push('comm'), { available: true, reason: '', needs_caregiver: false })

  const objOrNull = <K extends keyof State>(k: K): State[K] => (isObj(raw[k]) ? raw[k] : null) as State[K]

  return {
    ...(raw as object),
    version: typeof raw.version === 'number' ? raw.version : 0,
    mode, phase, screen, config, decoder, profile,
    phase_elapsed: typeof raw.phase_elapsed === 'number' ? raw.phase_elapsed : 0,
    source: objOrNull('source'),
    attempt: isObj(raw.attempt) ? { ...(raw.attempt as object), targets: arr((raw.attempt as Raw).targets) } as State['attempt'] : null,
    highlight: objOrNull('highlight'), feedback: objOrNull('feedback'), review: objOrNull('review'),
    sent: arr(raw.sent), comm: comm as State['comm'],
    quality: (isObj(raw.quality) ? raw.quality : {}) as State['quality'],
    routing: objOrNull('routing'), routing_pending: !!raw.routing_pending, task: objOrNull('task'), intended: objOrNull('intended'),
    metrics: { recent: arr(met.recent), summary: arr(met.summary) },
    adaptation: { auto: !!ad.auto, recent: arr<string>(ad.recent), last_change: (isObj(ad.last_change) ? ad.last_change : null) as State['adaptation']['last_change'] },
    sim: objOrNull('sim'), calibration: objOrNull('calibration'),
    blink: { config: { enabled: false, calibrated: false, threshold_uv: 0, ...(isObj(bl?.config) ? bl!.config : {}) }, events: arr(bl?.events) } as State['blink'],
    emergency: objOrNull('emergency'), recording: objOrNull('recording'), events: arr(raw.events),
    vocabulary: (voc ?? { phrases: 0, unique_sentences: 0, nodes: 0, max_depth: 0, per_category: {} }) as State['vocabulary'],
    _missing: missing,
  }
}
