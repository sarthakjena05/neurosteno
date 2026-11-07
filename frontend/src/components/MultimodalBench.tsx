import { useState } from 'react'
import {
  CAPABILITIES, cellFor, type MMBench, type MMCond, MULTIMODAL_CONTRACT, type Stat, STRATEGIES, type Verdict, verdictFor, WITHHELD_IDLE,
} from '../multimodal'

/**
 * Which method suits which patient: the backend's SIMULATED multimodal benchmark (contract v1.4,
 * GET /api/benchmark/multimodal). Small multiples, one per motion/capability profile, at a chosen
 * simulated EEG signal level. The answer per patient comes from the report's CI-based verdicts,
 * never from the highest point estimate.
 */

const SNRS = ['low', 'medium', 'high'] as const
const VERDICT_STYLE: Record<Verdict, { cls: string; mark: string }> = {
  better: { cls: 'text-green-300', mark: '▲' },
  worse: { cls: 'text-red-300', mark: '▼' },
  'no significant difference': { cls: 'text-muted', mark: '=' },
}
const f2 = (v: number | undefined) => (v == null ? '-' : v.toFixed(2))
const ci = (s?: Stat) => (s?.ci95 ? ` [${f2(s.ci95[0])}, ${f2(s.ci95[1])}]` : '')

function Bar({ role, color, c, max }: { role: string; color: string; c?: MMCond; max: number }) {
  if (!c) {
    return (
      <div className="flex flex-col gap-0.5 text-xs" data-testid="mm-bar" data-measured="false">
        <span className="flex justify-between"><span>{role}</span><span className="rounded border border-line px-1 text-[10px] text-muted">NOT MEASURED</span></span>
        <div className="h-3 rounded bg-panel2" />
      </div>
    )
  }
  const w = (v: number) => `${Math.max(1, Math.min(100, (v / max) * 100))}%`
  const m = c.cmpm
  return (
    <div className="flex flex-col gap-0.5 text-xs" data-testid="mm-bar" data-measured="true" title={`${c.label}: ${f2(m.mean)} correct msgs/min${ci(m)}`}>
      <span className="flex justify-between gap-2"><span>{role}</span><span className="tabular-nums font-semibold">{f2(m.mean)}</span></span>
      <div className="relative h-3 rounded bg-panel2">
        <div className={`h-3 rounded-r ${color}`} style={{ width: w(m.mean) }} />
        {m.ci95 && <div aria-hidden className="absolute top-1/2 h-0.5 -translate-y-1/2 bg-text/70" style={{ left: w(m.ci95[0]), width: `calc(${w(m.ci95[1])} - ${w(m.ci95[0])})` }} />}
      </div>
    </div>
  )
}

function VerdictLine({ label, v, ratio }: { label: string; v: Verdict; ratio: number }) {
  const st = VERDICT_STYLE[v]
  return (
    <p className={`text-xs ${st.cls}`} data-testid="mm-verdict">
      <span aria-hidden>{st.mark} </span>{label}: <b>{v}</b> (×{f2(ratio)})
    </p>
  )
}

