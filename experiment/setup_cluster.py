"""Create the local Kubernetes cluster and prepare everything the runs need.

Steps: create the kind cluster, install Metrics Server, build the web app
image and load it into the cluster, pull the Locust image, and record the
software and hardware environment in results/environment.json.

Usage:  python experiment/setup_cluster.py
"""

import json
import os
import platform
import sys
import time

from common import REPO, kubectl_json, load_plan, log, require_tools, run

METRICS_URL = ("https://github.com/kubernetes-sigs/metrics-server/releases/download/"
               "{version}/components.yaml")


def create_cluster(plan: dict) -> None:
    name = plan["cluster_name"]
    clusters = run(["kind", "get", "clusters"]).stdout.split()
    if name in clusters:
        log(f"kind cluster '{name}' already exists")
    else:
        log(f"creating kind cluster '{name}'")
        cmd = ["kind", "create", "cluster", "--config", str(REPO / "k8s" / "kind-config.yaml"),
               "--wait", "180s"]
        if plan.get("kind_node_image"):
            cmd += ["--image", plan["kind_node_image"]]
        run(cmd, capture=False)
    run(["kubectl", "config", "use-context", f"kind-{name}"])


def install_metrics_server(plan: dict) -> None:
    log("installing Metrics Server " + plan["metrics_server_version"])
    run(["kubectl", "apply", "-f", METRICS_URL.format(version=plan["metrics_server_version"])])
    deploy = kubectl_json(["get", "deployment", "metrics-server", "-n", "kube-system"])
    args = deploy["spec"]["template"]["spec"]["containers"][0].get("args", [])
    if "--kubelet-insecure-tls" not in args:
        # kind's kubelet serving certificates are self-signed.
        patch = [{"op": "add", "path": "/spec/template/spec/containers/0/args/-",
                  "value": "--kubelet-insecure-tls"}]
        run(["kubectl", "patch", "deployment", "metrics-server", "-n", "kube-system",
             "--type=json", "-p", json.dumps(patch)])
    run(["kubectl", "rollout", "status", "deployment/metrics-server", "-n", "kube-system",
         "--timeout=240s"], capture=False)
    deadline = time.time() + 240
    while time.time() < deadline:
        if run(["kubectl", "top", "nodes"], check=False).returncode == 0:
            log("Metrics API is serving")
            return
        time.sleep(5)
    sys.exit("Metrics API did not become available; check the metrics-server pod logs.")


def build_images(plan: dict) -> None:
    log("building web app image " + plan["app_image"])
    run(["docker", "build", "-t", plan["app_image"], str(REPO / "app")], capture=False)
    run(["kind", "load", "docker-image", plan["app_image"], "--name", plan["cluster_name"]],
        capture=False)
    log("pulling load generator image " + plan["locust_image"])
    run(["docker", "pull", plan["locust_image"]], capture=False)


def record_environment(plan: dict) -> None:
    def text(cmd):
        proc = run(cmd, check=False)
        return proc.stdout.strip() if proc.returncode == 0 else f"unavailable: {proc.stderr.strip()}"

    docker_info = json.loads(text(["docker", "info", "--format", "{{json .}}"]) or "{}")
    node = kubectl_json(["get", "nodes"])["items"][0]
    env = {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "host": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
            "python": sys.version.split()[0],
        },
        "docker": {
            "server_version": docker_info.get("ServerVersion"),
            "operating_system": docker_info.get("OperatingSystem"),
            "kernel": docker_info.get("KernelVersion"),
            "cpus": docker_info.get("NCPU"),
            "memory_bytes": docker_info.get("MemTotal"),
        },
        "kind": text(["kind", "version"]),
        "kubectl_client": json.loads(text(["kubectl", "version", "-o", "json"])).get("clientVersion", {}).get("gitVersion"),
        "kubernetes_node": {
            "kubelet": node["status"]["nodeInfo"]["kubeletVersion"],
            "container_runtime": node["status"]["nodeInfo"]["containerRuntimeVersion"],
            "os_image": node["status"]["nodeInfo"]["osImage"],
            "allocatable_cpu": node["status"]["allocatable"]["cpu"],
            "allocatable_memory": node["status"]["allocatable"]["memory"],
        },
        "metrics_server": plan["metrics_server_version"],
        "locust": text(["docker", "run", "--rm", plan["locust_image"], "--version"]),
        "app_image_id": text(["docker", "image", "inspect", plan["app_image"], "--format", "{{.Id}}"]),
        "git_commit": text(["git", "-C", str(REPO), "rev-parse", "HEAD"]),
    }
    out = REPO / "results" / "environment.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(env, indent=2), encoding="utf-8")
    log(f"environment recorded in {out.relative_to(REPO)}")


def main() -> None:
    require_tools(["docker", "kind", "kubectl", "git"])
    plan = load_plan()
    create_cluster(plan)
    install_metrics_server(plan)
    build_images(plan)
    record_environment(plan)
    log("setup complete")


if __name__ == "__main__":
    main()
