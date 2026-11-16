# kubernetes-hpa-workload-evaluation

Experimental evaluation of Kubernetes Horizontal Pod Autoscaler (HPA)
configurations under steady, bursty, and periodic web workloads.

Research article: *Experimental Sensitivity Analysis of Kubernetes Horizontal
Pod Autoscaler Configurations under Steady, Bursty, and Periodic Web
Workloads*. Basim Kafeel (22I-0775) and Ali Aamir Ashraf (22I-1199), FAST-NUCES
Islamabad, BS Computer Science, Section CS-B, Group G01.

Everything needed to reproduce the results is in this repository: the test
application, the Kubernetes manifests, the load-generator scripts, the
experiment runner, the raw measurements and the analysis code.

## What the experiment does

One CPU-bound web service runs in a local single-node Kubernetes cluster
(kind). Four scaling configurations are compared under three traffic patterns,
with three repetitions each (36 runs of 10 minutes):

| Configuration | Scaling | CPU target | min/max pods | Scale-up | Scale-down |
|---|---|---|---|---|---|
| Fixed | none (4 pods) | - | 4/4 | - | - |
| Default | HPA, built-in behaviour | 80% | 2/10 | max(100%, 4 pods)/15 s | 300 s window |
| Responsive | HPA, tuned for speed | 50% | 2/10 | max(200%, 4 pods)/15 s | 60 s window, 50%/15 s |
| Balanced | HPA, tuned for a middle ground | 65% | 2/10 | 2 pods/15 s, 15 s window | 120 s window, 1 pod/30 s |

Workloads (each Locust user sends at most one request per second):
steady (constant), bursty (two sharp 90 s bursts), periodic (raised-cosine
wave with a 200 s period). All values live in `experiment/plan.json`; the
manifests are in `k8s/`.

Per run we record every request (start time, latency, success) and, every
5 s, the replica counts, the HPA's view of CPU, and per-pod CPU and memory
from the Metrics API. The analysis reports mean, p50/p95/p99 latency,
throughput, failure rate, SLA violations, scale-up delay, scaling actions,
average/maximum pods, CPU/memory use and pod-seconds.

## Repository layout

```
app/                 test web service (Flask + Gunicorn) and Dockerfile
k8s/                 kind cluster config, app Deployment/Service, HPA manifests
workloads/           Locust load generator and the workload shapes
experiment/          plan.json, cluster setup, run orchestration, sampler
analysis/            metrics, tables and figures
results/raw/         raw data, one folder per run
results/processed/   per-run metrics, summary statistics, time series
paper/               bibliography (DOI-verified), generated tables and figures
```

## Requirements

- Docker (Docker Desktop on Windows/macOS), with at least 4 CPUs and 6 GB
  memory assigned
- kind and kubectl
- Python 3.10+ with `pip install -r requirements.txt`

Locust runs inside the official `locustio/locust` container on the kind
Docker network, so it does not need to be installed on the host.

## Reproducing the results

```bash
python experiment/setup_cluster.py            # cluster, Metrics Server, images
python experiment/run_experiments.py --calibrate
python analysis/calibration.py                 # one-pod capacity (pilot)
python experiment/run_experiments.py --dry-run # show the randomised run order
python experiment/run_experiments.py           # all 36 runs (about 7 hours)
python analysis/analyze.py
python analysis/plots.py
```

The runner is resumable: finished runs are skipped when it is restarted.
To repeat a single cell, for example: `python experiment/run_experiments.py
--only default:bursty --reps 1`. Remove the cluster afterwards with
`kind delete cluster --name hpa-exp`.

## Notes on method

- The namespace is deleted and re-created before every run; the run starts
  only after all pods are ready, the Metrics API reports them and (for HPA
  configurations) the HPA has read its first CPU value, followed by a 30 s
  settle period.
- Runs execute in a random order with a fixed seed to spread slow drift of
  the host across configurations.
- Requests use `Connection: close` so that kube-proxy distributes new
  connections across all ready pods, including pods added during a run.
- Load generator and cluster share one machine; the Locust container is
  limited to one CPU. Results describe this local setup and are not a
  statement about production clusters.

## License

MIT, see `LICENSE`.
