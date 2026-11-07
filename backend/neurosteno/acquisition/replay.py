"""Recording and replay of EEG sessions.

Recording format (.npz):
    data         float32 [n_samples, n_channels]  raw acquired frames
    sample_rate  float
    channels     JSON list of ChannelInfo dicts
    meta         JSON dict: origin ("real" | "simulated" | "replay"), device, created, notes
    markers      JSON list of {"sample": int, "event": str, ...} (stimulus/state annotations)

Replay streams the file in real time through exactly the same pipeline as live data.
The origin of the recording is always shown, so a replay of synthetic data is never
presented as real EEG.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from .base import AcquisitionError, ChannelInfo, EEGSource, SourceKind


class SessionRecorder:
    def __init__(self, source: EEGSource, path: Path, notes: str = ""):
        self.source = source
        self.path = Path(path)
        self.start_sample = source.total_samples
        self.markers: list[dict] = []
        self.notes = notes
        self.started = time.time()
        self._chunks: list[np.ndarray] = []
        self._pos = self.start_sample
        self.lost_samples = 0

    def flush(self) -> None:
        """Copy new samples out of the ring buffer. Call regularly (the engine does every tick):
        the source only retains ~2 min, so a long session read only at save() would be cut."""
        total = self.source.total_samples
        if total <= self._pos:
            return
        data = self.source.read_range(self._pos, total)
        lost = (total - self._pos) - data.shape[0]
        if lost > 0:  # overran the ring buffer: keep markers aligned with NaN rows
            self.lost_samples += lost
            self._chunks.append(np.full((lost, data.shape[1]), np.nan, dtype=np.float32))
        self._chunks.append(data.astype(np.float32))
        self._pos = total

    def mark(self, event: str, **fields) -> None:
        self.markers.append({"sample": self.source.total_samples - self.start_sample, "event": event, **fields})

    def save(self) -> dict:
        self.flush()
        n_ch = len(self.source.channels)
        data = np.concatenate(self._chunks) if self._chunks else np.zeros((0, n_ch), dtype=np.float32)
        meta = {
            "origin": self.source.kind.value,
            "device": self.source.status.device,
            "created": self.started,
            "duration_s": data.shape[0] / self.source.sample_rate,
            "notes": self.notes,
            "lost_samples": self.lost_samples,
        }
        if self.source.kind == SourceKind.REPLAY:
            meta["origin"] = self.source.status.extra.get("origin", "replay")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            self.path,
            data=data.astype(np.float32),
            sample_rate=np.float64(self.source.sample_rate),
            channels=json.dumps([c.to_dict() for c in self.source.channels]),
            meta=json.dumps(meta),
            markers=json.dumps(self.markers),
        )
        return {"path": str(self.path), "samples": int(data.shape[0]), **meta}


def load_recording(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise AcquisitionError(f"Recording not found: {path}", "recording_missing")
    try:
        z = np.load(path, allow_pickle=False)
        return {
            "data": z["data"].astype(np.float32),
            "sample_rate": float(z["sample_rate"]),
            "channels": [ChannelInfo(**c) for c in json.loads(str(z["channels"]))],
            "meta": json.loads(str(z["meta"])),
            "markers": json.loads(str(z["markers"])),
        }
    except AcquisitionError:
        raise
    except Exception as e:
        raise AcquisitionError(f"Could not read recording {path.name}: {e}", "recording_invalid") from e


def describe_recording(path: Path) -> dict:
    try:
        z = np.load(path, allow_pickle=False)
        meta = json.loads(str(z["meta"]))
        return {"name": path.name, "samples": int(z["data"].shape[0]), "sample_rate": float(z["sample_rate"]), **meta}
    except Exception as e:
        return {"name": path.name, "error": str(e)}


class ReplaySource(EEGSource):
    kind = SourceKind.REPLAY

    def __init__(self, path: Path, loop: bool = True, clock=time.monotonic):
        rec = load_recording(path)
        super().__init__(rec["sample_rate"], rec["channels"])
        self._data = rec["data"]
        self.meta = rec["meta"]
        self.markers = rec["markers"]
        self.loop = loop
        self.clock = clock
        self._t0: float | None = None
        self._pos = 0
        self._emitted = 0
        self.status.device = Path(path).name
        self.status.extra["origin"] = self.meta.get("origin", "unknown")
        self.status.extra["duration_s"] = round(self._data.shape[0] / self.sample_rate, 1)

    def start(self) -> None:
        self._t0 = self.clock()
        self._emitted = 0
        self.status.connected = True
        self.status.streaming = True
        origin = self.meta.get("origin", "unknown")
        self.status.message = f"REPLAY of recorded {origin.upper()} session '{self.status.device}'"

    def stop(self) -> None:
        self._t0 = None
        self.status.streaming = False
        self.status.connected = False

    def poll(self) -> None:
        if self._t0 is None:
            return
        due = int((self.clock() - self._t0) * self.sample_rate)
        n = min(due - self._emitted, int(self.sample_rate))
        if n <= 0:
            return
        out = []
        remaining = n
        total = self._data.shape[0]
        while remaining > 0:
            if self._pos >= total:
                if not self.loop:
                    self.status.streaming = False
                    self.status.message = "Replay finished"
                    break
                self._pos = 0
            take = min(remaining, total - self._pos)
            out.append(self._data[self._pos : self._pos + take])
            self._pos += take
            remaining -= take
        self._emitted += n
        if out:
            block = np.vstack(out)
            self.buffer.write(block)
            self.status.samples_received += block.shape[0]
            self.status.last_sample_time = time.monotonic()
        self.status.extra["position_s"] = round(self._pos / self.sample_rate, 1)
