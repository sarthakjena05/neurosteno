/** Spoken cue (e.g. during the eyes-closed check, when the patient cannot read the screen). */
export function speakCue(text: string): void {
  try {
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) return
    window.speechSynthesis.cancel()
    const u = new SpeechSynthesisUtterance(text)
    u.rate = 0.9
    window.speechSynthesis.speak(u)
  } catch { /* audio unavailable: the visual cue is still shown */ }
}
