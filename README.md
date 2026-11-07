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
| SIMULATED + REPLAY modes, full UI, phrase engine, routing | ✅ working; backend 50 tests, frontend 85 tests passing |
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
Patient-only fullscreen view: append `#patient` to the URL.

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
cd backend;  .\.venv\Scripts\python -m pytest -q      # 50 tests, ~40 s
cd frontend; npm test                                   # 85 tests
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
* c-VEP mode, the confusion-matrix-aware NeuroRoute optimizer, and the A–D benchmark are not implemented yet.
