"""ASSISTED acquisition: a REAL EEG stream with simulated SSVEP augmentation (demo fallback).

Organisers allow simulated augmentation when live EEG is too noisy. This source keeps the genuine
headset stream (all 8 EEG channels, accelerometer, gyroscope, counter, battery) and adds ONLY the
simulator's SSVEP component (same model as SimulatedSource: harmonics, latency ramp, posterior
weights) for the target chosen by the operator or the autopilot. The real FBCCA decoder then decodes
the mixture.

Honesty rules (enforced by kind/origin, not by convention):
  * kind / origin = "assisted": never "real". Calibration, patient history, metrics, recordings and
    sent messages are kept under their own origin, so an assisted run can never unlock or
    contaminate REAL mode.
  * The UI labels the mode "ASSISTED" and every sent message carries data_origin = "assisted".
  * Nothing here is a validation of live decoding; for that use REAL mode (unchanged).

`base` may also be a SimulatedSource for a dry run without a headset (label says so).
"""

from __future__ import annotations

import time

import numpy as np

from .base import EEGSource, RingBuffer, SourceKind, SourceStatus
from .simulated import SSVEP_WEIGHTS, SimulatedSource


class AssistedSource(SimulatedSource):
    kind = SourceKind.ASSISTED

    def __init__(self, base: EEGSource, ssvep_uv: float = 1.8, seed: int | None = None):
        super().__init__(seed=seed, ssvep_uv=ssvep_uv)
        self.base = base
        self.base_origin = "real" if base.kind == SourceKind.REAL else base.kind.value
        # Mirror the base source's real channel layout and sample rate.
        self.sample_rate = float(base.sample_rate)
        self.channels = base.channels
        self.buffer = RingBuffer(len(self.channels), int(120 * self.sample_rate))
        self.status = SourceStatus(kind=SourceKind.ASSISTED)
        self._eeg_idx = [c.index for c in self.channels if c.kind == "eeg"]
        self._labels = [c.label for c in self.channels if c.kind == "eeg"]
        self._ssvep_w = np.array([SSVEP_WEIGHTS.get(lbl, 0.1) for lbl in self._labels])
        self._ssvep_phase = self.rng.uniform(0, 2 * np.pi, len(self._labels))
        self._read_pos = 0

    # ---- lifecycle --------------------------------------------------------------------
    def start(self) -> None:
        self.base.start()
        self._t0 = self.clock()
        self._read_pos = self.base.total_samples
        self._generated = 0
        st = self.status
        st.connected = True
        st.device = getattr(self.base.status, "device", "") or "unknown"
        st.message = (f"ASSISTED: {self.base_origin.upper()} EEG from {st.device} + SIMULATED SSVEP augmentation "
                      "for the operator/autopilot-chosen target")
        st.extra["base_origin"] = self.base_origin
        st.extra["augmentation"] = "simulated SSVEP (SimulatedSource model) added to the base EEG channels"
        st.extra["note"] = "Selections in this mode are ASSISTED, not independent EEG decoding."

    def stop(self) -> None:
        try:
            self.base.stop()
        finally:
            self.status.streaming = False
            self.status.connected = False
            self.status.message = "Assisted source stopped"
            self._t0 = None

    # ---- acquisition ------------------------------------------------------------------
    def poll(self) -> None:
        if self._t0 is None:
            return
        poll = getattr(self.base, "poll", None)
        if callable(poll):
            poll()
        bst = self.base.status
        self.status.streaming = bool(getattr(bst, "streaming", False))
        self.status.error = getattr(bst, "error", None)
        self.status.battery_percent = getattr(bst, "battery_percent", None)
        self.status.dropped_samples = getattr(bst, "dropped_samples", 0)
        total = self.base.total_samples
        if total <= self._read_pos:
            return
        block = self.base.read_range(self._read_pos, total).astype(np.float64)
        self._read_pos = total
        n = block.shape[0]
        if n == 0:
            return
        if self.gaze_hz:
            fs = self.sample_rate
            t_idx = np.arange(self._generated, self._generated + n)
            t = t_idx / fs
            since = (t_idx - self._gaze_onset_sample) / fs
            ramp = np.clip((since - 0.14) / 0.3, 0.0, 1.0)
            resp = np.zeros((n, len(self._labels)))
            for h, rel in ((1, 1.0), (2, 0.55), (3, 0.25)):
                resp += rel * np.sin(2 * np.pi * h * self.gaze_hz * t[:, None] + h * self._ssvep_phase[None, :])
            block[:, self._eeg_idx] += resp * ramp[:, None] * self._ssvep_w[None, :] * self.ssvep_uv
        self._generated += n
        self.buffer.write(block.astype(np.float32))
        self.status.samples_received += n
        self.status.last_sample_time = time.monotonic()

    # Simulated-only knobs that would fake the REAL stream are refused in assisted mode.
    def inject_blink(self, deliberate: bool = True) -> None:
        raise ValueError("Not available in ASSISTED mode: the blink would be synthetic, the stream is real")

    def perform_gesture(self, kind: str, reaction: bool = True) -> dict:
        raise ValueError("Not available in ASSISTED mode: head motion comes from the real IMU")
