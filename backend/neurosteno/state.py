"""Serialisable engine snapshot pushed to the frontend over WebSocket.

The server is authoritative: the UI renders exactly this state, so what the patient sees
(highlight, pending vs accepted, paused) is always what the engine believes.
"""

from __future__ import annotations

import time

from .acquisition.base import SourceKind
from .engine import EMERGENCY_SOURCES, Engine, Phase
from .navigation import BACK_KEY

MODE_LABEL = {
    "real": "REAL EEG",
    "simulated": "SIMULATED EEG",
    "replay": "REPLAY",
    "none": "NO EEG SOURCE",
}


def _slot_dict(eng: Engine, i: int, slot, freqs: list[float]) -> dict:
    sugg_ids = {s.node_id for s in eng.shortcut_list}
    reasons = []
    for o in slot.options:
        if o.node_id:
            for s in eng.shortcut_list:
                node_path = {n.id for n in eng.catalog.get(s.node_id).path()}
                if o.node_id in node_path and o.key == o.node_id and eng.cfg.routing_mode == "adaptive":
                    reasons.append(f"Contains suggestion: {eng.catalog.get(s.node_id).title}")
    return {
        "index": i,
        "key": slot.key,
        "label": slot.label,
        "kind": slot.kind,
        "freq": freqs[i] if i < len(freqs) else None,
        "items": [{"key": o.key, "label": o.label, "kind": o.kind, "urgent": o.urgent, "group": o.group,
                   "icon": o.icon, "suggested": o.key.startswith("sc:") or (o.node_id in sugg_ids),
                   "urgent_route": o.key.startswith("ur:")} for o in slot.options],
        "is_back": slot.kind == "option" and slot.options[0].key == BACK_KEY,
        "suggest_reasons": sorted(set(reasons))[:3],
    }


