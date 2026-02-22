"""
Analytics Service
-----------------
Stores cleaning events and serves aggregated metrics via REST API.
Also serves the /dashboard HTML page.

Microservice boundary: port 5002.
In production → its own container with a real DB (Postgres/Redis).
For this PoC, state is stored in-memory (resets on restart).
"""

from flask import Flask, request, jsonify, render_template, send_from_directory
from collections import deque
from datetime import datetime
import os

app = Flask(__name__, template_folder="templates", static_folder="static")

# ── In-memory store (swap for Redis/Postgres in production) ──────────────────
_events: deque = deque(maxlen=1000)   # last 1000 cleaning events
_totals = {
    "total_requests":      0,
    "total_raw_tokens":    0,
    "total_clean_tokens":  0,
    "total_saved_tokens":  0,
    "total_saved_cost_usd":  0.0,
    "total_saved_energy_wh": 0.0,
    "total_saved_co2_g":     0.0,
}


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "analytics"})


@app.route("/event", methods=["POST"])
def record_event():
    """Called by the chat service after each clean+LLM cycle."""
    data = request.get_json(silent=True) or {}
    required = ["raw_tokens", "clean_tokens", "saved_tokens",
                "saved_cost_usd", "saved_energy_wh", "saved_co2_g",
                "reduction_pct"]
    if not all(k in data for k in required):
        return jsonify({"error": "Missing fields"}), 400

    event = {**data, "timestamp": datetime.utcnow().isoformat()}
    _events.appendleft(event)

    _totals["total_requests"]        += 1
    _totals["total_raw_tokens"]      += data["raw_tokens"]
    _totals["total_clean_tokens"]    += data["clean_tokens"]
    _totals["total_saved_tokens"]    += data["saved_tokens"]
    _totals["total_saved_cost_usd"]  += data["saved_cost_usd"]
    _totals["total_saved_energy_wh"] += data["saved_energy_wh"]
    _totals["total_saved_co2_g"]     += data["saved_co2_g"]

    return jsonify({"status": "recorded"})


@app.route("/metrics")
def metrics():
    """Returns current aggregate totals + recent event list."""
    n = _totals["total_requests"]
    avg_reduction = (
        sum(e["reduction_pct"] for e in _events) / len(_events)
        if _events else 0
    )
    return jsonify({
        **_totals,
        "avg_reduction_pct": round(avg_reduction, 1),
        "recent_events": list(_events)[:20],
    })


@app.route("/reset", methods=["POST"])
def reset():
    _events.clear()
    for k in _totals:
        _totals[k] = 0 if isinstance(_totals[k], int) else 0.0
    return jsonify({"status": "reset"})


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


if __name__ == "__main__":
    port = int(os.getenv("ANALYTICS_PORT", 5002))
    app.run(host="0.0.0.0", port=port, debug=True)
