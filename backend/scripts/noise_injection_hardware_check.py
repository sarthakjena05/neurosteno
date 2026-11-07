r"""Real Unicorn: is the headset STREAMING, and does controlled noise injection change the measured
metric on the live EEG? Prints facts only. Does not start the web server.

    .venv\Scripts\python scripts\noise_injection_hardware_check.py [--serial UN-...] [--seconds 12]
"""
import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neurosteno.acquisition import unicorn as u  # noqa: E402
from neurosteno.acquisition.noise_injection import band_noise_uv  # noqa: E402

DECODING = ["Pz", "PO7", "Oz", "PO8"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial")
    ap.add_argument("--seconds", type=float, default=12.0)
    a = ap.parse_args()
    serial = a.serial or (u.discover(True) or [None])[0]
    if not serial:
        print("RESULT: no paired Unicorn found"); return 2
    src = u.UnicornSource(serial)
    print("device", serial, "| channel map:", src.status.extra.get("channel_map", {}).get("verified"))
    src.start()
    try:
        t0 = time.time()
        while time.time() - t0 < 4.0:
            time.sleep(0.1)
        n0 = src.total_samples
        time.sleep(2.0)
        rate = (src.total_samples - n0) / 2.0
        fs = src.sample_rate
        cols = src.eeg_indices(DECODING)
        print(f"samples received {src.total_samples} | measured rate {rate:.1f} Hz (nominal {fs:.0f}) | "
              f"dropped {src.status.dropped_samples} | streaming flag {src.status.streaming}")
        win = int(2 * fs)
        raw = src.read_range_raw(src.total_samples - win, src.total_samples)[:, src.eeg_indices()]
        print("raw EEG per-channel std (uV):", np.round(raw.std(axis=0), 1).tolist(),
              "| identical channels:", bool(np.allclose(raw, raw[:, :1])))
        for uv in (0.0, 5.0, 15.0, 0.0):
            src.set_injected_noise(uv)
            time.sleep(2.3)
            tot = src.total_samples
            p = band_noise_uv(src.read_range(tot - win, tot)[:, cols].astype(np.float64), fs)
            r = band_noise_uv(src.read_range_raw(tot - win, tot)[:, cols].astype(np.float64), fs)
            ident = np.array_equal(src.read_range(tot - win, tot), src.read_range_raw(tot - win, tot))
            print(f"injected {uv:5.1f} uV -> processed band noise {p:.2f} uV, raw {r:.2f} uV, "
                  f"SNR loss {20 * math.log10(p / r):.2f} dB, processed==raw: {ident}")
        ok = rate > 0.9 * fs
        print("RESULT:", "STREAMING" if ok else "NOT STREAMING (rate too low)")
        return 0 if ok else 1
    finally:
        src.stop()


if __name__ == "__main__":
    sys.exit(main())
