import { Component, type ReactNode, useEffect, useState } from 'react'
import { useEngineState } from './api'
import ComparePage from './components/ComparePage'
import OperatorPanel from './components/OperatorPanel'
import PatientView from './components/PatientView'
import { MedicalIcon } from './components/MedicalIcon'
import { flicker } from './flicker'
import { useLiveAB } from './useLiveAB'
import { readInputs } from './multimodal'

export type View = 'operate' | 'compare'
function readHash() { return typeof location !== 'undefined' ? location.hash : '' }
function loadFlag(key: string) { try { return localStorage.getItem(key) === '1' } catch { return false } }
function saveFlag(key: string, on: boolean) { try { localStorage.setItem(key, on ? '1' : '0') } catch { /* unavailable */ } }

export class ErrorBoundary extends Component<{ area: string; children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null }
  static getDerivedStateFromError(e: unknown) { return { error: e instanceof Error ? e.message : String(e) } }
  render() {
    if (!this.state.error) return this.props.children
    return <div role="alert" data-testid={`error-${this.props.area}`} className="display-error">
      <h2>{this.props.area}: display error</h2>
      <p>Nothing is selected from this screen while it is broken. The backend keeps its own state.</p>
      <p className="text-muted">{this.state.error}</p>
      <button onClick={() => this.setState({ error: null })}>Try again</button>
    </div>
  }
}

export default function App() {
  const link = useEngineState()
  const { state, connected, stale } = link
  const [hash, setHash] = useState(readHash)
  const patientOnly = hash === '#patient'
  const demo = hash === '#demo'
  const view: View = hash === '#compare' ? 'compare' : 'operate'
  const [showPanel, setShowPanel] = useState(false)
  const [hc, setHc] = useState(() => loadFlag('ns.hc'))
  const ab = useLiveAB(state)
  const live = connected && !stale
  useEffect(() => {
    const enabled = loadFlag('ns.flicker')
    document.documentElement.dataset.visualStimulation = enabled ? 'on' : 'off'
    flicker.setEnabled(enabled)
    const on = () => setHash(readHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  useEffect(() => { flicker.setSuspended(!live) }, [live])
  const navigate = (next: string) => { location.hash = next; setHash(readHash()) }
  const enterPatientMode = () => {
    navigate('#patient')
    document.documentElement.requestFullscreen?.().catch(() => {})
  }
  const exitPatientMode = () => {
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {})
    navigate('')
  }
  if (!state) return <main className={`loading-screen ${hc ? 'hc' : ''}`}>
    <MedicalIcon name="brand" /><h1>NeuroSteno</h1>
    <p>{connected ? 'Loading communication workspace…' : 'Connecting to the communication service…'}</p>
    {link.reconnects > 2 && <p className="text-muted">Still trying ({link.reconnects} attempts). Check that the backend is running.</p>}
  </main>
  if (patientOnly) return <main className={`patient-fullscreen ${hc ? 'hc' : ''}`}>
    <ErrorBoundary area="Patient screen"><PatientView s={state} link={{ connected, stale }} /></ErrorBoundary>
    <button onClick={exitPatientMode} data-testid="exit-patient-mode" className="exit-patient">Exit patient mode (operator)</button>
  </main>

  return <main className={`app-shell ${hc ? 'hc' : ''}`} data-demo={demo}>
    <header className="app-header">
      <a className="brand" href="#" onClick={() => { setHash(''); setShowPanel(false) }} aria-label="NeuroSteno home">
        <MedicalIcon name="brand" /><span>NeuroSteno<small>Assistive communication</small></span>
      </a>
      <nav aria-label="View" className="view-nav">
        <button aria-pressed={view === 'operate' && !demo} onClick={() => navigate('')}>Patient + operator</button>
        <button aria-pressed={demo} onClick={() => { navigate('#demo'); setShowPanel(false) }}>Demo mode</button>
        <button aria-pressed={view === 'compare'} onClick={() => navigate('#compare')} data-testid="open-compare">Fixed menu vs NeuroSteno</button>
      </nav>
      <div className="header-tools">
        <button onClick={enterPatientMode} data-testid="enter-patient-mode" title="Patient full screen"><MedicalIcon name="expand" /><span>Full screen</span></button>
        <button aria-pressed={hc} onClick={() => setHc(v => { saveFlag('ns.hc', !v); return !v })} data-testid="toggle-hc" title="High contrast"><MedicalIcon name="contrast" /><span>High contrast</span></button>
        <button className="operator-toggle" aria-expanded={showPanel} aria-controls="operator-drawer" onClick={() => setShowPanel(v => !v)}><MedicalIcon name="settings" />{showPanel ? 'Close controls' : 'Controls'}</button>
      </div>
    </header>
    <div className="session-status" aria-label="Session status">
      <span className={`connection-status ${live ? 'is-live' : 'is-offline'}`}><i />{live ? 'Connected' : connected ? 'Data paused' : 'Reconnecting'}</span>
      <span><b>Input</b> {readInputs(state).mode.replaceAll('_', ' ')}</span>
      {state.mode.kind === 'real' && <span data-testid="live-eeg-status"><b>Live EEG</b> {!live ? 'Connection unverified' : state.source?.status?.connected && state.source?.status?.streaming ? 'Streaming' : 'Not streaming'}</span>}
      <span><b>Patient</b> {state.profile.display_name}</span>
      <span><b>Routing</b> {state.config.routing_mode === 'adaptive' ? 'Context-aware' : 'Fixed menu'}</span>
      <span><b>Choices</b> {state.screen.n_choices}{state.config.auto_adapt ? ' · adaptive' : ''}</span>
      <span data-testid="signal-quality-status" title={state.quality.problem ?? undefined} className={`signal-status ${state.quality.signal_ok ? 'signal-good' : ''}`}><MedicalIcon name="signal" />{state.quality.signal_ok ? 'Signal ready' : state.quality.signal_ok === false ? 'Signal needs attention' : 'Checking signal'}</span>
    </div>
    {demo && <div className="demo-toolbar" aria-label="Essential demo controls">
      <span>Recording view <small>{state.mode.kind === 'real' ? 'Live headset · backend signal status' : state.mode.kind === 'simulated' ? 'Synthetic EEG · virtual patient' : 'EEG data origin remains visible'}</small></span>
      <button onClick={() => setShowPanel(v => !v)}>Patient & simulation controls</button>
      <button onClick={() => navigate('#compare')}>Compare approaches</button>
    </div>}
    <div className={`workspace ${view === 'compare' ? 'comparison-workspace' : ''} ${showPanel ? 'with-controls' : ''}`}>
      <div className="patient-workspace"><ErrorBoundary area="Patient screen"><PatientView s={state} link={{ connected, stale }} /></ErrorBoundary></div>
      {view === 'compare' && <div className="comparison-panel"><ErrorBoundary area="Comparison"><ComparePage s={state} ab={ab} /></ErrorBoundary></div>}
      {showPanel && <div id="operator-drawer" className="operator-drawer"><ErrorBoundary area="Operator panel"><OperatorPanel s={state} link={link} onOpenCompare={() => { navigate('#compare'); setShowPanel(false) }} /></ErrorBoundary></div>}
    </div>
    {!live && <div role="status" data-testid="link-banner" className="link-banner">{connected ? 'No fresh data from the backend: display paused' : 'Backend disconnected: reconnecting…'}</div>}
  </main>
}
