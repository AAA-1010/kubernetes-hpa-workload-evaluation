"""Summarise the pilot (calibration) run: one fixed pod under step-wise load.

For every load step it reports throughput, latency and the pod's CPU use,
which shows the request rate one pod can sustain. That capacity is used to
choose the user counts and SLA threshold in experiment/plan.json.

Usage:  python analysis/calibration.py
Output: results/processed/calibration.csv (+ printed table)
"""

import json
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
RUN = REPO / "results" / "raw" / "fixed__calibration__r1"


def main() -> None:
    plan = json.loads((REPO / "experiment" / "plan.json").read_text(encoding="utf-8"))
    spec = plan["workloads"]["calibration"]
    meta = json.loads((RUN / "meta.json").read_text(encoding="utf-8"))
    t0 = float((RUN / "requests.csv.start").read_text().strip())
    req = pd.read_csv(RUN / "requests.csv")
    req["t"] = req["start_ts"] - t0
    cl = pd.read_csv(RUN / "cluster.csv")
    cl["t"] = cl["ts"] - (t0 - meta["docker_minus_host_s"])

    rows = []
    step = spec["step_s"]
    warmup = 15  # ignore the first seconds of each step while users ramp
    for i, users in enumerate(spec["steps_users"]):
        a, b = i * step + warmup, (i + 1) * step
        r = req[(req["t"] >= a) & (req["t"] < b)]
        ok = r[r["success"] == 1]
        c = cl[(cl["t"] >= a) & (cl["t"] < b)]
        rows.append({
            "target_users": users,
            "throughput_rps": len(ok) / (b - a),
            "failure_pct": 100.0 * (len(r) - len(ok)) / len(r) if len(r) else float("nan"),
            "mean_ms": ok["latency_ms"].mean(),
            "p50_ms": ok["latency_ms"].quantile(0.5),
            "p95_ms": ok["latency_ms"].quantile(0.95),
            "pod_cpu_m": pd.to_numeric(c["cpu_m_total"], errors="coerce").mean(),
        })
    out = pd.DataFrame(rows)
    path = REPO / "results" / "processed" / "calibration.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    print(out.round(1).to_string(index=False))


if __name__ == "__main__":
    main()
