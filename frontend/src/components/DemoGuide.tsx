import { useEffect, useState } from 'react'
import { DEMO_STEPS, DEMO_TOTAL_S, resetDemo, type DemoAction } from '../demoSteps'
import type { State } from '../types'
/** Operator-panel guide for the five-minute judge demo. Step content lives in ../demoSteps.ts. */

function fmt(sec: number): string {
  const m = Math.floor(Math.abs(sec) / 60), s = Math.floor(Math.abs(sec) % 60)
  return `${sec < 0 ? '-' : ''}${m}:${String(s).padStart(2, '0')}`
}

function loadStep(): number {
  try { return Number(localStorage.getItem('ns.demoStep') ?? 0) || 0 } catch { return 0 }
}

export default function DemoGuide({ s, onGoTab }: { s: State; onGoTab: (tab: string) => void }) {
  const [step, setStep] = useState(loadStep)
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => { try { localStorage.setItem('ns.demoStep', String(step)) } catch { /* ignore */ } }, [step])
  useEffect(() => {
    if (startedAt == null) return
    const t = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(t)
  }, [startedAt])

  const elapsed = startedAt == null ? 0 : (now - startedAt) / 1000
  const remaining = DEMO_TOTAL_S - elapsed
  const budgetToHere = DEMO_STEPS.slice(0, step + 1).reduce((a, d) => a + d.budget_s, 0)
  const behind = startedAt != null && elapsed > budgetToHere
  const cur = DEMO_STEPS[Math.min(step, DEMO_STEPS.length - 1)]
  const run = (a: DemoAction) => { setErr(null); a.run().catch((e: Error) => setErr(`${a.label}: ${e.message}`)) }

  return (
    <div className="flex flex-col gap-3" data-testid="demo-guide">
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line p-3">
        <span role="timer" aria-label="Demo time remaining" data-testid="demo-timer"
          className={`font-mono text-2xl font-black ${remaining < 0 ? 'text-red-400' : behind ? 'text-amber-300' : ''}`}>{fmt(remaining)}</span>
        <span className="text-xs text-muted">of 5:00{behind ? ' · behind schedule' : ''}</span>
        <div className="ml-auto flex gap-1">
          {startedAt == null
            ? <button type="button" className="rounded-lg bg-sky-600 px-3 py-1.5 text-sm font-semibold" onClick={() => { setStartedAt(Date.now()); setNow(Date.now()) }}>Start timer</button>
            : <button type="button" className="rounded-lg bg-panel2 px-3 py-1.5 text-sm font-semibold" onClick={() => setStartedAt(null)}>Stop</button>}
          <button type="button" className="rounded-lg bg-panel2 px-3 py-1.5 text-sm font-semibold" onClick={() => { setErr(null); setStep(0); resetDemo().catch(() => {}) }}>Reset demo</button>
        </div>
      </div>
      {err && <p role="alert" className="rounded bg-red-900 p-2 text-sm">{err}</p>}

      <ol className="flex flex-col gap-1" aria-label="Demo steps">
        {DEMO_STEPS.map((d, i) => {
          const done = d.done?.(s) ?? false
          return (
            <li key={d.id}>
              <button type="button" onClick={() => setStep(i)} aria-current={i === step ? 'step' : undefined}
                className={`flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left text-sm ${i === step ? 'bg-sky-900' : 'hover:bg-panel2'}`}>
                <span className="w-5 text-muted">{i + 1}</span>
                <span className="flex-1 font-semibold">{d.title}</span>
                {i === step && done && <span aria-label="live condition met" className="text-green-400">●</span>}
                <span className="text-xs text-muted">{d.budget_s}s</span>
              </button>
            </li>
          )
        })}
      </ol>

      <section className="flex flex-col gap-2 rounded-xl border-2 border-sky-700 p-3" aria-labelledby="demo-step-title" data-testid="demo-step">
        <h3 id="demo-step-title" className="text-base font-black">{step + 1}. {cur.title} <span className="text-xs font-normal text-muted">({cur.budget_s} s)</span></h3>
        <ul className="list-disc pl-5 text-sm">{cur.say.map((t) => <li key={t}>{t}</li>)}</ul>
        {cur.actions.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {cur.actions.map((a) => (
              <button key={a.label} type="button" onClick={() => run(a)}
                className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${a.tone ?? 'bg-panel2 hover:bg-line'}`}>{a.label}</button>
            ))}
          </div>
        )}
        {s.mode.kind !== 'simulated' && cur.id !== 'hardware' && (
          <p className="text-xs text-amber-300">This step uses simulation controls. Switch to Simulated first (step 1).</p>
        )}
        <div className="flex gap-2">
          <button type="button" disabled={step === 0} onClick={() => setStep(step - 1)} className="rounded-lg bg-panel2 px-3 py-1.5 text-sm disabled:opacity-40">← Prev</button>
          {cur.goTab && <button type="button" onClick={() => onGoTab(cur.goTab!)} className="rounded-lg bg-panel2 px-3 py-1.5 text-sm">Open {cur.goTab} tab</button>}
          <button type="button" disabled={step >= DEMO_STEPS.length - 1} onClick={() => setStep(step + 1)} className="ml-auto rounded-lg bg-sky-600 px-3 py-1.5 text-sm font-semibold disabled:opacity-40">Next →</button>
        </div>
      </section>
    </div>
  )
}
