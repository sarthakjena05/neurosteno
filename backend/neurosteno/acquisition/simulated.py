"""Synthetic EEG source for demos without a headset.

The simulator produces raw multichannel signals with the same channel layout as a Unicorn
Hybrid Black (8 EEG + accel + gyro + battery + counter + validation), so the real filtering,
FBCCA decoder, blink detector and signal-quality code run unchanged on it.

It does NOT produce predictions. "Patient gaze" adds an SSVEP response at a frequency to the
occipital/parietal channels; whether that is decoded correctly depends on the decoder.
Injected mistakes are produced by making the virtual patient's EEG respond to a different
target, which is how a real misclassification looks to the system.

Head motion (accelerometer + gyroscope, and motion artifacts in the EEG) comes from
motion_sim.HeadMotion with its own random generator, so the EEG of a given seed is unchanged
by the motion model. The default motion profile "steady" has no incidental movement and no
EEG coupling; the virtual patient can still nod / shake / tilt on request.

All values are synthetic. Status metrics (battery, dropped samples) are labelled simulated.
"""

from __future__ import annotations

import time

import numpy as np
from scipy.signal import lfilter

from .base import ChannelInfo, EEGSource, SourceKind
from .motion_sim import PROFILES as MOTION_PROFILES, HeadMotion
from .unicorn import DEFAULT_EEG_LABELS

FS = 250.0
UNICORN_LAYOUT = (
    [(f"EEG {i + 1}", "eeg", "uV") for i in range(8)]
    + [(f"Accelerometer {a}", "accel", "g") for a in "XYZ"]
    + [(f"Gyroscope {a}", "gyro", "deg/s") for a in "XYZ"]
    + [("Battery Level", "battery", "%"), ("Counter", "counter", ""), ("Validation Indicator", "validation", "")]
)

# Relative SSVEP strength per electrode site (occipital strongest).
SSVEP_WEIGHTS = {"Fz": 0.05, "C3": 0.1, "Cz": 0.12, "C4": 0.1, "Pz": 0.45, "PO7": 0.8, "Oz": 1.0, "PO8": 0.8}
BLINK_WEIGHTS = {"Fz": 1.0, "C3": 0.35, "Cz": 0.4, "C4": 0.35, "Pz": 0.15, "PO7": 0.05, "Oz": 0.03, "PO8": 0.05}

_PINK_B = np.array([0.049922035, -0.095993537, 0.050612699, -0.004408786])
_PINK_A = np.array([1.0, -2.494956002, 2.017265875, -0.522189400])


def _layout_channels(labels: list[str]) -> list[ChannelInfo]:
    out = []
    for i, (name, kind, unit) in enumerate(UNICORN_LAYOUT):
        out.append(ChannelInfo(index=i, name=name, kind=kind, unit=unit, label=labels[i] if kind == "eeg" else ""))
    return out


