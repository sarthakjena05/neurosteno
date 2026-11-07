import { useEffect, useRef, useState } from 'react'
import type { State } from './types'

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

/** Live engine state over WebSocket, with automatic reconnect. */
export function useEngineState(): { state: State | null; connected: boolean } {
  const [state, setState] = useState<State | null>(null)
  const [connected, setConnected] = useState(false)
  const retry = useRef(0)

  useEffect(() => {
    let ws: WebSocket | null = null
    let closed = false
    let timer: number | undefined
    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws`)
      ws.onopen = () => { setConnected(true); retry.current = 0 }
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'state') setState(msg.state)
      }
      ws.onclose = () => {
        setConnected(false)
        if (!closed) timer = window.setTimeout(connect, Math.min(5000, 500 * 2 ** retry.current++))
      }
    }
    connect()
    return () => { closed = true; window.clearTimeout(timer); ws?.close() }
  }, [])
  return { state, connected }
}
