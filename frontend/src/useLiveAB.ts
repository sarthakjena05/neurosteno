import { useCallback, useEffect, useRef, useState } from 'react'
import { command } from './api'
import type { Arm } from './compare'
import type { State } from './types'

/**
 * Live A/B in SIMULATED mode: the same message, NeuroSteno first, then the fixed menu.
 *
 * Order matters: NeuroSteno's ranking uses the profile's confirmed-message history, so a
 * fixed-menu run first would hand NeuroSteno an unfair boost. The fixed menu ignores
 * history, so running it second cannot help it.
 *
 * Only ordinary contract commands are sent. The virtual patient still has to look at YES
 * for every step and SEND for the final message; results are read back from the server's
 * own task metrics, never computed here.
 */

export interface ArmResult {
  arm: Arm
  status: 'waiting' | 'preparing' | 'running' | 'done' | 'timeout' | 'error'
  selections?: number; completion_s?: number; correct?: boolean | null
  rejected?: number; abstentions?: number; text?: string; error?: string
}
export interface LiveRun { node_id: string; profile: string; n: number; arms: ArmResult[]; finished: boolean; origin: string }

export const ARM_TIMEOUT_S = 300

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
    setRun((r) => (r ? { ...r, finished: true } : r))
    // leave the engine on the system's normal routing, not the baseline
    await command('set_routing', { mode: 'adaptive' }).catch(() => {})
  }, [])

  const start = useCallback(async (node_id: string, profile: string, n: number) => {
    if (!s || s.mode.kind !== 'simulated') return
    const r: LiveRun = { node_id, profile, n, finished: false, origin: s.mode.origin,
      arms: [{ arm: 'adaptive', status: 'waiting' }, { arm: 'static', status: 'waiting' }] }
    setRun(r)
    runRef.current = r
    busy.current = true
    stage.current = 'setup'
    try {
      await command('sim_autopilot', { node_id: null })
      await command('set_profile', { id: profile })
      await command('set_auto_adapt', { on: false })
      await command('set_choices', { n })
      await command('set_intended', { node_id })
    } catch (e) {
      patchArm(0, { status: 'error', error: (e as Error).message })
      busy.current = false
      await finish()
      return
    }
    busy.current = false
  }, [s, finish])

  const stop = useCallback(async () => {
    const r = runRef.current
    if (!r) return
    const i = current(r)
    if (i >= 0) patchArm(i, { status: 'error', error: 'stopped by operator' })
    await command('sim_autopilot', { node_id: null }).catch(() => {})
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
        await command('set_routing', { mode: arm.arm })
        await command('home')
        stage.current = 'plan'
      })
      return
    }
    if (stage.current === 'plan') {
      const ready = !s.routing_pending && s.config.routing_mode === arm.arm && s.routing?.mode === arm.arm
        && s.routing?.n === r.n && s.screen.at_home && s.screen.n_choices === r.n
      if (!ready) return
      void go(async () => {
        armStart.current = now
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
