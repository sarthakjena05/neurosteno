"""FastAPI app: REST commands + WebSocket state stream.

Run (from backend/):  .venv\\Scripts\\python -m uvicorn neurosteno.api:app --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .acquisition.assisted import AssistedSource
from .acquisition import unicorn as unicorn_mod
from .acquisition.base import AcquisitionError
from .acquisition.replay import ReplaySource, describe_recording
from .acquisition.simulated import SimulatedSource
from .config import DATA_DIR, RECORDINGS_DIR
from .context import ProfileStore
from .engine import Engine
from .phrases.catalog import build_catalog
from .routing import SCENARIO_MESSAGES, SCENARIOS, compare_scenarios, plan_routing
from .state import snapshot

log = logging.getLogger("neurosteno")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

catalog = build_catalog()
store = ProfileStore(DATA_DIR)
engine = Engine(catalog, store)
clients: set[WebSocket] = set()
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def _warm_routing_cache() -> None:
    """Precompute exact path lengths so routing plans are instant later."""
    try:
        for n in (2, 3, 5):
            plan_routing(catalog, [], n, "static", {}, "simulated")
        log.info("routing cache warm")
    except Exception as e:  # pragma: no cover
        log.warning("routing warm-up failed: %s", e)


async def _loop() -> None:
    last_sent = 0.0
    while True:
        try:
            engine.tick()
        except Exception as e:  # keep the loop alive; surface the error
            log.exception("engine tick failed")
            engine.log("error", f"Engine error: {e}")
        loop_t = asyncio.get_event_loop().time()
        if clients and loop_t - last_sent >= 0.1:
            last_sent = loop_t
            msg = json.dumps({"type": "state", "state": snapshot(engine)})
            for ws in list(clients):
                try:
                    await ws.send_text(msg)
                except Exception:
                    clients.discard(ws)
        await asyncio.sleep(0.05)


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=_warm_routing_cache, daemon=True).start()
    task = asyncio.create_task(_loop())
    yield
    task.cancel()
    engine.set_source(None)


app = FastAPI(title="NeuroSteno", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True, "phase": engine.phase.value, "mode": engine.source_origin()}


@app.get("/api/state")
def get_state():
    return snapshot(engine)


@app.get("/api/hardware")
def hardware():
    return unicorn_mod.library_info()


@app.post("/api/hardware/discover")
async def discover(body: dict | None = None):
    only_paired = bool((body or {}).get("only_paired", True))
    try:
        devices = await asyncio.to_thread(unicorn_mod.discover, only_paired)
    except AcquisitionError as e:
        raise HTTPException(400, {"error": str(e), "code": e.code})
    return {"devices": devices, "only_paired": only_paired}


@app.post("/api/mode")
async def set_mode(body: dict):
    mode = body.get("mode")
    try:
        if mode == "off":
            engine.set_source(None)
        elif mode == "simulated":
            engine.set_source(SimulatedSource(seed=body.get("seed")))
        elif mode == "replay":
            name = body.get("recording")
            path = (RECORDINGS_DIR / Path(name).name) if name else None
            if not path or not path.exists():
                raise HTTPException(400, "recording not found")
            engine.set_source(ReplaySource(path, loop=bool(body.get("loop", True))))
        elif mode == "real":
            serial = body.get("serial")
            if not serial:
                devices = await asyncio.to_thread(unicorn_mod.discover, True)
                if not devices:
                    raise HTTPException(400, {"error": "No paired Unicorn found. Pair the headset in Windows Bluetooth "
                                                       "settings and make sure no other g.tec app holds it.",
                                              "code": "no_device"})
                serial = devices[0]
            src = await asyncio.to_thread(unicorn_mod.UnicornSource, serial, body.get("eeg_labels"))
            engine.set_source(src)
        elif mode == "assisted":
            # Demo fallback: genuine EEG stream + simulated SSVEP augmentation (origin "assisted",
            # never "real"). base="simulated" is a dry run without a headset.
            base = body.get("base", "real")
            if base == "simulated":
                inner = SimulatedSource(seed=body.get("seed"))
            elif base == "real":
                serial = body.get("serial")
                if not serial:
                    devices = await asyncio.to_thread(unicorn_mod.discover, True)
                    if not devices:
                        raise HTTPException(400, {"error": "No paired Unicorn found for ASSISTED mode.", "code": "no_device"})
                    serial = devices[0]
                inner = await asyncio.to_thread(unicorn_mod.UnicornSource, serial, body.get("eeg_labels"))
            else:
                raise HTTPException(400, "base must be 'real' or 'simulated'")
            engine.set_source(AssistedSource(inner, ssvep_uv=float(body.get("ssvep_uv", 1.8)), seed=body.get("seed")))
        else:
            raise HTTPException(400, f"unknown mode {mode}")
    except AcquisitionError as e:
        engine.log("error", f"{mode} mode failed: {e}")
        raise HTTPException(400, {"error": str(e), "code": e.code})
    return {"ok": True, "mode": engine.source_origin()}


@app.get("/api/profiles")
def profiles():
    return store.list()


@app.get("/api/recordings")
def recordings():
    RECORDINGS_DIR.mkdir(exist_ok=True)
    return [describe_recording(p) for p in sorted(RECORDINGS_DIR.glob("*.npz"))]


@app.get("/api/vocabulary")
def vocabulary(q: str = "", limit: int = 40):
    nodes = catalog.search(q, limit) if q else catalog.leaves()[:limit]
    return {"stats": catalog.stats(), "results": [{"node_id": n.id, "title": n.title, "phrase": n.phrase,
                                                   "urgent": n.urgent} for n in nodes]}


@app.get("/api/routing/compare")
async def routing_compare():
    rows = await asyncio.to_thread(compare_scenarios, catalog, ProfileStore(DATA_DIR))
    return {"scenarios": SCENARIOS, "messages": SCENARIO_MESSAGES, "rows": rows,
            "note": "Exact selection counts from the real navigator (no EEG involved)."}


@app.get("/api/benchmark")
def benchmark(sessions: bool = False):
    """SIMULATED A/B/C/D benchmark report (scripts/run_benchmark.py). Per-session rows (~1 MB)
    only with ?sessions=true."""
    p = Path(__file__).resolve().parents[1] / "reports" / "benchmark_results.json"
    if not p.exists():
        return {"available": False}
    data = json.loads(p.read_text(encoding="utf-8"))
    if not sessions:
        data.pop("sessions", None)
        data.pop("decoder_pools", None)
    return {"available": True, **data}


@app.get("/api/benchmark/multimodal")
def benchmark_multimodal(sessions: bool = False):
    """SIMULATED multimodal benchmark (scripts/run_multimodal_benchmark.py)."""
    p = Path(__file__).resolve().parents[1] / "reports" / "multimodal_results.json"
    if not p.exists():
        return {"available": False}
    data = json.loads(p.read_text(encoding="utf-8"))
    if not sessions:
        data.pop("sessions", None)
    return {"available": True, **data}


@app.get("/api/interaction")
def interaction():
    """Capability profile + full policy decision (all candidates)."""
    cap = engine.capability().to_dict()
    return {"imu": engine.imu, "capability": cap, "decision": engine.policy_decision,
            **engine.interaction_state()}


@app.post("/api/emergency")
def emergency(body: dict):
    try:
        engine.cmd_emergency(body.get("source", "demo_button"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


def dispatch(action: str, p: dict) -> dict | None:
    e = engine
    sim = e.sim
    if action == "back":
        e.cmd_back()
    elif action == "home":
        e.cmd_home()
    elif action == "undo":
        e.cmd_undo()
    elif action == "caregiver_reject":
        e.cmd_caregiver_reject()
    elif action == "resume":
        e.cmd_resume()
    elif action == "set_choices":
        e.cmd_set_choices(int(p["n"]))
    elif action == "set_auto_adapt":
        e.cfg.auto_adapt = bool(p["on"])
        e.log("config", f"Adaptive choice count {'on' if e.cfg.auto_adapt else 'off'}")
    elif action == "set_policy":
        if p["policy"] not in ("explicit", "provisional", "gated", "auto"):
            raise ValueError("policy must be explicit, provisional, gated or auto")
        e.cfg.policy = p["policy"]
        e.request_plan()
        e.log("config", f"Confirmation policy: {p['policy']}")
    elif action == "set_routing":
        if p["mode"] not in ("adaptive", "static"):
            raise ValueError("routing mode must be adaptive or static")
        e.cfg.routing_mode = p["mode"]
        e.request_plan()
        e.log("config", f"Routing: {p['mode']} (applies when the patient is back on Home)")
    elif action == "set_profile":
        e.set_profile(p["id"])
    elif action == "set_session_tags":
        e.set_session_tags(list(p.get("tags", [])))
    elif action == "set_intended":
        nid = p.get("node_id")
        if nid and nid not in catalog.by_id:
            raise ValueError("unknown phrase")
        e.intended = nid or None
        e.log("task", f"Intended message for scoring: {catalog.get(nid).phrase if nid else 'none'}")
    elif action == "emergency":
        e.cmd_emergency(p.get("source", "demo_button"))
    elif action == "ack_emergency":
        e.cmd_ack_emergency()
    elif action == "start_calibration":
        e.start_calibration(int(p.get("n", 2)), int(p.get("trials_per_target", 5)), int(p.get("rest_trials", 8)))
    elif action == "start_blink_calibration":
        e.start_blink_calibration(int(p.get("cues", 8)))
    elif action == "start_alpha_check":
        e.start_alpha_check(int(p.get("cycles", 2)))
    elif action == "cancel_calibration":
        e.cancel_calibration()
    elif action == "set_blink_enabled":
        on = bool(p["on"])
        if on and e.source and e.source.kind.value == "real" and not e.blink_cfg.calibrated:
            raise ValueError("Blink input requires a successful blink calibration on real EEG")
        e.blink_cfg.enabled = on
        e.log("blink", f"Experimental blink-to-reject {'enabled' if on else 'disabled'}")
    elif action in ("set_interaction_mode", "set_input_mode"):
        mode = str(p["mode"])
        if action == "set_input_mode" and mode not in ("eeg_only", "auto") \
                and mode not in e.contract_v2()["input"]["modes_available"]:
            raise ValueError(f"mode {mode} not available: {e.interaction['reason']}")
        e.set_interaction_mode(mode)
    elif action in ("set_gestures_enabled", "set_gesture_enabled"):
        e.set_gestures_enabled(bool(p["on"]))  # v1.4: one switch for all usable gestures
    elif action == "start_gesture_calibration":
        e.start_gesture_calibration(int(p.get("cues_per_gesture", 6)))
    elif action == "optimise_policy":
        e.request_policy()
    elif action == "record_start":
        return {"path": e.start_recording(p.get("notes", ""))}
    elif action == "record_stop":
        return {"saved": e.stop_recording()}
    elif action.startswith("sim_"):
        if sim is None:
            raise ValueError("Simulation controls are only available in SIMULATED mode")
        if action == "sim_gaze":
            e.sim_gaze_key = p.get("key")
            e.sim_autopilot = None
        elif action == "sim_autopilot":
            nid = p.get("node_id")
            if nid and nid not in catalog.by_id:
                raise ValueError("unknown phrase")
            e.sim_autopilot = nid or None
            e.sim_gaze_key = None
            e.log("sim", f"Virtual patient intends: {catalog.get(nid).phrase if nid else 'nothing (idle)'}")
        elif action == "sim_inject_mistake":
            e.sim_pending_mistake = True
            e.log("sim", "Next selection: virtual patient's EEG will follow a WRONG target")
        elif action == "sim_mistake_rate":
            e.cfg.sim_mistake_rate = float(p["rate"])
        elif action == "sim_blink":
            sim.inject_blink(bool(p.get("deliberate", True)))
            e.log("sim", f"Injected {'deliberate' if p.get('deliberate', True) else 'natural'} blink")
        elif action == "sim_eyes_closed":
            sim.eyes_closed = bool(p.get("on", True))
        elif action == "sim_noise":
            sim.noise_multiplier = float(p["multiplier"])
        elif action == "sim_ssvep":
            sim.ssvep_uv = float(p["uv"])
        elif action == "sim_bad_channel":
            (sim.bad_channels.add if p.get("on") else sim.bad_channels.discard)(p["label"])
        elif action == "sim_disconnect":
            sim.disconnected = bool(p["on"])
            e.log("sim", "Simulated Bluetooth dropout" if sim.disconnected else "Simulated link restored")
        elif action == "sim_drop":
            sim.drop_probability = float(p["p"])
        elif action == "sim_gesture":
            k = p.get("kind") or p.get("gesture")
            sim.perform_gesture(k)
            e.log("sim", f"Virtual patient head movement: {k}")
        elif action == "sim_capability":
            prof = {"no_movement": "no_head_control", "reliable_movement": "accurate",
                    "unreliable_movement": "unreliable"}.get(p["profile"], p["profile"])
            sim.set_motion_profile(prof)
            e.log("sim", f"Virtual patient motion profile: {prof}")
        elif action == "sim_motion_profile":
            sim.set_motion_profile(str(p["profile"]))
            e.log("sim", f"Virtual patient motion profile: {p['profile']}")
        elif action == "sim_imu_fault":
            f = p.get("fault")
            if f not in (None, "nan", "frozen"):
                raise ValueError("fault must be null, 'nan' or 'frozen'")
            sim.head.fault = f
            e.log("sim", f"Simulated motion-sensor fault: {f or 'none'}")
        else:
            raise ValueError(f"unknown action {action}")
    else:
        raise ValueError(f"unknown action {action}")
    return None


@app.post("/api/command")
def command(body: dict):
    action = body.get("action", "")
    try:
        result = dispatch(action, body)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "result": result}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "state", "state": snapshot(engine)}))
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") == "command":
                try:
                    res = dispatch(msg.get("action", ""), msg)
                    await ws.send_text(json.dumps({"type": "ack", "action": msg.get("action"), "result": res}))
                except Exception as e:
                    await ws.send_text(json.dumps({"type": "error", "action": msg.get("action"), "error": str(e)}))
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


# Serve the built frontend (single-command launch) if present.
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = FRONTEND_DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    def root():
        return JSONResponse({"message": "NeuroSteno API. Frontend not built: run `npm run dev` in frontend/."})
