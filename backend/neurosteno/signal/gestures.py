"""OPTIONAL head-gesture input from the Unicorn's accelerometer + gyroscope.

Gestures: NOD (YES), SHAKE (NO), sustained TILT (go back; motion-assisted mode only).

Safety model
  * Disabled by default. A gesture is only ever acted on when (a) the operator explicitly enabled
    gesture input, (b) a gesture calibration on the CURRENT data origin marked that gesture usable,
    and (c) the IMU stream is healthy. Otherwise the system is EEG-only.
  * A gesture can never send a message, never confirm the final review, and never trigger an urgent
    message or an emergency. It can only answer a per-step YES/NO, or (tilt) navigate back.
  * Nothing here is a validated patient setting. All thresholds come from this patient's own
    calibration (neutral rest, cued gestures, natural-movement block); the few fixed numbers below
    are design parameters of the detector (filtering, segmentation floor, refractory period), are
    labelled as such, and are reported with every result.

Pipeline (same code for calibration, online use and the benchmark):
  1. 250 Hz -> 50 Hz block average; gyro bias (neutral calibration) removed; zero-phase 4th-order
     3 Hz low-pass (keeps nods/shakes at ~0.5-2 Hz, suppresses 4-8 Hz tremor).
  2. Movement segments: angular speed above max(design floor, 3 x neutral p99), gaps < 0.25 s merged.
  3. Temporal pattern per segment, projected on the patient's OWN calibrated axes (axes are learned
     by PCA of the cued movements; the sensor's mounting is never assumed):
       nod/shake: >= 2 alternating lobes (there-and-back), amplitude >= calibrated threshold,
                  axis specificity >= calibrated minimum, duration within the calibrated range.
       tilt:      gravity angle about the tilt axis beyond the calibrated angle, held still for
                  the calibrated hold time; re-armed only after returning near neutral.
  4. Artifact rejection: linear acceleration (body moved / handling), implausibly fast movement,
     multi-axis movement (low specificity), segments matching BOTH nod and shake (ambiguous).
  5. Debounce: a refractory period after every accepted gesture and a guard interval so that a
     movement is only classified once it has finished.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from functools import lru_cache

import numpy as np
from scipy.signal import butter, sosfiltfilt
from scipy.stats import chi2

KINDS = ("nod", "shake", "tilt")
OSC = ("nod", "shake")

# Design parameters (detector construction, not patient settings).
FS_D = 50.0
LP_HZ = 3.0
ACT_FLOOR_DPS = 5.0
MERGE_GAP_S = 0.25
PAD_S = 0.08
GUARD_S = 0.3
REFRACTORY_S = 1.0
REARM_S = 0.3
MIN_SENSITIVITY = 0.75      # LOO sensitivity a gesture needs before it can be marked usable
MIN_CUES = 4


@dataclass
class GestureConfig:
    enabled: bool = False                  # explicit opt-in (operator); never on by default
    calibrated: bool = False
    origin: str | None = None              # data origin the calibration was made on
    bias: list | None = None               # gyro bias (deg/s, device frame)
    g0: list | None = None                 # neutral gravity unit vector (device frame)
    act_dps: float = ACT_FLOOR_DPS         # segmentation threshold
    axes: dict = field(default_factory=dict)       # kind -> unit vector (device frame)
    tilt_sign: float = 1.0
    thresholds: dict = field(default_factory=dict)  # kind -> dict
    usable: dict = field(default_factory=lambda: {k: False for k in KINDS})
    refractory_s: float = REFRACTORY_S
    guard_s: float = GUARD_S

    def to_dict(self) -> dict:
        d = asdict(self)
        d["design_parameters"] = {"downsample_hz": FS_D, "lowpass_hz": LP_HZ, "activity_floor_dps": ACT_FLOOR_DPS,
                                  "merge_gap_s": MERGE_GAP_S, "guard_s": GUARD_S, "refractory_s": REFRACTORY_S,
                                  "rearm_s": REARM_S, "min_loo_sensitivity": MIN_SENSITIVITY,
                                  "min_cues": MIN_CUES}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "GestureConfig":
        keys = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in keys})

    def active(self, kind: str) -> bool:
        return bool(self.enabled and self.calibrated and self.usable.get(kind))


# --------------------------------------------------------------------------------------
# signal helpers
# --------------------------------------------------------------------------------------
@lru_cache(maxsize=4)
def _lp(fs: float):
    return butter(4, LP_HZ, btype="low", fs=fs, output="sos")


def imu_health(accel: np.ndarray, gyro: np.ndarray, fs: float) -> dict:
    """Is the motion stream usable? Missing, NaN, frozen or implausible data -> not usable."""
    if accel is None or gyro is None or len(accel) < int(0.5 * fs):
        return {"ok": False, "reason": "no motion data"}
    if not (np.isfinite(accel).all() and np.isfinite(gyro).all()):
        return {"ok": False, "reason": "motion channels contain invalid (NaN) samples"}
    if float(np.max(np.std(gyro, axis=0))) < 1e-3 and float(np.max(np.std(accel, axis=0))) < 1e-5:
        return {"ok": False, "reason": "motion channels are frozen (no sensor noise at all)"}
    norm = float(np.median(np.linalg.norm(accel, axis=1)))
    if not 0.7 <= norm <= 1.3:
        return {"ok": False, "reason": f"accelerometer magnitude {norm:.2f} g is not ~1 g (units/sensor problem)"}
    return {"ok": True, "reason": "", "accel_norm_g": round(norm, 3)}


def preprocess(accel: np.ndarray, gyro: np.ndarray, fs: float, bias=None) -> dict:
    k = max(1, int(round(fs / FS_D)))
    m = (len(gyro) // k) * k
    a = accel[:m].reshape(-1, k, 3).mean(axis=1)
    g = gyro[:m].reshape(-1, k, 3).mean(axis=1)
    if bias is not None:
        g = g - np.asarray(bias)
    if len(g) > 30:
        g = sosfiltfilt(_lp(FS_D), g, axis=0)
        a = sosfiltfilt(_lp(FS_D), a, axis=0)
    an = np.linalg.norm(a, axis=1)
    return {"w": g, "speed": np.linalg.norm(g, axis=1), "gu": a / np.maximum(an[:, None], 1e-9), "an": an,
            "k": k, "fs": FS_D}


def segments(speed: np.ndarray, thr: float) -> list[tuple[int, int]]:
    act = speed > thr
    if not act.any():
        return []
    edges = np.diff(np.concatenate([[0], act.astype(int), [0]]))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    segs: list[list[int]] = []
    gap = int(MERGE_GAP_S * FS_D)
    for s, e in zip(starts, ends):
        if segs and s - segs[-1][1] <= gap:
            segs[-1][1] = e
        else:
            segs.append([s, e])
    pad = int(PAD_S * FS_D)
    n = len(speed)
    # flatnonzero returns NumPy integers. These indices flow into event sample
    # fields and must be native integers for both REST and WebSocket JSON.
    return [(int(max(0, s - pad)), int(min(n, e + pad))) for s, e in segs if e - s >= 2]


def _lobes(p: np.ndarray) -> int:
    amp = float(np.max(np.abs(p))) if len(p) else 0.0
    if amp <= 0:
        return 0
    sig = np.sign(p[np.abs(p) >= 0.3 * amp])
    return int(1 + np.count_nonzero(np.diff(sig))) if len(sig) else 0


def seg_features(pre: dict, s: int, e: int, axes: dict) -> dict:
    w, speed, an = pre["w"][s:e], pre["speed"][s:e], pre["an"][s:e]
    tot = float((w ** 2).sum()) + 1e-9
    f = {"start": s, "end": e, "dur_s": (e - s) / FS_D, "peak_dps": float(speed.max()),
         "lin_g": float(np.max(np.abs(an - 1.0))) if len(an) else 0.0}
    for kind in OSC:
        u = axes.get(kind)
        if u is None:
            continue
        p = w @ np.asarray(u)
        f[kind] = {"amp": float(np.max(np.abs(p))), "spec": float((p ** 2).sum() / tot), "lobes": _lobes(p)}
    return f


def classify(f: dict, cfg: GestureConfig, kinds=OSC, require_usable: bool = True) -> tuple[str | None, str]:
    cands, why = [], []
    for kind in kinds:
        th = cfg.thresholds.get(kind)
        if th is None or kind not in f or (require_usable and not cfg.usable.get(kind)):
            continue
        x = f[kind]
        if f["lin_g"] > th["lin_max_g"]:
            why.append("linear acceleration (body moved or handled)")
        elif f["peak_dps"] > th["max_speed_dps"]:
            why.append("faster than any calibrated gesture (artifact)")
        elif x["lobes"] < 2:
            why.append(f"no there-and-back pattern for {kind}")
        elif x["spec"] < th["spec_min"]:
            why.append(f"multi-axis movement (not a clean {kind})")
        elif not th["dur_min_s"] <= f["dur_s"] <= th["dur_max_s"]:
            why.append(f"duration outside calibrated {kind} range")
        elif x["amp"] < th["amp_dps"]:
            why.append(f"below calibrated {kind} amplitude")
        else:
            cands.append(kind)
    if len(cands) == 2:
        return None, "ambiguous: matches both nod and shake (conflict, ignored)"
    if len(cands) == 1:
        return cands[0], "gesture"
    return None, (why[0] if why else "movement (no gesture calibrated)")


def tilt_angle(pre: dict, cfg: GestureConfig) -> np.ndarray | None:
    u, g0 = cfg.axes.get("tilt"), cfg.g0
    if u is None or g0 is None:
        return None
    g0 = np.asarray(g0)
    gu = pre["gu"]
    cr = np.cross(np.broadcast_to(g0, gu.shape), gu) @ np.asarray(u)
    return np.degrees(np.arctan2(cr, gu @ g0)) * cfg.tilt_sign


def tilt_holds(alpha: np.ndarray, speed: np.ndarray, an: np.ndarray, th: dict) -> list[tuple[int, int]]:
    """(start, index where the hold criterion is first met) for every sustained tilt."""
    ok = (alpha >= th["angle_deg"]) & (speed <= th["stable_dps"]) & (np.abs(an - 1) <= th["lin_max_g"])
    need = max(1, int(th["hold_s"] * FS_D))
    out, run = [], 0
    for i, v in enumerate(ok):
        run = run + 1 if v else 0
        if run == need:
            out.append((i - need + 1, i))
    return out


# --------------------------------------------------------------------------------------
# online detector
# --------------------------------------------------------------------------------------
class OnlineGestureDetector:
    """Incremental detector over the source's motion channels. update() returns new events;
    each event says whether it was ACCEPTED as a gesture (kind) or rejected (reason)."""

    LOOKBACK_S = 6.0

    def __init__(self, cfg: GestureConfig, fs: float):
        self.cfg = cfg
        self.fs = fs
        self.processed_until = 0
        self.last_accept: int | None = None
        self.done_ends: list[int] = []
        self.tilt_armed = True
        self._below_since: int | None = None

    def update(self, read_motion, total: int) -> list[dict]:
        cfg, fs = self.cfg, self.fs
        if cfg.bias is None:
            return []
        start = max(0, total - int(self.LOOKBACK_S * fs))
        m = read_motion(start, total)
        if m is None or len(m["gyro_dps"]) < int(1.0 * fs):
            return []
        if not (np.isfinite(m["gyro_dps"]).all() and np.isfinite(m["accel_g"]).all()):
            return []
        start = int(m["sample"][0])
        pre = preprocess(m["accel_g"], m["gyro_dps"], fs, cfg.bias)
        k = pre["k"]
        n = len(pre["speed"])
        decide = n - int(cfg.guard_s * FS_D)
        events = []
        for s, e in segments(pre["speed"], cfg.act_dps):
            if e >= decide:
                continue
            abs_end = start + e * k
            if any(abs(abs_end - d) < 0.2 * fs for d in self.done_ends):  # already classified
                continue
            self.done_ends = [d for d in self.done_ends if d > start] + [abs_end]
            f = seg_features(pre, s, e, cfg.axes)
            kind, reason = classify(f, cfg)
            ev = {"sample": abs_end, "t": round(abs_end / fs, 3), "kind": kind or "movement", "accepted": False,
                  "reason": reason, "duration_s": round(f["dur_s"], 2), "peak_dps": round(f["peak_dps"], 1)}
            if kind:
                if self.last_accept is not None and abs_end - self.last_accept < cfg.refractory_s * fs:
                    ev["reason"] = f"within {cfg.refractory_s:.1f} s refractory period"
                else:
                    ev.update(accepted=True, reason="gesture", amplitude_dps=round(f[kind]["amp"], 1))
                    self.last_accept = abs_end
            events.append(ev)
        th = cfg.thresholds.get("tilt")
        if th is not None and cfg.usable.get("tilt"):
            alpha = tilt_angle(pre, cfg)
            if alpha is not None:
                lo = max(0, int((self.processed_until - start) / k))
                for i in range(lo, decide):
                    a_i = alpha[i]
                    if not self.tilt_armed:
                        if a_i < th["angle_deg"] / 2:
                            self._below_since = self._below_since if self._below_since is not None else i
                            if i - self._below_since >= int(REARM_S * FS_D):
                                self.tilt_armed, self._below_since = True, None
                        else:
                            self._below_since = None
                for hs, hi in tilt_holds(alpha[:decide], pre["speed"][:decide], pre["an"][:decide], th):
                    abs_i = start + hi * k
                    if abs_i <= self.processed_until or not self.tilt_armed:
                        continue
                    ev = {"sample": abs_i, "t": round(abs_i / fs, 3), "kind": "tilt", "accepted": False,
                          "reason": "", "angle_deg": round(float(alpha[hi]), 1)}
                    if self.last_accept is not None and abs_i - self.last_accept < cfg.refractory_s * fs:
                        ev["reason"] = f"within {cfg.refractory_s:.1f} s refractory period"
                    else:
                        ev.update(accepted=True, reason="sustained tilt")
                        self.last_accept = abs_i
                        self.tilt_armed = False
                    events.append(ev)
        self.processed_until = start + decide * k
        return sorted(events, key=lambda e: e["sample"])


def detect_stream(accel: np.ndarray, gyro: np.ndarray, fs: float, cfg: GestureConfig, step_s: float = 0.25,
                  t0_sample: int = 0) -> list[dict]:
    """Run the ONLINE detector over a recording as if it were streaming (same code path)."""
    det = OnlineGestureDetector(cfg, fs)

    def read(a, b):
        a0, b0 = max(a - t0_sample, 0), max(b - t0_sample, 0)
        idx = np.arange(a0, min(b0, len(gyro))) + t0_sample
        return {"sample": idx, "accel_g": accel[a0:b0], "gyro_dps": gyro[a0:b0]}

    out = []
    step = int(step_s * fs)
    for total in range(t0_sample + step, t0_sample + len(gyro) + step, step):
        out += det.update(read, min(total, t0_sample + len(gyro)))
    # flush: let the last movement finish
    pad_a = np.vstack([accel, np.repeat(accel[-1:], int(1.0 * fs), axis=0)])
    pad_g = np.vstack([gyro, np.repeat(np.asarray(cfg.bias)[None] if cfg.bias is not None else gyro[-1:],
                                       int(1.0 * fs), axis=0)])
    accel, gyro = pad_a, pad_g
    out += det.update(read, t0_sample + len(gyro))
    return out


# --------------------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------------------
def _pca_axis(w: np.ndarray) -> tuple[np.ndarray | None, float]:
    if len(w) < 5:
        return None, 0.0
    c = w.T @ w
    vals, vecs = np.linalg.eigh(c)
    return vecs[:, -1], float(vals[-1] / max(vals.sum(), 1e-12))


def poisson_upper(k: int, exposure_h: float, conf: float = 0.95) -> float:
    if exposure_h <= 0:
        return math.inf
    return float(chi2.ppf(conf, 2 * (k + 1)) / 2 / exposure_h)


def _fit(cued_feats: dict, null_feats: list[dict], tilt_cued: list[dict], null_alpha_max: float, act: float) -> dict:
    """Thresholds from cued features (positives) and natural-movement features (negatives)."""
    th = {}
    for kind in OSC:
        pos = [f for f in cued_feats.get(kind, []) if f is not None and kind in f and f[kind]["lobes"] >= 2]
        if len(pos) < 3:
            th[kind] = None
            continue
        spec = np.array([f[kind]["spec"] for f in pos])
        dur = np.array([f["dur_s"] for f in pos])
        t = {"spec_min": float(max(0.5, 0.8 * np.percentile(spec, 10))),
             "dur_min_s": float(max(0.15, 0.6 * dur.min())), "dur_max_s": float(1.5 * dur.max() + 0.2),
             "max_speed_dps": float(2.0 * max(f["peak_dps"] for f in pos)),
             "lin_max_g": float(max(0.15, 1.5 * max(f["lin_g"] for f in pos)))}
        match = [f[kind]["amp"] for f in null_feats if kind in f and f[kind]["lobes"] >= 2
                 and f[kind]["spec"] >= t["spec_min"] and t["dur_min_s"] <= f["dur_s"] <= t["dur_max_s"]
                 and f["lin_g"] <= t["lin_max_g"]]
        t["amp_dps"] = float(max(1.5 * act, 1.15 * max(match, default=0.0)))
        t["null_matches"] = len(match)
        th[kind] = t
    holds = [h for h in tilt_cued if h is not None]
    if len(holds) >= 3:
        ang = np.array([h["angle"] for h in holds])
        dur = np.array([h["hold_s"] for h in holds])
        th["tilt"] = {"angle_deg": float(max(3.0, 1.15 * null_alpha_max, 0.4 * np.percentile(ang, 20))),
                      "hold_s": float(np.clip(0.5 * np.median(dur), 0.5, 1.5)),
                      "stable_dps": float(max(act, 1.5 * max(h["speed_p90"] for h in holds))),
                      "lin_max_g": 0.15}
    else:
        th["tilt"] = None
    return th


def _tilt_hold(pre: dict, cfg_like: GestureConfig, act: float) -> dict | None:
    alpha = tilt_angle(pre, cfg_like)
    if alpha is None:
        return None
    a = alpha
    peak = float(np.max(a))
    if peak < 2.0:
        return None
    held = (a >= 0.6 * peak) & (pre["speed"] <= max(act * 3, 15.0))
    if held.sum() < 3:
        return None
    return {"angle": float(np.median(a[held])), "hold_s": float(held.sum() / FS_D),
            "speed_p90": float(np.percentile(pre["speed"][held], 90))}


def calibrate_gestures(neutral: dict, cued: list[dict], natural: list[dict], fs: float,
                       origin: str = "unknown") -> dict:
    """neutral: {"accel_g", "gyro_dps"} with the head still (>= 10 s).
    cued: [{"kind", "accel_g", "gyro_dps", "cue_s"}] one window per cue (cue_s = cue time in the window).
    natural: [{"accel_g", "gyro_dps"}] natural movement without gestures (negatives).
    Returns the calibration result incl. a GestureConfig (usable flags) and honest estimates:
    leave-one-out sensitivity, confusion, latency, and HELD-OUT false activations per hour."""
    res: dict = {"origin": origin, "n_cues": {k: sum(1 for c in cued if c["kind"] == k) for k in KINDS}}
    h = imu_health(neutral["accel_g"], neutral["gyro_dps"], fs)
    cfg = GestureConfig(origin=origin)
    if not h["ok"]:
        res.update(ok=False, note=f"Motion data unusable: {h['reason']}. Gestures stay disabled.",
                   config=cfg.to_dict())
        return res
    bias = np.median(neutral["gyro_dps"], axis=0)
    g0 = np.mean(neutral["accel_g"], axis=0)
    g0 = g0 / np.linalg.norm(g0)
    pre_n = preprocess(neutral["accel_g"], neutral["gyro_dps"], fs, bias)
    # Robust noise floor: involuntary movements during the 'still' period must not inflate it.
    sp = pre_n["speed"]
    med = float(np.median(sp))
    noise = med + 5 * float(np.median(np.abs(sp - med))) * 1.4826
    act = float(max(ACT_FLOOR_DPS, 3 * noise))
    cfg.bias, cfg.g0, cfg.act_dps = bias.tolist(), g0.tolist(), act
    res["neutral"] = {"gyro_bias_dps": [round(float(b), 3) for b in bias], "speed_noise_dps": round(noise, 2),
                      "speed_median_dps": round(med, 2),
                      "activity_threshold_dps": round(act, 2), "duration_s": round(len(neutral["gyro_dps"]) / fs, 1)}
    if med > 10.0:
        res.update(ok=False, note="Head was not still during the neutral period; gestures stay disabled.",
                   config=cfg.to_dict())
        return res
    pres = [preprocess(c["accel_g"], c["gyro_dps"], fs, bias) for c in cued]
    # ---- axes from the cued movements -------------------------------------------------
    axes, axis_quality = {}, {}
    for kind in KINDS:
        ws = [p["w"][p["speed"] > act] for p, c in zip(pres, cued) if c["kind"] == kind]
        ws = [w for w in ws if len(w)]
        u, ev = _pca_axis(np.vstack(ws)) if ws else (None, 0.0)
        axis_quality[kind] = round(ev, 3)
        if u is not None and ev >= 0.6:
            first = [w[np.argmax(np.abs(w @ u))] @ u for w in ws]  # orient by the dominant first movement
            axes[kind] = (u * (1 if np.median(first) >= 0 else -1)).tolist()
    res["axes_explained_variance"] = axis_quality
    distinct = True
    if "nod" in axes and "shake" in axes and abs(float(np.dot(axes["nod"], axes["shake"]))) > 0.7:
        distinct = False
    cfg.axes = axes
    # tilt direction from gravity during the cued holds
    if "tilt" in axes:
        signs = []
        for p, c in zip(pres, cued):
            if c["kind"] == "tilt":
                a = tilt_angle(p, cfg)
                if a is not None:
                    signs.append(a[np.argmax(np.abs(a))])
        cfg.tilt_sign = 1.0 if (np.median(signs) if signs else 1.0) >= 0 else -1.0
    # ---- features ---------------------------------------------------------------------
    def best_seg(p, kind):
        best = None
        for s, e in segments(p["speed"], act):
            f = seg_features(p, s, e, axes)
            if kind in f and (best is None or f[kind]["amp"] > best[kind]["amp"]):
                best = f
        return best

    cued_f = {k: [] for k in KINDS}
    cued_idx = {k: [] for k in KINDS}
    for i, (p, c) in enumerate(zip(pres, cued)):
        if c["kind"] in OSC:
            cued_f[c["kind"]].append(best_seg(p, c["kind"]))
        else:
            cued_f["tilt"].append(_tilt_hold(p, cfg, act))
        cued_idx[c["kind"]].append(i)
    nat_pre = [pre_n] + [preprocess(x["accel_g"], x["gyro_dps"], fs, bias) for x in natural]
    null_f = [seg_features(p, s, e, axes) for p in nat_pre for s, e in segments(p["speed"], act)]
    null_alpha = 0.0
    if "tilt" in axes:
        for p in nat_pre:
            a = tilt_angle(p, cfg)
            run = int(0.5 * FS_D)
            if a is not None and len(a) > run:
                null_alpha = max(null_alpha, float(np.max(np.lib.stride_tricks.sliding_window_view(a, run).min(axis=1))))

    def fit_without(skip_kind=None, skip_j=None, null=None):
        cf = {k: [f for j, f in enumerate(v) if not (k == skip_kind and j == skip_j)] for k, v in cued_f.items()}
        return _fit(cf, null_f if null is None else null, cf["tilt"], null_alpha, act)

    def cfg_with(th):
        c = GestureConfig(**{**asdict(cfg), "thresholds": th, "usable": {k: th.get(k) is not None for k in KINDS},
                             "enabled": True, "calibrated": True})
        if not distinct:
            c.usable["nod"] = c.usable["shake"] = False
        return c

    # ---- leave-one-out sensitivity / confusion ---------------------------------------
    est = {}
    for kind in KINDS:
        hits = confused = 0
        lat = []
        for j, i in enumerate(cued_idx[kind]):
            th = fit_without(kind, j)
            c = cfg_with(th)
            if kind in OSC:
                f = cued_f[kind][j]
                # the held-out window may contain movements on other axes: classify every segment
                p = pres[i]
                got = []
                for s, e in segments(p["speed"], act):
                    kk, _ = classify(seg_features(p, s, e, axes), c, require_usable=False)
                    if kk:
                        got.append((kk, e))
                if any(kk == kind for kk, _ in got):
                    hits += 1
                    e = next(e for kk, e in got if kk == kind)
                    lat.append(e / FS_D + GUARD_S - cued[i].get("cue_s", 0.0))
                elif got:
                    confused += 1
            else:
                t = th.get("tilt")
                p = pres[i]
                a = tilt_angle(p, c)
                if t is not None and a is not None:
                    hl = tilt_holds(a, p["speed"], p["an"], t)
                    if hl:
                        hits += 1
                        lat.append(hl[0][1] / FS_D + GUARD_S - cued[i].get("cue_s", 0.0))
        n_c = len(cued_idx[kind])
        est[kind] = {"cues": n_c, "hits": hits, "confused": confused,
                     "sensitivity_loo": round(hits / n_c, 3) if n_c else None,
                     "latency_s": {"mean": round(float(np.mean(lat)), 2) if lat else None,
                                   "sd": round(float(np.std(lat)), 2) if len(lat) > 1 else None, "n": len(lat)}}
    # ---- held-out false activations on natural movement (2-fold by halves) ------------
    nat_all = [neutral] + list(natural)
    acc = np.vstack([x["accel_g"] for x in nat_all])
    gyr = np.vstack([x["gyro_dps"] for x in nat_all])
    half = len(gyr) // 2
    halves = [(acc[:half], gyr[:half]), (acc[half:], gyr[half:])]
    fa = {k: 0 for k in KINDS}
    for i_fit, i_test in ((0, 1), (1, 0)):
        pa = preprocess(*halves[i_fit], fs, bias)
        nf = [seg_features(pa, s, e, axes) for s, e in segments(pa["speed"], act)]
        th = _fit(cued_f, nf, cued_f["tilt"], null_alpha, act)
        c = cfg_with(th)
        for ev in detect_stream(*halves[i_test], fs, c):
            if ev["accepted"]:
                fa[ev["kind"]] += 1
    exposure_s = len(gyr) / fs
    # ---- final thresholds on everything ---------------------------------------------
    th = fit_without()
    cfg.thresholds = th
    cfg.calibrated = True
    for kind in KINDS:
        e = est[kind]
        e["false_activations"] = {"events": fa[kind], "exposure_s": round(exposure_s, 1),
                                  "per_hour": round(3600 * fa[kind] / exposure_s, 2),
                                  "upper95_per_hour": round(poisson_upper(fa[kind], exposure_s / 3600), 1),
                                  "method": "held-out (2-fold over the natural-movement recording)"}
        others = [i for k2 in KINDS if k2 != kind for i in cued_idx[k2]]
        e["confusion_from_other_gestures"] = None  # filled below with final thresholds
        ok = (th.get(kind) is not None and e["cues"] >= MIN_CUES and (e["sensitivity_loo"] or 0) >= MIN_SENSITIVITY)
        if kind in OSC and not distinct:
            ok = False
        cfg.usable[kind] = bool(ok)
    c_all = cfg_with(th)
    for kind in OSC:
        wrong = n_other = 0
        for k2 in KINDS:
            if k2 == kind:
                continue
            for i in cued_idx[k2]:
                n_other += 1
                p = pres[i]
                if any(classify(seg_features(p, s, e, axes), c_all, require_usable=False)[0] == kind
                       for s, e in segments(p["speed"], act)):
                    wrong += 1
        est[kind]["confusion_from_other_gestures"] = {"false": wrong, "of": n_other}
    cfg.enabled = False   # calibration never enables gesture input; the operator does
    res.update(ok=True, config=cfg.to_dict(), estimates=est, axes_distinct=distinct,
               usable={k: cfg.usable[k] for k in KINDS},
               hybrid_possible=bool(cfg.usable["nod"] and cfg.usable["shake"]),
               note=_note(cfg, est, distinct),
               caveat=("Estimates from one short calibration on this data origin: sensitivity is leave-one-out "
                       "over the cues, false activations are held-out over a short natural-movement block. "
                       "Not validated settings; real-world incidental movement may differ."))
    return res


def _note(cfg: GestureConfig, est: dict, distinct: bool) -> str:
    if not distinct:
        return "Nod and shake moved along the same axis; they cannot be told apart. Gesture input stays EEG-only."
    parts = []
    for k in KINDS:
        e = est[k]
        parts.append(f"{k}: {e['hits']}/{e['cues']} detected (LOO){' - usable' if cfg.usable[k] else ' - not usable'}")
    return "; ".join(parts) + ". Gesture input remains OFF until the operator enables it."
