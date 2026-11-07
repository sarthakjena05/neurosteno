"""Multimodal benchmark (SIMULATED): does adding head gestures (IMU) to SSVEP increase correctly
confirmed messages per minute, and for which patients?

Conditions (fixed BEFORE running; no post-hoc choice of the best condition):
  M1 fixed-menu EEG-only     static menu, fixed-window forced-choice SSVEP, no step confirmation;
                             n chosen from calibration by the conventional max-ITR rule
  M2 context EEG-only        M1 + context-aware risk-aware routing (same n rule)
  M3 adaptive EEG-only       full NeuroSteno EEG-only (= condition D of the A-D benchmark): dynamic
                             stopping, rest rejection, explicit YES/NO, live re-adaptation of n
  M4 hybrid                  M3, but every step is confirmed by nod (YES) / shake (NO) with EEG
                             YES/NO fallback after the gesture window. ALWAYS on, even when the
                             gesture calibration says gestures are unusable (the live engine would
                             refuse); this shows what assuming hybrid is better costs.
  M5 optimised multimodal    policy.optimise() picks mode (eeg_only | hybrid | motion_assisted), n,
                             routing and confirmation from the CALIBRATION capability profile
                             (conservative, falls back to EEG-only), then re-optimises from live
                             YES/NO and gesture evidence.
Final confirmation is a decoded EEG YES in every condition; a gesture can never send.

Synthetic patients: motion profile (no_head_control | accurate | unreliable | high_motion) x SSVEP
signal level (low 1.0 | medium 1.4 | high 2.0 uV) x replicate. Per replicate: new synthetic EEG
calibration + held-out test trials (decoder pools), new gesture calibration + held-out gesture
trials and natural movement (gesture pools; head-motion EEG artifacts for high_motion), new tasks.
Every condition runs the same tasks with the same seed.

Oracle: every fixed candidate policy (3 modes x 3 n x 2 routings x explicit/gated = 36) is run on
the same sessions; the oracle is the best one IN HINDSIGHT on the test sessions of that cell.
Regret = oracle CMPM - condition CMPM. A constrained oracle (measured error <= 2 %, idle false
sends <= 1/h) is reported as well.
"""

from __future__ import annotations

import json
import math
import pickle
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..adaptive import LATENCY_S, Timing
from ..capability import CapabilityProfile, eeg_from_calibration, gestures_from_calibration
from ..navigation import BACK_KEY
from ..phrases.catalog import build_catalog
from ..policy import Policy, Timing as PTiming, candidates, gesture_window, length_distribution, optimise
from ..signal.gestures import KINDS
from .gesture_pool import GesturePool, build_gesture_pool, protocol_seconds
from .pool import GATE_FACTOR, Pool, build_pool
from .run import CACHE_DIR, LEVELS, PROFILES, REPORTS_DIR, get_pools, make_tasks, routing_for
from .simulate import Condition, Metrics, Session, SessionSpec, calib_rel, fixed_window_for, wolpaw_bits
from .run import setup_condition

SCHEMA_VERSION = "1.0"
MOTION = ["no_head_control", "accurate", "unreliable", "high_motion"]
MM_LEVELS = ["low", "medium", "high"]
SESSION_MIN = 30.0
STEP_MAX_EVIDENCE = 8
# Calibration protocol durations (engine.CalibSession): (cued x n + 8 rest) x (cue 1.5 + stim 4 + break 1) s
EEG_CAL_S = {n: (5 * n + 8) * 6.5 for n in (2, 3, 5)}
GESTURE_CAL_S = protocol_seconds()


# --------------------------------------------------------------------------------------
# pools (cached)
# --------------------------------------------------------------------------------------
def motion_eeg_pools(rep: int, motion: str, tpt: int, trest: int) -> dict[str, dict[int, Pool]]:
    if motion != "high_motion":
        return get_pools(rep, tpt, trest)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / f"pools_motion_{motion}_r{rep}_t{tpt}_{trest}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    pools = {lvl: {n: build_pool(n, lvl, uv, seed=1000 + rep * 17 + int(uv * 10), test_per_target=tpt,
                                 test_rest=trest, motion_profile=motion) for n in (2, 3, 5)}
             for lvl, uv in LEVELS.items() if lvl in MM_LEVELS}
    f.write_bytes(pickle.dumps(pools))
    return pools


def gesture_pool(rep: int, motion: str, test_per_kind: int, natural_s: float) -> GesturePool:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / f"gestures_{motion}_r{rep}_t{test_per_kind}_{int(natural_s)}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    gp = build_gesture_pool(motion, seed=20_000 + rep * 101 + MOTION.index(motion) * 7,
                            test_per_kind=test_per_kind, natural_test_s=natural_s)
    f.write_bytes(pickle.dumps(gp))
    return gp


def _build(args):
    kind, rep, motion, cfg = args
    if kind == "eeg":
        motion_eeg_pools(rep, motion, cfg["test_per_target"], cfg["test_rest"])
    else:
        gesture_pool(rep, motion, cfg["gesture_test_per_kind"], cfg["gesture_natural_s"])
    return kind, rep, motion


