"""Signal-adaptive target count, calibration trial gating, gated confirmation, and the
benchmark harness (simulation only)."""

import numpy as np
import pytest

from neurosteno.adaptive import OnlineTracker, Rel, Timing, choose_n, expected_cmpm, live_estimates, transfer
from neurosteno.benchmark.pool import FREQS, build_pool, stopper_replay
from neurosteno.benchmark.run import summarize
from neurosteno.benchmark.simulate import Session, SessionSpec, calib_rel, conditions, fixed_window_for
from neurosteno.engine import Phase
from neurosteno.headless import make_engine, produce_message, run_until
from neurosteno.phrases.catalog import build_catalog
from neurosteno.routing import URGENT_ROUTES, plan_routing
from neurosteno.signal.calibration import Trial, calibrate, trial_quality
from neurosteno.signal.fbcca import DynamicStopper, FBCCAConfig, StoppingConfig, fbcca_scores

TM = Timing()
SEL = {2: 9.0, 3: 6.0, 5: 4.0}   # typical expected selections per message (fewer with more targets)


def rel(n, acc, ab, t=2.0):
    return Rel(n, acc, ab, t, 3.76, "test", 10)


# ---- adaptive model -----------------------------------------------------------------
def test_more_targets_win_when_the_signal_supports_them():
    rels = {n: rel(n, 0.97, 0.03) for n in (2, 3, 5)}
    d = choose_n(rels, rels[2], SEL, TM, [2, 3, 5])
    assert d["n"] == 5 and d["by_n"][5]["cmpm"] > d["by_n"][2]["cmpm"]


def test_fewer_targets_win_when_many_target_decoding_degrades():
    rels = {2: rel(2, 0.97, 0.05), 3: rel(3, 0.85, 0.35, 3.0), 5: rel(5, 0.65, 0.7, 3.5)}
    d = choose_n(rels, rels[2], SEL, TM, [2, 3, 5])
    assert d["n"] in (2, 3) and d["by_n"][5]["cmpm"] < d["by_n"][d["n"]]["cmpm"]


def test_hysteresis_keeps_current_n_for_small_gains():
    rels = {n: rel(n, 0.95, 0.05) for n in (2, 3, 5)}
    sel = {2: 5.0, 3: 4.9, 5: 4.8}
    d = choose_n(rels, rels[2], sel, TM, [2, 3, 5], current=2)
    assert d["n"] == 2 and "switch threshold" in d["reason"]


def test_confirmation_rescues_a_weak_decoder_but_forced_choice_cannot():
    weak = rel(5, 0.3, 0.5)
    without = expected_cmpm(5, weak, rel(2, 0.95, 0.05), 4.0, TM, step_confirm=False)
    with_confirm = expected_cmpm(5, weak, rel(2, 0.95, 0.05), 4.0, TM, step_confirm=True)
    assert without["cmpm"] == 0.0 and with_confirm["cmpm"] > 0.0


def test_degradation_transfers_less_to_fewer_targets():
    pri = {n: rel(n, 0.97, 0.05) for n in (2, 3, 5)}
    post5 = rel(5, 0.7, 0.6)
    t2 = transfer(pri[2], pri[5], post5)
    assert post5.abstain > t2.abstain > pri[2].abstain and post5.accuracy < t2.accuracy < pri[2].accuracy


def test_live_estimates_follow_patient_answers():
    pri = {n: rel(n, 0.97, 0.05) for n in (2, 3, 5)}
    tr = OnlineTracker()
    for _ in range(10):
        tr.add(5, "abstain")
    for _ in range(5):
        tr.add(5, "wrong", 3.0)
    est = live_estimates(pri, tr, 5)
    assert est[5].abstain > 0.4 and est[5].accuracy < 0.6 and "live" in est[5].source
    d = choose_n(est, pri[2], SEL, TM, [2, 3, 5], current=5)
    assert d["n"] != 5


# ---- calibration quality gate --------------------------------------------------------
def _trials(seed=0, n=2, per=4, rest=4):
    from neurosteno.acquisition.simulated import SimulatedSource
    s = SimulatedSource(seed=seed, ssvep_uv=2.5)
    cols = s.eeg_indices(["Pz", "PO7", "Oz", "PO8"])
    out = []
    for lab in [i for i in range(n) for _ in range(per)] + [None] * rest:
        s.set_gaze(None)
        pad = s.generate(125)[:, cols]
        s.set_gaze(None if lab is None else FREQS[n][lab])
        x = s.generate(1000)[:, cols]
        out.append(Trial(x[35:].astype(float), lab, pad.astype(float)))
    return out


