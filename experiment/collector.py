"""Background sampler that records cluster state during a run.

Every `interval` seconds it records the Deployment's desired/ready replicas,
the HPA's desired replicas and observed CPU utilisation, the number of pods
that hold resources, and per-pod CPU and memory from the Metrics API.
"""

import csv
import json
import threading
import time
from pathlib import Path

from common import kubectl_json, log, parse_cpu_millicores, parse_mem_mib, run

FIELDS = [
    "ts", "spec_replicas", "ready_replicas", "hpa_desired", "hpa_cpu_pct",
    "pods_allocated", "pods_ready", "metrics_pods", "cpu_m_total", "mem_mib_total",
]


def sample(namespace: str) -> dict:
    ts = time.time()
    objects = kubectl_json(["get", "deployment,hpa,pods", "-n", namespace])["items"]
    row = {k: "" for k in FIELDS}
    row["ts"] = f"{ts:.3f}"
    pods_allocated = pods_ready = 0
    for obj in objects:
        kind = obj["kind"]
        if kind == "Deployment" and obj["metadata"]["name"] == "web":
            row["spec_replicas"] = obj["spec"].get("replicas", 0)
            row["ready_replicas"] = obj["status"].get("readyReplicas", 0)
        elif kind == "HorizontalPodAutoscaler":
            status = obj.get("status", {})
            row["hpa_desired"] = status.get("desiredReplicas", "")
            for metric in status.get("currentMetrics") or []:
                res = metric.get("resource", {})
                if res.get("name") == "cpu":
                    row["hpa_cpu_pct"] = res.get("current", {}).get("averageUtilization", "")
        elif kind == "Pod":
            if obj["metadata"].get("deletionTimestamp"):
                continue  # terminating pods no longer serve traffic
            if obj["status"].get("phase") in ("Pending", "Running"):
                pods_allocated += 1
            conditions = obj["status"].get("conditions") or []
            if any(c["type"] == "Ready" and c["status"] == "True" for c in conditions):
                pods_ready += 1
    row["pods_allocated"] = pods_allocated
    row["pods_ready"] = pods_ready

    proc = run(["kubectl", "get", "--raw",
                f"/apis/metrics.k8s.io/v1beta1/namespaces/{namespace}/pods"],
               check=False, timeout=20)
    if proc.returncode == 0:
        items = json.loads(proc.stdout).get("items", [])
        cpu = mem = 0.0
        for item in items:
            for c in item.get("containers", []):
                cpu += parse_cpu_millicores(c["usage"]["cpu"])
                mem += parse_mem_mib(c["usage"]["memory"])
        row["metrics_pods"] = len(items)
        row["cpu_m_total"] = f"{cpu:.1f}"
        row["mem_mib_total"] = f"{mem:.1f}"
    return row


class Collector(threading.Thread):
    def __init__(self, namespace: str, out_path: Path, interval: float):
        super().__init__(daemon=True)
        self.namespace = namespace
        self.out_path = out_path
        self.interval = interval
        self._stop_event = threading.Event()
        self.errors = 0

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        with open(self.out_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            writer.writeheader()
            next_at = time.time()
            while not self._stop_event.is_set():
                try:
                    writer.writerow(sample(self.namespace))
                    fh.flush()
                except Exception as exc:  # keep sampling; record the failure
                    self.errors += 1
                    log(f"collector sample failed: {exc}")
                next_at += self.interval
                self._stop_event.wait(max(0.0, next_at - time.time()))