def capability_from(pools_level: dict[int, Pool], gp: GesturePool) -> CapabilityProfile:
    """What the system knows after calibration (calibration sets only, never test trials)."""
    cap = CapabilityProfile("simulated")
    for n, pl in pools_level.items():
        cap.eeg[n] = eeg_from_calibration(n, pl.calibration_step, "simulated", pl.stopping_step.max_window_s,
                                          tuple(pl.calib_gate))
    p2 = pools_level[2]
    cap.confirm_eeg = eeg_from_calibration(2, p2.calibration, "simulated", p2.stopping.max_window_s)
    cap.gestures = gestures_from_calibration(gp.calibration)
    cap.imu = {"ok": bool(gp.calibration.get("ok")), "reason": gp.calibration.get("note", "")}
    cap.calibration_overhead_s = {"eeg_per_n": EEG_CAL_S, "gesture": GESTURE_CAL_S}
    return cap


def length_dists(profile: str):
    cat = build_catalog()
    r = routing_for(profile)
    ld, reach = {}, {}
    for n in (2, 3, 5):
        for routing, key in (("context", "adaptive"), ("static", "static")):
            sc, ur = r[key][n]["routes"]
            ld[(routing, n)], reach[(routing, n)] = length_distribution(cat, n, sc, ur, r["p"])
    return ld, reach


# --------------------------------------------------------------------------------------
# conditions
# --------------------------------------------------------------------------------------
@dataclass
class MMCondition(Condition):
    mode: str = "eeg_only"
    optimise: bool = False
    group: str = "primary"
    policy_key: str | None = None


def primary_conditions() -> list[MMCondition]:
    return [
        MMCondition("M1", "M1: fixed-menu EEG-only", "static", -1, "fixed", False),
        MMCondition("M2", "M2: context-aware EEG-only", "adaptive", -1, "fixed", False),
        MMCondition("M3", "M3: adaptive EEG-only (NeuroSteno)", "adaptive", None, "dynamic", True, live_adapt=True),
        MMCondition("M4", "M4: hybrid EEG selection + IMU confirmation (always on)", "adaptive", None, "dynamic", True,
                    live_adapt=True, mode="hybrid"),
        MMCondition("M5", "M5: optimised multimodal policy", "adaptive", None, "dynamic", True, live_adapt=True,
                    optimise=True),
    ]


def policy_condition(p: Policy) -> MMCondition:
    return MMCondition(f"P:{p.key}", p.key, "adaptive" if p.routing == "context" else "static", p.n, "dynamic", True,
                       live_adapt=False, confirm_gate=GATE_FACTOR if p.gated else None, mode=p.mode,
                       group="oracle_candidate", policy_key=p.key)


def all_policies() -> list[Policy]:
    return [Policy(m, n, r, g) for m in ("eeg_only", "hybrid", "motion_assisted") for n in (2, 3, 5)
            for r in ("context", "static") for g in (False, True)]


def itr_n(cal: dict[int, Pool], tm: Timing) -> int:
    """Conventional pre-registered choice of n for fixed-window baselines: max Wolpaw ITR on calibration."""
    best, best_n = -1.0, 2
    for n, pl in cal.items():
        w = fixed_window_for(pl, tm)
        n_cal = max(1, pl.calibration["n_cued_trials"])
        p = (pl.fixed_calib_acc[w] * n_cal + 1) / (n_cal + 2)
        rate = wolpaw_bits(n, p) * 60 / (tm.rest_s + LATENCY_S + w + tm.feedback_s)
        if rate > best:
            best, best_n = rate, n
    return best_n