def test_artifact_trials_are_rejected_and_reported():
    trials = _trials()
    trials[0].eeg[200:230, 1] += 600.0                 # movement artifact
    trials[1].flags.append("12 dropped samples")       # flagged online by the engine
    assert trial_quality(trials[0], 250.0) and trial_quality(trials[1], 250.0) == ["12 dropped samples"]
    assert trial_quality(trials[2], 250.0) == []
    res = calibrate(trials, FREQS[2], 250.0, FBCCAConfig(), 1.0, 3.5, 0.25)
    assert res["n_rejected"] == 2 and {r["index"] for r in res["rejected_trials"]} == {0, 1}
    assert "rejected for quality" in res["caveat"]


def test_calibration_refuses_when_too_few_usable_trials():
    trials = _trials(per=2)
    trials[0].flags.append("signal problem")
    with pytest.raises(ValueError, match="usable"):
        calibrate(trials, FREQS[2], 250.0, FBCCAConfig(), 1.0, 3.5, 0.25)


# ---- benchmark replay equals the live decoder ------------------------------------------
def test_stopper_replay_matches_dynamic_stopper():
    trials = _trials(seed=3, n=3, per=3, rest=3)
    st = StoppingConfig(min_window_s=1.0, max_window_s=3.5, step_s=0.25)
    fb = FBCCAConfig()
    windows = [round(1.0 + 0.25 * i, 3) for i in range(11)]
    for t in trials:
        ds = DynamicStopper(FREQS[3], 250.0, st, fb)
        live = None
        for w in windows:
            live = ds.evaluate(t.eeg[: int(w * 250)], t.pad)
            if live.reliable or ds.exhausted:
                break
        sc = [fbcca_scores(t.eeg[: int(w * 250)], 250.0, FREQS[3], fb, t.pad) for w in windows]
        tgt, w, _, _ = stopper_replay(sc, windows, st)
        assert tgt == live.target and abs(w - live.window_s) <= 1 / 250 + 1e-9  # same decision; live window is whole samples


# ---- benchmark harness -------------------------------------------------------------------
@pytest.fixture(scope="module")
def small_pools():
    return {"medium": {n: build_pool(n, "medium", 1.6, seed=5, test_per_target=6, test_rest=6) for n in (2, 3, 5)}}


def test_sessions_are_reproducible_and_safe(small_pools):
    cat = build_catalog()
    tasks = [("comfort/position/turn-left", False), (URGENT_ROUTES[2], True)]
    sched = ["medium"] * len(tasks)
    cal = small_pools["medium"]
    win = {n: fixed_window_for(cal[n], TM) for n in (2, 3, 5)}
    out = {}
    for cond in [c for c in conditions() if c.key in ("A5", "D")]:
        plans = {n: plan_routing(cat, [], n, cond.routing, {}, "simulated") for n in (2, 3, 5)}
        routes = {n: (tuple(plans[n].shortcuts), tuple(plans[n].urgent)) for n in (2, 3, 5)}
        sel = {n: plans[n].expected["selections"] for n in (2, 3, 5)}
        pri = {n: calib_rel(cal[n], cond.decoder, win[n]) for n in (2, 3, 5)}
        runs = []
        for _ in range(2):
            spec = SessionSpec("new-patient", sched, tasks, idle_s=30.0, seed=11, mix="uniform")
            runs.append(Session(cond, cat, small_pools, routes, sel, TM, spec, 5,
                                {"medium": win}, pri, pri[2]).run().to_dict())
        assert runs[0] == runs[1]                      # same seed -> identical session
        out[cond.key] = runs[0]
    for m in out.values():
        assert m["correct_sent"] + m["timeouts"] == len(tasks)
        assert m["urgent_completed"].endswith("/1")
    assert out["D"]["idle_false_sent"] == 0             # D never sends while the patient attends nothing


def test_summary_schema():
    rows = []
    for rep in range(2):
        for c in conditions():
            rows.append({"replicate": rep, "profile": "p", "mix": "uniform", "scenario": "medium", "condition": c.key,
                         "initial_n": 5, "cmpm": 1.0 + rep + (0.5 if c.key == "D" else 0), "correct_sent": 5,
                         "wrong_sent": 0, "timeouts": 0, "selections_per_correct": 4.0, "mean_task_s": 30.0,
                         "idle_s": 60.0, "idle_false_selections": 0, "idle_false_sent": 0, "urgent_mean_s": 20.0,
                         "urgent_completed": "1/1", "caregiver_pauses": 0, "model_predicted_cmpm": 1.2,
                         "n_switches": []})
    s = summarize(rows)
    cell = s["by_cell"][0]
    assert cell["a_star"] in ("A2", "A3", "A5") and "D" in cell["conditions"]
    comp = [c for c in s["comparisons"] if c["condition"] == "D" and c["baseline"] == cell["a_star"]][0]
    assert comp["mean_diff_cmpm"] == pytest.approx(0.5) and comp["pairs"] == 2


