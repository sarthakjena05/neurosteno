# HANDOFF — Agent A (EEG/backend), branch `feature/eeg-backend`

## LATEST STATUS (read first)
- Last tested commit: `331358b`. Full backend suite: **119 passed, 1 xfailed**. The xfail is the strict
  `test_false_nods_from_coughs_and_swallows_rejected`, a known weakness.
- Contract v1.4 (accepts Agent B's v2.0 `input`/`policy` shapes) is posted and verified:
  https://github.com/sarthakjena05/neurosteno/issues/1#issuecomment-6712916615
- **Known failure: cough-like jerks are accepted as nods** (4-13/20 at 2-5 deg, 14-17/20 at 6-15 deg).
- **UNTESTED fix saved, not applied:** `backend/wip/jerk_fix_untested.patch`. It adds angular excursion and
  tempo features to `signal/gestures.py`.
  - Probe, seed 5: small jerks 0/20, large jerks 3/20, nods 27/30.
  - Seeds 12/21/22: nods 19-20/20.
  - Seed 11: the calibration already marked the nod unusable (LOO 0.667, "multi-axis" rejections); check whether
    that happens without the patch too.
- Next steps:
  1. `git apply backend/wip/jerk_fix_untested.patch`.
  2. Run the probe scripts (jerk/nod per seed) and the full suite.
  3. Remove the xfail if the jerk test passes; then commit and push.
  4. Re-run `scripts/run_multimodal_benchmark.py` to `backend/reports/`.
  5. Investigate the M1/M2 idle-false-send anomaly.
  6. Update the README.
  7. Answer B5 (history isolation) on issue #1.
- Items 2 and 4 under "Known bugs" below are DONE in 331358b: the test bugs are fixed and the suite has been run.


Last updated: 2026-11-07. Owner role: Agent A (backend). Agent B owns `frontend/**` on `feature/neuroroute-ui`.
Coordination: GitHub issue #1 (sarthakjena05/neurosteno). Never merge or push to `main`. Never edit `frontend/**` or `DEMO_SCRIPT.md`.
**No physical hardware validation until the user confirms the headset is available.**

## Current assignment (multimodal EEG + IMU)

Goal: maximise *correctly confirmed messages per minute* (CMPM) by jointly choosing EEG-only / hybrid / motion-assisted,
n = 2/3/5, routing (context/static), confirmation (explicit/gated). EEG-only must always work; no patient needs IMU.
Gestures never send, never confirm the final review, never trigger urgent messages or emergencies.

## Status by task

| Task | Status | Where |
|---|---|---|
| A1 IMU in acquisition | DONE | `acquisition/base.py` (`imu_layout`, `read_motion`), `acquisition/unicorn.py` (`normalize_unit`, `verify_channel_map` → `status.extra.channel_map`), `acquisition/motion_sim.py` (synthetic head kinematics, profiles, faults), `acquisition/simulated.py` |
| A2 gesture detection | DONE, **known weakness (below)** | `signal/gestures.py` (calibration, online detector, IMU health), disabled by default |
| A3 capability profile | DONE | `capability.py` (Beta/Gamma posteriors, simulated vs measured_real claim labels) |
| A4 policy optimiser | DONE | `policy.py` (exact absorbing-chain model, closed form + reference matrix solver, posterior q10 score, constraints) |
| A4 live engine integration | DONE (WIP-tested) | `interaction.py` (mixin), hooks in `engine.py`, `state.py` (additive keys), `api.py` (commands/endpoints), `config.py` |
| A5 benchmark | DONE (full run complete) | `benchmark/multimodal.py`, `scripts/run_multimodal_benchmark.py`, results `backend/reports/multimodal_results.json`, `multimodal_summary.md` |
| A6 contract post | v1.4 posted to issue #1 at checkpoint | see "Contract v1.4" in the issue |
| A7 tests | **IN PROGRESS** | `backend/tests/test_multimodal.py` |
| README update | NOT DONE | add multimodal section, results, limitations, test count |

## Known bugs / open issues (fix in this order)

1. **Nod-like coughs/jerks accepted as nods (A2, safety).** Probe (`test_false_nods_from_coughs_and_swallows_rejected`):
   20 quick pitch jerks (0.25–0.5 s) → 4–13/20 accepted at 2–5°, 14–17/20 at 6–15°. The 3 Hz low-pass stretches
   them to 0.6–1.0 s, overlapping cued nods (0.8–1.9 s), so duration doesn't separate them; the 30 s natural block
   rarely contains a jerk, so `amp_dps` stays at its floor.
   Planned fix in `signal/gestures.py`: add per-kind **angular excursion** (deg; integrate `w @ u` / FS_D, peak-to-peak)
   and **tempo** (= peak |w·u| / excursion ≈ π/cycle) to `seg_features`; fit `angle_min = max(3, 0.5*min cued)` and
   `tempo_max = 1.25*max cued` in `_fit`; check them in `classify`. Then re-run the jerk probe
   (scratchpad script reproduced in the test). Large fast jerks of nod size may stay indistinguishable: report that
   honestly. Mitigations already in place: gestures act only inside a prompted step window, never send, and the final
   review is always an EEG YES.
2. Test fixes still needed in `tests/test_multimodal.py` (test bugs, not engine bugs):
   - `normalize_unit` should map ASCII `"uV"` → `"µV"` (simulator uses "uV"; channel-map test fails on `unit_ok`).
     Edit the last line of `normalize_unit` in `acquisition/unicorn.py`.
   - `test_read_motion_is_sample_aligned_with_eeg`: `src.generate()` doesn't fill the ring buffer; use
     `make_engine(...)` and read from `eng.source` after ticking (or poll with a FakeClock).
   - Message tests assert `m["correct"] is True`, but the first task starts before the autopilot is set, so
     `correct` is None. Assert `m["final"] == target` instead.
   - `test_tilt_goes_back_only_in_motion_assisted` fails for motion_assisted. Investigate whether the tilt branch in
     `InteractionMixin._handle_gestures` is reached (phase must be IDLE/EVIDENCE_COLLECTION, `len(nav.stack) > 1`,
     `pending_leaf is None`, `gesture_cfg.active("tilt")` and effective mode `motion_assisted`). Likely
     `tilt` not usable or the effective mode is not motion_assisted. Print `eng.interaction` and `eng.gesture_cfg.usable`.
3. **Benchmark anomaly to investigate:** M1/M2 (fixed-menu / context EEG-only) show 35–63 idle false sends/hour in
   `multimodal_summary.md`, while M3–M5 show 0. Check the idle phase of `MMSession` for M1/M2 (probably no
   rest/no-control gate in those conditions). Report it, don't hide it.
4. The existing test suite (`test_engine.py` etc.) has **not been re-run** since the engine integration. Run it first.
5. In live auto mode with only assumed EEG reliability (no calibration), the optimiser reports "No policy satisfies every
   safety constraint; using the least-violating EEG-only policy". That's expected, but the UI should show it.

## Benchmark headline (SIMULATED, 5 replicates; see `backend/reports/multimodal_summary.md`)

- Always-on hybrid (M4) vs adaptive EEG-only (M3): **worse** for no_head_control (×0.66–0.74, all SNRs),
  unreliable/high, high_motion/medium+high. **Better only for accurate/low SNR (×1.84)**. Elsewhere no significant
  difference.
- The optimised policy (M5) is never significantly better than M3. It is worse in several high-SNR cells
  (policy-selection regret).
- The best fixed multimodal policy vs the best fixed EEG-only policy: no significant difference in most cells. Worse
  for no_head_control/high, unreliable/high and high_motion/high.
- Conclusion so far: hybrid helps only when EEG is weak and head control is accurate. It hurts when gestures are
  unreliable, impossible or noisy. EEG-only remains the default.

## Exact resume commands

```powershell
cd C:\path\to\neurosteno
git fetch origin; git checkout feature/eeg-backend; git pull
cd backend
python -m venv .venv; .venv\Scripts\pip install -r requirements.txt   # if new machine
.venv\Scripts\python -m pytest tests/test_multimodal.py -q            # A7 tests (some known failures, above)
.venv\Scripts\python -m pytest -q                                     # full suite
.venv\Scripts\python scripts/run_multimodal_benchmark.py --quick      # smoke (full run: no flags, ~3-4 min with cache)
```
The pool cache lives in `backend/data/runtime/pool_cache` (gitignored). It is rebuilt automatically on a new machine and
the first full run takes longer.

## Next steps

1. Fix item 2 (test bugs), run the full suite, and commit.
2. Fix item 1 (jerk rejection), re-run `test_multimodal.py` and the gesture benchmark (`--quick`), then commit.
3. Investigate item 3, then re-run the full benchmark into `backend/reports/`.
4. README: multimodal section, verdict table, limitations, test count.
5. Post the final contract and results to issue #1, checking Agent B's comments first.
6. Hardware: only after the user confirms the headset. `scripts/real_signal_check.py` first, then a real gesture calibration
   (`start_gesture_calibration`), which yields `claim = measured_real`.
