"""Device-agnostic acquisition interface.

Every EEG source (g.tec Unicorn, simulator, replay file, and later BrainFlow / LSL /
NerveNewt) implements :class:`EEGSource`. Sources push samples into a thread-safe ring
buffer; the engine pulls new samples with :meth:`EEGSource.read_new`.

A source always reports what it really is via ``kind``. The UI derives its mode badge
from that field so simulated or replayed data can never be presented as live EEG.
"""

from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass, field

import numpy as np


class SourceKind(str, enum.Enum):
    REAL = "real"
    SIMULATED = "simulated"
    REPLAY = "replay"
    ASSISTED = "assisted"  # real EEG + simulated SSVEP augmentation (demo fallback); never "real"


class AcquisitionError(RuntimeError):
    """Raised for any acquisition failure, with a user-facing message."""

    def __init__(self, message: str, code: str = "acquisition_error"):
        super().__init__(message)
        self.code = code


@dataclass
class ChannelInfo:
    index: int  # column index in the acquired frame
    name: str  # name reported by the device, e.g. "EEG 1"
    kind: str  # "eeg" | "accel" | "gyro" | "battery" | "counter" | "validation" | "other"
    unit: str = ""
    label: str = ""  # electrode site, e.g. "Oz" (only for EEG)

    def to_dict(self) -> dict:
        return {"index": self.index, "name": self.name, "kind": self.kind, "unit": self.unit, "label": self.label}


@dataclass
class SourceStatus:
    kind: SourceKind
    connected: bool = False
    streaming: bool = False
    device: str = ""
    message: str = ""
    error: str | None = None
    error_code: str | None = None
    samples_received: int = 0
    dropped_samples: int = 0  # from the device counter when available; never estimated for real data
    battery_percent: float | None = None
    last_sample_time: float | None = None  # time.monotonic() of the last received block
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        age = None if self.last_sample_time is None else round(time.monotonic() - self.last_sample_time, 3)
        return {
            "kind": self.kind.value,
            "connected": self.connected,
            "streaming": self.streaming,
            "device": self.device,
            "message": self.message,
            "error": self.error,
            "error_code": self.error_code,
            "samples_received": self.samples_received,
            "dropped_samples": self.dropped_samples,
            "battery_percent": self.battery_percent,
            "seconds_since_last_sample": age,
            "extra": self.extra,
        }


class RingBuffer:
    """Thread-safe 2-D ring buffer (samples x channels) with an absolute sample index."""

    def __init__(self, n_channels: int, capacity: int):
        self._buf = np.zeros((capacity, n_channels), dtype=np.float32)
        self._cap = capacity
        self._total = 0  # absolute number of samples ever written
        self._lock = threading.Lock()

    @property
    def total(self) -> int:
        with self._lock:
            return self._total

    def write(self, block: np.ndarray) -> None:
        block = np.asarray(block, dtype=np.float32)
        if block.ndim != 2 or block.shape[1] != self._buf.shape[1]:
            raise ValueError(f"block shape {block.shape} incompatible with buffer {self._buf.shape}")
        n = block.shape[0]
        if n == 0:
            return
        with self._lock:
            if n >= self._cap:
                block = block[-self._cap :]
                n = self._cap
            start = self._total % self._cap
            end = start + n
            if end <= self._cap:
                self._buf[start:end] = block
            else:
                k = self._cap - start
                self._buf[start:] = block[:k]
                self._buf[: n - k] = block[k:]
            self._total += n

    def read_range(self, abs_start: int, abs_end: int) -> np.ndarray:
        """Return samples with absolute indices [abs_start, abs_end). Clipped to what is retained."""
        with self._lock:
            abs_end = min(abs_end, self._total)
            abs_start = max(abs_start, self._total - self._cap, 0)
            n = abs_end - abs_start
            if n <= 0:
                return np.zeros((0, self._buf.shape[1]), dtype=np.float32)
            start = abs_start % self._cap
            end = start + n
            if end <= self._cap:
                return self._buf[start:end].copy()
            k = self._cap - start
            return np.vstack([self._buf[start:], self._buf[: n - k]])


