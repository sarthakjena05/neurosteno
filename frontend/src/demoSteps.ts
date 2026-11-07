import { command, post } from './api'
import type { State } from './types'

/**
 * Five-minute judge demo, step by step (mirrors DEMO_SCRIPT.md).
 *
 * Every action sends an ordinary contract command, the same ones the other tabs send. Nothing
 * here bypasses confirmation: the virtual patient still has to look at YES/SEND, and the
 * "done" ticks are read from the live snapshot, never assumed.
 */

export interface DemoAction { label: string; run: () => Promise<unknown>; tone?: string }
export interface DemoStep {
  id: string; title: string; budget_s: number; say: string[]
  actions: DemoAction[]; done?: (s: State) => boolean; goTab?: string
  /** Show an "Open Fixed menu vs NeuroSteno" button (the judge comparison view). */
  openCompare?: boolean
}

const sim = (action: string, params: Record<string, unknown> = {}) => () => command(action, params)

export const DEMO_STEPS: DemoStep[] = [
  {
    id: 'novelty', title: 'The idea in 30 seconds', budget_s: 30, openCompare: true,
    say: ['Open “Fixed menu vs NeuroSteno”. Start with the three tiles at the top: (1) fewer brain selections, EXACT; (2) speed vs safety, not a free win, BENCHMARK · SIMULATED; (3) input fits the patient, EEG-only today. Then read the two big numbers: same message, same patient (default: Jordan, “Turn me left”, 2 targets), traditional fixed menu vs NeuroSteno brain selections (EXACT navigator count). Every step is still confirmed.', 'Click patient A: for a new patient the count is unchanged. NeuroSteno does not guess without evidence. Every number is tagged EXACT, ESTIMATE or MEASURED (with its data origin).'],
    actions: [],
  },
  {
    id: 'honesty', title: 'Honesty first', budget_s: 20,
    say: ['Point at the striped purple frame and the SIMULATED EEG badge.', 'REAL, SIMULATED and REPLAY always look different. REAL mode refuses to communicate until it has been calibrated on real data.'],
    actions: [{ label: 'Switch to Simulated', run: () => post('/api/mode', { mode: 'simulated' }) }],
    done: (s) => s.mode.kind === 'simulated',
  },
  {
    id: 'home', title: 'Home screen, 2 or 5 targets', budget_s: 15,
    say: ['“What do you need?” shows five large categories.', 'With 2 targets, the same five cards stay visible inside two semantic groups. All 1,474 phrases are reachable with 2, 3 or 5 targets (tested).'],
    actions: [
      { label: '2 choices', run: sim('set_choices', { n: 2 }) },
      { label: '5 choices', run: sim('set_choices', { n: 5 }) },
      { label: 'Home', run: sim('home') },
    ],
    done: (s) => s.screen.at_home,
  },
  {
    id: 'select', title: 'Selection with correction', budget_s: 50,
    say: ['The virtual patient wants “Left leg: reposition”. FBCCA collects evidence, the choice is highlighted on the same screen, and YES confirms it.', 'Arm a wrong target: the wrong card lights up, the patient looks at NO, and the screen does not change.'],
    actions: [
      { label: 'Autopilot: left leg', run: sim('sim_autopilot', { node_id: 'pain/legs-feet/left-leg/reposition/skip' }) },
      { label: 'Next selection: wrong target', run: sim('sim_inject_mistake'), tone: 'bg-amber-700' },
    ],
    done: (s) => s.phase === 'MESSAGE_REVIEW' || s.sent.length > 0,
    goTab: 'Signal',
  },
  {
    id: 'confirm', title: 'Final confirmation', budget_s: 20,
    say: ['The full sentence appears and is only sent after SEND. Missing input never counts as approval.', 'Show “Messages confirmed by the patient” in the Session tab, tagged with its data origin.'],
    actions: [],
    done: (s) => s.sent.length > 0,
    goTab: 'Session',
  },
  {
    id: 'context', title: 'Context: NeuroRoute', budget_s: 40,
    say: ['Jordan (mobility): ★ suggestions and “NeuroRoute:” reasons appear on cards; the strip shows expected selections vs. the static menu.', 'Compute A/B/C: “Turn me left” goes 11 → 5 for Jordan but is unchanged for a new patient. Sam: suction-now 8 → 6 through the always-on urgent route.'],
    actions: [
      { label: 'Profile: Jordan', run: sim('set_profile', { id: 'mobility' }) },
      { label: 'Profile: Sam', run: sim('set_profile', { id: 'respiratory' }) },
      { label: 'Static baseline', run: sim('set_routing', { mode: 'static' }) },
      { label: 'Risk-aware', run: sim('set_routing', { mode: 'adaptive' }) },
    ],
    done: (s) => s.profile.id !== 'new-patient',
    goTab: 'Routing',
  },
  {
    id: 'failure', title: 'Failure handling', budget_s: 35,
    say: ['Bluetooth dropout means communication is paused. This is not an alarm and nothing is selected.', 'Restore it and the system resumes. Mark Oz + PO7 bad and it pauses again, showing the reason.'],
    actions: [
      { label: 'Bluetooth dropout', run: sim('sim_disconnect', { on: true }), tone: 'bg-red-700' },
      { label: 'Restore Bluetooth', run: sim('sim_disconnect', { on: false }) },
      { label: 'Oz + PO7 bad', run: () => command('sim_bad_channel', { label: 'Oz', on: true }).then(() => command('sim_bad_channel', { label: 'PO7', on: true })), tone: 'bg-red-700' },
      { label: 'Electrodes OK', run: () => command('sim_bad_channel', { label: 'Oz', on: false }).then(() => command('sim_bad_channel', { label: 'PO7', on: false })) },
    ],
    done: (s) => s.phase === 'COMM_UNAVAILABLE',
  },
  {
    id: 'safety', title: 'Safety', budget_s: 20,
    say: ['HELP NOW is clearly labelled as a demo, not a certified alarm.', 'Nonresponse never triggers an emergency, and missing input is never approval.'],
    actions: [
      { label: 'HELP NOW (demo)', run: sim('emergency', { source: 'demo_button' }), tone: 'bg-red-700' },
      { label: 'Acknowledge', run: sim('ack_emergency') },
    ],
    done: (s) => !!s.emergency,
  },
  {
    id: 'hardware', title: 'Real hardware', budget_s: 20,
    say: ['UN-2024.09.36 streamed 2500/2500 samples at 250 Hz over UnicornPy with 0 drops.', 'Signal tab: the electrode map (contact score is a signal heuristic, not impedance) and the eyes open/closed alpha check confirm brain activity before any decoding claim. The bring-up recording failed the plausibility check (all 8 channels identical: headset not on the scalp), so we make no claim about real classification yet.'],
    actions: [],
    goTab: 'Signal',
  },
  {
    id: 'inputs', title: 'Every patient is different', budget_s: 20, goTab: 'Inputs',
    say: ['“NeuroSteno doesn’t assume every patient has the same abilities. It calibrates available brain and movement signals, then selects the fastest reliable communication method for that patient.”', 'EEG-only example (works today, simulated): the Inputs tab shows the live backend state: EEG-only, movement never assumed. Gesture input is held for the demo (backend fix pending). Hybrid design: Inputs tab → UI preview (MOCK) “reliable residual head movement”: a nod can answer YES during navigation; the final SEND is always a separate EEG confirmation.', 'Simulated benchmark, per patient: always-on hybrid beats adaptive EEG-only in 1 of 12 cells (reliable head control, weak EEG), is worse in 6 (every no-head-control cell), no difference in 5. EEG-only stays the default. Nothing here is validated on real patients.'],
    actions: [],
  },
]

export const DEMO_TOTAL_S = 300

/** Restore a clean starting point between demo runs (simulation only). */
export async function resetDemo(): Promise<void> {
  const steps: [string, Record<string, unknown>][] = [
    ['sim_autopilot', { node_id: null }], ['sim_disconnect', { on: false }],
    ['sim_bad_channel', { label: 'Oz', on: false }], ['sim_bad_channel', { label: 'PO7', on: false }],
    ['ack_emergency', {}], ['set_profile', { id: 'new-patient' }], ['set_routing', { mode: 'adaptive' }],
    ['set_policy', { policy: 'explicit' }], ['set_choices', { n: 5 }], ['home', {}],
  ]
  for (const [a, p] of steps) await command(a, p).catch(() => { /* some are no-ops in this state */ })
}

