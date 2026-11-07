r"""Drive the RUNNING server exactly like the operator UI: injected noise 0 -> high -> 0 and watch the
automatic choice count. Uses only REST commands + /api/state.

    .venv\Scripts\python scripts\verify_noise_demo.py --url http://127.0.0.1:8000 --source assisted-real
    (--source simulated for the fully simulated fallback)
"""
import argparse
import sys
import time

import httpx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--source", default="assisted-real", choices=["assisted-real", "simulated", "keep"])
    ap.add_argument("--high", type=float, default=50.0)
    a = ap.parse_args()
    c = httpx.Client(base_url=a.url, timeout=60)

    def cmd(action, **p):
        r = c.post("/api/command", json={"action": action, **p})
        if r.status_code >= 400:
            raise SystemExit(f"{action} failed: {r.status_code} {r.text}")

    if a.source == "assisted-real":
        r = c.post("/api/mode", json={"mode": "assisted", "base": "real"})
        print("mode assisted/real:", r.status_code, r.text[:160])
    elif a.source == "simulated":
        c.post("/api/mode", json={"mode": "simulated", "seed": 1}).raise_for_status()
    time.sleep(3)
    st = c.get("/api/state").json()
    print("source:", st["mode"]["label"], "| status:", (st.get("source") or {}).get("status", {}).get("message", "")[:120])
    if 5 in st["decoder"]["allowed_choices"]:
        cmd("set_choices", n=5)
    cmd("set_adapt_source", source="signal_quality")
    cmd("set_auto_adapt", on=True)
    seq, t_start = [], time.time()
    for uv, dur in ((0.0, 12), (a.high, 25), (0.0, 25)):
        cmd("set_injected_noise", uv=uv)
        t0 = time.time()
        while time.time() - t0 < dur:
            st = c.get("/api/state").json()
            n = st["screen"]["n_choices"]
            if not seq or seq[-1] != n:
                seq.append(n)
                nz = st["quality"].get("noise") or {}
                print(f"t={time.time() - t_start:5.1f}s injected {nz.get('injected_uv')} uV  loss {nz.get('snr_loss_db_smoothed')} dB  "
                      f"raw {nz.get('raw_band_noise_uv')} / processed {nz.get('processed_band_noise_uv')} uV  -> n={n}  "
                      f"badge-src={st['mode']['kind']} gaze={(st.get('sim') or {}).get('gaze_hz')}")
            time.sleep(0.5)
    sw = st["adaptation"]["quality_switches"]
    print("sequence", seq)
    print("switches (newest first)", [(w["from"], w["to"], w["snr_loss_db"], w["injected_uv"]) for w in sw])
    print("safety: messages sent without final confirmation =",
          st["safety_invariants"]["messages_sent_without_final_confirmation"])
    ok = len(seq) >= 3 and seq[0] == 5 and min(seq) < 5 and seq[-1] == 5
    print("RESULT:", "PASS" if ok else "FAIL")
    cmd("set_injected_noise", uv=0)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
