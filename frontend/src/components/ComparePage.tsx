import { useEffect, useState } from 'react'
import { command, get } from '../api'
import {
  benchArm, benchFor, conditionLabel, estimatedTime, findRow, measuredFromSession, MESSAGE_LABEL, pct, selections, SNR_ORDER,
  type Arm, type Bench, type BenchRow, type CompareData, type CompareRow, type Measured, type Provenance,
} from '../compare'
import { originLabel } from '../mode'
import type { State } from '../types'
import { AB_SEED, type ArmResult, conditionsMatch, type LiveAB as LiveABApi, startBlocker } from '../useLiveAB'
import { ModeBadge } from './PatientView'

/**
 * Judge view: the same message under a traditional fixed menu and under NeuroSteno.
 * Every number is tagged EXACT / ESTIMATE / BENCHMARK / MEASURED / DEMO (with data origin) or
 * "not measured"; nothing is filled in.
 */

const ARM: Record<Arm, { title: string; sub: string; dot: string; bar: string }> = {
  static: { title: 'Traditional fixed menu', sub: 'Same tree for every patient. No context, no shortcuts.', dot: 'bg-fixed', bar: 'border-fixed' },
  adaptive: { title: 'NeuroSteno', sub: 'Context-aware · risk-aware · signal-adaptive', dot: 'bg-neuro', bar: 'border-neuro' },
}

const TAG_STYLE: Record<Provenance, string> = {
  EXACT: 'bg-zinc-200 text-black',
  ESTIMATE: 'border border-dashed border-zinc-300 text-zinc-100',
  BENCHMARK: 'bg-violet-200 text-black',
  MEASURED: 'bg-sky-200 text-black',
  DEMO: 'border border-sky-300 text-sky-100',
  NONE: 'border border-line text-muted',
}
const TAG_HELP: Record<Provenance, string> = {
  EXACT: 'Deterministic count from the real navigator (seed profiles). No EEG involved.',
  ESTIMATE: 'Backend routing model: selections × time per selection, from labelled assumptions. Not a measurement.',
  BENCHMARK: 'Headless benchmark: same profile, message, signal level and seed for every condition, many runs. Simulated EEG.',
  MEASURED: 'Completed messages in this session. The data origin is always shown.',
  DEMO: 'One live run per arm in a fixed order. An illustration, not a controlled experiment.',
  NONE: 'Nothing measured. No value is filled in.',
}
const TAG_TEXT: Partial<Record<Provenance, string>> = { NONE: 'NOT MEASURED', BENCHMARK: 'BENCHMARK · SIMULATED', DEMO: 'DEMO RUN · SIMULATED' }

export function Tag({ tag, origin }: { tag: Provenance; origin?: string }) {
  const label = tag === 'MEASURED' && origin ? `MEASURED · ${origin === 'simulated' ? 'SIMULATED' : origin.toUpperCase()}` : TAG_TEXT[tag] ?? tag
  return <span data-testid="prov-tag" title={TAG_HELP[tag]} className={`whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-bold tracking-wide ${TAG_STYLE[tag]}`}>{label}</span>
}

function Dots({ n, arm }: { n: number; arm: Arm }) {
  return (
    <div className="flex flex-wrap gap-1.5" role="img" aria-label={`${n} brain selections, one dot each`} data-testid={`dots-${arm}`}>
      {Array.from({ length: n }, (_, i) => <span key={i} className={`h-4 w-4 rounded-full ${ARM[arm].dot}`} />)}
    </div>
  )
}

/** The exact route (contract v1.3 B1), when the backend provides it. */
function Path({ path, arm }: { path?: { label: string; kind?: string }[]; arm: Arm }) {
  if (!path?.length) return null
  return (
    <ol className="flex flex-wrap items-center gap-1 text-xs" aria-label="Route, one selection per step" data-testid={`path-${arm}`}>
      {path.map((p, i) => (
        <li key={i} className="flex items-center gap-1">
          {i > 0 && <span aria-hidden className="text-muted">›</span>}
          <span className={`rounded px-1.5 py-0.5 ${p.kind === 'shortcut' ? 'bg-neuro font-bold text-white' : p.kind === 'urgent_route' ? 'bg-red-700' : p.kind === 'back' ? 'bg-zinc-700' : 'bg-panel2'}`}>
            {p.kind === 'shortcut' ? '★ ' : p.kind === 'urgent_route' ? '⚠ ' : p.kind === 'back' ? '↩ ' : ''}{p.label}
          </span>
        </li>
      ))}
    </ol>
  )
}

