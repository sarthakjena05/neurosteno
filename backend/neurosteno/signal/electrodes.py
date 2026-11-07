"""Electrode contact diagnostics and physiological validity checks for real EEG.

UnicornPy does not expose electrode impedance, so contact is judged from the signal itself.
Each heuristic below targets a failure mode seen with dry/gel headsets:

  * no signal          - flat channel (electrode or lead disconnected)
  * reference off      - channel ~identical to all others (ear clips off / headset not worn;
                         seen on the real UN-2024.09.36 during bring-up)
  * unstable contact   - large DC drift or slow swings (electrode moving / drying)
  * poor contact       - large broadband amplitude or saturation
  * mains noise        - 50/60 Hz dominates (high impedance)
  * muscle (EMG)       - strong 30-45 Hz power (jaw/neck tension; warning only)
  * low amplitude      - implausibly small for scalp EEG (warning only)

``contact_score`` (0-1) is a HEURISTIC summary, not an impedance measurement.

The definitive check that real brain signal is being recorded is ``alpha_reactivity``:
occipital alpha (8-13 Hz) rises when the eyes close (Berger effect). It is independent
of SSVEP and of the stimulus display.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from scipy.signal import sosfiltfilt, welch

from .quality import _bandpower, _bp


@dataclass
class ElectrodeConfig:
    flat_std_uv: float = 0.3
    low_std_uv: float = 1.5          # 1-40 Hz std below this is unusual for scalp EEG
    artifact_std_uv: float = 60.0
    artifact_ptp_uv: float = 400.0
    drift_uv_per_s: float = 50.0     # raw linear trend
    slow_std_uv: float = 80.0        # <1 Hz wander
    ref_corr: float = 0.95           # mean correlation with other channels
    line_ratio_max: float = 4.0
    emg_ratio_max: float = 0.35      # 30-45 Hz / 1-45 Hz power
    line_hz: float = 60.0
    alpha_band: tuple = (8.0, 13.0)
    alpha_min_ratio: float = 1.5     # eyes-closed / eyes-open alpha power
    occipital_labels: tuple = ("Oz", "PO7", "PO8", "Pz")


BAD = ("no_signal", "reference_off", "poor_contact", "unstable_contact", "mains_noise")


def channel_contacts(window: np.ndarray, fs: float, labels: list[str],
                     cfg: ElectrodeConfig | None = None) -> list[dict]:
    """window: [n_samples, n_channels] raw µV (at least ~1 s). Returns one dict per channel:
    {label, verdict: good|suspect|bad|no_data, contact_score, issues[], metrics{...}}."""
    cfg = cfg or ElectrodeConfig()
    n, k = window.shape if window.ndim == 2 else (0, len(labels))
    if n < int(fs):
        return [{"label": lbl, "verdict": "no_data", "contact_score": None, "issues": [], "metrics": {}}
                for lbl in labels]
    x = window.astype(np.float64)
    t = np.arange(n) / fs
    slope = np.polyfit(t, x, 1)[0]                                  # µV/s per channel
    xd = x - x.mean(axis=0)
    xf = sosfiltfilt(_bp(fs, 1.0, 40.0), xd, axis=0)
    slow = xd - sosfiltfilt(_bp(fs, 1.0, min(45.0, fs / 2 - 1)), xd, axis=0)
    std = xf.std(axis=0)
    ptp = np.ptp(xf, axis=0)
    slow_std = slow.std(axis=0)
    if k >= 2 and np.all(std > 1e-6):
        c = np.abs(np.corrcoef(xf.T))  # identical or sign-flipped copies both mean "reference off"
        corr = (c.sum(axis=1) - 1.0) / (k - 1)
    else:
        corr = np.full(k, np.nan)
    f, p = welch(xd, fs=fs, nperseg=min(n, int(2 * fs)), axis=0)
    out = []
    for j, lbl in enumerate(labels):
        band = _bandpower(f, p[:, j], 1.0, 45.0) + 1e-12
        line = _bandpower(f, p[:, j], cfg.line_hz - 2, cfg.line_hz + 2) / (_bandpower(f, p[:, j], 1.0, 40.0) + 1e-12)
        emg = _bandpower(f, p[:, j], 30.0, 45.0) / band
        alpha = _bandpower(f, p[:, j], *cfg.alpha_band) / band
        issues: list[str] = []
        if std[j] < cfg.flat_std_uv:
            issues.append("no_signal")
        else:
            if np.isfinite(corr[j]) and corr[j] > cfg.ref_corr:
                issues.append("reference_off")
            if std[j] > cfg.artifact_std_uv or ptp[j] > cfg.artifact_ptp_uv:
                issues.append("poor_contact")
            if abs(slope[j]) > cfg.drift_uv_per_s or slow_std[j] > cfg.slow_std_uv:
                issues.append("unstable_contact")
            if line > cfg.line_ratio_max:
                issues.append("mains_noise")
            if emg > cfg.emg_ratio_max:
                issues.append("muscle")
            if std[j] < cfg.low_std_uv:
                issues.append("low_amplitude")
        bad = [i for i in issues if i in BAD]
        verdict = "bad" if bad else ("suspect" if issues else "good")
        score = 1.0
        if std[j] >= cfg.flat_std_uv:
            score -= 0.5 * min(1.0, max(0.0, (std[j] - 25.0) / (cfg.artifact_std_uv - 25.0)))
            score -= 0.4 * min(1.0, max(0.0, (corr[j] - 0.7) / 0.25)) if np.isfinite(corr[j]) else 0
            score -= 0.3 * min(1.0, abs(slope[j]) / cfg.drift_uv_per_s)
            score -= 0.3 * min(1.0, line / cfg.line_ratio_max)
            score -= 0.2 * min(1.0, emg / cfg.emg_ratio_max)
        else:
            score = 0.0
        if bad:
            score = min(score, 0.3)
        out.append({
            "label": lbl, "verdict": verdict, "contact_score": round(float(max(0.0, score)), 2),
            "issues": issues, "advice": [ADVICE[i] for i in issues],
            "metrics": {
                "std_uv": round(float(std[j]), 2), "ptp_uv": round(float(ptp[j]), 1),
                "drift_uv_per_s": round(float(slope[j]), 2), "slow_std_uv": round(float(slow_std[j]), 1),
                "mean_corr": None if not np.isfinite(corr[j]) else round(float(corr[j]), 3),
                "line_ratio": round(float(line), 3), "emg_ratio": round(float(emg), 3),
                "alpha_rel": round(float(alpha), 3), "dc_uv": round(float(np.median(x[:, j])), 1),
            },
        })
    return out


ADVICE = {
    "no_signal": "No signal: check the electrode cable is seated.",
    "reference_off": "Same signal as every other channel: attach both ear clips to the earlobes/mastoids and seat the headset.",
    "poor_contact": "Large artifacts: press the electrode to the scalp, add gel, ask the wearer to relax.",
    "unstable_contact": "Drifting: electrode is moving or settling. Wait 1-2 min or reseat it.",
    "mains_noise": "Mains noise: improve contact; move away from power adapters/chargers.",
    "muscle": "Muscle activity: ask the wearer to relax jaw, face and neck.",
    "low_amplitude": "Unusually small signal: confirm the electrode touches skin.",
}


def summarize_contacts(contacts: list[dict], decoding_labels: list[str]) -> dict:
    dec = {lbl.lower() for lbl in decoding_labels}
    counts = {v: sum(1 for c in contacts if c["verdict"] == v) for v in ("good", "suspect", "bad", "no_data")}
    dec_ok = [c["label"] for c in contacts if c["label"].lower() in dec and c["verdict"] in ("good", "suspect")]
    ref_off = sum(1 for c in contacts if "reference_off" in c["issues"]) >= max(2, len(contacts) // 2)
    if counts["no_data"] == len(contacts):
        headline = "Waiting for EEG data"
    elif ref_off:
        headline = "Reference/ground not on skin: attach both ear clips and put the headset on"
    elif len(dec_ok) < min(3, len(dec)):
        headline = f"Fix occipital/parietal electrodes ({len(dec_ok)}/{len(dec)} usable)"
    elif counts["bad"]:
        headline = f"{counts['bad']} electrode(s) need attention; decoding channels usable"
    else:
        headline = "Electrode contact looks plausible (heuristic, not impedance)"
    return {"counts": counts, "decoding_usable": dec_ok, "reference_off": ref_off, "headline": headline}


def channel_spectra(window: np.ndarray, fs: float, labels: list[str], fmax: float = 45.0) -> dict:
    """Log power spectra for a signal-check display: 0.5 Hz bins, 1..fmax Hz, dB re 1 µV²/Hz."""
    if window.shape[0] < int(2 * fs):
        return {"freqs": [], "channels": {}}
    xd = window.astype(np.float64) - window.mean(axis=0)
    f, p = welch(xd, fs=fs, nperseg=int(2 * fs), axis=0)
    m = (f >= 1.0) & (f <= fmax)
    db = 10 * np.log10(p[m] + 1e-12)
    return {"freqs": [round(float(v), 2) for v in f[m]],
            "channels": {lbl: [round(float(v), 1) for v in db[:, j]] for j, lbl in enumerate(labels)}}


def alpha_power(x: np.ndarray, fs: float, band=(8.0, 13.0)) -> np.ndarray:
    """Absolute alpha band power (µV²) per channel of an [n, k] segment."""
    xd = x.astype(np.float64) - x.mean(axis=0)
    f, p = welch(xd, fs=fs, nperseg=min(xd.shape[0], int(2 * fs)), axis=0)
    m = (f >= band[0]) & (f <= band[1])
    return np.trapezoid(p[m], f[m], axis=0)


def _epochs(seg: np.ndarray, fs: float, trim_s: float, epoch_s: float) -> list[np.ndarray]:
    x = seg[int(trim_s * fs):]
    n = int(epoch_s * fs)
    return [x[i:i + n] for i in range(0, x.shape[0] - n + 1, n)]


def _auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """P(pos > neg) over all pairs (Mann-Whitney AUC)."""
    if pos.size == 0 or neg.size == 0:
        return float("nan")
    d = pos[:, None] - neg[None, :]
    return float(((d > 0).sum() + 0.5 * (d == 0).sum()) / d.size)


def alpha_reactivity(open_segs: list[np.ndarray], closed_segs: list[np.ndarray], fs: float,
                     labels: list[str], cfg: ElectrodeConfig | None = None,
                     trim_s: float = 1.5, epoch_s: float = 2.0) -> dict:
    """Berger-effect test. open_segs / closed_segs: alternating segments [n, k] of raw µV, in
    the order recorded (cycle i = open_segs[i], closed_segs[i]).

    A channel is *reactive* only if all of:
      * mean closed/open alpha power ratio >= cfg.alpha_min_ratio
      * epoch-level AUC (closed epochs > open epochs) >= 0.8, so it is not one lucky segment
      * alpha rose (>= 1.2x) in every cycle
      * no gross artifacts on that channel
    The first trim_s of every segment is discarded (eye movement / settling after the cue).
    Identical channels (reference off) invalidate the whole test.
    """
    cfg = cfg or ElectrodeConfig()
    if not open_segs or not closed_segs:
        raise ValueError("need eyes-open and eyes-closed segments")
    k = len(labels)
    ep_o = [e for s in open_segs for e in _epochs(s, fs, trim_s, epoch_s)]
    ep_c = [e for s in closed_segs for e in _epochs(s, fs, trim_s, epoch_s)]
    if len(ep_o) < 2 or len(ep_c) < 2:
        raise ValueError("segments too short for the alpha test")
    po_ep = np.array([alpha_power(e, fs, cfg.alpha_band) for e in ep_o])   # [epochs, k]
    pc_ep = np.array([alpha_power(e, fs, cfg.alpha_band) for e in ep_c])
    po, pc = po_ep.mean(axis=0), pc_ep.mean(axis=0)
    ratio = pc / (po + 1e-12)
    auc = [_auc(np.log(pc_ep[:, j] + 1e-12), np.log(po_ep[:, j] + 1e-12)) for j in range(k)]
    seg_o = np.array([alpha_power(s[int(trim_s * fs):], fs, cfg.alpha_band) for s in open_segs])
    seg_c = np.array([alpha_power(s[int(trim_s * fs):], fs, cfg.alpha_band) for s in closed_segs])
    cycles = min(len(seg_o), len(seg_c))
    cyc_up = (seg_c[:cycles] / (seg_o[:cycles] + 1e-12) >= 1.2).sum(axis=0)

    trimmed = [s[int(trim_s * fs):].astype(np.float64) for s in open_segs + closed_segs]
    filt = [sosfiltfilt(_bp(fs, 1.0, 40.0), s - s.mean(axis=0), axis=0) for s in trimmed]
    allx = np.concatenate(filt)
    std = allx.std(axis=0)
    ptp = np.max([np.ptp(f, axis=0) for f in filt], axis=0)
    artifact = (std > cfg.artifact_std_uv) | (ptp > cfg.artifact_ptp_uv) | (std < cfg.flat_std_uv)
    if k > 1 and np.all(std > 1e-6):
        c = np.abs(np.corrcoef(allx.T))
        cm = float((c.sum() - k) / (k * k - k))
    else:
        cm = None

    closed = np.concatenate([s[int(trim_s * fs):] for s in closed_segs]).astype(np.float64)
    closed -= closed.mean(axis=0)
    f, p = welch(closed, fs=fs, nperseg=min(closed.shape[0], int(4 * fs)), axis=0)
    m = (f >= 7.0) & (f <= 14.0)
    peak = f[m][np.argmax(p[m], axis=0)]

    occ = {lbl.lower() for lbl in cfg.occipital_labels}
    per = []
    for j, lbl in enumerate(labels):
        reactive = bool(ratio[j] >= cfg.alpha_min_ratio and auc[j] >= 0.8 and cyc_up[j] == cycles
                        and not artifact[j])
        per.append({"label": lbl, "ratio": round(float(ratio[j]), 2), "auc": round(float(auc[j]), 2),
                    "cycles_up": int(cyc_up[j]), "cycles": int(cycles), "peak_hz": round(float(peak[j]), 2),
                    "open_uv2": round(float(po[j]), 2), "closed_uv2": round(float(pc[j]), 2),
                    "artifact": bool(artifact[j]), "occipital": lbl.lower() in occ, "reactive": reactive})
    occ_rows = [r for r in per if r["occipital"] and not r["artifact"]]
    reactive = [r["label"] for r in occ_rows if r["reactive"]]
    best = max(occ_rows, key=lambda r: (r["reactive"], r["ratio"])) if occ_rows else None
    if cm is not None and cm > cfg.ref_corr:
        verdict, note = "invalid", (f"All channels carry the same signal (|corr| {cm:.2f}): reference/ground "
                                    "not on skin. Not evidence of EEG.")
    elif not occ_rows:
        verdict, note = "invalid", "Every occipital/parietal channel was flat or full of artifacts."
    elif reactive:
        verdict, note = "eeg_confirmed", (
            f"Alpha rose {best['ratio']}x with eyes closed at {best['label']} (epoch AUC {best['auc']}, "
            f"{best['cycles_up']}/{cycles} cycles, peak {best['peak_hz']} Hz). Physiological EEG is being recorded.")
    elif best and best["ratio"] >= 1.2 and best["auc"] >= 0.65:
        verdict, note = "weak", (f"Small or inconsistent alpha increase ({best['ratio']}x, AUC {best['auc']} at "
                                 f"{best['label']}). Improve occipital contact and repeat; some people have little alpha.")
    else:
        verdict, note = "not_detected", ("No reliable alpha increase with eyes closed. Check occipital electrodes "
                                         "and ear clips. Some people have weak alpha, so this alone is not proof of failure.")
    if cycles < 2 and verdict == "eeg_confirmed":
        note += " Only one cycle was recorded; repeat for stronger evidence."
    return {"verdict": verdict, "note": note, "channels": per, "reactive_occipital": reactive,
            "common_mode": None if cm is None else round(cm, 3), "epochs": {"open": len(ep_o), "closed": len(ep_c)},
            "config": {"alpha_band": list(cfg.alpha_band), "min_ratio": cfg.alpha_min_ratio, "min_auc": 0.8,
                       "trim_s": trim_s, "epoch_s": epoch_s}}


def config_dict(cfg: ElectrodeConfig) -> dict:
    return asdict(cfg)
