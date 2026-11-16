"""Locust load generator for the HPA experiments.

Environment variables (set by experiment/run_experiments.py):
  WORKLOAD     steady | bursty | periodic
  PLAN_PATH    path to experiment/plan.json
  REQUEST_LOG  CSV file that receives one row per request

Every request is logged individually (start time, latency, success), so
percentiles and SLA violations are computed from raw data rather than from
Locust's aggregated statistics.
"""

import csv
import json
import os
import threading
import time

from locust import HttpUser, LoadTestShape, constant_throughput, events, task

from shapes import duration_s, spawn_rate, users_at

WORKLOAD = os.environ.get("WORKLOAD", "steady")
with open(os.environ.get("PLAN_PATH", "experiment/plan.json"), encoding="utf-8") as fh:
    PLAN = json.load(fh)
DURATION = duration_s(WORKLOAD, PLAN)

_log_lock = threading.Lock()
_log_file = None
_log_writer = None


@events.test_start.add_listener
def _open_log(environment, **_kwargs):
    global _log_file, _log_writer
    path = os.environ.get("REQUEST_LOG", "requests.csv")
    # Load-generator clock at test start; t = 0 for all time series of the run.
    with open(path + ".start", "w", encoding="utf-8") as marker:
        marker.write(f"{time.time():.4f}\n")
    _log_file = open(path, "w", newline="", encoding="utf-8")
    _log_writer = csv.writer(_log_file)
    _log_writer.writerow(["start_ts", "latency_ms", "success", "status", "error"])


@events.request.add_listener
def _log_request(response_time, exception, response=None, start_time=None, **_kwargs):
    if _log_writer is None:
        return
    status = getattr(response, "status_code", 0) if response is not None else 0
    start = start_time if start_time is not None else time.time() - response_time / 1000.0
    error = "" if exception is None else type(exception).__name__
    with _log_lock:
        _log_writer.writerow(
            [f"{start:.4f}", f"{response_time:.2f}", int(exception is None), status, error]
        )


@events.test_stop.add_listener
def _close_log(environment, **_kwargs):
    global _log_file, _log_writer
    with _log_lock:
        if _log_file is not None:
            _log_file.flush()
            _log_file.close()
        _log_file, _log_writer = None, None


class WebUser(HttpUser):
    wait_time = constant_throughput(1)

    @task
    def work(self):
        # "Connection: close" makes every request open a new TCP connection,
        # so kube-proxy spreads requests across all ready pods, including pods
        # added by the autoscaler during the run. With keep-alive, users would
        # stay pinned to the pods that existed when they connected.
        self.client.get("/work", name="/work", headers={"Connection": "close"}, timeout=10)


class WorkloadShape(LoadTestShape):
    def tick(self):
        t = self.get_run_time()
        if t >= DURATION:
            return None
        return users_at(WORKLOAD, t, PLAN), spawn_rate(WORKLOAD)
