"""Risk-aware semantic routing: a deterministic, explainable routing policy.

Hypothesis under test (NOT an established result): end-to-end EEG communication can be made
faster without losing accuracy by jointly considering
  1. patient-specific request probabilities,
  2. the number of neural selections each request needs,
  3. measured decoder reliability and selection time,
  4. error-recovery and confirmation cost,
  5. urgency-sensitive access to important requests.

Policy (no learned model; every number below is shown in the debug panel):
  * Request distribution p(phrase) = 60 % profile signals (pins, preferences, confirmed
    history, recent requests, session context) spread over each signal's subtree, plus 40 %
    uniform over ALL phrases, so no phrase is ever treated as impossible.
  * Selections per phrase are measured exactly by walking the real navigator.
  * Expected cost  E[time] = E[selections] x T_selection + T_final_review, where
        T_selection = (rest + T_choose/(1 - abstain) + T_confirm + feedback) / accuracy
    from calibration on the current data origin, or explicitly-labelled assumptions.
  * Shortcut candidates (ranked by context.py) are added greedily ONLY if they lower E[time]
    over the whole distribution: a shortcut costs every other request on that screen a slot.
  * Urgent routes are fixed and always shown, independent of history.
  * Verification ("auto" policy): provisional correction windows only when calibration on
    this data origin shows >= 95 % accepted-trial accuracy and <= 5 % rest false accepts;
    otherwise explicit confirmation. The final message is always explicitly confirmed.
  * Context only changes what is offered and where. It never changes decoder output and never
    creates or confirms a message.

Baseline: "static" routing = the plain catalog menu (no suggestions, no urgent group).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from functools import lru_cache

from .context import Suggestion
from .navigation import Navigator
from .phrases.catalog import Catalog

# Curated urgent routes: always one selection inside their category (adaptive routing).
URGENT_ROUTES = [
    "breathing/short-of-breath/now",
    "breathing/not-enough-air/now",
    "breathing/suction-mouth/now",
    "food-water/mouth/choking",
    "call-caregiver/nurse/come-now",
    "pain/chest-back/chest/nurse/severe",
]

PROFILE_MASS = 0.6

# Messages used in demo scenarios A/B/C (same messages across profiles for a fair comparison).
SCENARIO_MESSAGES = {
    "turn-left": "comfort/position/turn-left",
    "left-leg-reposition": "pain/legs-feet/left-leg/reposition/skip",
    "suction-now": "breathing/suction-mouth/now",
    "water-sips": "food-water/drink/water/sips",
}
SCENARIOS = [
    {"id": "A", "profile": "new-patient", "title": "New patient, no history"},
    {"id": "B", "profile": "mobility", "title": "Mobility patient, frequent repositioning"},
    {"id": "C", "profile": "respiratory", "title": "Respiratory patient, breathing requests"},
]


# -------------------------------------------------------------------------------------
def selections_to(catalog: Catalog, target_id: str, n: int, shortcuts: tuple[str, ...] = (),
                  urgent: tuple[str, ...] = ()) -> int:
    """Exact number of accepted neural selections from Home to the target phrase (excluding
    the confirmation step and the final review), following the navigator's shortest route."""
    nav = Navigator(catalog, list(shortcuts), list(urgent))
    nav.track_undo = False
    for count in range(1, 40):
        slots = nav.slots(n)
        leaf = nav.select(slots[nav.slot_toward(slots, target_id)])
        if leaf is not None:
            if leaf.id != target_id:
                raise RuntimeError(f"routing reached {leaf.id} instead of {target_id}")
            return count
    raise RuntimeError(f"no route to {target_id}")


@lru_cache(maxsize=64)
def _static_lengths(catalog_id: int, n: int) -> dict[str, int]:
    cat = _CATALOGS[catalog_id]
    return {leaf.id: selections_to(cat, leaf.id, n) for leaf in cat.leaves()}


