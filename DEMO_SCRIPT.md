# NeuroSteno: 5-minute judge demo

Setup: `start-backend.ps1` + `start-frontend.ps1`, browser at http://localhost:5173 (fullscreen, F11).
Patient screen on the left, operator panel on the right. The operator panel opens on the **Demo** tab:
each step below appears there with its talking points, one-click buttons (ordinary API commands)
and a **5:00 timer** (step budgets add up to 4:30, leaving 30 s spare). Press **Start timer**, then
**Next →** after each step. **Reset demo** restores Alex, 5 choices, risk-aware routing, explicit
confirmation and healthy signals. The Demo tab never presses YES/SEND for the patient.

1. **Honesty first (20 s).** *Switch to Simulated.* The patient screen gets a **striped purple frame**,
   the **SIMULATED EEG · synthetic signals** badge, and the caption "Synthetic signals from a virtual
   patient. Not a person's brain activity." REAL has a solid green frame and a *Calibrated / NOT
   calibrated* chip; REPLAY has a striped amber frame and says "Not live". REAL mode will not
   communicate until it is calibrated on real data (a simulated calibration never counts).
2. **Home screen (20 s).** "What do you need?" shows the five large categories. Press **2 choices**: the
   same five cards stay visible inside two semantic targets ("Medical", "Daily needs · People").
   Every one of the 1,474 phrases is reachable in 2, 3 or 5 target mode (tested). Press **5 choices**.
3. **A selection with correction (60 s).** **Autopilot: left leg.** The virtual patient's EEG responds
   to the right target. FBCCA collects evidence (Signal tab: scores vs. threshold), the choice is
   **highlighted on the same screen** ("Did you mean …?"), then YES confirms it.
   Press **Next selection: wrong target**: the wrong card lights up, the virtual patient looks at
   **NO**, it is rejected and the screen does not change.
4. **Final confirmation (20 s).** The full sentence appears: *"My left leg hurts. Please help me
   reposition."* It is only sent after SEND. The "sent" screen states the message was produced from
   SIMULATED EEG. Show *Messages confirmed by the patient* (Session tab), each tagged with its origin.
5. **Context: NeuroRoute (60 s).** **Profile: Jordan.** Cards show ★ suggestions plus a
   *"NeuroRoute: Contains suggestion …"* reason. The NeuroRoute strip shows expected selections per
   message vs. the static menu and the always-on urgent routes. Routing tab: shortcut candidates with
   reasons and why a candidate was rejected. *Compute A/B/C*: "Turn me left" goes 11 → 5 selections for
   Jordan but is unchanged for the new patient. **Profile: Sam**: suction-now 8 → 6 via the urgent
   route. **Static baseline** / **Risk-aware** for the A/B comparison (the strip changes label).
6. **Failure handling (40 s).** **Bluetooth dropout** → **Communication paused** (not an alarm;
   nothing is selected). **Restore Bluetooth** → it resumes. **Oz + PO7 bad** → paused with the
   reason. **Electrodes OK**.
7. **Safety (20 s).** **HELP NOW (demo)** → clearly labelled as not a certified alarm; **Acknowledge**.
   Mention: nonresponse never triggers emergencies, and missing input is never approval.
8. **Real hardware (30 s).** Show `scripts/unicorn_hardware_check.py` output: UN-2024.09.36 streamed
   2500/2500 samples at 250 Hz with 0 drops over UnicornPy. The plausibility check also caught that
   the headset was not on the scalp, so we make no claim about real classification yet. Open the
   **Signal** tab: the electrode map (contact score is a signal heuristic, **not impedance**) and the
   **Eyes open/closed check** (no flicker, spoken cues). On simulated data its result is labelled
   "pipeline check only"; only a real-origin result may say "brain activity confirmed".

Accessibility notes for Q&A: the patient screen uses Atkinson Hyperlegible and very large
targets; operator tabs, overlays (dialog / alertdialog / status) and the evidence progress bar
have proper ARIA roles; only the phase sentence is a live region (no 10 Hz announcements); focus is
always visible; reduced-motion disables the highlight pulse; origin is shown by stripes and text,
not colour alone. Flicker stays off until the photosensitivity warning is acknowledged.
