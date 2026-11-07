"""Decoder, calibration, signal quality and blink detector on synthetic data."""

import numpy as np

from neurosteno.acquisition.base import RingBuffer
from neurosteno.acquisition.replay import ReplaySource, SessionRecorder
from neurosteno.acquisition.simulated import SimulatedSource
from neurosteno.headless import FakeClock
from neurosteno.signal.blink import BlinkConfig, OnlineBlinkDetector, calibrate_blinks
from neurosteno.signal.calibration import Trial, calibrate
from neurosteno.signal.fbcca import fbcca_scores, margin_of
from neurosteno.signal.quality import QualityConfig, assess_channels, common_mode

OCC = [4, 5, 6, 7]


def _sim(seed, gaze=None, seconds=4.0):
    s = SimulatedSource(seed=seed)
    s._t0 = 0
    s.set_gaze(gaze)
    return s.generate(int(250 * seconds))


def test_ring_buffer_wraps():
    rb = RingBuffer(2, 10)
    rb.write(np.arange(16, dtype=np.float32).reshape(8, 2))
    rb.write(np.arange(16, 24, dtype=np.float32).reshape(4, 2))
    assert rb.total == 12
    out = rb.read_range(2, 12)
    assert out.shape == (10, 2) and out[0, 0] == 4 and out[-1, 1] == 23


def test_fbcca_identifies_attended_frequency():
    freqs = [6.67, 7.5, 8.57, 10.0, 12.0]
    correct = 0
    for i in range(10):
        d = _sim(i, gaze=freqs[i % 5])[35:35 + 750, OCC]
        k, _ = margin_of(fbcca_scores(d.astype(float), 250, freqs))
        correct += k == i % 5
    assert correct >= 9


def test_calibration_produces_thresholds_and_rest_level():
    freqs = [7.5, 12.0]
    trials = [Trial(_sim(100 + i, freqs[i % 2])[35:1000, OCC].astype(float), i % 2) for i in range(8)]
    trials += [Trial(_sim(200 + i)[35:1000, OCC].astype(float), None) for i in range(4)]
    res = calibrate(trials, freqs, 250.0, max_window_s=3.5)
    assert res["stopping"]["calibrated"] is True
    assert res["stopping"]["rest_threshold"]
    est = res["dynamic_stopping_estimate"]
    assert est["accuracy_of_accepted"] >= 0.85
    assert "not a validated" in res["caveat"]


def test_quality_flags_bad_and_common_mode():
    d = _sim(1, seconds=2)[:, :8].astype(float)
    q = assess_channels(d, 250, ["Fz", "C3", "Cz", "C4", "Pz", "PO7", "Oz", "PO8"], QualityConfig())
    assert all(c["status"] == "ok" for c in q)
    assert common_mode(d, 250) < 0.9
    shared = np.random.default_rng(0).standard_normal(500)[:, None] * 150 + np.zeros((500, 8))
    assert common_mode(shared, 250) > 0.99  # pattern seen on the real headset with floating reference
    flat = np.zeros((500, 8))
    assert all(c["status"] == "flat" for c in assess_channels(flat, 250, list("abcdefgh"), QualityConfig()))


def test_blink_detector_threshold_and_refractory():
    clock = FakeClock()
    src = SimulatedSource(seed=3, clock=clock)
    src.start()
    cfg = BlinkConfig(enabled=True)
    det = OnlineBlinkDetector(cfg, 250.0)
    events = []
    plan = {2.0: True, 2.4: True, 5.0: False, 8.0: True}  # second deliberate blink inside refractory
    t = 0.0
    while t < 11:
        clock.advance(0.05)
        t += 0.05
        for k in list(plan):
            if t >= k:
                src.inject_blink(deliberate=plan.pop(k))
        src.poll()
        events += det.update(src.read_range, src.total_samples, [0])
    accepted = [e for e in events if e["accepted"]]
    assert len(accepted) == 2
    assert any("refractory" in e["reason"] for e in events)
    assert any("below deliberate threshold" in e["reason"] for e in events)


def test_blink_calibration_separability():
    rng = np.random.default_rng(0)
    s = SimulatedSource(seed=5)
    s._t0 = 0
    for _ in range(4):
        s.inject_blink(deliberate=False)
        s.generate(int(250 * 3))
    normal = s.generate(250 * 2)[:, :1].astype(float)
    windows = []
    for _ in range(6):
        s.inject_blink(deliberate=True)
        windows.append(s.generate(int(250 * 2.5))[:, :1].astype(float))
    res = calibrate_blinks(normal, windows, 250.0, BlinkConfig())
    assert res["separable"] and res["threshold_uv"] > res["natural_max_uv"]
    assert rng is not None


def test_record_and_replay_roundtrip(tmp_path):
    clock = FakeClock()
    src = SimulatedSource(seed=9, clock=clock)
    src.start()
    rec = SessionRecorder(src, tmp_path / "r.npz")
    for _ in range(40):
        clock.advance(0.05)
        src.poll()
    rec.mark("test")
    info = rec.save()
    assert info["origin"] == "simulated" and info["samples"] > 400
    rp = ReplaySource(tmp_path / "r.npz", clock=clock)
    rp.start()
    clock.advance(1.0)
    rp.poll()
    assert rp.kind.value == "replay" and rp.status.extra["origin"] == "simulated"
    assert rp.total_samples == 250
