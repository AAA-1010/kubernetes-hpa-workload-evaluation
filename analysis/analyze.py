"""Turn raw run data into per-run metrics, summary tables and time series.

Inputs:  results/raw/<config>__<workload>__r<rep>/  (requests.csv, cluster.csv, meta.json)
Outputs: results/processed/run_metrics.csv     one row per run
         results/processed/summary.csv         mean and std per configuration x workload
         results/processed/timeseries.csv      10 s bins per run (for figures)
         paper/tables/*.tex                    LaTeX tables used in the article

Usage:  python analysis/analyze.py
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "results" / "raw"
OUT = REPO / "results" / "processed"
TABLES = REPO / "paper" / "tables"
sys.path.insert(0, str(REPO / "workloads"))
from shapes import users_at  # noqa: E402

CONFIG_ORDER = ["fixed", "default", "responsive", "balanced"]
WORKLOAD_ORDER = ["steady", "bursty", "periodic"]
BIN_S = 10


def load_plan() -> dict:
    return json.loads((REPO / "experiment" / "plan.json").read_text(encoding="utf-8"))


def load_run(run_dir: Path, plan: dict):
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    t0_docker = float((run_dir / "requests.csv.start").read_text().strip())
    t0_host = t0_docker - meta["docker_minus_host_s"]
    req = pd.read_csv(run_dir / "requests.csv")
    req["t"] = req["start_ts"] - t0_docker
    req = req[(req["t"] >= 0) & (req["t"] < plan["duration_s"])].copy()
    cl = pd.read_csv(run_dir / "cluster.csv")
    cl["t"] = cl["ts"] - t0_host
    return meta, req, cl


def step_integral(t: np.ndarray, y: np.ndarray, start: float, end: float) -> float:
    """Integral of a sample-and-hold signal over [start, end]."""
    if len(t) == 0:
        return float("nan")
    total = 0.0
    for i in range(len(t)):
        a = max(t[i], start)
        b = min(t[i + 1] if i + 1 < len(t) else end, end)
        if b > a:
            total += y[i] * (b - a)
    # before the first sample, hold the first value
    if t[0] > start:
        total += y[0] * (min(t[0], end) - start)
    return total


def first_increase_delay(cl: pd.DataFrame, column: str, onset: float, until: float) -> float:
    before = cl[cl["t"] <= onset]
    if before.empty:
        return float("nan")
    level = before[column].iloc[-1]
    after = cl[(cl["t"] > onset) & (cl["t"] < until)]
    up = after[after[column] > level]
    return float(up["t"].iloc[0] - onset) if not up.empty else float("nan")


def run_metrics(meta: dict, req: pd.DataFrame, cl: pd.DataFrame, plan: dict) -> dict:
    dur = plan["duration_s"]
    sla = plan["sla_ms"]
    ok = req[req["success"] == 1]
    total = len(req)
    m = {k: meta[k] for k in ("run_id", "config", "workload", "rep", "order")}
    m["requests"] = total
    m["failures"] = total - len(ok)
    m["failure_pct"] = 100.0 * m["failures"] / total if total else float("nan")
    m["throughput_rps"] = len(ok) / dur
    m["lat_mean_ms"] = ok["latency_ms"].mean()
    m["lat_p50_ms"] = ok["latency_ms"].quantile(0.50)
    m["lat_p95_ms"] = ok["latency_ms"].quantile(0.95)
    m["lat_p99_ms"] = ok["latency_ms"].quantile(0.99)
    violations = (req["success"] == 0) | (req["latency_ms"] > sla)
    m["sla_violation_pct"] = 100.0 * violations.sum() / total if total else float("nan")

    in_run = cl[(cl["t"] >= 0) & (cl["t"] <= dur)]
    t = cl["t"].to_numpy()
    m["pods_ready_mean"] = in_run["pods_ready"].mean()
    m["pods_ready_max"] = in_run["pods_ready"].max()
    m["pod_seconds"] = step_integral(t, cl["pods_allocated"].to_numpy(dtype=float), 0, dur)

    spec = cl[(cl["t"] >= 0) & (cl["t"] <= dur)]["spec_replicas"].to_numpy()
    diffs = np.diff(spec) if len(spec) > 1 else np.array([])
    m["scale_up_actions"] = int((diffs > 0).sum())
    m["scale_down_actions"] = int((diffs < 0).sum())
    m["scaling_actions"] = m["scale_up_actions"] + m["scale_down_actions"]

    onsets = plan["workloads"][meta["workload"]]["onsets_s"]
    bounds = onsets[1:] + [dur]
    if meta["config"] == "fixed":
        m["scale_up_delay_s"] = m["ready_delay_s"] = float("nan")
    else:
        d_spec = [first_increase_delay(cl, "spec_replicas", o, b) for o, b in zip(onsets, bounds)]
        d_ready = [first_increase_delay(cl, "pods_ready", o, b) for o, b in zip(onsets, bounds)]
        m["scale_up_delay_s"] = np.nanmean(d_spec) if not np.all(np.isnan(d_spec)) else float("nan")
        m["ready_delay_s"] = np.nanmean(d_ready) if not np.all(np.isnan(d_ready)) else float("nan")

    cpu = pd.to_numeric(in_run["cpu_m_total"], errors="coerce")
    pods = pd.to_numeric(in_run["metrics_pods"], errors="coerce")
    mem = pd.to_numeric(in_run["mem_mib_total"], errors="coerce")
    request_m = 250.0  # CPU request per pod (k8s/base/app.yaml)
    m["cpu_util_pct_mean"] = (100.0 * cpu / (pods * request_m)).mean()
    m["cpu_cores_mean"] = (cpu / 1000.0).mean()
    m["mem_mib_per_pod_mean"] = (mem / pods).mean()
    # Successful requests per pod-second: a simple efficiency measure.
    m["req_per_pod_second"] = len(ok) / m["pod_seconds"] if m["pod_seconds"] else float("nan")
    return m


def timeseries(meta: dict, req: pd.DataFrame, cl: pd.DataFrame, plan: dict) -> pd.DataFrame:
    dur = plan["duration_s"]
    edges = np.arange(0, dur + BIN_S, BIN_S)
    req = req.assign(bin=pd.cut(req["t"], edges, right=False, labels=edges[:-1]))
    grouped = req.groupby("bin", observed=False)
    ts = pd.DataFrame({
        "t": edges[:-1],
        "rps": grouped["success"].sum().to_numpy() / BIN_S,
        "p95_ms": grouped["latency_ms"].quantile(0.95).to_numpy(),
        "sla_violation_pct": grouped.apply(
            lambda g: 100.0 * (((g["success"] == 0) | (g["latency_ms"] > plan["sla_ms"])).mean())
            if len(g) else np.nan, include_groups=False).to_numpy(),
    })
    ts["target_users"] = [users_at(meta["workload"], t, plan) for t in ts["t"]]
    # cluster state: last sample at or before the end of each bin
    cl_sorted = cl.sort_values("t")
    idx = np.searchsorted(cl_sorted["t"].to_numpy(), ts["t"].to_numpy() + BIN_S, side="right") - 1
    idx = np.clip(idx, 0, len(cl_sorted) - 1)
    for col in ("spec_replicas", "pods_ready", "pods_allocated"):
        ts[col] = cl_sorted[col].to_numpy()[idx]
    for k in ("run_id", "config", "workload", "rep"):
        ts[k] = meta[k]
    return ts


def fmt(mean: float, std: float, digits: int = 1) -> str:
    if pd.isna(mean):
        return "--"
    return f"{mean:.{digits}f} $\\pm$ {std:.{digits}f}"


def write_tables(summary: pd.DataFrame) -> None:
    TABLES.mkdir(parents=True, exist_ok=True)
    columns = [
        ("lat_p95_ms", "p95 (ms)", 0), ("lat_mean_ms", "Mean (ms)", 0),
        ("sla_violation_pct", "SLA viol. (\\%)", 1), ("failure_pct", "Fail (\\%)", 2),
        ("throughput_rps", "Thr. (req/s)", 1), ("pods_ready_mean", "Avg pods", 2),
        ("pods_ready_max", "Max pods", 1), ("pod_seconds", "Pod-s", 0),
        ("scaling_actions", "Actions", 1), ("scale_up_delay_s", "Up delay (s)", 1),
    ]
    for workload in WORKLOAD_ORDER:
        sub = summary[summary["workload"] == workload].set_index("config").reindex(CONFIG_ORDER)
        lines = ["\\begin{tabular}{l" + "r" * len(columns) + "}", "\\toprule",
                 "Config & " + " & ".join(c[1] for c in columns) + " \\\\", "\\midrule"]
        for config, row in sub.iterrows():
            cells = [fmt(row[f"{c}_mean"], row[f"{c}_std"], d) for c, _, d in columns]
            lines.append(f"{config.capitalize()} & " + " & ".join(cells) + " \\\\")
        lines += ["\\bottomrule", "\\end{tabular}"]
        (TABLES / f"results_{workload}.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    plan = load_plan()
    rows, series = [], []
    for run_dir in sorted(RAW.iterdir()) if RAW.exists() else []:
        if not (run_dir / "done.json").exists() or "__calibration__" in run_dir.name:
            continue
        meta, req, cl = load_run(run_dir, plan)
        rows.append(run_metrics(meta, req, cl, plan))
        series.append(timeseries(meta, req, cl, plan))
    if not rows:
        sys.exit("No finished runs found in results/raw.")

    OUT.mkdir(parents=True, exist_ok=True)
    runs = pd.DataFrame(rows).sort_values(["workload", "config", "rep"])
    runs.to_csv(OUT / "run_metrics.csv", index=False)
    pd.concat(series).to_csv(OUT / "timeseries.csv", index=False)

    metrics = [c for c in runs.columns if c not in ("run_id", "config", "workload", "rep", "order")]
    agg = runs.groupby(["config", "workload"])[metrics].agg(["mean", "std", "count"])
    agg.columns = [f"{a}_{b}" for a, b in agg.columns]
    summary = agg.reset_index()
    summary.to_csv(OUT / "summary.csv", index=False)
    write_tables(summary)
    print(f"{len(runs)} runs analysed -> {OUT.relative_to(REPO)} and {TABLES.relative_to(REPO)}")


if __name__ == "__main__":
    main()
