"""Unicorn Hybrid Black hardware bring-up check (official UnicornPy API only).

Run from backend/:   .venv\\Scripts\\python scripts\\unicorn_hardware_check.py [--scan] [--seconds 10] [--serial UN-XXXX.XX.XX]

Steps (stops at the first failure and prints the exact error + shortest fix):
  1. Windows sees a Bluetooth adapter / the g.tec CSR8510 A10 dongle (PnP query)
  2. UnicornPy.IsDeviceLibraryLoadable()
  3/4. UnicornPy.GetBluetoothAdapterInfo() -> Name, Manufacturer, IsRecommendedDevice, HasProblem
  5. UnicornPy.GetAvailableDevices(True)   (paired headsets)
  6. optional: UnicornPy.GetAvailableDevices(False)  (unpaired scan, --scan)
  7. UnicornPy.Unicorn(serial)
  8. GetConfiguration() / GetNumberOfAcquiredChannels() -> channel layout
  9/10. StartAcquisition(False) + GetData loop (same buffer handling as the official NumPy
        example) for N seconds; verifies sample count, counter continuity, EEG plausibility.
        Saves raw data to recordings/hardware_check_<serial>.npz (origin=real).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from neurosteno.acquisition.unicorn import (DEFAULT_EEG_LABELS, classify_channel, find_unicorn_lib_dir,  # noqa: E402
                                           load_unicornpy)

OK, WARN, FAIL = "[ OK ]", "[WARN]", "[FAIL]"


def fail(msg: str, fix: str) -> None:
    print(f"{FAIL} {msg}")
    print(f"       FIX: {fix}")
    sys.exit(1)


def step1_windows_adapter() -> None:
    print("\n1. Windows Bluetooth adapters / g.tec dongle")
    ps = ("Get-PnpDevice -PresentOnly -Class Bluetooth | Where-Object { $_.InstanceId -like 'USB\\VID*' } | "
          "ForEach-Object { $_.Status + '|' + $_.FriendlyName + '|' + $_.InstanceId }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30).stdout
    except Exception as e:
        print(f"{WARN} could not query Windows PnP: {e}")
        return
    adapters = [line.split("|") for line in out.strip().splitlines() if line.strip()]
    if not adapters:
        fail("Windows reports no USB Bluetooth adapter.", "Plug in the g.tec USB Bluetooth dongle and wait for Windows to install it.")
    csr = [a for a in adapters if "VID_0A12" in a[2].upper()]
    for status, name, iid in adapters:
        print(f"   {status:6} {name}  ({iid})")
    csr_ok = [a for a in csr if a[0].strip().upper() == "OK"]
    if csr_ok:
        print(f"{OK} g.tec-recommended CSR dongle present and working (VID_0A12).")
    elif csr:
        print(f"{WARN} CSR dongle (VID_0A12) is plugged in but Windows reports status '{csr[0][0]}' (driver problem).")
        print("       Acquisition can still work over another adapter. To use the dongle: Device Manager -> the")
        print("       'Generic Bluetooth Radio' -> Update driver, and follow the Unicorn Suite adapter instructions.")
    else:
        print(f"{WARN} No CSR8510 (VID_0A12) dongle detected. g.tec recommends it; other adapters may or may not work.")
        print("       If you have the dongle: plug it in. If Windows grabs it with a generic driver that's fine;")
        print("       the Unicorn Suite installer documents which adapter to keep enabled.")


def check_other_gtec_processes() -> None:
    print()
    print("1b. Other g.tec applications")
    ps = ("Get-Process | Where-Object { $_.Path -match 'gtec|Unicorn' -or $_.ProcessName -match 'Unicorn|intendiX' } | "
          "ForEach-Object { $_.ProcessName + '|' + $_.Id }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30).stdout
    except Exception as e:
        print(f"{WARN} could not list processes: {e}")
        return
    procs = [line.split("|") for line in out.strip().splitlines() if line.strip()]
    if not procs:
        print(f"{OK} no g.tec applications running.")
        return
    for name, pid in procs:
        holder = name.lower() not in ("unicornsuite",)
        tag = WARN if holder else "      "
        note = ("may hold the headset connection; close it before Python acquisition" if holder
                else "launcher only; did not block acquisition in our tests, but close it if connect fails")
        print(f"{tag} {name} (pid {pid}): {note}")


def eeg_plausibility(eeg, fs: float) -> None:
    """Separate 'bytes are streaming' from 'this looks like scalp EEG'."""
    from scipy.signal import butter, sosfiltfilt
    seg = eeg[int(2 * fs):] if eeg.shape[0] > 4 * fs else eeg  # skip amplifier settling
    seg = seg - seg.mean(axis=0)
    xf = sosfiltfilt(butter(4, [1, 40], btype="bandpass", fs=fs, output="sos"), seg, axis=0)
    stds = xf.std(axis=0)
    drift = np.abs(eeg[-int(fs):].mean(axis=0) - eeg[: int(fs)].mean(axis=0))
    corr = np.abs(np.corrcoef(xf.T))
    mean_offdiag = (corr.sum() - len(corr)) / (len(corr) ** 2 - len(corr))
    print("   1-40 Hz std per channel (µV):", np.round(stds, 1).tolist())
    print("   DC drift first->last second (µV):", np.round(drift).tolist())
    print(f"   mean inter-channel |correlation| (1-40 Hz): {mean_offdiag:.3f}")
    problems = []
    if mean_offdiag > 0.95:
        problems.append("all channels carry the same signal -> reference/ground (ear clips) not on skin, or headset not worn")
    if np.median(stds) > 60:
        problems.append("amplitudes far above scalp EEG (typ. 5-50 µV) -> electrodes not in contact")
    if np.median(drift) > 5000:
        problems.append("large DC drift -> amplifier settling with floating electrodes")
    if np.median(stds) < 0.3:
        problems.append("flat signals")
    if problems:
        print(f"{WARN} Streaming works, but the data does NOT look like usable scalp EEG:")
        for p in problems:
            print(f"       - {p}")
        print("       FIX: wear the headset, attach both ear-clip electrodes (reference + ground) behind the ears,")
        print("       wet/gel the electrodes per g.tec instructions, wait ~30 s to settle, and rerun with --seconds 30.")
    else:
        print(f"{OK} signal amplitudes and channel independence look plausible for scalp EEG (not a decoding test).")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true", help="also scan for unpaired devices (slow)")
    ap.add_argument("--serial", help="connect to this serial instead of the first paired device")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--frame-length", type=int, default=1, help="frames per GetData call (official example uses 1)")
    args = ap.parse_args()

    step1_windows_adapter()
    check_other_gtec_processes()

    print("\n2. UnicornPy.IsDeviceLibraryLoadable()")
    print(f"   UnicornPy dir: {find_unicorn_lib_dir()}")
    try:
        U = load_unicornpy()
    except Exception as e:
        fail(f"UnicornPy import failed: {e}", "Install Unicorn Suite Hybrid Black, or set UNICORN_PY_DIR to the folder with UnicornPy.pyd.")
    print(f"   API version: {U.GetApiVersion()}, SamplingRate: {U.SamplingRate} Hz")
    try:
        print(f"{OK} IsDeviceLibraryLoadable() -> {U.IsDeviceLibraryLoadable()}")
    except Exception as e:
        # Observed on UnicornPy API 4.0 / CPython 3.12: SystemError "new style getargs format but argument is not a tuple".
        print(f"{WARN} IsDeviceLibraryLoadable() raised {type(e).__name__}: {e}")
        print("       This is a binding bug in this UnicornPy build; the import above already proved the library loads. Continuing.")

    print("\n3/4. UnicornPy.GetBluetoothAdapterInfo()")
    try:
        bt = U.GetBluetoothAdapterInfo()
    except Exception as e:
        fail(f"GetBluetoothAdapterInfo raised {type(e).__name__}: {e}", "Turn Bluetooth on in Windows Settings and plug in the dongle.")
    print(f"   Name: {bt.Name}\n   Manufacturer: {bt.Manufacturer}\n   IsRecommendedDevice: {bt.IsRecommendedDevice}\n   HasProblem: {bt.HasProblem}")
    if bt.HasProblem:
        print(f"{WARN} Adapter reports a problem. Re-plug the dongle / check Device Manager.")
    if not bt.IsRecommendedDevice:
        print(f"{WARN} The API is using '{bt.Name}', not the recommended {U.RecommendedBluetoothDeviceName} "
              f"({U.RecommendedBluetoothDeviceManufacturer}). It may still work; if pairing or streaming fails, "
              "plug in the g.tec dongle and disable the built-in adapter in Device Manager. Also disable Bluetooth power "
              "saving: Device Manager -> Bluetooth adapter -> Power Management -> untick 'Allow the computer to turn off "
              "this device to save power'.")
    else:
        print(f"{OK} Recommended adapter in use.")

    print("\n5. UnicornPy.GetAvailableDevices(True)  [paired]")
    try:
        paired = list(U.GetAvailableDevices(True) or [])
    except Exception as e:
        fail(f"GetAvailableDevices(True) raised {type(e).__name__}: {e}", "Check the Bluetooth adapter.")
    print(f"   paired: {paired}")
    devices = paired
    if not paired:
        print(f"{WARN} No paired Unicorn found.")
        if args.scan:
            print("\n6. UnicornPy.GetAvailableDevices(False)  [scanning for unpaired devices, can take ~10-30 s]")
            try:
                found = list(U.GetAvailableDevices(False) or [])
            except Exception as e:
                fail(f"GetAvailableDevices(False) raised {type(e).__name__}: {e}", "Check the Bluetooth adapter.")
            print(f"   discovered (unpaired): {found}")
            if found:
                fail("Headset is visible but not paired.",
                     f"Pair it in Windows Bluetooth settings (Add device -> Bluetooth -> {found[0]}, PIN 1234), then rerun.")
        fail("No Unicorn headset available.",
             "Switch the headset on (LED blinking), pair it in Windows Bluetooth settings (device 'UN-xxxx.xx.xx', "
             "PIN 1234), then rerun. Add --scan to search for unpaired headsets.")

    serial = args.serial or devices[0]
    print(f"\n7. UnicornPy.Unicorn('{serial}')")
    try:
        device = U.Unicorn(serial)
    except Exception as e:
        fail(f"Connect failed: {type(e).__name__}: {e}",
             "Make sure the headset is on and in range, no other program (Unicorn Recorder/Suite) holds it, and the "
             "Python API license is activated in Unicorn Suite. Then power-cycle the headset and rerun.")
    print(f"{OK} Connected.")

    try:
        print("\n8. Configuration")
        n_acq = device.GetNumberOfAcquiredChannels()
        cfg = device.GetConfiguration()
        enabled = [c for c in cfg.Channels if c.Enabled]
        print(f"   GetNumberOfAcquiredChannels: {n_acq}")
        for i, c in enumerate(enabled):
            print(f"   [{i:2}] {c.Name:24} kind={classify_channel(c.Name):10} unit={c.Unit:6} range={tuple(c.Range)}")
        eeg_idx = [i for i, c in enumerate(enabled) if classify_channel(c.Name) == "eeg"]
        cnt_idx = next((i for i, c in enumerate(enabled) if classify_channel(c.Name) == "counter"), None)
        bat_idx = next((i for i, c in enumerate(enabled) if classify_channel(c.Name) == "battery"), None)
        print(f"{OK} {len(eeg_idx)} EEG channels at indices {eeg_idx}")
        try:
            di = device.GetDeviceInformation()
            print("   device info:", {k: getattr(di, k) for k in dir(di) if not k.startswith('_') and not callable(getattr(di, k))})
        except Exception as e:
            print(f"   (GetDeviceInformation: {e})")

        print(f"\n9/10. Acquisition for {args.seconds:.0f} s (StartAcquisition(False), GetData frame length {args.frame_length})")
        frame = args.frame_length
        buf_len = frame * n_acq * 4
        rx = bytearray(buf_len)
        n_calls = int(args.seconds * U.SamplingRate / frame)
        chunks = []
        device.StartAcquisition(False)
        t0 = time.perf_counter()
        try:
            for i in range(n_calls):
                device.GetData(frame, rx, buf_len)
                chunks.append(np.frombuffer(rx, dtype=np.float32, count=n_acq * frame).reshape(frame, n_acq).copy())
                if i % int(U.SamplingRate / frame) == 0:
                    print(".", end="", flush=True)
        finally:
            elapsed = time.perf_counter() - t0
            device.StopAcquisition()
            print()
        data = np.vstack(chunks)
        print(f"   samples: {data.shape[0]} in {elapsed:.2f} s wall time -> {data.shape[0] / elapsed:.1f} Hz effective")
        if cnt_idx is not None:
            cnt = data[:, cnt_idx].astype(np.int64)
            gaps = np.diff(cnt) - 1
            lost = int(gaps[gaps > 0].sum())
            print(f"   counter {cnt[0]} -> {cnt[-1]}; dropped samples: {lost}")
            (print(f"{OK} counter continuous") if lost == 0 else print(f"{WARN} {lost} samples dropped (Bluetooth congestion/range)"))
        if bat_idx is not None:
            print(f"   battery level channel: {data[-1, bat_idx]:.1f}")
        eeg = data[:, eeg_idx]
        stds = eeg.std(axis=0)
        print("   EEG raw std per channel (µV, incl. DC):", np.round(stds, 1).tolist())
        flat = [i for i, s in zip(eeg_idx, stds) if s < 0.3]
        if flat:
            print(f"{WARN} near-flat channels at indices {flat} (electrode contact?)")
        if data.shape[0] >= int(0.98 * args.seconds * U.SamplingRate):
            print(f"{OK} {args.seconds:.0f} s of continuous real data streamed from the headset.")
        print()
        print("11. Signal plausibility (is it scalp EEG?)")
        eeg_plausibility(eeg.astype(float), float(U.SamplingRate))
        out = Path(__file__).resolve().parents[1] / "recordings" / f"hardware_check_{serial.replace('.', '_')}.npz"
        out.parent.mkdir(exist_ok=True)
        channels, eeg_i = [], 0
        for i, c in enumerate(enabled):
            kind = classify_channel(c.Name)
            label = ""
            if kind == "eeg":
                label = DEFAULT_EEG_LABELS[eeg_i] if eeg_i < len(DEFAULT_EEG_LABELS) else c.Name
                eeg_i += 1
            channels.append({"index": i, "name": c.Name, "kind": kind, "unit": c.Unit, "label": label})
        np.savez_compressed(out, data=data, sample_rate=np.float64(U.SamplingRate), channels=json.dumps(channels),
                            meta=json.dumps({"origin": "real", "device": serial, "created": time.time(),
                                             "duration_s": data.shape[0] / U.SamplingRate, "notes": "hardware check"}),
                            markers=json.dumps([]))
        print(f"   saved raw data -> {out}")
    except Exception as e:
        fail(f"{type(e).__name__}: {e}", "Keep the headset within ~2 m of the dongle, check battery, rerun.")
    finally:
        del device
        print("   disconnected.")


if __name__ == "__main__":
    main()
