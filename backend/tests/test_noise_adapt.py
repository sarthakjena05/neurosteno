"""Controlled noise injection + signal-quality-driven choice count (SIMULATED base source).

The injected noise must change only the PROCESSED stream; the measured metric must react; the
choice count must come from the existing adaptive model (with dwell), never be set directly."""

import numpy as np

from neurosteno.acquisition.noise_injection import NoiseInjector, band_noise_uv
from neurosteno.engine import Phase
from neurosteno.headless import make_engine, run_until, wait_ready
from neurosteno.quality_adapt import DWELL_S, load_table, rel_at
from neurosteno.state import snapshot


def _engine(n=5, seed=1):
    eng, clock = make_engine(n=n, routing="adaptive", seed=seed)
    eng.cfg.auto_adapt = True
    eng.cfg.adapt_source = "signal_quality"
    assert wait_ready(eng, clock)
    return eng, clock


def test_zero_injection_is_bit_identical_to_raw():
    eng, clock = _engine()
    src = eng.source
    run_until(eng, clock, lambda: False, max_s=2)
    t = src.total_samples
    assert np.array_equal(src.read_range(t - 500, t), src.read_range_raw(t - 500, t))
    src.set_injected_noise(10)
    run_until(eng, clock, lambda: False, max_s=2)
    src.set_injected_noise(0)
    run_until(eng, clock, lambda: False, max_s=2)
    t = src.total_samples
    assert np.array_equal(src.read_range(t - 250, t), src.read_range_raw(t - 250, t))


def test_injection_only_touches_eeg_and_is_reproducible():
    inj = NoiseInjector(250.0, list(range(8)))
    x = np.zeros((1000, 17), dtype=np.float32)
    inj.set_level(5.0, 0)
    inj.set_level(0.0, 600)
    y1, y2 = inj.apply(x, 0), inj.apply(x, 0)
    assert np.array_equal(y1, y2)                       # same absolute samples -> same noise
    assert np.all(y1[:, 8:] == 0)                        # IMU / counter / battery untouched
    assert np.all(y1[600:] == 0)                         # level history is exact across a change
    assert 4.0 < float(np.sqrt(np.mean(y1[:600, :8] ** 2))) < 6.0
    assert np.array_equal(inj.apply(x[300:700], 300), y1[300:700])


def test_measured_metric_reacts_and_raw_is_unchanged():
    eng, clock = _engine()
    run_until(eng, clock, lambda: False, max_s=3)
    n0 = eng.quality["noise"]
    assert n0["snr_loss_db"] == 0.0 and n0["processed_band_noise_uv"] == n0["raw_band_noise_uv"]
    eng.source.set_injected_noise(15)
    run_until(eng, clock, lambda: False, max_s=3)
    n1 = eng.quality["noise"]
    assert n1["snr_loss_db"] > 5 and n1["processed_band_noise_uv"] > 2 * n1["raw_band_noise_uv"]
    assert abs(n1["raw_band_noise_uv"] - n0["raw_band_noise_uv"]) < 2.0
    assert band_noise_uv(np.zeros((10, 4)), 250.0) is None


def test_noise_drives_choice_count_down_and_back_up_with_dwell():
    eng, clock = _engine()
    assert eng.cfg.n_choices == 5
    eng.source.set_injected_noise(20)
    run_until(eng, clock, lambda: False, max_s=1.0)
    assert eng.cfg.n_choices == 5                        # not forced: needs measurement + dwell
    assert run_until(eng, clock, lambda: eng.cfg.n_choices < 5, max_s=20)
    low = eng.cfg.n_choices
    sw = eng.qa["switches"][-1]
    assert sw["from"] == 5 and sw["to"] == low and sw["snr_loss_db"] > 3
    assert eng.last_adaptation["source"] == "signal_quality"
    eng.source.set_injected_noise(0)
    assert run_until(eng, clock, lambda: eng.cfg.n_choices == 5, max_s=30)
    d = snapshot(eng)["adaptation"]
    assert d["source"] == "signal_quality" and d["signal_quality"]["available"]
    assert "SIMULATED reference" in d["signal_quality"]["model_note"]


def test_no_switch_without_auto_adapt():
    eng, clock = _engine()
    eng.cfg.auto_adapt = False
    eng.source.set_injected_noise(25)
    run_until(eng, clock, lambda: False, max_s=15)
    assert eng.cfg.n_choices == 5
    assert eng.qa["decision"]["chosen"] != 5             # the model still reports its advice


def test_outcome_adaptation_does_not_fight_quality_adaptation():
    eng, clock = _engine()
    eng.adapt_since_switch = 99
    eng._maybe_adapt()                                   # outcome path must not switch n in this mode
    assert eng.cfg.n_choices == 5


def test_no_switch_during_final_review():
    eng, clock = _engine(seed=3)
    eng.sim_autopilot = "comfort/temperature/too-cold"
    assert run_until(eng, clock, lambda: eng.phase == Phase.MESSAGE_REVIEW, max_s=600)
    eng.sim_autopilot = None
    n = eng.cfg.n_choices
    eng.source.set_injected_noise(25)
    run_until(eng, clock, lambda: eng.phase != Phase.MESSAGE_REVIEW, max_s=DWELL_S + 3)
    if eng.phase == Phase.MESSAGE_REVIEW:
        assert eng.cfg.n_choices == n


def test_reference_table_is_monotone_after_smoothing():
    t = load_table()
    assert t is not None and t["data_origin"] == "simulated"
    for n in (2, 3, 5):
        accs = [rel_at(L, n, t).accuracy for L in np.arange(0, 16, 0.5)]
        abs_ = [rel_at(L, n, t).abstain for L in np.arange(0, 16, 0.5)]
        assert all(a >= b - 1e-9 for a, b in zip(accs, accs[1:]))
        assert all(a <= b + 1e-9 for a, b in zip(abs_, abs_[1:]))


def test_recorder_keeps_raw_samples():
    from neurosteno.acquisition.replay import SessionRecorder
    eng, clock = _engine()
    src = eng.source
    rec = SessionRecorder.__new__(SessionRecorder)
    rec.source, rec._chunks, rec.lost_samples = src, [], 0
    rec._pos = src.total_samples
    src.set_injected_noise(20)
    run_until(eng, clock, lambda: False, max_s=2)
    end = src.total_samples
    rec.flush()
    got = np.concatenate(rec._chunks)
    assert np.array_equal(got, src.read_range_raw(end - got.shape[0], end))
