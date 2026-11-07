"""NeuroSteno engine: selection state machine + decoding orchestration.

State machine (per selection):

    IDLE --rest--> EVIDENCE_COLLECTION --reliable--> PREDICTION_HIGHLIGHTED --yes--> ACCEPTED --> next screen / MESSAGE_REVIEW
                        |                                   |   \\--no / blink / caregiver--> REJECTED --> same screen
                        \\--max window, not reliable--> ABSTAINED --> same screen

    MESSAGE_REVIEW --yes--> MESSAGE_SENT --> Home        (final confirmation is ALWAYS explicit)
                   --no---> same decision screen the phrase was chosen on
    any --signal lost / repeated abstention--> COMM_UNAVAILABLE (paused; nothing is accepted)

Safety-relevant rules implemented here:
  * A prediction is only highlighted; it changes nothing until confirmed (explicit policy) or
    until a correction window passes with good signal (provisional policy, opt-in, marked).
  * Missing input never counts as approval for a final message: review abstentions just retry.
  * Rejection returns to the same screen (navigation position preserved).
  * Context never changes decoder output.
  * Nonresponse never triggers an emergency.
"""

from __future__ import annotations

import concurrent.futures as cf
import enum
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .acquisition.base import EEGSource, SourceKind
from .acquisition.replay import SessionRecorder
from .acquisition.simulated import SimulatedSource
from .config import RECORDINGS_DIR, EngineConfig
from .context import Profile, ProfileStore, rank, shortcuts
from .navigation import BACK_KEY, Navigator, Slot
from .phrases.catalog import Catalog, Node
from .adaptive import OnlineTracker, Rel, Timing, choose_n, live_estimates
from .routing import RoutingPlan, plan_routing, reliability_for
from .signal.blink import BlinkConfig, OnlineBlinkDetector, calibrate_blinks
from .signal.calibration import Trial, calibrate, trial_quality
from .signal.fbcca import DynamicStopper, FBCCAConfig, StoppingConfig
from .signal.electrodes import (ElectrodeConfig, alpha_reactivity, channel_contacts, channel_spectra,
                                summarize_contacts)
from .signal.quality import QualityConfig, assess_channels, common_mode
from .interaction import InteractionMixin
from .signal.gestures import GestureConfig

log = logging.getLogger(__name__)


class Phase(str, enum.Enum):
    OFF = "OFF"
    IDLE = "IDLE"
    EVIDENCE_COLLECTION = "EVIDENCE_COLLECTION"
    PREDICTION_HIGHLIGHTED = "PREDICTION_HIGHLIGHTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    ABSTAINED = "ABSTAINED"
    MESSAGE_REVIEW = "MESSAGE_REVIEW"
    MESSAGE_SENT = "MESSAGE_SENT"
    COMM_UNAVAILABLE = "COMM_UNAVAILABLE"
    CALIBRATING = "CALIBRATING"


@dataclass
class Target:
    key: str
    label: str
    freq: float


@dataclass
class Attempt:
    kind: str  # "choose" | "confirm" | "review"
    targets: list[Target]
    start_sample: int
    stopper: DynamicStopper
    started: float
    mistake: bool = False
    last: dict | None = None
    motion_restarts: int = 0


@dataclass
class CalibSession:
    kind: str  # "ssvep" | "blink" | "alpha" | "gesture"
    n: int = 2
    freqs: list[float] = field(default_factory=list)
    plan: list = field(default_factory=list)  # ssvep: label|None per trial ; blink: "relax" then "blink" cues
    idx: int = 0
    sub: str = "cue"
    sub_since: float = 0.0
    stim_start: int = 0
    trials: list = field(default_factory=list)
    future: cf.Future | None = None
    error: str | None = None
    relax_eeg: np.ndarray | None = None
    windows: list = field(default_factory=list)
    max_extra: int = 0
    rejected: int = 0
    stim_dropped: int = 0
    stim_problem: str | None = None

    CUE_S = 1.5
    STIM_S = 4.0
    BREAK_S = 1.0
    RELAX_S = 20.0
    BLINK_CUE_S = 2.5
    EYES_S = 10.0


