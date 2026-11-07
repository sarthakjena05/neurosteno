"""Controlled synthetic noise injection on top of ANY EEG source (real Unicorn, assisted, simulated).

Purpose (demo + robustness testing): degrade the EEG that the system actually processes in a
controlled, reproducible way, so that the measured signal quality and the adaptive choice count
react to a known disturbance.

Rules:
  * The RAW stream is never modified. `EEGSource.read_range_raw()` returns it unchanged;
    `read_range()` returns the PROCESSED stream = raw + injected noise. Every consumer (quality
    monitor, decoder, calibration) reads the processed stream, so nothing downstream is faked.
  * Level 0 means the processed stream is bit-identical to the raw stream.
  * The noise is a fixed, seeded bank of independent pink (1/f) noise per EEG channel, indexed by
    the ABSOLUTE sample number: re-reading a window returns the same samples, and the level history
    (a step function over sample numbers) is kept, so a window spanning a slider change is exact.
  * Only EEG columns are touched (accelerometer, gyroscope, counter, battery, validation untouched).
  * The level is an RMS amplitude in microvolts per channel (broadband, 1-100 Hz).
"""

from __future__ import annotations

import threading

import numpy as np
from scipy.signal import butter, sosfiltfilt

BANK_SECONDS = 64.0
MAX_UV = 100.0
METRIC_BAND = (3.0, 40.0)   # SSVEP stimulation band incl. 2nd/3rd harmonics of 6.67-12 Hz


def _pink_bank(n: int, ch: int, fs: float, seed: int = 20261107) -> np.ndarray:
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal((ch, n)), axis=1)
    f = np.fft.rfftfreq(n, 1 / fs)
    shape = np.zeros_like(f)
    band = (f >= 1.0) & (f <= min(100.0, fs / 2 - 1))
    shape[band] = 1 / np.sqrt(f[band])                # 1/f power
    x = np.fft.irfft(spec * shape[None, :], n=n, axis=1).T
    x -= x.mean(axis=0)
    return (x / x.std(axis=0)).astype(np.float32)      # unit RMS per channel


class NoiseInjector:
    def __init__(self, fs: float, eeg_cols: list[int]):
        self.fs = float(fs)
        self.cols = list(eeg_cols)
        self._bank: np.ndarray | None = None
        self._starts: list[int] = [0]
        self._levels: list[float] = [0.0]
        self._lock = threading.Lock()

    @property
    def level_uv(self) -> float:
        return self._levels[-1]

    def set_level(self, uv: float, at_sample: int) -> float:
        uv = float(np.clip(uv, 0.0, MAX_UV))
        with self._lock:
            if at_sample <= self._starts[-1]:
                self._levels[-1] = uv
            else:
                self._starts.append(int(at_sample))
                self._levels.append(uv)
            if len(self._starts) > 2000:  # keep the history bounded (oldest steps are long gone)
                self._starts, self._levels = self._starts[-1000:], self._levels[-1000:]
        return uv

    def history(self) -> list[tuple[int, float]]:
        return list(zip(self._starts, self._levels))

    def apply(self, block: np.ndarray, first_sample: int) -> np.ndarray:
        """Return block (rows = absolute samples first_sample..) with noise added to EEG columns."""
        n = block.shape[0]
        if n == 0 or not self.cols:
            return block
        with self._lock:
            starts = np.asarray(self._starts)
            levels = np.asarray(self._levels)
        idx = np.arange(first_sample, first_sample + n)
        lv = levels[np.searchsorted(starts, idx, side="right") - 1]
        if not np.any(lv > 0):
            return block
        if self._bank is None:
            self._bank = _pink_bank(int(BANK_SECONDS * self.fs), len(self.cols), self.fs)
        noise = self._bank[idx % self._bank.shape[0]] * lv[:, None]
        out = block.astype(np.float32, copy=True)
        out[:, self.cols] += noise
        return out


def band_noise_uv(x: np.ndarray, fs: float, band: tuple[float, float] = METRIC_BAND) -> float | None:
    """Median over channels of the RMS (uV) in the SSVEP band: the measured noise floor that the
    SSVEP response has to compete with (no stimulus is assumed in the window)."""
    if x.ndim != 2 or x.shape[0] < int(fs) or x.shape[1] == 0:
        return None
    sos = butter(4, [band[0], band[1]], btype="bandpass", fs=fs, output="sos")
    y = sosfiltfilt(sos, x - x.mean(axis=0), axis=0)
    edge = int(0.25 * fs)
    y = y[edge:-edge] if y.shape[0] > 4 * edge else y
    return float(np.median(np.sqrt(np.mean(y ** 2, axis=0))))