# --------------------------------------------------------------------------------------
# session with gestures
# --------------------------------------------------------------------------------------
class MMSession(Session):
    def __init__(self, cond: MMCondition, *a, gpool: GesturePool | None = None, cap: CapabilityProfile | None = None,
                 W: float = 3.0, routes_by: dict | None = None, ldists=None, reach=None, opt0: dict | None = None,
                 **kw):
        super().__init__(cond, *a, **kw)
        self.mode = cond.mode
        self.gp = gpool
        self.cap = cap
        self.W = W
        self.routes_by = routes_by
        self.ldists, self.reach = ldists, reach
        self.policy_key = None
        self.live_g = {k: {"hits": 0, "prompts": 0, "unprompted": 0, "unprompted_exposure_s": 0.0} for k in KINDS}
        self.window_s = 0.0
        self.mm = {"gesture_decisions": 0, "gesture_fallbacks": 0, "false_gesture_actions": 0,
                   "false_gesture_events": 0, "tilt_backs": 0, "spurious_backs": 0, "policy_switches": [],
                   "conflicts": 0}
        if opt0 is not None:
            self._apply(opt0["chosen"], initial=True)

    # ---- policy application ---------------------------------------------------------
    def _apply(self, row: dict, initial: bool = False) -> None:
        self.mode = row["mode"]
        self.c = MMCondition(**{**self.c.__dict__, "confirm_gate": GATE_FACTOR if row["step_confirmation"] == "gated"
                                else None, "mode": row["mode"]})
        new_routes = self.routes_by["adaptive" if row["routing"] == "context" else "static"]
        if not initial:
            self.m.n_switches.append({"t": round(self.t, 1), "from": self.policy_key, "to": row["key"]})
            self.mm["policy_switches"].append({"t": round(self.t, 1), "from": self.policy_key, "to": row["key"]})
        self.policy_key = row["key"]
        if new_routes is not self.routes or row["n"] != self.n:
            self.routes = new_routes
            self._switch_n(row["n"])

    def _maybe_adapt(self) -> None:
        if not self.c.optimise:
            return super()._maybe_adapt()
        if self.since_switch < STEP_MAX_EVIDENCE:
            return
        self.since_switch = 0
        cap = CapabilityProfile("simulated", gestures={}, imu=self.cap.imu, confirm_eeg=self.cap.confirm_eeg)
        live = {n: self.tracker.counts(n) for n in (2, 3, 5)}
        cap.eeg = {n: _with_live(e, live.get(n)) for n, e in self.cap.eeg.items()}
        cap.gestures = gestures_from_calibration(self.gp.calibration, self.live_g) if self.gp else {}
        res = optimise(cap, self.ldists, self.reach, PTiming(), draws=100, seed=int(self.t),
                       current=self.policy_key, hysteresis=0.20)
        if res["chosen"] and res["chosen"]["key"] != self.policy_key:
            self._apply(res["chosen"])

    # ---- gestures -----------------------------------------------------------------
    def _rate(self, kind: str) -> float:
        return self.gp.rate_per_s(kind) if self.gp else 0.0

    def _confirm(self, want_yes, attempts):
        if attempts is None or self.mode == "eeg_only" or self.gp is None:
            return super()._confirm(want_yes, attempts)   # final review: ALWAYS EEG
        W = self.W
        events = []
        if want_yes is not None:
            kind = "nod" if want_yes else "shake"
            det, lat = self.gp.sample_cued(self.rng, kind)
            if det in ("nod", "shake") and lat is not None and lat <= W:
                events.append((max(0.3, lat), det, True))
            self.live_g["nod"]["prompts"] += 1
            self.live_g["shake"]["prompts"] += 1
        for k in ("nod", "shake"):
            lam = self._rate(k)
            if lam > 0:
                tf = float(self.rng.exponential(1 / lam))
                if tf <= W:
                    events.append((tf, k, False))
        if events:
            events.sort()
            t1, k1, genuine = events[0]
            self.t += t1
            self.window_s += t1
            self.mm["gesture_decisions"] += 1
            if want_yes is not None:
                self.live_g[k1]["hits"] += 1
            if not genuine:
                self.mm["false_gesture_actions"] += 1
                self.mm["false_gesture_events"] += 1
            return k1 == "nod"
        self.t += W
        self.window_s += W
        self.mm["gesture_fallbacks"] += 1
        return super()._confirm(want_yes, attempts)

    def _cycle(self, target):
        t0, w0 = self.t, self.window_s
        if self.mode == "motion_assisted" and target is not None and self.gp is not None:
            slots = self.nav.slots(self.n)
            i = self.nav.slot_toward(slots, target)
            s = slots[i]
            if s.contains(BACK_KEY) and len(s.options) == 1:
                det, lat = self.gp.sample_cued(self.rng, "tilt")
                self.live_g["tilt"]["prompts"] += 1
                if det == "tilt" and lat is not None and lat <= self.W:
                    self.t += lat + self.tm.feedback_s
                    self.live_g["tilt"]["hits"] += 1
                    self.nav.back()
                    self.mm["tilt_backs"] += 1
                    self._unprompted(self.t - t0, self.window_s - w0)
                    return None
                self.t += self.W
                self.window_s += self.W
        r = super()._cycle(target)
        dt = self.t - t0
        if self.mode == "motion_assisted" and r is None and self.gp is not None:
            lam = self._rate("tilt")
            if lam > 0 and self.rng.random() < 1 - math.exp(-lam * dt) and len(self.nav.stack) > 1:
                self.nav.back()
                self.mm["spurious_backs"] += 1
                self.mm["false_gesture_actions"] += 1
        self._unprompted(dt, self.window_s - w0)
        return r

    def _unprompted(self, dt: float, in_window: float) -> None:
        """False activations outside prompt windows: harmless, but observed by the detector and used
        as live false-activation evidence (exposure) by the optimiser."""
        if self.gp is None:
            return
        expo = max(0.0, dt - in_window)
        for k in KINDS:
            n_ev = int(self.rng.poisson(self._rate(k) * expo)) if self._rate(k) > 0 else 0
            self.live_g[k]["unprompted"] += n_ev
            self.live_g[k]["unprompted_exposure_s"] += expo
            self.mm["false_gesture_events"] += n_ev


