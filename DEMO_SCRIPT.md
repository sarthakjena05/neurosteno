# NeuroSteno: 5-minute judge demo

Setup: follow **OPERATOR_CHECKLIST.md** (launch, Preflight = GO, recovery). Browser at http://localhost:5173 (F11).
Patient screen on the left, operator panel on the right. The operator panel opens on the **Demo** tab:
each step below appears there with its talking points, one-click buttons (ordinary API commands)
and a **5:00 timer** (step budgets add up to 4:30, leaving 30 s spare). Press **Start timer**, then
**Next →** after each step. **Reset demo** restores Alex, 5 choices, risk-aware routing, explicit
confirmation and healthy signals. The Demo tab never presses YES/SEND for the patient.

**Pre-demo checklist (live A/B fairness)**
- The live NeuroSteno arm ranks with the seed profile **plus saved simulated history** in
  `backend/data/runtime/history_<profile>_simulated.json` (git-ignored, survives restarts; the UI cannot reset
  it). Every rehearsal adds to it. For a clean demo, stop the backend, move those files aside, and restart.
  The EXACT counts and the benchmark always use seed profiles only.
- After a live run, check the "Conditions recorded" table says **identical except routing ✓**. If a live count
  differs from the EXACT count, the card says so; leave it visible and explain it as history or retries.
- The live run restarts the simulator (default signal settings). Re-apply any simulation tweaks afterwards.


0. **The idea in 30 seconds (30 s).** Click **Fixed menu vs NeuroSteno** (top centre, or the Demo tab
   button). The live patient screen stays on the left. **First, the three summary tiles at the top (≈20 s):**
   (1) *Less effort*: fixed menu → NeuroSteno brain selections for the same message (**EXACT**);
   (2) *Speed vs safety, not a free win*: the fastest fixed menu (chosen in hindsight) vs full NeuroSteno, in
   correct messages/min **and** messages sent while the patient attended nothing (**BENCHMARK · SIMULATED**);
   (3) *Input fits the patient*: EEG-only today, head gestures only after calibration + caregiver switch,
   hybrid benefit **NOT MEASURED**. Then, below: the same message for the same patient
   (default: Jordan, "Turn me left", 2 targets): **Traditional fixed menu** vs **NeuroSteno**, with two big
   brain-selection counts, one dot per selection, and the difference. Every number carries a tag:
   **EXACT** (navigator count, no EEG), **ESTIMATE** (routing model, with its source), **MEASURED ·
   SIMULATED/REAL** (completed runs) or **NOT MEASURED**. Click patient **A**: same count, because NeuroSteno
   does not guess without evidence. The table shows every scenario, including the +1 cost of always-visible
   urgent routes. Below it, "How NeuroSteno decides": (1) why these phrases (probabilities, shortcuts kept
   only if they lower expected time, urgent routes always on), (2) why 2/3/5 targets (expected time per
   count, *recommended* vs *in use*), (3) how uncertainty works (live phase outlined: evidence →
   highlight on the same screen → YES/NO → final SEND).
   **Benchmark (controlled, simulated):** each card shows the like-for-like benchmark pair (A*n* fixed menu vs
   B*n* context routing, same *n* and decoder, 10 runs, same seed and signal level). Below that are all
   conditions and the pooled table. Say both halves out loud: the fixed menus are fast but send messages
   while the patient attends nothing (e.g. A5 ≈ 54/hour), while full NeuroSteno (D) sends none and is slower;
   D+ is about as fast as A5. Simulated EEG and a perfect-intent virtual patient only (see Limitations).
   Optional: **Run live: NeuroSteno, then fixed menu** (about 2 min). It is a **fixed-order demonstration,
   one run each, not a controlled experiment**; the benchmark is the controlled comparison. The panel lists
   what is held equal (simulator restarted with seed 4242 and default signal settings, mistakes off, same
   patient/message/targets/policy) and what is not (saved history, noise realisation, order), and shows the
   recorded conditions of both arms. Switch back to **Patient + operator**.
1. **Honesty first (20 s).** *Switch to Simulated.* The patient screen gets a **striped purple frame**,
   the **SIMULATED EEG · synthetic signals** badge, and the caption "Synthetic signals from a virtual
   patient. Not a person's brain activity." REAL has a solid green frame and a *Calibrated / NOT
   calibrated* chip; REPLAY has a striped amber frame and says "Not live". REAL mode will not
   communicate until it is calibrated on real data (a simulated calibration never counts).
