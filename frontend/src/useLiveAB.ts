import { useCallback, useEffect, useRef, useState } from 'react'
import { command, post } from './api'
import type { Arm } from './compare'
import type { State } from './types'

/**
 * Live A/B demonstration in SIMULATED mode: the same message, NeuroSteno first, then the fixed menu.
 *
 * What it controls (frontend only, no backend changes):
 *  - Each arm restarts the simulator with the SAME seed and default signal settings
 *    (POST /api/mode {mode:"simulated", seed}), which also clears any armed mistake / autopilot.
 *  - Random mistakes are set to 0 for both arms (the operator's rate is restored afterwards).
 *  - Same profile, target count (signal-adaptive count off), confirmation policy and intended message.
 *  - The conditions each arm actually ran under are recorded from the snapshot and compared.
 *
 * What it does NOT control (and the UI says so):
 *  - Session history: NeuroSteno ranks with seed history PLUS this machine's persisted simulated
 *    history; there is no reset command. The fixed menu ignores history, so running NeuroSteno
 *    first means nothing from the run itself can favour it.
 *  - Noise realisation: same seed, but sample timing differs between arms.
 *  - It is one run per arm in a fixed order: a demonstration, not a controlled experiment.
 *
 * Only ordinary contract commands are sent; the virtual patient still has to look at YES for
 * every step and SEND for the final message. Results are read from the server's task metrics.
 */

export const AB_SEED = 4242
export const ARM_TIMEOUT_S = 300

export interface ArmConditions {
  seed: number; routing: string; n: number; auto_adapt: boolean; policy: string
  noise_multiplier: number | null; ssvep_uv: number | null; mistake_rate: number | null
  bad_channels: string[]; drop_probability: number | null; disconnected: boolean | null
}
export interface ArmResult {
  arm: Arm
  status: 'waiting' | 'preparing' | 'running' | 'done' | 'timeout' | 'error'
  selections?: number; completion_s?: number; correct?: boolean | null
  rejected?: number; abstentions?: number; text?: string; error?: string
  conditions?: ArmConditions
}
export interface LiveRun {
  node_id: string; profile: string; n: number; arms: ArmResult[]; finished: boolean; origin: string
  seed: number; restoreMistakeRate: number
}

/** Fields that must match between arms (everything except the routing being compared). */
const MUST_MATCH: (keyof ArmConditions)[] = ['seed', 'n', 'auto_adapt', 'policy', 'noise_multiplier', 'ssvep_uv', 'mistake_rate', 'bad_channels', 'drop_probability', 'disconnected']

export function conditionsMatch(a?: ArmConditions, b?: ArmConditions): { ok: boolean; differences: string[] } {
  if (!a || !b) return { ok: false, differences: ['conditions not recorded for both arms'] }
  const differences = MUST_MATCH.filter((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k])).map((k) => String(k))
  return { ok: differences.length === 0, differences }
}

export function captureConditions(s: State, seed: number): ArmConditions {
  return {
    seed, routing: s.config.routing_mode, n: s.screen.n_choices, auto_adapt: s.config.auto_adapt, policy: s.config.effective_policy,
    noise_multiplier: s.sim?.noise_multiplier ?? null, ssvep_uv: s.sim?.ssvep_uv ?? null, mistake_rate: s.sim?.mistake_rate ?? null,
    bad_channels: [...(s.sim?.bad_channels ?? [])].sort(), drop_probability: s.sim?.drop_probability ?? null, disconnected: s.sim?.disconnected ?? null,
  }
}

/** Why a live A/B cannot start right now (null = OK). */
export function startBlocker(s: State | null): string | null {
  if (!s) return 'Not connected'
  if (s.mode.kind !== 'simulated') return 'Available in SIMULATED mode only. Real-EEG comparisons need a calibrated headset.'
  if (s.emergency) return 'Acknowledge the HELP NOW demo first.'
  if (s.calibration) return 'Wait for the calibration / signal check to finish.'
  if (s.recording) return 'Stop the recording first (the simulator is restarted for each arm).'
  return null
}

type Stage = 'idle' | 'setup' | 'plan' | 'running'

export type LiveAB = ReturnType<typeof useLiveAB>

