import { useEffect, useState } from 'react'
import { useEngineState } from './api'
import OperatorPanel from './components/OperatorPanel'
import PatientView from './components/PatientView'
import { flicker } from './flicker'

export default function App() {
  const { state, connected } = useEngineState()
  const patientOnly = typeof location !== 'undefined' && location.hash === '#patient'
  const [showPanel, setShowPanel] = useState(!patientOnly)

  useEffect(() => {
    try { flicker.setEnabled(localStorage.getItem('ns.flicker') === '1') } catch { /* storage unavailable */ }
  }, [])

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
    <main className="flex h-full flex-col gap-3 p-3 lg:flex-row">
      <div className="min-h-0 flex-1">
        <PatientView s={state} />
      </div>
      {showPanel && (
        <div className="h-[70vh] w-full shrink-0 lg:h-full lg:w-[440px]">
          <OperatorPanel s={state} />
        </div>
      )}
      <button type="button" aria-expanded={showPanel} onClick={() => setShowPanel((v) => !v)}
        className="fixed bottom-3 right-3 z-50 rounded-full bg-panel2 px-3 py-1 text-xs text-muted opacity-70 hover:opacity-100">
        {showPanel ? 'Hide' : 'Show'} operator panel
      </button>
      {!connected && <div role="status" className="fixed left-1/2 top-2 z-50 -translate-x-1/2 rounded bg-red-700 px-3 py-1 text-sm">Backend disconnected: reconnecting…</div>}
    </main>
  )
}