2. **Home screen (15 s).** "What do you need?" shows the five large categories. Press **2 choices**: the
   same five cards stay visible inside two semantic targets ("Medical", "Daily needs · People").
   Every one of the 1,474 phrases is reachable in 2, 3 or 5 target mode (tested). Press **5 choices**.
3. **A selection with correction (50 s).** **Autopilot: left leg.** The virtual patient's EEG responds
   to the right target. FBCCA collects evidence (Signal tab: scores vs. threshold), the choice is
   **highlighted on the same screen** ("Did you mean …?"), then YES confirms it.
   Press **Next selection: wrong target**: the wrong card lights up, the virtual patient looks at
   **NO**, it is rejected and the screen does not change.
4. **Final confirmation (20 s).** The full sentence appears: *"My left leg hurts. Please help me
   reposition."* It is only sent after SEND. The "sent" screen states the message was produced from
   SIMULATED EEG. Show *Messages confirmed by the patient* (Session tab), each tagged with its origin.
5. **Context: NeuroRoute (40 s).** **Profile: Jordan.** Cards show ★ suggestions plus a
   *"NeuroRoute: Contains suggestion …"* reason. The NeuroRoute strip shows expected selections per
   message vs. the static menu and the always-on urgent routes. Routing tab: shortcut candidates with
   reasons and why a candidate was rejected. *Compute A/B/C*: "Turn me left" goes 11 → 5 selections for
   Jordan but is unchanged for the new patient. **Profile: Sam**: suction-now 8 → 6 via the urgent
   route. **Static baseline** / **Risk-aware** for the A/B comparison (the strip changes label).
6. **Failure handling (35 s).** **Bluetooth dropout** → **Communication paused** (not an alarm;
   nothing is selected). **Restore Bluetooth** → it resumes. **Oz + PO7 bad** → paused with the
   reason. **Electrodes OK**.
7. **Safety (20 s).** **HELP NOW (demo)** → clearly labelled as not a certified alarm; **Acknowledge**.
   Mention: nonresponse never triggers emergencies, and missing input is never approval.
8. **Real hardware (20 s).** Show `scripts/unicorn_hardware_check.py` output: UN-2024.09.36 streamed
   2500/2500 samples at 250 Hz with 0 drops over UnicornPy. The plausibility check also caught that
   the headset was not on the scalp, so we make no claim about real classification yet. Open the
   **Signal** tab: the electrode map (contact score is a signal heuristic, **not impedance**) and the
   **Eyes open/closed check** (no flicker, spoken cues). On simulated data its result is labelled
   "pipeline check only"; only a real-origin result may say "brain activity confirmed".

9. **Every patient is different (20 s).** Say: *"NeuroSteno doesn't assume every patient has the same
   abilities. It calibrates available brain and movement signals, then selects the fastest reliable
   communication method for that patient."* Open the operator **Inputs** tab.
   - **EEG-only example (works today, simulated):** a patient with no reliable head movement. The mode is
     **EEG-only**, every choice and every YES/NO is by SSVEP, and head nod / shake / tilt show *not available*.
     Movement is never assumed: the headset's motion-sensor channels are listed as "present in the data
     stream, not evaluated".
   - **Hybrid example (design preview, not yet backed by the backend):** pick *Patient with reliable residual
     head movement* under **UI preview with mock states**. Point at the **MOCK · UI PREVIEW · NOT BACKEND
     STATE** banner: after a passing nod/shake check *and* a caregiver switch, the patient could answer
     YES with a nod **during navigation**; EEG still picks the choice, conflicting signals abstain (*Conflicting
     signals* mock), and the **final SEND is always a separate EEG confirmation**. Gesture-based final
     confirmation stays disabled until real-world false-activation testing supports it; EEG-only always works.
   - Neither example is validated. The **"Which method suits which patient?"** section of the comparison page
     shows *NOT MEASURED* for all four strategies (Fixed EEG-only, Adaptive EEG-only, Hybrid EEG + IMU,
     Optimized multimodal) until the backend publishes the multimodal benchmark; then it shows which method
     is best per patient group, from the data.

Accessibility notes for Q&A: the patient screen uses Atkinson Hyperlegible and very large
targets; operator tabs, overlays (dialog / alertdialog / status) and the evidence progress bar
have proper ARIA roles; only the phase sentence is a live region (no 10 Hz announcements); focus is
always visible; reduced-motion disables the highlight pulse; origin is shown by stripes and text,
not colour alone. Flicker stays off until the photosensitivity warning is acknowledged.
