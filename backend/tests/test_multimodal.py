"""Optional IMU / gesture / multimodal-policy layer (A1-A4, A7).

Everything here is SIMULATED (synthetic head kinematics + synthetic EEG). The tests check safety
properties and fallbacks, not that the detector works on a real patient."""

import time
from types import SimpleNamespace

import numpy as np
import pytest

from neurosteno.acquisition.motion_sim import AXES, _Event
from neurosteno.acquisition.simulated import SimulatedSource
from neurosteno.acquisition.unicorn import normalize_unit, verify_channel_map
from neurosteno.benchmark.gesture_pool import _chunk, record_calibration
from neurosteno.capability import (Binom, CapabilityProfile, GestureCap, PoissonRate, claim_for,
                                   eeg_from_calibration)
from neurosteno.engine import Phase
from neurosteno.headless import make_engine, produce_message, run_until, wait_ready
from neurosteno.policy import (Policy, Timing, cycle_model, draw_params, optimise, solve_message,
                               solve_message_matrix)
from neurosteno.signal.gestures import GestureConfig, calibrate_gestures, detect_stream, imu_health
from neurosteno.state import snapshot

FS = 250.0


# =====================================================================================
# A1: channel map + motion samples
# =====================================================================================
def _unicorn_consts():
    return SimpleNamespace(EEGConfigIndex=0, EEGChannelsCount=8, AccelerometerConfigIndex=8,
                           AccelerometerChannelsCount=3, GyroscopeConfigIndex=11, GyroscopeChannelsCount=3,
                           BatteryConfigIndex=14, CounterConfigIndex=15, ValidationConfigIndex=16)


def test_channel_map_verified_against_library_constants():
    src = SimulatedSource(seed=0)
    r = verify_channel_map(src.channels, _unicorn_consts())
    assert r["verified"] and r["imu_present"]
    assert all(c["ok"] for c in r["checks"] if c["ok"] is not None)


def test_channel_map_without_imu_is_reported_not_fatal():
    chans = [c for c in SimulatedSource(seed=0).channels if c.kind not in ("accel", "gyro")]
    r = verify_channel_map(chans, None)
    assert r["imu_present"] is False
    assert "note" in r


def test_mojibake_units_are_normalised():
    assert normalize_unit("Âµ" + "V") == "µV"
    assert normalize_unit("deg/s") == "deg/s"


def test_read_motion_is_sample_aligned_with_eeg():
    src = SimulatedSource(seed=1)
    src.generate(500)
    m = src.read_motion(100, 300)
    assert m["accel_g"].shape == (200, 3) and m["gyro_dps"].shape == (200, 3)
    assert m["sample"][0] == 100 and m["sample"][-1] == 299
    assert np.allclose(m["t"], m["sample"] / FS)
    # gravity ~1 g at rest
    assert 0.9 < np.median(np.linalg.norm(m["accel_g"], axis=1)) < 1.1


class _NoImuSource(SimulatedSource):
    def imu_layout(self):
        return None


def test_missing_imu_channels_keep_eeg_only_working():
    eng, clock = make_engine(n=2, routing="static", seed=21)
    src = _NoImuSource(seed=21, clock=clock)
    eng.set_source(src)
    assert src.read_motion(0, 10) is None
    assert wait_ready(eng, clock)
    run_until(eng, clock, lambda: False, max_s=1)
    assert eng.imu["available"] is False and "no accelerometer" in eng.imu["reason"]
    eng.set_interaction_mode("hybrid")
    assert eng.interaction["effective"] == "eeg_only"
    with pytest.raises(ValueError):
        eng.start_gesture_calibration()
    m = produce_message(eng, clock, "comfort/temperature/too-cold", max_s=600)
    assert m is not None and m["correct"] is True


# =====================================================================================
# A2: gesture detector
# =====================================================================================
def _calibrated(profile="accurate", seed=5):
    src = SimulatedSource(seed=seed)
    src.set_motion_profile(profile)
    neutral, cued, natural = record_calibration(src, np.random.default_rng(seed))
    res = calibrate_gestures(neutral, cued, natural, FS, origin="simulated")
    return src, res


