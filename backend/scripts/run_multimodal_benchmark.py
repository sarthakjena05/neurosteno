r"""Reproducible SIMULATED multimodal benchmark (EEG-only vs EEG + IMU gestures).

    cd backend
    .venv\Scripts\python scripts\run_multimodal_benchmark.py            # full run (~10-20 min first time; pools cached)
    .venv\Scripts\python scripts\run_multimodal_benchmark.py --quick    # smoke test

Writes reports/multimodal_results.json and reports/multimodal_summary.md.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neurosteno.benchmark.multimodal import MM_LEVELS, MOTION, run_multimodal  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replicates", type=int, default=5)
    ap.add_argument("--tasks", type=int, default=14)
    ap.add_argument("--idle", type=float, default=180.0)
    ap.add_argument("--test-per-target", type=int, default=40)
    ap.add_argument("--test-rest", type=int, default=60)
    ap.add_argument("--gesture-trials", type=int, default=40)
    ap.add_argument("--gesture-natural", type=float, default=900.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--motions", nargs="*", default=None, choices=MOTION)
    ap.add_argument("--levels", nargs="*", default=None, choices=MM_LEVELS)
    ap.add_argument("--quick", action="store_true", help="1 replicate, fewer tasks (smoke test)")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    if a.quick:
        a.replicates, a.tasks, a.idle, a.gesture_natural = 1, 6, 60.0, 300.0
    run_multimodal(a.replicates, a.tasks, a.idle, a.test_per_target, a.test_rest, a.gesture_trials, a.gesture_natural,
                   a.workers, a.motions, a.levels, a.out)


if __name__ == "__main__":
    main()
