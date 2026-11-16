"""Run the experiment matrix: every configuration x workload x repetition.

Runs are executed in a random order (fixed seed from plan.json) so that slow
drift of the laptop (temperature, background activity) does not line up with
one configuration. Before every run the namespace is deleted and re-created,
so each run starts from the same state. Finished runs are skipped when the
script is restarted, which makes long sessions resumable.

Usage:
  python experiment/run_experiments.py --dry-run         # show the run order
  python experiment/run_experiments.py --calibrate       # pilot: one pod, rising load
  python experiment/run_experiments.py                   # full matrix
  python experiment/run_experiments.py --only default:bursty --reps 1
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

from collector import Collector
from common import RAW_DIR, REPO, kubectl_json, load_plan, log, require_tools, run

sys.path.insert(0, str(REPO / "workloads"))
from shapes import duration_s  # noqa: E402


def build_matrix(plan: dict, reps: int) -> list[tuple[str, str, int]]:
    workloads = [w for w in plan["workloads"] if w != "calibration"]
    runs = [(c, w, r) for c in plan["configs"] for w in workloads for r in range(1, reps + 1)]
    random.Random(plan["seed"]).shuffle(runs)
    return runs


def wait_until(check, timeout: float, what: str, interval: float = 3) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return
        time.sleep(interval)
    raise RuntimeError(f"timed out waiting for {what}")


def reset_environment(plan: dict, replicas: int, hpa_manifest: str | None) -> None:
    ns = plan["namespace"]
    run(["kubectl", "delete", "namespace", ns, "--ignore-not-found", "--wait=true",
         "--timeout=240s"])
    run(["kubectl", "apply", "-f", str(REPO / "k8s" / "base" / "app.yaml")])
    run(["kubectl", "scale", "deployment", "web", "-n", ns, f"--replicas={replicas}"])
    run(["kubectl", "rollout", "status", "deployment/web", "-n", ns, "--timeout=240s"])

    def metrics_ready():
        proc = run(["kubectl", "get", "--raw",
                    f"/apis/metrics.k8s.io/v1beta1/namespaces/{ns}/pods"], check=False)
        return proc.returncode == 0 and len(json.loads(proc.stdout).get("items", [])) >= replicas

    wait_until(metrics_ready, 240, "pod metrics")

    if hpa_manifest:
        run(["kubectl", "apply", "-f", str(REPO / hpa_manifest)])

        def hpa_ready():
            status = kubectl_json(["get", "hpa", "web", "-n", ns]).get("status", {})
            return any(m.get("resource", {}).get("current", {}).get("averageUtilization")
                       is not None for m in status.get("currentMetrics") or [])

        wait_until(hpa_ready, 240, "HPA to read CPU metrics")
    time.sleep(plan["settle_s"])


def on_mains_power() -> bool:
    """True when the host runs on AC power (or has no battery information).

    A laptop on battery throttles its CPU and may sleep, which invalidates a
    run (see docs/pilot.md, pilot v2).
    """
    if sys.platform != "win32":
        return True
    import ctypes

    class PowerStatus(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

    status = PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return True
    return status.ACLineStatus != 0  # 0 = battery, 1 = AC, 255 = unknown


def prevent_idle_sleep() -> None:
    """Ask Windows not to idle-sleep while this process runs.

    Uses SetThreadExecutionState, the per-process request media players use;
    it changes no power settings and ends when the process exits. A closed lid
    or a manual Sleep still suspends the machine, which sampling_gap_s detects.
    """
    if sys.platform == "win32":
        import ctypes
        es_continuous, es_system_required = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)


def sampling_gap_s(cluster_csv: Path) -> float:
    """Largest interval between two cluster samples (detects host sleep)."""
    import csv
    with open(cluster_csv, encoding="utf-8") as fh:
        ts = [float(r["ts"]) for r in csv.DictReader(fh)]
    return max((b - a for a, b in zip(ts, ts[1:])), default=0.0)


def control_plane_restarts() -> dict:
    """Restart counts of the control-plane pods (the HPA runs in the controller manager)."""
    pods = kubectl_json(["get", "pods", "-n", "kube-system"])["items"]
    counts = {}
    for pod in pods:
        name = pod["metadata"]["name"]
        if any(k in name for k in ("controller-manager", "scheduler", "apiserver", "etcd",
                                   "metrics-server")):
            key = name.rsplit("-", 2)[0] if "metrics-server" in name else name
            counts[key] = sum(c.get("restartCount", 0)
                              for c in pod["status"].get("containerStatuses", []))
    return counts


def clock_offset_s(plan: dict) -> float:
    """Docker VM clock minus host clock, used to align the two time sources."""
    before = time.time()
    proc = run(["docker", "exec", f"{plan['cluster_name']}-control-plane", "date", "+%s.%N"])
    after = time.time()
    return float(proc.stdout.strip()) - (before + after) / 2


def run_one(plan: dict, config: str, workload: str, rep: int, order: int,
            replicas_override: int | None = None) -> bool:
    run_id = f"{config}__{workload}__r{rep}"
    out_dir = RAW_DIR / run_id
    if (out_dir / "done.json").exists():
        log(f"skip {run_id} (already done)")
        return True
    if not on_mains_power():
        log(f"run {order}: {run_id} - NOT started: laptop is on battery; plug in the charger")
        return False
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = plan["configs"][config]
    replicas = replicas_override or cfg["initial_replicas"]
    log(f"run {order}: {run_id} - resetting environment ({replicas} pods)")
    reset_environment(plan, replicas, cfg["hpa"])

    offset = clock_offset_s(plan)
    restarts_before = control_plane_restarts()
    collector = Collector(plan["namespace"], out_dir / "cluster.csv", plan["sample_interval_s"])
    rel = out_dir.relative_to(REPO).as_posix()
    cmd = [
        "docker", "run", "--rm", "--name", f"locust-{config}-{workload}-r{rep}",
        "--network", "kind", "--cpus", str(plan["locust_cpus"]),
        "--mount", f"type=bind,source={REPO},target=/work",
        "-w", "/work/workloads",
        "-e", f"WORKLOAD={workload}",
        "-e", "PLAN_PATH=/work/experiment/plan.json",
        "-e", f"REQUEST_LOG=/work/{rel}/requests.csv",
        plan["locust_image"],
        "-f", "/work/workloads/locustfile.py", "--headless",
        "--host", plan["target_host"], "--only-summary",
        "--csv", f"/work/{rel}/locust", "--loglevel", "WARNING",
    ]
    collector.start()
    started = time.time()
    log(f"run {order}: {run_id} - load running for {duration_s(workload, plan)} s")
    proc = run(cmd, check=False, timeout=duration_s(workload, plan) + 300)
    finished = time.time()
    time.sleep(plan["sample_interval_s"] * 2)  # capture the state right after the load ends
    collector.stop()
    collector.join()

    (out_dir / "locust_stdout.txt").write_text((proc.stdout or "") + (proc.stderr or ""),
                                              encoding="utf-8")
    meta = {
        "run_id": run_id, "config": config, "workload": workload, "rep": rep,
        "order": order, "initial_replicas": replicas, "hpa_manifest": cfg["hpa"],
        "host_start": started, "host_end": finished, "docker_minus_host_s": offset,
        "locust_exit_code": proc.returncode, "collector_errors": collector.errors,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # Locust exits with 1 when some requests failed; that is a valid result.
    # Any other code means the load generator itself broke.
    ok = proc.returncode in (0, 1) and (out_dir / "requests.csv").exists()
    gap = sampling_gap_s(out_dir / "cluster.csv")
    restarts_after = control_plane_restarts()
    meta["max_sampling_gap_s"] = gap
    meta["on_mains_power_at_end"] = on_mains_power()
    meta["control_plane_restarts_before"] = restarts_before
    meta["control_plane_restarts_after"] = restarts_after
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    restarted = restarts_after != restarts_before
    if gap > 4 * plan["sample_interval_s"] or not meta["on_mains_power_at_end"] or restarted:
        # The host stalled, slept or lost mains power, or a control-plane
        # component restarted (pausing the HPA) during the run: discard it.
        ok = False
        log(f"run {order}: {run_id} - INVALID (sampling gap {gap:.0f} s, "
            f"mains power {meta['on_mains_power_at_end']}, control-plane restart "
            f"{restarted}); it will be re-run")
        invalid = RAW_DIR.parent / "invalid" / f"{run_id}__{int(started)}"
        invalid.parent.mkdir(parents=True, exist_ok=True)
        out_dir.rename(invalid)
        return False
    if ok:
        (out_dir / "done.json").write_text(json.dumps({"finished": finished}), encoding="utf-8")
        log(f"run {order}: {run_id} - done")
    else:
        log(f"run {order}: {run_id} - FAILED (exit {proc.returncode}); see locust_stdout.txt")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print the run order and exit")
    parser.add_argument("--calibrate", action="store_true",
                        help="pilot run: one fixed pod under step-wise increasing load")
    parser.add_argument("--only", action="append", default=[],
                        help="restrict to config:workload (repeatable)")
    parser.add_argument("--reps", type=int, help="override the number of repetitions")
    args = parser.parse_args()

    plan = load_plan()
    prevent_idle_sleep()
    if args.calibrate:
        require_tools(["docker", "kubectl"])
        replicas = plan["workloads"]["calibration"]["replicas"]
        ok = run_one(plan, "fixed", "calibration", 1, 1, replicas_override=replicas)
        sys.exit(0 if ok else 1)

    runs = build_matrix(plan, args.reps or plan["repetitions"])
    if args.only:
        wanted = {tuple(o.split(":")) for o in args.only}
        runs = [r for r in runs if (r[0], r[1]) in wanted]

    per_run = plan["duration_s"] + plan["settle_s"] + 90
    log(f"{len(runs)} runs planned, roughly {len(runs) * per_run / 3600:.1f} h in total")
    if args.dry_run:
        for i, (c, w, r) in enumerate(runs, 1):
            print(f"{i:2d}. {c:<10} {w:<9} rep {r}")
        return

    require_tools(["docker", "kubectl"])

    def attempt(i, c, w, r) -> bool:
        try:
            return run_one(plan, c, w, r, i)
        except Exception as exc:  # keep going; the run is retried below
            log(f"run {i}: {c}__{w}__r{r} - ERROR: {exc}")
            return False

    pending = [(i, c, w, r) for i, (c, w, r) in enumerate(runs, 1)]
    for round_no in range(1, 4):  # first pass plus up to two retries
        pending = [p for p in pending if not attempt(*p)]
        if not pending:
            break
        log(f"pass {round_no}: {len(pending)} run(s) to retry")
    failed = [f"{c}:{w}:r{r}" for _, c, w, r in pending]
    if failed:
        log("failed runs (re-run the script to retry them): " + ", ".join(failed))
        sys.exit(1)
    log("all runs complete")


if __name__ == "__main__":
    main()