@pytest.fixture(scope="module")
def accurate_cal():
    return _calibrated("accurate", 5)


def test_calibration_never_enables_gestures(accurate_cal):
    _, res = accurate_cal
    assert res["ok"]
    cfg = GestureConfig.from_dict(res["config"])
    assert cfg.enabled is False
    assert GestureConfig().enabled is False and not GestureConfig().calibrated
    assert "false_activations" in res["estimates"]["nod"]
    assert res["estimates"]["nod"]["false_activations"]["upper95_per_hour"] >= 0


def test_false_nods_from_coughs_and_swallows_rejected(accurate_cal):
    src, res = accurate_cal
    cfg = GestureConfig.from_dict(res["config"])
    cfg.enabled = True
    src.set_motion_profile("steady")
    t = src._generated / FS
    rng = np.random.default_rng(3)
    for i in range(20):  # quick nod-like pitch jerks (cough / swallow), 0.25-0.5 s, 6-15 deg
        ax = AXES["pitch"] + rng.normal(0, 0.3, 3)
        src.head.events.append(_Event(t + 2 + 3 * i, rng.uniform(0.25, 0.5), ax / np.linalg.norm(ax),
                                      rng.uniform(6, 15), "jerk"))
    ch = _chunk(src, 65)
    evs = detect_stream(ch["accel_g"], ch["gyro_dps"], FS, cfg)
    accepted = [e for e in evs if e["accepted"]]
    assert len(accepted) <= 1, accepted  # <= 5 % of 20 jerks


def test_incidental_posture_shifts_do_not_trigger(accurate_cal):
    src, res = accurate_cal
    cfg = GestureConfig.from_dict(res["config"])
    cfg.enabled = True
    src.set_motion_profile("steady")
    t = src._generated / FS
    rng = np.random.default_rng(4)
    for i in range(10):  # slow posture shift on a random axis, held and returned (not a tilt cue)
        ax = rng.standard_normal(3)
        src.head.events.append(_Event(t + 2 + 6 * i, rng.uniform(1.0, 3.0), ax / np.linalg.norm(ax),
                                      rng.uniform(3, 8), "shift"))
    ch = _chunk(src, 64)
    accepted = [e for e in detect_stream(ch["accel_g"], ch["gyro_dps"], FS, cfg) if e["accepted"]]
    assert len(accepted) <= 1, accepted


def test_true_gestures_detected_after_calibration(accurate_cal):
    src, res = accurate_cal
    cfg = GestureConfig.from_dict(res["config"])
    cfg.enabled = True
    src.set_motion_profile("accurate")
    hits = 0
    for kind in ("nod", "shake") * 5:
        src.perform_gesture(kind)
        ch = _chunk(src, 4.0)
        found = [e["kind"] for e in detect_stream(ch["accel_g"], ch["gyro_dps"], FS, cfg) if e["accepted"]]
        hits += found == [kind]
    assert hits >= 7


def test_imu_health_detects_nan_frozen_and_bad_gravity():
    a = np.tile([0, 0, 1.0], (250, 1)) + np.random.default_rng(0).normal(0, 0.004, (250, 3))
    g = np.random.default_rng(1).normal(0, 0.5, (250, 3))
    assert imu_health(a, g, FS)["ok"]
    assert not imu_health(np.full_like(a, np.nan), g, FS)["ok"]
    assert not imu_health(np.tile(a[0], (250, 1)), np.tile(g[0], (250, 1)), FS)["ok"]
    assert not imu_health(a * 3, g, FS)["ok"]


def test_poor_calibration_no_head_control_unusable():
    _, res = _calibrated("no_head_control", 9)
    assert not any(res.get("usable", {}).values())
    assert res.get("hybrid_possible") in (False, None)


