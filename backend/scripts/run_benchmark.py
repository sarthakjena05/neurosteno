"""Reproducible NeuroSteno A/B/C/D benchmark (SIMULATED).

    cd backend
    .venv\\Scripts\\python scripts\\run_benchmark.py                 # full run (default 5 replicates)
    .venv\\Scripts\\python scripts\\run_benchmark.py --quick         # smoke run (~1 min)

Writes reports/benchmark_results.json (served at GET /api/benchmark) and
reports/benchmark_summary.md. Same arguments -> same numbers (all seeds are fixed).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neurosteno.benchmark.run import SCENARIOS, run_benchmark  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replicates", type=int, default=5, help="independent synthetic calibration/test pools")
    ap.add_argument("--tasks", type=int, default=14, help="non-urgent messages per session (+6 urgent probes)")
    ap.add_argument("--idle", type=float, default=180.0, help="no-control seconds per session")
    ap.add_argument("--test-per-target", type=int, default=40)
    ap.add_argument("--test-rest", type=int, default=60)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--message-runs", type=int, default=2, help="runs per replicate in the per-message experiment")
    ap.add_argument("--quick", action="store_true", help="1 replicate, small pools, 6 tasks")
    ap.add_argument("--out", default=None, help="output directory (default backend/reports)")
    a = ap.parse_args()
    if a.quick:
        a.replicates, a.tasks, a.idle, a.test_per_target, a.test_rest = 1, 6, 60.0, 15, 20
    run_benchmark(a.replicates, a.tasks, a.idle, a.test_per_target, a.test_rest, a.workers,
                        a.scenarios.split(","), Path(a.out) if a.out else None, message_runs=a.message_runs)
    out = Path(a.out) if a.out else Path(__file__).resolve().parents[1] / "reports"
    print()
    print((out / "benchmark_summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
