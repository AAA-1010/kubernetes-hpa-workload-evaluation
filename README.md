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
results/raw/         raw data of the 36 main runs, one folder per run
results/processed/   per-run metrics, summary statistics, time series, failures
results/pilot/       pilot runs used to fix the design (see docs/pilot.md)
results/run_log.txt  log of the main experiment (run order and timing)
results/environment.json  tool and image versions, git commit used
docs/pilot.md        what each pilot run showed and what was changed
paper/               article source (main.tex), DOI-verified bibliography,
                     generated tables and figures
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
python analysis/analyze.py                     # metrics, summary, table
python analysis/failures.py                    # failed requests vs scale-down
python analysis/plots.py                       # figures
```

The analysis scripts only read `results/raw/`, so they can be run on the
committed data without a cluster.

The runner is resumable: finished runs are skipped when it is restarted.
To repeat a single cell, for example: `python experiment/run_experiments.py
--only default:bursty --reps 1`. Remove the cluster afterwards with
`kind delete cluster --name hpa-exp`.

## Results in brief

All 36 runs passed the validity checks (mains power, no sampling gap above
20 s, no control-plane restarts) at the first attempt; they served 1,308,180
requests, 220 of which failed. Summary per workload (mean of three runs;
SLA violation = failed or slower than 250 ms):

| Workload | Fixed | Default | Responsive | Balanced |
|---|---|---|---|---|
| Steady: SLA violations / pod-seconds | 15.0% / 2400 | 5.7% / 4887 | 4.6% / 5599 | 4.4% / 4958 |
| Bursty: SLA violations / pod-seconds | 44.2% / 2400 | 33.5% / 4628 | 26.9% / 3461 | 32.8% / 4525 |
| Periodic: SLA violations / pod-seconds | 38.8% / 2400 | 14.8% / 5355 | 15.4% / 5035 | 11.2% / 5408 |

218 of the 220 failed requests started within 5 s before to 30 s after a
scale-down (`analysis/failures.py`). In the main runs the pods used about
17-22 ms of CPU per request, more than the 12.5 ms measured with one pod in the
pilot. Full results and their discussion are in the article (`paper/main.tex`).

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
  limited to one CPU. At 120 requests per second latency grew although the
  pods were far below their CPU limits, so part of the peak latency comes from
  the shared host. In one run (default, periodic, rep 1) Locust warned once
  about CPU above 90%. Results describe this local setup and are not a
  statement about production clusters.

## License

MIT, see `LICENSE`.
