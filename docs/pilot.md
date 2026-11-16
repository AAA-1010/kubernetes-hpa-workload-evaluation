# Pilot (calibration) runs

The proposal fixed the SLA threshold and load levels only "after initial
application testing". This file records those tests. They were done before
any main run, and their raw data are kept in `results/pilot/`.

Each pilot runs one fixed pod (CPU request 250m, limit 500m) and raises the
offered load in 60 s steps (`calibration` workload in `experiment/plan.json`).
Each Locust user sends at most one request per second.

## Pilot v1 (2026-11-16): `WORK_ROUNDS=20000`, default probe timeouts

Data: `results/pilot/v1_rounds20000_default-probes/` (stopped after about
2 minutes).

| Step | Users | Throughput (req/s) | p50 (ms) | p95 (ms) | Failed |
|---|---|---|---|---|---|
| 1 | 5 | 4.8 | 125 | 1274 | 0 |
| 2 | 10 | 6.1 | 1505 | 2908 | 4 |

Findings:

1. One request cost so much CPU that a single pod saturated at about 6 req/s,
   too few to exercise autoscaling at load levels the laptop can generate
   cleanly.
2. Under saturation the readiness probe (1 s default timeout) failed, the pod
   was removed from the Service and requests were refused
   (`ConnectionRefusedError`). That would mix a probe artefact into the
   failure metric.

Changes before pilot v2: `WORK_ROUNDS` reduced to 5000; readiness probe
timeout 3 s with 3 failures allowed; liveness probe timeout 5 s.

## Pilot v2 (2026-11-16): `WORK_ROUNDS=5000`, relaxed probes -- INVALID

Data: `results/pilot/v2_rounds5000_INVALID-laptop-slept/`. Kept for
transparency, not used.

The run started at 14:06. Windows logged a power-source change at 14:09
(Kernel-Power event 105) and the laptop entered sleep at 14:13 (event 42),
waking at 17:42. The request log has a 12,543 s gap starting at t = 452 s.
Latencies before the gap also do not follow the offered load (for example
p50 of 413 ms at 10 users but 15 ms at 30 users), which is consistent with
the CPU being throttled after the power change. Lesson: all runs must be on
mains power with sleep prevented; the runner now refuses to start on battery.

## Pilot v3 (2026-11-16): aborted before load

Data folder: `results/pilot/v3_aborted-before-load/` (empty). The reset step
timed out deleting the namespace. The cluster had been through the sleep in
pilot v2; its controller manager had restarted 8 times and its scheduler 7
times (lost leader leases). The cluster was deleted and rebuilt.

On the rebuilt cluster the scheduler and controller manager again lost their
leases while the app image was being loaded (etcd answered the lease renewal
after more than 5 s). The cluster was rebuilt once more with etcd's data
directory on the node's tmpfs (`k8s/kind-config.yaml`); after that, no
control-plane restarts occurred. The runner now records control-plane
restart counts before and after every run and discards a run if any changed,
because a controller-manager restart pauses the HPA.

## Pilot v4 (2026-11-16, 19:39-19:48): `WORK_ROUNDS=5000` -- valid

Data: `results/pilot/v4_rounds5000/` (summary in `calibration_summary.csv`).
No sampling gaps, mains power throughout, no control-plane restarts, no
failed requests.

| Users | Throughput (req/s) | Mean (ms) | p50 (ms) | p95 (ms) | Pod CPU (m) |
|---|---|---|---|---|---|
| 5 | 5.0 | 24.1 | 20.8 | 43.3 | 29 |
| 10 | 10.0 | 22.5 | 20.2 | 38.0 | 58 |
| 15 | 15.0 | 47.0 | 34.9 | 119.8 | 117 |
| 20 | 20.0 | 27.0 | 22.1 | 53.0 | 176 |
| 25 | 25.0 | 48.5 | 23.4 | 168.8 | 223 |
| 30 | 30.0 | 25.8 | 20.7 | 53.2 | 189 |
| 40 | 40.0 | 44.7 | 27.1 | 114.4 | 250 |
| 50 | 50.0 | 86.1 | 42.5 | 303.0 | 359 |

One request costs about 7 ms of CPU (359 m at 50 req/s). A single pod
therefore stays below its 500 m limit up to roughly 70 req/s, so pushing four
fixed pods into overload would need about 200 req/s from one Locust process,
which risks making the load generator the bottleneck. The per-request work
was doubled (`WORK_ROUNDS=10000`) so that the same scaling range is reached
at about half the request rate; pilot v5 measures the new cost.

## Pilot v5 (2026-11-16, 19:50-19:59): `WORK_ROUNDS=10000` -- valid, used

Data: `results/pilot/v5_rounds10000/`. No sampling gaps, mains power, no
control-plane restarts, no failed requests.

| Users | Throughput (req/s) | Mean (ms) | p50 (ms) | p95 (ms) | Pod CPU (m) |
|---|---|---|---|---|---|
| 5 | 5.0 | 49.9 | 33.0 | 122.7 | 42 |
| 10 | 10.0 | 47.7 | 33.6 | 113.7 | 99 |
| 15 | 15.0 | 48.0 | 33.4 | 111.5 | 165 |
| 20 | 20.0 | 90.6 | 70.9 | 218.5 | 235 |
| 25 | 25.0 | 98.9 | 83.1 | 273.3 | 262 |
| 30 | 30.0 | 83.6 | 61.7 | 210.1 | 393 |
| 35 | 32.9 | 605.8 | 594.2 | 1494.2 | 388 |
| 40 | 38.3 | 567.1 | 501.6 | 1314.8 | 498 |

Decisions fixed before the main runs:

- **Cost per request:** about 12.5 ms of CPU (393 m at 30 req/s, 498 m at
  38 req/s). One pod saturates at its 500 m limit near 35-40 req/s.
- **SLA threshold: 250 ms.** The unloaded p95 is about 110-120 ms; 250 ms is
  roughly twice that and lies where one pod's p95 crossed as it approached
  full load (218 ms at 20 req/s, 273 ms at 25 req/s). A request violates the
  SLA if it fails or takes longer than 250 ms.
- **Load levels unchanged:** base 20, steady 70, peak 120 users (about the
  same in req/s). Expected demand: about 0.25, 0.9 and 1.5 CPU cores. Four
  fixed pods (2 cores of limit) handle the steady level near their knee and
  are overloaded at the peak; the HPA configurations need between 2 and 10
  pods depending on their CPU target.
