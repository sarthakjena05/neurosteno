# NeuroSteno

Context-aware, adaptive, error-correcting SSVEP communication interface for people with severe
motor impairment (e.g. some people with ALS). Built for the USF BCI Sprint with the
g.tec Unicorn Hybrid Black and the official UnicornPy API.

> **Research / hackathon prototype. NOT a clinically validated medical device.** No accuracy or
> speed claim below is a validated result. All patient profiles are synthetic.

## Status (2026-11-07)

| Area | Status |
|---|---|
| UnicornPy acquisition (UN-2024.09.36 over Intel Bluetooth) | ✅ 10 s streamed: 2500 samples @ 250 Hz, 0 dropped (counter-verified) |
| Real EEG signal validity | ⚠️ **Not yet.** Bring-up recording had all 8 channels identical (corr 1.000) with large DC drift: headset not worn / ear-clip reference not attached. Must be redone with the headset on. |
| Real-EEG SSVEP classification | ❌ **Not validated.** REAL mode refuses to communicate until calibrated on real data. |
| SIMULATED + REPLAY modes, full UI, phrase engine, routing | ✅ working; backend 50 tests, frontend 112 tests passing |
| c-VEP (experimental mode) | ⏳ not started; stimulus math (cyclic-shift codes, frame-locked renderer) is in place |

## Architecture

```
Unicorn Hybrid Black ─BT─▶ Windows Bluetooth ─▶ UnicornPy (official) ─▶ acquisition/unicorn.py ─┐
SimulatedSource (synthetic EEG, same 17-ch layout) ──────────────────────────────────────────────┼─▶ ring buffer
ReplaySource (.npz recordings, origin preserved) ────────────────────────────────────────────────┘
      ▼
signal/quality.py (per-channel + common-mode + stall)   signal/fbcca.py (FBCCA + dynamic stopping)
signal/calibration.py (thresholds + rest/no-control)    signal/blink.py (EXPERIMENTAL)
      ▼
engine.py  selection state machine ─ navigation.py (2/3/5 targets, semantic groups)
           context.py (rule-based ranking) ─ routing.py (risk-aware semantic routing)
      ▼
api.py  FastAPI REST + WebSocket (/ws, 10 Hz state)  ─▶  React + TS + Tailwind (frontend/)
```

Any device can be added by implementing `acquisition/base.py:EEGSource` (BrainFlow, LSL, NerveNewt).

### Selection state machine
`IDLE → EVIDENCE_COLLECTION → PREDICTION_HIGHLIGHTED → ACCEPTED | REJECTED | ABSTAINED → next screen | MESSAGE_REVIEW → MESSAGE_SENT`,
plus `COMM_UNAVAILABLE` (paused) and `CALIBRATING`.

* A prediction only **highlights** the choice on the same screen; YES/NO targets appear.
* NO (or caregiver relay, or experimental blink) → **REJECTED**, same screen, position kept.
* Not reliable by the max window → **ABSTAINED** (never advances). Rest-level responses are
  treated as *no-control* (quiet standby, not a failure, never an emergency).
* Policy: `explicit` (confirm every step), `provisional` (high-margin predictions accepted after a
  correction window, marked *provisional*), or `auto` (provisional only if calibration on this
  data origin shows ≥95 % accuracy and ≤5 % rest false-accepts). **Final messages always require
  explicit confirmation**; missing input never counts as approval.
* Signal stall, poor decoding channels, or common-mode (reference off) → **communication paused**;
  pending highlights are discarded.

### Phrase engine
1,474 unique phrases (Pain 1,224 · Breathing 35 · Food & water 57 · Comfort 98 · Call caregiver 60),
max depth 5, every level a meaningful question. Example:
`Home › Pain › Legs and feet › Left leg › Reposition me › Skip rating` → *"My left leg hurts. Please help me reposition."*
In 2/3-target mode options are packed into **semantic groups** ("Medical", "Left side",
"Daily needs · People"). Tests prove every phrase is reachable in 2/3/5 modes for every profile.
Home always shows the five categories.

### Risk-aware semantic routing (`routing.py`)
Deterministic and explainable (Routing tab). It builds p(phrase) from profile signals plus a 40 % uniform floor,
measures each phrase's exact selection count with the real navigator, and estimates
`E[time] = E[selections] × T_selection + T_review`, with `T_selection` derived from calibrated
accuracy, abstention and decision time (or labelled assumptions). A shortcut is added only if it lowers
E[time] across the whole distribution. Urgent routes (short of breath now, suction now, choking,
nurse now, severe chest pain) are always present. A static menu is available as the A/B baseline.

Exact selection counts (no EEG involved), static → risk-aware, 2 targets:

| Scenario | Turn me left | Left-leg reposition | Suction now | Water sips |
|---|---|---|---|---|
| A new patient | 11 → 11 | 14 → 15 | 8 → 6 | 11 → 12 |
| B mobility | 11 → **5** | 14 → **7** | 8 → 6 | 11 → 12 |
| C respiratory | 11 → 11 | 14 → 15 | 8 → **6** | 11 → 12 |

The +1 cases are the deliberate cost of always-visible urgent routes in that category.

