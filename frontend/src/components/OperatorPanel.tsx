import { useEffect, useState } from 'react'
import { command, get, post } from '../api'
import DemoGuide from './DemoGuide'
import InputsTab from './Inputs'
import SignalCheck from './SignalCheck'
import { flicker } from '../flicker'
import type { State } from '../types'

const TABS = ['Demo', 'Session', 'Inputs', 'Simulation', 'Signal', 'Calibration', 'Routing', 'Metrics', 'Log'] as const
type Tab = (typeof TABS)[number]

function Btn({ children, onClick, active, disabled, tone, title }: { children: React.ReactNode; onClick: () => void; active?: boolean; disabled?: boolean; tone?: string; title?: string }) {
  return (
    <button type="button" title={title} disabled={disabled} onClick={onClick}
      className={`rounded-lg px-3 py-1.5 text-sm font-semibold disabled:opacity-40 ${tone ?? (active ? 'bg-sky-600' : 'bg-panel2 hover:bg-line')}`}>
      {children}
    </button>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-line p-3">
      <h3 className="text-xs font-bold uppercase tracking-wider text-muted">{title}</h3>
      {children}
    </div>
  )
}

function useRun() {
  const [err, setErr] = useState<string | null>(null)
  const run = (p: Promise<unknown>) => { setErr(null); p.catch((e: Error) => setErr(e.message)) }
  return { err, run }
}

function PhrasePicker({ onPick, placeholder }: { onPick: (id: string) => void; placeholder: string }) {
  const [q, setQ] = useState('')
  const [res, setRes] = useState<{ node_id: string; phrase: string; title: string }[]>([])
  useEffect(() => {
    if (q.length < 2) { setRes([]); return }
    const t = setTimeout(() => get(`/api/vocabulary?q=${encodeURIComponent(q)}&limit=12`).then((d) => setRes(d.results)).catch(() => {}), 200)
    return () => clearTimeout(t)
  }, [q])
  return (
    <div className="flex flex-col gap-1">
      <input aria-label={placeholder} value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder}
        className="rounded-lg border border-line bg-ink px-2 py-1.5 text-sm" />
      {res.map((r) => (
        <button key={r.node_id} type="button" onClick={() => { onPick(r.node_id); setQ(''); setRes([]) }}
          className="rounded bg-panel2 px-2 py-1 text-left text-xs hover:bg-line" title={r.title}>{r.phrase}</button>
      ))}
    </div>
  )
}

const SCENARIO_PHRASES: [string, string][] = [
  ['Turn me left', 'comfort/position/turn-left'],
  ['Left leg: reposition', 'pain/legs-feet/left-leg/reposition/skip'],
  ['Suction now', 'breathing/suction-mouth/now'],
  ['Water, sips', 'food-water/drink/water/sips'],
]

