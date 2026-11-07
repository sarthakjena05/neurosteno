"""g.tec Unicorn Hybrid Black adapter around the official UnicornPy library.

Lifecycle follows the official Python API documentation and the installed example
(UnicornPythonAcquisitionExampleNumpy.py):

    1. UnicornPy.GetAvailableDevices(onlyPaired)     -> discover
    2. UnicornPy.Unicorn(serial)                      -> connect (also validates license)
    3. device.GetConfiguration() / GetNumberOfAcquiredChannels()  -> channel layout
    4. device.StartAcquisition(testSignalEnabled)     -> start
    5. device.GetData(frames, buffer, bufferLength)   -> continuous retrieval (blocking)
    6. device.StopAcquisition(); del device           -> clean stop / disconnect

Channel roles are derived from the names the device reports in its configuration, never
from an assumed fixed layout. The adapter never reports "connected" unless the Unicorn
constructor succeeded, and never reports "streaming" unless samples are actually arriving.

Notes from local inspection (UnicornPy API 4.0, Windows):
  * UnicornPy.IsDeviceLibraryLoadable() raises SystemError in this build, so it is not used.
  * The .pyd links against python3.dll (stable ABI) and imports under CPython 3.12.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

from .base import AcquisitionError, ChannelInfo, EEGSource, SourceKind

log = logging.getLogger(__name__)

# g.tec's standard Unicorn Hybrid Black electrode montage (EEG 1..8). Overridable via config,
# because users can physically move electrodes.
DEFAULT_EEG_LABELS = ["Fz", "C3", "Cz", "C4", "Pz", "PO7", "Oz", "PO8"]

DEFAULT_LIB_DIRS = [
    Path.home() / "Documents" / "gtec" / "Unicorn Suite" / "Hybrid Black" / "Unicorn Python" / "Lib",
    Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Documents" / "gtec" / "Unicorn Suite" / "Hybrid Black" / "Unicorn Python" / "Lib",
]

_unicorn_module = None
_unicorn_import_error: str | None = None


def find_unicorn_lib_dir() -> Path | None:
    env = os.environ.get("UNICORN_PY_DIR")
    candidates = ([Path(env)] if env else []) + DEFAULT_LIB_DIRS
    for c in candidates:
        if (c / "UnicornPy.pyd").exists() or (c / "UnicornPy.py").exists():
            return c
    return None


def load_unicornpy():
    """Import UnicornPy once. Returns the module or raises AcquisitionError."""
    global _unicorn_module, _unicorn_import_error
    if _unicorn_module is not None:
        return _unicorn_module
    lib_dir = find_unicorn_lib_dir()
    if lib_dir is None:
        _unicorn_import_error = (
            "UnicornPy library not found. Install the Unicorn Suite (Hybrid Black) and/or set "
            "UNICORN_PY_DIR to the folder containing UnicornPy.pyd."
        )
        raise AcquisitionError(_unicorn_import_error, "library_missing")
    try:
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(lib_dir))
        if str(lib_dir) not in sys.path:
            sys.path.insert(0, str(lib_dir))
        import UnicornPy  # type: ignore

        _unicorn_module = UnicornPy
        return UnicornPy
    except Exception as e:  # ImportError, DLL load failures, ABI mismatch
        _unicorn_import_error = f"UnicornPy failed to import from {lib_dir}: {e}"
        raise AcquisitionError(_unicorn_import_error, "library_import_failed") from e


def library_info() -> dict:
    """Non-throwing diagnostic summary for the UI."""
    try:
        U = load_unicornpy()
    except AcquisitionError as e:
        return {"available": False, "error": str(e), "code": e.code}
    info = {"available": True, "lib_dir": str(find_unicorn_lib_dir()), "sampling_rate": U.SamplingRate}
    try:
        info["api_version"] = U.GetApiVersion()
    except Exception as e:
        info["api_version_error"] = str(e)
    try:
        bt = U.GetBluetoothAdapterInfo()
        info["bluetooth_adapter"] = {
            "name": bt.Name,
            "manufacturer": bt.Manufacturer,
            "is_recommended_device": bool(bt.IsRecommendedDevice),
            "has_problem": bool(bt.HasProblem),
        }
    except Exception as e:
        info["bluetooth_adapter_error"] = _describe_exception(e)
    return info


def _describe_exception(e: Exception) -> str:
    msg = str(e) or type(e).__name__
    low = msg.lower()
    if "licen" in low:
        return f"Unicorn license problem: {msg}"
    if "bluetooth" in low:
        return f"Bluetooth problem: {msg}. Check that the Bluetooth adapter is on and the headset is paired."
    return msg


def classify_channel(name: str) -> str:
    n = name.lower()
    if n.startswith("eeg"):
        return "eeg"
    if n.startswith("acc"):
        return "accel"
    if n.startswith("gyr"):
        return "gyro"
    if "battery" in n:
        return "battery"
    if "counter" in n:
        return "counter"
    if "validation" in n:
        return "validation"
    return "other"


# Units the Unicorn reports for its motion channels (confirmed on UN-2024.09.36, 2026-11-07:
# "Accelerometer X/Y/Z" in g, "Gyroscope X/Y/Z" in deg/s, stationary accel norm 1.03 g).
EXPECTED_UNITS = {"eeg": "µV", "accel": "g", "gyro": "deg/s"}


def normalize_unit(unit: str) -> str:
    """UnicornPy returns UTF-8 bytes that arrive decoded as Latin-1 ("Âµ" for "µ"). Repair that."""
    u = str(unit or "")
    try:
        fixed = u.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        fixed = u
    return fixed.replace("\u03bc", "µ")


def verify_channel_map(channels: list[ChannelInfo], U=None) -> dict:
    """Check the name-derived channel roles against the library's own layout constants
    (UnicornPy.EEGConfigIndex / AccelerometerConfigIndex / GyroscopeConfigIndex ...) and the
    expected units. Missing motion channels are reported, not treated as an error: EEG-only
    operation never needs them."""
    checks = []

    def idx(kind):
        return [c.index for c in channels if c.kind == kind]

    expect = {}
    if U is not None:
        try:
            expect = {
                "eeg": list(range(U.EEGConfigIndex, U.EEGConfigIndex + U.EEGChannelsCount)),
                "accel": list(range(U.AccelerometerConfigIndex, U.AccelerometerConfigIndex + U.AccelerometerChannelsCount)),
                "gyro": list(range(U.GyroscopeConfigIndex, U.GyroscopeConfigIndex + U.GyroscopeChannelsCount)),
                "battery": [U.BatteryConfigIndex], "counter": [U.CounterConfigIndex],
                "validation": [U.ValidationConfigIndex],
            }
        except AttributeError:
            expect = {}
    for kind in ("eeg", "accel", "gyro", "battery", "counter", "validation"):
        got = idx(kind)
        row = {"kind": kind, "indices": got}
        if kind in expect:
            row["library_indices"] = expect[kind]
            row["ok"] = got == expect[kind]
        else:
            row["ok"] = bool(got) if kind == "eeg" else None
        if kind in EXPECTED_UNITS and got:
            units = sorted({channels[i].unit for i in got})
            row["units"] = units
            row["unit_ok"] = units == [EXPECTED_UNITS[kind]]
        checks.append(row)
    imu = len(idx("accel")) == 3 and len(idx("gyro")) == 3
    ok = all(r["ok"] is not False and r.get("unit_ok", True) for r in checks if r["kind"] in ("eeg", "accel", "gyro"))
    return {"verified": bool(ok) and bool(expect), "imu_present": imu, "checks": checks,
            "note": ("Channel roles come from the names the device reports; they match the UnicornPy layout "
                     "constants." if ok and expect else
                     "Channel roles come from the names the device reports; layout check failed or unavailable.")}


def discover(only_paired: bool = True) -> list[str]:
    U = load_unicornpy()
    try:
        devices = U.GetAvailableDevices(only_paired)
    except Exception as e:
        raise AcquisitionError(f"Device discovery failed: {_describe_exception(e)}", "discovery_failed") from e
    return list(devices or [])


class UnicornSource(EEGSource):
    kind = SourceKind.REAL

    def __init__(self, serial: str, eeg_labels: list[str] | None = None, frame_length: int = 10,
                 test_signal: bool = False):
        self._U = load_unicornpy()
        self.serial = serial
        self.frame_length = int(frame_length)
        self.test_signal = bool(test_signal)
        self._device = None
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._last_counter: float | None = None
        labels = eeg_labels or DEFAULT_EEG_LABELS

        # --- 2. connect ---------------------------------------------------------------
        try:
            self._device = self._U.Unicorn(serial)
        except Exception as e:
            raise AcquisitionError(f"Could not connect to Unicorn '{serial}': {_describe_exception(e)}",
                                   "connect_failed") from e

        # --- 3. read the actual channel configuration ------------------------------------
        try:
            n_acq = int(self._device.GetNumberOfAcquiredChannels())
            config = self._device.GetConfiguration()
            enabled = [ch for ch in config.Channels if ch.Enabled]
        except Exception as e:
            self._close_device()
            raise AcquisitionError(f"Could not read Unicorn configuration: {_describe_exception(e)}",
                                   "config_failed") from e
        if len(enabled) != n_acq:
            self._close_device()
            raise AcquisitionError(
                f"Unicorn reported {n_acq} acquired channels but {len(enabled)} enabled channels in its configuration.",
                "config_mismatch")

        channels: list[ChannelInfo] = []
        eeg_i = 0
        for i, ch in enumerate(enabled):
            kind = classify_channel(ch.Name)
            label = ""
            if kind == "eeg":
                label = labels[eeg_i] if eeg_i < len(labels) else ch.Name
                eeg_i += 1
            channels.append(ChannelInfo(index=i, name=ch.Name, kind=kind, unit=normalize_unit(ch.Unit), label=label))
        if eeg_i == 0:
            self._close_device()
            raise AcquisitionError("Unicorn configuration contains no enabled EEG channels.", "no_eeg")

        super().__init__(float(self._U.SamplingRate), channels)
        self.n_acquired = n_acq
        self.status.extra["channel_map"] = verify_channel_map(channels, self._U)
        self.status.connected = True
        self.status.device = serial
        self.status.message = f"Connected to {serial} ({n_acq} channels, {eeg_i} EEG, {self.sample_rate:.0f} Hz)"
        try:
            di = self._device.GetDeviceInformation()
            self.status.extra["device_info"] = {
                k: str(getattr(di, k)) for k in dir(di) if not k.startswith("_") and not callable(getattr(di, k))
            }
        except Exception:  # informational only
            pass

    # --- 4/5. start + continuous retrieval --------------------------------------------
    def start(self) -> None:
        if self._device is None:
            raise AcquisitionError("Unicorn device is not connected.", "not_connected")
        if self._thread and self._thread.is_alive():
            return
        try:
            self._device.StartAcquisition(self.test_signal)
        except Exception as e:
            raise AcquisitionError(f"StartAcquisition failed: {_describe_exception(e)}", "start_failed") from e
        self._stop_evt.clear()
        self.status.message = "Acquisition started; waiting for samples"
        self._thread = threading.Thread(target=self._run, name="unicorn-acq", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        n_ch = self.n_acquired
        buf_len = self.frame_length * n_ch * 4
        rx = bytearray(buf_len)
        counter_ch = self.channel_by_kind("counter")
        battery_ch = self.channel_by_kind("battery")
        validation_ch = self.channel_by_kind("validation")
        while not self._stop_evt.is_set():
            try:
                self._device.GetData(self.frame_length, rx, buf_len)
            except Exception as e:
                if self._stop_evt.is_set():
                    break
                self.status.streaming = False
                self.status.connected = False
                self.status.error = f"Acquisition error: {_describe_exception(e)}"
                self.status.error_code = "acquisition_lost"
                log.error("Unicorn acquisition stopped: %s", e)
                break
            data = np.frombuffer(rx, dtype=np.float32, count=n_ch * self.frame_length).reshape(self.frame_length, n_ch)
            self.buffer.write(data)
            self.status.samples_received += self.frame_length
            self.status.streaming = True
            self.status.last_sample_time = time.monotonic()
            if counter_ch is not None:
                counts = data[:, counter_ch.index].astype(np.int64)
                if self._last_counter is not None:
                    seq = np.concatenate([[int(self._last_counter)], counts])
                else:
                    seq = counts
                gaps = np.diff(seq) - 1
                self.status.dropped_samples += int(gaps[gaps > 0].sum())
                self._last_counter = counts[-1]
            if battery_ch is not None:
                self.status.battery_percent = float(data[-1, battery_ch.index])
            if validation_ch is not None:
                self.status.extra["validation_indicator"] = float(data[-1, validation_ch.index])

    # --- 6. stop + disconnect -----------------------------------------------------------
    def stop(self) -> None:
        self._stop_evt.set()
        if self._device is not None:
            try:
                self._device.StopAcquisition()
            except Exception as e:  # already stopped or connection lost
                log.info("StopAcquisition: %s", e)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.status.streaming = False
        self._close_device()

    def _close_device(self) -> None:
        if self._device is not None:
            try:
                del self._device
            finally:
                self._device = None
        if hasattr(self, "status"):
            self.status.connected = False
            self.status.message = "Disconnected"