class Engine(InteractionMixin):
    def __init__(self, catalog: Catalog, store: ProfileStore, cfg: EngineConfig | None = None,
                 clock=time.monotonic):
        self.catalog = catalog
        self.store = store
        self.cfg = cfg or EngineConfig()
        self.clock = clock
        self.fbcca = FBCCAConfig()
        self.qcfg = QualityConfig()
        self.source: EEGSource | None = None
        self.phase = Phase.OFF
        self.phase_since = clock()
        self.attempt: Attempt | None = None
        self.highlight: dict | None = None
        self.feedback: dict | None = None
        self.pending_leaf: Node | None = None
        self.provisional_deadline: float | None = None
        self.confirm_abstains = 0
        self.consecutive_abstains = 0
        self.outcomes: deque = deque(maxlen=10)
        self.last_adaptation: dict | None = None
        self.tracker = OnlineTracker()
        self.unconfirmed_send_blocked = 0  # invariant counter: must stay 0
        self.adapt_since_switch = 0
        self.adapt_model: dict | None = None
        self.sent: list[dict] = []
        self.events: deque = deque(maxlen=200)
        self.comm = {"available": False, "reason": "No EEG source selected", "needs_caregiver": False}
        self._resume_to: Phase | None = None
        self._good_since: float | None = None
        self.quality: dict = {}
        self._last_quality = 0.0
        self.ecfg = ElectrodeConfig(line_hz=self.qcfg.line_hz)
        self.electrodes: dict | None = None
        self._last_electrodes = 0.0
        self.blink_cfg = BlinkConfig(channel_labels=list(self.cfg.blink_labels))
        self.blink_detector: OnlineBlinkDetector | None = None
        self.blink_events: deque = deque(maxlen=20)
        self.emergency: dict | None = None
        self.recorder: SessionRecorder | None = None
        self.calib: CalibSession | None = None
        self.calib_results: dict = {}
        self.stopping: dict[int, StoppingConfig] = {}
        self._executor = cf.ThreadPoolExecutor(max_workers=1)
        self._plan_executor = cf.ThreadPoolExecutor(max_workers=1)
        self.plan: RoutingPlan | None = None
        self._plan_future: cf.Future | None = None
        self._plan_pending: RoutingPlan | None = None
        self.task: dict | None = None
        self.metrics: list[dict] = store.metrics()
        self.intended: str | None = None  # message the patient was asked to produce (experiments/demo)
        self.sim_rng = np.random.default_rng(0)
        self.no_control_windows = 0  # evidence windows that ended at rest level (no request)
        # simulated patient
        self.sim_gaze_key: str | None = None
        self.sim_autopilot: str | None = None
        self.sim_pending_mistake = False
        self.version = 0
        self._init_interaction()
        self.profile: Profile
        self.set_profile("new-patient")

    # ===================================================================================
    # profile / context
    # ===================================================================================
    def set_profile(self, profile_id: str) -> None:
        self.profile = self.store.load(profile_id, self.history_origin())
        self.nav = Navigator(self.catalog)
        self.task = None
        self._load_calibration()
        self._cancel_attempt()
        self.pending_leaf = None
        self._rerank()
        self._enter(Phase.IDLE if self.source else Phase.OFF)
        self.log("profile", f"Profile loaded: {self.profile.display_name}")

    def _rerank(self) -> None:
        self.suggestions = rank(self.profile, self.catalog)
        self.shortcut_list = shortcuts(self.suggestions, self.catalog)
        self.request_plan()

    def _refresh_context(self) -> None:
        """Re-rank after a confirmed message. Applied on returning Home, so positions are
        stable while the patient is mid-navigation."""
        tags = self.profile.session_context.get("tags", [])
        self.profile = self.store.load(self.profile.id, self.history_origin())
        self.profile.session_context["tags"] = tags
        self._rerank()

    def set_session_tags(self, tags: list[str]) -> None:
        self.profile.session_context["tags"] = list(tags)
        self._rerank()
        self.log("context", f"Session context set to {tags or 'none'}")

    # ---- risk-aware semantic routing -------------------------------------------------
    def request_plan(self) -> None:
        """Recompute the routing plan in the background (deterministic, explainable)."""
        args = (self.catalog, list(self.shortcut_list), self.cfg.n_choices, self.cfg.routing_mode,
                dict(self.calib_results), self.source_origin(), self.cfg.rest_s, self.cfg.feedback_s,
                self.allowed_choices(), self.cfg.policy)
        self._plan_future = self._plan_executor.submit(plan_routing, *args)

    def plan_now(self) -> None:
        """Synchronous variant (tests / headless runs)."""
        self.request_plan()
        self._plan_pending = self._plan_future.result()
        self._plan_future = None
        self._apply_plan(force=True)

    def _apply_plan(self, force: bool = False) -> None:
        plan = self._plan_pending
        if plan is None:
            return
        at_home = len(self.nav.stack) == 1 and self.pending_leaf is None
        safe_phase = self.phase in (Phase.IDLE, Phase.OFF, Phase.COMM_UNAVAILABLE, Phase.ABSTAINED)
        if not force and not (at_home and safe_phase):
            return  # wait: never rearrange a screen the patient is in the middle of using
        self._plan_pending = None
        self.plan = plan
        self.nav.shortcuts = list(plan.shortcuts)
        self.nav.urgent = list(plan.urgent)
        self.version += 1
        self.log("routing", f"Routing plan applied ({plan.mode}, {plan.n} targets): shortcuts {plan.shortcuts or 'none'}, "
                 f"verification {plan.verification['policy']} ({plan.verification['reason']}), "
                 f"E[selections] {plan.expected.get('selections')} vs static {plan.expected.get('static_selections')}")

    @property
    def effective_policy(self) -> str:
        if self.cfg.policy == "auto":
            return self.plan.verification["policy"] if self.plan else "explicit"
        return self.cfg.policy

    # ---- task metrics -------------------------------------------------------------------
    def _task_start(self) -> None:
        if self.task is not None:
            return
        self.task = {
            "started": self.clock(), "wall_start": time.time(), "profile": self.profile.id,
            "origin": self.source_origin(), "source": self.source.kind.value if self.source else "none",
            "routing_mode": self.cfg.routing_mode, "n_start": self.cfg.n_choices,
            "policy": self.effective_policy, "intended": self.intended or self.sim_autopilot,
            "selections": 0, "back_selections": 0, "rejected_predictions": 0, "abstentions": 0,
            "decoder_decisions": 0, "review_rejections": 0, "caregiver_actions": 0, "provisional_accepts": 0,
            "injected_mistakes": 0,
        }

    def _task_count(self, key: str, k: int = 1) -> None:
        if self.task is not None:
            self.task[key] = self.task.get(key, 0) + k

    def _task_finish(self, leaf: Node) -> None:
        t = self.task
        if t is None:
            return
        t["completion_s"] = round(self.clock() - t["started"], 2)
        t["final"] = leaf.id
        t["final_text"] = leaf.phrase
        t["correct"] = None if not t["intended"] else (t["intended"] == leaf.id)
        t["n_end"] = self.cfg.n_choices
        t["confirmed_at"] = time.time()
        t.pop("started", None)
        self.metrics.append(t)
        self.store.append_metric(t)
        self.task = None

    def metrics_summary(self) -> list[dict]:
        groups: dict[tuple, list[dict]] = {}
        for m in self.metrics:
            groups.setdefault((m["origin"], m.get("source", "unknown"), m["routing_mode"], m["profile"],
                               m.get("n_start")), []).append(m)
        out = []
        for (origin, source, mode, prof, n), ms in sorted(groups.items(), key=lambda kv: [str(x) for x in kv[0]]):
            known = [m for m in ms if m["correct"] is not None]
            out.append({
                "origin": origin, "source": source, "routing_mode": mode, "profile": prof, "n": n, "messages": len(ms),
                "mean_selections": round(float(np.mean([m["selections"] for m in ms])), 2),
                "mean_rejected_predictions": round(float(np.mean([m["rejected_predictions"] for m in ms])), 2),
                "mean_abstentions": round(float(np.mean([m["abstentions"] for m in ms])), 2),
                "mean_completion_s": round(float(np.mean([m["completion_s"] for m in ms])), 1),
                "correct_final": f"{sum(1 for m in known if m['correct'])}/{len(known)}" if known else "unknown",
            })
        return out

    # ===================================================================================
    # calibration persistence
    # ===================================================================================
    def _calib_path(self) -> Path:
        return self.store.runtime_path(f"calibration_{self.profile.id}.json")

    def _load_calibration(self) -> None:
        self.calib_results = {}
        self.stopping = {}
        self.gesture_cfg = GestureConfig()
        p = self._calib_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            for key, res in data.items():
                self.calib_results[key] = res
                if key.startswith("ssvep_") and "stopping" in res:
                    self.stopping[int(key.split("_")[1])] = StoppingConfig.from_dict(res["stopping"])
                if key == "gesture":
                    self._load_gesture_calibration(res)
                if key == "blink" and res.get("separable"):
                    self.blink_cfg.threshold_uv = res["threshold_uv"]
                    self.blink_cfg.calibrated = True

    def _save_calibration(self) -> None:
        self._calib_path().write_text(json.dumps(self.calib_results, indent=1), encoding="utf-8")

    def stopping_for(self, n: int) -> StoppingConfig:
        """Calibrated thresholds apply only to the data origin they were measured on: a
        simulated calibration must never unlock REAL mode (and vice versa)."""
        st = self.stopping.get(n)
        res = self.calib_results.get(f"ssvep_{n}")
        if st is not None and res and res.get("origin") == self.source_origin():
            return st
        return StoppingConfig()

    def allowed_choices(self) -> list[int]:
        if self.source is None or self.source.kind == SourceKind.SIMULATED:
            return [2, 3, 5]
        out = [2]
        for n in (3, 5):
            res = self.calib_results.get(f"ssvep_{n}")
            if res and res.get("supported") and res.get("origin") == self.source_origin():
                out.append(n)
        return out

    def history_origin(self) -> str:
        """Origin key for the patient's confirmed-message history. A replay re-decodes old EEG,
        so messages it "sends" were never sent by the patient now: keep them out of the live
        real/simulated history that drives context ranking."""
        if self.source is not None and self.source.kind == SourceKind.REPLAY:
            return "replay"
        return self.source_origin()

    def calibration_required(self) -> bool:
        """Real-origin EEG (live or replayed) must be calibrated on real data before use."""
        return self.cfg.require_calibration_real and self.source_origin() == "real"

    def source_origin(self) -> str:
        if self.source is None:
            return "none"
        if self.source.kind == SourceKind.REPLAY:
            return str(self.source.status.extra.get("origin", "unknown"))
        return self.source.kind.value

    # ===================================================================================
    # sources
    # ===================================================================================
    def set_source(self, src: EEGSource | None) -> None:
        if self.recorder:
            self.stop_recording()
        if self.source is not None:
            try:
                self.source.stop()
            except Exception as e:
                self.log("error", f"Error stopping source: {e}")
        self._cancel_attempt()
        self.calib = None
        self.source = src
        self.quality = {}
        self.electrodes = None
        self.sim_gaze_key = None
        self.sim_autopilot = None
        self.sim_pending_mistake = False
        self.consecutive_abstains = 0
        if src is None:
            self.comm = {"available": False, "reason": "No EEG source selected", "needs_caregiver": False}
            self._interaction_on_source(None)
            self._enter(Phase.OFF)
            return
        src.start()
        self.blink_detector = OnlineBlinkDetector(self.blink_cfg, src.sample_rate)
        self.blink_detector.processed_until = src.total_samples
        self._interaction_on_source(src)
        if self.cfg.n_choices not in self.allowed_choices():
            self.cfg.n_choices = 2
        self.task = None
        self._refresh_context()
        # Start paused until the signal-quality monitor has seen good data.
        self._enter_comm_unavailable("Waiting for good EEG signal", needs_caregiver=False)
        self.log("source", f"{src.kind.value.upper()} source started: {src.status.message}")

    @property
    def sim(self) -> SimulatedSource | None:
        return self.source if isinstance(self.source, SimulatedSource) else None

    # ===================================================================================
    # main loop
    # ===================================================================================
    def tick(self) -> None:
        now = self.clock()
        if self._plan_future is not None and self._plan_future.done():
            try:
                self._plan_pending = self._plan_future.result()
            except Exception as e:
                self.log("routing", f"Routing plan failed: {e}")
            self._plan_future = None
        if self._plan_pending is not None:
            self._apply_plan()
        self._poll_policy()
        src = self.source
        if src is not None:
            src.poll()
            if self.recorder is not None:
                self.recorder.flush()
            if now - self._last_quality >= 0.25:
                self._update_quality(now)
                self._update_blinks()  # 4 Hz is plenty; the detector looks back over 3 s
                self._update_imu(now)  # motion channels + optional gestures (no-op without IMU)
            if now - self._last_electrodes >= 1.0:
                self._update_electrodes(now)
        if self.emergency:
            return
        if self.calib is not None:
            self._tick_calibration(now)
            return
        self._check_comm(now)
        ph = self.phase
        if ph == Phase.IDLE and now - self.phase_since >= self.cfg.rest_s:
            self._start_attempt("choose")
        elif ph in (Phase.EVIDENCE_COLLECTION, Phase.PREDICTION_HIGHLIGHTED, Phase.MESSAGE_REVIEW):
            self._update_sim_gaze()
            self._process_attempt()
            if self.phase == Phase.PREDICTION_HIGHLIGHTED and self.provisional_deadline and now >= self.provisional_deadline:
                self.log("decision", "Correction window elapsed with good signal: PROVISIONAL accept (undo available)")
                self._accept(provisional=True)
        elif ph in (Phase.ACCEPTED, Phase.REJECTED, Phase.ABSTAINED) and now - self.phase_since >= self.cfg.feedback_s:
            self._finish_feedback()
        elif ph == Phase.MESSAGE_SENT and now - self.phase_since >= self.cfg.sent_display_s:
            self.nav.home()
            self._enter(Phase.IDLE)
            self._refresh_context()

    # ===================================================================================
    # signal quality & communication availability
    # ===================================================================================
    def _eeg_cols(self, labels: list[str] | None = None) -> list[int]:
        return self.source.eeg_indices(labels) if self.source else []

    def _update_quality(self, now: float) -> None:
        self._last_quality = now
        src = self.source
        fs = src.sample_rate
        total = src.total_samples
        n = int(self.qcfg.window_s * fs)
        cols = self._eeg_cols()
        labels = [c.label or c.name for c in src.eeg_channels]
        win = src.read_range(max(0, total - n), total)[:, cols] if cols else np.zeros((0, 0))
        chans = assess_channels(win, fs, labels, self.qcfg) if cols else []
        dec_labels = {lbl.lower() for lbl in self.cfg.decoding_labels}
        good_dec = sum(1 for c in chans if c["label"].lower() in dec_labels and c["status"] == "ok")
        cm = common_mode(win, fs) if win.shape[0] >= fs else None
        age = None if src.status.last_sample_time is None else time.monotonic() - src.status.last_sample_time
        stalled = age is None or age > self.cfg.stall_s
        self.quality = {
            "channels": chans,
            "common_mode": None if cm is None else round(cm, 3),
            "stalled": stalled,
            "seconds_since_last_sample": None if age is None else round(age, 2),
            "good_decoding_channels": good_dec,
            "required_decoding_channels": self.qcfg.min_good_decoding_channels,
            "decoding_labels": self.cfg.decoding_labels,
        }
        problem = None
        if stalled:
            problem = "No EEG samples arriving (headset off, out of range, or Bluetooth dropped)"
        elif win.shape[0] < fs:
            problem = "Collecting initial EEG data"
        elif cm is not None and cm > 0.95:
            problem = "All channels carry the same signal: check reference/ground ear clips and headset fit"
        elif good_dec < self.qcfg.min_good_decoding_channels:
            problem = f"Poor signal on decoding channels ({good_dec}/{len(self.cfg.decoding_labels)} usable)"
        self.quality["signal_ok_for_calibration"] = problem is None
        if problem is None and self.calibration_required() and not self.stopping_for(self.cfg.n_choices).calibrated:
            what = "Real EEG" if src.kind == SourceKind.REAL else "Replayed real EEG"
            problem = (f"{what} is not calibrated for {self.cfg.n_choices} targets yet. Run SSVEP calibration "
                       "on real data before communicating (default thresholds come from synthetic data).")
        self.quality["problem"] = problem
        self.quality["signal_ok"] = problem is None

    def _update_electrodes(self, now: float) -> None:
        """Per-electrode contact diagnostics + spectra over the last 4 s (1 Hz). Advisory: the
        communication gate stays in _update_quality."""
        self._last_electrodes = now
        src = self.source
        fs = src.sample_rate
        cols = self._eeg_cols()
        if not cols:
            self.electrodes = None
            return
        labels = [c.label or c.name for c in src.eeg_channels]
        total = src.total_samples
        win = src.read_range(max(0, total - int(4 * fs)), total)[:, cols]
        contacts = channel_contacts(win, fs, labels, self.ecfg)
        for c in contacts:
            c["status"] = c["verdict"]
            c["hint"] = c["advice"][0] if c["advice"] else None
        summary = summarize_contacts(contacts, self.cfg.decoding_labels)
        self.electrodes = {
            "ready": (not summary["reference_off"] and summary["counts"]["no_data"] == 0
                      and len(summary["decoding_usable"]) >= self.qcfg.min_good_decoding_channels),
            "reference_ok": not summary["reference_off"] and summary["counts"]["no_data"] == 0,
            "message": summary["headline"],
            "window_s": round(win.shape[0] / fs, 2),
            "channels": contacts,
            "summary": summary,
            "spectra": channel_spectra(win, fs, labels),
            "note": "Heuristic contact estimate from the signal itself; UnicornPy exposes no impedance.",
        }

    def _check_comm(self, now: float) -> None:
        if self.source is None:
            return
        ok = self.quality.get("signal_ok", False)
        if self.phase == Phase.COMM_UNAVAILABLE:
            if self.comm.get("needs_caregiver"):
                return
            if ok:
                self._good_since = self._good_since or now
                if now - self._good_since >= self.cfg.recover_s:
                    self._resume()
            else:
                self._good_since = None
                self.comm["reason"] = self.quality.get("problem") or self.comm["reason"]
            return
        if not ok and self.phase not in (Phase.OFF, Phase.MESSAGE_SENT):
            self._enter_comm_unavailable(self.quality.get("problem") or "Signal unavailable", needs_caregiver=False)

    def _enter_comm_unavailable(self, reason: str, needs_caregiver: bool) -> None:
        # Pending highlights are discarded, never accepted. A pending final message is kept
        # for review again after recovery, but is not sent.
        self._resume_to = Phase.MESSAGE_REVIEW if self.pending_leaf is not None else Phase.IDLE
        self._cancel_attempt()
        self.comm = {"available": False, "reason": reason, "needs_caregiver": needs_caregiver}
        self._good_since = None
        self._enter(Phase.COMM_UNAVAILABLE)
        self.log("comm", f"Communication paused: {reason}")

    def _resume(self) -> None:
        self.comm = {"available": True, "reason": "", "needs_caregiver": False}
        self.consecutive_abstains = 0
        target = self._resume_to or Phase.IDLE
        self._resume_to = None
        if target == Phase.MESSAGE_REVIEW and self.pending_leaf is not None:
            self._enter(Phase.MESSAGE_REVIEW)
            self._start_attempt("review")
        else:
            self._enter(Phase.IDLE)
        self.log("comm", "Communication resumed")

    # ===================================================================================
    # attempts / decoding
    # ===================================================================================
    def current_slots(self) -> list[Slot]:
        return self.nav.slots(self.cfg.n_choices)

    def _targets_for(self, kind: str) -> list[Target]:
        if kind == "choose":
            slots = self.current_slots()
            freqs = self.cfg.freqs[self.cfg.n_choices]
            return [Target(s.key, s.label, freqs[i]) for i, s in enumerate(slots)]
        yes_label = "Yes, send it" if kind == "review" else "Yes"
        no_label = "No, change it" if kind == "review" else "No, not this"
        return [Target("yes", yes_label, self.cfg.confirm_freqs[0]), Target("no", no_label, self.cfg.confirm_freqs[1])]

    def _start_attempt(self, kind: str) -> None:
        src = self.source
        if src is None:
            return
        targets = self._targets_for(kind)
        # A screen may show fewer slots than n_choices (it then uses the first k frequencies of
        # the n-target set). Use the thresholds calibrated for that frequency set: there is no
        # calibration for k=4, and falling back to the synthetic defaults would be unsafe on REAL.
        n = self.cfg.n_choices if kind == "choose" else 2
        stopper = DynamicStopper([t.freq for t in targets], src.sample_rate, self.stopping_for(n), self.fbcca)
        mistake = False
        if kind == "choose":
            self._task_start()
        if kind == "choose" and self.sim:
            if self.sim_pending_mistake:
                mistake, self.sim_pending_mistake = True, False
            elif self.cfg.sim_mistake_rate > 0 and self.sim_autopilot:
                mistake = bool(self.sim_rng.random() < self.cfg.sim_mistake_rate)
            if mistake:
                self._task_count("injected_mistakes")
        self.attempt = Attempt(kind, targets, src.total_samples, stopper, self.clock(), mistake)
        self.sim_gaze_key = None
        if kind == "choose":
            self._enter(Phase.EVIDENCE_COLLECTION)
        elif kind == "confirm":
            self._arm_gesture_window(self.attempt)  # optional hybrid YES/NO; EEG keeps running in parallel
        self._mark("attempt_start", kind=kind, targets=[t.__dict__ for t in targets])
        self._update_sim_gaze()

    def _process_attempt(self) -> None:
        a, src = self.attempt, self.source
        if a is None or src is None:
            return
        fs = src.sample_rate
        lat = int(self.fbcca.latency_s * fs)
        start = a.start_sample + lat
        avail_s = (src.total_samples - start) / fs
        st = a.stopper.stopping
        if not a.stopper.due(avail_s):
            return
        if self._motion_restart(a):  # head movement inside the EEG window: collect fresh evidence
            return
        win_s = min(st.max_window_s, st.min_window_s + np.floor((avail_s - st.min_window_s) / st.step_s + 1e-9) * st.step_s)
        n = int(round(win_s * fs))
        cols = self._eeg_cols(self.cfg.decoding_labels)
        if len(cols) < 2:
            self._enter_comm_unavailable("Decoding channels not available in this source", needs_caregiver=True)
            return
        x = src.read_range(start, start + n)[:, cols].astype(np.float64)
        pad = src.read_range(max(0, start - int(0.5 * fs)), start)[:, cols].astype(np.float64)
        dec = a.stopper.evaluate(x, pad if pad.shape[0] > 10 else None)
        a.last = dec.to_dict() | {"threshold": round(st.threshold_at(dec.window_s), 3)}
        if dec.reliable:
            self._task_count("decoder_decisions")
            self._mark("decision", kind=a.kind, key=a.targets[dec.target].key, **dec.to_dict())
            self._on_decision(a, dec.target, dec)
        elif a.stopper.exhausted:
            self._mark("abstain", kind=a.kind, **dec.to_dict())
            self._on_abstain(a, dec)

    def _on_decision(self, a: Attempt, idx: int, dec) -> None:
        t = a.targets[idx]
        if a.kind == "choose":
            slots = self.current_slots()
            slot = slots[idx]
            thr = a.stopper.stopping.threshold_at(dec.window_s)
            eligible = self.effective_policy == "provisional" and dec.margin >= thr * self.cfg.provisional_margin_factor
            self.highlight = {"slot_index": idx, "key": slot.key, "label": slot.label, "margin": round(dec.margin, 3),
                              "window_s": round(dec.window_s, 2), "provisional": eligible,
                              "decision_time_s": round(self.clock() - a.started, 2)}
            self.provisional_deadline = (self.clock() + self.cfg.provisional_window_s) if eligible else None
            self.confirm_abstains = 0
            self._enter(Phase.PREDICTION_HIGHLIGHTED)
            gated = (self.effective_policy == "gated" and a.stopper.stopping.calibrated
                     and dec.margin >= thr * self.cfg.gate_margin_factor)
            if gated:
                # High confidence relative to thresholds calibrated on THIS data origin: accept
                # without a per-step YES/NO. The choice is still highlighted (ACCEPTED feedback),
                # Undo/Back stay available, and the final message is always confirmed explicitly.
                self.highlight["gated"] = True
                self.log("decision", f"Predicted '{slot.label}' with high confidence (margin {dec.margin:.2f} >= "
                         f"{self.cfg.gate_margin_factor} x {thr:.2f}): accepted without step confirmation (gated policy)")
                self.attempt = None
                self._accept(provisional=False, gated=True)
                return
            self.log("decision", f"Predicted '{slot.label}' (margin {dec.margin:.2f} at {dec.window_s:.2f} s)"
                     + ("; provisional correction window" if eligible else "; awaiting confirmation"))
            self.attempt = None
            self._start_attempt("confirm")
        elif a.kind == "confirm":
            self._gesture_window_eeg_decided(t.key == "yes")
            if t.key == "yes":
                self.log("decision", "Patient confirmed the highlighted choice")
                self._accept(provisional=False)
            else:
                self._reject("Patient rejected the prediction")
        elif a.kind == "review":
            if t.key == "yes":
                self._send(confirmed_by=a)
            else:
                self._cancel_review("Patient chose to change the message")

    def _on_abstain(self, a: Attempt, dec) -> None:
        if a.kind == "choose":
            self.attempt = None
            if "rest level" in dec.reason:
                # No-control: the response looks like rest (no target attended). This is not a
                # failure and not an emergency; keep listening quietly on the same screen.
                self.no_control_windows += 1
                self.feedback = {"type": "no_control", "label": "Standing by", "reason": dec.reason}
                self._enter(Phase.IDLE)
                return
            self._task_count("abstentions")
            self._evidence("abstain")
            self.outcomes.append(("abstained", None))
            self.consecutive_abstains += 1
            self.feedback = {"type": "abstained", "label": "Not sure what you chose - let's try again", "reason": dec.reason}
            self.log("decision", f"Abstained: {dec.reason}")
            if self.consecutive_abstains >= self.cfg.abstain_pause_after:
                self._enter_comm_unavailable(
                    f"{self.consecutive_abstains} selections in a row were not reliable. A caregiver should check the "
                    "headset and the patient before resuming.", needs_caregiver=True)
                return
            self._enter(Phase.ABSTAINED)
        elif a.kind == "confirm":
            self.confirm_abstains += 1
            if self.provisional_deadline is not None:
                self._start_attempt("confirm")  # keep listening for a correction until the window ends
            elif self.confirm_abstains >= self.cfg.confirm_attempts:
                self._reject("No clear confirmation, so the choice was NOT accepted", kind="not_confirmed")
            else:
                self.log("decision", "Confirmation unclear; asking again")
                self._start_attempt("confirm")
        elif a.kind == "review":
            self.log("decision", "Final confirmation unclear; message NOT sent, asking again")
            self._start_attempt("review")

    def _evidence(self, kind: str, decision_s: float | None = None) -> None:
        """Live evidence for signal-adaptive n: the patient's own YES/NO on a highlight."""
        self.tracker.add(self.cfg.n_choices, kind, decision_s)
        self.adapt_since_switch += 1
        self._note_outcome(kind, decision_s)

    def _accept(self, provisional: bool, gated: bool = False) -> None:
        h = self.highlight or {}
        if not gated:  # a gated accept was not confirmed, so it says nothing about correctness
            self._evidence("correct", h.get("window_s"))
        self.outcomes.append(("accepted", h.get("decision_time_s")))
        self._task_count("selections")
        if provisional:
            self._task_count("provisional_accepts")
        if h.get("key") == BACK_KEY:
            self._task_count("back_selections")
        self.consecutive_abstains = 0
        self.attempt = None
        self.provisional_deadline = None
        if gated:
            self._task_count("gated_accepts")
        self.feedback = {"type": "accepted", "label": h.get("label", ""), "provisional": provisional, "gated": gated}
        self._enter(Phase.ACCEPTED)

    def _reject(self, reason: str, kind: str = "rejected") -> None:
        self._evidence("abstain" if kind in ("not_confirmed", "conflict") else "wrong")
        self.outcomes.append(("rejected", None))
        self._task_count("rejected_predictions")
        self.feedback = {"type": kind, "label": (self.highlight or {}).get("label", ""), "reason": reason}
        self.log("decision", f"{reason}. Staying on the same screen.")
        self.attempt = None
        self.provisional_deadline = None
        self.highlight = None
        self._enter(Phase.REJECTED)

    def _finish_feedback(self) -> None:
        if self.phase == Phase.ACCEPTED:
            h = self.highlight
            self.highlight = None
            slots = self.current_slots()
            slot = next((s for s in slots if h and s.key == h["key"]), None)
            if slot is None:  # screen changed meanwhile; never apply a stale selection
                self._enter(Phase.IDLE)
                return
            leaf = self.nav.select(slot, provisional=bool(self.feedback and self.feedback.get("provisional")))
            self.log("navigation", f"Accepted '{slot.label}'" + (" (provisional)" if self.feedback.get("provisional") else ""))
            if leaf is not None:
                self.pending_leaf = leaf
                self._enter(Phase.MESSAGE_REVIEW)
                self._start_attempt("review")
                return
        self.feedback = None if self.phase != Phase.ABSTAINED else self.feedback
        self._maybe_adapt()
        self._enter(Phase.IDLE)

    def _send(self, confirmed_by: Attempt | None = None) -> None:
        """Only an explicit, decoded YES on the final review may send a message."""
        if confirmed_by is None or confirmed_by.kind != "review":
            self.unconfirmed_send_blocked += 1
            self.log("safety", "Blocked an attempt to send a message without explicit final confirmation")
            return
        leaf = self.pending_leaf
        msg = {
            "text": leaf.phrase, "node_id": leaf.id, "title": leaf.title, "time": time.time(),
            "confirmed_by": "decoded final YES",
            "profile": self.profile.id, "source": self.source.kind.value if self.source else "none",
            "data_origin": self.source_origin(), "urgent": leaf.urgent,
            "provisional_steps": sum(1 for c in self.nav.crumbs if c.provisional),
        }
        self.sent.append(msg)
        self.store.append_confirmed(self.profile.id, leaf.id, leaf.phrase, self.history_origin())
        self._task_finish(leaf)
        self.log("message", f"MESSAGE CONFIRMED by patient: \"{leaf.phrase}\"")
        self._mark("message_sent", text=leaf.phrase, node=leaf.id)
        if self.sim_autopilot == leaf.id:
            self.sim_autopilot = None
        self.pending_leaf = None
        self.attempt = None
        self.feedback = {"type": "sent", "label": leaf.phrase}
        self._enter(Phase.MESSAGE_SENT)

    def _cancel_review(self, reason: str) -> None:
        self._task_count("review_rejections")
        self.nav.leaf_cancelled()
        self.pending_leaf = None
        self.attempt = None
        self.feedback = {"type": "rejected", "label": "Message not sent", "reason": reason}
        self.log("message", f"{reason}; message discarded, back to the previous screen")
        self._enter(Phase.REJECTED)

    def _cancel_attempt(self) -> None:
        self.attempt = None
        self.highlight = None
        self.provisional_deadline = None
        self.sim_gaze_key = None
        if self.sim:
            self.sim.set_gaze(None)

    def _enter(self, phase: Phase) -> None:
        if phase != self.phase:
            self._mark("phase", phase=phase.value)
        self.phase = phase
        self.phase_since = self.clock()
        if phase == Phase.IDLE:
            self.comm = {"available": True, "reason": "", "needs_caregiver": False}
            if self.sim:
                self.sim.set_gaze(None)
        self.version += 1

    # ===================================================================================
    # adaptive choice count
    # ===================================================================================
    def _priors(self) -> dict[int, Rel]:
        """Per-n reliability before live evidence: calibration on THIS data origin, else the
        labelled assumptions from routing.ASSUMED."""
        out = {}
        for k in (2, 3, 5):
            r = reliability_for(k, self.calib_results, self.source_origin())
            res = self.calib_results.get(f"ssvep_{k}") or {}
            ev = res.get("n_cued_trials", 0) if r.source.startswith("calibrated") else 0
            out[k] = Rel(k, r.accuracy, r.abstain, r.decision_s, self.stopping_for(k).max_window_s, r.source, ev)
        return out

    def adaptation_model(self) -> dict | None:
        """Expected correctly-confirmed messages per minute for each allowed n (adaptive.py)."""
        allowed = [k for k in (2, 3, 5) if k in self.allowed_choices()]
        by_n = (self.plan.recommended_n or {}).get("by_n", {}) if self.plan else {}
        exp_sel = {k: by_n[k]["expected_selections"] for k in allowed if k in by_n}
        if not exp_sel or self.cfg.n_choices not in exp_sel:
            return None
        priors = self._priors()
        est = live_estimates(priors, self.tracker, self.cfg.n_choices)
        tm = Timing(self.cfg.rest_s, self.cfg.feedback_s, self.cfg.sent_display_s, self.cfg.confirm_attempts)
        dec = choose_n({k: est[k] for k in exp_sel}, priors[2], exp_sel, tm, sorted(exp_sel), step_confirm=True,
                       current=self.cfg.n_choices, hysteresis=self.cfg.adapt_hysteresis)
        dec["estimates"] = {k: est[k].to_dict() for k in exp_sel}
        dec["evidence"] = {k: self.tracker.counts(k) for k in (2, 3, 5) if self.tracker.counts(k)["total"]}
        dec["evidence_since_switch"] = self.adapt_since_switch
        dec["min_evidence"] = self.cfg.adapt_min_evidence
        dec["note"] = ("Model estimate (adaptive.py) from calibration on this data origin plus the patient's own "
                       "YES/NO answers; not a measured throughput.")
        return dec

    def _maybe_adapt(self) -> None:
        """Signal-adaptive target count: re-optimise expected correctly-confirmed messages per
        minute after enough live evidence. Fewer targets are NOT assumed to be better."""
        try:
            self.adapt_model = self.adaptation_model()
        except Exception as e:  # never let the optimiser break communication
            self.log("adapt", f"Adaptation model failed: {e}")
            return
        m = self.adapt_model
        if not self.cfg.auto_adapt or m is None or self.adapt_since_switch < self.cfg.adapt_min_evidence:
            return
        cur, new = self.cfg.n_choices, m["n"]
        if new == cur:
            return
        bn = m["by_n"]
        why = (f"expected correctly-confirmed messages/min {cur} targets: {bn[cur]['cmpm']:.2f} -> "
               f"{new} targets: {bn[new]['cmpm']:.2f} (model, {self.adapt_since_switch} live outcomes)")
        self.cfg.n_choices = new
        self.adapt_since_switch = 0
        self.outcomes.clear()
        self.request_plan()
        self.last_adaptation = {"from": cur, "to": new, "reason": why, "time": time.time()}
        self.log("adapt", f"Choices per screen {cur} -> {new}: {why}")

    # ===================================================================================
    # simulated patient (SIMULATED mode only)
    # ===================================================================================
    def _desired_target_index(self) -> int | None:
        a = self.attempt
        if a is None:
            return None
        if self.sim_gaze_key is not None:
            return next((i for i, t in enumerate(a.targets) if t.key == self.sim_gaze_key), None)
        if self.sim_autopilot is None:
            return None
        if a.kind == "choose":
            return self.nav.slot_toward(self.current_slots(), self.sim_autopilot)
        if a.kind == "confirm":
            want = self.nav.slot_toward(self.current_slots(), self.sim_autopilot)
            return 0 if self.highlight and self.highlight["slot_index"] == want else 1
        if a.kind == "review":
            return 0 if self.pending_leaf is not None and self.pending_leaf.id == self.sim_autopilot else 1
        return None

    def _update_sim_gaze(self) -> None:
        sim = self.sim
        if sim is None:
            return
        idx = self._desired_target_index()
        a = self.attempt
        if idx is None or a is None:
            sim.set_gaze(None)
            return
        if a.mistake:
            idx = (idx + 1) % len(a.targets)  # virtual patient's EEG responds to the wrong target
        sim.set_gaze(a.targets[idx].freq)

    # ===================================================================================
    # blinks (experimental)
    # ===================================================================================
    def _update_blinks(self) -> None:
        src, det = self.source, self.blink_detector
        if src is None or det is None:
            return
        cols = self._eeg_cols(self.blink_cfg.channel_labels)
        for ev in det.update(src.read_range, src.total_samples, cols):
            self.blink_events.append(ev | {"time": time.time()})
            if not self.blink_cfg.enabled:
                continue
            if ev["accepted"] and self.phase == Phase.PREDICTION_HIGHLIGHTED and self.calib is None:
                self._reject("Deliberate blink detected (experimental blink-to-reject)")
            elif not ev["accepted"]:
                self.log("blink", f"Blink-like event ignored: {ev['reason']} ({ev['amplitude_uv']} µV)")

    # ===================================================================================
    # calibration sessions
    # ===================================================================================
    def start_calibration(self, n: int, trials_per_target: int = 5, rest_trials: int = 8) -> None:
        if self.source is None:
            raise ValueError("Select an EEG source first")
        if n not in self.cfg.freqs:
            raise ValueError(f"no frequency set for {n} targets")
        rng = np.random.default_rng()
        plan: list = [i for i in range(n) for _ in range(trials_per_target)] + [None] * rest_trials
        rng.shuffle(plan)
        self._cancel_attempt()
        self.calib = CalibSession("ssvep", n=n, freqs=list(self.cfg.freqs[n]), plan=plan, sub_since=self.clock())
        self.calib.max_extra = len(plan) // 2  # re-queued trials allowed after quality rejections
        self._enter(Phase.CALIBRATING)
        self.log("calibration", f"SSVEP calibration started: {n} targets, {len(plan)} trials")

    def start_blink_calibration(self, cues: int = 8) -> None:
        if self.source is None:
            raise ValueError("Select an EEG source first")
        self._cancel_attempt()
        self.calib = CalibSession("blink", plan=["relax"] + ["blink"] * cues, sub="relax", sub_since=self.clock(),
                                  stim_start=self.source.total_samples)
        self._enter(Phase.CALIBRATING)
        self.log("calibration", "Blink calibration started (experimental)")

    def start_alpha_check(self, cycles: int = 2) -> None:
        """Guided eyes-open / eyes-closed check (Berger effect): proves physiological EEG is
        being recorded, independent of the stimulus display and of SSVEP."""
        if self.source is None:
            raise ValueError("Select an EEG source first")
        self._cancel_attempt()
        plan = ["eyes_open", "eyes_closed"] * max(1, int(cycles))
        self.calib = CalibSession("alpha", plan=plan, sub=plan[0], sub_since=self.clock(),
                                  stim_start=self.source.total_samples)
        self._enter(Phase.CALIBRATING)
        self._mark("alpha_segment", label=plan[0])
        self.log("calibration", f"Eyes-open/eyes-closed alpha check started ({len(plan)} x {CalibSession.EYES_S:.0f} s)")

    def cancel_calibration(self) -> None:
        if self.calib:
            self.log("calibration", "Calibration cancelled")
        self.calib = None
        if self.sim:
            self.sim.set_gaze(None)
            self.sim.eyes_closed = False
        self._enter_comm_unavailable("Re-checking signal after calibration", needs_caregiver=False)

    def _tick_calibration(self, now: float) -> None:
        c, src = self.calib, self.source
        if src is None:
            self.calib = None
            return
        if c.future is not None:
            if c.future.done():
                self._finish_calibration()
            return
        if self.quality.get("stalled"):
            c.error = "EEG stream stalled during calibration"
            self.calib = None
            self._enter_comm_unavailable("EEG stream stalled during calibration", needs_caregiver=False)
            return
        elapsed = now - c.sub_since
        fs = src.sample_rate
        if c.kind == "gesture":
            self._tick_gesture_calibration(now)
            return
        if c.kind == "ssvep" and c.sub == "stim" and not self.quality.get("signal_ok_for_calibration", True):
            c.stim_problem = c.stim_problem or self.quality.get("problem")
        if c.kind == "ssvep":
            label = c.plan[c.idx]
            if c.sub == "cue" and elapsed >= c.CUE_S:
                c.sub, c.sub_since, c.stim_start = "stim", now, src.total_samples
                c.stim_dropped = src.status.dropped_samples
                c.stim_problem = None
                if self.sim:
                    self.sim.set_gaze(None if label is None else c.freqs[label])
                self._mark("calib_trial", label=label, freq=None if label is None else c.freqs[label])
            elif c.sub == "stim" and elapsed >= c.STIM_S:
                lat = int(self.fbcca.latency_s * fs)
                cols = self._eeg_cols(self.cfg.decoding_labels)
                s0 = c.stim_start + lat
                eeg = src.read_range(s0, c.stim_start + int(c.STIM_S * fs))[:, cols].astype(np.float64)
                pad = src.read_range(max(0, s0 - int(0.5 * fs)), s0)[:, cols].astype(np.float64)
                tr = Trial(eeg, label, pad)
                if src.status.dropped_samples > c.stim_dropped:
                    tr.flags.append(f"{src.status.dropped_samples - c.stim_dropped} dropped samples")
                if c.stim_problem:
                    tr.flags.append(f"signal problem: {c.stim_problem}")
                why = trial_quality(tr, fs)
                if why and c.max_extra > 0:
                    c.max_extra -= 1
                    c.rejected += 1
                    c.plan.append(label)  # re-run this target later
                    self.log("calibration", f"Trial {c.idx + 1} rejected ({'; '.join(why)}); re-queued")
                elif why:
                    c.rejected += 1
                    self.log("calibration", f"Trial {c.idx + 1} rejected ({'; '.join(why)}); no re-runs left")
                c.trials.append(tr)  # calibrate() applies the same gate and reports it
                if self.sim:
                    self.sim.set_gaze(None)
                c.sub, c.sub_since = "break", now
            elif c.sub == "break" and elapsed >= c.BREAK_S:
                c.idx += 1
                if c.idx >= len(c.plan):
                    c.sub = "computing"
                    c.future = self._executor.submit(calibrate, c.trials, c.freqs, fs, self.fbcca,
                                                     1.0, CalibSession.STIM_S - self.fbcca.latency_s - 0.1, 0.25)
                else:
                    c.sub, c.sub_since = "cue", now
        elif c.kind == "alpha":
            if c.sub in ("eyes_open", "eyes_closed") and elapsed >= c.EYES_S:
                cols = self._eeg_cols()
                c.windows.append((c.sub, src.read_range(c.stim_start, src.total_samples)[:, cols].astype(np.float64)))
                c.idx += 1
                if c.idx >= len(c.plan):
                    if self.sim:
                        self.sim.eyes_closed = False
                    c.sub = "computing"
                    labels = [ch.label or ch.name for ch in src.eeg_channels]
                    c.future = self._executor.submit(
                        alpha_reactivity, [w for k, w in c.windows if k == "eyes_open"],
                        [w for k, w in c.windows if k == "eyes_closed"], fs, labels, self.ecfg)
                else:
                    c.sub, c.sub_since, c.stim_start = c.plan[c.idx], now, src.total_samples
                    self._mark("alpha_segment", label=c.sub)
            if self.sim:
                self.sim.eyes_closed = c.sub == "eyes_closed"
        else:  # blink
            cols = self._eeg_cols(self.blink_cfg.channel_labels)
            if c.sub == "relax":
                if self.sim and int(elapsed * 4) % 16 == 5 and int((elapsed - 0.25) * 4) % 16 != 5:
                    self.sim.inject_blink(deliberate=False)  # synthetic natural blinks
                if elapsed >= c.RELAX_S:
                    c.relax_eeg = src.read_range(c.stim_start, src.total_samples)[:, cols].astype(np.float64)
                    c.idx, c.sub, c.sub_since, c.stim_start = 1, "blink", now, src.total_samples
                    if self.sim:
                        self.sim.inject_blink(deliberate=True)
            elif c.sub == "blink" and elapsed >= c.BLINK_CUE_S:
                c.windows.append(src.read_range(c.stim_start, src.total_samples)[:, cols].astype(np.float64))
                c.idx += 1
                if c.idx >= len(c.plan):
                    c.sub = "computing"
                    c.future = self._executor.submit(calibrate_blinks, c.relax_eeg, c.windows, fs, self.blink_cfg)
                else:
                    c.sub, c.sub_since, c.stim_start = "blink", now, src.total_samples
                    if self.sim:
                        self.sim.inject_blink(deliberate=True)

    def _finish_calibration(self) -> None:
        c = self.calib
        try:
            res = c.future.result()
        except Exception as e:
            self.log("calibration", f"Calibration failed: {e}")
            self.calib_results[f"last_error"] = str(e)
            self.calib = None
            self._enter_comm_unavailable("Re-checking signal after calibration", needs_caregiver=False)
            return
        res["origin"] = self.source_origin()
        res["time"] = time.time()
        if c.kind == "ssvep":
            key = f"ssvep_{c.n}"
            self.stopping[c.n] = StoppingConfig.from_dict(res["stopping"])
            est = res["dynamic_stopping_estimate"]
            self.log("calibration", f"{c.n}-target calibration ({res['origin']} data): accepted-trial accuracy "
                     f"{est['accuracy_of_accepted']}, abstention {est['abstention_rate']}, "
                     f"mean {est['mean_decision_s']} s, supported={res['supported']}. {res['caveat']}")
        elif c.kind == "gesture":
            key = "gesture"
            self._finish_gesture_calibration(res)
        elif c.kind == "alpha":
            key = "alpha"
            if res["origin"] != "real":
                # Synthetic or non-real data cannot show that a brain is being recorded.
                res["raw_note"] = res["note"]
                res["claim"] = "pipeline_check_only"
                res["note"] = {
                    "eeg_confirmed": f"{res['origin'].capitalize()} alpha response detected: pipeline check only, "
                                     "not physiological EEG.",
                }.get(res["verdict"], f"Pipeline check on {res['origin']} data only (verdict {res['verdict']}); "
                                      "says nothing about a real brain.")
            else:
                res["claim"] = "physiological" if res["verdict"] == "eeg_confirmed" else "none"
            self.log("calibration", f"Alpha check ({res['origin']} data): {res['verdict']}. {res['note']}")
        else:
            key = "blink"
            if res["separable"]:
                self.blink_cfg.threshold_uv = res["threshold_uv"]
                self.blink_cfg.calibrated = True
            else:
                self.blink_cfg.enabled = False
                self.blink_cfg.calibrated = False
            self.log("calibration", f"Blink calibration: {res['note']}")
        self.calib_results[key] = res
        self._save_calibration()
        self.request_plan()  # verification + target-count recommendation depend on reliability
        self.request_policy()
        self.calib = None
        self._enter_comm_unavailable("Re-checking signal after calibration", needs_caregiver=False)

    # ===================================================================================
    # recording
    # ===================================================================================
    def start_recording(self, notes: str = "") -> str:
        if self.source is None:
            raise ValueError("No source to record")
        stamp = time.strftime("%Y%m%d_%H%M%S")
        origin = self.source_origin()
        path = RECORDINGS_DIR / f"{origin}_{stamp}.npz"
        self.recorder = SessionRecorder(self.source, path, notes)
        self.log("recording", f"Recording started -> {path.name}")
        return str(path)

    def stop_recording(self) -> dict | None:
        if not self.recorder:
            return None
        info = self.recorder.save()
        self.recorder = None
        self.log("recording", f"Recording saved: {Path(info['path']).name} ({info['duration_s']:.1f} s, origin {info['origin']})")
        return info

    def _mark(self, event: str, **fields) -> None:
        if self.recorder:
            self.recorder.mark(event, **fields)

    # ===================================================================================
    # commands
    # ===================================================================================
    def log(self, kind: str, text: str) -> None:
        self.events.append({"time": time.time(), "kind": kind, "text": text})
        self.version += 1
        log.info("[%s] %s", kind, text)

    def cmd_back(self) -> None:
        self._task_count("caregiver_actions")
        if self.phase == Phase.MESSAGE_REVIEW:
            self._cancel_review("Caregiver/patient pressed Back")
            return
        self._cancel_attempt()
        self.nav.back()
        self.log("navigation", "Back")
        self._enter(Phase.IDLE if self.phase not in (Phase.OFF, Phase.COMM_UNAVAILABLE) else self.phase)

    def cmd_home(self) -> None:
        self._task_count("caregiver_actions")
        self._cancel_attempt()
        self.pending_leaf = None
        self.nav.home()
        self.log("navigation", "Home")
        self._enter(Phase.IDLE if self.phase not in (Phase.OFF, Phase.COMM_UNAVAILABLE) else self.phase)

    def cmd_undo(self) -> None:
        self._task_count("caregiver_actions")
        self._cancel_attempt()
        if self.pending_leaf is not None:
            self.pending_leaf = None
            self.nav.leaf_cancelled()
            self.log("navigation", "Undo: message draft discarded")
        elif self.nav.undo():
            self.log("navigation", "Undo: last accepted selection reversed")
        else:
            self.log("navigation", "Nothing to undo")
        self._enter(Phase.IDLE if self.phase not in (Phase.OFF, Phase.COMM_UNAVAILABLE) else self.phase)

    def cmd_caregiver_reject(self) -> None:
        if self.phase == Phase.PREDICTION_HIGHLIGHTED:
            self._reject("Caregiver relayed 'no' from the patient")
        elif self.phase == Phase.MESSAGE_REVIEW:
            self._cancel_review("Caregiver relayed 'change it' from the patient")

    def cmd_resume(self) -> None:
        if self.phase == Phase.COMM_UNAVAILABLE:
            if not self.quality.get("signal_ok"):
                self.log("comm", f"Cannot resume yet: {self.quality.get('problem')}")
                return
            self._resume()

    def cmd_set_choices(self, n: int) -> None:
        if n not in self.allowed_choices():
            raise ValueError(f"{n} targets not available: calibrate {n} targets on this data source first")
        self.cfg.n_choices = n
        self.outcomes.clear()
        self.request_plan()
        if self.phase in (Phase.EVIDENCE_COLLECTION, Phase.PREDICTION_HIGHLIGHTED):
            self._cancel_attempt()
            self._enter(Phase.IDLE)
        self.log("config", f"Choices per screen set to {n}")

    def cmd_emergency(self, source_id: str) -> None:
        src = EMERGENCY_SOURCES.get(source_id)
        if src is None:
            raise ValueError(f"unknown emergency source {source_id}")
        self._cancel_attempt()
        self.emergency = {"active": True, "source": source_id, "source_label": src["label"],
                          "validated": src["validated"], "time": time.time()}
        self.log("emergency", f"HELP NOW triggered by {src['label']} (validated input: {src['validated']})")

    def cmd_ack_emergency(self) -> None:
        if self.emergency:
            self.log("emergency", "Help request acknowledged by caregiver")
        self.emergency = None
        if self.phase not in (Phase.OFF, Phase.COMM_UNAVAILABLE):
            self._enter(Phase.IDLE)


# Emergency inputs. Only the demo button exists in this prototype. A validated input (e.g. a
# certified nurse-call switch or a separately validated BCI alarm) would be registered here
# with validated=True and POST to /api/emergency with its id. Nonresponse is never an input.
EMERGENCY_SOURCES: dict[str, dict] = {
    "demo_button": {"label": "HELP NOW demo control (not a certified alarm)", "validated": False},
}
