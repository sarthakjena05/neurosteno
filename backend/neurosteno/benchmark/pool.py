"""Empirical decoder-outcome pools from synthetic EEG, decoded by the real FBCCA code.

For every target count n and signal level we generate cued trials (patient attends target j)
and rest trials (patient attends nothing) with the SimulatedSource, compute FBCCA scores at
every window length once, and store per trial:
  * the forced-choice prediction at each fixed window length (conventional fixed-window SSVEP),
  * the dynamic-stopping outcome (prediction + decision window, or abstention and whether it
    was the rest/no-control kind) under thresholds calibrated on a SEPARATE calibration set.

Calibration uses the same protocol as the live engine (5 cued trials per target + 4 rest
trials, 4 s stimulation, engine.calibrate()), so the estimates the adaptive policies see are
as noisy as they would be in a real session. Test outcomes come from a different seed.

Everything here is SYNTHETIC. These pools characterise the simulator, not a real user.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..acquisition.simulated import SimulatedSource
from ..signal.calibration import Trial, calibrate
from ..signal.fbcca import FBCCAConfig, StoppingConfig, fbcca_scores, margin_of

FS = 250.0
DECODING = ["Pz", "PO7", "Oz", "PO8"]
FREQS = {2: [7.5, 12.0], 3: [6.67, 8.57, 12.0], 5: [6.67, 7.5, 8.57, 10.0, 12.0]}
STIM_S = 4.0            # engine CalibSession.STIM_S
PAD_S = 0.5
FIXED_WINDOWS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5]


def stopper_replay(scores_by_window: list[np.ndarray], windows: list[float], st: StoppingConfig) -> tuple:
    """Pure re-implementation of DynamicStopper.evaluate over precomputed scores.
    Returns (target | None, window_s, rest_kind_abstain, margin / threshold at the decision).
    Checked against DynamicStopper in tests."""
    hist: list[int] = []
    k = st.require_consistent
    for sc, w in zip(scores_by_window, windows):
        if w + 1e-9 < st.min_window_s:
            continue
        target, margin = margin_of(sc)
        hist.append(target)
        thr = st.threshold_at(w)
        consistent = len(hist) >= k and len(set(hist[-k:])) == 1
        strong = float(sc[target]) >= st.rest_at(w)
        if margin >= thr and consistent and strong:
            return target, w, False, margin / max(thr, 1e-9)
        if w + 1e-9 >= st.max_window_s:
            return None, w, not strong, margin / max(thr, 1e-9)
    return None, windows[-1], False, 0.0


def _trials(src: SimulatedSource, freqs: list[float], labels: list, cols: list[int], length_s: float) -> list[Trial]:
    out = []
    lat = FBCCAConfig().latency_s
    for lab in labels:
        src.set_gaze(None)
        pad = src.generate(int(PAD_S * FS))[:, cols]
        src.set_gaze(None if lab is None else freqs[lab])
        x = src.generate(int((lat + length_s) * FS))[:, cols]
        src.set_gaze(None)
        src.generate(int(0.3 * FS))  # gap between trials
        out.append(Trial(x[int(lat * FS):].astype(np.float64), lab, pad.astype(np.float64)))
    return out


@dataclass
class Pool:
    n: int
    level: str
    ssvep_uv: float
    windows: list[float]
    stopping: StoppingConfig
    calibration: dict                     # engine-style calibration result (estimates)
    calib_gate: dict                      # {factor: (fraction above gate, accuracy above gate)} on calibration
    fixed_calib_acc: dict[float, float]   # forced-choice accuracy per window on the calibration set
    # test outcomes
    label: np.ndarray                     # [T] target index, -1 = rest
    fixed_pred: np.ndarray                # [T, len(FIXED_WINDOWS)]
    dyn_pred: np.ndarray                  # [T] -1 = abstain
    dyn_window: np.ndarray                # [T]
    dyn_rest_abstain: np.ndarray          # [T] bool
    dyn_margin_ratio: np.ndarray | None = None  # [T] margin / threshold at the decision
    scores: np.ndarray | None = None      # [T, len(windows), n] FBCCA scores for subset decoding
    dyn_windows: list = field(default_factory=list)
    stopping_step: StoppingConfig | None = None   # calibrated at STEP_TARGET_ACC (choose + explicit confirm)
    calibration_step: dict | None = None
    by_label: dict = field(default_factory=dict)

    def __post_init__(self):
        self.by_label = {int(l): np.flatnonzero(self.label == l) for l in np.unique(self.label)}
        self._sub_cache: dict = {}

    def decode_subset(self, i: int, k: int, fixed_window: float | None, op: str = "strict") -> tuple:
        """Outcome of trial i when the screen shows only the first k targets (engine behaviour:
        FBCCA over those k frequencies, thresholds calibrated for the n-target set).
        op: "strict" (target accuracy 0.9) or "step" (STEP_TARGET_ACC, used only for choose
        steps that an explicit YES/NO confirmation follows)."""
        if op == "step" and fixed_window is None:
            key = (i, k, "step")
            if key not in self._sub_cache:
                sc = self.scores[i, :, :k]
                widx = {w: j for j, w in enumerate(self.windows)}
                self._sub_cache[key] = stopper_replay([sc[widx[w]] for w in self.dyn_windows], self.dyn_windows,
                                                      self.stopping_step)
            return self._sub_cache[key]
        if k == self.n:
            if fixed_window is not None:
                return int(self.fixed_pred[i, FIXED_WINDOWS.index(fixed_window)]), fixed_window, False, 0.0
            p = int(self.dyn_pred[i])
            return ((None if p < 0 else p), float(self.dyn_window[i]), bool(self.dyn_rest_abstain[i]),
                    float(self.dyn_margin_ratio[i]))
        key = (i, k, fixed_window)
        if key not in self._sub_cache:
            sc = self.scores[i, :, :k]
            widx = {w: j for j, w in enumerate(self.windows)}
            if fixed_window is not None:
                self._sub_cache[key] = (int(np.argmax(sc[widx[fixed_window]])), fixed_window, False, 0.0)
            else:
                self._sub_cache[key] = stopper_replay([sc[widx[w]] for w in self.dyn_windows], self.dyn_windows,
                                                      self.stopping)
        return self._sub_cache[key]

    def sample(self, rng: np.random.Generator, label: int | None) -> int:
        idx = self.by_label[-1 if label is None else int(label)]
        return int(idx[rng.integers(len(idx))])

    def summary(self) -> dict:
        cued = self.label >= 0
        acc_fixed = {w: round(float((self.fixed_pred[cued, i] == self.label[cued]).mean()), 3)
                     for i, w in enumerate(FIXED_WINDOWS)}
        dec = cued & (self.dyn_pred >= 0)
        rest = ~cued
        return {
            "n": self.n, "level": self.level, "ssvep_uv": self.ssvep_uv,
            "test_trials": {"cued": int(cued.sum()), "rest": int(rest.sum())},
            "test_fixed_window_accuracy": acc_fixed,
            "test_dynamic": {
                "accuracy_of_accepted": round(float((self.dyn_pred[dec] == self.label[dec]).mean()), 3) if dec.any() else None,
                "abstention_rate": round(float((self.dyn_pred[cued] < 0).mean()), 3),
                "mean_decision_s": round(float(self.dyn_window[dec].mean()), 2) if dec.any() else None,
                "rest_false_accept_rate": round(float((self.dyn_pred[rest] >= 0).mean()), 3),
            },
            "calibration_estimate": self.calibration["dynamic_stopping_estimate"],
            "calibration_fixed_window_accuracy": self.fixed_calib_acc,
            "calibration_supported": self.calibration["supported"],
        }


GATE_FACTOR = 1.5
STEP_TARGET_ACC = 0.8


def gate_stats(trials: list[Trial], freqs: list[float], st: StoppingConfig, fb: FBCCAConfig,
               factor: float = GATE_FACTOR) -> tuple[float, float]:
    """On calibration trials: fraction of accepted decisions whose margin is >= factor x threshold,
    and the (Laplace-smoothed) accuracy of those decisions."""
    windows = [round(float(w), 3) for w in np.arange(st.min_window_s, st.max_window_s + 1e-9, st.step_s)]
    hi = hi_ok = acc_n = 0
    for t in trials:
        if t.label is None:
            continue
        sc = [fbcca_scores(t.eeg[: int(w * FS)], FS, freqs, fb, t.pad) for w in windows]
        tgt, _, _, ratio = stopper_replay(sc, windows, st)
        if tgt is None:
            continue
        acc_n += 1
        if ratio >= factor:
            hi += 1
            hi_ok += int(tgt == t.label)
    return (hi / acc_n if acc_n else 0.0, (hi_ok + 1) / (hi + 2))


def _scores_grid(trials: list[Trial], freqs: list[float], windows: list[float], fb: FBCCAConfig) -> list[list[np.ndarray]]:
    return [[fbcca_scores(t.eeg[: int(w * FS)], FS, freqs, fb, t.pad) for w in windows] for t in trials]


def build_pool(n: int, level: str, ssvep_uv: float, seed: int, test_per_target: int = 40,
               test_rest: int = 60, calib_per_target: int = 5, calib_rest: int = 8,
               motion_profile: str | None = None) -> Pool:
    """motion_profile: synthetic head-motion profile (acquisition/motion_sim.py) whose incidental
    movement adds motion artifacts to the EEG of BOTH the calibration and the test trials."""
    fb = FBCCAConfig()
    freqs = FREQS[n]
    # ---- calibration session (engine protocol) ------------------------------------------
    src = SimulatedSource(seed=seed * 1000 + n, ssvep_uv=ssvep_uv)
    if motion_profile:
        src.set_motion_profile(motion_profile)
    cols = src.eeg_indices(DECODING)
    rng = np.random.default_rng(seed * 1000 + n)
    plan = [i for i in range(n) for _ in range(calib_per_target)] + [None] * calib_rest
    rng.shuffle(plan)
    cal_trials = _trials(src, freqs, plan, cols, STIM_S)
    max_w = STIM_S - fb.latency_s - 0.1
    cal = calibrate(cal_trials, freqs, FS, fb, 1.0, max_w, 0.25)
    stopping = StoppingConfig.from_dict(cal["stopping"])
    cal_step = calibrate(cal_trials, freqs, FS, fb, 1.0, max_w, 0.25, target_acc=STEP_TARGET_ACC)
    stopping_step = StoppingConfig.from_dict(cal_step["stopping"])
    calib_gate = gate_stats(cal_trials, freqs, stopping, fb)
    fixed_acc = {}
    cued = [t for t in cal_trials if t.label is not None]
    for w in FIXED_WINDOWS:
        fixed_acc[w] = round(float(np.mean([margin_of(fbcca_scores(t.eeg[: int(w * FS)], FS, freqs, fb, t.pad))[0]
                                            == t.label for t in cued])), 3)
    # ---- test trials (different seed) ---------------------------------------------------
    src = SimulatedSource(seed=seed * 1000 + 500 + n, ssvep_uv=ssvep_uv)
    if motion_profile:
        src.set_motion_profile(motion_profile)
    labels = [i for i in range(n) for _ in range(test_per_target)] + [None] * test_rest
    test = _trials(src, freqs, labels, cols, max_w + 0.05)
    windows = [w for w in np.arange(stopping.min_window_s, stopping.max_window_s + 1e-9, stopping.step_s)]
    windows = sorted(set([round(float(w), 3) for w in windows] + FIXED_WINDOWS))
    grid = _scores_grid(test, freqs, windows, fb)
    widx = {w: i for i, w in enumerate(windows)}
    fixed_pred = np.array([[int(np.argmax(g[widx[w]])) for w in FIXED_WINDOWS] for g in grid])
    dyn_windows = [w for w in windows if abs((w - stopping.min_window_s) / stopping.step_s
                                            - round((w - stopping.min_window_s) / stopping.step_s)) < 1e-6]
    dyn = [stopper_replay([g[widx[w]] for w in dyn_windows], dyn_windows, stopping) for g in grid]
    return Pool(
        n=n, level=level, ssvep_uv=ssvep_uv, windows=windows, stopping=stopping, calibration=cal, calib_gate=calib_gate,
        fixed_calib_acc=fixed_acc,
        label=np.array([-1 if l is None else l for l in labels]),
        fixed_pred=fixed_pred,
        dyn_pred=np.array([-1 if d[0] is None else d[0] for d in dyn]),
        dyn_window=np.array([d[1] for d in dyn]),
        dyn_rest_abstain=np.array([d[2] for d in dyn]),
        dyn_margin_ratio=np.array([d[3] for d in dyn]),
        stopping_step=stopping_step, calibration_step=cal_step,
        scores=np.array([[np.asarray(x) for x in g] for g in grid], dtype=np.float32),
        dyn_windows=dyn_windows,
    )
