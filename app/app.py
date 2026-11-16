"""CPU-bound test web service used as the system under test.

Each request to /work performs a fixed number of SHA-256 rounds, so the CPU
cost per request is deterministic and the load on a pod grows linearly with
the request rate. This makes CPU-based autoscaling behaviour easy to observe.
"""

import hashlib
import os
import socket
import time

from flask import Flask, jsonify, request

app = Flask(__name__)

DEFAULT_ROUNDS = int(os.environ.get("WORK_ROUNDS", "10000"))
MAX_ROUNDS = 1_000_000
POD_NAME = socket.gethostname()


@app.get("/healthz")
def healthz():
    return "ok", 200


@app.get("/work")
def work():
    raw = request.args.get("rounds", str(DEFAULT_ROUNDS))
    try:
        rounds = int(raw)
    except ValueError:
        return jsonify(error="rounds must be an integer"), 400
    if rounds < 1 or rounds > MAX_ROUNDS:
        return jsonify(error=f"rounds must be between 1 and {MAX_ROUNDS}"), 400

    start = time.perf_counter()
    digest = b"hpa-experiment"
    for _ in range(rounds):
        digest = hashlib.sha256(digest).digest()
    service_ms = (time.perf_counter() - start) * 1000.0

    return jsonify(
        pod=POD_NAME,
        rounds=rounds,
        digest=digest.hex()[:16],
        service_ms=round(service_ms, 3),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