class EEGSource:
    """Base class for all acquisition back ends."""

    kind: SourceKind = SourceKind.SIMULATED

    def __init__(self, sample_rate: float, channels: list[ChannelInfo], buffer_seconds: float = 120.0):
        self.sample_rate = float(sample_rate)
        self.channels = channels
        self.buffer = RingBuffer(len(channels), int(buffer_seconds * sample_rate))
        self.status = SourceStatus(kind=self.kind)

    # ---- channel helpers -------------------------------------------------------------
    @property
    def eeg_channels(self) -> list[ChannelInfo]:
        return [c for c in self.channels if c.kind == "eeg"]

    def eeg_indices(self, labels: list[str] | None = None) -> list[int]:
        """Column indices of EEG channels, optionally restricted to the given electrode labels."""
        eeg = self.eeg_channels
        if not labels:
            return [c.index for c in eeg]
        wanted = {lbl.lower() for lbl in labels}
        return [c.index for c in eeg if c.label.lower() in wanted or c.name.lower() in wanted]

    def channel_by_kind(self, kind: str) -> ChannelInfo | None:
        return next((c for c in self.channels if c.kind == kind), None)

    # ---- motion sensors (optional) -----------------------------------------------------
    def imu_layout(self) -> dict | None:
        """Column indices of the 3-axis accelerometer and gyroscope, or None if the source does not
        carry both (e.g. a recording made without motion channels). EEG never depends on this."""
        acc = [c.index for c in self.channels if c.kind == "accel"]
        gyr = [c.index for c in self.channels if c.kind == "gyro"]
        if len(acc) != 3 or len(gyr) != 3:
            return None
        return {"accel": acc, "gyro": gyr,
                "accel_unit": self.channels[acc[0]].unit, "gyro_unit": self.channels[gyr[0]].unit}

    def read_motion(self, abs_start: int, abs_end: int) -> dict | None:
        """Accelerometer (g) and gyroscope (deg/s) samples for absolute sample indices
        [abs_start, abs_end), timestamped on the SAME sample clock as the EEG (the Unicorn sends
        EEG and motion in one frame, so they are sample-aligned by construction).
        Returns None if the source has no motion channels."""
        lay = self.imu_layout()
        if lay is None:
            return None
        end = min(abs_end, self.total_samples)
        x = self.read_range(abs_start, end)
        n = x.shape[0]
        first = end - n
        idx = np.arange(first, end)
        return {"sample": idx, "t": idx / self.sample_rate, "fs": self.sample_rate,
                "accel_g": x[:, lay["accel"]].astype(np.float64), "gyro_dps": x[:, lay["gyro"]].astype(np.float64)}

    # ---- lifecycle (overridden) --------------------------------------------------------
    def start(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def poll(self) -> None:
        """Called periodically by the engine on the event loop (used by clock-driven sources)."""

    # ---- data access -------------------------------------------------------------------
    # ---- controlled noise injection (acquisition/noise_injection.py) ---------------------
    _injector = None

    def injector(self):
        if self._injector is None:
            from .noise_injection import NoiseInjector
            self._injector = NoiseInjector(self.sample_rate, [c.index for c in self.channels if c.kind == "eeg"])
        return self._injector

    @property
    def injected_noise_uv(self) -> float:
        return 0.0 if self._injector is None else self._injector.level_uv

    def set_injected_noise(self, uv: float) -> float:
        """Add controlled synthetic noise (uV RMS per EEG channel) to the PROCESSED stream from now on."""
        lvl = self.injector().set_level(uv, self.total_samples)
        self.status.extra["injected_noise_uv"] = lvl
        return lvl

    def read_range_raw(self, abs_start: int, abs_end: int) -> np.ndarray:
        """Unmodified samples as acquired (no injected noise)."""
        return self.buffer.read_range(abs_start, abs_end)

    def read_range(self, abs_start: int, abs_end: int) -> np.ndarray:
        """PROCESSED samples: raw + injected noise (identical to raw while the level is 0)."""
        x = self.buffer.read_range(abs_start, abs_end)
        inj = self._injector
        if inj is None or x.shape[0] == 0:
            return x
        first = min(abs_end, self.total_samples) - x.shape[0]
        return inj.apply(x, first)

    @property
    def total_samples(self) -> int:
        return self.buffer.total

    def info(self) -> dict:
        return {
            "kind": self.kind.value,
            "sample_rate": self.sample_rate,
            "channels": [c.to_dict() for c in self.channels],
            "status": self.status.to_dict(),
        }
