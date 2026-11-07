"""Electrode contact diagnostics, alpha (Berger) check, origin-isolated calibration, and
long-session recording."""

import numpy as np
from neurosteno.acquisition.simulated import SimulatedSource
from neurosteno.engine import Phase
from neurosteno.headless import make_engine, run_until
from neurosteno.signal.electrodes import alpha_reactivity, channel_contacts, channel_spectra, summarize_contacts
from neurosteno.signal.fbcca import StoppingConfig
from neurosteno.state import snapshot

FS = 250.0
LABELS = ["Fz", "C3", "Cz", "C4", "Pz", "PO7", "Oz", "PO8"]
DEC = ["Pz", "PO7", "Oz", "PO8"]


def sim_eeg(seed=0, n=1000, src=None):
    s = src or SimulatedSource(seed=seed)
    return s.generate(n)[:, [c.index for c in s.eeg_channels]].astype(float)


def test_simulated_scalp_eeg_reads_good():
    cs = channel_contacts(sim_eeg(), FS, LABELS)
    assert all(c["verdict"] == "good" for c in cs)
    assert summarize_contacts(cs, DEC)["decoding_usable"] == DEC


def test_identical_channels_flag_reference_off():
    """The real UN-2024.09.36 bring-up failure: every channel the same (or sign-flipped)."""
    rng = np.random.default_rng(1)
    base = np.cumsum(rng.standard_normal(1000)) * 5 + rng.standard_normal(1000) * 20
    x = np.column_stack([base * (1 if j % 2 else -1) + rng.standard_normal(1000) * 0.01 for j in range(8)])
    cs = channel_contacts(x, FS, LABELS)
    assert all("reference_off" in c["issues"] and c["verdict"] == "bad" for c in cs)
    assert summarize_contacts(cs, DEC)["reference_off"]


def test_flat_drifting_and_noisy_channels_are_named():
    x = sim_eeg(seed=2)
    t = np.arange(x.shape[0]) / FS
    x[:, 0] = 0.0                                   # Fz flat
    x[:, 1] += 400.0 * t                            # C3 drifting 400 µV/s
    x[:, 2] += 40.0 * np.sin(2 * np.pi * 60 * t)    # Cz mains
    cs = {c["label"]: c for c in channel_contacts(x, FS, LABELS)}
    assert cs["Fz"]["issues"] == ["no_signal"] and cs["Fz"]["contact_score"] == 0.0
    assert "unstable_contact" in cs["C3"]["issues"]
    assert "mains_noise" in cs["Cz"]["issues"]
    assert cs["Oz"]["verdict"] == "good"
    assert all(c["advice"] for c in cs.values() if c["issues"])


def test_short_window_is_no_data_and_spectra_shape():
    assert channel_contacts(np.zeros((10, 8)), FS, LABELS)[0]["verdict"] == "no_data"
    sp = channel_spectra(sim_eeg(n=1000), FS, LABELS)
    assert sp["freqs"][0] == 1.0 and len(sp["channels"]["Oz"]) == len(sp["freqs"])


def _alpha_run(seed, change, cycles=2, seg=2500):
    s = SimulatedSource(seed=seed)
    op, cl = [], []
    for _ in range(cycles):
        s.eyes_closed = False
        op.append(sim_eeg(src=s, n=seg))
        s.eyes_closed = change
        cl.append(sim_eeg(src=s, n=seg))
    return alpha_reactivity(op, cl, FS, LABELS)


def test_alpha_check_never_confirms_without_a_real_change():
    verdicts = [_alpha_run(seed, change=False)["verdict"] for seed in range(12)]
    assert "eeg_confirmed" not in verdicts


def test_alpha_check_detects_eyes_closed_alpha():
    confirmed = sum(_alpha_run(seed, change=True, cycles=3)["verdict"] == "eeg_confirmed" for seed in range(6))
    assert confirmed >= 4
    r = _alpha_run(0, change=True, cycles=3)
    best = [c for c in r["channels"] if c["reactive"]]
    assert all(c["occipital"] and 9 <= c["peak_hz"] <= 11.5 for c in best)


def test_alpha_check_rejects_reference_off():
    rng = np.random.default_rng(3)
    def common(n, alpha):
        t = np.arange(n) / FS
        b = rng.standard_normal(n) * 10 + alpha * np.sin(2 * np.pi * 10 * t)
        return np.column_stack([b] * 8)
    r = alpha_reactivity([common(2500, 1), common(2500, 1)], [common(2500, 30), common(2500, 30)], FS, LABELS)
    assert r["verdict"] == "invalid"


