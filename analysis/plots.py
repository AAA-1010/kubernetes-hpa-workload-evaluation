"""Figures for the article, drawn from results/processed/*.csv.

Usage:  python analysis/plots.py      (run analysis/analyze.py first)
Output: paper/figures/*.pdf and *.png
"""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PROC = REPO / "results" / "processed"
FIG = REPO / "paper" / "figures"
sys.path.insert(0, str(REPO / "workloads"))
from shapes import users_at  # noqa: E402

CONFIGS = ["fixed", "default", "responsive", "balanced"]
WORKLOADS = ["steady", "bursty", "periodic"]
# Categorical slots 1-4 of the validated default palette, in fixed order.
COLOR = {"fixed": "#2a78d6", "default": "#eb6834", "responsive": "#1baf7a", "balanced": "#eda100"}
# Secondary encoding so the figures also read in grayscale print.
STYLE = {"fixed": "-", "default": "--", "responsive": "-.", "balanced": ":"}
MARKER = {"fixed": "o", "default": "s", "responsive": "^", "balanced": "D"}
INK, MUTED, GRID = "#1f1f1e", "#5f5e5a", "#e4e3df"

plt.rcParams.update({
    "font.family": "serif", "font.size": 8, "axes.titlesize": 8, "axes.labelsize": 8,
    "legend.fontsize": 7, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 1.4,
    "savefig.bbox": "tight", "savefig.dpi": 300,
})


def top_legend(fig, ax, ncol: int = 4) -> None:
    """One legend above all panels, so it never covers data."""
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=ncol,
               frameon=False, handlelength=2.6)


def save(fig, name: str) -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG / f"{name}.pdf")
    fig.savefig(FIG / f"{name}.png")
    plt.close(fig)


def load_plan() -> dict:
    return json.loads((REPO / "experiment" / "plan.json").read_text(encoding="utf-8"))


def fig_workloads(plan: dict) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(7.0, 1.7), sharey=True)
    t = np.arange(0, plan["duration_s"])
    for ax, w in zip(axes, WORKLOADS):
        ax.plot(t, [users_at(w, x, plan) for x in t], color=INK)
        ax.set_title(w.capitalize())
        ax.set_xlabel("Time (s)")
    axes[0].set_ylabel("Users (≈ req/s)")
    save(fig, "workloads")


def fig_timeseries(ts: pd.DataFrame, column: str, ylabel: str, name: str, log: bool = False) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(7.0, 1.9), sharey=True)
    mean = ts.groupby(["workload", "config", "t"])[column].mean().reset_index()
    for ax, w in zip(axes, WORKLOADS):
        for c in CONFIGS:
            d = mean[(mean["workload"] == w) & (mean["config"] == c)]
            if not d.empty:
                ax.step(d["t"] + 10, d[column], where="pre", color=COLOR[c],
                        linestyle=STYLE[c], label=c.capitalize())
        ax.set_title(w.capitalize())
        ax.set_xlabel("Time (s)")
        if log:
            ax.set_yscale("log")
    axes[0].set_ylabel(ylabel)
    fig.tight_layout()
    top_legend(fig, axes[0])
    save(fig, name)


def fig_bars(summary: pd.DataFrame) -> None:
    panels = [("lat_p95_ms", "p95 latency (ms)"), ("sla_violation_pct", "SLA violations (%)"),
              ("pod_seconds", "Pod-seconds")]
    fig, axes = plt.subplots(1, 3, figsize=(7.0, 2.1))
    x = np.arange(len(WORKLOADS))
    width = 0.2
    for ax, (metric, label) in zip(axes, panels):
        for i, c in enumerate(CONFIGS):
            d = summary[summary["config"] == c].set_index("workload").reindex(WORKLOADS)
            ax.bar(x + (i - 1.5) * width, d[f"{metric}_mean"], width * 0.9,
                   yerr=d[f"{metric}_std"], color=COLOR[c], label=c.capitalize(),
                   error_kw={"elinewidth": 0.8, "capsize": 1.5, "ecolor": INK})
        ax.set_xticks(x, [w.capitalize() for w in WORKLOADS])
        ax.set_ylabel(label)
        ax.grid(axis="x", visible=False)
    fig.tight_layout(w_pad=2.0)
    top_legend(fig, axes[0])
    save(fig, "summary_bars")


def fig_tradeoff(runs: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(7.0, 1.9), sharey=False)
    for ax, w in zip(axes, WORKLOADS):
        for c in CONFIGS:
            d = runs[(runs["workload"] == w) & (runs["config"] == c)]
            ax.scatter(d["pod_seconds"], d["sla_violation_pct"], s=22, color=COLOR[c],
                       marker=MARKER[c], edgecolors="white", linewidths=0.8, label=c.capitalize())
        ax.set_title(w.capitalize())
        ax.set_xlabel("Pod-seconds")
    axes[0].set_ylabel("SLA violations (%)")
    fig.tight_layout()
    top_legend(fig, axes[0])
    save(fig, "tradeoff")


def fig_calibration() -> None:
    # Final pilot (docs/pilot.md, pilot v5)
    path = REPO / "results" / "pilot" / "v5_rounds10000" / "calibration_summary.csv"
    if not path.exists():
        return
    cal = pd.read_csv(path)
    fig, axes = plt.subplots(1, 2, figsize=(5.0, 1.9))
    axes[0].plot(cal["target_users"], cal["throughput_rps"], color=INK, marker="o", markersize=3)
    axes[0].plot(cal["target_users"], cal["target_users"], color=MUTED, linestyle="--",
                 linewidth=0.8)
    axes[0].set_xlabel("Offered load (users ≈ req/s)")
    axes[0].set_ylabel("Throughput (req/s)")
    axes[1].plot(cal["target_users"], cal["p95_ms"], color=INK, marker="o", markersize=3)
    axes[1].set_xlabel("Offered load (users ≈ req/s)")
    axes[1].set_ylabel("p95 latency (ms)")
    fig.tight_layout(w_pad=2.0)
    save(fig, "calibration")


def main() -> None:
    plan = load_plan()
    fig_workloads(plan)
    fig_calibration()
    if not (PROC / "run_metrics.csv").exists():
        sys.exit("Run analysis/analyze.py first.")
    runs = pd.read_csv(PROC / "run_metrics.csv")
    summary = pd.read_csv(PROC / "summary.csv")
    ts = pd.read_csv(PROC / "timeseries.csv")
    fig_timeseries(ts, "pods_ready", "Ready pods", "replicas")
    fig_timeseries(ts, "p95_ms", "p95 latency (ms)", "p95_timeseries", log=True)
    fig_bars(summary)
    fig_tradeoff(runs)
    print(f"figures written to {FIG.relative_to(REPO)}")


if __name__ == "__main__":
    main()
