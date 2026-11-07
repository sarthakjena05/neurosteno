import { useEffect, useRef, useState } from 'react'
import { type NormalizedState, normalizeState } from './normalize'

export async function command(action: string, params: Record<string, unknown> = {}): Promise<unknown> {
  const r = await fetch('/api/command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, ...params }),
  })
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body))
  return body
}

export async function post(path: string, body: unknown): Promise<any> {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) {
    const d = data.detail
    throw new Error(typeof d === 'string' ? d : d?.error ?? JSON.stringify(d ?? data))
  }
  return data
}

export async function get<T = any>(path: string): Promise<T> {
  const r = await fetch(path)
  if (!r.ok) throw new Error(`${r.status} ${path}`)
  return r.json()
}

/** The server pushes ~10 Hz; no state for this long means the view is stale. */
export const STALE_AFTER_MS = 2500
/** An open socket that stays silent this long is closed and reconnected. */
export const FORCE_RECONNECT_MS = 6000

export interface EngineConnection {
  state: NormalizedState | null
  /** socket open */
  connected: boolean
  /** no fresh state recently (or never): the screen must not look live */
  stale: boolean
  reconnects: number
  badMessages: number
}

/** Live engine state over WebSocket: safe parsing, normalisation, staleness, automatic reconnect. */
export function useEngineState(): EngineConnection {
  const [state, setState] = useState<NormalizedState | null>(null)
  const [connected, setConnected] = useState(false)
  const [stale, setStale] = useState(true)
  const [reconnects, setReconnects] = useState(0)
  const [badMessages, setBad] = useState(0)
  const retry = useRef(0)
  const lastMsg = useRef(0)

  useEffect(() => {
    let ws: WebSocket | null = null
    let closed = false
    let timer: number | undefined
    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      try {
        ws = new WebSocket(`${proto}://${location.host}/ws`)
      } catch {
        timer = window.setTimeout(connect, Math.min(5000, 500 * 2 ** retry.current++))
        return
      }
      ws.onopen = () => { setConnected(true); retry.current = 0 }
      ws.onmessage = (ev) => {
        let msg: { type?: string; state?: unknown }
        try { msg = JSON.parse(String(ev.data)) } catch { setBad((n) => n + 1); return }
        if (msg?.type !== 'state') return
        lastMsg.current = Date.now()
        setStale(false)
        setState(normalizeState(msg.state))
      }
      ws.onerror = () => { /* onclose follows and schedules the reconnect */ }
      ws.onclose = () => {
        setConnected(false)
        setStale(true)
        if (!closed) {
          setReconnects((n) => n + 1)
          timer = window.setTimeout(connect, Math.min(5000, 500 * 2 ** retry.current++))
        }
      }
    }
    connect()
    const watchdog = window.setInterval(() => {
      const age = Date.now() - lastMsg.current
      if (lastMsg.current && age > STALE_AFTER_MS) setStale(true)
      if (lastMsg.current && age > FORCE_RECONNECT_MS && ws?.readyState === WebSocket.OPEN) ws.close()
    }, 500)
    return () => { closed = true; window.clearTimeout(timer); window.clearInterval(watchdog); ws?.close() }
  }, [])
  return { state, connected, stale, reconnects, badMessages }
}
