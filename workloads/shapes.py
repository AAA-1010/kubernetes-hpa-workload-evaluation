"""Workload patterns (number of concurrent Locust users over time).

Each user sends at most one request per second (constant throughput), so the
number of users is also the target request rate in requests per second while
the service keeps up. The same functions are used by the load generator and by
the analysis scripts, so the plotted schedule is exactly the one that was run.
"""

import math


def users_at(workload: str, t: float, plan: dict) -> int:
    """Return the target number of users at second t of a run."""
    users = plan["users"]
    base, peak, steady = users["base"], users["peak"], users["steady"]
    spec = plan["workloads"][workload]

    if workload == "steady":
        return steady
    if workload == "bursty":
        for start, end in spec["bursts_s"]:
            if start <= t < end:
                return peak
        return base
    if workload == "periodic":
        period = spec["period_s"]
        # Raised cosine: starts at base, reaches peak at mid-period.
        level = (1 - math.cos(2 * math.pi * t / period)) / 2
        return int(round(base + (peak - base) * level))
    if workload == "calibration":
        # Pilot only: user count rises in equal steps to find one pod's capacity.
        steps = spec["steps_users"]
        index = min(int(t // spec["step_s"]), len(steps) - 1)
        return steps[index]
    raise ValueError(f"unknown workload: {workload}")


def duration_s(workload: str, plan: dict) -> int:
    """Length of a run in seconds."""
    if workload == "calibration":
        spec = plan["workloads"]["calibration"]
        return len(spec["steps_users"]) * spec["step_s"]
    return int(plan["duration_s"])


def spawn_rate(workload: str) -> float:
    """Users started (or stopped) per second when the target changes."""
    # Bursts are meant to be sharp; the other patterns change gradually.
    return {"steady": 10.0, "bursty": 100.0, "periodic": 20.0, "calibration": 20.0}[workload]