def _with_live(e, cnt):
    from ..capability import Binom
    import copy
    if not cnt or not cnt["total"]:
        return e
    x = copy.copy(e)
    c, w, a = cnt["correct"], cnt["wrong"], cnt["abstain"]
    x.accuracy = Binom(e.accuracy.k + c, e.accuracy.n + c + w)
    x.abstain = Binom(e.abstain.k + a, e.abstain.n + c + w + a)
    return x


# --------------------------------------------------------------------------------------
# one cell = (replicate, motion profile, signal level): all conditions, identical sessions
# --------------------------------------------------------------------------------------
def run_cell(args) -> list[dict]:
    rep, motion, level, cfg = args
    profile = PROFILES[rep % len(PROFILES)]
    pools = motion_eeg_pools(rep, motion, cfg["test_per_target"], cfg["test_rest"])
    gp = gesture_pool(rep, motion, cfg["gesture_test_per_kind"], cfg["gesture_natural_s"])
    cat = build_catalog()
    tm = Timing()
    cal = pools[level]
    win = {n: fixed_window_for(cal[n], tm) for n in (2, 3, 5)}
    windows = {lvl: dict(win) for lvl in pools}
    r = routing_for(profile)
    tasks = make_tasks(profile, "matched", rep, cfg["n_tasks"])
    sched = [level] * len(tasks)
    seed = int(np.random.SeedSequence([rep, MOTION.index(motion), MM_LEVELS.index(level), 4242]).generate_state(1)[0])
    cap = capability_from(cal, gp)
    W = gesture_window(cap) if cap.gestures else 3.0
    ld, reach = length_dists(profile)
    t_opt = time.perf_counter()
    opt0 = optimise(cap, ld, reach, PTiming())
    t_opt = time.perf_counter() - t_opt
    routes_by = {k: {n: r[k][n]["routes"] for n in (2, 3, 5)} for k in ("adaptive", "static")}
    n_itr = itr_n(cal, tm)
    conds = primary_conditions() + [policy_condition(p) for p in all_policies()]
    rows = []
    for cond in conds:
        if cond.key in ("M1", "M2"):
            cond = MMCondition(**{**cond.__dict__, "n": n_itr})
        n0, exp_sel, routes, priors, confirm, gates = setup_condition(cond, cal, win, r, tm)
        spec = SessionSpec(profile, sched, tasks, cfg["idle_s"], seed, "matched")
        uses_g = cond.mode != "eeg_only" or cond.optimise
        sess = MMSession(cond, cat, pools, routes, exp_sel, tm, spec, n0, windows, priors, confirm, gates,
                         gpool=gp if uses_g else None, cap=cap, W=W, routes_by=routes_by, ldists=ld, reach=reach,
                         opt0=opt0 if cond.optimise else None)
        m = sess.run()
        d = m.to_dict()
        hours = (d["task_time_s"] + d["idle_s"]) / 3600
        cal_s = sum(EEG_CAL_S.values()) + (GESTURE_CAL_S if uses_g else 0.0)
        sent = d["correct_sent"] + d["wrong_sent"]
        msgs = d["correct_sent"] + d["timeouts"]
        rows.append({
            "condition": cond.key, "label": cond.label, "group": cond.group, "policy": cond.policy_key,
            "motion_profile": motion, "snr_level": level, "snr_uv": LEVELS[level], "replicate": rep,
            "patient_profile": profile, "n_start": n0 if not cond.optimise else opt0["chosen"]["n"],
            "initial_policy": opt0["chosen"]["key"] if cond.optimise else (cond.policy_key or None),
            "cmpm": d["cmpm"], "correct": d["correct_sent"], "wrong": d["wrong_sent"], "timeouts": d["timeouts"],
            "error_rate": d["error_rate"], "mean_task_s": d["mean_task_s"], "urgent_mean_s": d["urgent_mean_s"],
            "selections_per_correct": d["selections_per_correct"],
            "corrections": d["rejected_predictions"] + d["back_selections"] + sess.mm["tilt_backs"],
            "corrections_per_message": round((d["rejected_predictions"] + d["back_selections"] + sess.mm["tilt_backs"])
                                             / max(1, msgs), 3),
            "abstentions": d["abstentions"],
            "idle_false_sent_per_hour": round(3600 * d["idle_false_sent"] / d["idle_s"], 3) if d["idle_s"] else None,
            "idle_false_selections_per_min": round(60 * d["idle_false_selections"] / d["idle_s"], 3) if d["idle_s"] else None,
            "accidental_activations": sess.mm["false_gesture_actions"],
            "accidental_activations_per_hour": round(sess.mm["false_gesture_actions"] / max(hours, 1e-9), 2),
            "false_gesture_events_per_hour": round(sess.mm["false_gesture_events"] / max(hours, 1e-9), 2) if uses_g else 0.0,
            "gesture_decisions": sess.mm["gesture_decisions"], "gesture_fallbacks": sess.mm["gesture_fallbacks"],
            "tilt_backs": sess.mm["tilt_backs"], "spurious_backs": sess.mm["spurious_backs"],
            "policy_switches": sess.mm["policy_switches"],
            "calibration_overhead_s": round(cal_s, 1),
            "cmpm_30min_incl_calibration": round(d["cmpm"] * max(0.0, SESSION_MIN * 60 - cal_s) / (SESSION_MIN * 60), 4),
            "sent_without_final_eeg_yes": d["sent_without_final_yes"],
        })
    rows.append({"condition": "_optimiser", "motion_profile": motion, "snr_level": level, "replicate": rep,
                 "chosen": opt0["chosen"]["key"], "reason": opt0["reason"], "optimise_ms": round(1000 * t_opt, 1),
                 "modes_available": cap.modes(), "gesture_window_s": W, "readiness": opt0["readiness"],
                 "top_candidates": [{k: c[k] for k in ("key", "score", "cmpm", "feasible", "violations")}
                                    for c in opt0["candidates"][:6]],
                 "predicted_cmpm": {c["key"]: c["cmpm"]["mean"] for c in opt0["candidates"]}})
    return rows