export default function MultimodalBench({ mm }: { mm: MMBench | null }) {
  const [snr, setSnr] = useState<string>('medium')
  const cells = mm ? CAPABILITIES.map((p) => cellFor(mm, p.key, snr)) : []
  const max = Math.max(0.0001, ...cells.flatMap((c) => STRATEGIES.map((s) => c?.conditions[s.key]?.cmpm.ci95?.[1] ?? c?.conditions[s.key]?.cmpm.mean ?? 0)))
  const label = (k: string) => mm?.conditions.find((c) => c.key === k)?.label ?? k
  return (
    <section className="flex flex-col gap-3 rounded-2xl border border-line bg-panel p-4" aria-labelledby="mm-bench" data-testid="mm-bench">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="mm-bench" className="text-lg font-black">Which method suits which patient?</h3>
        <span className={`rounded px-1.5 py-0.5 text-[11px] font-bold ${mm ? 'bg-violet-200 text-black' : 'border border-line text-muted'}`}>
          {mm ? `BENCHMARK · ${mm.real_eeg || mm.real_imu ? 'INCLUDES REAL DATA' : 'SIMULATED EEG + SIMULATED HEAD MOTION'}` : 'NOT MEASURED'}
        </span>
      </div>
      <p className="text-sm text-muted">
        NeuroSteno doesn’t assume every patient has the same abilities. Bars: correctly confirmed messages per minute (line = 95% CI).
        The verdicts under each panel come from the report’s paired 95% confidence intervals.
      </p>
      {!mm && (
        <p className="rounded-lg bg-panel2 p-2 text-xs" data-testid="mm-pending">
          The multimodal benchmark is not available ({MULTIMODAL_CONTRACT === 'confirmed' ? 'not published by the backend' : 'contract not confirmed'}). No numbers are shown until it exists.
        </p>
      )}
      {mm && (
        <>
          <p className="rounded-lg bg-amber-950 p-2 text-xs text-amber-100" data-testid="mm-disclaimer">{mm.disclaimer ?? 'SIMULATED.'} {!mm.real_imu && <b>No real-patient head-motion data.</b>}</p>
          <div role="radiogroup" aria-label="Simulated EEG signal level" className="flex flex-wrap items-center gap-1">
            <span className="mr-1 text-xs font-bold uppercase tracking-wider text-muted">Simulated EEG signal</span>
            {SNRS.map((k) => {
              const uv = mm.cells.find((c) => c.snr_level === k)?.snr_uv
              return <button key={k} type="button" role="radio" aria-checked={snr === k} onClick={() => setSnr(k)}
                className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${snr === k ? 'bg-sky-600' : 'bg-panel2 hover:bg-line'}`}>{k}{uv != null ? ` (${uv} µV)` : ''}</button>
            })}
          </div>
        </>
      )}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {CAPABILITIES.map((p, i) => {
          const c = cells[i]
          const v = mm ? verdictFor(mm, p.key, snr) : undefined
          return (
            <div key={p.key} className="flex flex-col gap-1.5 rounded-xl bg-ink p-3" data-testid={`mm-${p.key}`}>
              <h4 className="text-sm font-black">{p.label}</h4>
              {STRATEGIES.map((st) => <Bar key={st.key} role={st.role} color={st.color} c={c?.conditions[st.key]} max={max} />)}
              {v && (
                <div className="mt-1 border-t border-line pt-1">
                  <VerdictLine label="Always-on hybrid vs adaptive EEG-only" v={v.always_on_hybrid_vs_adaptive_eeg} ratio={v.always_on_hybrid_ratio} />
                  <VerdictLine label="Optimised vs adaptive EEG-only" v={v.optimised_vs_adaptive_eeg} ratio={v.optimised_ratio} />
                </div>
              )}
            </div>
          )
        })}
      </div>
      {mm && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs tabular-nums" data-testid="mm-table">
            <caption className="text-left text-muted">Speed and safety together, {snr} simulated signal. “Sent while idle” for M1/M2 is withheld: the backend team is investigating those numbers.</caption>
            <thead><tr className="text-left"><th>patient</th><th>condition</th><th>correct msgs/min [95% CI]</th><th>time / message</th><th>corrections / message</th><th>false activations / h</th><th>sent while idle / h</th><th>wrong-message rate</th></tr></thead>
            <tbody>{CAPABILITIES.flatMap((p, i) => Object.entries(cells[i]?.conditions ?? {}).map(([k, c]) => (
              <tr key={`${p.key}-${k}`} className="border-t border-line">
                <td>{p.label}</td><td>{label(k)}</td>
                <td>{f2(c.cmpm.mean)}{ci(c.cmpm)}</td>
                <td>{c.mean_task_s ? `${Math.round(c.mean_task_s.mean)} s` : '-'}</td>
                <td>{f2(c.corrections_per_message?.mean)}</td>
                <td>{f2(c.accidental_activations_per_hour?.mean)}</td>
                <td data-testid={WITHHELD_IDLE.has(k) ? 'idle-withheld' : undefined}>{WITHHELD_IDLE.has(k) ? 'under investigation' : f2(c.idle_false_sent_per_hour?.mean)}</td>
                <td>{c.error_rate ? `${(c.error_rate.mean * 100).toFixed(1)}%` : '-'}</td>
              </tr>
            )))}</tbody>
          </table>
        </div>
      )}
      {mm && mm.limitations.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer font-bold">Limitations ({mm.limitations.length})</summary>
          <ul className="mt-1 list-disc pl-4">{mm.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
          {mm.generated_at && <p className="mt-1 text-muted">Generated {mm.generated_at}</p>}
        </details>
      )}
    </section>
  )
}
