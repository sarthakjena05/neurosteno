r"""Build the reliability-vs-SNR-loss table used by quality-driven adaptation (SIMULATED reference).

For n = 2, 3, 5 targets: calibrate the real FBCCA + dynamic stopping on clean synthetic SSVEP
(engine protocol), then decode held-out synthetic trials with the SAME controlled noise injector
used live (acquisition/noise_injection.py) at increasing levels. For every level we MEASURE the
SNR loss exactly as the live engine does (band noise of processed vs raw EEG, 3-40 Hz) and record
accuracy of accepted decisions, abstention rate and mean decision window.

Output: neurosteno/data/noise_reliability.json (committed). Run:
    .venv\Scripts\python scripts\build_noise_reliability.py
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neurosteno.acquisition.noise_injection import NoiseInjector, band_noise_uv  # noqa: E402
from neurosteno.acquisition.simulated import SimulatedSource  # noqa: E402
from neurosteno.benchmark.pool import DECODING, FREQS, PAD_S, STIM_S, _trials, stopper_replay  # noqa: E402
from neurosteno.signal.calibration import Trial, calibrate  # noqa: E402
from neurosteno.signal.fbcca import FBCCAConfig, StoppingConfig, fbcca_scores  # noqa: E402

FS = 250.0
LEVELS_UV = [0, 2, 4, 6, 8, 10, 12, 15, 18, 22, 27, 33, 40]


def noisy_trials(src, freqs, labels, cols, length_s, inj, level, counter):
    """Like benchmark.pool._trials, with the live injector applied at absolute sample numbers."""
    out, loss = [], []
    lat = FBCCAConfig().latency_s
    inj.set_level(level, 0)
    for lab in labels:
        src.set_gaze(None)
        pad_raw = src.generate(int(PAD_S * FS))
        pad = inj.apply(pad_raw, counter[0]); counter[0] += pad_raw.shape[0]
        src.set_gaze(None if lab is None else freqs[lab])
        raw = src.generate(int((lat + length_s) * FS))
        x = inj.apply(raw, counter[0]); counter[0] += raw.shape[0]
        src.set_gaze(None)
        gap = src.generate(int(0.3 * FS)); counter[0] += gap.shape[0]
        # SNR loss measured on the stimulus-free pad + gap is too short; measure on the trial itself
        # (processed vs raw), which is what the live monitor sees in a 2 s window.
        p, r = band_noise_uv(x[:, cols].astype(np.float64), FS), band_noise_uv(raw[:, cols].astype(np.float64), FS)
        if p and r:
            loss.append(20 * np.log10(p / r))
        out.append(Trial(x[int(lat * FS):, cols].astype(np.float64), lab, pad[:, cols].astype(np.float64)))
    return out, float(np.median(loss)) if loss else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=16, help="held-out trials per target per level")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "neurosteno" / "data" / "noise_reliability.json"))
    a = ap.parse_args()
    t0 = time.time()
    fb = FBCCAConfig()
    table = {"kind": "noise_reliability", "data_origin": "simulated",
             "description": ("Decoder reliability vs measured SNR loss from injected noise. Real FBCCA + dynamic "
                             "stopping calibrated on clean SIMULATED SSVEP, decoded under the live noise injector. "
                             "A reference model, not a measurement on the wearer."),
             "metric": "snr_loss_db = 20*log10(band_noise(processed)/band_noise(raw)), 3-40 Hz, decoding channels",
             "levels_uv": LEVELS_UV, "trials_per_target": a.trials, "seed": a.seed, "by_n": {}}
    for n in (2, 3, 5):
        freqs = FREQS[n]
        src = SimulatedSource(seed=a.seed * 100 + n)
        cols = src.eeg_indices(DECODING)
        rng = np.random.default_rng(a.seed * 100 + n)
        plan = [i for i in range(n) for _ in range(5)] + [None] * 8
        rng.shuffle(plan)
        cal_trials = _trials(src, freqs, plan, cols, STIM_S)
        max_w = STIM_S - fb.latency_s - 0.1
        cal = calibrate(cal_trials, freqs, FS, fb, 1.0, max_w, 0.25)
        st = StoppingConfig.from_dict(cal["stopping"])
        windows = [round(float(w), 3) for w in np.arange(st.min_window_s, st.max_window_s + 1e-9, st.step_s)]
        test_src = SimulatedSource(seed=a.seed * 100 + 50 + n)
        inj = NoiseInjector(FS, test_src.eeg_indices())
        counter = [0]
        rows = []
        for lvl in LEVELS_UV:
            labels = [i for i in range(n) for _ in range(a.trials)]
            trials, loss = noisy_trials(test_src, freqs, labels, cols, max_w + 0.05, inj, lvl, counter)
            res = [stopper_replay([fbcca_scores(t.eeg[: int(w * FS)], FS, freqs, fb, t.pad) for w in windows],
                                  windows, st) for t in trials]
            acc = [r[0] == t.label for r, t in zip(res, trials) if r[0] is not None]
            dec = [r[1] for r in res if r[0] is not None]
            rows.append({"injected_uv": lvl, "snr_loss_db": round(loss, 2), "trials": len(trials),
                         "accepted": len(acc), "accuracy": round(float(np.mean(acc)), 3) if acc else None,
                         "abstain": round(1 - len(acc) / len(trials), 3),
                         "decision_s": round(float(np.mean(dec)), 3) if dec else None})
            print(f"n={n} inj {lvl:>4} uV  loss {loss:5.2f} dB  acc {rows[-1]['accuracy']}  "
                  f"abstain {rows[-1]['abstain']}  t {rows[-1]['decision_s']}", flush=True)
        table["by_n"][str(n)] = {"freqs": freqs, "max_window_s": st.max_window_s,
                                 "calibration": {"supported": cal.get("supported"),
                                                 "estimate": cal.get("dynamic_stopping_estimate")},
                                 "rows": rows}
    table["runtime_s"] = round(time.time() - t0, 1)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(table, indent=1), encoding="utf-8")
    print("wrote", a.out, "in", table["runtime_s"], "s")


if __name__ == "__main__":
    main()
