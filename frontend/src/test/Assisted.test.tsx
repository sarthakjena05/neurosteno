import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import PatientView from '../components/PatientView'
import { normalizeState } from '../normalize'
import type { State } from '../types'
import { mockState } from './mockState'

const ASSISTED_REAL: State['mode'] = { kind: 'assisted', label: 'ASSISTED EEG', origin: 'assisted', is_real_eeg: false, replay_of: null, assisted_base: 'real' }

describe('ASSISTED mode (real EEG + simulated augmentation) is never presented as LIVE', () => {
  it('has its own frame, badge and caption', () => {
    render(<PatientView s={mockState({ mode: ASSISTED_REAL })} />)
    expect(screen.getByTestId('patient-screen').className).toContain('mode-frame-assisted')
    expect(screen.getByTestId('mode-badge')).toHaveTextContent('ASSISTED · real EEG + simulated augmentation')
    expect(screen.getByTestId('mode-caption')).toHaveTextContent('Every selection here is ASSISTED')
    expect(screen.queryByText(/^REAL EEG$/)).toBeNull()
  })

  it('the dry run says there is no headset', () => {
    render(<PatientView s={mockState({ mode: { ...ASSISTED_REAL, assisted_base: 'simulated' } })} />)
    expect(screen.getByTestId('mode-badge')).toHaveTextContent('dry run (no headset)')
  })

  it('sent messages from assisted mode are tagged as not live real EEG', () => {
    render(<PatientView s={mockState({ mode: ASSISTED_REAL, phase: 'MESSAGE_SENT', feedback: { type: 'sent', label: 'Please turn me onto my left side.' },
      sent: [{ text: 'Please turn me onto my left side.', time: 1, data_origin: 'assisted', urgent: false, provisional_steps: 0 }] })} />)
    expect(screen.getByTestId('sent-origin')).toHaveTextContent('ASSISTED EEG')
    expect(screen.getByTestId('sent-origin')).toHaveTextContent('Not a live real-EEG message')
  })

  it('normalisation keeps the assisted kind (never upgrades it to real)', () => {
    const s = normalizeState({ ...mockState(), mode: ASSISTED_REAL })
    expect(s.mode.kind).toBe('assisted')
    expect(s.mode.is_real_eeg).toBe(false)
  })
})
