"""Synthetic head motion for the simulator: accelerometer + gyroscope + EEG motion artifacts.

Everything here is SYNTHETIC. It exists so the real gesture detector, calibration and policy code
can be exercised and benchmarked before any patient has worn the headset. Nothing in this file is
a measured patient characteristic, except the stationary sensor noise floor:

  IMU noise floor (accel std ~0.0045 g, gyro std 0.18-0.73 deg/s, gyro bias 0.6-0.9 deg/s) is taken
  from a 10 s stationary recording of the Unicorn UN-2024.09.36 (not worn), 2026-11-07.

Model
  * Head orientation is a small rotation vector r(t) (head frame: x = pitch/nod, y = roll/tilt,
    z = yaw/shake), the sum of active movement events + optional tremor.
  * The sensor is mounted with an unknown rotation M (random per seed), so gesture axes in the
    device frame are NOT known in advance; calibration has to learn them.
  * gyro  = M (dr/dt) + bias + noise                       [deg/s]
    accel = M (R(r)^T g_up + a_lin) + noise                [g]
  * EEG motion artifact = coupling x |omega| (slow electrode/cable component, channel-specific
    sign) + EMG-like broadband burst proportional to |omega|.

Motion profiles (synthetic patient capability classes used by the multimodal benchmark):
  steady           voluntary gestures, no incidental movement, no EEG coupling (simulator default)
  no_head_control  no voluntary head movement (e.g. advanced ALS); occasional tiny involuntary motion
  accurate         clear nods/shakes/tilts, little incidental movement
  unreliable       weak, slow, variable gestures; frequent execution failures; moderate incidental motion
  high_motion      gestures possible, but frequent incidental movement (coughs, spasms, handling),
                   tremor and strong EEG motion artifacts
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# Measured stationary noise floor (see module docstring).
GYRO_NOISE_DPS = np.array([0.40, 0.72, 0.18])
GYRO_BIAS_DPS = np.array([0.71, 0.90, 0.61])
ACCEL_NOISE_G = 0.0045

AXES = {"pitch": np.array([1.0, 0.0, 0.0]), "roll": np.array([0.0, 1.0, 0.0]), "yaw": np.array([0.0, 0.0, 1.0])}
GESTURE_AXIS = {"nod": "pitch", "shake": "yaw", "tilt": "roll"}


@dataclass
class MotionProfile:
    name: str
    voluntary: bool = True
    amp_deg: dict = field(default_factory=lambda: {"nod": (15, 4), "shake": (18, 5), "tilt": (20, 4)})
    cycle_s: tuple = (0.7, 0.15)          # duration of one nod/shake cycle
    hold_s: tuple = (2.0, 0.3)            # tilt hold
    fail: float = 0.03                    # gesture not performed when cued
    wrong: float = 0.01                   # the other gesture performed instead
    reaction_s: tuple = (0.6, 0.15)
    incidental_per_min: float = 0.0
    incidental_amp_deg: tuple = (1.0, 5.0)
    incidental_mix: dict = field(default_factory=lambda: {"jerk": 0.4, "shift": 0.5, "oscillation": 0.1, "handling": 0.0})
    tremor_deg: float = 0.0
    tremor_hz: float = 5.0
    eeg_coupling_uv_per_dps: float = 0.0  # slow artifact
    emg_uv_per_dps: float = 0.0           # broadband burst

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


PROFILES: dict[str, MotionProfile] = {
    "steady": MotionProfile("steady"),
    "no_head_control": MotionProfile(
        "no_head_control", voluntary=False, incidental_per_min=0.3, incidental_amp_deg=(0.5, 2.0),
        incidental_mix={"jerk": 0.3, "shift": 0.7, "oscillation": 0.0, "handling": 0.0},
        eeg_coupling_uv_per_dps=0.3, emg_uv_per_dps=0.02),
    "accurate": MotionProfile(
        "accurate", incidental_per_min=1.0, incidental_amp_deg=(1.0, 5.0),
        eeg_coupling_uv_per_dps=0.3, emg_uv_per_dps=0.03),
    "unreliable": MotionProfile(
        "unreliable", amp_deg={"nod": (7, 4), "shake": (8, 4), "tilt": (9, 5)}, cycle_s=(1.1, 0.4),
        hold_s=(1.6, 0.5), fail=0.2, wrong=0.08, reaction_s=(1.2, 0.5), incidental_per_min=2.0,
        incidental_amp_deg=(2.0, 10.0), incidental_mix={"jerk": 0.35, "shift": 0.45, "oscillation": 0.2, "handling": 0.0},
        tremor_deg=0.5, eeg_coupling_uv_per_dps=0.5, emg_uv_per_dps=0.05),
    "high_motion": MotionProfile(
        "high_motion", amp_deg={"nod": (14, 5), "shake": (16, 5), "tilt": (18, 5)}, cycle_s=(0.8, 0.2),
        fail=0.05, wrong=0.03, reaction_s=(0.8, 0.25), incidental_per_min=6.0, incidental_amp_deg=(3.0, 20.0),
        incidental_mix={"jerk": 0.3, "shift": 0.3, "oscillation": 0.25, "handling": 0.15},
        tremor_deg=0.8, eeg_coupling_uv_per_dps=1.0, emg_uv_per_dps=0.12),
}


def _rodrigues(rv_deg: np.ndarray) -> np.ndarray:
    """Rotation matrices [n,3,3] from rotation vectors in degrees [n,3]."""
    th = np.deg2rad(np.linalg.norm(rv_deg, axis=1))
    k = np.zeros_like(rv_deg)
    nz = th > 1e-12
    k[nz] = rv_deg[nz] / np.linalg.norm(rv_deg[nz], axis=1, keepdims=True)
    K = np.zeros((len(rv_deg), 3, 3))
    K[:, 0, 1], K[:, 0, 2] = -k[:, 2], k[:, 1]
    K[:, 1, 0], K[:, 1, 2] = k[:, 2], -k[:, 0]
    K[:, 2, 0], K[:, 2, 1] = -k[:, 1], k[:, 0]
    s, c = np.sin(th)[:, None, None], np.cos(th)[:, None, None]
    return np.eye(3)[None] + s * K + (1 - c) * (K @ K)


def random_rotation(rng: np.random.Generator) -> np.ndarray:
    q = rng.standard_normal(4)
    q /= np.linalg.norm(q)
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


@dataclass
class _Event:
    onset: float          # seconds (sample clock)
    duration: float
    axis: np.ndarray      # head-frame unit vector
    amp: float            # degrees
    shape: str            # nod | shake | tilt | jerk | shift | oscillation | handling
    cycles: int = 1
    hold: float = 0.0
    lin_g: float = 0.0
    label: str = ""       # gesture kind for voluntary gestures, "" for incidental

    def angle(self, u: np.ndarray) -> np.ndarray:
        """Angle (deg) at times u since onset."""
        D = self.duration
        x = np.clip(u, 0, D)
        if self.shape in ("nod", "jerk"):
            cyc = D / self.cycles
            return self.amp * (1 - np.cos(2 * np.pi * x / cyc)) / 2
        if self.shape in ("shake", "oscillation"):
            cyc = D / self.cycles
            return self.amp * np.sin(2 * np.pi * x / cyc) * np.sin(np.pi * x / D)
        if self.shape in ("tilt", "shift", "handling"):
            ramp = min(0.4, D / 3)
            up = np.clip(x / ramp, 0, 1)
            down = np.clip((D - x) / ramp, 0, 1)
            sm = lambda v: v * v * (3 - 2 * v)  # noqa: E731
            return self.amp * np.minimum(sm(up), sm(down))
        return np.zeros_like(u)


class HeadMotion:
    """Streaming head-motion generator. Uses its OWN random generator so enabling motion never
    changes the synthetic EEG stream of an existing seed."""

    def __init__(self, profile: str | MotionProfile = "steady", seed: int | None = None, fs: float = 250.0):
        self.rng = np.random.default_rng(None if seed is None else seed + 7_000_003)
        self.fs = fs
        self.mount = random_rotation(self.rng)       # unknown sensor orientation on the head
        self.bias = GYRO_BIAS_DPS.copy()             # bias lives in the device frame
        self.profile = PROFILES[profile] if isinstance(profile, str) else profile
        self.events: list[_Event] = []
        self.tremor_axis = self._unit(self.rng.standard_normal(3))
        self.tremor_phase = self.rng.uniform(0, 2 * np.pi)
        self.next_incidental: float | None = None
        self.fault: str | None = None              # None | "nan" | "frozen" (sensor fault simulation)
        self._frozen: np.ndarray | None = None
        self._eeg_sign: np.ndarray | None = None
        self.performed: list[dict] = []            # log of cued gestures actually executed

    @staticmethod
    def _unit(v: np.ndarray) -> np.ndarray:
        return v / max(1e-12, float(np.linalg.norm(v)))

    def set_profile(self, name: str) -> None:
        self.profile = PROFILES[name]
        self.next_incidental = None

    # ---- voluntary gestures ----------------------------------------------------------------
    def perform(self, kind: str, at_s: float, reaction: bool = True) -> dict:
        """The virtual patient tries to perform a gesture (nod | shake | tilt) starting around at_s.
        Returns what was actually executed (may be nothing, or the wrong gesture)."""
        p, rng = self.profile, self.rng
        rec = {"cued": kind, "executed": None, "onset_s": None}
        if not p.voluntary or rng.random() < p.fail:
            self.performed.append(rec)
            return rec
        done = kind
        if rng.random() < p.wrong:
            done = {"nod": "shake", "shake": "nod", "tilt": "nod"}[kind]
        mu, sd = p.amp_deg[done]
        amp = max(0.5, rng.normal(mu, sd))
        onset = at_s + (max(0.2, rng.normal(*p.reaction_s)) if reaction else 0.0)
        axis = AXES[GESTURE_AXIS[done]] + rng.normal(0, 0.12, 3)  # imperfect execution
        if done == "tilt":
            hold = max(0.6, rng.normal(*p.hold_s))
            ev = _Event(onset, hold + 0.8, self._unit(axis), amp, "tilt", hold=hold, label=done)
        else:
            cyc = max(0.3, rng.normal(*p.cycle_s))
            cycles = 1 if rng.random() < 0.5 else 2
            ev = _Event(onset, cyc * cycles, self._unit(axis), amp, done, cycles=cycles, label=done)
        self.events.append(ev)
        rec.update(executed=done, onset_s=round(onset, 3), end_s=round(onset + ev.duration, 3), amp_deg=round(amp, 1))
        self.performed.append(rec)
        return rec

    # ---- incidental movement ------------------------------------------------------------
    def _schedule_incidental(self, t0: float, t1: float) -> None:
        p, rng = self.profile, self.rng
        if p.incidental_per_min <= 0:
            return
        rate = p.incidental_per_min / 60.0
        if self.next_incidental is None:
            self.next_incidental = t0 + rng.exponential(1 / rate)
        while self.next_incidental < t1:
            t = self.next_incidental
            kinds = list(p.incidental_mix)
            w = np.array([p.incidental_mix[k] for k in kinds], dtype=float)
            kind = kinds[int(rng.choice(len(kinds), p=w / w.sum()))]
            lo, hi = p.incidental_amp_deg
            amp = rng.uniform(lo, hi)
            if kind == "jerk":       # cough / swallow: quick pitch down-up (nod-like!)
                ev = _Event(t, rng.uniform(0.25, 0.5), self._unit(AXES["pitch"] + rng.normal(0, 0.3, 3)), amp, "jerk")
            elif kind == "shift":    # posture adjustment on a random axis, held, then back
                ev = _Event(t, rng.uniform(1.0, 4.0), self._unit(rng.standard_normal(3)), amp, "shift")
            elif kind == "oscillation":  # spasm / head bob: 2-3 cycles on a pitch- or yaw-biased axis
                base = AXES["pitch"] if rng.random() < 0.5 else AXES["yaw"]
                cyc = int(rng.integers(2, 4))
                ev = _Event(t, cyc / rng.uniform(1.0, 3.0), self._unit(base + rng.normal(0, 0.4, 3)), amp,
                            "oscillation", cycles=cyc)
            else:                    # handling / repositioning: large multi-axis + linear acceleration
                ev = _Event(t, rng.uniform(1.0, 2.5), self._unit(rng.standard_normal(3)), amp * 1.5, "handling",
                            lin_g=rng.uniform(0.15, 0.5))
            self.events.append(ev)
            self.next_incidental = t + rng.exponential(1 / rate)

    # ---- generation -----------------------------------------------------------------------
    def generate(self, t: np.ndarray, rng_noise: np.random.Generator | None = None) -> dict:
        """Head kinematics and sensor outputs at sample times t (seconds, increasing)."""
        n = len(t)
        rng = self.rng
        self._schedule_incidental(float(t[0]), float(t[-1]) + 1 / self.fs)
        rv = np.zeros((n, 3))
        drv = np.zeros((n, 3))
        lin = np.zeros((n, 3))
        h = 1e-3
        keep = []
        for ev in self.events:
            u = t - ev.onset
            if u[-1] < 0:
                keep.append(ev)
                continue
            if u[0] > ev.duration + 0.05:
                continue
            keep.append(ev)
            a = ev.angle(u)
            da = (ev.angle(u + h) - ev.angle(u - h)) / (2 * h)
            act = (u >= 0) & (u <= ev.duration)
            rv += np.outer(np.where(act, a, 0.0), ev.axis)
            drv += np.outer(np.where(act, da, 0.0), ev.axis)
            if ev.lin_g:
                env = np.where(act, np.sin(np.pi * np.clip(u / ev.duration, 0, 1)) ** 2, 0.0)
                lin += np.outer(env * ev.lin_g * np.sin(2 * np.pi * 2.0 * u), self._unit(np.array([0.3, 1.0, 0.2])))
        self.events = keep
        p = self.profile
        if p.tremor_deg > 0:
            w = 2 * np.pi * p.tremor_hz
            rv += np.outer(p.tremor_deg * np.sin(w * t + self.tremor_phase), self.tremor_axis)
            drv += np.outer(p.tremor_deg * w * np.cos(w * t + self.tremor_phase), self.tremor_axis)
        omega_head = drv                                   # deg/s (small-angle approximation)
        R = _rodrigues(rv)
        g_head = np.einsum("nji,j->ni", R, np.array([0.0, 0.0, 1.0]))   # R^T g_up
        gyro = omega_head @ self.mount.T + self.bias + rng.standard_normal((n, 3)) * GYRO_NOISE_DPS
        accel = (g_head + lin) @ self.mount.T + rng.standard_normal((n, 3)) * ACCEL_NOISE_G
        if self.fault == "nan":
            gyro[:] = np.nan
            accel[:] = np.nan
        elif self.fault == "frozen":
            if self._frozen is None:
                self._frozen = np.concatenate([accel[0], gyro[0]])
            accel[:] = self._frozen[:3]
            gyro[:] = self._frozen[3:]
        else:
            self._frozen = None
        return {"accel": accel, "gyro": gyro, "speed_dps": np.linalg.norm(omega_head, axis=1)}

    def eeg_artifact(self, speed_dps: np.ndarray, n_eeg: int) -> np.ndarray:
        """EEG motion artifact [n, n_eeg] (µV) for head angular speed (deg/s)."""
        p = self.profile
        if p.eeg_coupling_uv_per_dps <= 0 and p.emg_uv_per_dps <= 0:
            return np.zeros((len(speed_dps), n_eeg))
        if self._eeg_sign is None:
            self._eeg_sign = self.rng.uniform(-1, 1, n_eeg)
        slow = np.outer(speed_dps * p.eeg_coupling_uv_per_dps, self._eeg_sign)
        emg = self.rng.standard_normal((len(speed_dps), n_eeg)) * (speed_dps * p.emg_uv_per_dps)[:, None]
        return slow + emg


def gesture_kinematics_note() -> str:
    return ("Synthetic head-motion model (acquisition/motion_sim.py). Gesture amplitudes, timings, failure "
            "rates and incidental movement are modelling assumptions, not patient measurements.")
