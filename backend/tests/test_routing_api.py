"""Context ranking, risk-aware routing, and API smoke tests."""

import json

from fastapi.testclient import TestClient

from neurosteno.context import rank, shortcuts
from neurosteno.headless import isolated_store
from neurosteno.phrases.catalog import build_catalog
from neurosteno.routing import URGENT_ROUTES, compare_scenarios, plan_routing, request_distribution

CAT = build_catalog()


def test_ranking_is_transparent_and_profile_specific():
    store, _ = isolated_store()
    mob = shortcuts(rank(store.load("mobility"), CAT), CAT)
    assert mob[0].node_id == "pain/legs-feet/left-leg/reposition"
    assert any("Caregiver pinned" in r for r in mob[0].reasons)
    assert shortcuts(rank(store.load("new-patient"), CAT), CAT) == []


def test_distribution_never_zero():
    store, _ = isolated_store()
    p = request_distribution(rank(store.load("mobility"), CAT), CAT)
    assert abs(sum(p.values()) - 1.0) < 1e-6
    assert min(p.values()) > 0


def test_plan_static_baseline_has_no_context():
    plan = plan_routing(CAT, [], 2, "static", {}, "simulated")
    assert plan.shortcuts == [] and plan.urgent == []


def test_plan_adaptive_includes_urgent_and_explains():
    store, _ = isolated_store()
    cand = shortcuts(rank(store.load("respiratory"), CAT), CAT)
    plan = plan_routing(CAT, cand, 3, "adaptive", {}, "simulated", policy="auto")
    assert plan.urgent == URGENT_ROUTES
    assert plan.verification["policy"] == "explicit"  # no calibration -> no provisional acceptance
    assert all("why" in c for c in plan.candidates)
    assert plan.recommended_n["n"] in (2, 3, 5)


def test_scenarios_show_context_effect():
    store, _ = isolated_store()
    rows = compare_scenarios(CAT, store, ns=(2,))
    b = {r["message"]: r for r in rows if r["scenario"] == "B"}
    assert b["turn-left"]["adaptive"] < b["turn-left"]["static"]
    c = {r["message"]: r for r in rows if r["scenario"] == "C"}
    assert c["suction-now"]["adaptive"] < c["suction-now"]["static"]


def test_api_smoke():
    from neurosteno.api import app

    with TestClient(app) as client:
        assert client.get("/api/health").json()["ok"]
        assert client.get("/api/vocabulary?q=left leg").json()["results"]
        assert client.post("/api/mode", json={"mode": "simulated", "seed": 1}).json()["mode"] == "simulated"
        with client.websocket_connect("/ws") as ws:
            st = json.loads(ws.receive_text())["state"]
            assert st["mode"]["kind"] == "simulated" and st["mode"]["is_real_eeg"] is False
            assert st["screen"]["prompt"] == "What do you need?"
        r = client.post("/api/command", json={"action": "sim_autopilot", "node_id": "breathing/fan/now"})
        assert r.status_code == 200
        assert client.post("/api/command", json={"action": "set_choices", "n": 4}).status_code == 400
        assert client.post("/api/mode", json={"mode": "off"}).json()["mode"] == "none"
        assert client.post("/api/command", json={"action": "sim_noise", "multiplier": 2}).status_code == 400
