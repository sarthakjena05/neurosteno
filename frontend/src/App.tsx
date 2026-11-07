import { Component, type ReactNode, useEffect, useState } from 'react'
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
function loadFlag(key: string): boolean {
  try { return localStorage.getItem(key) === '1' } catch { return false }
}
function saveFlag(key: string, on: boolean) {
  try { localStorage.setItem(key, on ? '1' : '0') } catch { /* storage unavailable */ }
}

/** A render error in one area must never blank the whole screen. */
export class ErrorBoundary extends Component<{ area: string; children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null }
  static getDerivedStateFromError(e: unknown) { return { error: e instanceof Error ? e.message : String(e) } }
  render() {
    if (!this.state.error) return this.props.children
    return (
      <div role="alert" data-testid={`error-${this.props.area}`} className="flex h-full flex-col items-center justify-center gap-3 rounded-3xl border-4 border-amber-400 bg-zinc-900 p-6 text-center">
        <p className="text-3xl font-black text-amber-300">{this.props.area}: display error</p>
        <p className="max-w-xl text-lg">Nothing is selected from this screen while it is broken. The backend keeps its own state.</p>
        <p className="max-w-xl text-xs text-muted">{this.state.error}</p>
        <button type="button" onClick={() => this.setState({ error: null })} className="rounded-xl bg-sky-600 px-4 py-2 font-bold">Try again</button>
      </div>
    )
  }
}

export default function App() {
  const link = useEngineState()
  const { state, connected, stale } = link
  const [hash, setHash] = useState(readHash)
  const patientOnly = hash === '#patient'
  const view: View = hash === '#compare' ? 'compare' : 'operate'
  const [showPanel, setShowPanel] = useState(true)
  const [hc, setHc] = useState(() => loadFlag('ns.hc'))
  // Lives here so a live A/B run survives switching views.
  const ab = useLiveAB(state)
  const live = connected && !stale

  useEffect(() => {
    flicker.setEnabled(loadFlag('ns.flicker'))
    const on = () => setHash(readHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  // No stimulation while the screen is not live (nothing would be decoded from it).
  useEffect(() => { flicker.setSuspended(!live) }, [live])

  const setView = (v: View) => { location.hash = v === 'compare' ? '#compare' : ''; setHash(readHash()) }
  const toggleHc = () => setHc((v) => { saveFlag('ns.hc', !v); return !v })
  const enterPatientMode = () => {
    location.hash = '#patient'
    setHash(readHash())
    document.documentElement.requestFullscreen?.().catch(() => { /* fullscreen refused: patient-only layout still applies */ })
  }
  const exitPatientMode = () => {
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {})
    location.hash = ''
    setHash(readHash())
  }

  if (!state) {
    return (
      <main className={`flex h-full items-center justify-center p-8 text-center ${hc ? 'hc' : ''}`}>
        <div role="status">
          <h1 className="text-4xl font-black">NeuroSteno</h1>
          <p className="mt-2 text-xl text-muted">{connected ? 'Loading state…' : 'Connecting to the backend on port 8000…'}</p>
          {link.reconnects > 2 && <p className="mt-2 text-sm text-amber-300">Still trying ({link.reconnects} attempts). Is <code>start-backend.ps1</code> running? See OPERATOR_CHECKLIST.md.</p>}
        </div>
      </main>
    )
  }

  if (patientOnly) {
    return (
      <main className={`h-full p-2 ${hc ? 'hc' : ''}`}>
        <ErrorBoundary area="Patient screen"><PatientView s={state} link={{ connected, stale }} /></ErrorBoundary>
        <button type="button" onClick={exitPatientMode} data-testid="exit-patient-mode"
          className="fixed bottom-2 right-2 z-50 rounded-full bg-panel2 px-3 py-1 text-xs text-muted opacity-40 hover:opacity-100 focus:opacity-100">
          Exit patient mode (operator)
        </button>
      </main>
    )
  }

  return (
    <main className={`flex h-full flex-col gap-3 p-3 pt-12 lg:flex-row ${hc ? 'hc' : ''}`}>
      <nav aria-label="View" className="fixed left-1/2 top-1 z-40 flex -translate-x-1/2 gap-1 rounded-full border border-line bg-panel p-1 text-sm shadow-lg">
        <button type="button" aria-pressed={view === 'operate'} onClick={() => setView('operate')}
          className={`rounded-full px-3 py-1 font-semibold ${view === 'operate' ? 'bg-sky-600' : 'text-muted hover:text-text'}`}>Patient + operator</button>
        <button type="button" aria-pressed={view === 'compare'} onClick={() => setView('compare')} data-testid="open-compare"
          className={`rounded-full px-3 py-1 font-semibold ${view === 'compare' ? 'bg-sky-600' : 'text-muted hover:text-text'}`}>Fixed menu vs NeuroSteno</button>
        <span aria-hidden className="mx-1 w-px bg-line" />
        <button type="button" onClick={enterPatientMode} data-testid="enter-patient-mode" className="rounded-full px-3 py-1 font-semibold text-muted hover:text-text">⛶ Patient full screen</button>
        <button type="button" aria-pressed={hc} onClick={toggleHc} data-testid="toggle-hc"
          className={`rounded-full px-3 py-1 font-semibold ${hc ? 'bg-white text-black' : 'text-muted hover:text-text'}`}>◐ High contrast</button>
      </nav>
      {view === 'compare' ? (
        <>
          <div className="min-h-[60vh] lg:min-h-0 lg:w-[40%] lg:shrink-0">
            <ErrorBoundary area="Patient screen"><PatientView s={state} link={{ connected, stale }} /></ErrorBoundary>
          </div>
          <div className="min-h-0 flex-1">
            <ErrorBoundary area="Comparison"><ComparePage s={state} ab={ab} /></ErrorBoundary>
          </div>
        </>
      ) : (
        <>
          <div className="min-h-0 flex-1">
            <ErrorBoundary area="Patient screen"><PatientView s={state} link={{ connected, stale }} /></ErrorBoundary>
          </div>
          {showPanel && (
            <div className="h-[70vh] w-full shrink-0 lg:h-full lg:w-[440px]">
              <ErrorBoundary area="Operator panel"><OperatorPanel s={state} link={link} onOpenCompare={() => setView('compare')} /></ErrorBoundary>
            </div>
          )}
          <button type="button" aria-expanded={showPanel} onClick={() => setShowPanel((v) => !v)}
            className="fixed bottom-3 right-3 z-50 rounded-full bg-panel2 px-3 py-1 text-xs text-muted opacity-70 hover:opacity-100">
            {showPanel ? 'Hide' : 'Show'} operator panel
          </button>
        </>
      )}
      {!live && (
        <div role="status" data-testid="link-banner" className="fixed left-1/2 top-12 z-50 -translate-x-1/2 rounded bg-red-700 px-3 py-1 text-sm font-semibold">
          {connected ? 'No fresh data from the backend: display paused' : 'Backend disconnected: reconnecting…'}
        </div>
      )}
    </main>
  )
}
