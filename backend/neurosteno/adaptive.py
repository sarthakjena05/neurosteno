"""Signal-adaptive target count: choose n in {2, 3, 5} to maximise the expected number of
CORRECTLY CONFIRMED messages per minute (not the shortest menu path).

Fewer targets are not assumed to be better. More targets mean fewer selections per message
(measured exactly with the navigator for the current routing) but each selection is slower and
less accurate. This module puts both sides in one explicit model:

Per selection cycle (explicit per-step YES/NO confirmation, the live product behaviour):
    a  = abstention rate of the choose step          t  = mean decision time (accepted)
    p  = accuracy of accepted choose decisions        T  = max window (time spent on an abstention)
    confirm step (2 targets): accuracy q, abstention b, decision time tc, up to 2 attempts
    P(YES | right highlight) = q(1-b)(1+b),  P(YES | wrong highlight) = (1-q)(1-b)(1+b)
    forward f = (1-a) p P(YES|right),  wrong-accept w = (1-a)(1-p) P(YES|wrong)
    A wrong acceptance costs one extra "Go back" step, so progress is a random walk with drift
    f - w; expected cycles per net step = 1 / (f - w).
Without per-step confirmation (fixed-window baselines) f = p, w = 1 - p.

    E[time / message] = E[selections_n] * cycles_per_step * cycle_time + review + sent_display
    P(correct | sent) from the final-review confirmation accuracy
    CMPM = 60 * P(correct | sent) / E[time / message]

Inputs are calibration estimates on the current data origin, updated online from the
patient's own YES/NO answers (a YES on a highlight means the prediction was right). Online
degradation measured on the current n is transferred to the other n by a shift in log-odds
(the same signal drives all target counts). This is a model, and its predictions are checked
against Monte-Carlo simulation in the benchmark report; it is not a measured result.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

LATENCY_S = 0.14


@dataclass
class Rel:
    n: int
    accuracy: float          # accuracy of accepted decisions
    abstain: float           # abstention probability per attempt
    decision_s: float        # mean decision window of accepted decisions
    max_window_s: float = 3.76
    source: str = "assumed"
    n_evidence: float = 0.0  # trials behind the estimate

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


@dataclass
class Timing:
    rest_s: float = 1.0
    feedback_s: float = 1.0
    sent_display_s: float = 4.0
    confirm_attempts: int = 2


def _logit(p: float) -> float:
    p = min(max(p, 1e-3), 1 - 1e-3)
    return math.log(p / (1 - p))


def _sig(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def chance(n: int) -> float:
    return 1.0 / n


def expected_cmpm(n: int, rel: Rel, confirm: Rel, exp_selections: float, timing: Timing,
                  step_confirm: bool = True, gate: tuple[float, float] | None = None) -> dict:
    """Expected correctly confirmed messages per minute for target count n."""
    a, p, t, T = rel.abstain, rel.accuracy, rel.decision_s, rel.max_window_s
    q, b, tc = confirm.accuracy, confirm.abstain, confirm.decision_s
    tm = timing
    if step_confirm:
        k = tm.confirm_attempts
        decide = 1 - b ** k                          # P(confirm decides within k attempts)
        yes_right, yes_wrong = q * decide, (1 - q) * decide
        conf_time = sum(b ** i * (LATENCY_S + ((1 - b) * tc + b * T)) for i in range(k))
        # gate = (fraction of accepted decisions above the high-confidence bar, their accuracy)
        g, pg = gate if gate else (0.0, 1.0)
        p_rest = (p - g * pg) / (1 - g) if g < 1 else p
        p_rest = min(max(p_rest, 0.0), 1.0)
        f = (1 - a) * (g * pg + (1 - g) * p_rest * yes_right)
        w = (1 - a) * (g * (1 - pg) + (1 - g) * (1 - p_rest) * yes_wrong)
        cycle = (a * (tm.rest_s + LATENCY_S + T + tm.feedback_s)
                 + (1 - a) * (tm.rest_s + LATENCY_S + t + (1 - g) * conf_time + tm.feedback_s))
    else:
        f, w = (1 - a) * p, (1 - a) * (1 - p)
        cycle = (a * (tm.rest_s + LATENCY_S + T + tm.feedback_s)
                 + (1 - a) * (tm.rest_s + LATENCY_S + t + tm.feedback_s))
    drift = f - w
    # Final review: repeated until decided; abstentions cost a max window each.
    review_try = LATENCY_S + (1 - b) * tc + b * T
    review_time = review_try / max(1e-3, 1 - b)
    p_correct_sent = q  # review decides YES correctly on the right leaf; wrong leaves are sent w.p. 1-q
    if drift <= 0.02:
        return {"n": n, "cmpm": 0.0, "time_per_message_s": math.inf, "cycles_per_step": math.inf,
                "cycle_s": round(cycle, 2), "p_correct_given_sent": round(p_correct_sent, 3),
                "note": "decoder too unreliable at this target count: no net progress"}
    cps = 1 / drift
    t_msg = exp_selections * cps * cycle + review_time + tm.feedback_s + tm.sent_display_s
    return {"n": n, "cmpm": round(60 * p_correct_sent / t_msg, 3), "time_per_message_s": round(t_msg, 1),
            "cycles_per_step": round(cps, 3), "cycle_s": round(cycle, 2),
            "expected_selections": round(exp_selections, 3), "p_correct_given_sent": round(p_correct_sent, 3)}


def choose_n(rels: dict[int, Rel], confirm: Rel, exp_selections: dict[int, float], timing: Timing,
             allowed: list[int], step_confirm: bool = True, current: int | None = None,
             hysteresis: float = 0.20, gates: dict | None = None) -> dict:
    """Pick the allowed n with the highest expected CMPM; keep the current n unless another is
    at least `hysteresis` better (avoids flapping on noisy estimates)."""
    by_n = {k: expected_cmpm(k, rels[k], confirm, exp_selections[k], timing, step_confirm,
                             (gates or {}).get(k))
            for k in allowed if k in rels and k in exp_selections}
    best = max(by_n, key=lambda k: by_n[k]["cmpm"])
    chosen, why = best, "highest expected correctly-confirmed messages per minute"
    if current in by_n and best != current:
        cur, new = by_n[current]["cmpm"], by_n[best]["cmpm"]
        if new < cur * (1 + hysteresis):
            chosen, why = current, (f"{best} targets only {(new / max(cur, 1e-9) - 1):+.0%} better "
                                    f"(< {hysteresis:.0%} switch threshold); staying at {current}")
    return {"n": chosen, "best": best, "by_n": by_n, "reason": why,
            "objective": "correctly confirmed messages per minute (model estimate)"}


# --------------------------------------------------------------------------------------
# Online evidence
# --------------------------------------------------------------------------------------
@dataclass
class OnlineTracker:
    """Recent per-n evidence from live use. A YES on a highlighted prediction counts as a correct
    decision and a NO as a wrong one (the confirmation itself can err; accepted)."""
    window: int = 20
    outcomes: dict = field(default_factory=dict)   # n -> deque[(kind, decision_s)]

    def add(self, n: int, kind: str, decision_s: float | None = None) -> None:
        """kind: 'correct' | 'wrong' | 'abstain'."""
        self.outcomes.setdefault(n, deque(maxlen=self.window)).append((kind, decision_s))

    def counts(self, n: int) -> dict:
        d = self.outcomes.get(n, ())
        c = sum(1 for k, _ in d if k == "correct")
        w = sum(1 for k, _ in d if k == "wrong")
        ab = sum(1 for k, _ in d if k == "abstain")
        ts = [t for k, t in d if k in ("correct", "wrong") and t is not None]
        return {"correct": c, "wrong": w, "abstain": ab, "total": len(d),
                "mean_decision_s": (sum(ts) / len(ts)) if ts else None}

    def clear(self) -> None:
        self.outcomes.clear()


def posterior(prior: Rel, cnt: dict, prior_weight: float = 4.0) -> Rel:
    """Beta-binomial update of accuracy and abstention; decision time as a weighted mean."""
    w = min(prior_weight, max(2.0, prior.n_evidence or prior_weight))
    dec = cnt["correct"] + cnt["wrong"]
    acc = (prior.accuracy * w + cnt["correct"]) / (w + dec) if dec else prior.accuracy
    ab = (prior.abstain * w + cnt["abstain"]) / (w + cnt["total"]) if cnt["total"] else prior.abstain
    t = prior.decision_s
    if cnt["mean_decision_s"] is not None:
        t = (prior.decision_s * w + cnt["mean_decision_s"] * dec) / (w + dec)
    src = prior.source if not cnt["total"] else f"{prior.source} + {cnt['total']} live outcomes"
    return Rel(prior.n, acc, ab, t, prior.max_window_s, src, w + cnt["total"])


def _clogit(p: float, lo: float = 0.03) -> float:
    return _logit(min(max(p, lo), 1 - lo))


def transfer(prior_other: Rel, prior_cur: Rel, post_cur: Rel) -> Rel:
    """Apply the degradation (or improvement) measured on the current n to another n.

    The shift is measured in clamped log-odds (calibration estimates of 0 % or 100 % from a few
    trials would otherwise make any change look infinite) and scaled by log2(k)/log2(n_cur):
    an SSVEP decision among more targets needs more information per selection, so the same loss
    of signal costs it more. This scaling is a modelling assumption, stated here and in the
    benchmark report; live evidence at the other n replaces it as soon as it exists."""
    scale = math.log2(prior_other.n) / math.log2(prior_cur.n)
    d_acc = (_clogit(post_cur.accuracy) - _clogit(prior_cur.accuracy)) * scale
    d_ab = (_clogit(post_cur.abstain) - _clogit(prior_cur.abstain)) * scale
    acc = max(chance(prior_other.n), _sig(_clogit(prior_other.accuracy) + d_acc))
    ab = _sig(_clogit(prior_other.abstain) + d_ab)
    ratio = post_cur.decision_s / max(1e-3, prior_cur.decision_s)
    t = min(prior_other.max_window_s, prior_other.decision_s * (1 + (ratio - 1) * scale))
    src = prior_other.source if abs(d_acc) < 1e-9 and abs(d_ab) < 1e-9 else \
        f"{prior_other.source}, shifted by live evidence at n={prior_cur.n}"
    return Rel(prior_other.n, acc, ab, t, prior_other.max_window_s, src, prior_other.n_evidence)


def live_estimates(priors: dict[int, Rel], tracker: OnlineTracker, current: int) -> dict[int, Rel]:
    """Posterior for the current n from live evidence; other n's shifted by the same degradation.
    Any other n with its own recent evidence is updated directly."""
    post_cur = posterior(priors[current], tracker.counts(current))
    out = {}
    for k, pr in priors.items():
        if k == current:
            out[k] = post_cur
        elif tracker.counts(k)["total"] >= 4:
            out[k] = posterior(pr, tracker.counts(k))
        else:
            out[k] = transfer(pr, priors[current], post_cur)
    return out