export function useLiveAB(s: State | null) {
  const [run, setRun] = useState<LiveRun | null>(null)
  const stage = useRef<Stage>('idle')
  const busy = useRef(false)
  const armStart = useRef(0)
  const runRef = useRef<LiveRun | null>(null)
  useEffect(() => { runRef.current = run }, [run])

  const patchArm = (i: number, p: Partial<ArmResult>) =>
    setRun((r) => (r ? { ...r, arms: r.arms.map((a, j) => (j === i ? { ...a, ...p } : a)) } : r))
  const current = (r: LiveRun) => r.arms.findIndex((a) => a.status !== 'done' && a.status !== 'timeout' && a.status !== 'error')

  const finish = useCallback(async () => {
    stage.current = 'idle'
    const r = runRef.current
    setRun((x) => (x ? { ...x, finished: true } : x))
    await command('sim_autopilot', { node_id: null }).catch(() => {})
    // leave the engine on the system's normal routing and the operator's mistake rate
    await command('set_routing', { mode: 'adaptive' }).catch(() => {})
    if (r) await command('sim_mistake_rate', { rate: r.restoreMistakeRate }).catch(() => {})
  }, [])

  const start = useCallback(async (node_id: string, profile: string, n: number) => {
    if (startBlocker(s) || !s) return
    const r: LiveRun = {
      node_id, profile, n, finished: false, origin: s.mode.origin, seed: AB_SEED, restoreMistakeRate: s.sim?.mistake_rate ?? 0,
      arms: [{ arm: 'adaptive', status: 'waiting' }, { arm: 'static', status: 'waiting' }],
    }
    setRun(r)
    runRef.current = r
    stage.current = 'setup'
  }, [s])

  const stop = useCallback(async () => {
    const r = runRef.current
    if (!r) return
    const i = current(r)
    if (i >= 0) patchArm(i, { status: 'error', error: 'stopped by operator' })
    await finish()
  }, [finish])

  // Drive the run from the live snapshot (the server is authoritative).
  useEffect(() => {
    const r = runRef.current
    if (!s || !r || r.finished || busy.current || stage.current === 'idle') return
    const i = current(r)
    if (i < 0) { void finish(); return }
    const arm = r.arms[i]
    const now = s.server_time ?? 0
    const go = async (fn: () => Promise<void>) => {
      busy.current = true
      try { await fn() } catch (e) { patchArm(i, { status: 'error', error: (e as Error).message }); await finish() }
      busy.current = false
    }

    if (s.mode.kind !== 'simulated') {
      patchArm(i, { status: 'error', error: 'left SIMULATED mode' })
      void finish()
      return
    }
    if (arm.status === 'waiting' && stage.current === 'setup') {
      void go(async () => {
        patchArm(i, { status: 'preparing' })
        // identical starting point for every arm
        await post('/api/mode', { mode: 'simulated', seed: r.seed })
        await command('sim_mistake_rate', { rate: 0 })
        await command('set_profile', { id: r.profile })
        await command('set_auto_adapt', { on: false })
        await command('set_choices', { n: r.n })
        await command('set_intended', { node_id: r.node_id })
        await command('set_routing', { mode: arm.arm })
        await command('home')
        stage.current = 'plan'
      })
      return
    }
    if (stage.current === 'plan') {
      const ready = !!s.sim && !s.sim.disconnected && !s.routing_pending && s.config.routing_mode === arm.arm
        && s.routing?.mode === arm.arm && s.routing?.n === r.n && s.screen.at_home && s.screen.n_choices === r.n
      if (!ready) return
      void go(async () => {
        armStart.current = now
        patchArm(i, { conditions: captureConditions(s, r.seed) })
        await command('sim_autopilot', { node_id: r.node_id })
        patchArm(i, { status: 'running' })
        stage.current = 'running'
      })
      return
    }
    if (stage.current === 'running') {
      const sent = s.sent[0] as (State['sent'][number] & { node_id?: string }) | undefined
      if (sent && sent.time >= armStart.current && (sent.node_id == null || sent.node_id === r.node_id)) {
        const m = s.metrics.recent.find((x) => x.routing_mode === arm.arm && x.final_text === sent.text)
        patchArm(i, {
          status: 'done', text: sent.text, selections: m?.selections, completion_s: m?.completion_s,
          correct: m?.correct ?? null, rejected: m?.rejected_predictions, abstentions: m?.abstentions,
        })
        stage.current = 'setup'
        return
      }
      if (now - armStart.current > ARM_TIMEOUT_S) {
        void go(async () => {
          await command('sim_autopilot', { node_id: null })
          patchArm(i, { status: 'timeout', error: `not completed within ${ARM_TIMEOUT_S} s` })
          stage.current = 'setup'
        })
      }
    }
  }, [s, finish])

  return { run, start, stop, active: !!run && !run.finished }
}