_CATALOGS: dict[int, Catalog] = {}


def lengths(catalog: Catalog, n: int, shortcuts: tuple[str, ...], urgent: tuple[str, ...]) -> dict[str, int]:
    """Selections for every phrase (cached)."""
    _CATALOGS[id(catalog)] = catalog
    return _lengths_cached(id(catalog), n, tuple(shortcuts), tuple(urgent))


@lru_cache(maxsize=512)
def _lengths_cached(catalog_id: int, n: int, shortcuts: tuple[str, ...], urgent: tuple[str, ...]) -> dict[str, int]:
    """Only categories touched by shortcuts/urgent routes are recomputed; the rest reuse the
    cached static lengths (identical by construction)."""
    catalog = _CATALOGS[catalog_id]
    base = dict(_static_lengths(id(catalog), n))
    touched = {catalog.get(x).path()[1].id for x in shortcuts + urgent}
    if not touched:
        return base
    for leaf in catalog.leaves():
        if leaf.path()[1].id in touched:
            base[leaf.id] = selections_to(catalog, leaf.id, n, shortcuts, urgent)
    return base


def request_distribution(suggestions: list[Suggestion], catalog: Catalog) -> dict[str, float]:
    leaves = catalog.leaves()
    has_profile = bool(suggestions)
    uni = (1 - PROFILE_MASS if has_profile else 1.0) / len(leaves)
    p = {leaf.id: uni for leaf in leaves}
    if has_profile:
        total = sum(s.score for s in suggestions)
        for s in suggestions:
            node = catalog.get(s.node_id)
            sub = [x for x in _subtree_leaves(node)]
            for leaf in sub:
                p[leaf.id] += PROFILE_MASS * (s.score / total) / len(sub)
    return p


def _subtree_leaves(node):
    if node.is_leaf:
        return [node]
    out = []
    for c in node.children:
        out += _subtree_leaves(c)
    return out


# -------------------------------------------------------------------------------------
@dataclass
class Reliability:
    n: int
    accuracy: float
    abstain: float
    decision_s: float
    source: str  # "calibrated (<origin>)" or "assumed"
    rest_false_accept: float | None = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


ASSUMED = {2: (0.85, 0.15, 2.5), 3: (0.8, 0.2, 3.0), 5: (0.75, 0.25, 3.5)}


def reliability_for(n: int, calib_results: dict, origin: str) -> Reliability:
    res = calib_results.get(f"ssvep_{n}")
    if res and res.get("origin") == origin:
        est = res["dynamic_stopping_estimate"]
        if est.get("accuracy_of_accepted") is not None:
            return Reliability(n, est["accuracy_of_accepted"], est["abstention_rate"],
                               est.get("mean_decision_s") or ASSUMED[n][2], f"calibrated ({origin} data)",
                               est.get("rest_false_accept_rate"))
    acc, ab, t = ASSUMED[n]
    return Reliability(n, acc, ab, t, "assumed (no calibration for this data origin)")


def selection_time(rel_n: Reliability, rel_2: Reliability, rest_s: float, feedback_s: float) -> float:
    acc = max(0.05, rel_n.accuracy)
    choose = rel_n.decision_s / max(0.05, 1 - rel_n.abstain)
    confirm = rel_2.decision_s / max(0.05, 1 - rel_2.abstain)
    return (rest_s + choose + confirm + feedback_s) / acc


