# NeuroSteno: real-headset demo runbook (g.tec Unicorn Hybrid Black, UN-2024.09.36)

Branch: `feature/real-eeg-demo`. Run this on the laptop that already has **Unicorn Suite + UnicornPy**
working. No other installs are needed: Python 3.12 and Node 20+ must be on PATH.

## 1. Pull and start (one command)

```powershell
git fetch origin
git switch feature/real-eeg-demo        # first time: git switch -c feature/real-eeg-demo origin/feature/real-eeg-demo
git pull
.\start-hardware-demo.ps1 -Rebuild      # venv + deps, finds UnicornPy, builds UI, 5 s acquisition check, serves :8000
```
The browser opens at **http://127.0.0.1:8000** (UI and API on one server). Close Unicorn Recorder and any
other g.tec app first. Options: `-Serial UN-...`, `-SkipCheck`, `-UnicornPyDir "<folder with UnicornPy.pyd>"`
(only if UnicornPy isn't in the Unicorn Suite default folder), and `-NoBrowser`.
Optional, for a clean comparison: stop the server, then run `.\demo-reset.ps1` (archives simulated history, deletes nothing).

## 2. Two honest operating modes (always visible on screen)

| Mode | How to start (operator panel → Session) | What decides a selection | Label on screen |
|---|---|---|---|
| **LIVE** | *Find paired Unicorn* → **Connect UN-… (LIVE)** | Real EEG only: the existing filters + FBCCA + dynamic stopping, after a 2-target calibration on the wearer | green frame, **REAL EEG**, "Calibrated / NOT calibrated" chip |
| **ASSISTED** | *Find paired Unicorn* → **Assisted UN-…** | Real headset stream **plus simulated SSVEP augmentation** for the operator/autopilot-chosen target, decoded by the same FBCCA | teal striped frame, **ASSISTED · real EEG + simulated augmentation**; sent messages tagged *assisted* |

ASSISTED never counts as real: it has its own origin (calibration, history, metrics and sent messages are
separate). *Assisted dry run* (no headset) is for rehearsal only and says so.
Final SEND always needs a decoded **YES** on the review screen in every mode. Head gestures stay **off**.

## 3. LIVE attempt (hard limit: 15 minutes)

1. **Session → Patient profile: Jordan (mobility)** *before* calibrating (calibration is stored per profile).
2. **Connect UN-2024.09.36 (LIVE)**. Then **Signal** tab: the electrode map should be mostly ✓ (attach both ear
   clips). Optionally run **Eyes open/closed check** (~40 s): "Brain activity confirmed" on REAL data = good contact.
3. **Session → Interaction**: tick the photosensitivity warning, then **Visual stimulation (flicker) ON**.
   **Choices per screen: 2**.
4. **Calibration → Calibrate 2 targets**. Taban looks at the highlighted target on each cue and at the centre
   on REST. When it finishes, the result line must say **supported: true** (accepted-trial accuracy shown).
5. Patient screen: Taban looks at a card. It is highlighted ("Did you mean this?") and he looks at **YES** to
   accept. Jordan's route to **Turn me left** is 5 selections at 2 targets, including the ★ shortcut.
   At the final review, he looks at **SEND**.
6. Do it **twice** before recording. If calibration is not supported, or selections are mostly wrong or
   abstaining after ~15 min, **switch to ASSISTED**: do not keep tuning.

## 4. ASSISTED fallback (reliable)

**Session → Assisted UN-2024.09.36** → **Simulation** tab → **Autopilot: Turn me left** (or click a card on
the patient screen to steer the augmentation). The headset stays on Taban; the stream, electrode map and
motion sensor are real. The autopilot also gives the final YES once the review appears.
To show that nothing is sent without a YES, press **Stop autopilot** at the review: it waits; then restart the autopilot.

## 5. 60–90 s recording sequence

1. (5 s) Taban wearing the headset; **Signal** tab: live electrode map. **Inputs** tab: motion sensor streaming
   (accelerometer and gyroscope are live; gesture input is off on purpose).
2. (10 s) **Session**: profile **Jordan (synthetic)**. The patient screen shows ★ suggestions and the NeuroRoute strip.
3. (10 s) **Choices 2 → 3 → 5**: the same five categories as 2, 3 or 5 targets. Fewer targets means each
   decision is easier but the menu is deeper (Routing / comparison "Why 2, 3 or 5"). In LIVE mode 3 and 5
   stay locked until calibrated on real data (safety rule), so show this step in ASSISTED or SIMULATED.
4. (30 s) LIVE (if it worked) or ASSISTED: **Turn me left** → final review → **YES/SEND** → "Sent", labelled with its mode.
5. (20 s) **Fixed menu vs NeuroSteno** (top bar): **11 → 5** brain selections (EXACT). Speed-vs-safety tile
   (BENCHMARK · SIMULATED). "Which method suits which patient": hybrid is not a general win.
6. (5 s) End on the mode badge and "Research prototype. Not a medical device."

## 6. What you may say (verified against current data)

- **11 → 5** selections, Jordan, "Turn me left", 2 targets: exact navigator count (no EEG).
- Context routing vs the best fixed menu (simulated benchmark): **1.22–1.44× correctly confirmed msgs/min when
  requests match the patient profile**, 1.07–1.20× when they don't (95% CIs in the app).
- Full NeuroSteno trades speed for safety: **0 messages sent while idle** vs ~50/h for fixed menus (simulated).
- Hybrid head-gesture input: better in **1 of 12** simulated cells, worse in 6. EEG-only stays the default.
- Do **not** claim clinical validation, real-patient results, or that 3/5-target modes were validated live.
- Say whether each selection was **LIVE** or **ASSISTED**. The screen shows it.

## 7. If something goes wrong

| Problem | Fix |
|---|---|
| "UnicornPy library not found" | `.\start-hardware-demo.ps1 -UnicornPyDir "<folder with UnicornPy.pyd>"` |
| Connect fails / no device | Close Unicorn Recorder; power-cycle the headset; pair it in Windows Bluetooth |
| "Communication paused" | Signal tab: fix the electrodes or ear clips; Bluetooth within 2 m |
| Patient screen "Connection lost" | Backend stopped: rerun `.\start-hardware-demo.ps1 -SkipCheck` |
| LIVE not reliable in time | Use ASSISTED (section 4) |
