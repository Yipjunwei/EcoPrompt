"""
Analytics Service
-----------------
Stores cleaning events and serves aggregated metrics via REST API.
Also serves the /dashboard HTML page.
Microservice boundary: port 5002.
State is persisted in Postgres.
"""
from flask import Flask, request, jsonify, render_template
from datetime import datetime, timezone
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import psycopg2.pool
import decimal

app = Flask(__name__, template_folder="templates", static_folder="static")

# ── Postgres connection pool ──────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL")

pool = psycopg2.pool.SimpleConnectionPool(1, 10, dsn=DATABASE_URL)

def get_conn():
    return pool.getconn()

def release_conn(conn):
    pool.putconn(conn)

# ── Schema bootstrap ──────────────────────────────────────────────────────────
def init_db():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS event (
                    id               SERIAL PRIMARY KEY,
                    timestamp        TIMESTAMPTZ NOT NULL,
                    raw_tokens       INTEGER NOT NULL,
                    clean_tokens     INTEGER NOT NULL,
                    saved_tokens     INTEGER NOT NULL,
                    saved_cost_usd   NUMERIC(12, 8) NOT NULL,
                    saved_energy_wh  NUMERIC(12, 6) NOT NULL,
                    saved_co2_g      NUMERIC(12, 6) NOT NULL,
                    reduction_pct    NUMERIC(6, 2)  NOT NULL
                );
            """)
            conn.commit()
    finally:
        release_conn(conn)

init_db()

class DecimalEncoder(app.json_provider_class):
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        return super().default(obj)

app.json_provider_class = DecimalEncoder
app.json = DecimalEncoder(app)

# ── Routes ────────────────────────────────────────────────────────────────────
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

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO event
                    (timestamp, raw_tokens, clean_tokens, saved_tokens,
                     saved_cost_usd, saved_energy_wh, saved_co2_g, reduction_pct)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                datetime.now(timezone.utc),
                data["raw_tokens"], data["clean_tokens"], data["saved_tokens"],
                data["saved_cost_usd"], data["saved_energy_wh"],
                data["saved_co2_g"], data["reduction_pct"],
            ))
            conn.commit()
    finally:
        release_conn(conn)

    return jsonify({"status": "recorded"})


@app.route("/metrics")
def metrics():
    """Returns current aggregate totals + recent event list."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    COUNT(*)                        AS total_requests,
                    COALESCE(SUM(raw_tokens), 0)    AS total_raw_tokens,
                    COALESCE(SUM(clean_tokens), 0)  AS total_clean_tokens,
                    COALESCE(SUM(saved_tokens), 0)  AS total_saved_tokens,
                    COALESCE(SUM(saved_cost_usd), 0)   AS total_saved_cost_usd,
                    COALESCE(SUM(saved_energy_wh), 0)  AS total_saved_energy_wh,
                    COALESCE(SUM(saved_co2_g), 0)      AS total_saved_co2_g,
                    COALESCE(AVG(reduction_pct), 0)    AS avg_reduction_pct
                FROM event;
            """)
            totals = dict(cur.fetchone())

            cur.execute("""
                SELECT * FROM event
                ORDER BY timestamp DESC
                LIMIT 20;
            """)
            recent = [dict(row) for row in cur.fetchall()]
            # Make timestamps JSON-serialisable
            for row in recent:
                row["timestamp"] = row["timestamp"].isoformat()
    finally:
        release_conn(conn)

    totals["total_requests"] = int(totals["total_requests"])
    totals["avg_reduction_pct"] = round(float(totals["avg_reduction_pct"]), 1)
    return jsonify({**totals, "recent_events": recent})


@app.route("/reset", methods=["POST"])
def reset():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE event;")
            conn.commit()
    finally:
        release_conn(conn)
    return jsonify({"status": "reset"})


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


if __name__ == "__main__":
    port = int(os.getenv("ANALYTICS_PORT", 5002))
    app.run(host="0.0.0.0", port=port, debug=True)