"""Benchmark driver: builds decoder pools, runs every condition on identical sessions, and writes
backend/reports/benchmark_results.json (+ benchmark_summary.md).

Design (all SIMULATED):
  replicate r      -> its own pool seed (new synthetic calibration + test trials) and task seed
  session          = replicate x profile x task mix x signal scenario; every condition runs the
                     SAME tasks, SAME signal schedule and SAME random seed (common random numbers)
  unit of analysis = session; CIs are percentile bootstrap over sessions (paired for differences)
Strongest fixed-menu baseline A* = the fixed n with the highest mean CMPM *chosen in hindsight
on the test results* (an oracle choice that favours the baseline).
"""

from __future__ import annotations

import json
import math
import pickle
import time
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..adaptive import Timing, choose_n, expected_cmpm
from ..config import BACKEND_DIR, DATA_DIR
from ..context import ProfileStore, rank, shortcuts
from ..phrases.catalog import build_catalog
from ..routing import SCENARIO_MESSAGES, SCENARIOS as ROUTING_SCENARIOS, URGENT_ROUTES, lengths, plan_routing, \
    request_distribution
from .pool import Pool, build_pool
from .simulate import Condition, Session, SessionSpec, calib_rel, conditions, fixed_window_for

SCHEMA_VERSION = "1.0"
LEVELS = {"low": 1.0, "medium": 1.4, "high": 2.0}
SCENARIOS = {  # name -> (calibration level, per-task schedule rule)
    "low": ("low", None), "medium": ("medium", None), "high": ("high", None),
    "degrading": ("high", "high->low at half"),
}
PROFILES = ["new-patient", "mobility", "respiratory"]
MIXES = ["matched", "uniform"]
REPORTS_DIR = BACKEND_DIR / "reports"
CACHE_DIR = BACKEND_DIR / "data" / "runtime" / "pool_cache"


# --------------------------------------------------------------------------------------
def get_pools(rep: int, test_per_target: int, test_rest: int) -> dict[str, dict[int, Pool]]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / f"pools_r{rep}_t{test_per_target}_{test_rest}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    pools = {lvl: {n: build_pool(n, lvl, uv, seed=1000 + rep * 17 + int(uv * 10), test_per_target=test_per_target,
                                 test_rest=test_rest) for n in (2, 3, 5)} for lvl, uv in LEVELS.items()}
    f.write_bytes(pickle.dumps(pools))
    return pools


@lru_cache(maxsize=8)
def routing_for(profile: str) -> dict:
    """Routes and expected selections per n for static and context-aware routing."""
    cat = build_catalog()
    store = ProfileStore(DATA_DIR)
    prof = store.load(profile)   # seed profile only: no runtime history, reproducible
    sugg = rank(prof, cat)
    cand = shortcuts(sugg, cat)
    out = {"static": {}, "adaptive": {}, "p": request_distribution(cand, cat)}
    for n in (2, 3, 5):
        st = plan_routing(cat, cand, n, "static", {}, "simulated")
        ad = plan_routing(cat, cand, n, "adaptive", {}, "simulated")
        out["static"][n] = {"routes": ((), ()), "exp_sel": st.expected["selections"]}
        out["adaptive"][n] = {"routes": (tuple(ad.shortcuts), tuple(ad.urgent)), "exp_sel": ad.expected["selections"]}
    return out


def make_tasks(profile: str, mix: str, rep: int, n_tasks: int) -> list[tuple[str, bool]]:
    cat = build_catalog()
    rng = np.random.default_rng([rep, PROFILES.index(profile), MIXES.index(mix), 7])
    leaves = [l.id for l in cat.leaves()]
    if mix == "matched":
        p = routing_for(profile)["p"]
        w = np.array([p[i] for i in leaves])
        picks = list(rng.choice(leaves, size=n_tasks, p=w / w.sum()))
    else:
        picks = list(rng.choice(leaves, size=n_tasks))
    tasks = [(str(x), False) for x in picks]
    for u in URGENT_ROUTES:  # each urgent message once, at a random position
        tasks.insert(int(rng.integers(len(tasks) + 1)), (u, True))
    return tasks


