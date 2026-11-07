"""Gesture-outcome pools: the REAL gesture detector run on SYNTHETIC head motion.

Mirrors benchmark/pool.py for EEG. For one synthetic motion profile:
  * calibration session (seed A) with the live protocol: neutral rest, cued nods/shakes/tilts,
    natural-movement block -> signal.gestures.calibrate_gestures() -> what the SYSTEM believes
    (usable flags, LOO sensitivity, held-out false activations, latency);
  * held-out test set (seed B): many cued gesture trials and a long stretch of natural movement,
    each run through the ONLINE detector (detect_stream) with the calibrated thresholds ->
    empirical outcomes the Monte-Carlo sessions sample from.
Nothing here describes a real patient.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..acquisition.simulated import SimulatedSource
from ..signal.gestures import KINDS, GestureConfig, calibrate_gestures, detect_stream

FS = 250.0
ACC = slice(8, 11)
GYR = slice(11, 14)

# Live protocol (engine.start_gesture_calibration uses the same numbers).
NEUTRAL_S = 20.0
CUE_WINDOW_S = {"nod": 4.0, "shake": 4.0, "tilt": 5.0}
CUE_LEAD_S = 0.5           # cue shown this long into the window
NATURAL_S = 30.0
CUES_PER_KIND = 6


def protocol_seconds(cues_per_kind: int = CUES_PER_KIND) -> float:
    return NEUTRAL_S + cues_per_kind * sum(CUE_WINDOW_S.values()) + NATURAL_S


def _chunk(src: SimulatedSource, seconds: float) -> dict:
    x = src.generate(int(seconds * FS))
    return {"accel_g": x[:, ACC].astype(np.float64), "gyro_dps": x[:, GYR].astype(np.float64)}


def record_calibration(src: SimulatedSource, rng: np.random.Generator, cues_per_kind: int = CUES_PER_KIND) -> tuple:
    """Run the calibration protocol on a simulated source. Neutral: incidental motion of the profile
    is suppressed only in the sense that the patient is asked to stay still (the profile's
    involuntary movement still happens)."""
    neutral = _chunk(src, NEUTRAL_S)
    plan = [k for k in KINDS for _ in range(cues_per_kind)]
    rng.shuffle(plan)
    cued = []
    for kind in plan:
        pre = _chunk(src, CUE_LEAD_S)
        src.perform_gesture(kind)
        rest = _chunk(src, CUE_WINDOW_S[kind] - CUE_LEAD_S)
        cued.append({"kind": kind, "accel_g": np.vstack([pre["accel_g"], rest["accel_g"]]),
                     "gyro_dps": np.vstack([pre["gyro_dps"], rest["gyro_dps"]]), "cue_s": CUE_LEAD_S})
    natural = [_chunk(src, NATURAL_S)]
    return neutral, cued, natural


@dataclass
class GesturePool:
    profile: str
    calibration: dict
    config: GestureConfig
    # held-out cued trials: per cued kind, list of (detected kind | None, latency_s | None)
    cued: dict = field(default_factory=dict)
    # held-out natural movement: accepted false activations per kind and exposure
    false_events: dict = field(default_factory=dict)
    exposure_s: float = 0.0

    def sample_cued(self, rng: np.random.Generator, kind: str) -> tuple[str | None, float | None]:
        outs = self.cued[kind]
        return outs[int(rng.integers(len(outs)))]

    def rate_per_s(self, kind: str) -> float:
        return self.false_events.get(kind, 0) / self.exposure_s if self.exposure_s else 0.0

    def summary(self) -> dict:
        test = {}
        for k, outs in self.cued.items():
            n = len(outs)
            hit = [lat for d, lat in outs if d == k]
            test[k] = {"trials": n, "sensitivity": round(len(hit) / n, 3) if n else None,
                       "confused": sum(1 for d, _ in outs if d is not None and d != k),
                       "mean_latency_s": round(float(np.mean(hit)), 2) if hit else None}
        return {"profile": self.profile, "calibration_usable": self.calibration.get("usable"),
                "calibration_estimates": self.calibration.get("estimates"), "test_cued": test,
                "test_false_activations_per_hour": {k: round(3600 * self.rate_per_s(k), 2) for k in KINDS},
                "test_natural_exposure_s": round(self.exposure_s, 1)}


def build_gesture_pool(profile: str, seed: int, test_per_kind: int = 40, natural_test_s: float = 900.0,
                       cues_per_kind: int = CUES_PER_KIND) -> GesturePool:
    # ---- calibration (seed A) -------------------------------------------------------
    src = SimulatedSource(seed=seed)
    src.set_motion_profile(profile)
    rng = np.random.default_rng(seed)
    neutral, cued, natural = record_calibration(src, rng, cues_per_kind)
    cal = calibrate_gestures(neutral, cued, natural, FS, origin="simulated")
    cfg = GestureConfig.from_dict(cal["config"])
    # The benchmark measures what happens IF gestures are used, so held-out detection runs with every
    # fitted threshold, including gestures the calibration marked unusable (the system's own view
    # stays in pool.calibration["usable"]; the optimiser only ever sees that).
    cfg.enabled = True
    cfg.usable = {k: cfg.thresholds.get(k) is not None for k in KINDS}
    pool = GesturePool(profile, cal, cfg)
    if not cal.get("ok"):
        pool.cued = {k: [(None, None)] for k in KINDS}
        pool.exposure_s = 1.0
        return pool
    # ---- held-out test (seed B) -----------------------------------------------------
    mount = src.head.mount
    src = SimulatedSource(seed=seed + 500)
    src.set_motion_profile(profile)
    src.head.mount = mount  # same session, same headset placement (only the movements are new)
    for kind in KINDS:
        outs = []
        for _ in range(test_per_kind):
            pre = _chunk(src, CUE_LEAD_S + 1.0)       # includes a gap after the previous trial
            src.perform_gesture(kind)
            win = _chunk(src, CUE_WINDOW_S[kind] - CUE_LEAD_S)
            acc = np.vstack([pre["accel_g"], win["accel_g"]])
            gyr = np.vstack([pre["gyro_dps"], win["gyro_dps"]])
            cue_t = len(pre["gyro_dps"]) / FS
            evs = [e for e in detect_stream(acc, gyr, FS, cfg) if e["accepted"] and e["t"] >= cue_t - 0.2]
            if evs:
                outs.append((evs[0]["kind"], round(evs[0]["t"] - cue_t, 3)))
            else:
                outs.append((None, None))
        pool.cued[kind] = outs
    nat = _chunk(src, natural_test_s)
    evs = [e for e in detect_stream(nat["accel_g"], nat["gyro_dps"], FS, cfg) if e["accepted"]]
    pool.false_events = {k: sum(1 for e in evs if e["kind"] == k) for k in KINDS}
    pool.exposure_s = natural_test_s
    return pool
