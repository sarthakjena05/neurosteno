import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import PatientView, { gridClass } from '../components/PatientView'
import { cvepLuminance, ssvepLuminance } from '../flicker'
import { mockState } from './mockState'

const CATEGORIES = ['Pain', 'Breathing assistance', 'Food and water', 'Comfort', 'Call caregiver']

describe('home screen', () => {
  it('starts at "What do you need?" with five large categories (5 targets)', () => {
    render(<PatientView s={mockState()} />)
    expect(screen.getByTestId('prompt')).toHaveTextContent('What do you need?')
    for (const c of CATEGORIES) expect(screen.getByText(c)).toBeInTheDocument()
    expect(screen.getAllByTestId(/^slot-/)).toHaveLength(5)
  })

  it('keeps all five categories visible in 2-target mode (grouped into two targets)', () => {
    render(<PatientView s={mockState({}, 2)} />)
    expect(screen.getAllByTestId(/^slot-/)).toHaveLength(2)
    for (const c of CATEGORIES) expect(screen.getByText(c)).toBeInTheDocument()
    expect(screen.getByText('Medical')).toBeInTheDocument()
  })
})

describe('prediction feedback', () => {
  it('immediately illuminates the predicted choice, keeps the screen, and offers YES/NO', () => {
    const s = mockState({
      phase: 'PREDICTION_HIGHLIGHTED',
      highlight: { slot_index: 2, key: 'food-water', label: 'Food and water', margin: 0.6, window_s: 1.5, provisional: false, provisional_remaining_s: null },
      attempt: { kind: 'confirm', targets: [{ key: 'yes', label: 'Yes', freq: 7.5 }, { key: 'no', label: 'No', freq: 12 }], elapsed_s: 0.2, max_window_s: 5, min_window_s: 1.5, calibrated: false, last: null },
    })
    render(<PatientView s={s} />)
    const hl = screen.getByTestId('slot-2')
    expect(hl).toHaveAttribute('data-highlighted', 'true')
    expect(within(hl).getByText('Did you mean this?')).toBeInTheDocument()
    expect(screen.getByTestId('slot-0')).toHaveAttribute('data-highlighted', 'false')
    expect(screen.getByTestId('prompt')).toHaveTextContent('What do you need?') // same screen
    expect(screen.getByTestId('confirm-bar')).toBeInTheDocument()
    expect(screen.getByTestId('decision-yes')).toBeInTheDocument()
    expect(screen.getByTestId('decision-no')).toBeInTheDocument()
    expect(screen.getByText(/pending: Food and water\?/)).toBeInTheDocument()
  })

  it('shows the final message for explicit confirmation before sending', () => {
    const s = mockState({ phase: 'MESSAGE_REVIEW', review: { text: 'My left leg hurts. Please help me reposition.', node_id: 'x', title: 'x', urgent: false, provisional_steps: 0 } })
    render(<PatientView s={s} />)
    expect(screen.getByTestId('review-text')).toHaveTextContent('My left leg hurts. Please help me reposition.')
    expect(screen.getByText('SEND')).toBeInTheDocument()
    expect(screen.getByText('CHANGE')).toBeInTheDocument()
  })

  it('marks provisional steps distinctly from accepted ones', () => {
    const s = mockState({ screen: { ...mockState().screen, at_home: false, crumbs: [{ label: 'Pain', provisional: false }, { label: 'Legs and feet', provisional: true }] } })
    render(<PatientView s={s} />)
    expect(screen.getByText('Legs and feet (provisional)')).toBeInTheDocument()
  })
})

describe('modes and safety states', () => {
  it.each([
    ['real', 'REAL EEG'], ['simulated', 'SIMULATED EEG'], ['replay', 'REPLAY'],
  ] as const)('shows a distinct %s badge', (kind, label) => {
    render(<PatientView s={mockState({ mode: { kind, label, origin: kind, is_real_eeg: kind === 'real', replay_of: kind === 'replay' ? 'simulated' : null } })} />)
    expect(screen.getByTestId('mode-badge')).toHaveTextContent(label)
    if (kind === 'replay') expect(screen.getByTestId('mode-badge')).toHaveTextContent('recorded SIMULATED data')
    if (kind === 'simulated') expect(screen.getByTestId('mode-badge')).toHaveTextContent('synthetic')
  })

  it('pauses with a communication-unavailable state that is not an alarm', () => {
    render(<PatientView s={mockState({ phase: 'COMM_UNAVAILABLE', comm: { available: false, reason: 'No EEG samples arriving', needs_caregiver: true } })} />)
    expect(screen.getAllByText('Communication paused').length).toBeGreaterThan(0)
    expect(screen.getByTestId('comm-reason')).toHaveTextContent('No EEG samples arriving')
    expect(screen.getByText(/This is not an alarm/)).toBeInTheDocument()
    expect(screen.getByText('Caregiver: resume')).toBeInTheDocument()
  })

  it('has a clearly marked HELP NOW demo control', () => {
    render(<PatientView s={mockState()} />)
    expect(screen.getByLabelText('HELP NOW demo control')).toBeInTheDocument()
  })
})

describe('stimulation math', () => {
  it('sinusoidal SSVEP luminance stays in [0,1] and has the right period', () => {
    expect(ssvepLuminance(10, 0)).toBeCloseTo(0.5)
    expect(ssvepLuminance(10, 0.025)).toBeCloseTo(1)
    expect(ssvepLuminance(10, 0.1)).toBeCloseTo(0.5)
  })
  it('c-VEP luminance is a cyclically shifted binary code', () => {
    const code = [1, 0, 0, 1, 1]
    expect([0, 1, 2, 3, 4].map((f) => cvepLuminance(code, 0, f))).toEqual([1, 0, 0, 1, 1])
    expect([0, 1, 2, 3, 4].map((f) => cvepLuminance(code, 2, f))).toEqual([0, 1, 1, 1, 0])
  })
  it('lays out 2, 3 and 5 targets', () => {
    expect(gridClass(2)).toContain('md:grid-cols-2')
    expect(gridClass(3)).toContain('md:grid-cols-3')
    expect(gridClass(5)).toContain('grid-cols-2')
  })
})