# --------------------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------------------
def _boot(x: np.ndarray, rng, B: int = 2000) -> list[float]:
    if len(x) < 2:
        return [float(x.mean()) if len(x) else None] * 2
    idx = rng.integers(0, len(x), (B, len(x)))
    m = x[idx].mean(axis=1)
    return [round(float(np.quantile(m, 0.025)), 4), round(float(np.quantile(m, 0.975)), 4)]


METRICS = ["cmpm", "mean_task_s", "error_rate", "accidental_activations_per_hour", "corrections_per_message",
           "idle_false_sent_per_hour", "calibration_overhead_s", "cmpm_30min_incl_calibration", "urgent_mean_s"]


def _spearman(a, b) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return round(float(np.corrcoef(ra, rb)[0, 1]), 3)


def summarize(rows: list[dict]) -> dict:
    rng = np.random.default_rng(7)
    sess = [r for r in rows if r["condition"] != "_optimiser"]
    opts = [r for r in rows if r["condition"] == "_optimiser"]
    pooled_pred, pooled_meas = [], []
    cells = sorted({(r["motion_profile"], r["snr_level"]) for r in sess}, key=lambda c: (MOTION.index(c[0]), MM_LEVELS.index(c[1])))
    out_cells = []
    for motion, level in cells:
        cs = [r for r in sess if r["motion_profile"] == motion and r["snr_level"] == level]
        reps = sorted({r["replicate"] for r in cs})
        by = {}
        for r in cs:
            by.setdefault(r["condition"], {})[r["replicate"]] = r
        # oracle over fixed candidate policies, in hindsight on these test sessions
        cand = {k: v for k, v in by.items() if k.startswith("P:")}
        mean_c = {k: float(np.mean([v[i]["cmpm"] for i in reps])) for k, v in cand.items()}
        oracle = max(mean_c, key=mean_c.get)

        def ok(k):
            v = cand[k]
            err = [v[i]["error_rate"] for i in reps if v[i]["error_rate"] is not None]
            idle = [v[i]["idle_false_sent_per_hour"] for i in reps if v[i]["idle_false_sent_per_hour"] is not None]
            return (np.mean(err) if err else 0) <= 0.02 and (np.mean(idle) if idle else 0) <= 1.0
        safe = [k for k in cand if ok(k)]
        oracle_c = max(safe, key=mean_c.get) if safe else None
        best_eeg = max((k for k in cand if k.startswith("P:eeg_only")), key=mean_c.get)
        best_hyb = max((k for k in cand if not k.startswith("P:eeg_only")), key=mean_c.get)
        table = {}
        for key in ["M1", "M2", "M3", "M4", "M5"]:
            if key not in by:
                continue
            v = by[key]
            row = {"label": v[reps[0]]["label"], "replicates": len(reps)}
            for met in METRICS:
                x = np.array([v[i][met] for i in reps if v[i][met] is not None], dtype=float)
                row[met] = {"mean": round(float(x.mean()), 4) if len(x) else None, "ci95": _boot(x, rng) if len(x) else None}
            reg = np.array([by[oracle][i]["cmpm"] - v[i]["cmpm"] for i in reps])
            row["regret_vs_oracle"] = {"mean": round(float(reg.mean()), 4), "ci95": _boot(reg, rng)}
            if oracle_c:
                regc = np.array([by[oracle_c][i]["cmpm"] - v[i]["cmpm"] for i in reps])
                row["regret_vs_constrained_oracle"] = {"mean": round(float(regc.mean()), 4), "ci95": _boot(regc, rng)}
            if key == "M5":
                init = [v[i]["initial_policy"] for i in reps]
                sel = np.array([by[oracle][i]["cmpm"] - by["P:" + p][i]["cmpm"] for i, p in zip(reps, init)])
                row["initial_policies"] = {p: init.count(p) for p in sorted(set(init))}
                row["policy_selection_regret"] = {"mean": round(float(sel.mean()), 4), "ci95": _boot(sel, rng),
                                                  "note": "oracle CMPM minus the CMPM of the initially selected policy "
                                                          "run as a fixed policy on the same sessions"}
                row["policy_switches_per_session"] = round(float(np.mean([len(v[i]["policy_switches"]) for i in reps])), 2)
            table[key] = row

        def paired(a, b):
            d = np.array([by[a][i]["cmpm"] - by[b][i]["cmpm"] for i in reps])
            base = np.mean([by[b][i]["cmpm"] for i in reps])
            lo, hi = _boot(d, rng)
            verdict = "better" if lo is not None and lo > 0 else "worse" if hi is not None and hi < 0 else "no significant difference"
            return {"a": a, "b": b, "delta_cmpm": round(float(d.mean()), 4), "ci95": [lo, hi],
                    "ratio": round(float(np.mean([by[a][i]["cmpm"] for i in reps]) / base), 3) if base else None,
                    "a_better_in": f"{int((d > 0).sum())}/{len(d)}", "verdict": verdict}
        comps = [paired("M2", "M1"), paired("M3", "M2"), paired("M4", "M3"), paired("M5", "M3"), paired("M5", "M4"),
                 paired(best_hyb, best_eeg)]
        # policy model (calibration only) vs measured fixed-policy CMPM on held-out sessions
        ops = [o for o in opts if o["motion_profile"] == motion and o["snr_level"] == level]
        keys = sorted(set.intersection(*[set(o["predicted_cmpm"]) for o in ops])) if ops else []
        pred = [float(np.mean([o["predicted_cmpm"][k] for o in ops])) for k in keys]
        meas = [mean_c["P:" + k] for k in keys]
        pooled_pred += pred
        pooled_meas += meas
        model_check = {"policies": len(keys), "spearman_rank_correlation": _spearman(pred, meas),
                       "median_measured_over_predicted": round(float(np.median(np.array(meas) / np.maximum(pred, 1e-9))), 3)
                       if keys else None}
        out_cells.append({
            "motion_profile": motion, "snr_level": level, "snr_uv": LEVELS[level], "replicates": len(reps),
            "model_check": model_check,
            "conditions": table, "comparisons": comps,
            "oracle": {"policy": oracle[2:], "cmpm": round(mean_c[oracle], 4)},
            "constrained_oracle": None if oracle_c is None else {"policy": oracle_c[2:], "cmpm": round(mean_c[oracle_c], 4)},
            "best_fixed_eeg_only": {"policy": best_eeg[2:], "cmpm": round(mean_c[best_eeg], 4)},
            "best_fixed_multimodal": {"policy": best_hyb[2:], "cmpm": round(mean_c[best_hyb], 4)},
            "fixed_policies": {k[2:]: round(v, 4) for k, v in sorted(mean_c.items(), key=lambda kv: -kv[1])},
        })
    mc = {"policies_x_cells": len(pooled_pred), "spearman_rank_correlation": _spearman(pooled_pred, pooled_meas),
          "median_measured_over_predicted": round(float(np.median(np.array(pooled_meas) / np.maximum(pooled_pred, 1e-9))), 3)
          if pooled_pred else None,
          "note": ("Policy-model prediction from CALIBRATION data only vs CMPM measured on held-out simulated sessions "
                   "for the same fixed policy. Checks the optimiser's model against the simulator, not against patients.")}
    return {"cells": out_cells, "hybrid_verdicts": _verdicts(out_cells), "model_check": mc}


