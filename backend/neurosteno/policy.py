"""Interaction-policy optimiser: choose modality, target count, route and confirmation strategy to
maximise expected CORRECTLY CONFIRMED messages per minute, under explicit safety constraints and
with uncertainty from finite calibration data.

Policy  pi = (mode, n, routing, confirmation)
  mode          eeg_only | hybrid (EEG selection, nod/shake step confirmation, EEG fallback)
                | motion_assisted (hybrid + sustained tilt = "go back")
  n             2 | 3 | 5 SSVEP targets per screen
  routing       context (risk-aware shortcuts + urgent routes) | static (plain menu)
  confirmation  explicit (every step confirmed) | gated (step confirmation skipped when the
                SSVEP margin >= 1.5 x calibrated threshold)
In EVERY policy the final message needs a decoded EEG "YES, send it"; gestures can neither send
nor confirm the final review, nor trigger an urgent message.

Model: a COMPLETE MESSAGE as an absorbing Markov chain over navigation states (dynamic
programming / exact policy evaluation by solving the Bellman equations V = c + P V):
  state (d, e): d = correct selections made on the route to the phrase (0..L-1), e = how many
  wrong selections deep the patient is off the route (0..E, each needs a "Go back" selection).
  Per selection cycle (rest, SSVEP choose, step confirmation, feedback):
    abstention (stay, full window), correct accept (d+1), wrong accept (e+1, or a WRONG LEAF at the
    last step), rejected at confirmation (stay); off-route the intended target is "Go back"
    (motion_assisted: a sustained tilt first, EEG if it is missed). Spurious tilts (false
    activations) move the patient back one level.
  At the leaf: EEG final review. Right leaf: YES -> correct send (absorbing), NO -> back.
  Wrong leaf: NO -> back, YES -> WRONG MESSAGE SENT, the need remains (restart at Home).
  Step confirmation:
    EEG   2-target YES/NO, up to k attempts (abstentions), decoded accuracy q.
    gesture window W: P(nod), P(shake) from sensitivity, confusion and false activations
          (Poisson, rate lambda over W); both -> conflict -> not accepted; neither -> EEG fallback
          after W (the patient then looks at YES/NO).
  The path-length distribution L comes from the real navigator for every phrase (routing.lengths)
  weighted by the request distribution, so every one of the 1,474 phrases is in the objective.

  Expected time per correctly sent message T(pi) = sum_L pi_L V_L(0,0);  CMPM = 60 / T.
  Expected wrong messages per correct one W(pi) from the same chain (reward on wrong-send edges).
  Idle (no-control) false sends per hour from rest false-accepts, idle confirmations and the
  final review at rest.

Solution: every on-route level is identical except the last, so the Bellman equations are
solved exactly level by level: a small (E x E) system for the off-route excursion, then the
expected time per level tau and the last-level/review equation in closed form (restarts after a
wrong send included). solve_message_matrix() solves the full chain directly; tests check that
both agree.

Uncertainty: every probability is drawn from its posterior (Beta for rates from k/n trials,
Gamma for false-activation rates from events/exposure), D draws shared across candidate
policies (paired comparison). Decision rule (fixed before any benchmark was run):
  1. feasible = P(wrong message | sent) <= 2 %, idle false sends <= 1 per hour,
     P(step confirmed | wrong highlight) <= 10 % (confirmation reliability), every phrase
     reachable. Checked on posterior means (q90 reported): with ~10 calibration trials a q90
     bound cannot certify any policy, and the conservative SCORE below already carries the
     uncertainty. Final-confirmation accuracy is the same EEG YES/NO in every policy, so it is a
     global readiness check (posterior mean >= 0.9), not a policy discriminator.
  2. score = 10th percentile of CMPM over draws (conservative when evidence is thin).
  3. the best feasible EEG-only policy is the default. A multimodal policy replaces it only if
     P(CMPM_multimodal > CMPM_eeg_only) >= 0.9 over paired draws AND its score is higher.
     Without a usable gesture calibration, multimodal candidates are not generated at all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .capability import CapabilityProfile

LATENCY_S = 0.14
OFF_ROUTE_DEPTH = 3
DRAWS = 200
DECISION_QUANTILE = 0.10
P_BETTER_REQUIRED = 0.90
MAX_WRONG_PER_SENT = 0.02
MAX_IDLE_FALSE_SENDS_PER_HOUR = 1.0
MAX_STEP_FALSE_CONFIRM = 0.10
MIN_FINAL_CONFIRM_ACC = 0.90
GATE_FACTOR = 1.5


@dataclass(frozen=True)
class Policy:
    mode: str
    n: int
    routing: str = "context"
    gated: bool = False

    @property
    def key(self) -> str:
        return f"{self.mode}|n{self.n}|{self.routing}|{'gated' if self.gated else 'explicit'}"

    def to_dict(self) -> dict:
        return {"key": self.key, "mode": self.mode, "n": self.n, "routing": self.routing,
                "step_confirmation": "gated" if self.gated else "explicit",
                "step_confirm_modality": "EEG YES/NO" if self.mode == "eeg_only" else "nod/shake, EEG fallback",
                "final_confirmation": "EEG YES (always)",
                "correction": ("tilt = go back, EEG fallback" if self.mode == "motion_assisted" else
                               "reject at confirmation + EEG 'Go back'")}


@dataclass
class Timing:
    rest_s: float = 1.0
    feedback_s: float = 1.0
    sent_display_s: float = 4.0
    confirm_attempts: int = 2


# --------------------------------------------------------------------------------------
# posterior draws
# --------------------------------------------------------------------------------------
def draw_params(cap: CapabilityProfile, draws: int = DRAWS, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    out = {"eeg": {}, "gest": {}, "D": draws}
    for n, e in cap.eeg.items():
        d = {"acc": e.accuracy.draws(rng, draws), "ab": e.abstain.draws(rng, draws),
             "t": np.clip(rng.normal(e.decision_s, e.decision_sd / math.sqrt(max(e.accuracy.n, 1)), draws),
                          0.5, e.max_window_s),
             "T": np.full(draws, e.max_window_s), "rfa": e.rest_false_accept.draws(rng, draws),
             "g": e.gate_fraction,
             "pg": (e.gate_accuracy.draws(rng, draws) if e.gate_accuracy is not None else np.ones(draws))}
        out["eeg"][n] = d
    c = cap.confirm
    out["confirm"] = out["eeg"].get(2) if cap.confirm_eeg is None else {
        "acc": c.accuracy.draws(rng, draws), "ab": c.abstain.draws(rng, draws),
        "t": np.clip(rng.normal(c.decision_s, c.decision_sd / math.sqrt(max(c.accuracy.n, 1)), draws), 0.5,
                     c.max_window_s),
        "T": np.full(draws, c.max_window_s), "rfa": c.rest_false_accept.draws(rng, draws), "g": 0.0,
        "pg": np.ones(draws)}
    for k, g in cap.gestures.items():
        out["gest"][k] = {"s": g.sensitivity.draws(rng, draws), "c": g.confusion.draws(rng, draws),
                          "lam": g.false_activation.draws(rng, draws),
                          "lat": np.clip(rng.normal(g.latency_s, g.latency_sd / 2, draws), 0.3, None)}
    return out


# --------------------------------------------------------------------------------------
# per-cycle model
# --------------------------------------------------------------------------------------
def _eeg_confirm(c: dict, tm: Timing):
    q, b, tc, T = c["acc"], c["ab"], c["t"], c["T"]
    k = tm.confirm_attempts
    decide = 1 - b ** k
    t = sum(b ** i * (LATENCY_S + (1 - b) * tc + b * T) for i in range(k))
    return q * decide, (1 - q) * decide, t


def _gesture_confirm(P: dict, W: float, eeg_conf: tuple):
    """Returns (P(YES|right), P(YES|wrong), time|right, time|wrong) for nod/shake confirmation."""
    gn, gs = P["gest"]["nod"], P["gest"]["shake"]
    fn, fs = 1 - np.exp(-gn["lam"] * W), 1 - np.exp(-gs["lam"] * W)
    yes_e_r, yes_e_w, t_e = eeg_conf
    lat = (gn["lat"] + gs["lat"]) / 2
    res = []
    for intend_nod in (True, False):
        if intend_nod:
            pn = 1 - (1 - gn["s"]) * (1 - fn)
            ps = 1 - (1 - gn["c"]) * (1 - fs)
            yes_e = yes_e_r
        else:
            pn = 1 - (1 - gs["c"]) * (1 - fn)
            ps = 1 - (1 - gs["s"]) * (1 - fs)
            yes_e = yes_e_w
        yes_g = pn * (1 - ps)
        none = (1 - pn) * (1 - ps)
        res.append((yes_g + none * yes_e, (1 - none) * np.minimum(lat, W) + none * (W + t_e)))
    return res[0][0], res[1][0], res[0][1], res[1][1]


def cycle_model(pol: Policy, P: dict, tm: Timing, W: float) -> dict:
    """Per-cycle transition probabilities and times (arrays over posterior draws)."""
    e, c2 = P["eeg"][pol.n], P["confirm"]
    a, p, t, T = e["ab"], e["acc"], e["t"], e["T"]
    conf = _eeg_confirm(c2, tm)
    if pol.mode == "eeg_only":
        yes_r, yes_w, tc_r, tc_w = conf[0], conf[1], conf[2], conf[2]
    else:
        yes_r, yes_w, tc_r, tc_w = _gesture_confirm(P, W, conf)
    g = e["g"] if pol.gated else 0.0
    pg = e["pg"]
    p_rest = np.clip((p - g * pg) / (1 - g), 0, 1) if g < 1 else p
    F = (1 - a) * (g * pg + (1 - g) * p_rest * yes_r)
    Wr = (1 - a) * (g * (1 - pg) + (1 - g) * (1 - p_rest) * yes_w)
    cyc = (a * (tm.rest_s + LATENCY_S + T + tm.feedback_s)
           + (1 - a) * (tm.rest_s + LATENCY_S + t + (1 - g) * (p_rest * tc_r + (1 - p_rest) * tc_w) + tm.feedback_s))
    q, b, tc, T2 = c2["acc"], c2["ab"], c2["t"], c2["T"]
    review = (LATENCY_S + (1 - b) * tc + b * T2) / np.maximum(1e-3, 1 - b)
    out = {"F": F, "Wr": Wr, "cycle": cyc, "q": q, "review": review, "yes_r": yes_r, "yes_w": yes_w}
    if pol.mode == "motion_assisted":
        gt = P["gest"]["tilt"]
        out["tilt_s"], out["tilt_lat"] = gt["s"], gt["lat"]
        out["beta"] = 1 - np.exp(-gt["lam"] * cyc)          # spurious "go back" per on-route cycle
    return out


# --------------------------------------------------------------------------------------
# the chain (exact policy evaluation, batched over draws)
# --------------------------------------------------------------------------------------
def _off_route(cm: dict, tm: Timing, W: float, motion: bool) -> np.ndarray:
    """x[e] = expected time to get from e wrong selections deep back onto the route (e = 1..E)."""
    E = OFF_ROUTE_DEPTH
    F, Wr, cyc = cm["F"], cm["Wr"], cm["cycle"]
    D = F.shape[0]
    if motion:
        ts, tl = cm["tilt_s"], cm["tilt_lat"]
        cost = ts * (tl + tm.feedback_s) + (1 - ts) * (W + cyc)
        up, dn = ts + (1 - ts) * F, (1 - ts) * Wr
    else:
        cost, up, dn = cyc, F, Wr
    A = np.zeros((D, E, E))
    b = np.repeat(cost[:, None], E, axis=1)
    for i in range(E):            # state e = i + 1
        A[:, i, i] = up + dn       # leave the state (stay cancels out)
        if i > 0:
            A[:, i, i - 1] = -up
        if i + 1 < E:
            A[:, i, i + 1] = -dn
        else:
            A[:, i, i] -= dn       # deeper than E is lumped into E
    A += np.eye(E)[None] * 1e-12
    return np.linalg.solve(A, b[..., None])[..., 0]


def solve_message(L: int, cm: dict, tm: Timing, W: float, motion: bool = False,
                  x_off: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Expected time to a correctly sent message and expected wrong sends, from Home, for a phrase
    L selections away (arrays over draws). Exact level-by-level solution of the chain in
    solve_message_matrix (tests check equality):
      tau_0 = (c + W' x_1) / F',  tau_d = (c + W' x_1 + beta tau_{d-1}) / F'   (spurious back d -> d-1)
      U F' q = K + beta tau_{L-2} + W' (1-q) sum_d tau_d   (last level + review, restarts included)
    where F' = (1-beta) F, W' = (1-beta) Wr and beta = P(spurious tilt) per cycle (0 unless
    motion_assisted)."""
    F, Wr, cyc, q, R = cm["F"], cm["Wr"], cm["cycle"], cm["q"], cm["review"]
    fb, sent = tm.feedback_s, tm.sent_display_s
    x = _off_route(cm, tm, W, motion) if x_off is None else x_off
    beta = cm["beta"] if motion else np.zeros_like(F)
    Fp, Wp = (1 - beta) * F, (1 - beta) * Wr
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        base = cyc + Wp * x[:, 0]
        taus = []
        prev = np.zeros_like(F)
        for d in range(L - 1):
            tau = (base + (beta * prev if d > 0 else 0.0)) / Fp
            taus.append(tau)
            prev = tau
        sum_tau = np.sum(taus, axis=0) if taus else np.zeros_like(F)
        K = cyc + Fp * (R + q * sent + (1 - q) * fb) + Wp * (R + (1 - q) * sent + q * fb)
        U = (K + (beta * prev if L >= 2 else 0.0) + Wp * (1 - q) * sum_tau) / (Fp * q)
        T0 = sum_tau + U
        N = Wp * (1 - q) / (Fp * q)
    bad = ~np.isfinite(T0) | (T0 <= 0) | (Fp < 1e-4)
    return np.where(bad, np.inf, T0), np.where(bad, np.inf, N)