# ---- engine: live adaptation + gated policy ------------------------------------------------
def test_engine_adapts_n_from_patient_evidence():
    eng, clock = make_engine(n=5)
    eng.cfg.auto_adapt = True
    eng.plan_now()
    for _ in range(6):
        eng._evidence("abstain")
    for _ in range(4):
        eng._evidence("wrong", 3.5)
    eng._maybe_adapt()
    assert eng.cfg.n_choices in (2, 3)
    assert "messages/min" in eng.last_adaptation["reason"]
    assert eng.adapt_model["objective"].startswith("correctly confirmed")


def test_engine_reports_model_without_switching_when_auto_adapt_off():
    eng, clock = make_engine(n=2)
    eng.plan_now()
    eng._maybe_adapt()
    assert eng.adapt_model is not None and set(eng.adapt_model["by_n"]) == {2, 3, 5}
    assert eng.cfg.n_choices == 2


def test_gated_policy_needs_calibration_and_still_confirms_the_message():
    eng, clock = make_engine(n=2, seed=4)
    eng.cfg.policy = "gated"
    # uncalibrated: every highlight still asks YES/NO
    run_until(eng, clock, lambda: eng.phase == Phase.IDLE, max_s=30)
    eng.sim_autopilot = "comfort/position/turn-left"
    run_until(eng, clock, lambda: eng.phase == Phase.PREDICTION_HIGHLIGHTED, max_s=60)
    assert eng.attempt is not None and eng.attempt.kind == "confirm"
    # calibrated on this (simulated) origin: high-confidence choices skip the step confirmation
    eng.sim_autopilot = None
    eng.start_calibration(2, trials_per_target=5, rest_trials=4)
    assert run_until(eng, clock, lambda: eng.calib is None, max_s=400)
    assert eng.stopping_for(2).calibrated
    m = produce_message(eng, clock, "comfort/position/turn-left", max_s=600)
    assert m is not None and m["correct"]
    assert eng.sent[-1]["node_id"] == "comfort/position/turn-left"   # sent only after explicit SEND
    assert m.get("gated_accepts", 0) >= 1


# ---- contract additions for the frontend (issue #1: R5, B1-B4) ------------------------------
def test_compare_rows_have_paths_and_time_estimates():
    from neurosteno.config import DATA_DIR
    from neurosteno.context import ProfileStore
    from neurosteno.routing import compare_scenarios
    rows = compare_scenarios(build_catalog(), ProfileStore(DATA_DIR), ns=(2,))
    for r in rows:
        assert len(r["path_static"]) == r["static"] and len(r["path_adaptive"]) == r["adaptive"]
        assert r["time_source"] == "assumption" and r["profile_source"] == "seed"
        assert (r["est_time_adaptive_s"] < r["est_time_static_s"]) == (r["adaptive"] < r["static"])
    b = next(r for r in rows if r["scenario"] == "B" and r["message"] == "turn-left")
    assert b["path_adaptive"][-1]["kind"] == "shortcut"
    assert all(p["kind"] != "shortcut" for p in b["path_static"])


def test_engine_refuses_to_send_without_final_yes():
    eng, clock = make_engine()
    eng.pending_leaf = build_catalog().get("comfort/position/turn-left")
    eng._send()                                   # no decoded review YES
    assert eng.sent == [] and eng.unconfirmed_send_blocked == 1
    from neurosteno.state import snapshot
    assert snapshot(eng)["safety_invariants"]["blocked_unconfirmed_send_attempts"] == 1


def test_per_message_rows_shape():
    from neurosteno.benchmark.run import aggregate_messages
    base = {"scenario": "B", "profile": "mobility", "message": "turn-left", "node_id": "x", "snr_level": "medium",
            "replicate": 0, "n_start": 5, "selections": 3, "completion_s": 30.0, "correct": 1, "wrong": 0,
            "not_completed": 0, "rejected": 0, "abstentions": 1, "sent_without_final_yes": 0}
    rows = aggregate_messages([base | {"condition": "D"}, base | {"condition": "D", "completion_s": 60.0}])
    r = rows[0]
    assert r["runs"] == 2 and r["correct_msgs_per_min"] == pytest.approx(60 * 2 / 90.0, abs=1e-3)
    assert r["final_sent_without_confirm"] == 0 and r["snr_uv"] == 1.4 and r["origin"] == "simulated"
