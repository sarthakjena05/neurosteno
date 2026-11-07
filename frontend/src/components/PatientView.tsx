import { useEffect, useRef } from 'react'
import { command } from '../api'
import { flicker, type Stim } from '../flicker'
import { calibratedForCurrent, MODE_FRAME, modeCaption, originLabel } from '../mode'
import type { Slot, State, Target } from '../types'

const ICONS: Record<string, string> = {
  pain: '🤕', breathing: '🫁', 'food-water': '🥤', comfort: '🛏️', 'call-caregiver': '🧑‍⚕️',
}

export const MODE_STYLE: Record<string, string> = {
  real: 'bg-real text-white',
  simulated: 'bg-sim text-white',
  replay: 'bg-replay text-black',
  none: 'bg-zinc-600 text-white',
}

export function gridClass(n: number): string {
  if (n <= 2) return 'grid-cols-1 md:grid-cols-2'
  if (n === 3) return 'grid-cols-1 md:grid-cols-3'
  return 'grid-cols-2 md:grid-cols-3'
}

export function phaseText(s: State): string {
  switch (s.phase) {
    case 'OFF': return 'No EEG source connected'
    case 'IDLE': return s.feedback?.type === 'no_control' ? 'Standing by. Look at a choice when you are ready.' : 'Get ready…'
    case 'EVIDENCE_COLLECTION': return 'Listening. Look at the choice you want.'
    case 'PREDICTION_HIGHLIGHTED': return s.highlight ? `Did you mean “${s.highlight.label}”? Look at YES or NO.` : 'Is the highlighted choice right? Look at YES or NO.'
    case 'ACCEPTED': return 'Accepted.'
    case 'REJECTED': return s.feedback?.type === 'not_confirmed' ? 'Not confirmed. Nothing was selected.' : 'Rejected. Same screen, try again.'
    case 'ABSTAINED': return 'Not sure what you chose. Let’s try again.'
    case 'MESSAGE_REVIEW': return 'Check your message. Look at SEND or CHANGE.'
    case 'MESSAGE_SENT': return 'Message sent to your caregiver.'
    case 'COMM_UNAVAILABLE': return 'Communication paused'
    case 'CALIBRATING': return 'Calibration'
  }
}