## A/B/C/D benchmark (SIMULATED)

**Question:** can context-aware, risk-aware, signal-adaptive navigation increase *correctly confirmed
messages per minute* (CMPM) compared with a conventional fixed-menu EEG speller?

```powershell
cd backend
.\.venv\Scripts\python scripts\run_benchmark.py              # 5 replicates, ~2 min first run, ~25 s cached
.\.venv\Scripts\python scripts\run_benchmark.py --quick      # smoke run
```
Output: `backend/reports/benchmark_results.json` (schema 1.0, served by `GET /api/benchmark`) and
`backend/reports/benchmark_summary.md`. Fixed seeds: same arguments → same numbers.

**Method.** Decoder outcomes come from the real FBCCA/dynamic-stopping code run on synthetic EEG at
three SNR levels plus a *degrading* scenario (high → low half-way). Calibration uses the live
protocol (5 trials/target + 8 rest) on one seed; outcomes are drawn from **held-out** test trials on
another. A perfect-intent simulated patient walks the real `Navigator`, which includes recovery via
"Go back". Every condition gets the same tasks, signal schedule and random seed. Sessions:
5 replicates × 3 profiles × 2 task mixes × 4 scenarios, each 14 messages + 6 urgent probes + 3 min of
no-control. 95% CIs are percentile bootstrap over sessions; differences are paired.

| | Condition |
|---|---|
| A*n* | fixed menu, *n* targets, fixed-window forced-choice SSVEP (window by ITR on calibration), final confirmation only |
| B*n* | A + context-aware semantic routing (shortcuts + urgent routes) |
| C | B + signal-adaptive *n* chosen from calibration (`adaptive.choose_n`) |
| D | full NeuroSteno: dynamic stopping + abstention + no-control rejection, explicit YES/NO on every step, live re-adaptation of *n* |
| D+ | D with confidence-gated step confirmation (`policy: gated`): YES/NO skipped only when margin ≥ 1.5× calibrated threshold; final message always confirmed |

A* = best fixed-menu *n* **chosen in hindsight** (an oracle that favours the baseline; it was A5 in every cell).

**Results (simulated, ratio of CMPM vs A\*, mean [95% CI], 15 paired sessions per cell, matched task mix):**

| Scenario | B (context) | D (explicit) | D+ (gated) |
|---|---|---|---|
| high SNR | 1.22 [1.14, 1.30] | 0.98 [0.90, 1.07] | **1.28 [1.19, 1.38]** |
| medium SNR | 1.23 [1.14, 1.33] | 0.72 [0.64, 0.80] | 0.85 [0.74, 0.96] |
| low SNR | 1.31 [1.20, 1.44] | 0.32 [0.22, 0.43] | 0.60 [0.46, 0.76] |
| degrading | 1.44 [1.27, 1.63] | 1.33 [0.88, 1.86] | 1.45 [1.02, 2.04] |

Safety, pooled over all sessions:

| | error rate (wrong messages confirmed) | messages sent during no-control | selections during no-control |
|---|---|---|---|
| A5 / B5 / C | 2.6–2.9 % (11 % for A5 in *degrading*) | **about 50 per hour** (49.7–53.9) | 10.7–10.9 per min |
| D | 0 % | 0 per hour | 0.06 per min |
| D+ | 0.05 % | 0.3 per hour | 0.7 per min |

Urgent access: urgent messages need 5.1 / 3.7 / 2.5 selections from Home with context routing,
versus 9.3 / 7.7 / 4.5 on the static menu (*n* = 2 / 3 / 5, exact counts).

**What this does and does not show (simulation only):**
* Context-aware routing (B) gave the most consistent gain: +7 % to +44 % CMPM in every scenario. The
  gain is smaller on the *uniform* task mix (+7 % to +20 %), where context does not match the need.
* Forced-choice fixed menus keep selecting when the patient attends nothing. In these runs they confirmed
  about one unintended message per minute of no-control. D/D+ nearly eliminate this; that is the main
  safety benefit of abstention, no-control rejection and confirmation.
* Safety costs throughput. D (explicit YES/NO every step) is slower than A* except in the degrading
  scenario. D+ beats A* only at high SNR and under degradation, and is slower at medium and low SNR,
  where dynamic stopping abstains instead of guessing.
* Signal-adaptive *n* (C) did not beat the best fixed *n* here. 5 targets was best whenever the signal
  was constant, and at low SNR the 5-trial calibration occasionally picked 3 and lost. Its value is
  avoiding the wrong *n* when the best one is not known in advance; the oracle A* hides that.
* `adaptive.py`'s analytic model over-predicts simulated CMPM by about 25–35 %, because it is fed
  optimistic in-sample calibration. It is used to rank *n*, not to report throughput.
* No real-EEG results exist yet; real sessions will be reported separately (origin `real`).

## Windows installation

Prerequisites: Windows 10/11, **Python 3.12** (`py -3.12`), Node.js 20+, Unicorn Suite Hybrid
Black with an activated Unicorn Python API license, and the headset paired in Windows Bluetooth.

