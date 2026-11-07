r"""End-to-end on the REAL headset (no web server): live Unicorn EEG -> injected noise -> measured
SNR loss -> existing adaptive policy -> automatic choice count. Real-time; ~80 s.

Uses the ASSISTED wrapper with NO gaze/autopilot, so no simulated SSVEP is added: the processed
stream is the live headset EEG plus the injected noise only. (Plain REAL mode keeps 3/5 targets
locked until they are calibrated on the wearer.)

    .venv\Scripts\python scripts\real_noise_adapt_check.py [--high 35]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neurosteno.acquisition import unicorn as u  # noqa: E402
from neurosteno.acquisition.assisted import AssistedSource  # noqa: E402
from neurosteno.config import EngineConfig  # noqa: E402
from neurosteno.engine import Engine  # noqa: E402
from neurosteno.headless import isolated_store  # noqa: E402
from neurosteno.phrases.catalog import build_catalog  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--high", type=float, default=35.0)
    ap.add_argument("--serial")
    a = ap.parse_args()
    serial = a.serial or (u.discover(True) or [None])[0]
    if not serial:
        print("RESULT: no paired Unicorn"); return 2
    store, _ = isolated_store()
    cfg = EngineConfig()
    cfg.n_choices = 5
    eng = Engine(build_catalog(), store, cfg)
    src = AssistedSource(u.UnicornSource(serial))
    eng.set_source(src)
    eng.plan_now()
    eng.cfg.n_choices = 5
    eng.cfg.auto_adapt = True
    eng.cfg.adapt_source = "signal_quality"
    plan = [(0.0, 15), (a.high, 25), (0.0, 30)]
    seq = [eng.cfg.n_choices]
    try:
        for uv, dur in plan:
            src.set_injected_noise(uv)
            t0 = time.time()
            last = 0
            while time.time() - t0 < dur:
                eng.tick()
                if eng.cfg.n_choices != seq[-1]:
                    seq.append(eng.cfg.n_choices)
                if time.time() - last > 5:
                    last = time.time()
                    nz = eng.quality.get("noise", {})
                    d = eng.qa.get("decision") or {}
                    print(f"t+{time.time() - t0:4.1f}s inject {uv:4.1f} uV | raw {nz.get('raw_band_noise_uv')} uV "
                          f"proc {nz.get('processed_band_noise_uv')} uV loss {nz.get('snr_loss_db_smoothed')} dB | "
                          f"n={eng.cfg.n_choices} model->{d.get('chosen')} | samples {src.total_samples} "
                          f"streaming {src.status.streaming} gaze {src.gaze_hz} | phase {eng.phase.value}", flush=True)
                time.sleep(0.04)
    finally:
        eng.set_source(None)
    print("sequence", seq)
    print("switches", [(s["from"], s["to"], s["snr_loss_db"], s["injected_uv"]) for s in eng.qa["switches"]])
    ok = seq[0] == 5 and 2 in seq and seq[-1] == 5
    print("RESULT:", "ADAPTED DOWN AND BACK UP ON LIVE EEG" if ok else "DID NOT COMPLETE THE ROUND TRIP")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