def snapshot(eng: Engine) -> dict:
    src = eng.source
    kind = src.kind.value if src else "none"
    origin = eng.source_origin()
    n = eng.cfg.n_choices
    freqs = eng.cfg.freqs.get(n, [])
    slots = eng.current_slots()
    a = eng.attempt
    now = eng.clock()
    home_badges = {}
    if eng.cfg.routing_mode == "adaptive":
        for nid in eng.nav.shortcuts + eng.nav.urgent:
            cat = eng.catalog.get(nid).path()[1].id
            home_badges.setdefault(cat, []).append(
                {"title": " › ".join(x.label for x in eng.catalog.get(nid).path()[2:]),
                 "urgent": nid in eng.nav.urgent})
    calib = None
    if eng.calib is not None:
        c = eng.calib
        cue = None
        if c.kind == "ssvep" and c.idx < len(c.plan):
            lbl = c.plan[c.idx]
            cue = None if lbl is None else lbl
        calib = {
            "kind": c.kind, "n": c.n, "freqs": c.freqs, "trial": c.idx + (0 if c.kind == "ssvep" else 0),
            "trials": len(c.plan), "sub": c.sub, "cue_target": cue,
            "rest_trial": c.kind == "ssvep" and c.idx < len(c.plan) and c.plan[c.idx] is None,
            "elapsed": round(now - c.sub_since, 2),
            "rejected_trials": c.rejected,
            "durations": {"cue": c.CUE_S, "stim": c.STIM_S, "break": c.BREAK_S, "relax": c.RELAX_S,
                          "blink": c.BLINK_CUE_S, "eyes_open": c.EYES_S, "eyes_closed": c.EYES_S},
        }
    sim = eng.sim
    plan = eng.plan.to_dict() if eng.plan else None
    return {
        "version": eng.version,
        "server_time": time.time(),
        "mode": {
            "kind": kind,
            "label": MODE_LABEL.get(kind, kind),
            "origin": origin,
            "is_real_eeg": kind == "real",
            "replay_of": src.status.extra.get("origin") if src and src.kind == SourceKind.REPLAY else None,
            "calibrated": bool(src) and eng.stopping_for(eng.cfg.n_choices).calibrated,
            "calibration_required": bool(src) and eng.calibration_required(),
        },
        "source": src.info() if src else None,
        "phase": eng.phase.value,
        "phase_elapsed": round(now - eng.phase_since, 2),
        "screen": {
            "prompt": eng.nav.prompt(),
            "location": eng.nav.location(),
            "at_home": len(eng.nav.stack) == 1,
            "crumbs": [{"label": c.label, "provisional": c.provisional} for c in eng.nav.crumbs],
            "slots": [_slot_dict(eng, i, s, freqs) for i, s in enumerate(slots)],
            "n_choices": n,
            "can_undo": eng.nav.can_undo() or eng.pending_leaf is not None,
            "home_badges": home_badges,
        },
        "attempt": None if a is None else {
            "kind": a.kind,
            "targets": [t.__dict__ for t in a.targets],
            "elapsed_s": round(now - a.started, 2),
            "max_window_s": a.stopper.stopping.max_window_s,
            "min_window_s": a.stopper.stopping.min_window_s,
            "calibrated": a.stopper.stopping.calibrated,
            "last": a.last,
        },
        "highlight": None if eng.highlight is None else eng.highlight | {
            "provisional_remaining_s": None if eng.provisional_deadline is None
            else round(max(0.0, eng.provisional_deadline - now), 2)},
        "feedback": eng.feedback,
        "review": None if eng.pending_leaf is None else {
            "text": eng.pending_leaf.phrase, "node_id": eng.pending_leaf.id, "title": eng.pending_leaf.title,
            "urgent": eng.pending_leaf.urgent,
            "provisional_steps": sum(1 for c in eng.nav.crumbs if c.provisional)},
        "sent": eng.sent[-10:][::-1],
        "comm": eng.comm,
        "quality": eng.quality,
        "decoder": {
            "allowed_choices": eng.allowed_choices(),
            "stopping": eng.stopping_for(n).to_dict(),
            "calibration": {k: {kk: v for kk, v in r.items() if kk != "stopping"} for k, r in eng.calib_results.items()
                            if isinstance(r, dict)},
        },
        "config": eng.cfg.to_dict() | {"effective_policy": eng.effective_policy},
        "profile": {
            "id": eng.profile.id, "display_name": eng.profile.display_name, "summary": eng.profile.summary,
            "synthetic": eng.profile.synthetic, "notes": eng.profile.caregiver_info.get("notes", []),
            "session_tags": eng.profile.session_context.get("tags", []),
            "suggestions": [s.to_dict(eng.catalog) for s in eng.suggestions[:8]],
        },
        "routing": plan,
        "routing_pending": eng._plan_future is not None or eng._plan_pending is not None,
        "task": None if eng.task is None else {k: v for k, v in eng.task.items() if k != "started"}
        | {"elapsed_s": round(now - eng.task["started"], 1)},
        "intended": None if not eng.intended else {"node_id": eng.intended, "text": eng.catalog.get(eng.intended).phrase},
        "metrics": {"recent": eng.metrics[-12:][::-1], "summary": eng.metrics_summary()},
        "adaptation": {"auto": eng.cfg.auto_adapt, "recent": [o[0] for o in eng.outcomes], "last_change": eng.last_adaptation,
                       "model": eng.adapt_model},
        "sim": None if sim is None else {
            "gaze_key": eng.sim_gaze_key, "gaze_hz": sim.gaze_hz,
            "autopilot": None if not eng.sim_autopilot else {"node_id": eng.sim_autopilot,
                                                            "text": eng.catalog.get(eng.sim_autopilot).phrase},
            "pending_mistake": eng.sim_pending_mistake, "mistake_rate": eng.cfg.sim_mistake_rate,
            "noise_multiplier": sim.noise_multiplier, "bad_channels": sorted(sim.bad_channels),
            "disconnected": sim.disconnected, "drop_probability": sim.drop_probability,
            "ssvep_uv": sim.ssvep_uv,
        },
        "calibration": calib,
        "electrode_check": eng.electrodes,
        "blink": {"config": eng.blink_cfg.to_dict(), "events": list(eng.blink_events)[-8:][::-1]},
        "emergency": eng.emergency,
        "emergency_sources": EMERGENCY_SOURCES,
        "recording": None if not eng.recorder else {
            "path": str(eng.recorder.path.name),
            "seconds": round((src.total_samples - eng.recorder.start_sample) / src.sample_rate, 1) if src else 0},
        "events": list(eng.events)[-60:][::-1],
        "vocabulary": eng.catalog.stats(),
        "phases": [p.value for p in Phase],
    }