# =====================================================================================
# Engine integration (A1-A4 live), all SIMULATED
# =====================================================================================
def _hybrid_engine(seed, profile="accurate", mode="hybrid", enable=True, n=3):
    eng, clock = make_engine(n=n, routing="static", seed=seed)
    assert wait_ready(eng, clock)
    eng.sim.set_motion_profile(profile)
    eng.start_gesture_calibration()
    assert run_until(eng, clock, lambda: eng.calib is None, max_s=400)
    if enable:
        eng.set_gestures_enabled(True)
    eng.set_interaction_mode(mode)
    assert wait_ready(eng, clock)
    run_until(eng, clock, lambda: False, max_s=1.5)
    return eng, clock


def _ev(eng, kind):
    s = eng.source.total_samples
    return {"sample": s, "t": s / FS, "kind": kind, "accepted": True, "reason": "test"}


def test_default_is_eeg_only_and_gestures_off():
    eng, clock = make_engine(n=2, routing="static", seed=30)
    assert wait_ready(eng, clock)
    run_until(eng, clock, lambda: False, max_s=1)
    st = snapshot(eng)
    assert st["interaction"]["requested"] == "eeg_only" and st["interaction"]["effective"] == "eeg_only"
    assert st["gestures"]["enabled"] is False and st["gestures"]["calibrated"] is False
    assert st["imu"]["available"] is True and st["imu"]["claim"] == claim_for("simulated")
    with pytest.raises(ValueError):
        eng.set_gestures_enabled(True)  # no calibration


def test_calibrated_but_not_enabled_stays_eeg_only():
    eng, clock = _hybrid_engine(31, enable=False)
    assert eng.gesture_cfg.calibrated and not eng.gesture_cfg.enabled
    assert eng.interaction["effective"] == "eeg_only"
    assert run_until(eng, clock, lambda: eng.phase == Phase.EVIDENCE_COLLECTION, max_s=10)


def test_hybrid_nod_confirms_steps_and_final_still_needs_eeg():
    eng, clock = _hybrid_engine(32)
    assert eng.interaction["effective"] == "hybrid"
    m = produce_message(eng, clock, "comfort/temperature/too-cold", max_s=600)
    assert m is not None and m["correct"] is True
    assert eng.gesture_live["nod"]["hits"] >= 1
    assert eng.sent[-1]["confirmed_by"] == "decoded final YES"
    assert eng.unconfirmed_send_blocked == 0 and eng.emergency is None


def test_gesture_during_final_review_cannot_send():
    eng, clock = _hybrid_engine(33)
    eng.sim_autopilot = "comfort/temperature/too-cold"
    assert run_until(eng, clock, lambda: eng.phase == Phase.MESSAGE_REVIEW, max_s=600)
    eng.sim_autopilot = None  # patient stops looking: EEG review keeps abstaining
    for _ in range(3):
        eng._handle_gestures([_ev(eng, "nod")], clock())
        run_until(eng, clock, lambda: False, max_s=3)
    assert eng.sent == [] and eng.phase == Phase.MESSAGE_REVIEW
    assert eng.gesture_blocked_final >= 3
    assert eng.unconfirmed_send_blocked == 0  # never even reached _send


def test_gestures_never_trigger_urgent_or_emergency():
    eng, clock = _hybrid_engine(34, mode="motion_assisted")
    for k in ("nod", "shake", "tilt") * 4:
        eng._handle_gestures([_ev(eng, k)], clock())
        run_until(eng, clock, lambda: False, max_s=2)
    assert eng.emergency is None
    assert all(not s.get("urgent") for s in eng.sent) and eng.sent == []


def test_conflicting_nod_and_shake_rejects_choice():
    eng, clock = _hybrid_engine(35)
    eng.sim_autopilot = "comfort/temperature/too-cold"
    assert run_until(eng, clock, lambda: eng.gesture_window_open(), max_s=120)
    eng._handle_gestures([_ev(eng, "nod"), _ev(eng, "shake")], clock())
    assert eng.phase == Phase.REJECTED and eng.feedback["type"] == "not_confirmed"
    assert eng.gesture_conflicts == 1


