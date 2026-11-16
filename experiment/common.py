"""Shared helpers for the experiment scripts (paths, plan loading, kubectl)."""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLAN_PATH = REPO / "experiment" / "plan.json"
RAW_DIR = REPO / "results" / "raw"


def load_plan() -> dict:
    with open(PLAN_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd: list[str], check: bool = True, capture: bool = True, timeout: float | None = None,
        input_text: str | None = None) -> subprocess.CompletedProcess:
    """Run a command and fail loudly (with its stderr) if it does not succeed."""
    try:
        proc = subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout,
                              input=input_text)
    except FileNotFoundError:
        sys.exit(f"Command not found: {cmd[0]}. See README.md for prerequisites.")
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() if capture else ""
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{detail}")
    return proc


def kubectl_json(args: list[str], timeout: float = 20) -> dict:
    proc = run(["kubectl", *args, "-o", "json"], timeout=timeout)
    return json.loads(proc.stdout)


def require_tools(tools: list[str]) -> None:
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        sys.exit(f"Missing required tools: {', '.join(missing)}. See README.md.")


def parse_cpu_millicores(value: str) -> float:
    """Convert a Kubernetes CPU quantity (e.g. '12345678n', '250m', '1') to millicores."""
    if value.endswith("n"):
        return int(value[:-1]) / 1e6
    if value.endswith("u"):
        return int(value[:-1]) / 1e3
    if value.endswith("m"):
        return float(value[:-1])
    return float(value) * 1000


def parse_mem_mib(value: str) -> float:
    """Convert a Kubernetes memory quantity (e.g. '23456Ki', '128Mi') to MiB."""
    units = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "K": 1000 / 1024**2, "M": 1e6 / 1024**2,
             "G": 1e9 / 1024**2}
    for suffix, factor in units.items():
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * factor
    return float(value) / 1024**2
