# NeuroSteno: operator checklist (launch + recovery)

For the person driving the judge demo. The talking points are in `DEMO_SCRIPT.md`; this file covers
getting the system up and getting it back when something breaks. **Nothing here resets or edits
patient history from the UI.** The only history step is a manual file move, with the backend stopped.

## T-30 min: launch

1. **Close** Unicorn Recorder and other g.tec apps (only needed if a headset is used).
2. **Saved simulated history (manual).** With the backend **stopped**, move
   `backend\data\runtime\history_*_simulated.json` to a backup folder. Rehearsals add to it and would
   skew the live A/B. Optionally also move `backend\data\runtime\metrics.jsonl`: the comparison's
   "MEASURED · saved runs on this machine" lines include every earlier rehearsal. The EXACT counts and the
   benchmark always use the seed profiles, so they are unaffected.
3. Terminal 1: `.\start-backend.ps1` and wait for `Uvicorn running on http://127.0.0.1:8000`.
   (The first SciPy import after install can take 30–90 s.)
4. Terminal 2: `.\start-frontend.ps1`, then open **http://localhost:5173** in Chrome or Edge.
5. Operator panel → **Demo** tab → **Preflight** must read **GO** (or **CHECK** with only the manual
   history item, after you tick it):
   - Backend live · Snapshot complete · EEG source labelled · Signal / communication · Routing plan ·
     Comparison data · Simulated benchmark · Clean starting state · Head gestures off.
6. Press **Reset demo**, then Demo step 1 **Switch to Simulated**: the striped purple frame and the
   **SIMULATED EEG · synthetic signals** badge must be visible.
7. Projector: browser zoom so the five Home cards fit; optional **◐ High contrast**. Use
   **⛶ Patient full screen** only for the patient-only view; exit it with the small
   *Exit patient mode* button at the bottom right (or Esc, then the button).
8. Flicker: leave **off** for the simulated demo (it is not needed and avoids photosensitivity risk).
   If a real headset is used, read and tick the photosensitivity warning first.

## During the demo: what the screen tells you

| You see | Meaning | Action |
|---|---|---|
| Red banner "Backend disconnected: reconnecting…" + patient screen **Connection lost** | WebSocket closed; nothing is read or selected; flicker paused | Check terminal 1. If it died, restart `start-backend.ps1`; the page reconnects by itself (no reload needed). |
| Banner "No fresh data from the backend: display paused" | Socket open but no state for > 2.5 s (backend hung or busy) | Wait 5 s (auto-reconnect after 6 s of silence). If it persists, restart the backend. |
| **Communication paused** (grey overlay) | Signal problem or simulated dropout; not an alarm | Simulation tab: *Restore Bluetooth*, un-mark bad electrodes; or Demo step "Failure handling" buttons. |
| Amber **⚠ Signal quality** line | Signal degraded but not yet paused | Same as above; explain it as a feature. |
| **"…: display error"** box | A UI panel failed to render; the rest keeps working | Click **Try again**; if it repeats, reload the page (backend state is unaffected). |
| Comparison shows **NOT MEASURED** / "benchmark not published" | `/api/benchmark` has no report | Honest state; say so. Do not quote numbers that are not on screen. |
| Live A/B: "**NOT comparable**: …" | The two arms did not run under identical conditions | Say so; rerun after **Reset demo**, or skip the live run and use the benchmark. |
| HELP NOW overlay | Demo control only | Caregiver: acknowledge. |

## Recovery recipes

- **Page blank / stuck on "Connecting to the backend…"**: is terminal 1 running? Is port 8000 free?
  (`Get-NetTCPConnection -LocalPort 8000`). Restart the backend and the page reconnects.
- **Port 5173 in use**: close the other terminal running Vite, or run `npm run build` once and use
  http://127.0.0.1:8000 (the backend serves the built UI).
- **Simulation feels off** (stuck autopilot, armed mistake, dropout left on): Demo tab → **Reset demo**.
  The live A/B also restarts the simulator for each arm.
- **Wrong patient / target count**: Reset demo (Alex, 5 targets, risk-aware routing, explicit confirmation).
- **Comparison view errors**: switch to *Patient + operator* and continue the script; the comparison
  is optional after the 30-second opening.

## Labels that must stay true

- SIMULATED / REPLAY / REAL are always shown by frame, badge and caption. Never describe simulated or
  replayed numbers as real-EEG results.
- Numbers are tagged **EXACT**, **ESTIMATE**, **BENCHMARK · SIMULATED**, **DEMO RUN**, **MEASURED** or
  **NOT MEASURED**. Quote them with their tag.
- Head gestures are **off** until the multimodal backend contract is confirmed and verified. The final
  SEND is always an EEG confirmation.
