import { bestFor, CAPABILITIES, type MMBench, type MMBenchRow, MULTIMODAL_CONTRACT, STRATEGIES } from '../multimodal'

/**
 * Four strategies x patient capability, from the backend benchmark only (contract v2.0).
 * Small multiples, one per capability profile, so the patient-specific answer is visible:
 * some patients gain from hybrid controls, others should stay EEG-only.
 */

function Bar({ label, color, row, max, best }: { label: string; color: string; row?: MMBenchRow; max: number; best: boolean }) {
  if (!row) {
    return (
      <div className="flex flex-col gap-0.5 text-xs" data-testid="mm-bar" data-measured="false">
        <span className="flex justify-between"><span>{label}</span><span className="rounded border border-line px-1 text-[10px] text-muted">NOT MEASURED</span></span>
        <div className="h-3 rounded bg-panel2" />
      </div>
    )
  }
  const w = (v: number) => `${Math.max(1, Math.min(100, (v / max) * 100))}%`
  return (
    <div className="flex flex-col gap-0.5 text-xs" data-testid="mm-bar" data-measured="true"
      title={`${label}: ${row.correct_msgs_per_min} correct msgs/min${row.ci95 ? ` (95% CI ${row.ci95[0]}–${row.ci95[1]})` : ''}, ${row.runs} runs`}>
      <span className="flex justify-between gap-2">
        <span className={best ? 'font-black' : ''}>{label}{best ? ' · best for this patient' : ''}</span>
        <span className="tabular-nums font-semibold">{row.correct_msgs_per_min}</span>
      </span>
      <div className="relative h-3 rounded bg-panel2">
        <div className={`h-3 rounded-r ${color}`} style={{ width: w(row.correct_msgs_per_min) }} />
        {row.ci95 && <div aria-hidden className="absolute top-1/2 h-0.5 -translate-y-1/2 bg-text/70" style={{ left: w(row.ci95[0]), width: `calc(${w(row.ci95[1])} - ${w(row.ci95[0])})` }} />}
      </div>
    </div>
  )
}

export default function MultimodalBench({ mm }: { mm: MMBench | null }) {
  const rows = mm?.rows ?? []
  const max = Math.max(0.0001, ...rows.map((r) => r.ci95?.[1] ?? r.correct_msgs_per_min))
  return (
    <section className="flex flex-col gap-3 rounded-2xl border border-line bg-panel p-4" aria-labelledby="mm-bench" data-testid="mm-bench">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="mm-bench" className="text-lg font-black">Which method suits which patient?</h3>
        <span className={`rounded px-1.5 py-0.5 text-[11px] font-bold ${mm ? (mm.real_eeg ? 'bg-green-300 text-black' : 'bg-violet-200 text-black') : 'border border-line text-muted'}`}>
          {mm ? (mm.real_eeg ? 'BENCHMARK · REAL' : `BENCHMARK · ${mm.origin.toUpperCase()}`) : 'NOT MEASURED'}
        </span>
      </div>
      <p className="text-sm text-muted">
        NeuroSteno doesn’t assume every patient has the same abilities. It calibrates the brain and movement signals that are available,
        then selects the fastest <b>reliable</b> method. Bars: correctly confirmed messages per minute (line = 95% CI).
      </p>
      {!mm && (
        <p className="rounded-lg bg-panel2 p-2 text-xs" data-testid="mm-pending">
          The multimodal benchmark is not published yet ({MULTIMODAL_CONTRACT === 'confirmed' ? 'no rows in the report' : 'contract v2.0 proposed to the backend, not confirmed'}). No numbers are shown until it exists.
          Hybrid is expected to help only patients whose movement check passes; that is a hypothesis until measured.
        </p>
      )}
      <div className="grid gap-3 md:grid-cols-3">
        {CAPABILITIES.map((c) => {
          const best = bestFor(rows, c.key)
          return (
            <div key={c.key} className="flex flex-col gap-1.5 rounded-xl bg-ink p-3" data-testid={`mm-${c.key}`}>
              <h4 className="text-sm font-black">{c.label}</h4>
              {STRATEGIES.map((st) => {
                const r = rows.find((x) => x.capability_profile === c.key && x.condition === st.key)
                return <Bar key={st.key} label={st.label} color={st.color} row={r} max={max} best={!!best && best === r} />
              })}
            </div>
          )
        })}
      </div>
      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs tabular-nums" data-testid="mm-table">
            <caption className="text-left text-muted">Speed and safety together. {mm?.note ?? ''} {mm?.generated_at ? `Generated ${mm.generated_at}.` : ''}</caption>
            <thead><tr className="text-left"><th>patient</th><th>strategy</th><th>runs</th><th>correct msgs/min</th><th>selection time</th><th>corrections</th><th>false activations / h</th><th>sent while idle / h</th><th>wrong-message rate</th><th>sent w/o final YES</th></tr></thead>
            <tbody>{rows.map((r, i) => (
              <tr key={i} className="border-t border-line">
                <td>{CAPABILITIES.find((c) => c.key === r.capability_profile)?.label ?? r.capability_profile}</td>
                <td>{STRATEGIES.find((s) => s.key === r.condition)?.label ?? r.condition}</td>
                <td>{r.runs}</td><td>{r.correct_msgs_per_min}{r.ci95 ? ` [${r.ci95[0]}, ${r.ci95[1]}]` : ''}</td>
                <td>{r.mean_selection_s != null ? `${r.mean_selection_s} s` : '-'}</td><td>{r.corrections ?? '-'}</td>
                <td>{r.false_activations_per_hour ?? '-'}</td><td>{r.idle_false_sent_per_hour ?? '-'}</td>
                <td>{r.error_rate != null ? `${(r.error_rate * 100).toFixed(1)}%` : '-'}</td><td>{r.final_sent_without_confirm ?? '-'}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </section>
  )
}
