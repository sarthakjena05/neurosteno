import { useEffect, useState } from 'react'
import { command } from '../api'
import { MOCK_LABEL, MOCKS, type MockKey } from '../mockMultimodal'
import {
  answerMethods, calibPhaseSeconds, GESTURE_CONTROLS_HOLD, GESTURE_FINAL_CONFIRM_ENABLED, type GestureCalibSession, type InputMode, type InputsVM, METHOD_INFO, type MethodKey, MODE_INFO, MULTIMODAL_CONTRACT,
  readInputs, STATUS_INFO,
} from '../multimodal'
import { speakCue } from '../speech'
import type { State } from '../types'

/**
 * Multimodal input UI. Everything here renders from an InputsVM:
 *  - provenance 'backend'          → confirmed backend state (contract v2.0)
 *  - provenance 'eeg_only_default' → backend publishes no gesture input: EEG-only, gestures "not available"
 *  - provenance 'mock'             → operator preview only, always under a MOCK banner
 * Nothing here selects or confirms anything; it only shows server state and sends commands.
 */

export function MockBanner() {
  return (
    <p data-testid="mock-banner" className="rounded-lg border-2 border-dashed border-fuchsia-400 bg-fuchsia-950 px-3 py-1.5 text-sm font-black tracking-wide text-fuchsia-100">
      MOCK · UI PREVIEW · NOT BACKEND STATE. Illustrative values, not measurements.
    </p>
  )
}

const GESTURE_KEYS: MethodKey[] = ['nod_yes', 'shake_no', 'tilt_nav']
const ALL_KEYS: MethodKey[] = ['eeg_ssvep', ...GESTURE_KEYS]