class SimulatedSource(EEGSource):
    kind = SourceKind.SIMULATED

    def __init__(self, seed: int | None = None, background_uv: float = 9.0, ssvep_uv: float = 1.8,
                 alpha_uv: float = 2.0, line_noise_uv: float = 1.5, clock=time.monotonic):
        super().__init__(FS, _layout_channels(DEFAULT_EEG_LABELS))
        self.rng = np.random.default_rng(seed)
        self.clock = clock
        self.background_uv = background_uv
        self.ssvep_uv = ssvep_uv
        self.alpha_uv = alpha_uv
        self.line_noise_uv = line_noise_uv
        # Live-adjustable simulation knobs
        self.gaze_hz: float | None = None
        self.noise_multiplier = 1.0
        self.bad_channels: set[str] = set()  # electrode labels with simulated poor contact
        self.disconnected = False  # simulate a Bluetooth drop: no samples arrive
        self.eyes_closed = False  # posterior alpha rises ~4x in power (Berger effect)
        self.drop_probability = 0.0  # per-block probability of a simulated packet loss
        # Internal state
        self._eeg_idx = [c.index for c in self.channels if c.kind == "eeg"]
        self._labels = [c.label for c in self.channels if c.kind == "eeg"]
        self._ssvep_w = np.array([SSVEP_WEIGHTS.get(lbl, 0.1) for lbl in self._labels])
        self._blink_w = np.array([BLINK_WEIGHTS.get(lbl, 0.1) for lbl in self._labels])
        self._ssvep_phase = self.rng.uniform(0, 2 * np.pi, len(self._labels))
        self._pink_zi = np.zeros((len(self._labels), len(_PINK_A) - 1))
        self._pink_gain = self._calibrate_pink_gain()
        self._mix = np.eye(len(self._labels)) * 0.7 + 0.3 / len(self._labels)  # mild spatial correlation
        self._t0: float | None = None
        self._generated = 0
        self._counter = 0
        self._gaze_onset_sample = 0
        self._blinks: list[tuple[int, float, float]] = []  # (onset_sample, amplitude_uv, duration_s)
        self._drift_phase = self.rng.uniform(0, 2 * np.pi, len(self._labels))
        self.head = HeadMotion("steady", seed=seed, fs=FS)

    def _calibrate_pink_gain(self) -> float:
        w = np.random.default_rng(0).standard_normal(20000)
        return 1.0 / float(np.std(lfilter(_PINK_B, _PINK_A, w)[2000:]))

    # ---- lifecycle --------------------------------------------------------------------
    def start(self) -> None:
        self._t0 = self.clock()
        self._generated = 0
        self.status.connected = True
        self.status.streaming = True
        self.status.device = "Synthetic Unicorn-layout simulator"
        self.status.message = "SIMULATED EEG: synthetic signals, no headset involved"
        self.status.battery_percent = None  # never fake hardware metrics
        self.status.extra["note"] = "All signals are synthetic."

    def stop(self) -> None:
        self.status.streaming = False
        self.status.connected = False
        self.status.message = "Simulator stopped"
        self._t0 = None

    # ---- controls ---------------------------------------------------------------------
    def set_gaze(self, hz: float | None) -> None:
        if hz != self.gaze_hz:
            self.gaze_hz = hz
            self._gaze_onset_sample = self._generated

    def set_motion_profile(self, name: str) -> None:
        if name not in MOTION_PROFILES:
            raise ValueError(f"unknown motion profile {name}; one of {sorted(MOTION_PROFILES)}")
        self.head.set_profile(name)

    def perform_gesture(self, kind: str, reaction: bool = True) -> dict:
        """Virtual patient attempts a head gesture now (nod | shake | tilt). Synthetic."""
        if kind not in ("nod", "shake", "tilt"):
            raise ValueError("gesture must be nod, shake or tilt")
        return self.head.perform(kind, self._generated / self.sample_rate, reaction)

    def inject_blink(self, deliberate: bool = True) -> None:
        amp = self.rng.uniform(140, 190) if deliberate else self.rng.uniform(35, 60)
        dur = self.rng.uniform(0.35, 0.5) if deliberate else self.rng.uniform(0.2, 0.3)
        self._blinks.append((self._generated + int(0.05 * FS), amp, dur))

    # ---- generation -------------------------------------------------------------------
    def poll(self) -> None:
        if self._t0 is None:
            return
        due = int((self.clock() - self._t0) * self.sample_rate)
        n = min(due - self._generated, int(self.sample_rate))  # cap catch-up to 1 s
        if n <= 0:
            return
        if self.disconnected:
            # Samples are lost, as with a real Bluetooth outage. Advance the clock but write nothing.
            self._generated += n
            self._counter += n
            self.status.streaming = False
            return
        block = self.generate(n)
        if self.drop_probability > 0 and self.rng.random() < self.drop_probability:
            lost = max(1, n // 2)
            self._counter += lost
            self.status.dropped_samples += lost
            block = block[lost:]
        self.buffer.write(block)
        self.status.samples_received += block.shape[0]
        self.status.streaming = True
        self.status.last_sample_time = time.monotonic()

    def generate(self, n: int) -> np.ndarray:
        fs = self.sample_rate
        t_idx = np.arange(self._generated, self._generated + n)
        t = t_idx / fs
        n_eeg = len(self._labels)
        out = np.zeros((n, len(self.channels)), dtype=np.float64)

        # Background 1/f activity
        white = self.rng.standard_normal((n_eeg, n))
        pink, self._pink_zi = lfilter(_PINK_B, _PINK_A, white, axis=1, zi=self._pink_zi)
        eeg = (self._mix @ (pink * self._pink_gain)).T * self.background_uv * self.noise_multiplier

        # Alpha (posterior, amplitude-modulated) and line noise
        alpha_env = 0.6 + 0.4 * np.sin(2 * np.pi * 0.1 * t)
        alpha_gain = 3.0 if self.eyes_closed else 1.0
        eeg += np.outer(np.sin(2 * np.pi * 10.2 * t) * alpha_env, self._ssvep_w) * self.alpha_uv * alpha_gain
        eeg += np.sin(2 * np.pi * 60.0 * t)[:, None] * self.line_noise_uv * self.noise_multiplier

        # SSVEP response to the target the virtual patient is looking at
        if self.gaze_hz:
            f = self.gaze_hz
            since = (t_idx - self._gaze_onset_sample) / fs
            ramp = np.clip((since - 0.14) / 0.3, 0.0, 1.0)  # visual latency + build-up
            resp = np.zeros((n, n_eeg))
            for h, rel in ((1, 1.0), (2, 0.55), (3, 0.25)):
                resp += rel * np.sin(2 * np.pi * h * f * t[:, None] + h * self._ssvep_phase[None, :])
            eeg += resp * ramp[:, None] * self._ssvep_w[None, :] * self.ssvep_uv

        # Blinks (frontal-dominant slow waveform)
        keep = []
        for onset, amp, dur in self._blinks:
            rel = (t_idx - onset) / fs
            if rel[0] > dur + 0.1:
                continue
            keep.append((onset, amp, dur))
            shape = np.where((rel >= 0) & (rel <= dur), np.sin(np.pi * np.clip(rel / dur, 0, 1)) ** 2, 0.0)
            eeg += np.outer(shape * amp, self._blink_w)
        self._blinks = keep

        # Simulated poor electrode contact: large drifting noise on selected channels
        for j, lbl in enumerate(self._labels):
            if lbl in self.bad_channels:
                eeg[:, j] = (self.rng.standard_normal(n) * 120 + 400 * np.sin(2 * np.pi * 0.7 * t + self._drift_phase[j]))

        # Head motion: IMU channels + EEG motion artifact (own RNG, see module docstring)
        mot = self.head.generate(t)
        eeg += self.head.eeg_artifact(mot["speed_dps"], n_eeg)

        out[:, self._eeg_idx] = eeg
        acc_i = [c.index for c in self.channels if c.kind == "accel"]
        gyr_i = [c.index for c in self.channels if c.kind == "gyro"]
        for c in self.channels:
            if c.kind in ("accel", "gyro"):
                self.rng.standard_normal(n)  # keep the EEG random stream identical to earlier versions
            elif c.kind == "counter":
                out[:, c.index] = np.arange(self._counter + 1, self._counter + 1 + n)
            elif c.kind == "validation":
                out[:, c.index] = 1.0
        out[:, acc_i] = mot["accel"]
        out[:, gyr_i] = mot["gyro"]
        self._counter += n
        self._generated += n
        return out.astype(np.float32)
