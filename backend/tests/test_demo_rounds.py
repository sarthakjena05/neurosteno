"""Consecutive demo rounds through synthetic EEG and the real decoder."""
import json
import asyncio
import contextlib
from copy import deepcopy

import numpy as np
from fastapi.testclient import TestClient

from neurosteno.engine import Phase
from neurosteno.headless import make_engine, run_until, wait_ready
from neurosteno.state import snapshot


def test_two_rounds_manual_final_yes_survives_unclear_window():
    eng, clock = make_engine(n=5, routing="static", seed=1)
    assert wait_ready(eng, clock)
    for round_index, target in enumerate(("food-water/mouth/lips", "comfort/temperature/too-cold")):
        eng.sim_autopilot = target
        assert run_until(eng, clock, lambda: eng.phase == Phase.MESSAGE_REVIEW, max_s=180)
        eng.sim_autopilot = None
        # Operator clicks SEND late in the current evidence window. The old
        # rest samples must abstain, then fresh EEG must retain the same gaze.
        old = eng.attempt
        old.stopper.stopping = deepcopy(old.stopper.stopping)
        old.stopper.stopping.margin_threshold = {0.0: 1.1}
        eng.sim_gaze_key = "yes"
        assert run_until(eng, clock, lambda: eng.attempt is not old, max_s=15)
        assert len(eng.sent) == round_index
        assert eng.sim_gaze_key == "yes"
        assert run_until(eng, clock, lambda: len(eng.sent) == round_index + 1, max_s=60)
        assert eng.sent[-1]["node_id"] == target
        assert eng.sent[-1]["confirmed_by"] == "decoded final YES"
        json.dumps(snapshot(eng))
        assert run_until(eng, clock, lambda: eng.phase != Phase.MESSAGE_SENT, max_s=10)
    assert not eng.gesture_cfg.enabled


def test_movement_sample_serializes_over_rest_and_websocket(monkeypatch):
    from neurosteno import api
    from neurosteno.signal.gestures import segments

    eng, _ = make_engine(n=5, routing="static", seed=1)
    # Reproduce the sample arithmetic used by OnlineGestureDetector without
    # enabling gestures or performing any movement in the demo session.
    _, end = segments(np.r_[np.zeros(20), np.ones(10) * 10, np.zeros(20)], 5)[0]
    eng.gesture_events.append({"sample": 100 + end * 5, "kind": "movement", "accepted": False})
    monkeypatch.setattr(api, "engine", eng)
    with TestClient(api.app) as client:
        assert client.get("/api/state").status_code == 200
        with client.websocket_connect("/ws") as ws:
            assert json.loads(ws.receive_text())["type"] == "state"
            # A second frame exercises the background broadcasting task.
            assert json.loads(ws.receive_text())["type"] == "state"
        assert client.get("/api/interaction").status_code == 200
        assert client.get("/api/health").json()["ok"]


def test_broadcast_serialization_error_does_not_stop_engine(monkeypatch, caplog):
    from neurosteno import api

    class EngineStub:
        ticks = 0

        def tick(self):
            self.ticks += 1

    async def exercise():
        received = asyncio.Event()

        class SocketStub:
            async def send_text(self, msg):
                assert json.loads(msg)["type"] == "state"
                received.set()

        engine = EngineStub()
        calls = 0

        def state(_):
            nonlocal calls
            calls += 1
            return {"sample": object() if calls == 1 else 1}

        monkeypatch.setattr(api, "engine", engine)
        monkeypatch.setattr(api, "snapshot", state)
        monkeypatch.setattr(api, "clients", {SocketStub()})
        task = asyncio.create_task(api._loop())
        try:
            await asyncio.wait_for(received.wait(), 3)
            assert not task.done()
            assert engine.ticks > 1
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(exercise())
    assert "state broadcast serialization failed" in caplog.text
