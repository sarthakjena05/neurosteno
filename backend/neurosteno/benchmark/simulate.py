"""Monte-Carlo sessions for the A/B/C/D comparison.

The simulated patient always knows the intended phrase and attends the slot that the real
navigator says leads toward it (Navigator.slot_toward, including "Go back" when off-path); on a
highlight it attends YES iff the highlight is right; at the final review it attends YES iff the
phrase is the intended one. ALL errors therefore come from the decoder, whose outcomes are drawn
from the empirical pools (real FBCCA code on synthetic EEG, held-out test trials).

Timing mirrors engine.py: rest_s before every choose attempt, visual latency + decision window,
confirmation directly after a highlight, feedback_s after accept/reject/abstain, final review
right after the leaf is accepted, sent_display_s after a sent message, then Home.

Conditions
  A<n>  fixed hierarchical menu (static routing), fixed n, conventional fixed-window forced-choice
        SSVEP (window chosen per n by Laplace-smoothed Wolpaw ITR on the calibration set), no
        per-step confirmation, final review as a forced YES/NO choice.
  B<n>  A + context-aware semantic routing (shortcuts + urgent routes from routing.plan_routing).
  C     B + signal-adaptive n chosen ONCE from the calibration estimates (adaptive.choose_n).
        Without confirmations C has no online measure of its own accuracy, so it cannot re-adapt.
  D     full NeuroSteno: context + risk-aware routing, dynamic stopping with abstention and
        rest/no-control rejection, explicit YES/NO confirmation of every selection, explicit
        final confirmation, and live re-adaptation of n from the patient's own YES/NO answers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..adaptive import LATENCY_S, OnlineTracker, Rel, Timing, choose_n, live_estimates
from ..navigation import BACK_KEY, Navigator
from ..phrases.catalog import Catalog
from .pool import FIXED_WINDOWS, GATE_FACTOR, Pool

TASK_TIMEOUT_S = 300.0
CAREGIVER_RESUME_S = 15.0
ABSTAIN_PAUSE_AFTER = 4


@dataclass
class Condition:
    key: str
    label: str
    routing: str            # static | adaptive
    n: int | None           # fixed n, or None = chosen by adaptive.choose_n
    decoder: str            # fixed | dynamic
    step_confirm: bool
    live_adapt: bool = False
    confirm_gate: float | None = None   # skip per-step confirmation when margin >= gate x threshold


def conditions() -> list[Condition]:
    out = []
    for n in (2, 3, 5):
        out.append(Condition(f"A{n}", f"A: fixed menu, {n} targets", "static", n, "fixed", False))
    for n in (2, 3, 5):
        out.append(Condition(f"B{n}", f"B: context routing, {n} targets", "adaptive", n, "fixed", False))
    out.append(Condition("C", "C: context routing + adaptive n", "adaptive", None, "fixed", False))
    out.append(Condition("D", "D: full NeuroSteno", "adaptive", None, "dynamic", True, live_adapt=True))
    out.append(Condition("D+", "D+: full NeuroSteno, confidence-gated step confirmation", "adaptive", None,
                         "dynamic", True, live_adapt=True, confirm_gate=GATE_FACTOR))
    return out


# --------------------------------------------------------------------------------------
def wolpaw_bits(n: int, p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-9)
    if p <= 1 / n:
        return 0.0
    return math.log2(n) + p * math.log2(p) + (1 - p) * math.log2((1 - p) / (n - 1))


def fixed_window_for(pool: Pool, timing: Timing) -> float:
    """Conventional choice of a fixed window: maximise ITR (bits/min) on the calibration trials,
    with Laplace-smoothed accuracy so a perfect small sample does not force the shortest window."""
    n_cal = max(1, pool.calibration["n_cued_trials"])
    best, best_w = -1.0, FIXED_WINDOWS[-1]
    for w, acc in pool.fixed_calib_acc.items():
        p = (acc * n_cal + 1) / (n_cal + 2)
        rate = wolpaw_bits(pool.n, p) * 60 / (timing.rest_s + LATENCY_S + w + timing.feedback_s)
        if rate > best:
            best, best_w = rate, w
    return best_w


def calib_rel(pool: Pool, decoder: str, window: float | None = None, op: str = "strict") -> Rel:
    """What the system believes after calibration (calibration set only, never the test trials)."""
    st = pool.stopping if op == "strict" else pool.stopping_step
    if decoder == "fixed":
        n_cal = max(1, pool.calibration["n_cued_trials"])
        acc = (pool.fixed_calib_acc[window] * n_cal + 1) / (n_cal + 2)
        return Rel(pool.n, acc, 0.0, window, window, "calibration (fixed window)", n_cal)
    est = (pool.calibration if op == "strict" else pool.calibration_step)["dynamic_stopping_estimate"]
    n_acc = est.get("n_accepted") or 0
    acc = ((est["accuracy_of_accepted"] or 1 / pool.n) * n_acc + 1) / (n_acc + 2)
    return Rel(pool.n, acc, est["abstention_rate"], est["mean_decision_s"] or st.max_window_s,
               st.max_window_s, "calibration (dynamic stopping)", pool.calibration["n_cued_trials"])


# --------------------------------------------------------------------------------------
@dataclass
class SessionSpec:
    profile: str
    level_schedule: list[str]          # signal level per task (supports degradation)
    tasks: list[tuple[str, bool]]      # (phrase id, is urgent probe)
    idle_s: float
    seed: int
    mix: str


@dataclass
class Metrics:
    task_time_s: float = 0.0
    correct_sent: int = 0
    wrong_sent: int = 0
    timeouts: int = 0
    choose_attempts: int = 0
    accepted_selections: int = 0
    rejected_predictions: int = 0
    abstentions: int = 0
    no_control_abstentions: int = 0
    back_selections: int = 0
    caregiver_pauses: int = 0
    gated_accepts: int = 0
    task_times: list = field(default_factory=list)
    urgent_times: list = field(default_factory=list)
    n_switches: list = field(default_factory=list)
    n_used: dict = field(default_factory=dict)
    idle_s: float = 0.0
    idle_false_selections: int = 0
    idle_false_sent: int = 0

    def to_dict(self) -> dict:
        sent = self.correct_sent + self.wrong_sent
        return {
            "task_time_s": round(self.task_time_s, 1),
            "correct_sent": self.correct_sent, "wrong_sent": self.wrong_sent, "timeouts": self.timeouts,
            "cmpm": round(60 * self.correct_sent / self.task_time_s, 4) if self.task_time_s else 0.0,
            "error_rate": round(self.wrong_sent / sent, 4) if sent else None,
            "selections_per_correct": round(self.accepted_selections / self.correct_sent, 3) if self.correct_sent else None,
            "choose_attempts": self.choose_attempts, "accepted_selections": self.accepted_selections,
            "rejected_predictions": self.rejected_predictions, "abstentions": self.abstentions,
            "no_control_abstentions": self.no_control_abstentions, "back_selections": self.back_selections,
            "caregiver_pauses": self.caregiver_pauses, "gated_accepts": self.gated_accepts,
            "mean_task_s": round(float(np.mean(self.task_times)), 2) if self.task_times else None,
            "median_task_s": round(float(np.median(self.task_times)), 2) if self.task_times else None,
            "urgent_mean_s": (round(float(np.mean([t for t in self.urgent_times if t is not None])), 2)
                              if any(t is not None for t in self.urgent_times) else None),
            "urgent_completed": f"{sum(1 for t in self.urgent_times if t is not None)}/{len(self.urgent_times)}",
            "idle_s": self.idle_s, "idle_false_selections": self.idle_false_selections,
            "idle_false_sent": self.idle_false_sent,
            "n_used": self.n_used, "n_switches": self.n_switches,
        }


class Session:
    def __init__(self, cond: Condition, catalog: Catalog, pools: dict[str, dict[int, Pool]],
                 routes: dict[int, tuple[tuple, tuple]], exp_sel: dict[int, float], timing: Timing,
                 spec: SessionSpec, initial_n: int, windows: dict[str, dict[int, float]],
                 priors: dict[int, Rel] | None, confirm_prior: Rel | None, gates: dict | None = None):
        self.c, self.cat, self.pools, self.routes = cond, catalog, pools, routes
        self.exp_sel, self.tm, self.spec = exp_sel, timing, spec
        self.n = initial_n
        self.windows = windows
        self.priors, self.confirm_prior = priors, confirm_prior
        self.gates = gates if cond.confirm_gate is not None else None
        self.last_ratio = 0.0
        self.rng = np.random.default_rng(spec.seed)
        self.m = Metrics()
        self.t = 0.0
        self.level = spec.level_schedule[0]
        self.tracker = OnlineTracker()
        self.since_switch = 0
        self.navs = {}
        self.nav = self._nav_for(self.n)

    # ---- helpers -------------------------------------------------------------------
    def _nav_for(self, n: int) -> Navigator:
        sc, ur = self.routes[n]
        nav = Navigator(self.cat, list(sc), list(ur))
        nav.track_undo = False
        return nav

    def _switch_n(self, new: int) -> None:
        """Change target count mid-message; keep the patient's place in the hierarchy (node path)."""
        old = self.nav
        self.n = new
        nav = self._nav_for(new)
        nav.stack = [f for f in old.stack if not f.subset]  # group subsets depend on n
        if not nav.stack:
            nav.home()
        nav.crumbs = []
        self.nav = nav

    def _decode(self, n: int, label: int | None, k: int | None = None, op: str = "strict") -> tuple[int | None, float, bool]:
        """Returns (prediction or None, seconds from attempt start, rest-kind abstention).
        n = target set in use, k = slots actually shown (<= n)."""
        pool = self.pools[self.level][n]
        i = pool.sample(self.rng, label)
        w = self.windows[self.level][n] if self.c.decoder == "fixed" else None
        pred, dt, rest, ratio = pool.decode_subset(i, k or n, w, op)
        self.last_ratio = ratio
        return pred, LATENCY_S + dt, rest

    def _confirm(self, want_yes: bool | None, attempts: int | None) -> bool | None:
        """Decode YES/NO. want_yes None = patient attends nothing. Returns True/False, or None if
        undecided after `attempts` (None = retry until decided)."""
        k = 0
        while True:
            pred, dt, _ = self._decode(2, None if want_yes is None else (0 if want_yes else 1))
            self.t += dt
            if pred is not None:
                return pred == 0
            k += 1
            if attempts is not None and k >= attempts:
                return None
            if self.t > self._deadline:
                return None

    def _maybe_adapt(self) -> None:
        if not self.c.live_adapt or self.since_switch < 8:
            return
        est = live_estimates(self.priors, self.tracker, self.n)
        conf = self.confirm_prior
        dec = choose_n(est, conf, self.exp_sel, self.tm, sorted(est), step_confirm=True, current=self.n,
                       gates=self.gates)
        if dec["n"] != self.n:
            self.m.n_switches.append({"t": round(self.t, 1), "from": self.n, "to": dec["n"]})
            self._switch_n(dec["n"])
            self.since_switch = 0

    # ---- one selection cycle ---------------------------------------------------------
    def _cycle(self, target: str | None) -> str | None:
        """One choose (+confirm) cycle. Returns the leaf id if a phrase reached final review and
        was sent ('SENT:<id>'), else None."""
        nav, n, tm = self.nav, self.n, self.tm
        slots = nav.slots(n)
        intended = None if target is None else nav.slot_toward(slots, target)
        self.t += tm.rest_s
        self.m.choose_attempts += 1
        op = "step" if (self.c.step_confirm and self.c.confirm_gate is None) else "strict"
        pred, dt, rest_kind = self._decode(n, intended, len(slots), op)
        self.t += dt
        if pred is None:
            if rest_kind:
                self.m.no_control_abstentions += 1
                return None
            self.m.abstentions += 1
            self.consec_abstain += 1
            if self.c.live_adapt and target is not None:
                self.tracker.add(n, "abstain")
                self.since_switch += 1
            self.t += tm.feedback_s
            if self.consec_abstain >= ABSTAIN_PAUSE_AFTER:
                self.m.caregiver_pauses += 1
                self.t += CAREGIVER_RESUME_S
                self.consec_abstain = 0
            self._maybe_adapt()
            return None
        self.consec_abstain = 0
        gated = self.c.confirm_gate is not None and self.last_ratio >= self.c.confirm_gate
        if gated:
            # No YES/NO was asked, so the system does not learn whether this was right; a
            # wrong gated accept only shows up later as the patient's "Go back".
            self.m.gated_accepts += 1
        if self.c.step_confirm and not gated:
            yes = self._confirm(None if target is None else pred == intended, tm.confirm_attempts)
            if self.c.live_adapt and target is not None:
                self.tracker.add(n, "correct" if yes else "wrong" if yes is False else "abstain", dt - LATENCY_S)
                self.since_switch += 1
            if not yes:
                self.m.rejected_predictions += 1
                self.t += tm.feedback_s
                self._maybe_adapt()
                return None
        self.t += tm.feedback_s
        self.m.accepted_selections += 1
        self.m.n_used[n] = self.m.n_used.get(n, 0) + 1
        slot = slots[pred]
        if slot.contains(BACK_KEY) and len(slot.options) == 1:
            self.m.back_selections += 1
        leaf = nav.select(slot)
        if leaf is None:
            self._maybe_adapt()
            return None
        # final review: always explicit; dynamic decoder retries abstentions, fixed is forced choice
        send = self._confirm(None if target is None else leaf.id == target, None)
        if send:
            self.t += tm.sent_display_s
            nav.home()
            return "SENT:" + leaf.id
        self.t += tm.feedback_s
        nav.leaf_cancelled()
        return None

    # ---- session ---------------------------------------------------------------------
    def run(self) -> Metrics:
        self.consec_abstain = 0
        for (target, urgent), level in zip(self.spec.tasks, self.spec.level_schedule):
            self.level = level
            start = self.t
            self._deadline = start + TASK_TIMEOUT_S
            self.nav.home()
            done = False
            while self.t - start < TASK_TIMEOUT_S:
                r = self._cycle(target)
                if r is None:
                    continue
                if r == "SENT:" + target:
                    self.m.correct_sent += 1
                    done = True
                    break
                self.m.wrong_sent += 1  # a wrong message was confirmed; the need remains
            dur = self.t - start
            self.m.task_time_s += dur
            if not done:
                self.m.timeouts += 1
            self.m.task_times.append(dur)
            if urgent:
                self.m.urgent_times.append(dur if done else None)
        # no-control block: the patient attends nothing
        self.level = self.spec.level_schedule[-1]
        t0 = self.t
        self._deadline = t0 + self.spec.idle_s
        self.nav.home()
        task_m, self.m = self.m, Metrics()
        idle_sent = 0
        while self.t - t0 < self.spec.idle_s:
            if self._cycle(None) is not None:
                idle_sent += 1
        task_m.idle_false_selections = self.m.accepted_selections
        task_m.idle_false_sent = idle_sent
        task_m.idle_s = round(self.t - t0, 1)
        self.m = task_m
        return self.m
