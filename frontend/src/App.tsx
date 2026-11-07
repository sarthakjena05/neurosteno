import { useEffect, useState } from 'react'
import { useEngineState } from './api'
import ComparePage from './components/ComparePage'
import OperatorPanel from './components/OperatorPanel'
import PatientView from './components/PatientView'
import { flicker } from './flicker'
import { useLiveAB } from './useLiveAB'

export type View = 'operate' | 'compare'

function readHash(): string {
  return typeof location !== 'undefined' ? location.hash : ''
}

export default function App() {
  const { state, connected } = useEngineState()
  const [hash, setHash] = useState(readHash)
  const patientOnly = hash === '#patient'
  const view: View = hash === '#compare' ? 'compare' : 'operate'
  const [showPanel, setShowPanel] = useState(!patientOnly)
  // Lives here so a live A/B run survives switching views.
  const ab = useLiveAB(state)

  useEffect(() => {
    try { flicker.setEnabled(localStorage.getItem('ns.flicker') === '1') } catch { /* storage unavailable */ }
    const on = () => setHash(readHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  const setView = (v: View) => { location.hash = v === 'compare' ? '#compare' : '' ; setHash(readHash()) }

  if (!state) {
    return (
      <main className="flex h-full items-center justify-center p-8 text-center">
        <div>
          <h1 className="text-4xl font-black">NeuroSteno</h1>
          <p className="mt-2 text-xl text-muted">{connected ? 'Loading state…' : 'Connecting to backend on port 8000…'}</p>
        </div>
      </main>
    )
  }
  return (
    <main className={`flex h-full flex-col gap-3 p-3 lg:flex-row ${patientOnly ? "" : "pt-12"}`}>
      {!patientOnly && (
        <nav aria-label="View" className="fixed left-1/2 top-1 z-40 flex -translate-x-1/2 gap-1 rounded-full border border-line bg-panel p-1 text-sm shadow-lg">
          <button type="button" aria-pressed={view === 'operate'} onClick={() => setView('operate')}
            className={`rounded-full px-3 py-1 font-semibold ${view === 'operate' ? 'bg-sky-600' : 'text-muted hover:text-text'}`}>Patient + operator</button>
          <button type="button" aria-pressed={view === 'compare'} onClick={() => setView('compare')} data-testid="open-compare"
            className={`rounded-full px-3 py-1 font-semibold ${view === 'compare' ? 'bg-sky-600' : 'text-muted hover:text-text'}`}>Fixed menu vs NeuroSteno</button>
        </nav>
      )}
      {view === 'compare' ? (
        <>
          <div className="min-h-[60vh] lg:min-h-0 lg:w-[40%] lg:shrink-0">
            <PatientView s={state} />
          </div>
          <div className="min-h-0 flex-1">
            <ComparePage s={state} ab={ab} />
          </div>
        </>
      ) : (
        <>
          <div className="min-h-0 flex-1">
            <PatientView s={state} />
          </div>
          {showPanel && (
            <div className="h-[70vh] w-full shrink-0 lg:h-full lg:w-[440px]">
              <OperatorPanel s={state} onOpenCompare={() => setView('compare')} />
            </div>
          )}
          <button type="button" aria-expanded={showPanel} onClick={() => setShowPanel((v) => !v)}
            className="fixed bottom-3 right-3 z-50 rounded-full bg-panel2 px-3 py-1 text-xs text-muted opacity-70 hover:opacity-100">
            {showPanel ? 'Hide' : 'Show'} operator panel
          </button>
        </>
      )}
      {!connected && <div role="status" className="fixed left-1/2 top-12 z-50 -translate-x-1/2 rounded bg-red-700 px-3 py-1 text-sm">Backend disconnected: reconnecting…</div>}
    </main>
  )
}
