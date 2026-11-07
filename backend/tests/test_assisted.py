"""ASSISTED source: real stream + simulated SSVEP augmentation, never reported as real."""
import json

import numpy as np

from neurosteno.acquisition.assisted import AssistedSource
from neurosteno.acquisition.base import SourceKind
from neurosteno.acquisition.simulated import SimulatedSource


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _src(gaze=None):
    clk = Clock()
    base = SimulatedSource(seed=1, ssvep_uv=0.0, clock=clk)
    src = AssistedSource(base, ssvep_uv=2.0, seed=2)
    src.clock = clk
    src.start()
    if gaze:
        src.set_gaze(gaze)
    for _ in range(20):
        clk.t += 0.1
        src.poll()
    return src, base


def test_kind_and_status_are_assisted_not_real():
    src, _ = _src()
    assert src.kind == SourceKind.ASSISTED and src.kind != SourceKind.REAL
    assert src.status.kind == SourceKind.ASSISTED
    assert src.status.extra["base_origin"] == "simulated"
    json.dumps(src.info())


def test_passes_base_samples_through_and_adds_ssvep_only_on_gaze():
    plain, base = _src()
    n = plain.total_samples
    assert n > 0
    np.testing.assert_allclose(plain.read_range(0, n), base.read_range(0, n), atol=1e-4)  # no gaze: unchanged
    aug, base2 = _src(gaze=10.0)
    m = min(aug.total_samples, base2.total_samples)
    diff = aug.read_range(0, m) - base2.read_range(0, m)
    eeg = [c.index for c in aug.channels if c.kind == "eeg"]
    other = [c.index for c in aug.channels if c.kind != "eeg"]
    assert np.abs(diff[:, eeg]).max() > 0.5  # augmentation present on EEG
    assert np.abs(diff[:, other]).max() < 1e-4  # IMU / counter untouched


def test_synthetic_blinks_and_gestures_refused():
    src, _ = _src()
    for f in (lambda: src.inject_blink(True), lambda: src.perform_gesture("nod")):
        try:
            f()
        except ValueError:
            continue
        raise AssertionError("must be refused in assisted mode")
