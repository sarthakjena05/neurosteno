"""State machine + error correction, driven end-to-end through synthetic EEG and the real decoder."""

from neurosteno.engine import Phase
from neurosteno.headless import make_engine, produce_message, run_until, wait_ready


def _engine(**kw):
    eng, clock = make_engine(**kw)
    assert wait_ready(eng, clock)
    return eng, clock


def test_starts_at_home_and_produces_correct_message():
    eng, clock = _engine(profile="new-patient", n=5, routing="static", seed=1)
    assert eng.nav.prompt() == "What do you need?"
    m = produce_message(eng, clock, "breathing/suction-mouth/now")
    assert m is not None and m["correct"] is True
    assert eng.sent[-1]["text"] == "Please suction my mouth. I need this right now."
    assert eng.sent[-1]["data_origin"] == "simulated"


def test_wrong_prediction_is_rejected_and_stays_on_same_screen():
    eng, clock = _engine(profile="new-patient", n=5, routing="static", seed=2)
    eng.sim_autopilot = "comfort/temperature/too-cold"
    eng.sim_pending_mistake = True
    assert run_until(eng, clock, lambda: eng.phase == Phase.PREDICTION_HIGHLIGHTED, max_s=60)
    screen_before = [s.key for s in eng.current_slots()]
    stack_before = list(f.node_id for f in eng.nav.stack)
    assert eng.highlight["key"] != "comfort"  # injected mistake -> wrong target highlighted
    assert eng.nav.crumbs == []  # highlighted, NOT accepted
    assert run_until(eng, clock, lambda: eng.phase == Phase.REJECTED, max_s=60)
    assert [f.node_id for f in eng.nav.stack] == stack_before
    assert [s.key for s in eng.current_slots()] == screen_before
    m = produce_message(eng, clock, "comfort/temperature/too-cold")
    assert m["correct"] is True and m["rejected_predictions"] >= 1


def test_final_message_requires_explicit_confirmation():
    eng, clock = _engine(profile="new-patient", n=5, routing="static", seed=3)
    eng.sim_autopilot = "food-water/mouth/lips"
    assert run_until(eng, clock, lambda: eng.phase == Phase.MESSAGE_REVIEW, max_s=120)
    # Patient stops responding: missing input is never approval.
    eng.sim_autopilot = None
    run_until(eng, clock, lambda: False, max_s=40)
    # Never sent. (A false "CHANGE" during no-control may discard the draft, which is the
    # safe direction; it is reported as a measured limitation.)
    assert eng.sent == []
    assert not any("MESSAGE CONFIRMED" in e["text"] for e in eng.events)


def test_review_rejection_returns_to_same_decision_screen():
    eng, clock = _engine(profile="new-patient", n=5, routing="static", seed=4)
    eng.sim_autopilot = "food-water/mouth/lips"
    assert run_until(eng, clock, lambda: eng.phase == Phase.MESSAGE_REVIEW, max_s=120)
    node_before = eng.nav.frame.node_id
    eng.sim_autopilot = "food-water/mouth/dry-mouth"  # patient changes their mind -> CHANGE
    assert run_until(eng, clock, lambda: eng.phase == Phase.REJECTED, max_s=60)
    assert eng.pending_leaf is None and eng.sent == []
    assert eng.nav.frame.node_id == node_before


def test_no_control_produces_no_accepted_selection():
    eng, clock = _engine(profile="new-patient", n=2, routing="static", seed=5)
    run_until(eng, clock, lambda: False, max_s=120)
    assert eng.nav.crumbs == [] and eng.sent == []
    assert eng.no_control_windows > 0
    assert eng.emergency is None  # nonresponse never triggers an emergency


def test_signal_loss_pauses_and_discards_pending_highlight():
    eng, clock = _engine(profile="new-patient", n=5, routing="static", seed=6)
    eng.sim_autopilot = "comfort/temperature/too-hot"
    assert run_until(eng, clock, lambda: eng.phase == Phase.PREDICTION_HIGHLIGHTED, max_s=60)
    eng.sim.disconnected = True
    import time as _t
    _t.sleep(1.2)  # stall detection uses the wall-clock age of the last real sample block
    assert run_until(eng, clock, lambda: eng.phase == Phase.COMM_UNAVAILABLE, max_s=10)
    assert eng.highlight is None and eng.nav.crumbs == []
    eng.sim.disconnected = False
    assert run_until(eng, clock, lambda: eng.phase not in (Phase.COMM_UNAVAILABLE,), max_s=30)


def test_bad_electrodes_pause_communication():
    eng, clock = _engine(profile="new-patient", n=2, routing="static", seed=7)
    eng.sim.bad_channels.update({"Oz", "PO7"})
    assert run_until(eng, clock, lambda: eng.phase == Phase.COMM_UNAVAILABLE, max_s=10)
    assert "decoding channels" in eng.comm["reason"]


def test_undo_reverses_last_accepted_selection():
    eng, clock = _engine(profile="new-patient", n=5, routing="static", seed=8)
    eng.sim_autopilot = "pain/head-face/head/medicine/mild"
    assert run_until(eng, clock, lambda: len(eng.nav.stack) == 3, max_s=120)
    eng.sim_autopilot = None
    eng.cmd_undo()
    assert len(eng.nav.stack) == 2
    eng.cmd_home()
    assert len(eng.nav.stack) == 1


def test_provisional_policy_marks_steps_and_still_confirms_message():
    eng, clock = _engine(profile="new-patient", n=5, routing="static", seed=9)
    eng.cfg.policy = "provisional"
    eng.sim_autopilot = "food-water/mouth/lips"
    # Virtual patient never looks at YES on intermediate steps: correction window decides.
    assert run_until(eng, clock, lambda: eng.phase == Phase.MESSAGE_REVIEW, max_s=180)
    assert eng.sent == []  # final message still needs explicit confirmation
    assert run_until(eng, clock, lambda: len(eng.sent) == 1, max_s=60)


def test_adaptive_choice_count_increases_with_good_performance():
    eng, clock = _engine(profile="new-patient", n=2, routing="static", seed=10)
    eng.cfg.auto_adapt = True
    for target in ["comfort/temperature/too-hot", "comfort/temperature/too-cold"]:
        produce_message(eng, clock, target)
    assert eng.cfg.n_choices in (3, 5)
    assert eng.last_adaptation and eng.last_adaptation["to"] > eng.last_adaptation["from"]
    assert any("Choices per screen 2 -> 3" in e["text"] for e in eng.events)


def test_emergency_demo_control():
    eng, clock = _engine(profile="new-patient", n=2, routing="static", seed=11)
    eng.cmd_emergency("demo_button")
    assert eng.emergency["validated"] is False
    run_until(eng, clock, lambda: False, max_s=3)
    assert eng.attempt is None
    eng.cmd_ack_emergency()
    assert eng.emergency is None


def test_metrics_and_history_are_separated_by_origin():
    eng, clock = _engine(profile="mobility", n=2, routing="adaptive", seed=12)
    m = produce_message(eng, clock, "comfort/position/turn-left")
    assert m["origin"] == "simulated" and m["routing_mode"] == "adaptive"
    assert eng.store.history("mobility", "real") == []
    assert eng.store.history("mobility", "simulated")[-1]["node"] == "comfort/position/turn-left"
