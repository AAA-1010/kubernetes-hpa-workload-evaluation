"""Relates failed requests to scale-down events.

For every run with at least one failed request, each failure is checked against
the scale-down events of that run (decreases of the Deployment's spec.replicas in
cluster.csv). A failure counts as "near a scale-down" if it started between
WINDOW_BEFORE seconds before and WINDOW_AFTER seconds after such an event.
As a chance baseline, the script also reports the fraction of each run's
duration that these windows cover: if failures were unrelated to scale-down,
roughly that fraction of them would fall inside the windows.

Usage:  python analysis/failures.py      (reads results/raw, writes results/processed/failures.csv)
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "results" / "raw"
OUT = REPO / "results" / "processed"
WINDOW_BEFORE, WINDOW_AFTER = 5.0, 30.0


def covered_seconds(events: np.ndarray, start: float, end: float) -> float:
    """Length of the union of [e - before, e + after] windows clipped to [start, end]."""
    spans = sorted((max(start, e - WINDOW_BEFORE), min(end, e + WINDOW_AFTER)) for e in events)
    total, cur_s, cur_e = 0.0, None, None
    for s, e in spans:
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def main() -> None:
    plan = json.loads((REPO / "experiment" / "plan.json").read_text(encoding="utf-8"))
    rows = []
    for run in sorted(p for p in RAW.iterdir() if (p / "done.json").exists()):
        meta = json.loads((run / "meta.json").read_text(encoding="utf-8"))
        if meta["workload"] not in ("steady", "bursty", "periodic"):
            continue
        req = pd.read_csv(run / "requests.csv")
        cl = pd.read_csv(run / "cluster.csv")
        start = float((run / "requests.csv.start").read_text().strip()) - meta["docker_minus_host_s"]
        end = start + plan["duration_s"]
        downs = cl["ts"][cl["spec_replicas"].diff() < 0].to_numpy()
        fail_t = req.loc[req["success"] == 0, "start_ts"].to_numpy() - meta["docker_minus_host_s"]
        near = sum(bool(np.any((t >= downs - WINDOW_BEFORE) & (t <= downs + WINDOW_AFTER)))
                   for t in fail_t)
        rows.append({
            "run_id": meta["run_id"], "config": meta["config"], "workload": meta["workload"],
            "failures": len(fail_t), "scale_downs": len(downs), "failures_near_scale_down": near,
            "window_coverage_pct": 100.0 * covered_seconds(downs, start, end) / (end - start),
        })
    df = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "failures.csv", index=False)
    f = df[df["failures"] > 0]
    total, near = int(f["failures"].sum()), int(f["failures_near_scale_down"].sum())
    expected = float((f["failures"] * f["window_coverage_pct"] / 100.0).sum())
    print(f.to_string(index=False, float_format=lambda v: f"{v:.1f}"))
    print(f"\nruns with failures: {len(f)} of {len(df)}; all of them had a scale-down: "
          f"{bool((f['scale_downs'] > 0).all())}")
    print(f"failures within [-{WINDOW_BEFORE:.0f}, +{WINDOW_AFTER:.0f}] s of a scale-down: "
          f"{near} of {total} ({100.0 * near / total:.1f}%); expected by chance: {expected:.1f} "
          f"({100.0 * expected / total:.1f}%)")
    zero = df[(df["scale_downs"] == 0)]
    print(f"runs without any scale-down: {len(zero)}, failures in them: {int(zero['failures'].sum())}")


if __name__ == "__main__":
    main()