@dataclass
class RoutingPlan:
    mode: str
    n: int
    shortcuts: list[str] = field(default_factory=list)
    urgent: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    expected: dict = field(default_factory=dict)
    reliability: dict = field(default_factory=dict)
    verification: dict = field(default_factory=dict)
    recommended_n: dict = field(default_factory=dict)
    top_requests: list[dict] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)
    computed_ms: float = 0.0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def plan_routing(catalog: Catalog, suggestions: list[Suggestion], n: int, mode: str, calib_results: dict,
                 origin: str, rest_s: float = 1.0, feedback_s: float = 1.0, allowed_n: list[int] | None = None,
                 policy: str = "explicit") -> RoutingPlan:
    t0 = time.perf_counter()
    allowed_n = allowed_n or [2, 3, 5]
    p = request_distribution(suggestions, catalog)
    rel = {k: reliability_for(k, calib_results, origin) for k in (2, 3, 5)}

    def t_sel(k: int) -> float:
        return selection_time(rel[k], rel[2], rest_s, feedback_s)

    t_review = rel[2].decision_s / max(0.05, 1 - rel[2].abstain)

    def cost(k: int, sc: tuple, ur: tuple) -> tuple[float, float]:
        L = lengths(catalog, k, sc, ur)
        es = sum(p[i] * L[i] for i in p)
        return es, es * t_sel(k) + t_review

    plan = RoutingPlan(mode=mode, n=n, reliability={k: r.to_dict() for k, r in rel.items()})
    static_es, static_t = cost(n, (), ())
    plan.explanation.append(
        f"Request distribution: {'60% profile signals + 40% uniform over all phrases' if suggestions else 'uniform (no profile signals)'}"
        f" across {len(p)} phrases.")
    if mode == "static":
        plan.expected = {"static_selections": round(static_es, 3), "static_time_s": round(static_t, 1),
                         "selections": round(static_es, 3), "time_s": round(static_t, 1)}
        plan.explanation.append("STATIC baseline: plain catalog menu, no suggestions and no urgent group.")
    else:
        ur = tuple(URGENT_ROUTES)
        plan.urgent = list(ur)
        es_u, t_u = cost(n, (), ur)
        plan.explanation.append(f"Urgent routes ({len(ur)}) are always included regardless of history "
                                f"(E[selections] {static_es:.2f} -> {es_u:.2f}).")
        chosen: list[str] = []
        cur_es, cur_t = es_u, t_u
        for s in suggestions:
            if s.score < 2.0 or len(chosen) >= 4:
                break
            if s.node_id in ur or any(s.node_id.startswith(c + "/") or c.startswith(s.node_id + "/") for c in chosen):
                plan.candidates.append({"node_id": s.node_id, "score": round(s.score, 2), "reasons": s.reasons,
                                        "included": False, "why": "covered by an urgent route or another shortcut"})
                continue
            es2, t2 = cost(n, tuple(chosen + [s.node_id]), ur)
            keep = t2 < cur_t - 1e-6
            plan.candidates.append({
                "node_id": s.node_id, "title": catalog.get(s.node_id).title, "score": round(s.score, 2),
                "reasons": s.reasons, "included": keep,
                "delta_selections": round(es2 - cur_es, 4), "delta_time_s": round(t2 - cur_t, 3),
                "why": ("lowers expected time over the whole request distribution" if keep else
                        "would cost other requests on that screen more than it saves"),
            })
            if keep:
                chosen.append(s.node_id)
                cur_es, cur_t = es2, t2
        plan.shortcuts = chosen
        plan.expected = {"static_selections": round(static_es, 3), "static_time_s": round(static_t, 1),
                         "selections": round(cur_es, 3), "time_s": round(cur_t, 1)}
    # Target-count recommendation from measured/assumed reliability.
    rec = {}
    for k in allowed_n:
        es_k, t_k = cost(k, tuple(plan.shortcuts), tuple(plan.urgent))
        rec[k] = {"expected_selections": round(es_k, 3), "expected_time_s": round(t_k, 1),
                  "selection_time_s": round(t_sel(k), 2), "reliability_source": rel[k].source}
    best = min(rec, key=lambda k: rec[k]["expected_time_s"])
    plan.recommended_n = {"n": best, "by_n": rec,
                          "note": "Model-based estimate from the formula above; not a measured result."}
    # Verification behaviour from measured reliability.
    r = rel[n]
    measured = r.source.startswith("calibrated")
    ok_prov = measured and r.accuracy >= 0.95 and (r.rest_false_accept or 0) <= 0.05
    if policy == "auto":
        chosen_policy = "provisional" if ok_prov else "explicit"
        why = (f"calibrated accuracy {r.accuracy:.0%} >= 95% and rest false-accepts <= 5%" if ok_prov else
               ("no calibration on this data origin" if not measured else
                f"calibrated accuracy {r.accuracy:.0%} / rest false-accepts {r.rest_false_accept} below the bar"))
    else:
        chosen_policy, why = policy, "set by operator"
    plan.verification = {"policy": chosen_policy, "requested": policy, "reason": why,
                         "final_message": "always explicit confirmation"}
    top = sorted(p.items(), key=lambda kv: -kv[1])[:8]
    plan.top_requests = [{"node_id": k, "title": catalog.get(k).title, "p": round(v, 4)} for k, v in top]
    plan.computed_ms = round((time.perf_counter() - t0) * 1000, 1)
    return plan