function SessionTab({ s }: { s: State }) {
  const { err, run } = useRun()
  const [profiles, setProfiles] = useState<{ id: string; display_name: string; summary: string }[]>([])
  const [recs, setRecs] = useState<{ name: string; origin?: string; duration_s?: number; error?: string }[]>([])
  const [devices, setDevices] = useState<string[] | null>(null)
  const [hw, setHw] = useState<Record<string, any> | null>(null)
  const [flickerOn, setFlickerOn] = useState(flicker.enabled)
  const [warnAck, setWarnAck] = useState(false)
  useEffect(() => {
    get('/api/profiles').then(setProfiles).catch(() => {})
    get('/api/recordings').then(setRecs).catch(() => {})
  }, [])
  const toggleFlicker = (on: boolean) => { flicker.setEnabled(on); setFlickerOn(on); try { localStorage.setItem('ns.flicker', on ? '1' : '0') } catch { /* ignore */ } }
  const allowed = s.decoder.allowed_choices
  return (
    <div className="flex flex-col gap-3">
      {err && <p className="rounded bg-red-900 p-2 text-sm">{err}</p>}
      <Section title="EEG source">
        <div className="flex flex-wrap gap-2">
          <Btn active={s.mode.kind === 'simulated'} onClick={() => run(post('/api/mode', { mode: 'simulated' }))}>Simulated</Btn>
          <Btn active={s.mode.kind === 'none'} onClick={() => run(post('/api/mode', { mode: 'off' }))}>Off</Btn>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Btn onClick={() => run(get('/api/hardware').then(setHw))}>Hardware check</Btn>
          <Btn onClick={() => run(post('/api/hardware/discover', { only_paired: true }).then((d) => setDevices(d.devices)))}>Find paired Unicorn</Btn>
          {devices?.map((d) => <Btn key={d} tone="bg-real" onClick={() => run(post('/api/mode', { mode: 'real', serial: d }))}>Connect {d}</Btn>)}
          {devices && devices.length === 0 && <span className="text-xs text-amber-300">No paired headset found. Pair it in Windows Bluetooth settings and close other g.tec apps.</span>}
        </div>
        {hw && <pre className="max-h-32 overflow-auto rounded bg-ink p-2 text-xs">{JSON.stringify(hw, null, 1)}</pre>}
        <div className="flex flex-wrap gap-2">
          {recs.map((r) => (
            <Btn key={r.name} tone="bg-amber-700" onClick={() => run(post('/api/mode', { mode: 'replay', recording: r.name }))} title={r.error ?? ''}>
              Replay {r.name} ({r.origin ?? '?'}, {r.duration_s?.toFixed(0)} s)
            </Btn>
          ))}
        </div>
        {s.source && <p className="text-xs text-muted">{s.source.status.message} · {s.source.status.samples_received} samples · dropped {s.source.status.dropped_samples}{s.source.status.battery_percent != null ? ` · battery ${s.source.status.battery_percent}%` : ''}</p>}
        {s.source?.status.error && <p className="rounded bg-red-900 p-2 text-sm">{s.source.status.error}</p>}
      </Section>
      <Section title="Patient profile (synthetic)">
        <select aria-label="Patient profile" value={s.profile.id} onChange={(e) => run(command('set_profile', { id: e.target.value }))} className="rounded-lg border border-line bg-ink px-2 py-1.5">
          {profiles.map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}
        </select>
        <p className="text-xs text-muted">{s.profile.summary}</p>
        <ul className="list-disc pl-5 text-xs text-muted">{s.profile.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        <div className="flex flex-wrap gap-2 text-xs">
          {['night', 'after-meal', 'on-niv', 'visitors-expected'].map((t) => {
            const on = s.profile.session_tags.includes(t)
            return <Btn key={t} active={on} onClick={() => run(command('set_session_tags', { tags: on ? s.profile.session_tags.filter((x) => x !== t) : [...s.profile.session_tags, t] }))}>{t}</Btn>
          })}
        </div>
      </Section>
      <Section title="Interaction">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm">Choices per screen</span>
          {[2, 3, 5].map((n) => <Btn key={n} active={s.screen.n_choices === n} disabled={!allowed.includes(n)} title={allowed.includes(n) ? '' : 'Calibrate this target count on the current data origin first'} onClick={() => run(command('set_choices', { n }))}>{n}</Btn>)}
          <Btn active={s.config.auto_adapt} onClick={() => run(command('set_auto_adapt', { on: !s.config.auto_adapt }))}>Adaptive count {s.config.auto_adapt ? 'ON' : 'OFF'}</Btn>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm">Confirmation</span>
          {['explicit', 'provisional', 'auto'].map((p) => <Btn key={p} active={s.config.policy === p} onClick={() => run(command('set_policy', { policy: p }))}>{p}</Btn>)}
          <span className="text-xs text-muted">effective: {s.config.effective_policy}; final message always confirmed</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm">Routing</span>
          <Btn active={s.config.routing_mode === 'adaptive'} onClick={() => run(command('set_routing', { mode: 'adaptive' }))}>Risk-aware (adaptive)</Btn>
          <Btn active={s.config.routing_mode === 'static'} onClick={() => run(command('set_routing', { mode: 'static' }))}>Static baseline</Btn>
        </div>
        <div className="flex flex-col gap-1">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={warnAck} onChange={(e) => setWarnAck(e.target.checked)} />
            I understand flicker at 6–12 Hz can trigger seizures in people with photosensitive epilepsy.
          </label>
          <Btn disabled={!warnAck && !flickerOn} active={flickerOn} tone={flickerOn ? 'bg-amber-600' : undefined} onClick={() => toggleFlicker(!flickerOn)}>
            Visual stimulation (flicker) {flickerOn ? 'ON' : 'OFF'}
          </Btn>
          <span className="text-xs text-muted">Required for real SSVEP. Not needed in simulation. Refresh ≈ {flicker.stats.refreshHz ? flicker.stats.refreshHz.toFixed(0) : '?'} Hz, missed frames {flicker.stats.missed}.</span>
        </div>
      </Section>
      <Section title="Intended message (for scoring correctness)">
        <p className="text-xs">{s.intended ? `“${s.intended.text}”` : 'none (correctness recorded as unknown)'}</p>
        <PhrasePicker placeholder="Search phrases…" onPick={(id) => run(command('set_intended', { node_id: id }))} />
        {s.intended && <Btn onClick={() => run(command('set_intended', { node_id: null }))}>Clear</Btn>}
      </Section>
      <Section title="Recording">
        {s.recording ? <Btn tone="bg-red-700" onClick={() => run(command('record_stop'))}>Stop recording ({s.recording.seconds} s)</Btn>
          : <Btn disabled={!s.source} onClick={() => run(command('record_start'))}>Start recording</Btn>}
      </Section>
      <Section title="Messages confirmed by the patient">
        {s.sent.length === 0 && <p className="text-xs text-muted">None yet.</p>}
        {s.sent.map((m) => <p key={m.time} className="text-sm">“{m.text}” <span className="text-xs text-muted">({m.data_origin}{m.source === 'replay' ? ', replayed' : ''}, {new Date(m.time * 1000).toLocaleTimeString()})</span></p>)}
      </Section>
    </div>
  )
}

function SimulationTab({ s }: { s: State }) {
  const { err, run } = useRun()
  if (!s.sim) return <p className="text-sm text-muted">Switch to SIMULATED mode to use these controls. They never affect real or replayed EEG.</p>
  const sim = s.sim
  const targets = s.attempt?.targets ?? []
  return (
    <div className="flex flex-col gap-3">
      {err && <p className="rounded bg-red-900 p-2 text-sm">{err}</p>}
      <Section title="Virtual patient intent (autopilot)">
        <p className="text-sm">{sim.autopilot ? `Producing: “${sim.autopilot.text}”` : 'Idle. Click a choice on the patient screen to steer gaze manually.'}</p>
        <div className="flex flex-wrap gap-2">{SCENARIO_PHRASES.map(([l, id]) => <Btn key={id} onClick={() => run(command('sim_autopilot', { node_id: id }))}>{l}</Btn>)}</div>
        <PhrasePicker placeholder="Any phrase…" onPick={(id) => run(command('sim_autopilot', { node_id: id }))} />
        <Btn onClick={() => run(command('sim_autopilot', { node_id: null }))}>Stop autopilot</Btn>
      </Section>
      <Section title="Manual gaze (current decision)">
        <div className="flex flex-wrap gap-2">
          {targets.map((t) => <Btn key={t.key} active={sim.gaze_key === t.key} onClick={() => run(command('sim_gaze', { key: t.key }))}>{t.label} ({t.freq} Hz)</Btn>)}
          <Btn onClick={() => run(command('sim_gaze', { key: null }))}>Look away</Btn>
        </div>
      </Section>
      <Section title="Inject errors">
        <div className="flex flex-wrap gap-2">
          <Btn tone="bg-amber-700" onClick={() => run(command('sim_inject_mistake'))}>Next selection: wrong target{sim.pending_mistake ? ' (armed)' : ''}</Btn>
          <Btn onClick={() => run(command('sim_blink', { deliberate: true }))}>Deliberate blink</Btn>
          <Btn onClick={() => run(command('sim_blink', { deliberate: false }))}>Natural blink</Btn>
        </div>
        <label className="text-sm">Random mistake rate {Math.round(sim.mistake_rate * 100)}%
          <input type="range" min={0} max={0.5} step={0.05} value={sim.mistake_rate} onChange={(e) => run(command('sim_mistake_rate', { rate: Number(e.target.value) }))} className="w-full" /></label>
      </Section>
      <Section title="Signal degradation">
        <label className="text-sm">Noise × {sim.noise_multiplier.toFixed(1)}
          <input type="range" min={0.5} max={6} step={0.5} value={sim.noise_multiplier} onChange={(e) => run(command('sim_noise', { multiplier: Number(e.target.value) }))} className="w-full" /></label>
        <label className="text-sm">SSVEP amplitude {sim.ssvep_uv.toFixed(1)} µV
          <input type="range" min={0.2} max={3} step={0.1} value={sim.ssvep_uv} onChange={(e) => run(command('sim_ssvep', { uv: Number(e.target.value) }))} className="w-full" /></label>
        <div className="flex flex-wrap gap-1">
          {['Fz', 'C3', 'Cz', 'C4', 'Pz', 'PO7', 'Oz', 'PO8'].map((l) => {
            const on = sim.bad_channels.includes(l)
            return <Btn key={l} tone={on ? 'bg-red-700' : undefined} onClick={() => run(command('sim_bad_channel', { label: l, on: !on }))}>{l}{on ? ' ✗' : ''}</Btn>
          })}
        </div>
        <div className="flex flex-wrap gap-2">
          <Btn tone={sim.disconnected ? 'bg-red-700' : undefined} onClick={() => run(command('sim_disconnect', { on: !sim.disconnected }))}>{sim.disconnected ? 'Restore Bluetooth' : 'Simulate Bluetooth dropout'}</Btn>
          <Btn onClick={() => run(command('sim_drop', { p: sim.drop_probability ? 0 : 0.1 }))}>Packet loss {sim.drop_probability ? 'ON' : 'OFF'}</Btn>
        </div>
      </Section>
    </div>
  )
}

function SignalTab({ s }: { s: State }) {
  const q = s.quality
  const last = s.attempt?.last
  return (
    <div className="flex flex-col gap-3">
      <Section title="Signal check: electrodes + eyes open/closed">
        <SignalCheck s={s} />
      </Section>
      <Section title="Signal quality (independent of classification)">
        {!q.channels?.length && <p className="text-sm text-muted">No data.</p>}
        <div className="grid grid-cols-4 gap-1 text-xs">
          {q.channels?.map((c) => (
            <div key={c.label} className={`rounded p-1.5 ${c.status === 'ok' ? 'bg-green-900' : c.status === 'flat' || c.status === 'no_data' ? 'bg-zinc-700' : 'bg-red-900'}`}>
              <b>{c.label}</b> {c.status}<br />{c.std_uv ?? '-'} µV
            </div>
          ))}
        </div>
        <p className="text-xs">Decoding channels OK: {q.good_decoding_channels ?? 0}/{q.decoding_labels?.length ?? 4} (need {q.required_decoding_channels}) · inter-channel correlation {q.common_mode ?? '-'} · last sample {q.seconds_since_last_sample ?? '-'} s ago</p>
        {q.problem && <p className="rounded bg-amber-900 p-2 text-sm">{q.problem}</p>}
      </Section>
      <Section title="Live decoder (FBCCA + dynamic stopping)">
        {!s.attempt && <p className="text-sm text-muted">No evidence window open.</p>}
        {s.attempt && (
          <>
            <p className="text-xs">{s.attempt.kind} · window {last?.window_s ?? 0} s of max {s.attempt.max_window_s} s · thresholds {s.attempt.calibrated ? 'CALIBRATED' : 'DEFAULT (synthetic-derived)'}</p>
            {s.attempt.targets.map((t, i) => {
              const sc = last?.scores[i] ?? 0
              return (
                <div key={t.key} className="flex items-center gap-2 text-xs">
                  <span className="w-32 truncate">{t.label}</span>
                  <div className="h-3 flex-1 rounded bg-panel2"><div className="h-3 rounded bg-sky-500" style={{ width: `${Math.min(100, sc * 100)}%` }} /></div>
                  <span className="w-12 text-right">{sc.toFixed(3)}</span>
                </div>
              )
            })}
            {last && <p className="text-xs text-muted">margin {last.margin} vs threshold {last.threshold}: {last.reason}</p>}
          </>
        )}
      </Section>
      <Section title="Experimental blink detector">
        <p className="text-xs">enabled {String(s.blink.config.enabled)} · calibrated {String(s.blink.config.calibrated)} · threshold {String(s.blink.config.threshold_uv)} µV · refractory {String(s.blink.config.refractory_s)} s</p>
        {s.blink.events.map((e) => <p key={e.time} className="text-xs">{e.accepted ? '✓' : '·'} {e.amplitude_uv} µV, {e.reason}</p>)}
      </Section>
    </div>
  )
}

function CalibrationTab({ s }: { s: State }) {
  const { err, run } = useRun()
  const cal = s.decoder.calibration
  return (
    <div className="flex flex-col gap-3">
      {err && <p className="rounded bg-red-900 p-2 text-sm">{err}</p>}
      <Section title="SSVEP calibration (cued trials + rest trials)">
        <p className="text-xs text-muted">Tunes accept/abstain thresholds per window length and the no-control (rest) level. Results are per data origin and never shared between real and simulated data.</p>
        <div className="flex flex-wrap gap-2">
          {[2, 3, 5].map((n) => <Btn key={n} disabled={!s.source || !!s.calibration} onClick={() => run(command('start_calibration', { n }))}>Calibrate {n} targets</Btn>)}
          {s.calibration && <Btn tone="bg-red-700" onClick={() => run(command('cancel_calibration'))}>Cancel</Btn>}
        </div>
        {Object.entries(cal).filter(([k]) => k.startsWith('ssvep_')).map(([k, r]: [string, any]) => (
          <div key={k} className="rounded bg-ink p-2 text-xs">
            <b>{r.n_targets} targets</b> on <b>{String(r.origin).toUpperCase()}</b> data: accepted-trial accuracy {r.dynamic_stopping_estimate?.accuracy_of_accepted ?? '-'},
            abstention {r.dynamic_stopping_estimate?.abstention_rate}, mean decision {r.dynamic_stopping_estimate?.mean_decision_s} s,
            rest false-accepts {r.dynamic_stopping_estimate?.rest_false_accept_rate ?? '-'} · supported: <b>{String(r.supported)}</b>
            <br /><span className="text-muted">{r.caveat}</span>
          </div>
        ))}
      </Section>
      <Section title="Blink calibration (experimental)">
        <div className="flex flex-wrap gap-2">
          <Btn disabled={!s.source || !!s.calibration} onClick={() => run(command('start_blink_calibration'))}>Calibrate blinks</Btn>
          <Btn active={s.blink.config.enabled} onClick={() => run(command('set_blink_enabled', { on: !s.blink.config.enabled }))}>Blink-to-reject {s.blink.config.enabled ? 'ON' : 'OFF'}</Btn>
        </div>
        {cal.blink && <p className="text-xs">{String((cal.blink as any).note)} natural max {String((cal.blink as any).natural_max_uv)} µV, deliberate p20 {String((cal.blink as any).deliberate_p20_uv)} µV</p>}
        <p className="text-xs text-muted">Double-blink vs. sustained eye closure is NOT distinguished; that is untested on this hardware.</p>
      </Section>
    </div>
  )
}

function RoutingTab({ s }: { s: State }) {
  const p = s.routing
  const [cmp, setCmp] = useState<any>(null)
  const [bench, setBench] = useState<any>(null)
  return (
    <div className="flex flex-col gap-3">
      {!p && <p className="text-sm text-muted">Computing routing plan…</p>}
      {p && (
        <>
          <Section title={`Routing decision · ${p.mode} · ${p.n} targets ${s.routing_pending ? '(update pending: applies on Home)' : ''}`}>
            {p.explanation.map((e) => <p key={e} className="text-xs">{e}</p>)}
            <p className="text-sm">E[selections per message]: <b>{p.expected.selections}</b> (static menu {p.expected.static_selections}) · E[time] {p.expected.time_s} s (static {p.expected.static_time_s} s)</p>
            <p className="text-xs">Verification: <b>{p.verification.policy}</b> ({p.verification.reason}); final message: {p.verification.final_message}</p>
          </Section>
          <Section title="Shortcut candidates (context ranking → cost check)">
            {p.candidates.length === 0 && <p className="text-xs text-muted">No candidates above threshold for this profile.</p>}
            {p.candidates.map((c) => (
              <div key={c.node_id} className={`rounded p-2 text-xs ${c.included ? 'bg-green-950' : 'bg-zinc-800'}`}>
                <b>{c.included ? '✓ included' : '✗ excluded'}</b> {c.title ?? c.node_id} (score {c.score})
                {c.delta_selections !== undefined && <> · ΔE[sel] {c.delta_selections} · Δt {c.delta_time_s} s</>}
                <br />{c.why} · {c.reasons.join('; ')}
              </div>
            ))}
          </Section>
          <Section title="Urgent routes (always available, history-independent)">
            {p.urgent.length ? p.urgent.map((u) => <p key={u} className="text-xs">⚠ {u}</p>) : <p className="text-xs text-muted">None (static baseline)</p>}
          </Section>
          <Section title="Target count from expected completion time">
            <table className="text-xs"><thead><tr><th className="text-left">n</th><th>E[sel]</th><th>T/sel</th><th>E[time]</th><th className="text-left">reliability</th></tr></thead>
              <tbody>{Object.entries(p.recommended_n.by_n).map(([n, r]) => (
                <tr key={n} className={Number(n) === p.recommended_n.n ? 'text-green-300' : ''}><td>{n}</td><td className="text-center">{r.expected_selections}</td><td className="text-center">{r.selection_time_s}</td><td className="text-center">{r.expected_time_s}</td><td>{r.reliability_source}</td></tr>
              ))}</tbody></table>
            <p className="text-xs text-muted">{p.recommended_n.note}</p>
          </Section>
          <Section title="Most likely requests (profile distribution)">
            {p.top_requests.map((t) => <p key={t.node_id} className="text-xs">{(t.p * 100).toFixed(2)}% · {t.title}</p>)}
          </Section>
        </>
      )}
      <Section title="Scenario comparison (exact selection counts)">
        <Btn onClick={() => get('/api/routing/compare').then(setCmp).catch(() => {})}>Compute A/B/C</Btn>
        {cmp && (
          <table className="text-xs">
            <thead><tr><th className="text-left">scn</th><th>n</th><th className="text-left">message</th><th>static</th><th>adaptive</th></tr></thead>
            <tbody>{cmp.rows.map((r: any, i: number) => (
              <tr key={i}><td>{r.scenario}</td><td className="text-center">{r.n}</td><td>{r.message}</td><td className="text-center">{r.static}</td>
                <td className={`text-center ${r.delta < 0 ? 'text-green-300' : r.delta > 0 ? 'text-red-300' : ''}`}>{r.adaptive}</td></tr>
            ))}</tbody>
          </table>
        )}
      </Section>
      <Section title="Benchmark report">
        <Btn onClick={() => get('/api/benchmark').then(setBench).catch(() => {})}>Load latest benchmark</Btn>
        {bench && !bench.available && <p className="text-xs text-muted">No benchmark run yet (scripts/benchmark.py).</p>}
        {bench?.available && <pre className="max-h-80 overflow-auto rounded bg-ink p-2 text-xs">{JSON.stringify(bench.summary ?? bench, null, 1)}</pre>}
      </Section>
    </div>
  )
}

function MetricsTab({ s }: { s: State }) {
  return (
    <div className="flex flex-col gap-3">
      <Section title="Current message task">
        {s.task ? <pre className="text-xs">{JSON.stringify(s.task, null, 1)}</pre> : <p className="text-xs text-muted">No task in progress.</p>}
      </Section>
      <Section title="Summary (grouped by data origin; real, simulated and replay never mixed)">
        <table className="text-xs">
          <thead><tr><th>origin</th><th>source</th><th>routing</th><th>profile</th><th>n</th><th>msgs</th><th>sel</th><th>rej</th><th>time s</th><th>correct</th></tr></thead>
          <tbody>{s.metrics.summary.map((r: any, i) => (
            <tr key={i} className={r.origin === 'real' && r.source !== 'replay' ? 'text-green-300' : ''}><td>{r.origin}</td><td>{r.source ?? '-'}</td><td>{r.routing_mode}</td><td>{r.profile}</td><td>{r.n}</td><td>{r.messages}</td><td>{r.mean_selections}</td><td>{r.mean_rejected_predictions}</td><td>{r.mean_completion_s}</td><td>{r.correct_final}</td></tr>
          ))}</tbody>
        </table>
      </Section>
      <Section title="Recent messages">
        {s.metrics.recent.map((m, i) => (
          <p key={i} className="text-xs">[{m.origin}/{m.routing_mode}/{m.n_start}] {m.selections} sel, {m.rejected_predictions} rejected, {m.completion_s} s, {m.correct === null ? 'unscored' : m.correct ? 'correct' : 'WRONG'}: “{m.final_text}”</p>
        ))}
      </Section>
    </div>
  )
}

function LogTab({ s }: { s: State }) {
  return (
    <div className="flex flex-col gap-1">
      {s.events.map((e, i) => (
        <p key={i} className="text-xs"><span className="text-muted">{new Date(e.time * 1000).toLocaleTimeString()} [{e.kind}]</span> {e.text}</p>
      ))}
    </div>
  )
}

export default function OperatorPanel({ s, onOpenCompare }: { s: State; onOpenCompare?: () => void }) {
  const [tab, setTab] = useState<Tab>('Demo')
  const goTab = (t: string) => { if ((TABS as readonly string[]).includes(t)) setTab(t as Tab) }
  // WAI-ARIA tabs pattern: arrow keys / Home / End move between tabs.
  const onTabKey = (e: React.KeyboardEvent<HTMLButtonElement>, i: number) => {
    const j = e.key === 'ArrowRight' ? (i + 1) % TABS.length : e.key === 'ArrowLeft' ? (i - 1 + TABS.length) % TABS.length
      : e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : -1
    if (j < 0) return
    e.preventDefault()
    setTab(TABS[j])
    document.getElementById(`optab-${TABS[j]}`)?.focus()
  }
  return (
    <aside className="flex h-full flex-col gap-2 overflow-hidden rounded-3xl border border-line bg-panel p-3" aria-label="Operator and caregiver panel">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-black">Operator panel</h2>
        <span className="text-xs text-muted">phase {s.phase} · {s.vocabulary.phrases} phrases</span>
      </div>
      <div className="flex flex-wrap gap-1" role="tablist" aria-label="Operator panel sections">
        {TABS.map((t, i) => (
          <button key={t} id={`optab-${t}`} type="button" role="tab" aria-selected={tab === t} aria-controls="optab-panel"
            tabIndex={tab === t ? 0 : -1} onClick={() => setTab(t)} onKeyDown={(e) => onTabKey(e, i)}
            className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${tab === t ? 'bg-sky-600' : 'bg-panel2 hover:bg-line'}`}>
            {t}
          </button>
        ))}
      </div>
      <div id="optab-panel" role="tabpanel" aria-labelledby={`optab-${tab}`} tabIndex={0} className="flex-1 overflow-y-auto pr-1">
        {tab === 'Demo' && <DemoGuide s={s} onGoTab={goTab} onOpenCompare={onOpenCompare} />}
        {tab === 'Session' && <SessionTab s={s} />}
        {tab === 'Inputs' && <InputsTab s={s} />}
        {tab === 'Simulation' && <SimulationTab s={s} />}
        {tab === 'Signal' && <SignalTab s={s} />}
        {tab === 'Calibration' && <CalibrationTab s={s} />}
        {tab === 'Routing' && <RoutingTab s={s} />}
        {tab === 'Metrics' && <MetricsTab s={s} />}
        {tab === 'Log' && <LogTab s={s} />}
      </div>
    </aside>
  )
}
