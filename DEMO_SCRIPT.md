# NeuroSteno: 5-minute judge demo

Setup: `start-backend.ps1` + `start-frontend.ps1`, browser at http://localhost:5173.
Operator panel on the right, patient screen on the left.

1. **Honesty first (20 s).** Point at the mode badge. Click *Simulated* → the purple
   **SIMULATED EEG · synthetic signals** badge appears. Explain: REAL / SIMULATED / REPLAY
   are always visibly different, and REAL mode will not communicate until calibrated.
2. **Home screen (20 s).** "What do you need?" with the five large categories. Session → choices
   **2**: the same five cards stay visible inside two semantic targets ("Medical",
   "Daily needs · People"). Every one of 1,474 phrases is reachable in 2, 3 or 5 target mode (tested).
3. **A selection with correction (60 s).** Simulation → *Left leg: reposition*. The virtual patient's
   EEG responds to the right target; FBCCA collects evidence (Signal tab shows scores vs. threshold),
   the choice is **highlighted on the same screen**, then YES confirms it.
   Click *Next selection: wrong target*: the wrong card lights up, the virtual patient looks at
   **NO**, it is rejected and the screen does not change.
4. **Final confirmation (20 s).** The full sentence appears: *"My left leg hurts. Please help me
   reposition."* It is only sent after SEND. Show *Messages confirmed by the patient*.
5. **Context (60 s).** Profile → *Jordan (mobility)*. The Pain and Comfort cards show ★ suggestions.
   Routing tab: shortcut candidates with reasons, expected selections vs. the static menu, and why
   a candidate was rejected. Click *Compute A/B/C*: "Turn me left" takes 11 → 5 selections for
   Jordan but is unchanged for the new patient. Switch to *Sam (respiratory)*: suction-now 8 → 6 via
   the always-on urgent route. Toggle *Static baseline* for the A/B comparison.
6. **Failure handling (40 s).** Simulation → *Simulate Bluetooth dropout* → **Communication paused**
   (not an alarm; nothing is selected). Restore → it resumes. Mark Oz + PO7 bad → paused with the reason.
7. **Safety (20 s).** HELP NOW (demo) → clearly labelled as not a certified alarm. Mention:
   nonresponse never triggers emergencies, and missing input is never approval.
8. **Real hardware (30 s).** Show `scripts/unicorn_hardware_check.py` output: UN-2024.09.36 streamed
   2500/2500 samples at 250 Hz with 0 drops over UnicornPy. The plausibility check also caught that
   the headset was not on the scalp, so we make no claim about real classification yet.