function fmtS(v: number) { return v >= 90 ? `${Math.floor(v / 60)} min ${Math.round(v % 60)} s` : `${Math.round(v)} s` }

function MeasuredLine({ m, scope }: { m: Measured; scope: string }) {
  return (
    <p className="flex flex-wrap items-center gap-2 text-xs">
      <Tag tag="MEASURED" origin={m.origin} />
      {m.source === 'replay' && <span className="text-amber-300">replayed</span>}
      <span className="text-muted">{scope}:</span>
      <span>{m.messages} msg · mean {fmtS(m.mean_completion_s)}</span>
      {m.correct_per_min != null
        ? <span>{m.correct_per_min} correct msgs/min ({m.correct}/{m.scored})</span>
        : <span className="text-muted">correctness not scored</span>}
    </p>
  )
}

function BenchLine({ r, snr }: { r: BenchRow; snr: string }) {
  return (
    <p className="flex flex-wrap items-center gap-2 text-sm" data-testid="bench-line">
      <Tag tag="BENCHMARK" />
      <span><b>{r.correct_msgs_per_min}</b> correct msgs/min</span>
      <span>· {r.correct}/{r.runs} correct{(r.wrong ?? 0) > 0 ? `, ${r.wrong} wrong` : ''}{(r.not_completed ?? 0) > 0 ? `, ${r.not_completed} not completed` : ''}</span>
      <span>· mean {fmtS(r.mean_completion_s)}, {r.mean_selections} selections</span>
      <span className="text-xs text-muted">{snr} signal ({r.snr_uv} µV) · condition {r.condition}</span>
    </p>
  )
}

function ArmCard({ arm, row, s, live, measured, bench, snr, profileName }: {
  arm: Arm; row: CompareRow; s: State; live?: ArmResult; measured: Measured[]; bench?: BenchRow; snr: string; profileName: string
}) {
  const sel = selections(row, arm)
  const t = estimatedTime(row, arm, s)
  const path = arm === 'static' ? row.path_static : row.path_adaptive
  return (
    <section data-testid={`arm-${arm}`} aria-labelledby={`arm-${arm}-title`}
      className={`flex flex-col gap-3 rounded-2xl border border-line border-t-8 bg-panel p-4 ${ARM[arm].bar}`}>
      <div>
        <h3 id={`arm-${arm}-title`} className="text-2xl font-black">{ARM[arm].title}</h3>
        <p className="text-sm text-muted">{ARM[arm].sub}</p>
      </div>
      <div className="flex items-end gap-3">
        <span data-testid={`sel-${arm}`} className="text-7xl font-black leading-none">{sel.value}</span>
        <span className="pb-1 text-lg">brain selections</span>
        <span className="ml-auto pb-1"><Tag tag="EXACT" /></span>
      </div>
      <Dots n={sel.value} arm={arm} />
      <Path path={path} arm={arm} />
      <div className="flex flex-col gap-1.5 border-t border-line pt-2">
        {bench ? <BenchLine r={bench} snr={snr} />
          : <p className="flex items-center gap-2 text-sm text-muted"><Tag tag="NONE" /> no benchmark row for this task / target count / signal level</p>}
        <p className="flex flex-wrap items-center gap-2 text-sm">
          {t ? <><Tag tag="ESTIMATE" /><span>≈ <b>{fmtS(t.value)}</b> to compose</span><span className="text-xs text-muted">{t.source}</span></>
            : <><Tag tag="NONE" /><span className="text-muted">no time estimate for {row.n} targets</span></>}
        </p>
        {live && <LiveLine r={live} exact={sel.value} />}
        {measured.map((m, i) => <MeasuredLine key={i} m={m} scope={`this session, all ${profileName} messages at ${row.n} targets`} />)}
      </div>
    </section>
  )
}

function LiveLine({ r, exact }: { r: ArmResult; exact: number }) {
  if (r.status !== 'done') {
    return <p className="text-sm text-sky-300" data-testid={`live-${r.arm}`}>Live demo run: {r.status === 'running' ? 'virtual patient is composing (confirms every step)…' : r.status}{r.error ? `: ${r.error}` : ''}</p>
  }
  return (
    <p className="flex flex-wrap items-center gap-2 text-sm" data-testid={`live-${r.arm}`}>
      <Tag tag="DEMO" />
      <span>1 run: <b>{r.selections ?? '?'}</b> selections in <b>{r.completion_s != null ? fmtS(r.completion_s) : '?'}</b></span>
      <span>{r.correct === true ? '✓ correct message' : r.correct === false ? '✗ wrong message' : 'unscored'}</span>
      {(r.rejected ?? 0) > 0 && <span className="text-muted">{r.rejected} wrong highlight(s) rejected</span>}
      {r.selections != null && r.selections !== exact && (
        <span className="w-full text-xs text-amber-300" data-testid={`live-mismatch-${r.arm}`}>
          Differs from the EXACT seed-profile count ({exact}): live routing also uses this machine’s saved simulated history{r.abstentions ? ' and abstentions/retries' : ''}.
        </span>
      )}
    </p>
  )
}

