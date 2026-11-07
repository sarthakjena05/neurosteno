"""Optional multimodal interaction for the live engine (mixed into engine.Engine).

EEG-only is the default and always works: patients without voluntary head movement never need
the IMU. Everything here is additive:

  * IMU monitor (4 Hz): availability/health of the accelerometer + gyroscope, latest values,
    head-motion flag (used to restart EEG evidence windows contaminated by movement).
  * Gesture detector (signal/gestures.py) on the motion channels, only after a gesture
    calibration on the CURRENT data origin. It runs passively whenever calibrated, so
    unprompted activations accumulate as false-activation evidence even while gestures are off.
  * Interaction mode: requested by the operator (eeg_only | hybrid | motion_assisted | auto) and
    EFFECTIVE mode actually in use. The effective mode falls back to eeg_only whenever gesture
    input is disabled, not calibrated on this origin, not usable for this patient, or the IMU
    stream is missing/invalid (gesture dropout) -- with the reason reported.
  * Hybrid step confirmation: when a highlight asks YES/NO, a gesture window opens; a NOD accepts,
    a SHAKE rejects; the EEG YES/NO keeps running in parallel and is the fallback. Conflicts
    (nod and shake together, or a shake right after an EEG YES) are never accepted.
  * Motion-assisted: a sustained TILT = "go back" (navigation only).
  * Hard rules: a gesture never sends a message, never confirms the final review, never
    triggers an urgent message or an emergency, and is ignored while communication is paused.
  * Policy optimiser (policy.py) runs in the background on the capability profile; in "auto"
    mode its choice (mode, n, routing, confirmation) is applied, otherwise it is advisory.
"""

from __future__ import annotations

import concurrent.futures as cf
import random
import time
from collections import deque

import numpy as np

from .capability import (CapabilityProfile, claim_for, eeg_from_calibration, fatigue_from, gestures_from_calibration,
                         motion_summary)
from .policy import Timing as PTiming, gesture_window, length_distribution, optimise
from .routing import request_distribution
from .signal.gestures import KINDS, GestureConfig, OnlineGestureDetector, calibrate_gestures, imu_health, preprocess

INTERACTION_MODES = ("eeg_only", "hybrid", "motion_assisted", "auto")
GESTURE_NEUTRAL_S = 20.0
GESTURE_CUE_S = {"nod": 4.0, "shake": 4.0, "tilt": 5.0}
GESTURE_NATURAL_S = 30.0
MOTION_GATE_FACTOR = 3.0        # EEG window restarted if angular speed > 3 x calibrated activity threshold
MAX_MOTION_RESTARTS = 3         # per attempt; after that the decoder's own abstention handles it
LATE_GESTURE_S = 2.0
POLICY_EVERY = 8                # live outcomes between background re-optimisations


def _empty_live() -> dict:
    return {k: {"hits": 0, "prompts": 0, "unprompted": 0, "unprompted_exposure_s": 0.0} for k in KINDS}


