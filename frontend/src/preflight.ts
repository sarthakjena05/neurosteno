import type { EngineConnection } from './api'
import { GESTURE_CONTROLS_HOLD, readInputs } from './multimodal'
import type { State } from './types'

/** Live go / no-go checks for the judge demo (pure). Anything the UI cannot verify is 'manual', never ticked automatically. */
export type Level = 'ok' | 'warn' | 'fail' | 'manual'
export interface Check { id: string; level: Level; title: string; detail: string }

export interface Endpoints { compare: boolean | null; benchmark: boolean | null }

export function preflightChecks(s: State & { _missing?: string[] }, link: Pick<EngineConnection, 'connected' | 'stale' | 'reconnects' | 'badMessages'>, ep: Endpoints, historyMoved: boolean): Check[] {
  const c: Check[] = []
  const live = link.connected && !link.stale
  c.push({ id: 'link', level: live ? 'ok' : 'fail', title: 'Backend live',
    detail: live ? `fresh state over WebSocket${link.reconnects ? ` (${link.reconnects} reconnects so far)` : ''}` : 'no fresh state: start or restart start-backend.ps1, then wait for reconnect' })
  const miss = s._missing ?? []
  c.push({ id: 'fields', level: miss.length ? 'warn' : 'ok', title: 'Snapshot complete',
    detail: miss.length ? `missing from the backend: ${miss.join(', ')} (UI shows safe empty defaults)` : 'all expected fields present' })
  if (link.badMessages) c.push({ id: 'parse', level: 'warn', title: 'Unreadable messages', detail: `${link.badMessages} WebSocket message(s) could not be parsed and were ignored` })
  const k = s.mode.kind
  c.push({ id: 'mode', level: k === 'simulated' || k === 'assisted' ? 'ok' : k === 'none' ? 'fail' : 'warn', title: 'EEG source labelled',
    detail: k === 'simulated' ? 'SIMULATED EEG: striped purple frame + badge visible' : k === 'none' ? 'no source: Demo step "Switch to Simulated"' : `${s.mode.label}: make sure the audience sees this is ${k === 'real' ? 'REAL (calibrated?)' : 'a REPLAY'}` })
  const sig = s.phase !== 'COMM_UNAVAILABLE' && s.quality.signal_ok !== false
  c.push({ id: 'signal', level: s.phase === 'OFF' ? 'fail' : sig ? 'ok' : 'warn', title: 'Signal / communication',
    detail: sig ? 'communication available' : `paused: ${s.comm.reason || s.quality.problem || 'signal check'} (Simulation tab: restore Bluetooth / electrodes)` })
  c.push({ id: 'routing', level: s.routing ? 'ok' : 'warn', title: 'Routing plan', detail: s.routing ? `${s.routing.mode}, ${s.routing.n} targets` : 'not computed yet: wait a second or reselect the profile' })
  c.push({ id: 'compare', level: ep.compare === null ? 'warn' : ep.compare ? 'ok' : 'fail', title: 'Comparison data (/api/routing/compare)',
    detail: ep.compare === null ? 'checking…' : ep.compare ? 'reachable' : 'not reachable: comparison view will show an error' })
  c.push({ id: 'bench', level: ep.benchmark === null ? 'warn' : ep.benchmark ? 'ok' : 'warn', title: 'Simulated benchmark (/api/benchmark)',
    detail: ep.benchmark === null ? 'checking…' : ep.benchmark ? 'published: benchmark tables will show' : 'not published: tables show NOT MEASURED (honest, but less to show)' })
  const busy = [s.emergency && 'HELP NOW active', s.calibration && 'calibration running', s.recording && 'recording', s.sim?.autopilot && 'autopilot running', s.sim?.disconnected && 'simulated dropout ON', (s.sim?.bad_channels?.length ?? 0) > 0 && 'simulated bad electrodes'].filter(Boolean)
  c.push({ id: 'clean', level: busy.length ? 'warn' : 'ok', title: 'Clean starting state', detail: busy.length ? `${busy.join(', ')}: press "Reset demo"` : 'nothing left over from a rehearsal' })
  const inp = readInputs(s)
  const gOn = (['nod_yes', 'shake_no', 'tilt_nav'] as const).filter((k) => inp.methods[k].enabled)
  const gesturesOff = gOn.length === 0 && inp.mode === 'eeg_only'
  c.push({ id: 'gestures', level: gesturesOff && GESTURE_CONTROLS_HOLD.held ? 'ok' : 'warn', title: 'Head gestures off for the demo',
    detail: gesturesOff
      ? `EEG-only${GESTURE_CONTROLS_HOLD.held ? '; gesture controls held in the UI' : ''}; final SEND is EEG-only`
      : `input mode ${inp.mode}, enabled: ${gOn.join(', ') || 'none'}: Inputs tab → turn gestures OFF / EEG-only` })
  c.push({ id: 'history', level: historyMoved ? 'ok' : 'manual', title: 'Saved simulated history moved aside (manual)',
    detail: 'The UI cannot see or reset backend/data/runtime/history_*_simulated.json. Move it aside with the backend stopped so the live A/B is not skewed by rehearsals.' })
  return c
}

export function overall(checks: Check[]): Level {
  if (checks.some((c) => c.level === 'fail')) return 'fail'
  if (checks.some((c) => c.level === 'warn' || c.level === 'manual')) return 'warn'
  return 'ok'
}