def schedule(scenario: str, n: int) -> list[str]:
    if scenario == "degrading":
        return ["high"] * (n // 2) + ["low"] * (n - n // 2)
    return [scenario] * n


def setup_condition(cond: Condition, cal: dict, win: dict, r: dict, tm: Timing):
    """What a condition knows before the session: routes, expected selections, calibration
    priors (calibration set only) and the initial n."""
    rt = r[cond.routing]
    exp_sel = {n: rt[n]["exp_sel"] for n in (2, 3, 5)}
    routes = {n: rt[n]["routes"] for n in (2, 3, 5)}
    if cond.decoder == "fixed":
        priors = {n: calib_rel(cal[n], "fixed", win[n]) for n in (2, 3, 5)}
    else:
        op = "step" if cond.confirm_gate is None else "strict"
        priors = {n: calib_rel(cal[n], "dynamic", op=op) for n in (2, 3, 5)}
    confirm = calib_rel(cal[2], "dynamic") if cond.decoder == "dynamic" else priors[2]
    gates = {n: tuple(cal[n].calib_gate) for n in (2, 3, 5)} if cond.confirm_gate else None
    if cond.n is None:
        n0 = choose_n(priors, confirm, exp_sel, tm, [2, 3, 5], step_confirm=cond.step_confirm, gates=gates)["n"]
    else:
        n0 = cond.n
    return n0, exp_sel, routes, priors, confirm, gates


MESSAGE_LEVELS = ["low", "medium", "high"]


def run_message_cell(args) -> list[dict]:
    """Single-message sessions for the demo scenario messages (routing.SCENARIO_MESSAGES) under
    every condition: the same message, profile, signal level and seed for each condition."""
    rep, sc_id, level, runs, cfg = args
    profile = next(s["profile"] for s in ROUTING_SCENARIOS if s["id"] == sc_id)
    pools = get_pools(rep, cfg["test_per_target"], cfg["test_rest"])
    cat = build_catalog()
    tm = Timing()
    cal = pools[level]
    win = {n: fixed_window_for(cal[n], tm) for n in (2, 3, 5)}
    windows = {lvl: dict(win) for lvl in pools}
    r = routing_for(profile)
    rows = []
    for mi, (mkey, mid) in enumerate(SCENARIO_MESSAGES.items()):
        for cond in conditions():
            n0, exp_sel, routes, priors, confirm, gates = setup_condition(cond, cal, win, r, tm)
            for k in range(runs):
                seed = int(np.random.SeedSequence([rep, k, mi, MESSAGE_LEVELS.index(level), ord(sc_id), 99])
                           .generate_state(1)[0])
                spec = SessionSpec(profile, [level], [(mid, mid in URGENT_ROUTES)], 0.0, seed, "message")
                m = Session(cond, cat, pools, routes, exp_sel, tm, spec, n0, windows, priors, confirm, gates).run()
                rows.append({"condition": cond.key, "scenario": sc_id, "profile": profile, "message": mkey,
                             "node_id": mid, "snr_level": level, "replicate": rep, "n_start": n0,
                             "selections": m.accepted_selections, "completion_s": m.task_time_s,
                             "correct": m.correct_sent, "wrong": m.wrong_sent, "not_completed": m.timeouts,
                             "rejected": m.rejected_predictions, "abstentions": m.abstentions,
                             "sent_without_final_yes": m.sent_without_final_yes})
    return rows


def aggregate_messages(rows: list[dict]) -> list[dict]:
    """Rows in the shape agreed with the frontend (issue #1, B3)."""
    groups: dict = {}
    for r in rows:
        groups.setdefault((r["condition"], r["scenario"], r["profile"], r["message"], r["snr_level"]), []).append(r)
    out = []
    for (cond, sc, prof, msg, lvl), rs in sorted(groups.items()):
        comp = np.array([x["completion_s"] for x in rs])
        n_starts = [x["n_start"] for x in rs]
        out.append({
            "condition": cond, "scenario": sc, "profile": prof, "message": msg, "node_id": rs[0]["node_id"],
            "snr_level": lvl, "snr_uv": LEVELS[lvl],
            "n_start": max(set(n_starts), key=n_starts.count),
            "n_start_counts": {str(n): n_starts.count(n) for n in sorted(set(n_starts))},
            "runs": len(rs),
            "mean_selections": round(float(np.mean([x["selections"] for x in rs])), 2),
            "mean_completion_s": round(float(comp.mean()), 1),
            "sd_completion_s": round(float(comp.std(ddof=1)), 1) if len(rs) > 1 else None,
            "correct": sum(x["correct"] for x in rs), "wrong": sum(x["wrong"] for x in rs),
            "not_completed": sum(x["not_completed"] for x in rs),
            "mean_rejected": round(float(np.mean([x["rejected"] for x in rs])), 2),
            "mean_abstentions": round(float(np.mean([x["abstentions"] for x in rs])), 2),
            "correct_msgs_per_min": round(60 * sum(x["correct"] for x in rs) / comp.sum(), 3) if comp.sum() else 0.0,
            "final_sent_without_confirm": sum(x["sent_without_final_yes"] for x in rs),
            "origin": "simulated",
        })
    return out


def run_cell(args) -> list[dict]:
    """All conditions for one (replicate, profile, mix, scenario) session. Runs in a worker."""
    rep, profile, mix, scenario, cfg = args
    pools = get_pools(rep, cfg["test_per_target"], cfg["test_rest"])
    cat = build_catalog()
    tm = Timing()
    tasks = make_tasks(profile, mix, rep, cfg["n_tasks"])
    sched = schedule(scenario, len(tasks))
    cal_level = SCENARIOS[scenario][0]
    cal = pools[cal_level]
    win = {n: fixed_window_for(cal[n], tm) for n in (2, 3, 5)}
    windows = {lvl: dict(win) for lvl in pools}
    r = routing_for(profile)
    rows = []
    for cond in conditions():
        n0, exp_sel, routes, priors, confirm, gates = setup_condition(cond, cal, win, r, tm)
        spec = SessionSpec(profile, sched, tasks, cfg["idle_s"], seed=hash((rep, profile, mix, scenario)) % 2**32,
                           mix=mix)
        spec.seed = int(np.random.SeedSequence([rep, PROFILES.index(profile), MIXES.index(mix),
                                                list(SCENARIOS).index(scenario)]).generate_state(1)[0])
        sess = Session(cond, cat, pools, routes, exp_sel, tm, spec, n0, windows, priors, confirm, gates)
        m = sess.run().to_dict()
        pred = expected_cmpm(n0, priors[n0], confirm, exp_sel[n0], tm, step_confirm=cond.step_confirm,
                             gate=(gates or {}).get(n0))
        rows.append({"replicate": rep, "profile": profile, "mix": mix, "scenario": scenario, "condition": cond.key,
                     "initial_n": n0, "fixed_window_s": win[n0] if cond.decoder == "fixed" else None,
                     "model_predicted_cmpm": pred["cmpm"], **m})
    return rows


# --------------------------------------------------------------------------------------
def boot_ci(x, rng, iters=2000) -> list[float] | None:
    x = np.asarray([v for v in x if v is not None and not (isinstance(v, float) and math.isnan(v))], dtype=float)
    if x.size < 2:
        return None
    bs = rng.choice(x, size=(iters, x.size)).mean(axis=1)
    return [round(float(np.percentile(bs, 2.5)), 4), round(float(np.percentile(bs, 97.5)), 4)]


def summarize(rows: list[dict], seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    out: dict = {"by_cell": [], "comparisons": [], "pooled": []}
    keys = sorted({(r["scenario"], r["mix"]) for r in rows}, key=lambda k: (list(SCENARIOS).index(k[0]), k[1]))
    conds = [c.key for c in conditions()]

    def agg(rs: list[dict]) -> dict:
        cm = [r["cmpm"] for r in rs]
        sent = sum(r["correct_sent"] + r["wrong_sent"] for r in rs)
        idle_min = sum(r["idle_s"] for r in rs) / 60
        urgent = [r["urgent_mean_s"] for r in rs if r["urgent_mean_s"] is not None]
        return {
            "sessions": len(rs),
            "cmpm_mean": round(float(np.mean(cm)), 4), "cmpm_sd": round(float(np.std(cm, ddof=1)), 4) if len(cm) > 1 else None,
            "cmpm_ci95": boot_ci(cm, rng),
            "selections_per_correct": round(float(np.mean([r["selections_per_correct"] for r in rs
                                                           if r["selections_per_correct"] is not None] or [np.nan])), 2),
            "mean_task_s": round(float(np.mean([r["mean_task_s"] for r in rs])), 1),
            "error_rate": round(sum(r["wrong_sent"] for r in rs) / sent, 4) if sent else None,
            "false_confirmations_task": sum(r["wrong_sent"] for r in rs),
            "messages_sent": sent,
            "timeouts": sum(r["timeouts"] for r in rs),
            "idle_false_selections_per_min": round(sum(r["idle_false_selections"] for r in rs) / idle_min, 3) if idle_min else None,
            "idle_false_sent_per_hour": round(60 * sum(r["idle_false_sent"] for r in rs) / idle_min, 2) if idle_min else None,
            "urgent_mean_s": round(float(np.mean(urgent)), 1) if urgent else None,
            "urgent_completed": f"{sum(int(r['urgent_completed'].split('/')[0]) for r in rs)}/"
                                f"{sum(int(r['urgent_completed'].split('/')[1]) for r in rs)}",
            "caregiver_pauses": sum(r["caregiver_pauses"] for r in rs),
            "model_predicted_cmpm_mean": round(float(np.mean([r["model_predicted_cmpm"] for r in rs])), 4),
            "initial_n_counts": {str(k): sum(1 for r in rs if r["initial_n"] == k) for k in (2, 3, 5)},
            "sessions_with_n_switch": sum(1 for r in rs if r["n_switches"]),
        }

    def paired(a_rows: dict, b_rows: dict) -> dict:
        common = sorted(set(a_rows) & set(b_rows))
        d = np.array([b_rows[k]["cmpm"] - a_rows[k]["cmpm"] for k in common])
        ratio = np.array([b_rows[k]["cmpm"] / a_rows[k]["cmpm"] for k in common if a_rows[k]["cmpm"] > 0])
        return {"pairs": len(common), "mean_diff_cmpm": round(float(d.mean()), 4), "diff_ci95": boot_ci(d, rng),
                "mean_ratio": round(float(ratio.mean()), 3) if ratio.size else None,
                "ratio_ci95": boot_ci(ratio, rng), "fraction_better": round(float((d > 0).mean()), 3)}

    def sess_key(r):
        return (r["replicate"], r["profile"], r["mix"], r["scenario"])

    for sc, mix in keys:
        cell = [r for r in rows if r["scenario"] == sc and r["mix"] == mix]
        per = {c: [r for r in cell if r["condition"] == c] for c in conds}
        stats = {c: agg(per[c]) for c in conds if per[c]}
        a_star = max(["A2", "A3", "A5"], key=lambda c: stats[c]["cmpm_mean"])
        b_star = max(["B2", "B3", "B5"], key=lambda c: stats[c]["cmpm_mean"])
        out["by_cell"].append({"scenario": sc, "mix": mix, "a_star": a_star, "b_star": b_star, "conditions": stats})
        idx = {c: {sess_key(r): r for r in per[c]} for c in conds}
        for other in (b_star, "C", "D", "D+"):
            out["comparisons"].append({"scenario": sc, "mix": mix, "baseline": a_star, "condition": other,
                                       **paired(idx[a_star], idx[other])})
        out["comparisons"].append({"scenario": sc, "mix": mix, "baseline": "C", "condition": "D",
                                   **paired(idx["C"], idx["D"])})
    for c in conds:
        out["pooled"].append({"condition": c, **agg([r for r in rows if r["condition"] == c])})
    return out


def urgent_accessibility(profiles=PROFILES) -> list[dict]:
    """Exact selections from Home for each urgent message (no decoder involved)."""
    cat = build_catalog()
    rows = []
    for prof in profiles:
        r = routing_for(prof)
        for n in (2, 3, 5):
            st = lengths(cat, n, (), ())
            sc, ur = r["adaptive"][n]["routes"]
            ad = lengths(cat, n, sc, ur)
            for u in URGENT_ROUTES:
                rows.append({"profile": prof, "n": n, "message": u, "static_selections": st[u],
                             "context_selections": ad[u]})
    return rows


def run_benchmark(replicates: int = 5, n_tasks: int = 14, idle_s: float = 180.0, test_per_target: int = 40,
                  test_rest: int = 60, workers: int = 4, scenarios=None, out_dir: Path | None = None,
                  log=print, message_runs: int = 2) -> dict:
    t0 = time.time()
    cfg = {"n_tasks": n_tasks, "idle_s": idle_s, "test_per_target": test_per_target, "test_rest": test_rest}
    scenarios = list(scenarios or SCENARIOS)
    log(f"Building decoder pools for {replicates} replicate(s) (cached under {CACHE_DIR}) ...")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        list(ex.map(get_pools, range(replicates), [test_per_target] * replicates, [test_rest] * replicates))
        pool_summaries = [{"replicate": r, "pools": [p.summary() for lvl in get_pools(r, test_per_target, test_rest).values()
                                                    for p in lvl.values()]} for r in range(replicates)]
        cells = [(rep, prof, mix, sc, cfg) for rep in range(replicates) for prof in PROFILES for mix in MIXES
                 for sc in scenarios]
        log(f"Running {len(cells)} sessions x {len(conditions())} conditions ...")
        rows = [row for rs in ex.map(run_cell, cells) for row in rs]
        mcells = [(rep, s["id"], lvl, message_runs, cfg) for rep in range(replicates) for s in ROUTING_SCENARIOS
                  for lvl in MESSAGE_LEVELS]
        log(f"Running per-message experiment: {len(mcells)} cells x {len(SCENARIO_MESSAGES)} messages x "
            f"{len(conditions())} conditions x {message_runs} runs ...")
        mrows = [row for rs in ex.map(run_message_cell, mcells) for row in rs]
    summary = summarize(rows)
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": "simulated",
        "data_origin": "simulated",
        "real_eeg": False,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "runtime_s": round(time.time() - t0, 1),
        "command": (f"python scripts/run_benchmark.py --replicates {replicates} --tasks {n_tasks} --idle {idle_s:.0f} "
                    f"--test-per-target {test_per_target} --test-rest {test_rest}"
                    + (f" --scenarios {','.join(scenarios)}" if scenarios != list(SCENARIOS) else "")
                    + f" --message-runs {message_runs}"),
        "research_question": ("Can context-aware, risk-aware, signal-adaptive navigation increase correctly confirmed "
                              "medical messages per minute compared with conventional fixed-menu EEG communication?"),
        "disclaimer": ("SIMULATION ONLY. Decoder outcomes come from the real FBCCA code run on SYNTHETIC EEG; the patient "
                       "is a perfect-intent simulated agent; profiles are synthetic. These are model estimates for "
                       "comparing strategies, not empirical or clinical results."),
        "config": cfg | {"levels_ssvep_uv": LEVELS, "scenarios": {k: v[1] or "constant" for k, v in SCENARIOS.items()},
                         "profiles": PROFILES, "mixes": {"matched": "tasks drawn from the profile's request distribution "
                                                         "(60% profile signals + 40% uniform)",
                                                         "uniform": "tasks uniform over all 1,474 phrases (context gives no help)"},
                         "urgent_probes_per_session": len(URGENT_ROUTES), "timing": Timing().__dict__,
                         "task_timeout_s": 300.0},
        "conditions": [c.__dict__ for c in conditions()],
        "metrics_definitions": {
            "cmpm": "correctly confirmed messages per minute of task time (primary outcome)",
            "selections_per_correct": "accepted neural selections per correctly confirmed message",
            "error_rate": "wrong messages confirmed / all messages confirmed during tasks",
            "false_confirmations_task": "wrong messages confirmed during tasks (count)",
            "idle_false_selections_per_min": "selections accepted while the patient attended nothing",
            "idle_false_sent_per_hour": "messages sent while the patient attended nothing",
            "urgent_mean_s": "mean time to correctly confirm an urgent message (completed probes)",
        },
        "summary": summary,
        "urgent_accessibility": urgent_accessibility(),
        "per_message": {
            "description": ("Single-message sessions for the demo scenario messages: same profile, message, signal "
                            "level and seed for every condition. runs = replicates x message_runs."),
            "messages": SCENARIO_MESSAGES, "snr_levels_uv": {k: LEVELS[k] for k in MESSAGE_LEVELS},
            "final_sent_without_confirm_note": ("Simulator protocol counter (a message can only be sent after a "
                                                "decoded final YES); the live engine enforces and reports the same "
                                                "invariant in state.safety_invariants."),
            "rows": aggregate_messages(mrows),
        },
        "decoder_pools": pool_summaries,
        "sessions": rows,
        "limitations": LIMITATIONS,
    }
    out_dir = out_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "benchmark_results.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    (out_dir / "benchmark_summary.md").write_text(to_markdown(result), encoding="utf-8")
    log(f"Done in {result['runtime_s']} s -> {out_dir / 'benchmark_results.json'}")
    return result


LIMITATIONS = [
    "Synthetic EEG: SNR levels are simulator settings, not measurements from people; real SSVEP varies with "
    "the person, display, electrode contact and fatigue.",
    "Perfect-intent patient model: no attention lapses, no changes of mind; only decoder errors occur.",
    "The fixed-menu baselines A/B/C decode with conventional fixed-window forced choice and confirm only the final "
    "message; D uses dynamic stopping, abstention and per-step confirmation. Part of D's difference therefore comes "
    "from the decoder protocol, not only navigation; the per-condition rows separate the contributions.",
    "A* is chosen in hindsight on the test results (oracle), which favours the baseline.",
    "Tasks in the 'matched' mix come from the same request distribution the context router optimises for; the "
    "'uniform' mix shows the cost when context does not match the patient's need.",
    "Calibration estimates (used by C and D to pick n) come from 5 trials per target, as in the live protocol, "
    "and are optimistic in-sample; test outcomes are held out.",
    "No real-EEG results are included. Real results must come from REAL-mode sessions and are reported separately.",
]


def to_markdown(res: dict) -> str:
    s = res["summary"]
    L = [f"# NeuroSteno benchmark (SIMULATED) · {res['generated_at']}", "",
         f"> {res['disclaimer']}", "", f"Command: `{res['command']}` · runtime {res['runtime_s']} s", "",
         f"**Question:** {res['research_question']}", "",
         "Primary outcome: **CMPM** = correctly confirmed messages per minute (mean, 95% bootstrap CI over sessions).", ""]
    for cell in s["by_cell"]:
        L.append(f"## Scenario `{cell['scenario']}`, task mix `{cell['mix']}` (A* = {cell['a_star']}, B* = {cell['b_star']})")
        L.append("")
        L.append("| Condition | CMPM | 95% CI | sel/correct | task s | error rate | idle false sel/min | idle false sent/h | urgent s | n chosen |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for k, v in cell["conditions"].items():
            n_ch = ",".join(f"{n}:{c}" for n, c in v["initial_n_counts"].items() if c)
            L.append(f"| {k} | {v['cmpm_mean']:.3f} | {v['cmpm_ci95']} | {v['selections_per_correct']} | {v['mean_task_s']} | "
                     f"{v['error_rate']} | {v['idle_false_selections_per_min']} | {v['idle_false_sent_per_hour']} | "
                     f"{v['urgent_mean_s']} | {n_ch} |")
        L.append("")
        for c in [c for c in s["comparisons"] if c["scenario"] == cell["scenario"] and c["mix"] == cell["mix"]]:
            L.append(f"- {c['condition']} vs {c['baseline']}: ΔCMPM {c['mean_diff_cmpm']:+.3f} {c['diff_ci95']}, "
                     f"ratio {c['mean_ratio']} {c['ratio_ci95']}, better in {c['fraction_better']:.0%} of {c['pairs']} paired sessions")
        L.append("")
    pm = [r for r in res.get("per_message", {}).get("rows", []) if r["snr_level"] == "medium"]
    if pm:
        conds = [c["key"] for c in res["conditions"]]
        L.append("## Demo scenario messages, medium SNR: mean selections / mean completion s (runs per cell)")
        L.append("")
        L.append("| Scenario | Message | " + " | ".join(conds) + " |")
        L.append("|---|---|" + "---|" * len(conds))
        for sc in sorted({r["scenario"] for r in pm}):
            for msg in sorted({r["message"] for r in pm}):
                cells = {r["condition"]: r for r in pm if r["scenario"] == sc and r["message"] == msg}
                L.append(f"| {sc} | {msg} | " + " | ".join(
                    f"{cells[c]['mean_selections']} / {cells[c]['mean_completion_s']}" if c in cells else "-"
                    for c in conds) + " |")
        L.append("")
    L.append("## Limitations")
    L += [f"- {x}" for x in res["limitations"]]
    return "\n".join(L) + "\n"