def test_shake_after_eeg_yes_withdraws_choice():
    eng, clock = _hybrid_engine(36)
    eng.sim_autopilot = "comfort/temperature/too-cold"
    assert run_until(eng, clock, lambda: eng.gesture_window_open(), max_s=120)
    eng.attempt = None
    eng._accept(provisional=False)  # EEG YES lands first
    eng._handle_gestures([_ev(eng, "shake")], clock())
    assert eng.phase == Phase.REJECTED and eng.feedback["type"] == "not_confirmed"
    assert eng.gesture_conflicts == 1


def test_unprompted_gesture_ignored_and_counted():
    eng, clock = _hybrid_engine(37)
    depth = len(eng.nav.stack)
    for _ in range(3):
        eng._handle_gestures([_ev(eng, "nod")], clock())
    assert len(eng.nav.stack) == depth and eng.highlight is None
    assert eng.gesture_live["nod"]["unprompted"] == 3


def test_gestures_ignored_while_communication_paused():
    eng, clock = _hybrid_engine(38)
    eng.sim_autopilot = "comfort/temperature/too-cold"
    assert run_until(eng, clock, lambda: eng.gesture_window_open(), max_s=120)
    eng._enter_comm_unavailable("test pause", needs_caregiver=False)
    eng._handle_gestures([_ev(eng, "nod")], clock())
    assert eng.phase == Phase.COMM_UNAVAILABLE
    assert eng.gesture_events[-1]["action"].startswith("ignored: communication paused")


def test_eeg_dropout_pauses_and_gestures_do_nothing():
    eng, clock = _hybrid_engine(39)
    eng.sim.disconnected = True
    time.sleep(1.2)  # stall detection uses the wall-clock age of the last sample block
    assert run_until(eng, clock, lambda: eng.phase == Phase.COMM_UNAVAILABLE, max_s=10)
    eng.sim.perform_gesture("nod")
    run_until(eng, clock, lambda: False, max_s=4)
    assert eng.phase == Phase.COMM_UNAVAILABLE and eng.imu["available"] is False
    assert eng.interaction["effective"] == "eeg_only"


@pytest.mark.parametrize("fault", ["nan", "frozen"])
def test_gesture_dropout_falls_back_to_eeg_only(fault):
    eng, clock = _hybrid_engine(40)
    eng.sim.head.fault = fault
    run_until(eng, clock, lambda: False, max_s=2)
    assert eng.imu["available"] is False
    assert eng.interaction["effective"] == "eeg_only" and "unavailable" in eng.interaction["reason"]
    m = produce_message(eng, clock, "comfort/temperature/too-cold", max_s=600)
    assert m is not None and m["correct"] is True  # EEG-only path still works
    eng.sim.head.fault = None
    eng.sim.head._frozen = None
    run_until(eng, clock, lambda: False, max_s=3)
    assert eng.interaction["effective"] == "hybrid"  # restored when the sensor recovers


def test_no_head_control_cannot_enable_and_eeg_only_works():
    eng, clock = _hybrid_engine(41, profile="no_head_control", enable=False)
    with pytest.raises(ValueError):
        eng.set_gestures_enabled(True)
    assert eng.interaction["effective"] == "eeg_only"
    m = produce_message(eng, clock, "comfort/temperature/too-cold", max_s=600)
    assert m is not None and m["correct"] is True


def test_gesture_calibration_bound_to_data_origin():
    eng, clock = _hybrid_engine(42)
    eng.gesture_cfg.origin = "real"  # pretend the calibration came from another origin
    assert not eng.gestures_ready()
    run_until(eng, clock, lambda: False, max_s=1)
    assert eng.interaction["effective"] == "eeg_only"
    with pytest.raises(ValueError):
        eng.set_gestures_enabled(True)