export function MethodTable({ vm }: { vm: InputsVM }) {
  return (
    <ul className="flex flex-col gap-1" data-testid="method-table">
      {ALL_KEYS.map((k) => {
        const m = vm.methods[k]
        const st = STATUS_INFO[m.status]
        const c = m.calibration
        return (
          <li key={k} data-testid={`method-${k}`} data-status={m.status} data-active={m.active ? 'true' : 'false'}
            className="grid grid-cols-[1.5rem_1fr_auto] items-start gap-2 rounded-lg bg-panel2 px-2 py-1.5 text-sm">
            <span aria-hidden className="text-lg leading-none">{METHOD_INFO[k].icon}</span>
            <span>
              <b>{METHOD_INFO[k].label}</b> <span className="text-muted">→ {METHOD_INFO[k].purpose}</span>
              {c && <span className="block text-xs">Calibration ({c.origin}): {c.detected}/{c.attempts} detected · {c.rest_false_triggers} false triggers in {c.rest_s} s rest{c.median_latency_s != null ? ` · median ${c.median_latency_s} s` : ''} · {c.verdict}</span>}
              {m.reason && <span className="block text-xs text-muted">{m.reason}</span>}
            </span>
            <span className={`whitespace-nowrap rounded px-2 py-0.5 text-xs font-bold ${st.cls}`}>
              <span aria-hidden>{st.mark} </span>{m.active && k !== 'eeg_ssvep' ? 'ACTIVE' : st.text}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

export function ModeCards({ vm, onSet }: { vm: InputsVM; onSet?: (m: InputMode) => void }) {
  const modes: InputMode[] = ['eeg_only', 'hybrid', 'motion_assisted', 'auto']
  return (
    <div className="flex flex-col gap-1">
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4" role="radiogroup" aria-label="Input mode" data-testid="mode-cards">
        {modes.map((m) => {
          const on = vm.requested === m || (vm.requested !== 'auto' && vm.mode === m)
          const avail = m === 'eeg_only' || m === 'auto' || vm.modesAvailable.includes(m)
          // Under the hold only the safe fallback (EEG-only) can be selected from the UI.
          const blockedByHold = GESTURE_CONTROLS_HOLD.held && m !== 'eeg_only'
          return (
            <button key={m} type="button" role="radio" aria-checked={on} disabled={!avail || !onSet || on || blockedByHold} onClick={() => onSet?.(m)}
              data-testid={`mode-${m}`}
              className={`flex flex-col gap-1 rounded-xl border-2 p-2 text-left text-xs disabled:cursor-default ${on ? 'border-sky-400 bg-sky-950' : avail ? 'border-line bg-panel2 hover:border-sky-600' : 'border-line bg-panel opacity-60'}`}>
              <span className="text-sm font-black">{on ? '● ' : ''}{MODE_INFO[m].label}</span>
              <span className="text-muted">{MODE_INFO[m].text}</span>
              {!avail && <span className="font-semibold text-amber-300">Not available: needs a passing movement calibration</span>}
              {avail && blockedByHold && !on && <span className="font-semibold text-amber-300">Held (see below)</span>}
            </button>
          )
        })}
      </div>
      {vm.requested !== vm.mode && <p className="text-xs" data-testid="mode-effective">Requested <b>{MODE_INFO[vm.requested].label}</b> → using <b>{MODE_INFO[vm.mode].label}</b></p>}
    </div>
  )
}

export function ImuStatus({ vm }: { vm: InputsVM }) {
  const i = vm.imu
  if (!i) {
    return (
      <p className="text-xs text-muted" data-testid="imu-status">
        {vm.sensorInStream
          ? 'Motion sensor channels are present in the headset data stream, but no gesture detection is published. Not evaluated; this says nothing about whether the patient can move.'
          : 'No motion sensor data in the current stream.'}
      </p>
    )
  }
  return (
    <p className="flex flex-wrap items-center gap-2 text-xs" data-testid="imu-status">
      <span className={`rounded px-1.5 py-0.5 font-bold ${i.streaming ? 'bg-green-900' : 'bg-zinc-700'}`}>Motion sensor {i.streaming ? 'streaming' : 'not streaming'} · {i.origin}{i.claim && i.claim !== i.origin ? ` · ${i.claim}` : ''}</span>
      <span>motion {i.motion_level == null ? '–' : `${Math.round(i.motion_level * 100)}%`}</span>
      {i.moving && <span className="rounded bg-amber-900 px-1.5">head moving</span>}
      {i.eeg_contaminated && <span className="rounded bg-red-900 px-1.5 font-bold">EEG disturbed by movement: listening restarts</span>}
      {i.reason && <span className="text-muted">{i.reason}</span>}
      <span className="w-full text-muted">Presence of a sensor says nothing about whether the patient can move; only a passing movement check does.</span>
    </p>
  )
}

export function PolicyCard({ vm }: { vm: InputsVM }) {
  const p = vm.policy
  if (!p) {
    return (
      <p className="text-sm" data-testid="policy-card">
        <b>Selected input: 👁 EEG target selection</b>{' '}
        <span className="text-muted">{vm.provenance === 'eeg_only_default' ? '(EEG-only: no optimized input policy is published by the backend yet)' : '(no policy decision for this moment)'}</span>
      </p>
    )
  }
  return (
    <div className="flex flex-col gap-1 text-sm" data-testid="policy-card">
      <p>For this <b>{p.action}</b> step the policy selected <b data-testid="policy-selected">{METHOD_INFO[p.selected].icon} {METHOD_INFO[p.selected].label} ({METHOD_INFO[p.selected].purpose})</b>
        <span className="ml-1 rounded border border-dashed border-zinc-300 px-1 text-[11px]">{p.source === 'calibrated' ? 'from calibration' : 'ASSUMPTION'}</span></p>
      <table className="text-xs tabular-nums">
        <thead><tr className="text-left"><th>method</th><th>allowed</th><th>expected latency</th><th>confidence</th><th>why</th></tr></thead>
        <tbody>{p.options.map((o) => (
          <tr key={o.method} className={`border-t border-line ${o.method === p.selected ? 'font-bold' : ''}`}>
            <td>{METHOD_INFO[o.method].label}</td>
            <td>{p.action === 'review' && !GESTURE_FINAL_CONFIRM_ENABLED && o.method !== 'eeg_ssvep' && o.method !== 'eeg_fallback'
              ? <span data-testid="final-gesture-blocked" title="Final SEND stays a separate EEG confirmation until real-world false-activation testing supports gestures">✗ not for final SEND</span>
              : o.allowed ? '✓' : '✗'}</td>
            <td>{o.expected_latency_s != null ? `${o.expected_latency_s} s` : '-'}</td><td>{o.confidence != null ? `${Math.round(o.confidence * 100)}%` : '-'}</td><td>{o.reason}</td>
          </tr>
        ))}</tbody>
      </table>
      {p.constraints.length > 0 && <ul className="list-disc pl-4 text-xs">{p.constraints.map((c) => <li key={c}>🔒 {c}</li>)}</ul>}
    </div>
  )
}

/** Patient-facing: how the patient can answer right now, gesture feedback, visible abstain. */
export function InputStrip({ vm, action }: { vm: InputsVM; action: 'choose' | 'confirm' | 'review' | null }) {
  const methods = answerMethods(vm, action)
  const gestures = methods.filter((m) => m !== 'eeg_ssvep')
  const anyGestureActive = (['nod_yes', 'shake_no', 'tilt_nav'] as MethodKey[]).some((k) => vm.methods[k].active)
  const g = vm.lastGesture
  const showAnswer = (action === 'confirm' && gestures.length > 0) || (action === 'review' && anyGestureActive)
  // Nothing gesture-related on the patient screen unless the backend says gestures are in use.
  if (!vm.abstain && !vm.motionRestart && !showAnswer && !(g && anyGestureActive)) return null
  return (
    <div className="flex flex-col gap-2" data-testid="input-strip">
      {vm.abstain && (
        <div role="status" data-testid="abstain-banner" className="rounded-2xl border-4 border-amber-400 bg-amber-950 px-4 py-2 text-xl font-bold">
          ⏸ {vm.abstain.type === 'conflict' ? 'Signals disagreed' : 'Head movement disturbed the brain signal'}: nothing was selected.
          <span className="block text-base font-normal">{vm.abstain.text} Please try again.</span>
        </div>
      )}
      {vm.motionRestart && !vm.abstain && (
        <p role="status" data-testid="motion-restart" className="rounded-xl bg-amber-950 px-3 py-1 text-lg">Head movement disturbed the brain signal: listening again. Nothing was selected.</p>
      )}
      {showAnswer && (
        <p className="flex flex-wrap items-center gap-2 text-lg" data-testid="answer-with">
          <span className="text-muted">Answer with:</span>
          <span className="rounded-lg bg-panel2 px-2 py-0.5 font-bold">👁 look at YES / NO</span>
          {gestures.includes('nod_yes') && <span className="rounded-lg bg-green-900 px-2 py-0.5 font-bold">↕ nod = YES</span>}
          {gestures.includes('shake_no') && <span className="rounded-lg bg-red-900 px-2 py-0.5 font-bold">↔ shake = NO</span>}
          {action === 'review' && <span className="text-sm text-muted" data-testid="final-eeg-only">Final message: look at SEND. Head gestures are not used to send.</span>}
        </p>
      )}
      {g && anyGestureActive && (
        <p className="text-base" data-testid="last-gesture">
          {g.accepted
            ? <>✓ {g.gesture} detected → <b>{g.used_for?.toUpperCase() ?? 'noted'}</b> ({Math.round(g.confidence * 100)}%)</>
            : <>· {g.gesture} seen but <b>not used</b>: {g.reason}</>}
        </p>
      )}
    </div>
  )
}

const CAL_TEXT: Record<string, { cue: string; speech: string }> = {
  neutral: { cue: 'Keep your head still', speech: 'Keep your head still and relax.' },
  natural: { cue: 'Move naturally (no gestures)', speech: 'Move naturally. No gestures.' },
  nod: { cue: 'Nod your head YES, gently', speech: 'Gently nod your head, yes.' },
  shake: { cue: 'Shake your head NO, gently', speech: 'Gently shake your head, no.' },
  tilt: { cue: 'Tilt your head to the side and hold', speech: 'Tilt your head to the side and hold.' },
}

/** Guided gesture calibration (patient-facing). Spoken cues only for live backend sessions. */
export function GestureCalibrationView({ cal, speak = true }: { cal: GestureCalibSession; speak?: boolean }) {
  const key = cal.sub === 'cue' ? (cal.gesture ?? cal.step_label ?? '') : (cal.step_label ?? (cal.sub === 'rest' ? 'neutral' : ''))
  const t = CAL_TEXT[key] ?? { cue: `Make the "${key}" movement`, speech: `Make the ${key} movement.` }
  useEffect(() => {
    if (!speak || cal.sub === 'computing') return
    speakCue(t.speech)
  }, [cal.step, cal.sub, speak, t.speech])
  const total = calibPhaseSeconds(cal)
  const left = total == null ? null : Math.max(0, Math.ceil(total - cal.elapsed))
  const lt = cal.last_trial
  return (
    <div data-testid="gesture-cal" data-sub={cal.sub} className="flex flex-col items-center gap-4 text-center">
      <p className="text-lg text-muted">Movement check · step {cal.step} of {cal.steps}{cal.trials ? ` · cue ${Math.min(cal.trial + (cal.sub === 'cue' ? 1 : 0), cal.trials)}/${cal.trials}` : ''}</p>
      {cal.sub === 'computing'
        ? <p className="text-4xl font-black">Checking…</p>
        : <p className={`text-5xl font-black ${cal.sub === 'cue' ? 'text-sky-200' : ''}`}>{t.cue}</p>}
      {cal.sub === 'feedback' && lt && (
        <p className={`text-4xl font-black ${lt.detected ? 'text-green-300' : 'text-zinc-300'}`}>
          {lt.detected ? `✓ ${lt.seen ?? cal.gesture} detected` : 'Not detected. That’s OK.'}
        </p>
      )}
      {left != null && cal.sub !== 'computing' && <p className="text-xl text-muted">{left} s</p>}
      <p className="max-w-xl text-sm text-muted">Only move if it is comfortable. If moving is hard, that is fine: NeuroSteno works with brain signals alone. A caregiver can stop this check or turn gestures off at any time.</p>
    </div>
  )
}

function GestureControls({ s, vm }: { s: State; vm: InputsVM }) {
  const [err, setErr] = useState<string | null>(null)
  const live = vm.provenance === 'backend'
  const held = GESTURE_CONTROLS_HOLD.held
  // Commands are only ever sent for confirmed backend state (guard in the handler, not just disabled styling).
  const send = (fn: () => Promise<unknown>) => { if (!live) return; setErr(null); fn().catch((e: Error) => setErr(e.message)) }
  const anyEnabled = (['nod_yes', 'shake_no', 'tilt_nav'] as MethodKey[]).some((k) => vm.methods[k].enabled)
  const anyCalibrated = (['nod_yes', 'shake_no', 'tilt_nav'] as MethodKey[]).some((k) => vm.methods[k].status === 'calibrated')
  const busy = !!s.calibration || !!vm.gestureCal
  return (
    <div className="flex flex-col gap-2" data-testid="gesture-controls">
      {err && <p role="alert" className="rounded bg-red-900 p-2 text-sm">{err}</p>}
      {!live && <p className="text-xs text-amber-300">The backend does not publish gesture input; nothing is sent.</p>}
      {held && (
        <div role="note" data-testid="gesture-hold" className="rounded-lg border-2 border-amber-500 bg-amber-950 p-2 text-xs">
          <b>Gesture controls are held for the demo.</b> Status above is live; starting gesture activity is disabled:
          <ul className="list-disc pl-4">{GESTURE_CONTROLS_HOLD.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-xs text-muted">Guided movement check: neutral 20 s → shuffled nod / shake / tilt cues → natural movement 30 s</span>
        <button type="button" disabled={!live || held || busy} onClick={() => { if (!held) send(() => command('start_gesture_calibration', { cues_per_gesture: 6 })) }}
          className="rounded-lg bg-sky-700 px-3 py-1 font-semibold disabled:opacity-40">Start movement check</button>
        {vm.gestureCal && live && <button type="button" onClick={() => send(() => command('cancel_calibration'))} className="rounded-lg bg-red-700 px-3 py-1 font-semibold">Stop check</button>}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-xs text-muted">One switch for all usable gestures (backend v1.4):</span>
        {anyEnabled
          // switching OFF is always allowed
          ? <button type="button" disabled={!live} onClick={() => send(() => command('set_gesture_enabled', { on: false }))} className="rounded-lg bg-amber-700 px-3 py-1 font-semibold">Gestures ON: turn OFF</button>
          : <button type="button" disabled={!live || held || !anyCalibrated} title={!anyCalibrated ? 'Needs a passing movement check on this data origin' : ''}
              onClick={() => { if (!held) send(() => command('set_gesture_enabled', { on: true })) }} className="rounded-lg bg-panel2 px-3 py-1 disabled:opacity-40">Gestures OFF: turn on</button>}
      </div>
      {live && s.sim && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-muted">Simulated patient movement:</span>
          {(['no_movement', 'reliable_movement', 'unreliable_movement', 'high_motion'] as const).map((p) => (
            <button key={p} type="button" disabled={held} onClick={() => { if (!held) send(() => command('sim_capability', { profile: p })) }} className="rounded bg-panel2 px-2 py-0.5 disabled:opacity-40">{p.replace('_', ' ')}</button>
          ))}
        </div>
      )}
    </div>
  )
}

function Box({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-line p-3">
      <h3 className="text-xs font-bold uppercase tracking-wider text-muted">{title}</h3>
      {children}
    </div>
  )
}

/** Operator "Inputs" tab. */
export default function InputsTab({ s, contract = MULTIMODAL_CONTRACT }: { s: State; contract?: 'proposed' | 'confirmed' }) {
  const vm = readInputs(s, contract)
  const [err, setErr] = useState<string | null>(null)
  const [mock, setMock] = useState<MockKey | null>(null)
  const mvm = mock ? MOCKS[mock] : null
  // Under the hold only EEG-only (the safe fallback) can be selected; ModeCards disables the rest.
  const onSet = vm.provenance === 'backend' ? (m: InputMode) => { if (GESTURE_CONTROLS_HOLD.held && m !== 'eeg_only') return; setErr(null); command('set_input_mode', { mode: m }).catch((e: Error) => setErr(e.message)) } : undefined
  return (
    <div className="flex flex-col gap-3" data-testid="inputs-tab">
      <p className="rounded-lg bg-panel2 p-2 text-xs">
        <b>Movement is never assumed.</b> NeuroSteno starts EEG-only for every patient. Head gestures can only be used after a passing
        movement check on this data origin <b>and</b> a caregiver turning them on. Conflicting or movement-disturbed signals abstain.
        <span className="block text-muted">Input contract: {contract === 'confirmed' ? 'v1.4 confirmed by the backend (input/policy shapes)' : 'proposed (not used)'}. No real-patient motion data has been validated yet.</span>
      </p>
      {err && <p role="alert" className="rounded bg-red-900 p-2 text-sm">{err}</p>}
      <Box title="Input mode (server state)">
        <ModeCards vm={vm} onSet={onSet} />
        <p className="text-xs text-muted" data-testid="mode-reason">{vm.modeReason}</p>
      </Box>
      <Box title="Input methods">
        <MethodTable vm={vm} />
        <ImuStatus vm={vm} />
      </Box>
      <Box title="Movement calibration and gesture switches">
        <GestureControls s={s} vm={vm} />
      </Box>
      <Box title="Why this input method (policy)">
        <PolicyCard vm={vm} />
      </Box>
      <Box title="UI preview with mock states">
        <p className="text-xs text-muted">For reviewing the design before the backend is ready. Never shown on the patient screen.</p>
        <div className="flex flex-wrap gap-1">
          <button type="button" onClick={() => setMock(null)} className={`rounded px-2 py-0.5 text-xs ${mock === null ? 'bg-sky-600' : 'bg-panel2'}`}>off</button>
          {(Object.keys(MOCKS) as MockKey[]).map((k) => (
            <button key={k} type="button" onClick={() => setMock(k)} className={`rounded px-2 py-0.5 text-xs ${mock === k ? 'bg-fuchsia-700' : 'bg-panel2'}`}>{MOCK_LABEL[k]}</button>
          ))}
        </div>
        {mvm && (
          <div className="flex flex-col gap-2 rounded-xl border-2 border-dashed border-fuchsia-500 p-2" data-testid="mock-preview">
            <MockBanner />
            <ModeCards vm={mvm} />
            <MethodTable vm={mvm} />
            <ImuStatus vm={mvm} />
            <PolicyCard vm={mvm} />
            <div className="rounded-xl bg-ink p-2"><InputStrip vm={mvm} action="confirm" /></div>
            {mvm.gestureCal && <div className="rounded-xl bg-ink p-3"><GestureCalibrationView cal={mvm.gestureCal} speak={false} /></div>}
          </div>
        )}
      </Box>
    </div>
  )
}