def solve_message_matrix(L: int, cm: dict, tm: Timing, W: float, motion: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Reference: the full absorbing chain over (d, e), solved directly (used in tests)."""
    E = OFF_ROUTE_DEPTH
    D = cm["F"].shape[0]
    S = L * (E + 1)
    idx = lambda d, e: d * (E + 1) + e  # noqa: E731
    Pm = np.zeros((D, S, S))
    cost = np.zeros((D, S))
    wrong = np.zeros((D, S))
    F, Wr, cyc, q, R = cm["F"], cm["Wr"], cm["cycle"], cm["q"], cm["review"]
    fb, sent = tm.feedback_s, tm.sent_display_s
    stay = 1 - F - Wr
    for d in range(L):
        # on route
        s = idx(d, 0)
        beta = cm.get("beta") if motion else None
        keep = 1.0 if beta is None else (1 - beta)
        cost[:, s] += cyc
        Pm[:, s, s] += keep * stay
        if d < L - 1:
            Pm[:, s, idx(d + 1, 0)] += keep * F
            Pm[:, s, idx(d, 1)] += keep * Wr
        else:
            # right leaf -> review: YES (q) absorbs; NO -> back to this screen
            cost[:, s] += keep * F * (R + q * sent + (1 - q) * fb)
            Pm[:, s, s] += keep * F * (1 - q)
            # wrong leaf -> review: YES (1-q) = WRONG SEND -> restart at Home; NO -> back
            cost[:, s] += keep * Wr * (R + (1 - q) * sent + q * fb)
            Pm[:, s, s] += keep * Wr * q
            Pm[:, s, idx(0, 0)] += keep * Wr * (1 - q)
            wrong[:, s] += keep * Wr * (1 - q)
        if beta is not None:
            Pm[:, s, idx(max(d - 1, 0), 0)] += beta
        # off route: the intended target is "Go back"
        for e in range(1, E + 1):
            s = idx(d, e)
            up, down = idx(d, e - 1), idx(d, min(e + 1, E))
            if motion:
                ts, tl = cm["tilt_s"], cm["tilt_lat"]
                cost[:, s] += ts * (tl + fb) + (1 - ts) * (W + cyc)
                Pm[:, s, up] += ts + (1 - ts) * F
                Pm[:, s, down] += (1 - ts) * Wr
                Pm[:, s, s] += (1 - ts) * stay
            else:
                cost[:, s] += cyc
                Pm[:, s, up] += F
                Pm[:, s, down] += Wr
                Pm[:, s, s] += stay
    A = np.eye(S)[None] - Pm
    # guard against no-progress chains (singular): add tiny leak to keep the solve finite
    bad = (F < 1e-4)
    if bad.any():
        A[bad] += np.eye(S)[None] * 1e-6
    V = np.linalg.solve(A, cost[..., None])[..., 0]
    N = np.linalg.solve(A, wrong[..., None])[..., 0]
    T0 = V[:, 0]
    T0 = np.where(bad | ~np.isfinite(T0) | (T0 <= 0), np.inf, T0)
    return T0, N[:, 0]


def idle_false_sends_per_hour(pol: Policy, P: dict, tm: Timing, W: float, mean_depth: float) -> np.ndarray:
    e, c2 = P["eeg"][pol.n], P["confirm"]
    r, T = e["rfa"], e["T"]
    k = tm.confirm_attempts
    eeg_yes_idle = 0.5 * (1 - (1 - c2["rfa"]) ** k)
    if pol.mode == "eeg_only":
        yes_idle = eeg_yes_idle
    else:
        fn = 1 - np.exp(-P["gest"]["nod"]["lam"] * W)
        fs = 1 - np.exp(-P["gest"]["shake"]["lam"] * W)
        yes_idle = fn * (1 - fs) + (1 - fn) * (1 - fs) * eeg_yes_idle
    g = e["g"] if pol.gated else 0.0
    cycle_idle = tm.rest_s + LATENCY_S + T
    sel_h = 3600 / cycle_idle * r * (g + (1 - g) * yes_idle)
    return sel_h / max(mean_depth, 1.0) * 0.5


def evaluate(pol: Policy, P: dict, ldist: dict[int, float], tm: Timing, W: float) -> dict:
    cm = cycle_model(pol, P, tm, W)
    T = np.zeros(P["D"])
    N = np.zeros(P["D"])
    motion = pol.mode == "motion_assisted"
    x_off = _off_route(cm, tm, W, motion)
    for L, w in ldist.items():
        t, nwr = solve_message(int(L), cm, tm, W, motion, x_off)
        T += w * t
        N += w * nwr
    cmpm = np.where(np.isfinite(T), 60.0 / T, 0.0)
    wrong_per_sent = N / (1 + N)
    depth = sum(L * w for L, w in ldist.items())
    idle = idle_false_sends_per_hour(pol, P, tm, W, depth)
    return {"cmpm": cmpm, "time_s": T, "wrong_per_sent": wrong_per_sent, "idle_fs_h": idle, "q": cm["q"],
            "step_yes_right": np.broadcast_to(cm["yes_r"], T.shape), "step_yes_wrong": np.broadcast_to(cm["yes_w"], T.shape)}


def _q(x: np.ndarray, p: float) -> float:
    x = x[np.isfinite(x)]
    return float(np.quantile(x, p)) if len(x) else math.inf


# --------------------------------------------------------------------------------------
# path lengths for every phrase
# --------------------------------------------------------------------------------------
def length_distribution(catalog, n: int, shortcuts: tuple, urgent: tuple, p: dict[str, float]) -> tuple[dict, dict]:
    """{L: probability} over the request distribution, and a reachability report for ALL phrases."""
    from .routing import lengths
    L = lengths(catalog, n, tuple(shortcuts), tuple(urgent))
    leaves = catalog.leaves()
    reach = [x.id for x in leaves if x.id in L and L[x.id] >= 1]
    dist: dict[int, float] = {}
    tot = 0.0
    for lid, pr in p.items():
        if lid in L:
            dist[L[lid]] = dist.get(L[lid], 0.0) + pr
            tot += pr
    dist = {k: v / tot for k, v in sorted(dist.items())}
    return dist, {"phrases": len(leaves), "reachable": len(reach), "all_reachable": len(reach) == len(leaves),
                  "max_selections": max(L.values()) if L else None}


# --------------------------------------------------------------------------------------
# optimiser
# --------------------------------------------------------------------------------------
def candidates(cap: CapabilityProfile, allowed_n=(2, 3, 5), routings=("context", "static"),
               allow_gated: bool = True) -> list[Policy]:
    modes = cap.modes()
    out = []
    for mode in ("eeg_only", "hybrid", "motion_assisted"):
        if not modes[mode]:
            continue
        for n in allowed_n:
            if n not in cap.eeg:
                continue
            for r in routings:
                for gated in ((False, True) if allow_gated and cap.eeg[n].gate_fraction > 0 else (False,)):
                    out.append(Policy(mode, n, r, gated))
    return out


def gesture_window(cap: CapabilityProfile) -> float:
    if cap.gesture_window_s:
        return cap.gesture_window_s
    lats = [g.latency_s + 2 * g.latency_sd for k, g in cap.gestures.items() if k in ("nod", "shake")]
    return float(np.clip(max(lats) if lats else 3.0, 1.5, 4.0))


def optimise(cap: CapabilityProfile, ldists: dict, reach: dict, tm: Timing | None = None,
             draws: int = DRAWS, seed: int = 0, allowed_n=(2, 3, 5), routings=("context", "static"),
             allow_gated: bool = True, current: str | None = None, hysteresis: float = 0.0) -> dict:
    """ldists[(routing, n)] = {L: p}; reach[(routing, n)] = reachability report."""
    tm = tm or Timing()
    P = draw_params(cap, draws, seed)
    W = gesture_window(cap)
    cands = candidates(cap, allowed_n, routings, allow_gated)
    rows, raw = [], {}
    for pol in cands:
        if (pol.routing, pol.n) not in ldists:
            continue
        ev = evaluate(pol, P, ldists[(pol.routing, pol.n)], tm, W)
        raw[pol.key] = ev
        viol = []
        w90 = _q(ev["wrong_per_sent"], 0.9)
        wmean = float(np.mean(np.where(np.isfinite(ev["wrong_per_sent"]), ev["wrong_per_sent"], 1.0)))
        idle_mean = float(np.mean(ev["idle_fs_h"]))
        q10 = _q(ev["q"], 0.1)
        sfc = float(np.mean(ev["step_yes_wrong"]))
        if wmean > MAX_WRONG_PER_SENT:
            viol.append(f"P(wrong message | sent) {wmean:.3f} > {MAX_WRONG_PER_SENT}")
        if idle_mean > MAX_IDLE_FALSE_SENDS_PER_HOUR:
            viol.append(f"idle false sends {idle_mean:.2f}/h > {MAX_IDLE_FALSE_SENDS_PER_HOUR}/h")
        if not pol.gated and sfc > MAX_STEP_FALSE_CONFIRM:
            viol.append(f"P(step confirmed | wrong highlight) {sfc:.3f} > {MAX_STEP_FALSE_CONFIRM}")
        if not reach[(pol.routing, pol.n)]["all_reachable"]:
            viol.append("not every phrase reachable")
        rows.append({**pol.to_dict(),
                     "cmpm": {"mean": round(float(np.mean(ev["cmpm"])), 4), "q10": round(_q(ev["cmpm"], 0.1), 4),
                              "q90": round(_q(ev["cmpm"], 0.9), 4)},
                     "time_per_message_s": round(_q(ev["time_s"], 0.5), 1),
                     "wrong_per_sent": {"mean": round(wmean, 4), "q90": round(w90, 4)},
                     "idle_false_sends_per_hour": {"mean": round(idle_mean, 3),
                                                   "q90": round(_q(ev["idle_fs_h"], 0.9), 3)},
                     "final_confirm_accuracy_q10": round(q10, 3),
                     "step_false_confirm": round(sfc, 4),
                     "step_confirm": {"p_yes_if_right": round(float(np.mean(ev["step_yes_right"])), 3),
                                      "p_yes_if_wrong": round(float(np.mean(ev["step_yes_wrong"])), 4)},
                     "feasible": not viol, "violations": viol, "score": round(_q(ev["cmpm"], DECISION_QUANTILE), 4)})
    c2 = cap.confirm
    readiness = {"final_confirmation_accuracy_mean": round(c2.accuracy.mean, 3) if c2 else None,
                 "final_confirmation_accuracy_interval_90": c2.accuracy.to_dict()["interval_90"] if c2 else None,
                 "ok": bool(c2 and c2.accuracy.mean >= MIN_FINAL_CONFIRM_ACC),
                 "rule": f"posterior mean >= {MIN_FINAL_CONFIRM_ACC} (EEG YES/NO; identical in every policy)"}
    if not readiness["ok"]:
        readiness["warning"] = ("Final YES/NO confirmation reliability is not established on this data origin; "
                                "recalibrate 2 targets before relying on any policy.")
    feas = [r for r in rows if r["feasible"]]
    eeg = [r for r in feas if r["mode"] == "eeg_only"]
    mm = [r for r in feas if r["mode"] != "eeg_only"]
    base = max(eeg, key=lambda r: r["score"]) if eeg else None
    for r in rows:
        if base is not None and r["key"] != base["key"]:
            r["p_better_than_eeg_only"] = round(float(np.mean(raw[r["key"]]["cmpm"] > raw[base["key"]]["cmpm"])), 3)
    chosen, why = base, None
    if base is None and mm:
        # No EEG-only policy is safe enough; a calibrated multimodal one is. Safety constraints
        # take precedence over the EEG-only default (gestures still never send a message).
        chosen = max(mm, key=lambda r: r["score"])
        why = (f"No EEG-only policy meets the safety constraints at this signal level; {chosen['key']} does "
               "(gesture step confirmation lowers wrong acceptances). Final confirmation stays EEG.")
    elif base is None:
        alt = [r for r in rows if r["mode"] == "eeg_only"] or rows
        chosen = min(alt, key=lambda r: (len(r["violations"]), -r["score"])) if alt else None
        why = "No policy satisfies every safety constraint; using the least-violating EEG-only policy."
    else:
        best_mm = max(mm, key=lambda r: r["score"]) if mm else None
        if best_mm is None:
            why = ("EEG-only: " + cap.modes()["why"]["hybrid"]) if not cap.modes()["hybrid"] else \
                "EEG-only: no multimodal policy met the safety constraints."
        elif best_mm["p_better_than_eeg_only"] >= P_BETTER_REQUIRED and best_mm["score"] > base["score"]:
            chosen = best_mm
            why = (f"{best_mm['mode']} beats the best EEG-only policy in {best_mm['p_better_than_eeg_only']:.0%} of "
                   f"posterior draws (needs {P_BETTER_REQUIRED:.0%}); conservative CMPM {best_mm['score']:.2f} vs "
                   f"{base['score']:.2f}.")
        else:
            why = (f"EEG-only kept: best multimodal policy ({best_mm['key']}) is better in only "
                   f"{best_mm['p_better_than_eeg_only']:.0%} of posterior draws (needs {P_BETTER_REQUIRED:.0%}), "
                   f"conservative CMPM {best_mm['score']:.2f} vs {base['score']:.2f}.")
        if why is None:
            why = "Highest conservative expected correctly-confirmed messages/min."
    if current and chosen and current != chosen["key"] and hysteresis > 0:
        cur = next((r for r in rows if r["key"] == current), None)
        if cur is not None and cur["feasible"] and chosen["score"] < cur["score"] * (1 + hysteresis):
            why = (f"Keeping {current}: {chosen['key']} only {chosen['score'] / max(cur['score'], 1e-9) - 1:+.0%} "
                   f"better (< {hysteresis:.0%} switch threshold).")
            chosen = cur
    rows.sort(key=lambda r: (not r["feasible"], -r["score"]))
    return {"chosen": chosen, "reason": why, "baseline_eeg_only": base, "candidates": rows, "readiness": readiness,
            "gesture_window_s": round(W, 2), "draws": draws,
            "rules": {"score": f"q{int(DECISION_QUANTILE * 100)} of CMPM over posterior draws",
                      "multimodal_requires_p_better": P_BETTER_REQUIRED,
                      "max_wrong_per_sent": MAX_WRONG_PER_SENT,
                      "max_idle_false_sends_per_hour": MAX_IDLE_FALSE_SENDS_PER_HOUR,
                      "max_step_false_confirm": MAX_STEP_FALSE_CONFIRM,
                      "constraints_on": "posterior means (q90 reported per candidate)",
                      "min_final_confirm_accuracy": MIN_FINAL_CONFIRM_ACC,
                      "gestures_can_send": False, "gestures_can_trigger_urgent": False},
            "capability_claim": cap.to_dict()["claim"], "capability_origin": cap.origin,
            "note": ("Model estimate (policy.py); inputs are calibration and live evidence on the "
                     f"'{cap.origin}' data origin. Not a measured throughput.")}
