"""EXPERIMENTAL blink-event detector on frontal EEG channels.

Detects large, slow frontal deflections (eye blinks) on the available EEG sites (Fz on the
standard Unicorn montage; the headset has no dedicated EOG). It is intended only as an
optional "reject / not this" input during correction windows, and is disabled until a
calibration shows deliberate blinks separate from natural blinks for this user.

Deliberately NOT implemented: double-blink vs. sustained-eye-closure discrimination. That
distinction has not been tested on this hardware and is not assumed to be reliable.

Accidental-trigger protection: amplitude threshold (calibrated), duration limits, and a
refractory period after each accepted event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from scipy.signal import butter, find_peaks, sosfiltfilt


@dataclass
class BlinkConfig:
    channel_labels: list[str] = field(default_factory=lambda: ["Fz"])
    band: tuple[float, float] = (0.5, 8.0)
    threshold_uv: float = 100.0  # uncalibrated default
    report_floor_uv: float = 25.0  # below this a deflection is not even reported
    min_width_s: float = 0.08
    max_width_s: float = 0.8
    refractory_s: float = 1.0
    calibrated: bool = False
    enabled: bool = False

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


@lru_cache(maxsize=8)
def _sos(fs: float, lo: float, hi: float):
    return butter(2, [lo, hi], btype="bandpass", fs=fs, output="sos")


def frontal_trace(eeg: np.ndarray, fs: float, band: tuple[float, float]) -> np.ndarray:
    x = eeg.mean(axis=1) if eeg.ndim == 2 else eeg
    x = x - np.median(x)
    return sosfiltfilt(_sos(fs, float(band[0]), float(band[1])), x)


def detect_peaks(trace: np.ndarray, fs: float, cfg: BlinkConfig, height: float | None = None) -> list[tuple[int, float, float]]:
    """Return (sample, amplitude_uv, width_s) of blink-like deflections above `height`."""
    h = cfg.report_floor_uv if height is None else height
    mag = np.abs(trace)
    idx, props = find_peaks(mag, height=h, width=(cfg.min_width_s * fs / 2, cfg.max_width_s * fs),
                            distance=int(0.15 * fs), rel_height=0.5)
    return [(int(i), float(mag[i]), float(w / fs)) for i, w in zip(idx, props["widths"])]


class OnlineBlinkDetector:
    """Incremental detector. Call update() with the source and it returns new events."""

    LOOKBACK_S = 3.0
    GUARD_S = 0.5  # don't decide on the newest samples (filter edge, unfinished blink)

    def __init__(self, cfg: BlinkConfig, fs: float):
        self.cfg = cfg
        self.fs = fs
        self.processed_until = 0  # absolute sample index
        self.last_accept: int | None = None

    def update(self, read_range, total: int, channel_idx: list[int]) -> list[dict]:
        if not channel_idx:
            return []
        start = max(0, total - int(self.LOOKBACK_S * self.fs))
        decide_until = total - int(self.GUARD_S * self.fs)
        if decide_until <= self.processed_until or total - start < int(self.fs):
            return []
        eeg = read_range(start, total)[:, channel_idx]
        trace = frontal_trace(eeg, self.fs, self.cfg.band)
        events = []
        for i, amp, width in detect_peaks(trace, self.fs, self.cfg):
            abs_i = start + i
            if abs_i < self.processed_until or abs_i >= decide_until:
                continue
            ev = {"sample": abs_i, "amplitude_uv": round(amp, 1), "width_s": round(width, 3)}
            if amp < self.cfg.threshold_uv:
                ev.update(accepted=False, reason=f"below deliberate threshold ({self.cfg.threshold_uv:.0f} µV)")
            elif self.last_accept is not None and abs_i - self.last_accept < self.cfg.refractory_s * self.fs:
                ev.update(accepted=False, reason=f"within {self.cfg.refractory_s:.1f} s refractory period")
            else:
                ev.update(accepted=True, reason="deliberate blink")
                self.last_accept = abs_i
            events.append(ev)
        self.processed_until = decide_until
        return events


def calibrate_blinks(normal: np.ndarray, deliberate_windows: list[np.ndarray], fs: float, cfg: BlinkConfig) -> dict:
    """normal: frontal EEG during relaxed viewing with natural blinks.
    deliberate_windows: one window per cue in which the user blinked deliberately."""
    normal_peaks = [a for _, a, _ in detect_peaks(frontal_trace(normal, fs, cfg.band), fs, cfg)]
    delib = []
    for w in deliberate_windows:
        pk = detect_peaks(frontal_trace(w, fs, cfg.band), fs, cfg)
        delib.append(max((a for _, a, _ in pk), default=0.0))
    delib_a = np.array(delib)
    normal_max = max(normal_peaks, default=cfg.report_floor_uv)
    normal_p95 = float(np.percentile(normal_peaks, 95)) if normal_peaks else cfg.report_floor_uv
    delib_p20 = float(np.percentile(delib_a, 20)) if len(delib_a) else 0.0
    separable = delib_p20 > 1.2 * normal_max and float((delib_a > 0).mean()) >= 0.8
    threshold = (normal_max + delib_p20) / 2 if separable else None
    return {
        "n_natural_peaks": len(normal_peaks),
        "natural_max_uv": round(normal_max, 1),
        "natural_p95_uv": round(normal_p95, 1),
        "deliberate_amplitudes_uv": [round(float(a), 1) for a in delib_a],
        "deliberate_p20_uv": round(delib_p20, 1),
        "detected_deliberate_fraction": round(float((delib_a > 0).mean()), 3) if len(delib_a) else 0.0,
        "separable": bool(separable),
        "threshold_uv": None if threshold is None else round(float(threshold), 1),
        "note": ("Deliberate blinks separate from natural blinks in this recording." if separable else
                 "Deliberate and natural blinks overlap; blink input stays DISABLED for this user."),
    }
