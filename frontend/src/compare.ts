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

export type Provenance = 'EXACT' | 'ESTIMATE' | 'MEASURED' | 'BENCHMARK' | 'DEMO' | 'NONE'
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

/**
 * Headless benchmark (contract v1.3 B3: backend/reports/benchmark_results.json via /api/benchmark).
 * Shapes mirror the published file; every field is optional-safe because older reports may lack them.
 */
export interface BenchCondition {
  key: string; label: string; routing: string; n: number | null; decoder: string
  step_confirm: boolean; live_adapt: boolean; confirm_gate: number | null
}
export interface BenchRow {
  condition: string; scenario: string; profile: string; message: string; node_id?: string
  snr_level?: string; snr_uv?: number; n_start: number; n_start_counts?: Record<string, number>; runs: number
  mean_selections: number; mean_completion_s: number; sd_completion_s?: number
  correct: number; wrong?: number; not_completed?: number; mean_rejected?: number; mean_abstentions?: number
  correct_msgs_per_min: number; final_sent_without_confirm?: number; origin?: string
}
export interface BenchPooled {
  condition: string; sessions: number; cmpm_mean: number; cmpm_ci95?: [number, number]
  error_rate: number; false_confirmations_task: number; messages_sent: number; timeouts?: number
  idle_false_sent_per_hour: number; idle_false_selections_per_min?: number
  urgent_mean_s?: number; urgent_completed?: string
}
export interface Bench {
  available: boolean; kind?: string; data_origin?: string; real_eeg?: boolean; generated_at?: string
  disclaimer?: string; limitations?: string[]; command?: string; research_question?: string
  conditions?: BenchCondition[]
  metrics_definitions?: Record<string, string>
  per_message?: { description?: string; snr_levels_uv?: Record<string, number>; final_sent_without_confirm_note?: string; rows: BenchRow[] }
  summary?: { pooled?: BenchPooled[] }
}

export const SNR_ORDER = ['low', 'medium', 'high']

/** All benchmark conditions for one task at one simulated signal level (no n filter: C/D choose n). */
export function benchFor(b: Bench | null, scenario: string, message: string, snr: string): BenchRow[] {
  const rows = b?.available ? b.per_message?.rows : undefined
  if (!Array.isArray(rows)) return []
  return rows.filter((r) => r.scenario === scenario && r.message === message && (r.snr_level ?? 'medium') === snr)
}

/** The like-for-like pair behind the EXACT counts: fixed menu (A{n}) vs context routing (B{n}), same n and decoder. */
export function benchArm(b: Bench | null, scenario: string, message: string, snr: string, arm: Arm, n: number): BenchRow | undefined {
  const key = `${arm === 'static' ? 'A' : 'B'}${n}`
  return benchFor(b, scenario, message, snr).find((r) => r.condition === key)
}

export function conditionLabel(b: Bench | null, key: string): string {
  return b?.conditions?.find((c) => c.key === key)?.label ?? key
}

export function pct(from: number, to: number): number {
  return from > 0 ? Math.round(((to - from) / from) * 100) : 0
}

/** Pooled benchmark rows for the summary: the fastest fixed menu (hindsight) and the full NeuroSteno variants. Data-driven. */
export function summaryRows(bench: Bench | null): { fixed: BenchPooled | null; full: BenchPooled[] } {
  const pooled = bench?.available ? bench.summary?.pooled ?? [] : []
  const conds = bench?.conditions ?? []
  const fixedKeys = new Set(conds.filter((c) => c.routing === 'static').map((c) => c.key))
  const fullKeys = new Set(conds.filter((c) => c.decoder === 'dynamic').map((c) => c.key))
  const fixed = pooled.filter((p) => fixedKeys.has(p.condition)).reduce<BenchPooled | null>((a, b) => (!a || b.cmpm_mean > a.cmpm_mean ? b : a), null)
  return { fixed, full: pooled.filter((p) => fullKeys.has(p.condition)) }
}