class InteractionMixin:
    # ===================================================================================
    # setup
    # ===================================================================================
    def _init_interaction(self) -> None:
        self.gesture_cfg = GestureConfig()
        self.gesture_detector: OnlineGestureDetector | None = None
        self.gesture_events: deque = deque(maxlen=30)
        self.imu: dict = {"available": False, "reason": "No source selected"}
        self.gesture_live = _empty_live()
        self.gesture_window: dict | None = None
        self.interaction = {"requested": "eeg_only", "effective": "eeg_only", "reason": "EEG-only (default)"}
        self.policy_decision: dict | None = None
        self._policy_future: cf.Future | None = None
        self._policy_executor = cf.ThreadPoolExecutor(max_workers=1)
        self._policy_outcomes = 0
        self.applied_policy: str | None = None
        self.decision_log: deque = deque(maxlen=400)
        self.motion_restarts = 0
        self.gesture_blocked_final = 0
        self.gesture_conflicts = 0
        self._last_imu = 0.0
        self._imu_t: float | None = None
        self._last_motion_sample = -1

    def _interaction_on_source(self, src) -> None:
        self.gesture_window = None
        self._imu_t = None
        self._last_motion_sample = -1
        self.gesture_live = _empty_live()
        self.imu = {"available": False, "reason": "Waiting for motion data"}
        self.gesture_detector = None
        if src is not None and src.imu_layout() is not None:
            self.gesture_detector = OnlineGestureDetector(self.gesture_cfg, src.sample_rate)
            self.gesture_detector.processed_until = src.total_samples
        self._refresh_interaction()
        self.request_policy()

    def gestures_ready(self) -> bool:
        return bool(self.gesture_cfg.calibrated and self.gesture_cfg.origin == self.source_origin())

    # ===================================================================================
    # IMU monitor + gesture detection (4 Hz)
    # ===================================================================================
    def _update_imu(self, now: float) -> None:
        self._last_imu = now
        src = self.source
        origin = self.source_origin()
        lay = src.imu_layout() if src is not None else None
        if lay is None:
            self.imu = {"available": False, "origin": origin, "claim": claim_for(origin),
                        "reason": "This source has no accelerometer/gyroscope channels: EEG-only."}
            self._imu_t = now
            self._refresh_interaction()
            return
        fs, total = src.sample_rate, src.total_samples
        m = src.read_motion(max(0, total - int(fs)), total)
        if self.quality.get("stalled"):
            h = {"ok": False, "reason": "data stream stalled (no samples arriving)"}
        elif m is None or len(m["gyro_dps"]) < int(0.5 * fs):
            h = {"ok": False, "reason": "collecting motion data"}
        else:
            h = imu_health(m["accel_g"], m["gyro_dps"], fs)
        info = {"available": bool(h["ok"]), "reason": h["reason"], "origin": origin, "claim": claim_for(origin),
                "sample_rate": fs, "units": {"accel": lay["accel_unit"], "gyro": lay["gyro_unit"]},
                "channels": {"accel": [src.channels[i].name for i in lay["accel"]],
                             "gyro": [src.channels[i].name for i in lay["gyro"]]},
                "timestamping": "device sample clock, shared with EEG (same frame)",
                "channel_map": src.status.extra.get("channel_map")}
        if m is not None and len(m["gyro_dps"]):
            info["latest"] = {"t": round(float(m["t"][-1]), 3), "accel_g": [round(float(v), 4) for v in m["accel_g"][-1]],
                              "gyro_dps": [round(float(v), 2) for v in m["gyro_dps"][-1]]}
        if h["ok"] and self.gestures_ready():
            pre = preprocess(m["accel_g"][-int(0.5 * fs):], m["gyro_dps"][-int(0.5 * fs):], fs, self.gesture_cfg.bias)
            peak = float(pre["speed"].max()) if len(pre["speed"]) else 0.0
            gate = MOTION_GATE_FACTOR * self.gesture_cfg.act_dps
            if peak > gate:
                self._last_motion_sample = total
            info["motion"] = {"angular_speed_dps": round(peak, 1), "moving": peak > gate, "gate_dps": round(gate, 1)}
        self.imu = info
        if self.gesture_detector is not None and self.gesture_detector.cfg is not self.gesture_cfg:
            self.gesture_detector = OnlineGestureDetector(self.gesture_cfg, fs)  # profile/calibration changed
            self.gesture_detector.processed_until = total
        if h["ok"] and self.gestures_ready() and self.gesture_detector is not None:
            in_window = self.gesture_window is not None and now < self.gesture_window["until"]
            dt = 0.0 if self._imu_t is None else max(0.0, now - self._imu_t)
            if not in_window:
                for k in KINDS:
                    self.gesture_live[k]["unprompted_exposure_s"] += dt
            evs = self.gesture_detector.update(src.read_motion, total)
            if evs:
                self._handle_gestures(evs, now)
        self._imu_t = now
        self._refresh_interaction()

    # ===================================================================================
    # interaction mode
    # ===================================================================================
    def _refresh_interaction(self) -> None:
        req = self.cfg.interaction_mode
        prev = self.interaction.get("effective")
        eff, why = "eeg_only", ""
        modes = self.capability(light=True).modes()
        if req == "eeg_only":
            why = "EEG-only selected (default; no head movement needed)"
        elif not self.gesture_cfg.enabled:
            why = "Gesture input is OFF (operator must enable it after a gesture calibration)"
        elif not self.gestures_ready():
            why = "No gesture calibration on this data origin"
        elif not self.imu.get("available"):
            why = f"Motion sensor unavailable ({self.imu.get('reason')}): EEG-only fallback"
        else:
            target = req
            if req == "auto":
                ch = (self.policy_decision or {}).get("chosen") or {}
                target = ch.get("mode", "eeg_only")
            if target == "eeg_only":
                why = "Optimiser chose EEG-only" if req == "auto" else ""
            elif not modes.get(target):
                why = modes["why"][target]
            else:
                eff, why = target, ("chosen by the optimiser" if req == "auto" else "selected by operator")
        self.interaction = {"requested": req, "effective": eff, "reason": why}
        if prev is not None and prev != eff:
            self.log("interaction", f"Interaction mode now {eff.upper()}" + (f": {why}" if why else ""))
            self.version += 1

    def set_interaction_mode(self, mode: str) -> None:
        if mode not in INTERACTION_MODES:
            raise ValueError(f"interaction mode must be one of {INTERACTION_MODES}")
        self.cfg.interaction_mode = mode
        self.log("config", f"Interaction mode requested: {mode}")
        self._refresh_interaction()
        self.request_policy()

    def set_gestures_enabled(self, on: bool) -> None:
        if on:
            if not self.gestures_ready():
                raise ValueError("Gesture input needs a gesture calibration on the current data origin first")
            if not any(self.gesture_cfg.usable.values()):
                raise ValueError("The gesture calibration found no reliably detectable gesture for this patient")
        self.gesture_cfg.enabled = bool(on)
        self.log("gesture", f"Gesture input {'ENABLED' if on else 'disabled'}"
                 + (f" (usable: {', '.join(k for k, v in self.gesture_cfg.usable.items() if v)})" if on else ""))
        self._refresh_interaction()
        self.request_policy()

    def gesture_window_s(self) -> float:
        if self.cfg.gesture_window_s:
            return float(self.cfg.gesture_window_s)
        return gesture_window(self.capability(light=True)) if self.gestures_ready() else 3.0

    # ===================================================================================
    # gesture events
    # ===================================================================================
    def _arm_gesture_window(self, a) -> None:
        """Called when a per-step YES/NO confirmation starts."""
        if self.interaction["effective"] == "eeg_only" or not (self.gesture_cfg.active("nod") or
                                                               self.gesture_cfg.active("shake")):
            self.gesture_window = None
            return
        now = self.clock()
        W = self.gesture_window_s()
        self.gesture_window = {"attempt": a, "started": now, "until": now + W, "W": W, "decided": None}
        sim = self.sim
        if sim is not None and self.sim_autopilot and self.highlight is not None:
            want = self.nav.slot_toward(self.current_slots(), self.sim_autopilot)
            kind = "nod" if self.highlight["slot_index"] == want else "shake"
            if a.mistake:
                kind = "shake" if kind == "nod" else "nod"
            sim.perform_gesture(kind)
            self.gesture_window["sim_gesture"] = kind

    def _gesture_window_eeg_decided(self, yes: bool) -> None:
        """The EEG answered a step confirmation. If the gesture window had already expired with no
        gesture, that is a missed gesture for the intended kind (live sensitivity evidence)."""
        w = self.gesture_window
        if w is None or w["decided"] is not None:
            return
        w["decided"] = "eeg"
        if self.clock() >= w["until"]:
            self.gesture_live["nod" if yes else "shake"]["prompts"] += 1

    def gesture_window_open(self) -> bool:
        w = self.gesture_window
        return bool(w is not None and self.attempt is w["attempt"] and self.clock() < w["until"])

    def _handle_gestures(self, evs: list[dict], now: float) -> None:
        acc = [e for e in evs if e["accepted"]]
        for e in evs:
            self.gesture_events.append(e | {"time": time.time(), "action": None if e["accepted"] else "rejected"})
        if not acc:
            return
        eff = self.interaction["effective"]
        a = self.attempt
        w = self.gesture_window
        paused = self.phase.value in ("COMM_UNAVAILABLE", "CALIBRATING", "OFF") or self.emergency
        oscs = [e for e in acc if e["kind"] in ("nod", "shake")]

        def mark(e, action):
            for x in reversed(self.gesture_events):
                if x.get("sample") == e["sample"] and x.get("kind") == e["kind"]:
                    x["action"] = action
                    break

        if paused:
            for e in acc:
                mark(e, "ignored: communication paused")
            return
        # ---- hybrid step confirmation ------------------------------------------------
        if (oscs and eff != "eeg_only" and a is not None and a.kind == "confirm" and w is not None
                and w["attempt"] is a and self.phase.value == "PREDICTION_HIGHLIGHTED"):
            kinds = {e["kind"] for e in oscs if self.gesture_cfg.active(e["kind"])}
            if len(kinds) == 2:
                self.gesture_conflicts += 1
                for e in oscs:
                    mark(e, "conflict: nod and shake together")
                self.attempt = None
                self._reject("Conflicting head gestures (nod and shake together): choice NOT accepted",
                             kind="conflict")
                return
            if kinds:
                k = kinds.pop()
                e = next(x for x in oscs if x["kind"] == k)
                self.gesture_live[k]["hits"] += 1
                self.gesture_live[k]["prompts"] += 1
                w["decided"] = k
                self.attempt = None
                if k == "nod":
                    mark(e, "confirmed highlighted choice (YES)")
                    self.log("decision", "Patient confirmed the highlighted choice with a NOD")
                    self._accept(provisional=False)
                else:
                    mark(e, "rejected highlighted choice (NO)")
                    self._reject("Patient rejected the prediction with a head SHAKE")
                return
        # ---- conflict with a just-made EEG YES (choice not yet applied) ------------------
        if (oscs and eff != "eeg_only" and w is not None and w["decided"] is None and self.phase.value == "ACCEPTED"
                and now - w["started"] <= w["W"] + LATE_GESTURE_S
                and any(e["kind"] == "shake" and self.gesture_cfg.active("shake") for e in oscs)):
            self.gesture_conflicts += 1
            for e in oscs:
                mark(e, "conflict with EEG YES: choice withdrawn")
            self.gesture_window = None
            self._reject("EEG said YES but a head SHAKE said NO: conflicting commands, choice NOT accepted",
                         kind="conflict")
            return
        for e in acc:
            k = e["kind"]
            late = w is not None and now - w["started"] <= w["W"] + LATE_GESTURE_S
            if k == "tilt" and eff == "motion_assisted" and self.gesture_cfg.active("tilt"):
                if (self.phase.value in ("IDLE", "EVIDENCE_COLLECTION") and len(self.nav.stack) > 1
                        and self.pending_leaf is None):
                    self._cancel_attempt()
                    self.nav.back()
                    self.log("navigation", "Sustained head tilt: went back one level")
                    self._enter(type(self.phase).IDLE)
                    mark(e, "went back one level")
                    continue
            if a is not None and a.kind == "review" and k in ("nod", "shake"):
                self.gesture_blocked_final += 1
                mark(e, "ignored: the final message needs an EEG 'Yes, send it'")
                self.log("safety", f"Head {k} during final review ignored: gestures cannot send or cancel messages")
                continue
            if late:
                mark(e, "late: decision already made")
                continue
            self.gesture_live[k]["unprompted"] += 1
            mark(e, "ignored (no prompt); counted as possible false activation")

    # ===================================================================================
    # EEG motion gate
    # ===================================================================================
    def _motion_restart(self, a) -> bool:
        """Restart an EEG evidence window that head movement contaminated (IMU-based artifact
        rejection). Only with a calibrated neutral position on this origin; bounded per attempt."""
        if not self.cfg.eeg_motion_gate or not self.gestures_ready() or not self.imu.get("available"):
            return False
        if a.motion_restarts >= MAX_MOTION_RESTARTS:
            return False
        lat = int(self.fbcca.latency_s * self.source.sample_rate)
        if self._last_motion_sample < a.start_sample + lat:
            return False
        a.motion_restarts += 1
        self.motion_restarts += 1
        a.start_sample = self.source.total_samples
        a.stopper = type(a.stopper)(a.stopper.freqs, a.stopper.fs, a.stopper.stopping, a.stopper.fbcca)
        a.last = {"motion_restart": a.motion_restarts}
        if a.motion_restarts == 1:
            self.log("decision", "Head movement during the EEG window: restarting evidence collection")
        return True

    # ===================================================================================
    # gesture calibration session
    # ===================================================================================
    def start_gesture_calibration(self, cues_per_gesture: int = 6) -> None:
        from .engine import CalibSession, Phase
        if self.source is None:
            raise ValueError("Select a source first")
        if self.source.imu_layout() is None:
            raise ValueError("This source has no accelerometer/gyroscope channels")
        plan = [k for k in KINDS for _ in range(max(2, int(cues_per_gesture)))]
        random.shuffle(plan)
        plan = ["neutral"] + plan + ["natural"]
        self._cancel_attempt()
        self.calib = CalibSession("gesture", plan=plan, sub="neutral", sub_since=self.clock(),
                                  stim_start=self.source.total_samples)
        self._enter(Phase.CALIBRATING)
        self.log("calibration", f"Gesture calibration started: keep still {GESTURE_NEUTRAL_S:.0f} s, then "
                 f"{len(plan) - 2} cued gestures, then {GESTURE_NATURAL_S:.0f} s of natural movement")

    @staticmethod
    def gesture_step_duration(label: str) -> float:
        return {"neutral": GESTURE_NEUTRAL_S, "natural": GESTURE_NATURAL_S}.get(label, GESTURE_CUE_S.get(label, 4.0))

    def _tick_gesture_calibration(self, now: float) -> None:
        c, src = self.calib, self.source
        label = c.plan[c.idx]
        if now - c.sub_since < self.gesture_step_duration(label):
            return
        m = src.read_motion(c.stim_start, src.total_samples)
        c.windows.append((label, m))
        c.idx += 1
        if c.idx >= len(c.plan):
            c.sub = "computing"
            neutral = next(w for k, w in c.windows if k == "neutral")
            cued = [{"kind": k, "accel_g": w["accel_g"], "gyro_dps": w["gyro_dps"], "cue_s": 0.0}
                    for k, w in c.windows if k in KINDS]
            natural = [w for k, w in c.windows if k == "natural"]
            c.future = self._executor.submit(calibrate_gestures, neutral, cued, natural, src.sample_rate,
                                             self.source_origin())
            return
        c.sub, c.sub_since, c.stim_start = c.plan[c.idx], now, src.total_samples
        self._mark("gesture_cue", label=c.sub)
        if self.sim is not None and c.sub in KINDS:
            self.sim.perform_gesture(c.sub)

    def _finish_gesture_calibration(self, res: dict) -> None:
        res["claim"] = claim_for(res.get("origin", "none"))
        if res.get("ok"):
            self.gesture_cfg = GestureConfig.from_dict(res["config"])
            self.gesture_cfg.enabled = False  # calibration never turns gesture input on
        else:
            self.gesture_cfg = GestureConfig()
        self.gesture_live = _empty_live()
        if self.source is not None and self.source.imu_layout() is not None:
            self.gesture_detector = OnlineGestureDetector(self.gesture_cfg, self.source.sample_rate)
            self.gesture_detector.processed_until = self.source.total_samples
        self.log("calibration", f"Gesture calibration ({res.get('origin')} data): {res.get('note')}")

    def _load_gesture_calibration(self, res: dict) -> None:
        if res.get("ok"):
            self.gesture_cfg = GestureConfig.from_dict(res["config"])
            self.gesture_cfg.enabled = False

    # ===================================================================================
    # capability + policy
    # ===================================================================================
    def capability(self, light: bool = False) -> CapabilityProfile:
        origin = self.source_origin()
        cap = CapabilityProfile(origin)
        for n in (2, 3, 5):
            res = self.calib_results.get(f"ssvep_{n}")
            res = res if res and res.get("origin") == origin else None
            cap.eeg[n] = eeg_from_calibration(n, res, origin, self.stopping_for(n).max_window_s,
                                              live=self.tracker.counts(n))
        g = self.calib_results.get("gesture")
        if g and g.get("origin") == origin and self.gestures_ready():
            cap.gestures = gestures_from_calibration(g, self.gesture_live)
        cap.imu = {"ok": bool(self.imu.get("available")), "reason": self.imu.get("reason", "")}
        cap.gesture_window_s = self.cfg.gesture_window_s
        if not light:
            cap.motion = motion_summary(g if g and g.get("origin") == origin else None)
            cap.fatigue = fatigue_from(list(self.decision_log))
            cap.calibration_overhead_s = {"eeg_per_n": {n: (5 * n + 8) * 6.5 for n in (2, 3, 5)},
                                          "gesture": GESTURE_NEUTRAL_S + 6 * sum(GESTURE_CUE_S.values()) + GESTURE_NATURAL_S}
        return cap

    def _length_dists(self) -> tuple[dict, dict]:
        p = request_distribution(self.suggestions, self.catalog)
        sc = tuple(self.plan.shortcuts) if self.plan else ()
        ur = tuple(self.plan.urgent) if self.plan else ()
        ld, reach = {}, {}
        for n in self.allowed_choices():
            ld[("context", n)], reach[("context", n)] = length_distribution(self.catalog, n, sc, ur, p)
            ld[("static", n)], reach[("static", n)] = length_distribution(self.catalog, n, (), (), p)
        return ld, reach

    def request_policy(self) -> None:
        if self.source is None or (self._policy_future is not None and not self._policy_future.done()):
            return
        cap = self.capability()
        tm = PTiming(self.cfg.rest_s, self.cfg.feedback_s, self.cfg.sent_display_s, self.cfg.confirm_attempts)
        cur = self.applied_policy

        def job():
            ld, reach = self._length_dists()
            out = optimise(cap, ld, reach, tm, allowed_n=tuple(self.allowed_choices()), current=cur,
                           hysteresis=self.cfg.adapt_hysteresis)
            out["capability"] = cap.to_dict()
            return out
        self._policy_future = self._policy_executor.submit(job)

    def _poll_policy(self) -> None:
        f = self._policy_future
        if f is None or not f.done():
            return
        self._policy_future = None
        try:
            self.policy_decision = f.result()
        except Exception as e:  # never let the optimiser break communication
            self.log("policy", f"Policy optimisation failed: {e}")
            return
        self._refresh_interaction()
        ch = self.policy_decision.get("chosen")
        if self.cfg.interaction_mode == "auto" and ch and ch["key"] != self.applied_policy:
            self._apply_policy(ch, self.policy_decision["reason"])

    def _apply_policy(self, row: dict, why: str) -> None:
        if row["n"] in self.allowed_choices() and row["n"] != self.cfg.n_choices:
            self.cfg.n_choices = row["n"]
        self.cfg.routing_mode = "adaptive" if row["routing"] == "context" else "static"
        self.cfg.policy = "gated" if row["step_confirmation"] == "gated" else "explicit"
        self.applied_policy = row["key"]
        self.request_plan()
        self._refresh_interaction()
        self.log("policy", f"Interaction policy {row['key']} applied: {why}")

    def _note_outcome(self, kind: str, decision_s: float | None) -> None:
        self.decision_log.append((self.clock(), kind, decision_s))
        self._policy_outcomes += 1
        if self._policy_outcomes >= POLICY_EVERY:
            self._policy_outcomes = 0
            self.request_policy()

    # ===================================================================================
    # snapshot pieces
    # ===================================================================================
    def interaction_state(self) -> dict:
        w = self.gesture_window
        now = self.clock()
        win = None
        if w is not None and self.attempt is w["attempt"] and now < w["until"]:
            win = {"kind": "step_confirmation", "remaining_s": round(w["until"] - now, 2), "window_s": w["W"],
                   "yes": "nod", "no": "shake"}
        pd = self.policy_decision or {}
        return {
            "gestures": {
                "enabled": self.gesture_cfg.enabled, "calibrated": self.gesture_cfg.calibrated,
                "calibration_origin": self.gesture_cfg.origin, "ready": self.gestures_ready(),
                "usable": dict(self.gesture_cfg.usable), "active": {k: self.gesture_cfg.active(k) and self.gestures_ready()
                                                                    for k in KINDS},
                "window": win, "events": list(self.gesture_events)[-8:][::-1], "live": self.gesture_live,
                "counters": {"conflicts": self.gesture_conflicts, "blocked_final": self.gesture_blocked_final,
                             "eeg_motion_restarts": self.motion_restarts},
                "mapping": {"nod": "YES (step only)", "shake": "NO (step only)", "tilt": "go back (motion-assisted only)"},
                "rules": ["Gestures never send a message or confirm the final review",
                          "Gestures never trigger urgent messages or emergencies",
                          "Ignored while communication is paused"],
            },
            "interaction": {
                **self.interaction, "modes": self.capability(light=True).modes(),
                "applied_policy": self.applied_policy,
                "policy": None if not pd else {
                    "chosen": pd.get("chosen"), "reason": pd.get("reason"), "readiness": pd.get("readiness"),
                    "baseline_eeg_only": (pd.get("baseline_eeg_only") or {}).get("key"),
                    "top": [{k: c[k] for k in ("key", "mode", "n", "routing", "step_confirmation", "cmpm", "score",
                                                "feasible", "violations")} | {"p_better_than_eeg_only":
                                                                              c.get("p_better_than_eeg_only")}
                            for c in pd.get("candidates", [])[:6]],
                    "capability_claim": pd.get("capability_claim"), "note": pd.get("note")},
                "computing": self._policy_future is not None,
            },
        }


    # ===================================================================================
    # contract v2.0 adapter (Agent B's proposed shapes, issue #1). Derived from the state above;
    # nothing here changes behaviour.
    # ===================================================================================
    METHOD_KIND = {"nod_yes": "nod", "shake_no": "shake", "tilt_nav": "tilt"}

    def contract_v2(self) -> dict:
        now = self.clock()
        eff = self.interaction["effective"]
        modes = self.capability(light=True).modes()
        src = self.source
        lay = src.imu_layout() if src is not None else None
        g = self.calib_results.get("gesture")
        g = g if g and g.get("origin") == self.source_origin() else None
        calibrating = self.calib is not None and self.calib.kind == "gesture"
        n = self.cfg.n_choices
        eeg_cal = self.stopping_for(n).calibrated
        methods = {"eeg_ssvep": {"status": "calibrated" if eeg_cal else "not_calibrated", "enabled": True,
                                 "reason": None if eeg_cal else "default thresholds (no SSVEP calibration on this origin)",
                                 "calibration": None}}
        for key, k in self.METHOD_KIND.items():
            usable = bool(self.gesture_cfg.usable.get(k))
            if lay is None or not self.imu.get("available"):
                st, why = "unavailable", self.imu.get("reason") or "no motion sensor"
            elif calibrating:
                st, why = "calibrating", None
            elif not self.gestures_ready():
                st, why = "not_calibrated", "needs a gesture calibration on this data origin"
            elif not usable:
                st, why = "unreliable", "not reliably detected in calibration"
            elif not self.gesture_cfg.enabled:
                st, why = "disabled", "caregiver has not enabled gesture input"
            else:
                st, why = "calibrated", None
            in_mode = eff == "motion_assisted" if k == "tilt" else eff in ("hybrid", "motion_assisted")
            enabled = st == "calibrated" and in_mode
            if st == "calibrated" and not in_mode:
                why = f"not used in {eff} mode"
            cal = None
            if g and g.get("estimates", {}).get(k):
                e = g["estimates"][k]
                fa = e["false_activations"]
                cal = {"origin": g.get("origin"), "claim": claim_for(g.get("origin", "none")), "attempts": e["cues"],
                       "detected": e["hits"], "rest_s": fa["exposure_s"], "rest_false_triggers": fa["events"],
                       "false_per_hour_upper95": fa.get("upper95_per_hour"),
                       "median_latency_s": e["latency_s"]["mean"], "confidence": e["sensitivity_loo"],
                       "verdict": "pass" if g["usable"].get(k) else ("unreliable" if e["hits"] else "fail"),
                       "at": g.get("time")}
            methods[key] = {"status": st, "enabled": enabled, "reason": why, "calibration": cal}
        motion = self.imu.get("motion") or {}
        a = self.attempt
        contaminated = bool(a is not None and src is not None and self._last_motion_sample >= a.start_sample)
        imu = {"present": lay is not None, "streaming": bool(self.imu.get("available")),
               "origin": self.source_origin(), "claim": claim_for(self.source_origin()),
               "moving": bool(motion.get("moving")),
               "motion_level": (round(min(1.0, motion["angular_speed_dps"] / (2 * motion["gate_dps"])), 2)
                                if motion.get("gate_dps") else None),
               "eeg_contaminated": contaminated, "seconds_since_last_sample": self.quality.get("age_s"),
               "reason": self.imu.get("reason")}
        last = None
        if self.gesture_events:
            e = self.gesture_events[-1]
            act = e.get("action") or ""
            used = "yes" if "(YES)" in act else "no" if "(NO)" in act else "nav" if "back" in act else None
            last = {"gesture": e.get("kind"), "confidence": None, "accepted": bool(e.get("accepted")),
                    "used_for": used, "reason": act or e.get("reason"), "time": e.get("time")}
        gc = None
        if calibrating:
            c = self.calib
            step = c.plan[c.idx] if c.idx < len(c.plan) else None
            sub = ("computing" if step is None or c.sub == "computing" else
                   "rest" if step in ("neutral", "natural") else "cue")
            gc = {"gesture": step if step in KINDS else None, "sub": sub, "step_label": step,
                  "trial": sum(1 for s in c.plan[:c.idx] if s in KINDS), "trials": sum(1 for s in c.plan if s in KINDS),
                  "step": c.idx + 1, "steps": len(c.plan), "elapsed": round(now - c.sub_since, 2),
                  "durations": {"rest": GESTURE_NEUTRAL_S, "natural": GESTURE_NATURAL_S, "cue": dict(GESTURE_CUE_S),
                                "feedback": 0.0},
                  "last_trial": None}
        avail = [m for m in ("eeg_only", "hybrid", "motion_assisted") if modes.get(m)]
        if not (self.gesture_cfg.enabled and self.gestures_ready()):
            avail = ["eeg_only"]
        inp = {"mode": eff, "requested": self.interaction["requested"], "modes_available": avail,
               "mode_reason": self.interaction["reason"], "methods": methods, "imu": imu, "last_gesture": last,
               "gesture_calibration": gc}
        # ---- policy for the CURRENT action ---------------------------------------------------
        kind = a.kind if a is not None else ("navigate" if self.phase.value == "IDLE" else "choose")
        opts = [{"method": "eeg_ssvep", "allowed": True, "expected_latency_s": None, "confidence": None,
                 "reason": "always available"}]
        cons = ["final message: EEG 'Yes, send it' required (gestures never send)",
                "gestures never trigger urgent messages or emergencies",
                "conflicting EEG/gesture answers: choice not accepted"]
        gw = self.gesture_window_s() if self.gestures_ready() else None
        for key, k in self.METHOD_KIND.items():
            m = methods[key]
            if kind == "review":
                allowed, why = False, "not for final SEND"
            elif kind == "confirm" and k in ("nod", "shake"):
                allowed, why = m["enabled"], m["reason"] or "step YES/NO"
            elif kind in ("navigate", "choose") and k == "tilt":
                allowed, why = m["enabled"], m["reason"] or "sustained tilt = back"
            else:
                allowed, why = False, "not used for this action"
            calm = m["calibration"] or {}
            opts.append({"method": key, "allowed": allowed, "expected_latency_s": calm.get("median_latency_s"),
                         "confidence": calm.get("confidence"), "reason": why,
                         "window_s": gw if allowed and k != "tilt" else None})
        selected = "eeg_ssvep"
        if kind == "confirm" and self.gesture_window_open():
            selected = "nod_yes"  # the EEG YES/NO still runs in parallel as the fallback
        elif kind == "confirm" and eff != "eeg_only":
            selected = "eeg_fallback"
        pd = self.policy_decision or {}
        pol = {"action": kind, "selected": selected, "options": opts, "constraints": cons,
               "source": "calibrated" if (eeg_cal and self.gestures_ready()) else "assumption",
               "optimiser": None if not pd else {"chosen": (pd.get("chosen") or {}).get("key"),
                                                 "reason": pd.get("reason"), "applied": self.applied_policy,
                                                 "auto": self.cfg.interaction_mode == "auto"}}
        return {"input": inp, "policy": pol}