def _verdicts(cells: list[dict]) -> list[dict]:
    out = []
    for c in cells:
        m4 = next(x for x in c["comparisons"] if x["a"] == "M4")
        m5 = next(x for x in c["comparisons"] if x["a"] == "M5" and x["b"] == "M3")
        fx = c["comparisons"][-1]
        out.append({"motion_profile": c["motion_profile"], "snr_level": c["snr_level"],
                    "always_on_hybrid_vs_adaptive_eeg": m4["verdict"], "always_on_hybrid_ratio": m4["ratio"],
                    "optimised_vs_adaptive_eeg": m5["verdict"], "optimised_ratio": m5["ratio"],
                    "best_fixed_multimodal_vs_best_fixed_eeg": fx["verdict"], "best_fixed_ratio": fx["ratio"],
                    "best_fixed_multimodal": c["best_fixed_multimodal"]["policy"],
                    "best_fixed_eeg_only": c["best_fixed_eeg_only"]["policy"]})
    return out


LIMITATIONS = [
    "SIMULATION ONLY. EEG and head motion are synthetic; gesture amplitudes, timings, failure rates and "
    "incidental movement are modelling assumptions (acquisition/motion_sim.py), not patient data. Only the "
    "stationary IMU noise floor comes from a real (not worn) Unicorn recording.",
    "The simulated patient always intends the right answer; all errors come from the decoders (real FBCCA and "
    "real gesture-detector code on synthetic signals, held-out trials).",
    "Gesture outcomes are drawn from a finite held-out pool per profile and replicate; false activations are "
    "Poisson at the rate measured on 15 min of held-out synthetic natural movement.",
    "Head-motion EEG artifacts are modelled only for the high_motion profile.",
    "M4 runs hybrid even where the calibration marks gestures unusable (the engine would refuse). It shows what "
    "ASSUMING hybrid is better costs; it is not a recommended configuration.",
    "The oracle is chosen in hindsight on the same test sessions; regret against it is a best-case bound, not "
    "an achievable target.",
    "Calibration overhead is protocol time only (EEG 2/3/5-target calibrations; gesture protocol), amortised over "
    "an assumed 30-minute session.",
    "The policy model (policy.py) is evaluated here against Monte-Carlo simulation of the same simulator; agreement "
    "says nothing about real patients.",
    "No clinical validation. No real patient data. Real-EEG / real-IMU capability estimates require the headset.",
]