function useStim(id: string, stim: Stim | null) {
  const ref = useRef<HTMLDivElement>(null)
  const key = stim ? JSON.stringify(stim) : ''
  useEffect(() => {
    flicker.register(id, ref.current, stim)
    return () => flicker.unregister(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, key])
  return ref
}

function targetFor(s: State, key: string, kinds: string[]): Target | undefined {
  if (!s.attempt || !kinds.includes(s.attempt.kind)) return undefined
  return s.attempt.targets.find((t) => t.key === key)
}

function stimOf(t: Target | undefined): Stim | null {
  if (!t) return null
  const anyT = t as Target & { code?: number[]; shift?: number }
  if (anyT.code && anyT.shift !== undefined) return { kind: 'cvep', code: anyT.code, shift: anyT.shift }
  return { kind: 'ssvep', freq: t.freq }
}

function SlotCard({ s, slot }: { s: State; slot: Slot }) {
  const t = targetFor(s, slot.key, ['choose'])
  const ref = useStim(`slot-${slot.index}`, stimOf(t))
  const hl = s.highlight?.slot_index === slot.index && ['PREDICTION_HIGHLIGHTED', 'ACCEPTED'].includes(s.phase)
  const accepted = hl && s.phase === 'ACCEPTED'
  const dimmed = s.highlight && !hl && ['PREDICTION_HIGHLIGHTED', 'ACCEPTED'].includes(s.phase)
  const isSim = s.mode.kind === 'simulated'
  const gazed = s.sim?.gaze_key === slot.key
  const homeCat = s.screen.at_home && slot.kind === 'option'
  const onClick = () => { if (isSim) command('sim_gaze', { key: slot.key }).catch(() => {}) }
  const urgentGroup = slot.items.some((i) => i.urgent_route)
  const suggestedGroup = slot.items.some((i) => i.suggested && i.key.startsWith('sc:'))
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`Choice ${slot.index + 1}: ${slot.label}${hl ? ' (predicted)' : ''}`}
      data-testid={`slot-${slot.index}`}
      data-highlighted={hl ? 'true' : 'false'}
      className={[
        'relative flex min-h-48 flex-col overflow-hidden rounded-3xl border-4 p-5 text-left transition-all',
        slot.is_back ? 'border-line bg-panel2' : 'border-line bg-panel',
        hl && !accepted ? 'border-pending highlight-ring scale-[1.02]' : '',
        accepted ? 'border-accept bg-green-950' : '',
        dimmed ? 'opacity-35' : '',
        isSim ? 'cursor-pointer hover:border-sky-400' : 'cursor-default',
      ].join(' ')}
    >
      <div ref={ref} className="stim-layer" />
      <div className="relative z-10 flex items-start justify-between gap-2">
        <span className="rounded-lg bg-black/60 px-2 py-0.5 text-sm font-semibold text-muted">
          {slot.index + 1}{t ? ` · ${t.freq} Hz` : ''}
        </span>
        <div className="flex gap-1">
          {urgentGroup && <span className="rounded-lg bg-red-600 px-2 py-0.5 text-sm font-bold">URGENT</span>}
          {suggestedGroup && <span className="rounded-lg bg-sky-600 px-2 py-0.5 text-sm font-bold">FOR YOU</span>}
          {gazed && <span className="rounded-lg bg-sim px-2 py-0.5 text-sm font-bold">virtual gaze</span>}
        </div>
      </div>
      {hl && !accepted && (
        <div className="relative z-10 mt-2 self-start rounded-xl bg-pending px-3 py-1 text-xl font-black text-black">
          Did you mean this?
        </div>
      )}
      {accepted && <div className="relative z-10 mt-2 self-start rounded-xl bg-accept px-3 py-1 text-xl font-black text-black">✓ Accepted</div>}
      {slot.kind === 'option' ? (
        <div className="relative z-10 mt-auto flex items-center gap-4 rounded-2xl bg-black/55 p-3">
          {homeCat && <span className="text-6xl" aria-hidden>{ICONS[slot.key] ?? ''}</span>}
          <span className={`${slot.is_back ? 'text-3xl' : 'text-4xl'} font-extrabold leading-tight`}>
            {slot.is_back ? '↩ ' : ''}{slot.label}
          </span>
        </div>
      ) : (
        <div className="relative z-10 mt-3 flex flex-1 flex-col gap-2">
          <div className="rounded-xl bg-black/55 px-3 py-1 text-2xl font-extrabold">{slot.label}</div>
          <div className={`grid gap-2 ${s.screen.at_home ? 'grid-cols-1' : 'grid-cols-1 sm:grid-cols-2'}`}>
            {slot.items.map((it) => (
              <div key={it.key} className={`flex items-center gap-3 rounded-xl px-3 py-2 ${s.screen.at_home ? 'bg-panel2 text-3xl font-extrabold' : 'bg-black/50 text-xl font-semibold'}`}>
                {s.screen.at_home && <span aria-hidden className="text-4xl">{ICONS[it.key] ?? ''}</span>}
                <span>{it.kind === 'back' ? '↩ ' : ''}{it.label}</span>
                {it.urgent_route && <span className="ml-auto rounded bg-red-600 px-1.5 text-xs font-bold">URGENT</span>}
              </div>
            ))}
          </div>
        </div>
      )}
      {homeCat && s.screen.home_badges[slot.key] && (
        <div className="relative z-10 mt-2 flex flex-wrap gap-1">
          {s.screen.home_badges[slot.key].slice(0, 3).map((b) => (
            <span key={b.title} className={`rounded-lg px-2 py-0.5 text-sm font-semibold ${b.urgent ? 'bg-red-900/80' : 'bg-sky-900/80'}`}>
              {b.urgent ? '⚠ ' : '★ '}{b.title}
            </span>
          ))}
        </div>
      )}
      {slot.suggest_reasons.length > 0 && (
        <p data-testid={`why-${slot.index}`} className="relative z-10 mt-2 self-start rounded-lg bg-sky-950/85 px-2 py-1 text-sm text-sky-100">
          <span className="font-bold">NeuroRoute:</span> {slot.suggest_reasons.join(' · ')}
        </p>
      )}
    </button>
  )
}

