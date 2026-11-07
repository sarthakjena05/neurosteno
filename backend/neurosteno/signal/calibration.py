"""SSVEP calibration: tune accept/abstain thresholds from cued trials.

FBCCA itself is training-free. Calibration measures, for this user, session and display:
  * offline accuracy vs window length,
  * the relative-margin threshold per window length at which accepted trials reach a target
    accuracy (the dynamic-stopping reliability criterion),
  * the FBCCA top-score level reached during rest (no attention) trials, used to abstain when
    the patient is not looking at any target,
  * a dynamic-stopping replay over the same trials (accuracy, abstention, mean decision time).

With a handful of trials per class these estimates are noisy. They are reported as
"calibration estimates on N trials", never as validated accuracy.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .fbcca import DynamicStopper, FBCCAConfig, StoppingConfig, fbcca_scores, margin_of


@dataclass
class Trial:
    eeg: np.ndarray  # [n_samples, n_channels] from stimulus onset (latency already skipped)
    label: int | None  # target index, or None for a rest trial
    pad: np.ndarray | None = None


def window_grid(min_s: float, max_s: float, step_s: float) -> list[float]:
    n = int(round((max_s - min_s) / step_s)) + 1
    return [round(min_s + i * step_s, 3) for i in range(n)]


def choose_margin_threshold(margins: np.ndarray, correct: np.ndarray, target_acc: float, min_accepted: int) -> float | None:
    """Smallest threshold whose accepted subset (margin >= thr) meets target accuracy."""
    for thr in np.sort(np.unique(margins)):
        sel = margins >= thr
        if sel.sum() < min_accepted:
            break
        if correct[sel].mean() >= target_acc:
            return float(thr)
    return None


def calibrate(trials: list[Trial], freqs: list[float], fs: float, fbcca: FBCCAConfig | None = None,
              min_window_s: float = 1.0, max_window_s: float = 4.0, step_s: float = 0.25,
              target_acc: float = 0.9, rest_percentile: float = 95.0, base: StoppingConfig | None = None) -> dict:
    fbcca = fbcca or FBCCAConfig()
    cued = [t for t in trials if t.label is not None]
    rest = [t for t in trials if t.label is None]
    if len(cued) < 2 * len(freqs):
        raise ValueError(f"need at least 2 cued trials per target ({2 * len(freqs)}), got {len(cued)}")
    max_len = min(t.eeg.shape[0] for t in cued) / fs
    max_window_s = min(max_window_s, max_len)
    grid = window_grid(min_window_s, max_window_s, step_s)

    per_window = []
    margin_thr: dict[float, float] = {}
    rest_thr: dict[float, float] = {}
    for L in grid:
        n = int(L * fs)
        margins, correct = [], []
        for t in cued:
            s = fbcca_scores(t.eeg[:n], fs, freqs, fbcca, t.pad)
            k, m = margin_of(s)
            margins.append(m)
            correct.append(k == t.label)
        margins_a, correct_a = np.array(margins), np.array(correct, dtype=float)
        thr = choose_margin_threshold(margins_a, correct_a, target_acc, min_accepted=max(2, len(cued) // 4))
        rest_top = []
        for t in rest:
            if t.eeg.shape[0] >= n:
                rest_top.append(float(np.max(fbcca_scores(t.eeg[:n], fs, freqs, fbcca, t.pad))))
        if rest_top:
            rest_thr[L] = float(np.percentile(rest_top, rest_percentile))
        # A window length where no threshold reaches target accuracy can never accept.
        margin_thr[L] = thr if thr is not None else 1.01
        per_window.append({
            "window_s": L,
            "accuracy_no_abstention": round(float(correct_a.mean()), 3),
            "margin_threshold": None if thr is None else round(thr, 4),
            "accepted_fraction": None if thr is None else round(float((margins_a >= thr).mean()), 3),
            "rest_top_score_p": None if not rest_top else round(rest_thr[L], 4),
        })

    stopping = StoppingConfig(
        min_window_s=min_window_s, max_window_s=max_window_s, step_s=step_s,
        margin_threshold=margin_thr, rest_threshold=rest_thr,
        require_consistent=(base.require_consistent if base else 2), calibrated=True,
    )
    sim = simulate_dynamic_stopping(trials, freqs, fs, stopping, fbcca)
    supported = (
        sim["accuracy_of_accepted"] is not None
        and sim["accuracy_of_accepted"] >= 0.85
        and sim["abstention_rate"] <= 0.35
        and (sim["rest_false_accept_rate"] is None or sim["rest_false_accept_rate"] <= 0.2)
    )
    return {
        "n_targets": len(freqs),
        "freqs": list(freqs),
        "n_cued_trials": len(cued),
        "n_rest_trials": len(rest),
        "target_accuracy": target_acc,
        "per_window": per_window,
        "dynamic_stopping_estimate": sim,
        "supported": bool(supported),
        "stopping": stopping.to_dict(),
        "caveat": (f"Estimate from {len(cued)} cued + {len(rest)} rest trials in one session; "
                   "not a validated accuracy figure."),
    }


def simulate_dynamic_stopping(trials: list[Trial], freqs: list[float], fs: float, stopping: StoppingConfig,
                              fbcca: FBCCAConfig) -> dict:
    """Replay each trial through the online dynamic stopping rule (same code path as live use)."""
    acc, times, abstain = [], [], 0
    rest_accepts, rest_n = 0, 0
    for t in trials:
        ds = DynamicStopper(freqs, fs, stopping, fbcca)
        n = int(stopping.min_window_s * fs)
        dec = None
        while n <= t.eeg.shape[0]:
            dec = ds.evaluate(t.eeg[:n], t.pad)
            if dec.reliable or ds.exhausted:
                break
            n += int(stopping.step_s * fs)
        accepted = dec is not None and dec.reliable
        if t.label is None:
            rest_n += 1
            rest_accepts += int(accepted)
            continue
        if accepted:
            acc.append(dec.target == t.label)
            times.append(dec.window_s)
        else:
            abstain += 1
    n_cued = len([t for t in trials if t.label is not None])
    return {
        "accuracy_of_accepted": None if not acc else round(float(np.mean(acc)), 3),
        "abstention_rate": round(abstain / max(1, n_cued), 3),
        "mean_decision_s": None if not times else round(float(np.mean(times)), 2),
        "rest_false_accept_rate": None if rest_n == 0 else round(rest_accepts / rest_n, 3),
        "n_accepted": len(acc),
    }
