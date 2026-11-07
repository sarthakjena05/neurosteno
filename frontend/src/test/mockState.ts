import type { Slot, State } from '../types'

const CATS: [string, string, string][] = [
  ['pain', 'Pain', 'Medical'], ['breathing', 'Breathing assistance', 'Medical'],
  ['food-water', 'Food and water', 'Daily needs'], ['comfort', 'Comfort', 'Daily needs'],
  ['call-caregiver', 'Call caregiver', 'People'],
]

const item = (key: string, label: string, group: string) => ({ key, label, kind: 'node', urgent: false, group, icon: key, suggested: false, urgent_route: false })

export function homeSlots(n: number): Slot[] {
  if (n === 5) {
    return CATS.map(([k, l, g], i) => ({ index: i, key: k, label: l, kind: 'option', freq: [6.67, 7.5, 8.57, 10, 12][i], items: [item(k, l, g)], is_back: false, suggest_reasons: [] }))
  }
  return [
    { index: 0, key: 'group:pain|breathing', label: 'Medical', kind: 'group', freq: 7.5, items: CATS.slice(0, 2).map(([k, l, g]) => item(k, l, g)), is_back: false, suggest_reasons: [] },
    { index: 1, key: 'group:food-water|comfort|call-caregiver', label: 'Daily needs · People', kind: 'group', freq: 12, items: CATS.slice(2).map(([k, l, g]) => item(k, l, g)), is_back: false, suggest_reasons: [] },
  ]
}

export function mockState(over: Partial<State> = {}, n = 5): State {
  return {
    version: 1,
    mode: { kind: 'simulated', label: 'SIMULATED EEG', origin: 'simulated', is_real_eeg: false, replay_of: null },
    source: null,
    phase: 'IDLE',
    phase_elapsed: 0,
    screen: { prompt: 'What do you need?', location: [], at_home: true, crumbs: [], slots: homeSlots(n), n_choices: n, can_undo: false, home_badges: {} },
    attempt: null, highlight: null, feedback: null, review: null, sent: [],
    comm: { available: true, reason: '', needs_caregiver: false },
    quality: {},
    decoder: { allowed_choices: [2, 3, 5], stopping: {}, calibration: {} },
    config: { n_choices: n, policy: 'explicit', effective_policy: 'explicit', routing_mode: 'adaptive', auto_adapt: false },
    profile: { id: 'new-patient', display_name: 'Alex', summary: '', synthetic: true, notes: [], session_tags: [], suggestions: [] },
    routing: null, routing_pending: false, task: null, intended: null,
    metrics: { recent: [], summary: [] },
    adaptation: { auto: false, recent: [], last_change: null },
    sim: null, calibration: null,
    blink: { config: { enabled: false, calibrated: false, threshold_uv: 100 }, events: [] },
    emergency: null, recording: null, events: [],
    vocabulary: { phrases: 1474, unique_sentences: 1474, nodes: 1907, max_depth: 5, per_category: {} },
    ...over,
  }
}
