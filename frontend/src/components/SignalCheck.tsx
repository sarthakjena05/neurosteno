import { useState } from 'react'
import { command } from '../api'
import type { AlphaResult, ElectrodeChannel, ElectrodeVerdict, State } from '../types'

/**
 * Electrode signal check (contract v1.1/1.2 `electrode_check`) and the eyes-open/closed alpha
 * check. `contact_score` is a heuristic computed from the EEG signal. It is NOT impedance:
 * UnicornPy exposes none, so the UI never calls it that.
 */

// Approximate 10-20 positions (percent of the head box) for the Unicorn Hybrid Black montage.
const MONTAGE: Record<string, [number, number]> = {
  Fz: [50, 22], C3: [24, 46], Cz: [50, 46], C4: [76, 46], Pz: [50, 66], PO7: [27, 80], Oz: [50, 88], PO8: [73, 80],
}

const VERDICT: Record<ElectrodeVerdict, { cls: string; mark: string; text: string }> = {
  good: { cls: 'bg-green-700 border-green-300', mark: '✓', text: 'good' },
  suspect: { cls: 'bg-amber-500 border-amber-200 text-black', mark: '?', text: 'suspect' },
  bad: { cls: 'bg-red-700 border-red-300', mark: '✗', text: 'bad' },
  no_data: { cls: 'bg-zinc-700 border-zinc-400', mark: '–', text: 'no data' },
}

const verdictOf = (c: ElectrodeChannel): ElectrodeVerdict => c.verdict ?? c.status ?? 'no_data'

const ALPHA_VERDICT: Record<AlphaResult['verdict'], { cls: string; text: string }> = {
  eeg_confirmed: { cls: 'bg-green-900', text: 'Brain activity confirmed (alpha rhythm rises with eyes closed)' },
  weak: { cls: 'bg-amber-900', text: 'Weak alpha response, so the signal is uncertain' },
  not_detected: { cls: 'bg-red-900', text: 'No alpha response. The headset may not be picking up brain activity' },
  invalid: { cls: 'bg-zinc-800', text: 'Check invalid (artifacts or missing data)' },
}

export function Montage({ channels }: { channels: ElectrodeChannel[] }) {
  const by = new Map(channels.map((c) => [c.label, c]))
  return (
    <div className="relative mx-auto aspect-square w-full max-w-72" role="img" aria-label="Electrode contact by position on the head"
      data-testid="montage">
      <svg viewBox="0 0 100 100" className="absolute inset-0 h-full w-full" aria-hidden>
        <ellipse cx="50" cy="54" rx="44" ry="44" fill="none" stroke="currentColor" strokeOpacity="0.35" strokeWidth="1.2" />
        <path d="M44 11 L50 3 L56 11" fill="none" stroke="currentColor" strokeOpacity="0.35" strokeWidth="1.2" />
        <text x="50" y="99" textAnchor="middle" fontSize="5" fill="currentColor" fillOpacity="0.5">back of head</text>
      </svg>
      {Object.entries(MONTAGE).map(([label, [x, y]]) => {
        const c = by.get(label)
        const v = VERDICT[c ? verdictOf(c) : 'no_data']
        return (
          <div key={label} data-testid={`electrode-${label}`} data-verdict={c ? verdictOf(c) : 'no_data'}
            title={c?.hint ?? c?.advice?.[0] ?? v.text}
            className={`absolute flex h-12 w-12 -translate-x-1/2 -translate-y-1/2 flex-col items-center justify-center rounded-full border-2 text-xs font-bold leading-tight ${v.cls}`}
            style={{ left: `${x}%`, top: `${y}%` }}>
            <span>{label}</span>
            <span aria-label={v.text}>{v.mark}</span>
          </div>
        )
      })}
    </div>
  )
}

export default function SignalCheck({ s }: { s: State }) {
  const [err, setErr] = useState<string | null>(null)
  const run = (p: Promise<unknown>) => { setErr(null); p.catch((e: Error) => setErr(e.message)) }
  const ec = s.electrode_check
  const alpha = s.decoder.calibration.alpha as unknown as AlphaResult | undefined
  const busy = !!s.calibration
  return (
    <div className="flex flex-col gap-2" data-testid="signal-check">
      {err && <p role="alert" className="rounded bg-red-900 p-2 text-sm">{err}</p>}
      {!ec && <p className="text-sm text-muted">No EEG source running. Connect a headset, simulation or replay to check the electrodes.</p>}
      {ec && (
        <>
          <p data-testid="electrode-headline" className={`rounded p-2 text-sm font-semibold ${ec.ready ? 'bg-green-950' : 'bg-amber-950'}`}>
            {ec.ready ? '✓ Ready' : '⚠ Not ready'}: {ec.summary?.headline ?? ec.message ?? ''}
            {ec.reference_ok === false && <span className="block font-normal">Reference/ground: attach both ear clips.</span>}
          </p>
          <Montage channels={ec.channels} />
          <ul className="flex flex-col gap-1 text-xs">
            {ec.channels.filter((c) => verdictOf(c) !== 'good').map((c) => (
              <li key={c.label}>
                <b>{c.label}</b> {VERDICT[verdictOf(c)].text}
                {c.contact_score != null && <> · contact {c.contact_score.toFixed(2)}</>}
                {(c.hint ?? c.advice?.[0]) && <>: {c.hint ?? c.advice[0]}</>}
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted">
            Contact score is estimated from the EEG signal itself (0–1). It is <b>not impedance</b>; the Unicorn API does not report impedance.
            {ec.window_s ? ` Last ${ec.window_s} s of data.` : ''}
          </p>
        </>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={!s.source || busy} onClick={() => run(command('start_alpha_check', { cycles: 2 }))}
          className="rounded-lg bg-sky-700 px-3 py-1.5 text-sm font-semibold disabled:opacity-40">Eyes open/closed check (~40 s)</button>
        {s.calibration?.kind === 'alpha' && (
          <button type="button" onClick={() => run(command('cancel_calibration'))} className="rounded-lg bg-red-700 px-3 py-1.5 text-sm font-semibold">Cancel check</button>
        )}
        {s.sim && (
          <>
            <button type="button" onClick={() => run(command('sim_eyes_closed', { on: true }))} className="rounded-lg bg-panel2 px-3 py-1.5 text-sm">Virtual patient: close eyes</button>
            <button type="button" onClick={() => run(command('sim_eyes_closed', { on: false }))} className="rounded-lg bg-panel2 px-3 py-1.5 text-sm">open eyes</button>
          </>
        )}
      </div>
      {alpha && (
        <div data-testid="alpha-result" className={`rounded p-2 text-xs ${ALPHA_VERDICT[alpha.verdict]?.cls ?? 'bg-zinc-800'}`}>
          {alpha.origin === 'real'
            ? <b>{ALPHA_VERDICT[alpha.verdict]?.text ?? alpha.verdict}</b>
            : <b>Pipeline check only: {alpha.verdict.replace('_', ' ')} on synthetic data. This says nothing about a real brain.</b>}
          {' '}· on <b>{String(alpha.origin).toUpperCase()}</b> data
          {alpha.reactive_occipital?.length ? <> · reactive: {alpha.reactive_occipital.join(', ')}</> : null}
          <br /><span className="text-muted">{alpha.note}</span>
        </div>
      )}
    </div>
  )
}