def run_multimodal(replicates: int = 5, n_tasks: int = 14, idle_s: float = 180.0, test_per_target: int = 40,
                   test_rest: int = 60, gesture_test_per_kind: int = 40, gesture_natural_s: float = 900.0,
                   workers: int = 4, motions=None, levels=None, out_dir: Path | None = None, log=print) -> dict:
    t0 = time.time()
    motions = motions or MOTION
    levels = levels or MM_LEVELS
    cfg = {"n_tasks": n_tasks, "idle_s": idle_s, "test_per_target": test_per_target, "test_rest": test_rest,
           "gesture_test_per_kind": gesture_test_per_kind, "gesture_natural_s": gesture_natural_s}
    builds = [("eeg", r, "accurate", cfg) for r in range(replicates)]
    builds += [("eeg", r, "high_motion", cfg) for r in range(replicates) if "high_motion" in motions]
    builds += [("gest", r, m, cfg) for r in range(replicates) for m in motions]
    log(f"Building/loading pools ({len(builds)} jobs, cached in {CACHE_DIR}) ...")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_build, builds))
        cells = [(r, m, lvl, cfg) for r in range(replicates) for m in motions for lvl in levels]
        log(f"Running {len(cells)} cells x {len(primary_conditions()) + len(all_policies())} conditions ...")
        rows = [x for rs in ex.map(run_cell, cells) for x in rs]
    summary = summarize(rows)
    gpools = {m: [gesture_pool(r, m, gesture_test_per_kind, gesture_natural_s).summary() for r in range(replicates)]
              for m in motions}
    cmd = (f"python scripts/run_multimodal_benchmark.py --replicates {replicates} --tasks {n_tasks} --idle {idle_s:g} "
           f"--test-per-target {test_per_target} --test-rest {test_rest} --gesture-trials {gesture_test_per_kind} "
           f"--gesture-natural {gesture_natural_s:g}")
    result = {
        "schema_version": SCHEMA_VERSION, "kind": "simulated_multimodal_benchmark", "data_origin": "simulated",
        "real_eeg": False, "real_imu": False, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "runtime_s": round(time.time() - t0, 1), "command": cmd,
        "research_question": ("Does adding optional head gestures (IMU) to SSVEP selection increase correctly "
                              "confirmed messages per minute, and for which synthetic patient profiles?"),
        "disclaimer": ("SIMULATED. Synthetic EEG and synthetic head motion decoded by the real FBCCA and gesture "
                       "code. Not empirical or clinical evidence."),
        "config": cfg | {"replicates": replicates, "motion_profiles": motions, "snr_levels_uv": {k: LEVELS[k] for k in levels},
                         "session_minutes_for_amortisation": SESSION_MIN},
        "conditions": [{"key": c.key, "label": c.label} for c in primary_conditions()],
        "oracle_candidates": [p.key for p in all_policies()],
        "metrics_definitions": {
            "cmpm": "correctly confirmed messages per minute of task time",
            "mean_task_s": "mean time per requested message (latency), incl. failed attempts until timeout",
            "error_rate": "wrong messages sent / messages sent",
            "accidental_activations_per_hour": "false gesture events that changed something (false step YES/NO, "
                                               "spurious tilt-back) per hour",
            "corrections_per_message": "rejected highlights + Go-back selections + tilt-backs per requested message",
            "idle_false_sent_per_hour": "messages sent during a no-control block (patient attends nothing)",
            "calibration_overhead_s": "protocol time for the calibrations the condition needs",
            "cmpm_30min_incl_calibration": "cmpm x (30 min - calibration) / 30 min",
            "regret_vs_oracle": "oracle CMPM (best fixed candidate policy in hindsight) - condition CMPM",
        },
        "summary": summary,
        "optimiser_decisions": [r for r in rows if r["condition"] == "_optimiser"],
        "gesture_pools": gpools,
        "sessions": [r for r in rows if r["condition"] != "_optimiser"],
        "limitations": LIMITATIONS,
    }
    out_dir = out_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "multimodal_results.json").write_text(json.dumps(result, indent=1, default=_json), encoding="utf-8")
    (out_dir / "multimodal_summary.md").write_text(markdown(result), encoding="utf-8")
    log(f"Done in {result['runtime_s']} s -> {out_dir / 'multimodal_results.json'}")
    return result