def route_path(catalog: Catalog, target_id: str, n: int, shortcuts: tuple[str, ...] = (),
               urgent: tuple[str, ...] = ()) -> list[dict]:
    """The exact selections (slot label + kind) the navigator's shortest route takes."""
    nav = Navigator(catalog, list(shortcuts), list(urgent))
    nav.track_undo = False
    out = []
    for _ in range(40):
        slots = nav.slots(n)
        slot = slots[nav.slot_toward(slots, target_id)]
        if slot.kind == "group":
            kind = "group"
        else:
            o = slot.options[0]
            kind = ("back" if o.kind == "back" else "urgent_route" if o.key.startswith("ur:") else
                    "shortcut" if o.key.startswith("sc:") else "option")
        out.append({"label": slot.label, "kind": kind})
        if nav.select(slot) is not None:
            return out
    raise RuntimeError(f"no route to {target_id}")


def compare_scenarios(catalog: Catalog, store, ns=(2, 3, 5)) -> list[dict]:
    """Exact selection counts for the scenario messages: static menu vs. risk-aware routing.
    Uses the SEED profiles only (no runtime history), so the numbers are stable during a demo.
    Time estimates use the labelled assumptions in ASSUMED (no calibration involved)."""
    from .context import rank, shortcuts as pick

    rel = {k: reliability_for(k, {}, "none") for k in (2, 3, 5)}
    t_review = rel[2].decision_s / max(0.05, 1 - rel[2].abstain)
    rows = []
    for sc in SCENARIOS:
        profile = store.load(sc["profile"])  # seed only: no origin -> no runtime history
        sugg = rank(profile, catalog)
        cand = pick(sugg, catalog)
        for n in ns:
            plan = plan_routing(catalog, cand, n, "adaptive", {}, "simulated")
            for mkey, mid in SCENARIO_MESSAGES.items():
                s_static = selections_to(catalog, mid, n)
                s_adapt = selections_to(catalog, mid, n, tuple(plan.shortcuts), tuple(plan.urgent))
                t_sel = selection_time(rel[n], rel[2], 1.0, 1.0)
                rows.append({"scenario": sc["id"], "profile": sc["profile"], "n": n, "message": mkey,
                             "text": catalog.get(mid).phrase, "static": s_static, "adaptive": s_adapt,
                             "delta": s_adapt - s_static, "shortcuts": plan.shortcuts,
                             "path_static": route_path(catalog, mid, n),
                             "path_adaptive": route_path(catalog, mid, n, tuple(plan.shortcuts), tuple(plan.urgent)),
                             "est_time_static_s": round(s_static * t_sel + t_review, 1),
                             "est_time_adaptive_s": round(s_adapt * t_sel + t_review, 1),
                             "time_source": "assumption", "selection_time_s": round(t_sel, 2),
                             "profile_source": "seed"})
    return rows