function DecisionTarget({ s, tkey, label, tone }: { s: State; tkey: 'yes' | 'no'; label: string; tone: 'yes' | 'no' }) {
  const t = targetFor(s, tkey, ['confirm', 'review'])
  const ref = useStim(`decision-${tkey}`, stimOf(t))
  const isSim = s.mode.kind === 'simulated'
  const gazed = s.sim?.gaze_key === tkey
  return (
    <button
      type="button"
      data-testid={`decision-${tkey}`}
      onClick={() => isSim && command('sim_gaze', { key: tkey }).catch(() => {})}
      className={`relative min-h-32 flex-1 overflow-hidden rounded-3xl border-4 p-4 text-4xl font-black ${tone === 'yes' ? 'border-accept bg-green-950' : 'border-reject bg-red-950'} ${isSim ? 'cursor-pointer' : 'cursor-default'}`}
    >
      <div ref={ref} className="stim-layer" />
      <span className="relative z-10 rounded-2xl bg-black/60 px-4 py-2">{label}</span>
      {t && <span className="absolute left-3 top-2 z-10 rounded bg-black/60 px-2 text-sm text-muted">{t.freq} Hz</span>}
      {gazed && <span className="absolute right-3 top-2 z-10 rounded bg-sim px-2 text-sm">virtual gaze</span>}
    </button>
  )
}

function Evidence({ s }: { s: State }) {
  const a = s.attempt
  if (!a) return <div className="h-3" />
  const frac = Math.min(1, a.elapsed_s / (a.max_window_s + 0.3))
  // Deliberately NOT aria-live: this updates ~10x per second and would flood screen readers.
  // The phase sentence above carries the announcements.
  return (
    <div className="flex items-center gap-3">
      <div className="h-3 flex-1 overflow-hidden rounded-full bg-panel2" role="progressbar" data-testid="evidence"
        aria-label={a.kind === 'choose' ? 'Evidence collected for this choice' : 'Evidence collected for the confirmation'}
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(frac * 100)}>
        <div className="h-full bg-sky-400 transition-all" style={{ width: `${frac * 100}%` }} />
      </div>
      <span className="w-40 text-right text-sm text-muted">{a.kind === 'choose' ? 'collecting evidence' : 'checking'} {a.elapsed_s.toFixed(1)} s</span>
    </div>
  )
}

export function ModeBadge({ s }: { s: State }) {
  const k = s.mode.kind
  const label = k === 'replay' ? `REPLAY · recorded ${String(s.mode.replay_of ?? 'unknown').toUpperCase()} data` : s.mode.label
  return (
    <span data-testid="mode-badge" className={`rounded-xl px-4 py-2 text-lg font-black tracking-wide ${MODE_STYLE[k]}`}>
      {label}{k === 'simulated' ? ' · synthetic signals' : ''}
    </span>
  )
}

/** REAL mode only: whether the decoder may communicate on this data at the current target count. */
export function CalibrationChip({ s }: { s: State }) {
  if (s.mode.kind !== 'real') return null
  const ok = calibratedForCurrent(s)
  return (
    <span data-testid="calib-chip" className={`rounded-xl px-3 py-2 text-base font-bold ${ok ? 'bg-green-900 text-green-100' : 'bg-amber-400 text-black'}`}>
      {ok ? `✓ Calibrated for ${s.screen.n_choices} targets` : `⚠ Not calibrated for ${s.screen.n_choices} targets`}
    </span>
  )
}

function Overlay({ children, tone = 'bg-black/85', label, role = 'dialog', testId }: {
  children: React.ReactNode; tone?: string; label: string; role?: 'dialog' | 'alertdialog' | 'status'; testId?: string
}) {
  return (
    <div role={role} aria-modal={role === 'status' ? undefined : true} aria-label={label} data-testid={testId}
      className={`absolute inset-0 z-30 flex flex-col items-center justify-center gap-6 rounded-3xl p-8 text-center ${tone}`}>
      {children}
    </div>
  )
}

