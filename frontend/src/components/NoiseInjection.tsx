import { useEffect, useState } from 'react'
import { command } from '../api'
import type { State } from '../types'

/** Controlled noise injection on the LIVE stream + signal-quality-driven choice count.
 *  The slider only adds synthetic noise to the processed EEG; metrics and the target count are
 *  computed by the backend from that processed stream (nothing here sets n directly). */
export default function NoiseInjection({ s }: { s: State }) {
  const nz = s.quality?.noise
  const sq = s.adaptation.signal_quality
  const injected = nz?.injected_uv ?? 0
  const [val, setVal] = useState(injected)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { setVal(injected) }, [injected])
  const run = (p: Promise<unknown>) => { setErr(null); p.catch((e: Error) => setErr(e.message)) }
  const qualityMode = s.adaptation.source === 'signal_quality' && s.adaptation.auto
  const realEeg = s.mode.kind === 'real' || s.mode.kind === 'assisted'
  const send = (uv: number) => run(command('set_injected_noise', { uv }))

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-2 rounded-xl border border-line p-3">
        <h3 className="text-xs font-bold uppercase tracking-wider text-muted">Injected noise</h3>
        <p className="text-xs text-muted">
          Source: <b>{realEeg ? 'LIVE headset EEG' : 'SIMULATED EEG'}</b> ({s.mode.label}). The slider adds controlled synthetic
          noise to the <b>processed</b> stream that the quality monitor and decoder read. The raw stream is never modified.
        </p>
        <label className="text-sm" htmlFor="inj">Injected noise: <b>{val.toFixed(0)} µV</b> RMS per EEG channel
          {val === 0 ? ' · processed = raw EEG' : ' · processed = raw EEG + synthetic noise'}</label>
        <input id="inj" type="range" min={0} max={60} step={1} value={val}
          onChange={(e) => setVal(Number(e.target.value))}
          onMouseUp={() => send(val)} onTouchEnd={() => send(val)} onKeyUp={() => send(val)} className="w-full" />
        <div className="flex flex-wrap gap-2">
          {[0, 10, 20, 30, 45].map((v) => (
            <button key={v} type="button" onClick={() => { setVal(v); send(v) }}
              className={`rounded-lg px-3 py-1 text-sm font-semibold ${injected === v ? 'bg-sky-600 text-white' : 'bg-panel2 hover:bg-line'}`}>{v} µV</button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => run(Promise.all([command('set_adapt_source', { source: 'signal_quality' }), command('set_auto_adapt', { on: true })]))}
            className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${qualityMode ? 'bg-emerald-600 text-white' : 'bg-panel2 hover:bg-line'}`}>
            Automatic choice count from signal quality {qualityMode ? 'ON' : 'OFF'}</button>
          {qualityMode && <button type="button" onClick={() => run(command('set_auto_adapt', { on: false }))} className="rounded-lg bg-panel2 px-3 py-1.5 text-sm hover:bg-line">Turn off</button>}
        </div>
        {err && <p className="text-sm text-red-500">{err}</p>}
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Metric label="Raw EEG band noise" value={nz ? `${nz.raw_band_noise_uv.toFixed(1)} µV` : '—'} note="unmodified headset stream" />
        <Metric label="Processed band noise" value={nz ? `${nz.processed_band_noise_uv.toFixed(1)} µV` : '—'} note="what the system uses" />
        <Metric label="Measured SNR loss" value={nz ? `${nz.snr_loss_db_smoothed.toFixed(1)} dB` : '—'} note="3–40 Hz, decoding channels" />
        <Metric label="Choices per screen" value={String(s.screen.n_choices)} note={sq?.pending ? `→ ${sq.pending.to} in ${(sq.pending.dwell_s - sq.pending.stable_s).toFixed(1)} s` : 'stable'} big />
      </div>

      {sq?.available && (
        <div className="rounded-xl border border-line p-3 text-sm">
          <div className="mb-1 text-xs font-bold uppercase tracking-wider text-muted">Adaptive policy (expected correctly-confirmed messages / min)</div>
          <div className="flex flex-wrap gap-3">
            {Object.entries(sq.by_n).map(([n, v]) => (
              <span key={n} className={`rounded-lg px-2 py-1 ${Number(n) === sq.chosen ? 'bg-emerald-600 text-white' : 'bg-panel2'}`}>
                {n} targets: <b>{v.cmpm.toFixed(2)}</b> <span className="opacity-75">(acc {Math.round(v.accuracy * 100)}%, abstain {Math.round(v.abstain * 100)}%)</span>
              </span>
            ))}
          </div>
          <p className="mt-1 text-xs text-muted">{sq.reason}</p>
          {sq.locked_targets.length > 0 && <p className="mt-1 text-xs text-amber-600">Locked: {sq.locked_targets.join(', ')} targets — {sq.locked_reason}</p>}
          <p className="mt-1 text-xs text-muted">{sq.model_note}</p>
        </div>
      )}
      {s.adaptation.quality_switches.length > 0 && (
        <ul className="text-xs text-muted">
          {s.adaptation.quality_switches.map((w) => (
            <li key={w.time}>{new Date(w.time * 1000).toLocaleTimeString()}: {w.from} → {w.to} targets at {w.snr_loss_db} dB SNR loss (injected {w.injected_uv} µV)</li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Metric({ label, value, note, big }: { label: string; value: string; note: string; big?: boolean }) {
  return (
    <div className="rounded-xl border border-line p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className={`${big ? 'text-4xl' : 'text-2xl'} font-bold`}>{value}</div>
      <div className="text-xs text-muted">{note}</div>
    </div>
  )
}
