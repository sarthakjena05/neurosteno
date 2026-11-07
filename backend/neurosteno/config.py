"""Runtime configuration (all values adjustable from the operator panel or API)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_DIR / "data"
RECORDINGS_DIR = BACKEND_DIR / "recordings"


@dataclass
class EngineConfig:
    # Stimulation frequencies per target count (sinusoidal flicker; all < half of a 60 Hz refresh).
    freqs: dict[int, list[float]] = field(default_factory=lambda: {
        2: [7.5, 12.0],
        3: [6.67, 8.57, 12.0],
        5: [6.67, 7.5, 8.57, 10.0, 12.0],
    })
    confirm_freqs: list[float] = field(default_factory=lambda: [7.5, 12.0])  # [Yes, No]
    decoding_labels: list[str] = field(default_factory=lambda: ["Pz", "PO7", "Oz", "PO8"])
    blink_labels: list[str] = field(default_factory=lambda: ["Fz"])
    n_choices: int = 2  # start with TWO real SSVEP targets
    auto_adapt: bool = False
    policy: str = "explicit"  # "explicit" | "provisional" | "gated" | "auto" (chosen from measured reliability)
    gate_margin_factor: float = 1.5  # "gated": skip the per-step YES/NO when margin >= factor x calibrated threshold
    adapt_min_evidence: int = 8      # live outcomes at the current n before re-optimising n
    adapt_hysteresis: float = 0.20   # required expected gain before switching n
    routing_mode: str = "adaptive"  # "adaptive" (risk-aware semantic routing) | "static" (baseline menu)
    sim_mistake_rate: float = 0.0  # SIMULATED only: probability the virtual patient's EEG follows a wrong target
    provisional_window_s: float = 3.0
    provisional_margin_factor: float = 1.25  # margin must exceed threshold by this factor
    rest_s: float = 1.0  # gaze-shift pause before each evidence window
    feedback_s: float = 1.0  # how long ACCEPTED/REJECTED/ABSTAINED is shown
    sent_display_s: float = 4.0
    confirm_attempts: int = 2  # abstentions on a confirmation before it counts as NOT confirmed
    abstain_pause_after: int = 4  # consecutive abstentions -> communication unavailable
    stall_s: float = 1.0  # no samples for this long -> communication unavailable
    recover_s: float = 3.0  # good signal for this long -> auto-resume after a signal failure
    require_calibration_real: bool = True  # REAL mode refuses to decode for communication until calibrated

    def to_dict(self) -> dict:
        d = asdict(self)
        d["freqs"] = {str(k): v for k, v in self.freqs.items()}
        return d