/** Says which data origin produced a sent message, so a simulated message is never mistaken for a real one. */
function SentOrigin({ s }: { s: State }) {
  const origin = s.sent[0]?.data_origin ?? s.mode.origin
  if (origin === 'real' && s.mode.kind === 'real') return null
  return (
    <p data-testid="sent-origin" className="rounded-xl bg-black/50 px-4 py-2 text-xl font-bold">
      Produced from {originLabel(origin)}{s.mode.kind === 'replay' ? ' played back from a recording' : ''}. Not a live real-EEG message.
    </p>
  )
}

/**
 * Compact, judge-visible summary of what risk-aware routing is doing right now.
 * Everything here is read from the snapshot; context never changes decoder output.
 */
export function NeuroRouteStrip({ s }: { s: State }) {
  const p = s.routing
  const change = s.adaptation.last_change
  // Server clock only (no Date.now() during render). Without a timestamp, show the latest change.
  const freshChange = change && (change.time == null || s.server_time == null || s.server_time - change.time < 30)
  if (!p && !freshChange && !s.routing_pending) return null
  const adaptive = s.config.routing_mode === 'adaptive'
  const sel = p?.expected.selections
  const stat = p?.expected.static_selections
  const saved = sel != null && stat != null ? Math.round((stat - sel) * 10) / 10 : null
  return (
    <div data-testid="neuroroute-strip" className="flex flex-wrap items-center gap-2 rounded-2xl border border-line bg-panel px-3 py-2 text-base">
      <span className={`rounded-lg px-2 py-0.5 font-black ${adaptive ? 'bg-sky-600' : 'bg-zinc-600'}`}>
        {adaptive ? 'NeuroRoute · risk-aware' : 'Static menu (baseline)'}
      </span>
      {p && sel != null && (
        <span className="text-muted">
          ≈ <b className="text-text">{sel}</b> selections per message for {s.profile.display_name.split(' ')[0]}
          {adaptive && stat != null && <> (static menu {stat}{saved != null && saved > 0 ? `, ${saved} fewer` : ''})</>}
        </span>
      )}
      {p && adaptive && p.shortcuts.length > 0 && <span className="text-muted">· {p.shortcuts.length} shortcut{p.shortcuts.length === 1 ? '' : 's'}</span>}
      {p && p.urgent.length > 0 && <span className="text-muted">· {p.urgent.length} urgent routes always on</span>}
      {s.routing_pending && <span className="rounded-lg bg-amber-900 px-2 py-0.5 text-amber-100">Route update applies when you return Home</span>}
      {freshChange && change && (
        <span role="status" data-testid="adapt-notice" className="rounded-lg bg-sky-950 px-2 py-0.5 text-sky-100">
          Now {change.to} choices per screen (was {change.from}): {change.reason}
        </span>
      )}
    </div>
  )
}

function CalibrationOverlay({ s }: { s: State }) {
  const c = s.calibration!
  if (c.kind === 'blink') {
    return (
      <Overlay label="Blink calibration">
        <h2 className="text-4xl font-black">Blink calibration (experimental)</h2>
        <p className="text-3xl">{c.sub === 'relax' ? 'Relax and look at the screen. Blink normally.' : c.sub === 'computing' ? 'Computing…' : 'BLINK FIRMLY NOW, once.'}</p>
        <p className="text-muted">Step {c.trial + 1} of {c.trials}</p>
      </Overlay>
    )
  }
  return <CalibrationTargets s={s} />
}

function CalibTarget({ i, freq, active, cue }: { i: number; freq: number; active: boolean; cue: boolean }) {
  const ref = useStim(`calib-${i}`, active ? { kind: 'ssvep', freq } : null)
  return (
    <div className={`relative flex min-h-40 items-center justify-center overflow-hidden rounded-3xl border-4 ${cue ? 'border-pending' : 'border-line'} bg-panel`}>
      <div ref={ref} className="stim-layer" />
      <span className="relative z-10 rounded-xl bg-black/60 px-3 py-1 text-3xl font-black">{cue ? '👁 LOOK HERE' : `${freq} Hz`}</span>
    </div>
  )
}