def test_engine_alpha_check_flow_and_snapshot():
    eng, clock = make_engine()
    run_until(eng, clock, lambda: eng.electrodes is not None and eng.electrodes["window_s"] >= 4, max_s=8)
    snap = snapshot(eng)
    assert snap["electrode_check"]["summary"]["counts"]["good"] == 8
    eng.start_alpha_check(cycles=2)
    assert eng.phase == Phase.CALIBRATING and snapshot(eng)["calibration"]["sub"] == "eyes_open"
    run_until(eng, clock, lambda: snapshot(eng)["calibration"] and snapshot(eng)["calibration"]["sub"] == "eyes_closed",
              max_s=15)
    assert eng.sim.eyes_closed
    assert run_until(eng, clock, lambda: eng.calib is None, max_s=60)
    res = eng.calib_results["alpha"]
    assert res["origin"] == "simulated" and res["verdict"] in ("eeg_confirmed", "weak", "not_detected")
    assert not eng.sim.eyes_closed
    assert snapshot(eng)["decoder"]["calibration"]["alpha"]["verdict"] == res["verdict"]
    # synthetic data must never claim physiological EEG (issue #1, R5)
    assert res["claim"] == "pipeline_check_only" and "Physiological EEG" not in res["note"]
    assert "pipeline check" in res["note"].lower()


def test_calibration_from_another_origin_never_applies():
    """A simulated calibration must not unlock REAL mode; a real one must not be used on synthetic data."""
    eng, _ = make_engine()
    cal = StoppingConfig(calibrated=True).to_dict()
    eng.stopping[2] = StoppingConfig.from_dict(cal)
    eng.calib_results["ssvep_2"] = {"origin": "real", "supported": True, "stopping": cal}
    assert eng.source_origin() == "simulated"
    assert not eng.stopping_for(2).calibrated
    eng.calib_results["ssvep_2"]["origin"] = "simulated"
    assert eng.stopping_for(2).calibrated


def test_recording_longer_than_ring_buffer_is_complete(tmp_path, monkeypatch):
    monkeypatch.setattr("neurosteno.engine.RECORDINGS_DIR", tmp_path)
    eng, clock = make_engine()
    eng.start_recording("long")
    run_until(eng, clock, lambda: False, max_s=135, dt=0.1)
    info = eng.stop_recording()
    assert info["duration_s"] >= 134 and info["lost_samples"] == 0


def _real_origin_recording(tmp_path):
    """A recording whose metadata says origin=real (data synthetic, for gating tests only)."""
    import json
    from neurosteno.acquisition.replay import SessionRecorder
    from neurosteno.headless import FakeClock
    clock = FakeClock()
    s = SimulatedSource(seed=4, clock=clock)
    s.start()
    rec = SessionRecorder(s, tmp_path / "real.npz")
    for _ in range(100):
        clock.advance(0.1)
        s.poll()
        rec.flush()
    rec.save()
    z = dict(np.load(tmp_path / "real.npz"))
    meta = json.loads(str(z["meta"]))
    meta["origin"] = "real"
    z["meta"] = json.dumps(meta)
    np.savez_compressed(tmp_path / "real.npz", **z)
    return tmp_path / "real.npz"


def test_replay_of_real_data_needs_real_calibration_and_stays_out_of_live_history(tmp_path):
    from neurosteno.acquisition.replay import ReplaySource
    path = _real_origin_recording(tmp_path)
    eng, clock = make_engine()
    eng.set_source(ReplaySource(path, clock=clock))
    run_until(eng, clock, lambda: eng.quality.get("problem") and "calibrated" in eng.quality["problem"], max_s=5)
    assert "Replayed real EEG is not calibrated" in eng.quality["problem"]
    snap = snapshot(eng)
    assert snap["mode"]["calibration_required"] and not snap["mode"]["calibrated"]
    assert snap["mode"]["kind"] == "replay" and snap["mode"]["replay_of"] == "real"
    assert eng.source_origin() == "real" and eng.history_origin() == "replay"
    assert eng.phase == Phase.COMM_UNAVAILABLE


def test_mode_calibrated_flag_and_electrode_aliases():
    eng, clock = make_engine()
    run_until(eng, clock, lambda: eng.electrodes is not None and eng.electrodes["window_s"] >= 4, max_s=8)
    snap = snapshot(eng)
    assert snap["mode"]["calibration_required"] is False and snap["mode"]["calibrated"] is False
    ec = snap["electrode_check"]
    assert ec["ready"] and ec["reference_ok"] and ec["message"]
    assert all(c["status"] == c["verdict"] for c in ec["channels"])
