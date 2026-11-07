import { useCallback, useEffect, useState } from 'react'
import { get } from '../api'
import type { EngineConnection } from '../api'
import { type Endpoints, type Level, overall, preflightChecks } from '../preflight'
import type { State } from '../types'

/**
 * Live go / no-go checklist for the judge demo. Every check reads current state or a backend
 * endpoint; anything the UI cannot verify is listed as a manual step, never ticked automatically.
 */


const MARK: Record<Level, { sym: string; cls: string; text: string }> = {
  ok: { sym: '', cls: 'text-green-300', text: 'ok' },
  warn: { sym: '!', cls: 'text-amber-300', text: 'warning' },
  fail: { sym: '', cls: 'text-red-300', text: 'problem' },
  manual: { sym: '', cls: 'text-sky-300', text: 'manual' },
}


function loadHist(): boolean { try { return localStorage.getItem('ns.historyMoved') === '1' } catch { return false } }

export default function Preflight({ s, link }: { s: State & { _missing?: string[] }; link: Pick<EngineConnection, 'connected' | 'stale' | 'reconnects' | 'badMessages'> }) {
  const [ep, setEp] = useState<Endpoints>({ compare: null, benchmark: null })
  const [hist, setHist] = useState(loadHist)
  const [open, setOpen] = useState(true)
  const probe = useCallback(() => {
    get<{ rows?: unknown[] }>('/api/routing/compare').then((d) => setEp((e) => ({ ...e, compare: Array.isArray(d?.rows) }))).catch(() => setEp((e) => ({ ...e, compare: false })))
    get<{ available?: boolean }>('/api/benchmark').then((d) => setEp((e) => ({ ...e, benchmark: d?.available === true }))).catch(() => setEp((e) => ({ ...e, benchmark: false })))
  }, [])
  useEffect(() => { probe() }, [probe])
  const checks = preflightChecks(s, link, ep, hist)
  const o = overall(checks)
  return (
    <section className="flex flex-col gap-2 rounded-xl border-2 border-line p-3" aria-labelledby="preflight" data-testid="preflight">
      <div className="flex items-center gap-2">
        <h3 id="preflight" className="text-base font-black">Preflight</h3>
        <span data-testid="preflight-overall" className={`rounded px-2 py-0.5 text-xs font-black ${o === 'ok' ? 'bg-green-800' : o === 'warn' ? 'bg-amber-700' : 'bg-red-700'}`}>
          {o === 'ok' ? 'GO' : o === 'warn' ? 'CHECK' : 'NO GO'}
        </span>
        <button type="button" onClick={() => { setEp({ compare: null, benchmark: null }); probe() }} className="ml-auto rounded bg-panel2 px-2 py-0.5 text-xs">Re-check</button>
        <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="rounded bg-panel2 px-2 py-0.5 text-xs">{open ? 'Hide' : 'Show'}</button>
      </div>
      {open && (
        <ul className="flex flex-col gap-1 text-xs">
          {checks.map((c) => (
            <li key={c.id} data-testid={`pf-${c.id}`} data-level={c.level} className="grid grid-cols-[1.2rem_1fr] gap-1">
              <span aria-label={MARK[c.level].text} className={`font-black ${MARK[c.level].cls}`}>{MARK[c.level].sym}</span>
              <span><b>{c.title}</b>: <span className="text-muted">{c.detail}</span>
                {c.id === 'history' && (
                  <label className="ml-1 inline-flex items-center gap-1 text-text">
                    <input type="checkbox" checked={hist} onChange={(e) => { setHist(e.target.checked); try { localStorage.setItem('ns.historyMoved', e.target.checked ? '1' : '0') } catch { /* ignore */ } }} />
                    done
                  </label>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-[11px] text-muted">Launch and recovery steps: OPERATOR_CHECKLIST.md in the repo.</p>
    </section>
  )
}
