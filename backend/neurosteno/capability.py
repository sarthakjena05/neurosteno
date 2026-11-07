"""Patient capability profile: what THIS patient can reliably do, with the evidence behind it.

Every estimate keeps its raw counts (successes / trials, events / exposure), so the policy
optimiser can sample from posteriors instead of trusting point estimates from a few trials.

Origins are never mixed: estimates carry the data origin they were measured on
("real" = the Unicorn on a person, "simulated" = synthetic signals, "replay" = recorded data).
Only origin == "real" may be presented as a measured patient capability; everything else is
labelled as a pipeline / simulation estimate.

Sections
  eeg[n]      SSVEP selection with n targets: accuracy of accepted decisions, abstention, decision
              time, no-control (rest) false-accept; calibration + live YES/NO evidence; correction rate
  confirm     EEG YES/NO (2-target set) used for step and final confirmation
  gestures    nod / shake / tilt: LOO sensitivity, confusion, latency, held-out false activations,
              plus live unprompted activations (outside any prompt window) as extra exposure
  motion      neutral noise, incidental movement rate, limitations (where measurable)
  fatigue     decision-time and YES-rate drift within the session (only when enough data)
  modes       eeg_only (always), hybrid (nod + shake usable), motion_assisted (+ tilt usable)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

MODES = ("eeg_only", "hybrid", "motion_assisted")


def claim_for(origin: str) -> str:
    return {"real": "measured_real", "replay": "replayed_recording", "simulated": "simulated"}.get(origin, "assumed")


@dataclass
class Binom:
    k: float
    n: float

    @property
    def mean(self) -> float:
        return (self.k + 1) / (self.n + 2)

    def draws(self, rng: np.random.Generator, size: int) -> np.ndarray:
        return rng.beta(self.k + 1, max(self.n - self.k, 0) + 1, size)

    def to_dict(self) -> dict:
        return {"k": round(float(self.k), 2), "n": round(float(self.n), 2), "posterior_mean": round(self.mean, 4),
                "interval_90": [round(float(x), 4) for x in _beta_q(self.k + 1, max(self.n - self.k, 0) + 1)]}


def _beta_q(a: float, b: float) -> tuple[float, float]:
    from scipy.stats import beta
    return beta.ppf(0.05, a, b), beta.ppf(0.95, a, b)


@dataclass
class PoissonRate:
    events: float
    exposure_s: float

    def draws(self, rng: np.random.Generator, size: int) -> np.ndarray:
        """Per-second rate, Jeffreys Gamma posterior (events + 0.5) / exposure."""
        return rng.gamma(self.events + 0.5, 1.0 / max(self.exposure_s, 1e-6), size)

    @property
    def mean_per_hour(self) -> float:
        return 3600 * (self.events + 0.5) / max(self.exposure_s, 1e-6)

    def to_dict(self) -> dict:
        from scipy.stats import chi2
        hi = chi2.ppf(0.95, 2 * (self.events + 1)) / 2 / max(self.exposure_s / 3600, 1e-9)
        return {"events": self.events, "exposure_s": round(self.exposure_s, 1),
                "posterior_mean_per_hour": round(self.mean_per_hour, 2), "upper95_per_hour": round(float(hi), 1)}


@dataclass
class EEGCap:
    n: int
    accuracy: Binom
    abstain: Binom
    decision_s: float
    decision_sd: float
    max_window_s: float
    rest_false_accept: Binom
    gate_fraction: float = 0.0       # calibration: share of accepted decisions with margin >= gate x threshold
    gate_accuracy: Binom | None = None
    source: str = "assumed"
    origin: str = "none"
    live: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"n": self.n, "accuracy": self.accuracy.to_dict(), "abstention": self.abstain.to_dict(),
                "decision_s": round(self.decision_s, 3), "decision_sd": round(self.decision_sd, 3),
                "max_window_s": self.max_window_s, "rest_false_accept": self.rest_false_accept.to_dict(),
                "gate": {"fraction": round(self.gate_fraction, 3),
                         "accuracy": None if self.gate_accuracy is None else self.gate_accuracy.to_dict()},
                "correction_rate": self.live.get("correction_rate"), "live": self.live,
                "source": self.source, "origin": self.origin, "claim": claim_for(self.origin)}


@dataclass
class GestureCap:
    kind: str
    usable: bool
    sensitivity: Binom
    confusion: Binom
    latency_s: float
    latency_sd: float
    false_activation: PoissonRate
    origin: str = "none"
    live: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "usable": self.usable, "sensitivity": self.sensitivity.to_dict(),
                "confusion": self.confusion.to_dict(), "latency_s": round(self.latency_s, 2),
                "latency_sd": round(self.latency_sd, 2), "false_activation": self.false_activation.to_dict(),
                "live": self.live, "origin": self.origin, "claim": claim_for(self.origin)}


@dataclass
class CapabilityProfile:
    origin: str
    eeg: dict = field(default_factory=dict)          # n -> EEGCap
    gestures: dict = field(default_factory=dict)     # kind -> GestureCap (empty = no gesture calibration)
    imu: dict = field(default_factory=dict)
    motion: dict = field(default_factory=dict)
    fatigue: dict = field(default_factory=lambda: {"measured": False, "reason": "not enough live data"})
    gesture_window_s: float | None = None
    calibration_overhead_s: dict = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    confirm_eeg: EEGCap | None = None   # YES/NO decoder if it differs from the 2-target choose decoder

    @property
    def confirm(self) -> EEGCap | None:
        return self.confirm_eeg or self.eeg.get(2)

    def modes(self) -> dict:
        g = self.gestures
        imu_ok = bool(self.imu.get("ok", False))
        hyb = imu_ok and all(k in g and g[k].usable for k in ("nod", "shake"))
        mot = hyb and "tilt" in g and g["tilt"].usable
        why = {"eeg_only": "always available (no motion control required)"}
        if not g:
            why["hybrid"] = why["motion_assisted"] = "no gesture calibration on this data origin"
        elif not imu_ok:
            why["hybrid"] = why["motion_assisted"] = f"IMU not usable: {self.imu.get('reason', 'unknown')}"
        else:
            why["hybrid"] = "nod and shake usable" if hyb else "nod and/or shake not reliably detected in calibration"
            why["motion_assisted"] = ("nod, shake and tilt usable" if mot else
                                      "requires hybrid plus a usable sustained tilt")
        return {"eeg_only": True, "hybrid": hyb, "motion_assisted": mot, "why": why}

    def to_dict(self) -> dict:
        return {"origin": self.origin, "claim": claim_for(self.origin),
                "label": ("MEASURED · REAL" if self.origin == "real" else
                          f"ESTIMATE · {self.origin.upper()} DATA (not a patient measurement)"),
                "eeg": {str(k): v.to_dict() for k, v in sorted(self.eeg.items())},
                "confirm": None if self.confirm is None else self.confirm.to_dict(),
                "gestures": {k: v.to_dict() for k, v in self.gestures.items()},
                "gesture_window_s": self.gesture_window_s,
                "imu": self.imu, "motion": self.motion, "fatigue": self.fatigue, "modes": self.modes(),
                "calibration_overhead_s": self.calibration_overhead_s, "created": self.created}


# --------------------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------------------
ASSUMED_EEG = {2: (0.85, 0.15, 2.5), 3: (0.8, 0.2, 3.0), 5: (0.75, 0.25, 3.5)}   # = routing.ASSUMED
ASSUMED_WEIGHT = 4.0   # an assumption counts as 4 pseudo-trials: weak, so live data dominates fast


def eeg_from_calibration(n: int, res: dict | None, origin: str, max_window_s: float,
                         gate: tuple[float, float] | None = None, live: dict | None = None) -> EEGCap:
    """res = engine/pool calibration result for n targets (signal.calibration.calibrate)."""
    live = dict(live or {})
    if res and res.get("dynamic_stopping_estimate", {}).get("accuracy_of_accepted") is not None:
        est = res["dynamic_stopping_estimate"]
        n_cued = res.get("n_cued_trials", 0)
        n_acc = est.get("n_accepted") or 0
        acc = Binom(est["accuracy_of_accepted"] * n_acc, n_acc)
        ab = Binom(est["abstention_rate"] * n_cued, n_cued)
        n_rest = res.get("n_rest_trials", 0)
        rfa = Binom((est.get("rest_false_accept_rate") or 0.0) * n_rest, n_rest)
        t = est.get("mean_decision_s") or max_window_s
        src = f"calibration ({origin} data, {n_cued} cued + {n_rest} rest trials)"
        g_acc = None
        g_frac = 0.0
        if gate is not None:
            g_frac = float(gate[0])
            hi = g_frac * n_acc
            g_acc = Binom(float(gate[1]) * (hi + 2) - 1, hi) if hi > 0 else None
        cap = EEGCap(n, acc, ab, float(t), 0.35, max_window_s, rfa, g_frac, g_acc, src, res.get("origin", origin))
    else:
        a, b, t = ASSUMED_EEG[n]
        w = ASSUMED_WEIGHT
        cap = EEGCap(n, Binom(a * w, w), Binom(b * w, w), t, 0.6, max_window_s, Binom(0.25 * w, w), 0.0, None,
                     "assumed (no calibration on this data origin)", "none")
    # live YES/NO evidence (a YES on a highlight = correct decision, NO = wrong, unclear = abstain)
    c, w_, a_ = live.get("correct", 0), live.get("wrong", 0), live.get("abstain", 0)
    if c + w_ + a_:
        cap.accuracy = Binom(cap.accuracy.k + c, cap.accuracy.n + c + w_)
        cap.abstain = Binom(cap.abstain.k + a_, cap.abstain.n + c + w_ + a_)
        if live.get("mean_decision_s") is not None and c + w_:
            m = cap.accuracy.n
            cap.decision_s = (cap.decision_s * max(m - c - w_, 1) + live["mean_decision_s"] * (c + w_)) / max(m, 1)
        cap.source += f" + {c + w_ + a_} live outcomes"
        live["correction_rate"] = round(w_ / (c + w_), 3) if c + w_ else None
    cap.live = live
    return cap


def gestures_from_calibration(res: dict | None, live: dict | None = None) -> dict:
    """res = signal.gestures.calibrate_gestures() result."""
    if not res or not res.get("ok"):
        return {}
    out = {}
    live = live or {}
    for kind, e in res["estimates"].items():
        fa = e["false_activations"]
        lv = live.get(kind, {})
        lat = e["latency_s"]
        out[kind] = GestureCap(
            kind=kind, usable=bool(res["usable"].get(kind)),
            sensitivity=Binom(e["hits"] + lv.get("hits", 0), e["cues"] + lv.get("prompts", 0)),
            confusion=Binom(e["confused"], e["cues"]),   # intended this gesture, another one detected
            latency_s=float(lat["mean"] if lat["mean"] is not None else 3.0),
            latency_sd=float(lat["sd"] if lat["sd"] is not None else 0.5),
            false_activation=PoissonRate(fa["events"] + lv.get("unprompted", 0),
                                         fa["exposure_s"] + lv.get("unprompted_exposure_s", 0.0)),
            origin=res.get("origin", "none"), live=lv)
    return out


def motion_summary(res: dict | None) -> dict:
    if not res:
        return {"measured": False, "reason": "no gesture calibration"}
    lim = []
    est = res.get("estimates") or {}
    if est and all(e["hits"] == 0 for e in est.values()):
        lim.append("no voluntary head movement detected in calibration")
    if not res.get("ok"):
        lim.append(res.get("note", "gesture calibration failed"))
    n = res.get("neutral") or {}
    if (n.get("speed_noise_dps") or 0) > 3:
        lim.append(f"head not still at rest (noise {n['speed_noise_dps']} deg/s; tremor or incidental motion)")
    for k, e in est.items():
        if e["false_activations"]["events"] > 0:
            lim.append(f"natural movement produced {e['false_activations']['events']} false {k}(s) in calibration")
    return {"measured": True, "origin": res.get("origin"), "neutral": n, "limitations": lim,
            "axes_explained_variance": res.get("axes_explained_variance")}


def fatigue_from(log: list[tuple[float, str, float | None]], min_n: int = 20, min_span_s: float = 600.0) -> dict:
    """log: (time_s, 'correct'|'wrong'|'abstain', decision_s). Compares the first and last third."""
    if len(log) < min_n or log[-1][0] - log[0][0] < min_span_s:
        return {"measured": False, "reason": f"needs >= {min_n} live decisions over >= {min_span_s / 60:.0f} min"}
    k = len(log) // 3
    def stats(xs):
        dec = [x for x in xs if x[1] != "abstain"]
        return {"yes_rate": round(sum(1 for x in dec if x[1] == "correct") / max(1, len(dec)), 3),
                "abstain_rate": round(sum(1 for x in xs if x[1] == "abstain") / len(xs), 3),
                "mean_decision_s": round(float(np.mean([x[2] for x in dec if x[2] is not None] or [np.nan])), 3)}
    a, b = stats(log[:k]), stats(log[-k:])
    worse = (b["yes_rate"] < a["yes_rate"] - 0.1) or (b["abstain_rate"] > a["abstain_rate"] + 0.1)
    return {"measured": True, "first_third": a, "last_third": b, "possible_fatigue": bool(worse),
            "note": "Within-session drift only; not a clinical fatigue measure."}
