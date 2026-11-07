"""Real-EEG signal check for the Unicorn Hybrid Black (official UnicornPy only).

Run from backend/ with the headset ON the head and BOTH ear clips attached:

    .venv\\Scripts\\python scripts\\real_signal_check.py --serial UN-2024.09.36

Stages
  1. Connect through neurosteno.acquisition.unicorn.UnicornSource (same code as the backend).
  2. Electrode fitting: a live per-electrode contact table every 2 s for --fit seconds
     (heuristic from the signal; UnicornPy exposes no impedance). Adjust electrodes until the
     decoding channels Pz, PO7, Oz, PO8 read "good". Ctrl+C skips ahead.
  3. Alpha check (Berger effect): --cycles x (eyes OPEN 10 s, eyes CLOSED 10 s) with spoken-
     style terminal cues and a beep. Occipital alpha must rise with eyes closed, consistently,
     before we call the signal "physiological EEG".
  4. Saves recordings/real_signal_check_<stamp>.npz (origin=real, montage labels, markers) for
     REPLAY mode and offline analysis.

Exit code 0 = EEG confirmed; 2 = streamed but EEG not confirmed; 1 = acquisition failure.
Use --sim to dry-run the whole flow with the simulator (results are labelled SIMULATED).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from neurosteno.acquisition.base import AcquisitionError  # noqa: E402
from neurosteno.acquisition.replay import SessionRecorder  # noqa: E402
from neurosteno.config import RECORDINGS_DIR  # noqa: E402
from neurosteno.signal.electrodes import (ElectrodeConfig, alpha_reactivity, channel_contacts,  # noqa: E402
                                          summarize_contacts)

DECODING = ["Pz", "PO7", "Oz", "PO8"]
SYM = {"good": "OK  ", "suspect": "WARN", "bad": "BAD ", "no_data": "... "}


def beep() -> None:
    try:
        import winsound
        winsound.Beep(880, 250)
    except Exception:
        print("\a", end="", flush=True)


def pump(src, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        src.poll()
        time.sleep(0.02)


def eeg_window(src, seconds: float) -> tuple[np.ndarray, list[str]]:
    cols = src.eeg_indices()
    total = src.total_samples
    win = src.read_range(max(0, total - int(seconds * src.sample_rate)), total)[:, cols].astype(np.float64)
    return win, [c.label or c.name for c in src.eeg_channels]


def print_contacts(src, cfg: ElectrodeConfig) -> dict:
    win, labels = eeg_window(src, 4.0)
    contacts = channel_contacts(win, src.sample_rate, labels, cfg)
    summ = summarize_contacts(contacts, DECODING)
    st = src.status
    print(f"\n[{time.strftime('%H:%M:%S')}] samples={st.samples_received} dropped={st.dropped_samples} "
          f"battery={st.battery_percent if st.battery_percent is not None else 'n/a'}  ->  {summ['headline']}")
    print("   ch    contact  score  1-40Hz std  drift uV/s  |corr|  mains  EMG   issues")
    for c in contacts:
        m = c["metrics"]
        if not m:
            print(f"   {c['label']:5} {SYM[c['verdict']]}")
            continue
        dec = "*" if c["label"] in DECODING else " "
        print(f"  {dec}{c['label']:5} {SYM[c['verdict']]}    {c['contact_score']:.2f}  {m['std_uv']:9.1f}  "
              f"{m['drift_uv_per_s']:10.1f}  {m['mean_corr'] if m['mean_corr'] is not None else '-':>6}  "
              f"{m['line_ratio']:5.2f}  {m['emg_ratio']:.2f}  {', '.join(c['issues']) or '-'}")
    advice = sorted({a for c in contacts for a in c["advice"]})
    for a in advice[:4]:
        print(f"   -> {a}")
    return summ


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default=None, help="headset serial (default: first paired)")
    ap.add_argument("--fit", type=float, default=60.0, help="electrode-fitting seconds (Ctrl+C to skip)")
    ap.add_argument("--cycles", type=int, default=3, help="eyes-open/closed cycles")
    ap.add_argument("--eyes-s", type=float, default=10.0)
    ap.add_argument("--settle", type=float, default=5.0, help="seconds discarded after start (amplifier settling)")
    ap.add_argument("--sim", action="store_true", help="dry run with the simulator (NOT real EEG)")
    args = ap.parse_args()
    cfg = ElectrodeConfig()

    if args.sim:
        from neurosteno.acquisition.simulated import SimulatedSource
        src = SimulatedSource(seed=None)
        print("*** SIMULATED dry run: synthetic signals, NOT real EEG ***")
    else:
        from neurosteno.acquisition.unicorn import UnicornSource, discover
        try:
            serial = args.serial or (discover(True) or [None])[0]
            if not serial:
                print("[FAIL] No paired Unicorn found. FIX: pair it in Windows Bluetooth settings (PIN 1234), "
                      "or pass --serial.")
                return 1
            print(f"Connecting to {serial} via UnicornPy ...")
            src = UnicornSource(serial)
        except AcquisitionError as e:
            print(f"[FAIL] {e} (code {e.code})")
            print("       FIX: close Unicorn Recorder/Suite apps holding the headset, power-cycle it, keep it within 2 m.")
            return 1
    src.start()
    print(src.status.message)
    pump(src, args.settle)
    if src.total_samples == 0:
        print("[FAIL] Connected but no samples arrived. FIX: power-cycle the headset and rerun.")
        src.stop()
        return 1

    rec = SessionRecorder(src, RECORDINGS_DIR / f"{'simulated' if args.sim else 'real'}_signal_check_"
                                              f"{time.strftime('%Y%m%d_%H%M%S')}.npz",
                          notes="real_signal_check: electrode fit + eyes open/closed alpha")
    result: dict = {"origin": "simulated" if args.sim else "real"}
    try:
        # ---- electrode fitting ------------------------------------------------------
        print(f"\n=== Electrode fitting ({args.fit:.0f} s, Ctrl+C to continue) ===")
        print("Sit still, relax jaw and neck, eyes open. '*' = SSVEP decoding channel.")
        rec.mark("fit_start")
        summ = None
        try:
            end = time.monotonic() + args.fit
            while time.monotonic() < end:
                pump(src, 2.0)
                rec.flush()
                summ = print_contacts(src, cfg)
        except KeyboardInterrupt:
            print("\n(skipping to the alpha check)")
        result["contacts_final"] = summ
        if summ and summ["reference_off"]:
            print("\n[WARN] Reference/ground look disconnected; the alpha check will be invalid until fixed.")

        # ---- alpha check ------------------------------------------------------------
        print(f"\n=== Alpha check: {args.cycles} x (eyes OPEN {args.eyes_s:.0f} s, eyes CLOSED {args.eyes_s:.0f} s) ===")
        print("Keep still. Follow the cue after each beep. Starting in 3 s ...")
        pump(src, 3.0)
        open_segs, closed_segs = [], []
        for cyc in range(args.cycles):
            for state in ("eyes_open", "eyes_closed"):
                beep()
                print(f"  cycle {cyc + 1}/{args.cycles}: {'OPEN your eyes, look at a fixed point' if state == 'eyes_open' else 'CLOSE your eyes, stay relaxed'}")
                rec.mark("alpha_segment", label=state, cycle=cyc)
                if args.sim:
                    src.eyes_closed = state == "eyes_closed"
                s0 = src.total_samples
                pump(src, args.eyes_s)
                rec.flush()
                seg = src.read_range(s0, src.total_samples)[:, src.eeg_indices()].astype(np.float64)
                (open_segs if state == "eyes_open" else closed_segs).append(seg)
        beep()
        print("  Done, you can open your eyes.")
        labels = [c.label or c.name for c in src.eeg_channels]
        res = alpha_reactivity(open_segs, closed_segs, src.sample_rate, labels, cfg)
        result["alpha"] = res
        print("\n   ch    closed/open  AUC   cycles  peak Hz  reactive")
        for r in res["channels"]:
            occ = "*" if r["occipital"] else " "
            print(f"  {occ}{r['label']:5} {r['ratio']:9.2f}  {r['auc']:.2f}   {r['cycles_up']}/{r['cycles']}    "
                  f"{r['peak_hz']:5.2f}   {'YES' if r['reactive'] else 'no'}{'  (artifact)' if r['artifact'] else ''}")
        tag = "[ OK ]" if res["verdict"] == "eeg_confirmed" else "[WARN]"
        print(f"\n{tag} {res['verdict']}: {res['note']}")
        if args.sim:
            print("       (SIMULATED: this says nothing about the real headset)")
    finally:
        info = rec.save()
        src.stop()
        out = Path(info["path"]).with_suffix(".json")
        result["recording"] = info
        out.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
        print(f"\nSaved {info['duration_s']:.1f} s ({info['origin']}) -> {info['path']}")
        print(f"Report -> {out}")
        print(f"Dropped samples: {src.status.dropped_samples}")
    return 0 if result.get("alpha", {}).get("verdict") == "eeg_confirmed" else 2


if __name__ == "__main__":
    sys.exit(main())
