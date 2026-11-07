"""Per-channel signal quality, assessed independently of classification.

Heuristic checks on the most recent window of raw EEG (µV):
  * flat        - near-zero variance (disconnected electrode / dead channel)
  * artifact    - very large amplitude (poor contact, movement, saturation)
  * line_noise  - mains (50/60 Hz) power dominating the EEG band
  * ok

Thresholds are conservative defaults for a dry/gel Unicorn setup and are configurable.
They indicate whether the signal is plausible, not whether SSVEP decoding will work.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy.signal import butter, sosfiltfilt, welch


@dataclass
class QualityConfig:
    window_s: float = 2.0
    flat_std_uv: float = 0.3
    artifact_std_uv: float = 60.0
    artifact_ptp_uv: float = 400.0
    line_ratio_max: float = 4.0
    line_hz: float = 60.0
    stall_s: float = 1.0
    min_good_decoding_channels: int = 3


@lru_cache(maxsize=16)
def _bp(fs: float, lo: float, hi: float, order: int = 4):
    return butter(order, [lo, hi], btype="bandpass", fs=fs, output="sos")


def _bandpower(f: np.ndarray, p: np.ndarray, lo: float, hi: float) -> float:
    m = (f >= lo) & (f <= hi)
    return float(np.trapezoid(p[m], f[m])) if m.any() else 0.0


def common_mode(window: np.ndarray, fs: float) -> float | None:
    """Mean inter-channel correlation (1-40 Hz). ~1.0 means every channel carries the same
    signal: the reference/ground ear clips are not on skin or the headset is not worn
    (observed on the real UN-2024.09.36 during bring-up)."""
    if window.shape[0] < int(fs) or window.shape[1] < 2:
        return None
    x = window - window.mean(axis=0)
    xf = sosfiltfilt(_bp(fs, 1.0, 40.0), x, axis=0)
    if np.any(xf.std(axis=0) < 1e-6):
        return None
    c = np.corrcoef(xf.T)
    k = c.shape[0]
    return float((c.sum() - k) / (k * k - k))


def assess_channels(window: np.ndarray, fs: float, labels: list[str], cfg: QualityConfig) -> list[dict]:
    """window: [n_samples, n_channels] raw EEG for the channels named in labels."""
    results = []
    if window.shape[0] < int(fs * 0.5):
        return [{"label": lbl, "status": "no_data", "std_uv": None, "line_ratio": None} for lbl in labels]
    x = window - np.median(window, axis=0, keepdims=True)
    xf = sosfiltfilt(_bp(fs, 1.0, 40.0), x, axis=0)
    nper = min(x.shape[0], int(fs))
    f, p = welch(x, fs=fs, nperseg=nper, axis=0)
    for j, lbl in enumerate(labels):
        std = float(np.std(xf[:, j]))
        ptp = float(np.ptp(xf[:, j]))
        line = _bandpower(f, p[:, j], cfg.line_hz - 2, cfg.line_hz + 2)
        band = _bandpower(f, p[:, j], 1.0, 40.0) + 1e-12
        ratio = line / band
        if std < cfg.flat_std_uv:
            status = "flat"
        elif std > cfg.artifact_std_uv or ptp > cfg.artifact_ptp_uv:
            status = "artifact"
        elif ratio > cfg.line_ratio_max:
            status = "line_noise"
        else:
            status = "ok"
        results.append({"label": lbl, "status": status, "std_uv": round(std, 2), "line_ratio": round(ratio, 3)})
    return results
