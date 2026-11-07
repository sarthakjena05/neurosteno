"""Headless, time-accelerated driver for the real engine (tests + A/B scenario runs).

Uses a fake clock so a session that takes minutes of simulated time runs in seconds. The
full pipeline is exercised: synthetic EEG -> filters -> FBCCA -> dynamic stopping ->
state machine -> navigation. Results are SIMULATED and labelled as such.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .acquisition.simulated import SimulatedSource
from .config import DATA_DIR, EngineConfig
from .context import ProfileStore
from .engine import Engine, Phase
from .phrases.catalog import build_catalog


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def isolated_store() -> tuple[ProfileStore, Path]:
    """Copy of the seed profiles with an empty runtime dir (no cross-run history leakage)."""
    tmp = Path(tempfile.mkdtemp(prefix="neurosteno_"))
    shutil.copytree(DATA_DIR / "profiles", tmp / "profiles")
    return ProfileStore(tmp), tmp


def make_engine(profile: str = "new-patient", n: int = 2, routing: str = "adaptive", seed: int = 0,
                cfg: EngineConfig | None = None, store: ProfileStore | None = None):
    clock = FakeClock()
    if store is None:
        store, _ = isolated_store()
    cfg = cfg or EngineConfig()
    cfg.n_choices = n
    cfg.routing_mode = routing
    eng = Engine(build_catalog(), store, cfg, clock=clock)
    eng.set_profile(profile)
    src = SimulatedSource(seed=seed, clock=clock)
    eng.set_source(src)
    eng.sim_rng = __import__("numpy").random.default_rng(seed)
    eng.plan_now()
    return eng, clock


def run_until(eng: Engine, clock: FakeClock, cond, max_s: float = 600.0, dt: float = 0.05) -> bool:
    t_end = clock.t + max_s
    while clock.t < t_end:
        clock.advance(dt)
        eng.tick()
        if cond():
            return True
    return False


def wait_ready(eng: Engine, clock: FakeClock) -> bool:
    return run_until(eng, clock, lambda: eng.phase in (Phase.IDLE, Phase.EVIDENCE_COLLECTION), max_s=30)


def produce_message(eng: Engine, clock: FakeClock, target_id: str, max_s: float = 900.0) -> dict | None:
    """Let the simulated patient (autopilot) produce `target_id`; returns the task metrics."""
    n_before = len(eng.metrics)
    eng.sim_autopilot = target_id
    ok = run_until(eng, clock, lambda: len(eng.metrics) > n_before, max_s=max_s)
    # let the "sent" screen finish so the next task starts from Home
    run_until(eng, clock, lambda: eng.phase != Phase.MESSAGE_SENT, max_s=10)
    return eng.metrics[-1] if ok else None
