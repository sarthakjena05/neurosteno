"""Signal-quality-driven choice count (5 / 3 / 2) through the EXISTING adaptive policy.

Pipeline (live):
    EEG (raw)  --controlled noise injection-->  processed EEG
    measured: band noise (3-40 Hz, decoding channels) of processed AND raw over the last 2 s
    snr_loss_db = 20 log10(processed / raw)          (0 dB exactly when the injected level is 0)
    -> predicted per-n reliability (accuracy, abstention, decision time) from a reference table
       (scripts/build_noise_reliability.py: real FBCCA + dynamic stopping on simulated SSVEP under the
       same injector, keyed by the same measured SNR loss)
    -> adaptive.choose_n (expected correctly-confirmed messages per minute, hysteresis)
    -> switch n after the choice has been stable for DWELL_S (no flapping while a slider moves)

Nothing is forced: the operator only changes the injected noise; the quality metrics are measured
on the processed stream, and the target count comes from the same model as outcome-based
adaptation. Honest limits (also in the snapshot): the reliability table is a SIMULATED reference
(the wearer's own SSVEP response at 0 dB is assumed comparable), and the metric measures noise
ADDED to this stream, not the absolute quality of the wearer's raw EEG (which the electrode and
channel checks cover separately).
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np

from .adaptive import Rel, Timing, chance, choose_n

TABLE_PATH = Path(__file__).resolve().parent / "data" / "noise_reliability.json"
DWELL_S = 3.0           # the new choice must persist this long before switching
MIN_INTERVAL_S = 5.0    # between switches
EMA_S = 1.5             # smoothing of the measured SNR loss
SAFE_PHASES = ("IDLE", "EVIDENCE_COLLECTION", "ABSTAINED", "REJECTED", "COMM_UNAVAILABLE")

_TABLE: dict | None = None


def load_table() -> dict | None:
    global _TABLE
    if _TABLE is None and TABLE_PATH.exists():
        _TABLE = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
    return _TABLE


def _monotone(xs: list[float], decreasing: bool) -> list[float]:
    """Running min (or max): more added noise never predicts a BETTER decoder (small-sample noise)."""
    out, cur = [], None
    for x in xs:
        cur = x if cur is None else (min(cur, x) if decreasing else max(cur, x))
        out.append(cur)
    return out


def rel_at(loss_db: float, n: int, table: dict) -> Rel:
    t = table["by_n"][str(n)]
    rows = sorted(t["rows"], key=lambda r: r["snr_loss_db"])
    x = [r["snr_loss_db"] for r in rows]
    acc = _monotone([r["accuracy"] if r["accuracy"] is not None else chance(n) for r in rows], True)
    acc = [max(a, chance(n)) for a in acc]
    ab = _monotone([r["abstain"] for r in rows], False)
    dec = [r["decision_s"] if r["decision_s"] is not None else t["max_window_s"] for r in rows]
    L = float(np.clip(loss_db, x[0], x[-1]))
    return Rel(n, float(np.interp(L, x, acc)), float(min(0.995, np.interp(L, x, ab))), float(np.interp(L, x, dec)),
               float(t["max_window_s"]), f"model: measured SNR loss {loss_db:.1f} dB -> simulated reference table",
               float(rows[0]["trials"]))


class QualityAdaptMixin:
    def _init_quality_adapt(self) -> None:
        self.qa = {"loss_ema": None, "candidate": None, "since": None, "last_switch": None, "decision": None,
                   "switches": []}

    def _noise_metrics(self, now: float) -> None:
        """Measured on the processed AND raw stream (decoding channels, last quality window)."""
        from .acquisition.noise_injection import band_noise_uv
        src = self.source
        fs, total = src.sample_rate, src.total_samples
        n = int(self.qcfg.window_s * fs)
        cols = self._eeg_cols(self.cfg.decoding_labels)
        if len(cols) < 2 or total < n:
            return
        proc = src.read_range(total - n, total)[:, cols].astype(np.float64)
        raw = src.read_range_raw(total - n, total)[:, cols].astype(np.float64)
        p, r = band_noise_uv(proc, fs), band_noise_uv(raw, fs)
        if p is None or not r:
            return
        loss = 20 * math.log10(max(p, 1e-9) / max(r, 1e-9))
        prev = self.qa["loss_ema"]
        last_t = self.qa.get("loss_t")
        dt = 0.25 if last_t is None else min(5.0, max(0.0, now - last_t))  # real elapsed time between updates
        self.qa["loss_t"] = now
        alpha = 1 - math.exp(-dt / EMA_S)
        ema = loss if prev is None else prev + alpha * (loss - prev)
        self.qa["loss_ema"] = ema
        self.quality["noise"] = {
            "injected_uv": round(src.injected_noise_uv, 2),
            "processed_band_noise_uv": round(p, 2), "raw_band_noise_uv": round(r, 2),
            "snr_loss_db": round(loss, 2), "snr_loss_db_smoothed": round(ema, 2),
            "band_hz": [3.0, 40.0], "channels": list(self.cfg.decoding_labels),
            "stream": "processed = raw + injected noise" if src.injected_noise_uv > 0 else "processed = raw (no injection)",
        }

    def _quality_adapt(self, now: float) -> None:
        if self.source is None:
            return
        table = load_table()
        q = self.qa
        if table is None or q["loss_ema"] is None or self.plan is None:
            q["decision"] = {"available": False, "reason": "no reliability table / no measurement / no routing plan yet"}
            return
        allowed = [k for k in (2, 3, 5) if k in self.allowed_choices()]
        by_n = (self.plan.recommended_n or {}).get("by_n", {})
        exp_sel = {k: by_n[k]["expected_selections"] for k in allowed if k in by_n}
        cur = self.cfg.n_choices
        if cur not in exp_sel:
            q["decision"] = {"available": False, "reason": f"no expected path length for {cur} targets"}
            return
        rels = {k: rel_at(q["loss_ema"], k, table) for k in exp_sel}
        tm = Timing(self.cfg.rest_s, self.cfg.feedback_s, self.cfg.sent_display_s, self.cfg.confirm_attempts)
        dec = choose_n(rels, rels.get(2) or rel_at(q["loss_ema"], 2, table), exp_sel, tm, sorted(exp_sel),
                       step_confirm=True, current=cur, hysteresis=self.cfg.adapt_hysteresis)
        want = dec["n"]
        if want != cur:
            if q["candidate"] != want:
                q["candidate"], q["since"] = want, now
        else:
            q["candidate"], q["since"] = None, None
        locked = [k for k in (2, 3, 5) if k not in allowed]
        q["decision"] = {
            "available": True, "objective": dec["objective"], "chosen": want, "current": cur, "reason": dec["reason"],
            "snr_loss_db": round(q["loss_ema"], 2),
            "by_n": {k: {"cmpm": v["cmpm"], "accuracy": round(rels[k].accuracy, 3), "abstain": round(rels[k].abstain, 3),
                         "decision_s": round(rels[k].decision_s, 2)} for k, v in dec["by_n"].items()},
            "pending": None if q["candidate"] is None else {
                "to": q["candidate"], "stable_s": round(now - q["since"], 1), "dwell_s": DWELL_S},
            "locked_targets": locked,
            "locked_reason": ("3/5 targets need an SSVEP calibration on this real data source (safety rule)"
                              if locked else None),
            "model_note": ("Predicted reliability from the MEASURED SNR loss, via a SIMULATED reference table "
                           "(real FBCCA on synthetic SSVEP). Not a measurement of the wearer's selection accuracy."),
        }
        if not self.cfg.auto_adapt or q["candidate"] is None:
            return
        if now - q["since"] < DWELL_S or (q["last_switch"] is not None and now - q["last_switch"] < MIN_INTERVAL_S):
            return
        if self.phase.value not in SAFE_PHASES or self.pending_leaf is not None or self.calib is not None:
            return
        frm, to = cur, q["candidate"]
        bn = dec["by_n"]
        why = (f"measured SNR loss {q['loss_ema']:.1f} dB: expected correctly-confirmed messages/min "
               f"{frm} targets {bn[frm]['cmpm']:.2f} -> {to} targets {bn[to]['cmpm']:.2f} (model)")
        self.cfg.n_choices = to
        self.outcomes.clear()
        self.adapt_since_switch = 0
        self.request_plan()
        if self.phase.value in ("EVIDENCE_COLLECTION",):
            self._cancel_attempt()
            self._enter(type(self.phase).IDLE)
        q["last_switch"], q["candidate"], q["since"] = now, None, None
        q["switches"].append({"from": frm, "to": to, "snr_loss_db": round(self.qa["loss_ema"], 2),
                              "injected_uv": round(self.source.injected_noise_uv, 2), "time": time.time()})
        q["switches"] = q["switches"][-20:]
        self.last_adaptation = {"from": frm, "to": to, "reason": why, "time": time.time(), "source": "signal_quality"}
        self._mark("adapt", source="signal_quality", frm=frm, to=to, snr_loss_db=round(self.qa["loss_ema"], 2))
        self.log("adapt", f"Choices per screen {frm} -> {to}: {why}")