```powershell
cd C:\Users\sarth\neurosteno\backend
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
cd ..\frontend
npm install
```
The first SciPy import after install can take 30–90 s while Windows Defender scans the new DLLs.

## Launch

```powershell
# Terminal 1
.\start-backend.ps1          # http://127.0.0.1:8000
# Terminal 2 (dev UI)
.\start-frontend.ps1         # http://localhost:5173
```
Or build once (`cd frontend; npm run build`) and use only the backend at http://127.0.0.1:8000.
Patient-only fullscreen view: **⛶ Patient full screen** in the top bar (or append `#patient` to the URL); **◐ High contrast** next to it.
Launch, preflight and recovery steps for the demo: **OPERATOR_CHECKLIST.md**.

## Real-EEG signal check (electrodes + alpha)

After the hardware test streams, check that the data is **brain signal**, not just bytes:

```powershell
cd C:\Users\sarth\neurosteno\backend
.\.venv\Scripts\python scripts\real_signal_check.py --serial UN-2024.09.36
```
1. **Electrode fitting** (60 s, Ctrl+C to skip): a live per-electrode table showing contact verdict,
   1–40 Hz amplitude, drift, |correlation| with other channels, mains, and EMG, plus a fix for each problem.
   UnicornPy exposes no impedance, so contact is estimated heuristically from the signal itself.
2. **Alpha check** (3 × eyes open 10 s / eyes closed 10 s, beep cues): occipital alpha must rise with
   eyes closed (ratio ≥ 1.5, epoch AUC ≥ 0.8, in every cycle) before we report `eeg_confirmed`.
   Identical channels (ear clips off) are reported as `invalid`, never as EEG.
3. Saves `recordings/real_signal_check_<time>.npz` (+ `.json` report) for REPLAY mode.

`--sim` dry-runs the flow on synthetic data (clearly labelled). The same checks run live in the
backend (`electrode_check` in the state, `start_alpha_check` command).

## Hardware test (real headset)

Close Unicorn Recorder and other g.tec apps that might hold the headset (the Unicorn Suite
launcher did not block acquisition in our test). Wear the headset, attach both ear clips, then:

```powershell
cd C:\Users\sarth\neurosteno\backend
.\.venv\Scripts\python scripts\unicorn_hardware_check.py --serial UN-2024.09.36 --seconds 10
```
The script checks the adapter, `IsDeviceLibraryLoadable` (raises a SystemError in this UnicornPy
build; the script reports it and continues), `GetBluetoothAdapterInfo`, `GetAvailableDevices`, connect,
channel configuration, and N seconds of acquisition with counter-based drop detection. It then checks
**signal plausibility** (amplitude, DC drift, inter-channel correlation) and saves
`recordings/hardware_check_<serial>.npz` (origin = real). Add `--scan` to search for unpaired headsets.

Troubleshooting: *connect failed* → close other g.tec apps, power-cycle the headset, check the
license in Unicorn Suite. *Dropped samples* → move within 2 m and disable Bluetooth power saving
(Device Manager → adapter → Power Management). *All channels identical* → reference/ground ear clips.
The Intel adapter is not g.tec's recommended adapter but worked for streaming. On this PC the CSR dongle
currently shows a driver error in Device Manager.

## Tests

```powershell
cd backend;  .\.venv\Scripts\python -m pytest -q      # 79 tests, ~50 s
cd frontend; npm test                                   # 112 tests
```
Covered: catalog quality (≥1,000 unique phrases), reachability of every phrase for 2/3/5 targets ×
3 profiles × static/adaptive, slot packing never loops, Back/Home/Undo, wrong prediction → rejection
on the same screen, final-message confirmation, no-control produces no accepted selection,
Bluetooth dropout / bad electrodes pause communication, provisional policy, adaptive target count,
emergency demo control, origin-separated metrics, FBCCA, calibration, quality, blink refractory,
record/replay round trip, API + WebSocket smoke, and UI rendering (home, highlight, review, modes,
origin frames / calibration chip / sent-origin tags, NeuroRoute strip, ARIA roles, judge Demo tab, electrode map, eyes open/closed check,
fixed-menu vs NeuroSteno comparison with provenance tags, live A/B runner, multimodal input UI
(EEG-only default, gesture calibration, policy, per-patient benchmark) behind the proposed v2.0 contract).

## Known limitations

* No real-EEG SSVEP accuracy yet (headset not on scalp during bring-up). All accuracy and timing numbers are synthetic.
* Default (uncalibrated) thresholds come from the simulator. REAL mode requires calibration.
* Simulated no-control: about 1 false *highlight* per 26 windows and 0 false *acceptances* in tests. A false
  "CHANGE" during an unattended final review discards the draft (safe direction, never sends).
* Urgent routes cost about +1 selection for other requests in their categories (visible in the Routing tab).
* Blink input is experimental and off by default. Double-blink vs. eye closure is not distinguished.
* Flicker at 6–12 Hz can trigger photosensitive seizures. Stimulation is off until explicitly enabled.
* HELP NOW is a demo control only. Nonresponse never triggers an emergency.
* c-VEP mode and the confusion-matrix-aware NeuroRoute optimizer are not implemented yet. The A–D benchmark is simulation-only.
