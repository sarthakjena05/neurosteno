"""Filter-bank canonical correlation analysis (FBCCA) for SSVEP detection.

Reference: Chen X. et al., "Filter bank canonical correlation analysis for implementing a
high-speed SSVEP-based brain-computer interface", J. Neural Eng. 12 (2015) 046008.

For each sub-band m, the EEG window is band-pass filtered and the largest canonical
correlation rho_{m,k} with sine/cosine references (fundamental + harmonics) of each target
frequency f_k is computed. Scores are combined as  S_k = sum_m w(m) * rho_{m,k}^2,
w(m) = m^-a + b.

The decoder is training-free; calibration (see calibration.py) only tunes the
accept/abstain thresholds, channel set and window lengths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from scipy.signal import butter, sosfiltfilt


@dataclass
class FBCCAConfig:
    n_harmonics: int = 3
    # Sub-band lower edges (Hz); each sub-band spans [lo, upper_hz]. First edge below the lowest target.
    subband_lows: tuple[float, ...] = (5.5, 13.0, 20.5, 28.0, 35.5)
    upper_hz: float = 50.0
    weight_a: float = 1.25
    weight_b: float = 0.25
    latency_s: float = 0.14  # visual pathway latency skipped at stimulus onset

    def weights(self) -> np.ndarray:
        m = np.arange(1, len(self.subband_lows) + 1, dtype=float)
        return m ** (-self.weight_a) + self.weight_b


@lru_cache(maxsize=64)
def _subband_sos(fs: float, lo: float, hi: float):
    hi = min(hi, fs / 2 - 1)
    return butter(4, [lo, hi], btype="bandpass", fs=fs, output="sos")


@lru_cache(maxsize=256)
def _reference_q(freq: float, fs: float, n: int, n_harm: int) -> np.ndarray:
    t = np.arange(n) / fs
    cols = []
    for h in range(1, n_harm + 1):
        if h * freq >= fs / 2:
            break
        cols += [np.sin(2 * np.pi * h * freq * t), np.cos(2 * np.pi * h * freq * t)]
    y = np.column_stack(cols)
    y = y - y.mean(axis=0)
    q, _ = np.linalg.qr(y)
    return q


def max_canon_corr(qx: np.ndarray, qy: np.ndarray) -> float:
    s = np.linalg.svd(qx.T @ qy, compute_uv=False)
    return float(np.clip(s[0], 0.0, 1.0))


def fbcca_scores(x: np.ndarray, fs: float, freqs: list[float], cfg: FBCCAConfig | None = None,
                 pad: np.ndarray | None = None) -> np.ndarray:
    """Return FBCCA scores, one per target frequency.

    x:   [n_samples, n_channels] EEG window (µV), already restricted to decoding channels.
    pad: optional preceding samples used only to reduce filter edge effects.
    """
    cfg = cfg or FBCCAConfig()
    n = x.shape[0]
    if n < int(fs * 0.5):
        raise ValueError("window too short for FBCCA")
    data = x if pad is None or pad.shape[0] == 0 else np.vstack([pad, x])
    data = data - data.mean(axis=0, keepdims=True)
    w = cfg.weights()
    scores = np.zeros(len(freqs))
    for m, lo in enumerate(cfg.subband_lows):
        xf = sosfiltfilt(_subband_sos(fs, lo, cfg.upper_hz), data, axis=0)[-n:]
        xf = xf - xf.mean(axis=0)
        qx, _ = np.linalg.qr(xf)
        for k, f in enumerate(freqs):
            rho = max_canon_corr(qx, _reference_q(round(f, 4), fs, n, cfg.n_harmonics))
            scores[k] += w[m] * rho * rho
    return scores


@dataclass
class Decision:
    target: int | None  # index of predicted target, None if not (yet) decided
    scores: list[float]
    margin: float  # (s1 - s2) / s1
    window_s: float
    reliable: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "scores": [round(s, 4) for s in self.scores],
            "margin": round(self.margin, 4),
            "window_s": round(self.window_s, 2),
            "reliable": self.reliable,
            "reason": self.reason,
        }


@dataclass
class StoppingConfig:
    """Dynamic stopping: accept only when a reliability criterion holds, else gather more data.

    margin_threshold maps window length (s) -> required relative margin (s1-s2)/s1.
    Defaults are conservative and UNCALIBRATED; calibration replaces them per user.
    """

    min_window_s: float = 1.5
    max_window_s: float = 5.0
    step_s: float = 0.25
    # Defaults were derived from the SYNTHETIC simulator (rest vs. attended FBCCA statistics) and
    # are only used until a calibration on the current data origin replaces them. REAL mode
    # requires calibration before communicating (EngineConfig.require_calibration_real).
    margin_threshold: dict[float, float] = field(default_factory=lambda: {1.5: 0.45, 3.0: 0.4, 5.0: 0.35})
    # window length (s) -> minimum top score; calibrated from rest (no-attention) trials. Empty = no check.
    rest_threshold: dict[float, float] = field(default_factory=lambda: {
        1.0: 0.85, 1.5: 0.55, 2.0: 0.42, 3.0: 0.32, 4.0: 0.27, 5.0: 0.22})
    require_consistent: int = 2  # consecutive evaluations agreeing on the same target
    calibrated: bool = False

    def threshold_at(self, window_s: float) -> float:
        ks = sorted(self.margin_threshold)
        vs = [self.margin_threshold[k] for k in ks]
        return float(np.interp(window_s, ks, vs))

    def rest_at(self, window_s: float) -> float:
        if not self.rest_threshold:
            return 0.0
        ks = sorted(self.rest_threshold)
        return float(np.interp(window_s, ks, [self.rest_threshold[k] for k in ks]))

    def to_dict(self) -> dict:
        return {
            "min_window_s": self.min_window_s,
            "max_window_s": self.max_window_s,
            "step_s": self.step_s,
            "margin_threshold": {str(k): round(v, 4) for k, v in sorted(self.margin_threshold.items())},
            "rest_threshold": {str(k): round(v, 4) for k, v in sorted(self.rest_threshold.items())},
            "require_consistent": self.require_consistent,
            "calibrated": self.calibrated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StoppingConfig":
        return cls(
            min_window_s=d["min_window_s"], max_window_s=d["max_window_s"], step_s=d["step_s"],
            margin_threshold={float(k): float(v) for k, v in d["margin_threshold"].items()},
            rest_threshold={float(k): float(v) for k, v in d.get("rest_threshold", {}).items()},
            require_consistent=d.get("require_consistent", 2),
            calibrated=d.get("calibrated", False),
        )


def margin_of(scores: np.ndarray) -> tuple[int, float]:
    order = np.argsort(scores)[::-1]
    s1 = float(scores[order[0]])
    s2 = float(scores[order[1]]) if len(scores) > 1 else 0.0
    return int(order[0]), (s1 - s2) / s1 if s1 > 0 else 0.0


class DynamicStopper:
    """Incrementally evaluates growing windows for one selection attempt."""

    def __init__(self, freqs: list[float], fs: float, stopping: StoppingConfig, fbcca: FBCCAConfig):
        self.freqs = list(freqs)
        self.fs = fs
        self.stopping = stopping
        self.fbcca = fbcca
        self.history: list[int] = []
        self.next_eval_s = stopping.min_window_s
        self.last: Decision | None = None

    def due(self, available_s: float) -> bool:
        return available_s >= self.next_eval_s

    def evaluate(self, x: np.ndarray, pad: np.ndarray | None = None) -> Decision:
        window_s = x.shape[0] / self.fs
        scores = fbcca_scores(x, self.fs, self.freqs, self.fbcca, pad)
        target, margin = margin_of(scores)
        self.history.append(target)
        self.next_eval_s = window_s + self.stopping.step_s
        thr = self.stopping.threshold_at(window_s)
        k = self.stopping.require_consistent
        consistent = len(self.history) >= k and len(set(self.history[-k:])) == 1
        strong = float(scores[target]) >= self.stopping.rest_at(window_s)
        reliable = margin >= thr and consistent and strong
        if reliable:
            reason = f"margin {margin:.2f} >= {thr:.2f}, consistent over {k} evaluations"
            dec = Decision(target, scores.tolist(), margin, window_s, True, reason)
        elif window_s + 1e-9 >= self.stopping.max_window_s:
            why = []
            if margin < thr:
                why.append(f"margin {margin:.2f} < {thr:.2f}")
            if not consistent:
                why.append("prediction not stable")
            if not strong:
                why.append("response weaker than calibrated rest level")
            dec = Decision(None, scores.tolist(), margin, window_s, False, "abstain: " + ", ".join(why))
        else:
            dec = Decision(None, scores.tolist(), margin, window_s, False, "collecting more evidence")
        self.last = dec
        return dec

    @property
    def exhausted(self) -> bool:
        return self.last is not None and self.last.target is None and self.last.window_s + 1e-9 >= self.stopping.max_window_s
