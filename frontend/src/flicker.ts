/**
 * Frame-synchronised visual stimulation.
 *
 * Every registered element is updated once per display frame (requestAnimationFrame), so
 * luminance changes are locked to the monitor refresh:
 *   - SSVEP: sampled sinusoid  L(t) = 0.5 * (1 + sin(2*pi*f*t))  evaluated at frame time t.
 *   - c-VEP: binary code value  code[(frameIndex + shift) mod len]  (one code bit per frame).
 *
 * Frame timing is recorded (timestamps, intervals, missed frames) so presentation timing can
 * be reported and, for c-VEP, aligned to EEG samples.
 */

export type Stim =
  | { kind: 'ssvep'; freq: number; phase?: number }
  | { kind: 'cvep'; code: number[]; shift: number }

export function ssvepLuminance(freq: number, tSeconds: number, phase = 0): number {
  return 0.5 * (1 + Math.sin(2 * Math.PI * freq * tSeconds + phase))
}

export function cvepLuminance(code: number[], shift: number, frameIndex: number): number {
  const n = code.length
  return code[(((frameIndex + shift) % n) + n) % n] ? 1 : 0
}

interface Entry { el: HTMLElement; stim: Stim }

export class FlickerEngine {
  private entries = new Map<string, Entry>()
  private raf = 0
  private t0 = 0
  private frame = 0
  private last = 0
  enabled = false
  maxOpacity = 0.9
  stats = { frames: 0, meanIntervalMs: 0, missed: 0, refreshHz: 0 }

  register(id: string, el: HTMLElement | null, stim: Stim | null) {
    if (!el || !stim) { this.unregister(id); return }
    this.entries.set(id, { el, stim })
    this.ensureRunning()
  }

  unregister(id: string) {
    const e = this.entries.get(id)
    if (e) e.el.style.opacity = '0'
    this.entries.delete(id)
  }

  setEnabled(on: boolean) {
    this.enabled = on
    if (!on) this.entries.forEach((e) => (e.el.style.opacity = '0'))
    this.ensureRunning()
  }

  private ensureRunning() {
    if (this.raf || typeof window === 'undefined' || !window.requestAnimationFrame) return
    this.t0 = performance.now()
    this.frame = 0
    const step = (now: number) => {
      this.raf = window.requestAnimationFrame(step)
      if (this.last) {
        const dt = now - this.last
        const s = this.stats
        s.frames++
        s.meanIntervalMs = s.meanIntervalMs ? 0.98 * s.meanIntervalMs + 0.02 * dt : dt
        s.refreshHz = 1000 / s.meanIntervalMs
        if (dt > 1.5 * s.meanIntervalMs) s.missed += Math.round(dt / s.meanIntervalMs) - 1
      }
      this.last = now
      const t = (now - this.t0) / 1000
      this.frame++
      if (!this.enabled) return
      this.entries.forEach(({ el, stim }) => {
        const l = stim.kind === 'ssvep' ? ssvepLuminance(stim.freq, t, stim.phase) : cvepLuminance(stim.code, stim.shift, this.frame)
        el.style.opacity = String(l * this.maxOpacity)
      })
    }
    this.raf = window.requestAnimationFrame(step)
  }
}

export const flicker = new FlickerEngine()