function Delta({ row }: { row: CompareRow }) {
  const d = row.adaptive - row.static
  const msg = d < 0
    ? <><b className="text-3xl">{-d} fewer</b> brain selections ({pct(row.static, row.adaptive)}%)</>
    : d === 0
      ? <><b className="text-2xl">Same count.</b> NeuroSteno adds no shortcut without evidence for this patient.</>
      : <><b className="text-2xl">+{d}</b>: the cost of always-visible urgent routes in this category (shown, not hidden).</>
  return (
    <div data-testid="delta" className={`rounded-2xl px-4 py-3 text-lg ${d < 0 ? 'bg-sky-950' : 'bg-panel2'}`} aria-live="polite">
      {msg} <span className="ml-2 align-middle"><Tag tag="EXACT" /></span>
    </div>
  )
}

function Picker<T extends string | number>({ label, value, options, onPick }: { label: string; value: T; options: { v: T; text: string }[]; onPick: (v: T) => void }) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap items-center gap-1">
      <span className="mr-1 text-xs font-bold uppercase tracking-wider text-muted">{label}</span>
      {options.map((o) => (
        <button key={String(o.v)} type="button" role="radio" aria-checked={o.v === value} onClick={() => onPick(o.v)}
          className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${o.v === value ? 'bg-sky-600' : 'bg-panel2 hover:bg-line'}`}>{o.text}</button>
      ))}
    </div>
  )
}

function ScenarioTable({ data, n, sel, onPick }: { data: CompareData; n: number; sel: { scenario: string; message: string }; onPick: (sc: string, m: string) => void }) {
  const msgs = Object.keys(data.messages)
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm tabular-nums" data-testid="scenario-table">
        <caption className="mb-1 text-left text-xs text-muted">Every scenario at {n} targets. Fixed → NeuroSteno brain selections (EXACT). Click a cell to compare it.</caption>
        <thead><tr><th className="text-left">Patient</th>{msgs.map((m) => <th key={m} className="px-2 text-left">{MESSAGE_LABEL[m] ?? m}</th>)}</tr></thead>
        <tbody>
          {data.scenarios.map((sc) => (
            <tr key={sc.id} className="border-t border-line">
              <th scope="row" className="py-1 pr-2 text-left font-semibold">{sc.id} · {sc.title}</th>
              {msgs.map((m) => {
                const r = findRow(data, sc.id, m, n)
                if (!r) return <td key={m} className="px-2 text-muted">-</td>
                const on = sel.scenario === sc.id && sel.message === m
                return (
                  <td key={m} className="px-1">
                    <button type="button" onClick={() => onPick(sc.id, m)} aria-pressed={on}
                      className={`w-full rounded px-2 py-1 text-left ${on ? 'ring-2 ring-sky-400' : 'hover:bg-panel2'}`}>
                      {r.static} → <b className={r.delta < 0 ? 'text-sky-300' : r.delta > 0 ? 'text-amber-300' : ''}>{r.adaptive}</b>
                    </button>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function HBar({ label, value, max, unit, strong, note }: { label: string; value: number; max: number; unit: string; strong?: boolean; note?: string }) {
  const w = max > 0 ? Math.max(2, (value / max) * 100) : 0
  return (
    <div className="flex flex-col gap-0.5 text-sm" title={`${label}: ${value}${unit}${note ? ` · ${note}` : ''}`}>
      <div className="flex items-baseline justify-between gap-2">
        <span className={`min-w-0 truncate ${strong ? 'font-black' : ''}`}>{label}</span>
        <span className="shrink-0 tabular-nums font-semibold">{value}{unit}</span>
      </div>
      <div className="h-3 w-full rounded-r bg-panel2">
        <div className={`h-3 rounded-r ${strong ? 'bg-neuro' : 'bg-neuro/45'}`} style={{ width: `${w}%` }} />
      </div>
      {note && <span className="text-xs text-muted">{note}</span>}
    </div>
  )
}

function WhyPhrases({ s, profileWanted }: { s: State; profileWanted: string }) {
  const p = s.routing
  const mismatch = s.profile.id !== profileWanted
  const top = p?.top_requests.slice(0, 5) ?? []
  const max = Math.max(...top.map((t) => t.p), 0)
  return (
    <section className="flex flex-col gap-2 rounded-2xl border border-line bg-panel p-4" aria-labelledby="why-phrases">
      <h3 id="why-phrases" className="text-lg font-black">1 · Why these phrases come first</h3>
      <p className="text-sm text-muted">NeuroSteno ranks phrases from the patient profile and confirmed history (rule-based, explainable) with a 40% uniform floor so rare needs stay reachable. A shortcut is kept <b>only if it lowers expected time across all messages</b>. Urgent routes are always on, whatever the history.</p>
      {mismatch && <button type="button" onClick={() => command('set_profile', { id: profileWanted }).catch(() => {})} className="self-start rounded-lg bg-panel2 px-3 py-1 text-sm">Show reasoning for this patient (load profile)</button>}
      {!p && <p className="text-sm text-muted">Routing plan not computed yet.</p>}
      {p && (
        <>
          <p className="text-xs font-bold uppercase tracking-wider text-muted">Most likely requests · {s.profile.display_name}</p>
          <div className="flex flex-col gap-1">{top.map((t) => <HBar key={t.node_id} label={t.title.split(" › ").slice(-2).join(" › ")} value={Math.round(t.p * 1000) / 10} max={max * 100} unit="%" />)}</div>
          <ul className="flex flex-col gap-1 text-sm" data-testid="why-candidates">
            {p.candidates.slice(0, 5).map((c) => (
              <li key={c.node_id} className={`rounded-lg px-2 py-1 ${c.included ? 'bg-sky-950' : 'bg-panel2'}`}>
                <b>{c.included ? '✓ shortcut' : '✗ not added'}</b> {c.title ?? c.node_id}
                {c.delta_selections !== undefined && <span className="text-muted"> · ΔE[selections] {c.delta_selections.toFixed(2)}</span>}
                <span className="block text-xs text-muted">{c.why}{c.reasons.length ? ` · ${c.reasons.join('; ')}` : ''}</span>
              </li>
            ))}
          </ul>
          {p.urgent.length > 0 && <p className="text-xs">⚠ {p.urgent.length} urgent routes always visible (e.g. suction, short of breath, nurse now).</p>}
        </>
      )}
    </section>
  )
}

function WhyTargets({ s }: { s: State }) {
  const r = s.routing?.recommended_n
  const entries = r ? Object.entries(r.by_n).sort(([a], [b]) => Number(a) - Number(b)) : []
  const max = Math.max(...entries.map(([, v]) => v.expected_time_s), 0)
  const ch = s.adaptation.last_change
  return (
    <section className="flex flex-col gap-2 rounded-2xl border border-line bg-panel p-4" aria-labelledby="why-targets">
      <h3 id="why-targets" className="text-lg font-black">2 · Why 2, 3 or 5 targets</h3>
      <p className="text-sm text-muted">More targets on screen means fewer screens to go through, but each brain decision is harder and slower. The routing model recommends the count with the lowest expected time to a confirmed message, using calibrated accuracy where it exists and labelled assumptions where it doesn't.</p>
      {entries.length === 0 && <p className="text-sm text-muted">Not computed yet.</p>}
      <div className="flex flex-col gap-1.5" data-testid="why-targets-bars">
        {entries.map(([n, v]) => {
          const rec = Number(n) === r!.n
          const inUse = Number(n) === s.screen.n_choices
          return (
            <HBar key={n} label={`${n} targets${rec ? ' · recommended' : ''}${inUse ? ' · in use' : ''}`} value={Math.round(v.expected_time_s)} max={max} unit=" s" strong={rec}
              note={`${v.expected_selections.toFixed(1)} selections × ${v.selection_time_s.toFixed(1)} s each · ${v.reliability_source}`} />
          )
        })}
      </div>
      {r && (
        <p className="flex flex-wrap items-center gap-2 text-xs" data-testid="targets-verdict">
          <Tag tag="ESTIMATE" /> model recommends <b>{r.n}</b> · in use <b>{s.screen.n_choices}</b>
          {r.n !== s.screen.n_choices && <span className="text-muted">({s.config.auto_adapt ? 'signal-adaptive count is adjusting from live decisions' : 'operator setting; signal-adaptive count is off'})</span>}
          <span className="block w-full text-muted">{r.note}</span>
        </p>
      )}
      <p className="text-xs">Signal-adaptive: {s.config.auto_adapt ? 'ON' : 'OFF'}.{' '}
        {ch ? <>Last change {ch.from} → {ch.to} targets: {ch.reason}.</> : 'If decisions keep abstaining or getting rejected, the count drops; when they are reliable, it rises.'}</p>
    </section>
  )
}

const FLOW: { id: string; phases: string[]; title: string; text: string; tone?: string }[] = [
  { id: 'ev', phases: ['EVIDENCE_COLLECTION'], title: 'Collect evidence', text: 'FBCCA scores every target; the window grows until one is clearly ahead.' },
  { id: 'abst', phases: ['ABSTAINED'], title: 'Not sure → try again', text: 'Below threshold at max window: nothing is selected, the screen never advances.', tone: 'bg-zinc-800' },
  { id: 'hl', phases: ['PREDICTION_HIGHLIGHTED'], title: 'Highlight, same screen', text: '“Did you mean this?” The prediction only lights up the card.' },
  { id: 'yes', phases: ['ACCEPTED'], title: 'YES → accepted', text: 'Only now does the next screen appear.', tone: 'bg-green-950' },
  { id: 'no', phases: ['REJECTED'], title: 'NO → rejected', text: 'Same screen, same position. Nothing is lost.', tone: 'bg-red-950' },
  { id: 'rev', phases: ['MESSAGE_REVIEW'], title: 'Final review', text: 'The whole sentence. Sent only after SEND; missing input is never approval.' },
  { id: 'sent', phases: ['MESSAGE_SENT'], title: 'Sent', text: 'Logged with its data origin.', tone: 'bg-green-950' },
  { id: 'pause', phases: ['COMM_UNAVAILABLE'], title: 'Signal bad → paused', text: 'Dropout or bad electrodes: nothing is selected. Not an alarm.', tone: 'bg-zinc-800' },
]

function Uncertainty({ s }: { s: State }) {
  const last = s.attempt?.last
  return (
    <section className="flex flex-col gap-2 rounded-2xl border border-line bg-panel p-4" aria-labelledby="why-uncertainty">
      <h3 id="why-uncertainty" className="text-lg font-black">3 · How uncertainty and confirmation work</h3>
      <p className="text-sm text-muted">A brain signal is never treated as a command. It is evidence, and the patient confirms it. The live phase is outlined below.</p>
      <ol className="grid grid-cols-2 gap-2" data-testid="flow">
        {FLOW.map((f) => {
          const on = f.phases.includes(s.phase)
          return (
            <li key={f.id} data-active={on ? 'true' : 'false'} aria-current={on ? 'step' : undefined}
              className={`rounded-xl p-2 text-sm ${f.tone ?? 'bg-panel2'} ${on ? 'ring-4 ring-pending' : ''}`}>
              <b>{f.title}</b><span className="block text-xs text-muted">{f.text}</span>
            </li>
          )
        })}
      </ol>
      <p className="text-xs">Confirmation policy: <b>{s.config.effective_policy}</b>. Provisional acceptance is used only when calibration on this data origin supports it; the final message is <b>always</b> explicitly confirmed.</p>
      {last && (
        <div className="text-xs" title="Margin between the best and second-best target vs. the accept threshold">
          Live decision margin {last.margin} vs threshold {last.threshold} ({last.reliable ? 'clear enough to highlight' : 'not yet'})
          <div className="relative mt-1 h-3 rounded bg-panel2">
            <div className="h-3 rounded bg-neuro" style={{ width: `${Math.min(100, (last.margin / Math.max(last.threshold * 2, 1e-6)) * 100)}%` }} />
            <div className="absolute inset-y-0 left-1/2 w-0.5 bg-pending" aria-hidden />
          </div>
        </div>
      )}
    </section>
  )
}

function CondRow({ label, a, b }: { label: string; a: unknown; b: unknown }) {
  const same = JSON.stringify(a) === JSON.stringify(b)
  const f = (v: unknown) => (Array.isArray(v) ? (v.length ? v.join(', ') : 'none') : v == null ? '-' : String(v))
  return <tr className="border-t border-line"><th scope="row" className="pr-2 text-left font-normal text-muted">{label}</th><td className="px-2">{f(a)}</td><td className="px-2">{f(b)}</td><td>{label === 'routing' ? '(compared)' : same ? '✓' : '✗ differs'}</td></tr>
}

function LiveAB({ s, node, profile, n, ab }: { s: State; node: string; profile: string; n: number; ab: LiveABApi }) {
  const blocker = startBlocker(s)
  const run = ab.run && ab.run.node_id === node && ab.run.n === n ? ab.run : null
  const [a, b] = run ? [run.arms.find((x) => x.arm === 'adaptive'), run.arms.find((x) => x.arm === 'static')] : []
  const match = run && a?.conditions && b?.conditions ? conditionsMatch(a.conditions, b.conditions) : null
  return (
    <section className="flex flex-col gap-2 rounded-2xl border border-line bg-panel p-4" aria-labelledby="live-ab" data-testid="live-ab">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="live-ab" className="text-lg font-black">Live demonstration</h3>
        <Tag tag="DEMO" />
        <span className="text-sm text-amber-200">Fixed order, one run each: an illustration, <b>not a controlled experiment</b>. For the controlled comparison, see the benchmark above.</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {!ab.active
          ? <button type="button" disabled={!!blocker} onClick={() => ab.start(node, profile, n)} className="rounded-lg bg-sky-600 px-4 py-2 font-bold disabled:opacity-40">▶ Run live: NeuroSteno, then fixed menu (simulated EEG)</button>
          : <button type="button" onClick={() => ab.stop()} className="rounded-lg bg-red-700 px-4 py-2 font-bold">■ Stop</button>}
        {blocker && <span className="text-sm text-amber-300" data-testid="ab-blocker">{blocker}</span>}
      </div>
      <div className="grid gap-2 text-xs md:grid-cols-2">
        <div>
          <p className="font-bold">Held the same for both arms</p>
          <ul className="list-disc pl-4 text-muted">
            <li>Simulator restarted for each arm with seed {AB_SEED} and default signal settings</li>
            <li>Random mistakes off; armed mistakes and autopilot cleared</li>
            <li>Same patient, message, {n} targets (signal-adaptive count off), confirmation policy</li>
            <li>The virtual patient confirms every step and SEND itself</li>
          </ul>
        </div>
        <div>
          <p className="font-bold">Not controlled</p>
          <ul className="list-disc pl-4 text-muted">
            <li>Patient history: NeuroSteno uses the seed profile <b>plus this machine’s saved simulated history</b>. There is no reset from the UI, so the live count can differ from the EXACT seed count.</li>
            <li>Noise realisation: same seed, different sample timing</li>
            <li>Order: NeuroSteno always runs first. That can only hand the fixed menu history, which it ignores.</li>
            <li>One run per arm: no variance, no statistics</li>
          </ul>
        </div>
      </div>
      {run && a?.conditions && b?.conditions && (
        <table className="text-xs tabular-nums" data-testid="ab-conditions">
          <caption className="text-left">
            Conditions recorded at the start of each arm:{' '}
            {match?.ok ? <b className="text-green-300">identical except routing ✓</b> : <b className="text-red-300">NOT comparable: {match?.differences.join(', ')}</b>}
          </caption>
          <thead><tr><th /><th className="px-2 text-left">NeuroSteno</th><th className="px-2 text-left">Fixed menu</th><th /></tr></thead>
          <tbody>
            {(['routing', 'seed', 'n', 'auto_adapt', 'policy', 'ssvep_uv', 'noise_multiplier', 'mistake_rate', 'drop_probability', 'bad_channels', 'disconnected'] as const).map((k) =>
              <CondRow key={k} label={k} a={a.conditions![k]} b={b.conditions![k]} />)}
          </tbody>
        </table>
      )}
      {run?.finished && <p className="text-xs text-muted">Afterwards the simulator keeps the default signal settings; routing is back on NeuroSteno and the operator’s mistake rate is restored.</p>}
    </section>
  )
}

function pctTxt(v: number) { return `${(v * 100).toFixed(v < 0.01 && v > 0 ? 2 : 1)}%` }

function BenchPanel({ bench, scenario, message, n, snr, setSnr }: { bench: Bench | null; scenario: string; message: string; n: number; snr: string; setSnr: (v: string) => void }) {
  const rows = benchFor(bench, scenario, message, snr)
  const pooled = bench?.summary?.pooled ?? []
  const levels = bench?.per_message?.snr_levels_uv ?? {}
  return (
    <section className="flex flex-col gap-3 rounded-2xl border border-line bg-panel p-4" aria-labelledby="bench" data-testid="bench-panel">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="bench" className="text-lg font-black">Benchmark: controlled, many runs</h3>
        <Tag tag={bench?.available ? 'BENCHMARK' : 'NONE'} />
      </div>
      {!bench?.available && <p className="text-sm text-muted">Not published yet. No numbers are shown until it exists.</p>}
      {bench?.available && (
        <>
          <p className="rounded-lg bg-amber-950 p-2 text-xs text-amber-100" data-testid="bench-disclaimer">
            {bench.disclaimer ?? 'Simulation only.'} {bench.real_eeg === false && <b>No real-EEG results.</b>}
          </p>
          <Picker label="Simulated signal" value={snr} onPick={setSnr}
            options={SNR_ORDER.filter((k) => k in levels || rows.length === 0).map((k) => ({ v: k, text: levels[k] != null ? `${k} (${levels[k]} µV)` : k }))} />
          {rows.length === 0 && <p className="text-sm text-muted">No benchmark rows for this task at this signal level.</p>}
          {rows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm tabular-nums" data-testid="bench-table">
                <caption className="text-left text-xs text-muted">This task, every condition: same profile, message, signal level and seed. {rows[0].runs} runs each. Bold rows = the like-for-like pair behind the counts above ({n} targets).</caption>
                <thead><tr className="text-left"><th>condition</th><th>targets</th><th>correct</th><th>sel</th><th>time</th><th>correct msgs/min</th><th>sent w/o final YES</th></tr></thead>
                <tbody>{rows.map((r) => {
                  const pair = r.condition === `A${n}` || r.condition === `B${n}`
                  return (
                    <tr key={r.condition} className={`border-t border-line ${pair ? 'font-bold' : ''}`}>
                      <td title={r.condition}>{conditionLabel(bench, r.condition)}</td>
                      <td>{r.n_start_counts && Object.keys(r.n_start_counts).length > 1 ? Object.entries(r.n_start_counts).map(([k, v]) => `${k}×${v}`).join(' ') : r.n_start}</td>
                      <td>{r.correct}/{r.runs}{(r.wrong ?? 0) > 0 ? ` (${r.wrong} wrong)` : ''}{(r.not_completed ?? 0) > 0 ? ` (${r.not_completed} unfinished)` : ''}</td>
                      <td>{r.mean_selections}</td><td>{fmtS(r.mean_completion_s)}</td>
                      <td>{r.correct_msgs_per_min}</td><td>{r.final_sent_without_confirm ?? '-'}</td>
                    </tr>
                  )
                })}</tbody>
              </table>
            </div>
          )}
          {pooled.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm tabular-nums" data-testid="pooled-table">
                <caption className="text-left text-xs text-muted">All sessions pooled. <b>Speed and safety together</b>: neither column alone tells the story. “Sent while idle” = messages sent while the virtual patient attended nothing.</caption>
                <thead><tr className="text-left"><th>condition</th><th>sessions</th><th>correct msgs/min [95% CI]</th><th>wrong-message rate</th><th>wrong msgs confirmed</th><th>sent while idle / hour</th><th>urgent completed</th></tr></thead>
                <tbody>{pooled.map((p) => (
                  <tr key={p.condition} className="border-t border-line">
                    <td title={p.condition}>{conditionLabel(bench, p.condition)}</td><td>{p.sessions}</td>
                    <td>{p.cmpm_mean.toFixed(2)}{p.cmpm_ci95 ? ` [${p.cmpm_ci95[0].toFixed(2)}, ${p.cmpm_ci95[1].toFixed(2)}]` : ''}</td>
                    <td>{pctTxt(p.error_rate)}</td><td>{p.false_confirmations_task}</td>
                    <td className={p.idle_false_sent_per_hour >= 1 ? 'text-red-300' : 'text-green-300'}>{p.idle_false_sent_per_hour}</td>
                    <td>{p.urgent_completed ?? '-'}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
          <details className="text-xs">
            <summary className="cursor-pointer font-bold">Limitations ({bench.limitations?.length ?? 0}) · how it was run</summary>
            <ul className="mt-1 list-disc pl-4">{bench.limitations?.map((l) => <li key={l}>{l}</li>)}</ul>
            <p className="mt-1 text-muted">Generated {bench.generated_at} · data origin {originLabel(bench.data_origin ?? 'simulated')} · <code>{bench.command}</code></p>
          </details>
        </>
      )}
    </section>
  )
}

function SafetyInvariant({ s }: { s: State }) {
  const inv = s.safety_invariants
  if (!inv) return null
  const blocked = inv.blocked_unconfirmed_send_attempts
  return (
    <p className="flex flex-wrap items-center gap-2 text-sm" data-testid="safety-invariant">
      <span>🔒 <b>Enforced by the engine:</b> a message cannot be sent without a decoded final YES (guaranteed by code, not a measurement).</span>
      {blocked != null && (
        <span className="flex items-center gap-2"><Tag tag="MEASURED" origin={s.mode.origin} /> unconfirmed send attempts blocked this session: <b>{blocked}</b></span>
      )}
    </p>
  )
}

export default function ComparePage({ s, ab }: { s: State; ab: LiveABApi }) {
  const [data, setData] = useState<CompareData | null>(null)
  const [bench, setBench] = useState<Bench | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [scenario, setScenario] = useState('B')
  const [message, setMessage] = useState('turn-left')
  const [n, setN] = useState(2)
  const [snr, setSnr] = useState('medium')

  useEffect(() => {
    get<CompareData>('/api/routing/compare').then(setData).catch((e: Error) => setErr(e.message))
    get<Bench>('/api/benchmark').then(setBench).catch(() => setBench({ available: false }))
  }, [])

  const row = findRow(data, scenario, message, n)
  const sc = data?.scenarios.find((x) => x.id === scenario)
  const profile = sc?.profile ?? 'new-patient'
  const profileName = sc?.title ?? profile
  const node = data?.messages[message] ?? ''
  const live = ab.run && ab.run.node_id === node && ab.run.n === n ? ab.run.arms : []

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto rounded-3xl border border-line bg-ink p-4" data-testid="compare-page" aria-label="Fixed menu versus NeuroSteno comparison">
      <header className="flex flex-wrap items-center gap-3">
        <h2 className="text-3xl font-black md:text-4xl">Same message. Fewer brain selections. Every step still confirmed.</h2>
        <ModeBadge s={s} />
      </header>
      <p className="flex flex-wrap items-center gap-2 text-xs text-muted">
        Numbers are tagged: <Tag tag="EXACT" /> navigator count · <Tag tag="ESTIMATE" /> routing model · <Tag tag="BENCHMARK" /> controlled simulated runs · <Tag tag="DEMO" /> one live run · <Tag tag="MEASURED" origin="simulated" /> this session · <Tag tag="NONE" />. Research prototype; no real-EEG performance is claimed.
      </p>
      {err && <p role="alert" className="rounded bg-red-900 p-2 text-sm">Could not load the comparison: {err}</p>}

      {data && (
        <div className="flex flex-col gap-2 rounded-2xl border border-line p-3">
          <Picker label="Patient" value={scenario} onPick={setScenario} options={data.scenarios.map((x) => ({ v: x.id, text: `${x.id} · ${x.title}` }))} />
          <Picker label="Message" value={message} onPick={setMessage} options={Object.keys(data.messages).map((m) => ({ v: m, text: MESSAGE_LABEL[m] ?? m }))} />
          <Picker label="Targets" value={n} onPick={setN} options={[2, 3, 5].map((v) => ({ v, text: String(v) }))} />
          {row && <p className="text-lg">Task: <b>“{row.text}”</b></p>}
        </div>
      )}

      {row && (
        <>
          <div className="grid gap-3 lg:grid-cols-2">
            <ArmCard arm="static" row={row} s={s} live={live.find((a) => a.arm === 'static')} measured={measuredFromSession(s, profile, n, 'static')}
              bench={benchArm(bench, scenario, message, snr, 'static', n)} snr={snr} profileName={profileName} />
            <ArmCard arm="adaptive" row={row} s={s} live={live.find((a) => a.arm === 'adaptive')} measured={measuredFromSession(s, profile, n, 'adaptive')}
              bench={benchArm(bench, scenario, message, snr, 'adaptive', n)} snr={snr} profileName={profileName} />
          </div>
          <Delta row={row} />
          <SafetyInvariant s={s} />
          <BenchPanel bench={bench} scenario={scenario} message={message} n={n} snr={snr} setSnr={setSnr} />
          <LiveAB s={s} node={node} profile={profile} n={n} ab={ab} />
        </>
      )}
      {data && <ScenarioTable data={data} n={n} sel={{ scenario, message }} onPick={(a, b) => { setScenario(a); setMessage(b) }} />}

      <h2 className="mt-2 text-2xl font-black">How NeuroSteno decides</h2>
      <div className="grid gap-3 xl:grid-cols-3">
        <WhyPhrases s={s} profileWanted={profile} />
        <WhyTargets s={s} />
        <Uncertainty s={s} />
      </div>
    </div>
  )
}