def _json(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, tuple):
        return list(o)
    return str(o)


def markdown(res: dict) -> str:
    L = [f"# NeuroSteno multimodal benchmark (SIMULATED) · {res['generated_at']}", "",
         f"> {res['disclaimer']}", "", f"Command: `{res['command']}` · runtime {res['runtime_s']} s", "",
         f"**Question:** {res['research_question']}", "",
         "Primary outcome: correctly confirmed messages per minute (CMPM), mean over replicates with 95% bootstrap CI.",
         "Final confirmation is a decoded EEG YES in every condition; gestures never send.", ""]
    L += ["## Does hybrid help? (verdict = 95% CI of the paired difference excludes 0)", "",
          "| motion profile | SNR | M4 always-on hybrid vs M3 | M5 optimised vs M3 | best fixed multimodal vs best fixed EEG |",
          "|---|---|---|---|---|"]
    for v in res["summary"]["hybrid_verdicts"]:
        L.append(f"| {v['motion_profile']} | {v['snr_level']} | {v['always_on_hybrid_vs_adaptive_eeg']} (x{v['always_on_hybrid_ratio']}) "
                 f"| {v['optimised_vs_adaptive_eeg']} (x{v['optimised_ratio']}) | {v['best_fixed_multimodal_vs_best_fixed_eeg']} "
                 f"(x{v['best_fixed_ratio']}; {v['best_fixed_multimodal']} vs {v['best_fixed_eeg_only']}) |")
    L.append("")
    for c in res["summary"]["cells"]:
        L += [f"## {c['motion_profile']} · SNR {c['snr_level']} ({c['snr_uv']} µV) · {c['replicates']} replicates", "",
              f"Oracle (hindsight): `{c['oracle']['policy']}` {c['oracle']['cmpm']:.3f} · constrained oracle: "
              f"`{(c['constrained_oracle'] or {}).get('policy')}` · best fixed EEG-only `{c['best_fixed_eeg_only']['policy']}` "
              f"{c['best_fixed_eeg_only']['cmpm']:.3f} · best fixed multimodal `{c['best_fixed_multimodal']['policy']}` "
              f"{c['best_fixed_multimodal']['cmpm']:.3f}", "",
              "| condition | CMPM [95% CI] | task s | error | accidental/h | corrections/msg | idle false sent/h | calib s | CMPM 30-min incl. calib | regret |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for k, r in c["conditions"].items():
            f = lambda m: r[m]["mean"]  # noqa: E731
            L.append(f"| {r['label']} | {f('cmpm'):.3f} {r['cmpm']['ci95']} | {f('mean_task_s')} | {f('error_rate')} | "
                     f"{f('accidental_activations_per_hour')} | {f('corrections_per_message')} | {f('idle_false_sent_per_hour')} | "
                     f"{f('calibration_overhead_s'):.0f} | {f('cmpm_30min_incl_calibration'):.3f} | {r['regret_vs_oracle']['mean']:.3f} |")
        m5 = c["conditions"].get("M5", {})
        if m5:
            L.append(f"\nM5 initial policies: {m5.get('initial_policies')} · policy-selection regret "
                     f"{m5['policy_selection_regret']['mean']:.3f} {m5['policy_selection_regret']['ci95']} · switches/session "
                     f"{m5.get('policy_switches_per_session')}")
        L.append("")
        for x in c["comparisons"]:
            L.append(f"- {x['a']} vs {x['b']}: ΔCMPM {x['delta_cmpm']:+.3f} {x['ci95']}, ratio {x['ratio']}, "
                     f"better in {x['a_better_in']} → **{x['verdict']}**")
        L.append("")
    mc = res["summary"]["model_check"]
    L += ["## Does the policy model predict the simulator?", "",
          f"Across {mc['policies_x_cells']} (policy, cell) pairs: Spearman rank correlation "
          f"{mc['spearman_rank_correlation']}, median measured/predicted CMPM {mc['median_measured_over_predicted']}. "
          + mc["note"], ""]
    L += ["## Gesture detector on held-out synthetic data", ""]
    for m, ps in res["gesture_pools"].items():
        sens = {k: [p["test_cued"][k]["sensitivity"] for p in ps] for k in KINDS}
        fa = {k: [p["test_false_activations_per_hour"][k] for p in ps] for k in KINDS}
        us = [p["calibration_usable"] for p in ps]
        L.append(f"- **{m}**: held-out sensitivity " + ", ".join(
            f"{k} {np.mean([s for s in v if s is not None]):.2f}" for k, v in sens.items()) +
            " · false activations/h " + ", ".join(f"{k} {np.mean(v):.1f}" for k, v in fa.items()) +
            f" · calibration marked usable (nod/shake/tilt) in " +
            ", ".join(f"{k} {sum(1 for u in us if u and u.get(k))}/{len(us)}" for k in KINDS))
    L += ["", "## Limitations", ""] + [f"- {x}" for x in res["limitations"]]
    return "\n".join(L) + "\n"