function CalibrationTargets({ s }: { s: State }) {
  const c = s.calibration!
  const stim = c.sub === 'stim' || c.sub === 'cue'
  return (
    <div role="dialog" aria-modal="true" aria-label="SSVEP calibration" className="absolute inset-0 z-30 flex flex-col gap-4 rounded-3xl bg-ink p-6">
      <div className="flex items-baseline justify-between">
        <h2 className="text-3xl font-black">SSVEP calibration · {c.n} targets</h2>
        <span className="text-xl text-muted">Trial {Math.min(c.trial + 1, c.trials)} / {c.trials}</span>
      </div>
      <p className="text-2xl">
        {c.sub === 'computing' ? 'Computing thresholds…' : c.rest_trial ? 'REST: look at the centre, do not focus on any target.' : c.sub === 'break' ? 'Short break' : 'Focus on the highlighted target.'}
      </p>
      <div className={`grid flex-1 gap-4 ${gridClass(c.n)}`}>
        {c.freqs.map((f, i) => <CalibTarget key={i} i={i} freq={f} active={stim} cue={c.cue_target === i && c.sub !== 'break'} />)}
      </div>
    </div>
  )
}

export default function PatientView({ s }: { s: State }) {
  const highlighted = s.phase === 'PREDICTION_HIGHLIGHTED'
  const review = s.phase === 'MESSAGE_REVIEW' && s.review
  return (
    <section data-testid="patient-screen" data-mode={s.mode.kind}
      className={`relative flex h-full flex-col gap-4 overflow-y-auto rounded-3xl border-[6px] bg-ink p-4 md:p-6 ${MODE_FRAME[s.mode.kind]}`}
      aria-label={`Patient communication screen, ${s.mode.label}`}>
      <header className="flex flex-wrap items-center gap-3">
        <ModeBadge s={s} />
        <CalibrationChip s={s} />
        <span className="text-lg text-muted">{s.screen.n_choices} choices · {s.config.effective_policy === 'provisional' ? 'provisional + correction window' : 'confirm every choice'}</span>
        <span className="ml-auto rounded-lg border border-line px-2 py-1 text-xs text-muted">Research prototype. Not a medical device.</span>
        <p data-testid="mode-caption" className="w-full text-sm text-muted">{modeCaption(s)}</p>
      </header>
      <NeuroRouteStrip s={s} />

      <nav aria-label="Where you are" className="flex flex-wrap items-center gap-2 text-xl">
        <span className="font-bold">Home</span>
        {s.screen.crumbs.map((c, i) => (
          <span key={i} className="flex items-center gap-2">
            <span className="text-muted">›</span>
            <span className={c.provisional ? 'rounded border-2 border-dashed border-pending px-2' : 'font-bold'} title={c.provisional ? 'provisional (not explicitly confirmed)' : 'accepted'}>
              {c.label}{c.provisional ? ' (provisional)' : ''}
            </span>
          </span>
        ))}
        {highlighted && s.highlight && (
          <span className="flex items-center gap-2"><span className="text-muted">›</span>
            <span className="rounded bg-pending px-2 font-bold text-black">pending: {s.highlight.label}?</span></span>
        )}
      </nav>

      <div className="flex flex-wrap items-end justify-between gap-2">
        <h1 data-testid="prompt" className="text-5xl font-black md:text-6xl">{s.screen.prompt}</h1>
        <p className="text-2xl font-semibold text-sky-300" aria-live="polite">{phaseText(s)}</p>
      </div>
      <Evidence s={s} />

      <div className={`grid flex-1 gap-4 ${gridClass(s.screen.n_choices)}`} data-testid="slots">
        {s.screen.slots.map((slot) => <SlotCard key={slot.key + slot.index} s={s} slot={slot} />)}
      </div>

      {highlighted && (
        <div className="flex flex-col gap-2" data-testid="confirm-bar">
          {s.highlight?.provisional_remaining_s != null && (
            <p className="text-center text-2xl text-pending">Selecting “{s.highlight.label}” in {s.highlight.provisional_remaining_s.toFixed(1)} s unless you look at NO</p>
          )}
          <div className="flex gap-4">
            <DecisionTarget s={s} tkey="yes" label="YES" tone="yes" />
            <DecisionTarget s={s} tkey="no" label="NO, not this" tone="no" />
          </div>
        </div>
      )}

      {review && (
        <Overlay tone="bg-ink/95" label="Review your message before sending" testId="review-overlay">
          <p className="text-2xl text-muted">Your message</p>
          <p data-testid="review-text" className={`max-w-4xl text-5xl font-black leading-tight ${s.review!.urgent ? 'text-red-300' : ''}`}>“{s.review!.text}”</p>
          {s.review!.provisional_steps > 0 && <p className="text-pending">{s.review!.provisional_steps} step(s) were accepted provisionally. Please check carefully.</p>}
          <div className="flex w-full max-w-4xl gap-4">
            <DecisionTarget s={s} tkey="yes" label="SEND" tone="yes" />
            <DecisionTarget s={s} tkey="no" label="CHANGE" tone="no" />
          </div>
          <p className="text-muted">The message is only sent after you confirm it.</p>
        </Overlay>
      )}
      {s.phase === 'MESSAGE_SENT' && s.feedback && (
        <Overlay tone="bg-green-950/95" label="Message sent" role="status" testId="sent-overlay">
          <p className="text-3xl">✓ Sent to your caregiver</p>
          <p className="max-w-4xl text-5xl font-black">“{s.feedback.label}”</p>
          <SentOrigin s={s} />
        </Overlay>
      )}
      {s.phase === 'COMM_UNAVAILABLE' && (
        <Overlay tone="bg-zinc-900/95" label="Communication paused" role="status">
          <p className="text-5xl font-black text-amber-300">Communication paused</p>
          <p data-testid="comm-reason" className="max-w-3xl text-2xl">{s.comm.reason}</p>
          <p className="max-w-3xl text-lg text-muted">No choices are being read and nothing will be selected while paused. This is not an alarm.</p>
          {s.comm.needs_caregiver && (
            <button type="button" onClick={() => command('resume').catch(() => {})} className="rounded-2xl bg-sky-600 px-6 py-3 text-2xl font-bold">
              Caregiver: resume
            </button>
          )}
        </Overlay>
      )}
      {s.phase === 'OFF' && (
        <Overlay tone="bg-zinc-900/90" label="No EEG source" role="status">
          <p className="text-4xl font-black">No EEG source</p>
          <p className="text-xl text-muted">Choose REAL, SIMULATED or REPLAY in the operator panel.</p>
        </Overlay>
      )}
      {s.calibration && <CalibrationOverlay s={s} />}
      {s.emergency && (
        <Overlay tone="bg-red-800/95" label="Help requested (demo control, not a certified alarm)" role="alertdialog">
          <p className="text-6xl font-black">HELP REQUESTED</p>
          <p className="text-2xl">Source: {s.emergency.source_label}</p>
          <p className="max-w-3xl text-lg">Demo control. This prototype and the headset are NOT a certified alarm system.</p>
          <button type="button" onClick={() => command('ack_emergency').catch(() => {})} className="rounded-2xl bg-white px-6 py-3 text-2xl font-black text-red-800">Caregiver: acknowledge</button>
        </Overlay>
      )}

      <footer className="flex flex-wrap items-center gap-3" aria-label="Caregiver controls">
        <button type="button" onClick={() => command('back').catch(() => {})} className="rounded-xl bg-panel2 px-5 py-3 text-xl font-bold">↩ Back</button>
        <button type="button" onClick={() => command('home').catch(() => {})} className="rounded-xl bg-panel2 px-5 py-3 text-xl font-bold">⌂ Home</button>
        <button type="button" disabled={!s.screen.can_undo} onClick={() => command('undo').catch(() => {})} className="rounded-xl bg-panel2 px-5 py-3 text-xl font-bold disabled:opacity-40">⟲ Undo</button>
        <button type="button" disabled={!(highlighted || review)} onClick={() => command('caregiver_reject').catch(() => {})} className="rounded-xl bg-panel2 px-5 py-3 text-xl font-bold disabled:opacity-40">✋ Patient said no</button>
        <button type="button" onClick={() => command('emergency', { source: 'demo_button' }).catch(() => {})} className="ml-auto rounded-xl border-4 border-red-300 bg-red-700 px-6 py-3 text-2xl font-black" aria-label="HELP NOW demo control">
          HELP NOW <span className="text-sm font-semibold">(demo)</span>
        </button>
      </footer>
    </section>
  )
}
