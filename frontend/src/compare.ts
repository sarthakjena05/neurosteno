import type { State } from './types'

/**
 * Fixed-menu vs NeuroSteno comparison: data shaping only.
 *
 * Every number carries a provenance tag, and nothing is computed that the backend did not
 * provide or that was not measured in a run:
 *   EXACT      deterministic selection count from the real navigator (/api/routing/compare)
 *   ESTIMATE   the backend routing model (selections x time per selection), with its source
 *   MEASURED   completed runs (live session metrics or the headless benchmark), with origin
 * If a number does not exist, the UI says "not measured yet". It never fills in a value.
 */

export type Provenance = 'EXACT' | 'ESTIMATE' | 'MEASURED' | 'NONE'
export type Arm = 'static' | 'adaptive'

export interface PathStep { label: string; kind?: string }
export interface CompareRow {
  scenario: string; profile: string; n: number; message: string; text: string
  static: number; adaptive: number; delta: number; shortcuts?: string[]
  // contract v1.3 requests (B1/B2), optional until Agent A ships them
  path_static?: PathStep[]; path_adaptive?: PathStep[]
  est_time_static_s?: number; est_time_adaptive_s?: number; time_source?: string
}
export interface CompareData {
  scenarios: { id: string; profile: string; title: string }[]
  messages: Record<string, string>
  rows: CompareRow[]; note?: string
}

export interface Valued { value: number; tag: Provenance; source: string; detail?: string }

export const MESSAGE_LABEL: Record<string, string> = {
  'turn-left': 'Turn me left',
  'left-leg-reposition': 'Left leg: reposition',
  'suction-now': 'Suction now',
  'water-sips': 'Water, sips',
}

export function findRow(d: CompareData | null, scenario: string, message: string, n: number): CompareRow | undefined {
  return d?.rows.find((r) => r.scenario === scenario && r.message === message && r.n === n)
}

export function selections(row: CompareRow, arm: Arm): Valued {
  return { value: arm === 'static' ? row.static : row.adaptive, tag: 'EXACT', source: 'navigator count, no EEG involved',
    detail: 'Accepted brain selections from Home to the phrase (excludes YES/NO confirmations and the final SEND).' }
}

/**
 * Time estimate. Prefers the backend's own per-row estimate (B2). Otherwise uses the backend
 * routing model's time per selection for this target count, scaled by the exact count.
 * Only available when the engine's current plan is for the same target count.
 */
export function estimatedTime(row: CompareRow, arm: Arm, s: State | null): Valued | null {
  const direct = arm === 'static' ? row.est_time_static_s : row.est_time_adaptive_s
  if (typeof direct === 'number') return { value: direct, tag: 'ESTIMATE', source: row.time_source ?? 'routing model' }
  const byN = s?.routing?.recommended_n.by_n[String(row.n)]
  if (!byN || !(byN.selection_time_s > 0)) return null
  return {
    value: Math.round(selections(row, arm).value * byN.selection_time_s),
    tag: 'ESTIMATE',
    source: `routing model: ${byN.selection_time_s} s per selection (${byN.reliability_source})`,
    detail: 'Excludes the final review step.',
  }
}

export interface Measured {
  tag: 'MEASURED'; origin: string; source: string; messages: number
  mean_completion_s: number; correct: number | null; scored: number
  /** correct messages per minute of completion time; null when correctness was not scored */
  correct_per_min: number | null
}

/** Live-session measurements grouped by the backend (origin x source x routing x profile x n). */
export function measuredFromSession(s: State | null, profile: string, n: number, arm: Arm): Measured[] {
  if (!s) return []
  return (s.metrics.summary as Record<string, unknown>[])
    .filter((r) => r.routing_mode === arm && r.profile === profile && Number(r.n) === n)
    .map((r) => {
      const [k, m] = String(r.correct_final ?? 'unknown').split('/').map(Number)
      const scored = Number.isFinite(m) ? m : 0
      const correct = Number.isFinite(k) && scored > 0 ? k : null
      const mean = Number(r.mean_completion_s)
      return {
        tag: 'MEASURED' as const, origin: String(r.origin), source: String(r.source ?? 'unknown'), messages: Number(r.messages),
        mean_completion_s: mean, correct, scored,
        correct_per_min: correct != null && mean > 0 ? Math.round((correct / scored) * (60 / mean) * 100) / 100 : null,
      }
    })
}

/** Headless benchmark rows (B3), when Agent A publishes them. Condition A = fixed menu. */
export interface BenchRow {
  condition: string; scenario: string; profile: string; message: string; n_start: number; snr_uv?: number; runs: number
  mean_selections: number; mean_completion_s: number; correct: number; wrong?: number; not_completed?: number
  correct_msgs_per_min: number; final_sent_without_confirm?: number
}
export interface Bench { available: boolean; origin?: string; generated_at?: string; note?: string; rows?: BenchRow[] }

export const BENCH_CONDITIONS: Record<string, string> = {
  A: 'Fixed menu · fixed targets · confirm every step',
  B: '+ context / risk-aware routing',
  C: '+ signal-adaptive target count',
  D: '+ provisional confirmation (only if calibration allows)',
}

export function benchFor(b: Bench | null, scenario: string, message: string, n: number): BenchRow[] {
  if (!b?.available || !Array.isArray(b.rows)) return []
  return b.rows.filter((r) => r.scenario === scenario && r.message === message && r.n_start === n)
}

export function pct(from: number, to: number): number {
  return from > 0 ? Math.round(((to - from) / from) * 100) : 0
}