def test_tilt_goes_back_only_in_motion_assisted():
    for mode, expect_back in (("hybrid", False), ("motion_assisted", True)):
        eng, clock = _hybrid_engine(43, mode=mode)
        eng.sim_autopilot = "comfort/temperature/too-cold"
        assert run_until(eng, clock, lambda: len(eng.nav.stack) == 2 and eng.phase in (
            Phase.IDLE, Phase.EVIDENCE_COLLECTION), max_s=300)
        eng.sim_autopilot = None
        eng._handle_gestures([_ev(eng, "tilt")], clock())
        assert (len(eng.nav.stack) == 1) is expect_back, mode


def test_snapshot_is_additive():
    eng, clock = _hybrid_engine(44)
    st = snapshot(eng)
    for k in ("phase", "screen", "attempt", "highlight", "review", "comm", "quality", "decoder", "config",
              "routing", "blink", "sim"):
        assert k in st  # pre-existing contract unchanged
    for k in ("imu", "gestures", "interaction"):
        assert k in st
    assert st["sim"]["motion_profile"] == "accurate"


# =====================================================================================
# A3/A4: capability + policy
# =====================================================================================
def _cap(gestures=True, imu_ok=True, origin="simulated"):
    cap = CapabilityProfile(origin)
    for n in (2, 3, 5):
        cap.eeg[n] = eeg_from_calibration(n, None, origin, 4.0)
    if gestures:
        for k, sens in (("nod", 0.95), ("shake", 0.95), ("tilt", 0.9)):
            cap.gestures[k] = GestureCap(k, True, Binom(sens * 20, 20), Binom(0, 20), 1.2, 0.3,
                                         PoissonRate(0, 1800.0), origin)
    cap.imu = {"ok": imu_ok}
    return cap


@pytest.mark.parametrize("mode", ["eeg_only", "hybrid", "motion_assisted"])
@pytest.mark.parametrize("L", [1, 2, 4])
def test_closed_form_matches_full_chain(mode, L):
    cap = _cap()
    P = draw_params(cap, draws=16, seed=1)
    tm = Timing()
    pol = Policy(mode, 3, "static", False)
    cm = cycle_model(pol, P, tm, 2.5)
    t1, n1 = solve_message(L, cm, tm, 2.5, mode == "motion_assisted")
    t2, n2 = solve_message_matrix(L, cm, tm, 2.5, mode == "motion_assisted")
    ok = np.isfinite(t2)
    assert ok.any()
    assert np.allclose(t1[ok], t2[ok], rtol=1e-6) and np.allclose(n1[ok], n2[ok], rtol=1e-6, atol=1e-9)


def _ld():
    ld = {(r, n): {1: 0.2, 2: 0.5, 3: 0.3} for r in ("context", "static") for n in (2, 3, 5)}
    reach = {k: {"all_reachable": True} for k in ld}
    return ld, reach


def test_optimiser_without_gestures_is_eeg_only():
    ld, reach = _ld()
    out = optimise(_cap(gestures=False), ld, reach, draws=60)
    assert out["chosen"]["mode"] == "eeg_only"
    assert {c["mode"] for c in out["candidates"]} == {"eeg_only"}


def test_optimiser_falls_back_when_imu_unavailable():
    ld, reach = _ld()
    out = optimise(_cap(imu_ok=False), ld, reach, draws=60)
    assert out["chosen"]["mode"] == "eeg_only"
    assert {c["mode"] for c in out["candidates"]} == {"eeg_only"}


def test_optimiser_labels_claim_and_requires_evidence_for_multimodal():
    ld, reach = _ld()
    out = optimise(_cap(), ld, reach, draws=60)
    assert out["capability_claim"] == claim_for("simulated") != claim_for("real")
    ch = out["chosen"]
    if ch["mode"] != "eeg_only":
        assert ch["p_better_than_eeg_only"] >= 0.9 or not out["baseline_eeg_only"]["feasible"]
    assert out["baseline_eeg_only"]["mode"] == "eeg_only"
