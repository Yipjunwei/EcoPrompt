"""
Chat Service
------------
Main user-facing Flask app. Orchestrates:
  1. Cleaner Service   → rule-based NLP cleaning          (port 5001)
  2. AI Model Service  → T5 LoRA query shortener           (port 5003)
  3. Groq LLM API      → final AI response
  4. Analytics Service → record the event                  (port 5002)

Microservice boundary: port 5000.
"""

from flask import Flask, render_template, request, jsonify, session
from groq import Groq
from dotenv import load_dotenv
import requests as http
import os

load_dotenv()

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key")

# ── Groq setup ────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is not set — check your .env or Docker env_file")

groq_client = Groq(api_key=GROQ_API_KEY)
MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# Service URLs — override via env vars in Docker/K8s
CLEANER_URL   = os.getenv("CLEANER_URL",   "http://localhost:5001")
ANALYTICS_URL = os.getenv("ANALYTICS_URL", "http://localhost:5002")
AIMODEL_URL   = os.getenv("AIMODEL_URL",   "http://localhost:5003")


# ── Service calls ─────────────────────────────────────────────────────────────

def call_cleaner(text: str) -> dict:
    try:
        r = http.post(f"{CLEANER_URL}/clean", json={"text": text}, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        approx = max(1, round(len(text.split()) / 0.75))
        return {
            "original": text, "cleaned": text,
            "raw_tokens": approx, "clean_tokens": approx,
            "saved_tokens": 0, "reduction_pct": 0,
            "saved_cost_usd": 0.0, "saved_energy_wh": 0.0, "saved_co2_g": 0.0,
            "_cleaner_error": str(e),
        }


def call_aimodel(text: str) -> tuple[str, str | None]:
    """Returns (result, error_or_None)"""
    try:
        r = http.post(f"{AIMODEL_URL}/infer", json={"text": text}, timeout=10)
        r.raise_for_status()
        return r.json().get("query", ""), None
    except Exception as e:
        return "", str(e)


def record_analytics(event: dict):
    try:
        http.post(f"{ANALYTICS_URL}/event", json=event, timeout=3)
    except Exception:
        pass


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/debug")
def debug():
    """Hit this in your browser to see the current config."""
    return jsonify({
        "model":        MODEL,
        "groq_key_set": bool(GROQ_API_KEY),
        "cleaner_url":  CLEANER_URL,
        "aimodel_url":  AIMODEL_URL,
        "analytics_url":ANALYTICS_URL,
    })


@app.route("/api/clean", methods=["POST"])
def clean():
    data  = request.get_json(silent=True) or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "No query provided"}), 400

    trace = {}   # collects debug info returned to frontend

    # ── Step 1: Rule-based NLP cleaner ───────────────────────────────────────
    clean_result = call_cleaner(query)
    cleaned_text = clean_result["cleaned"]
    trace["step1_cleaned"]       = cleaned_text
    trace["step1_cleaner_error"] = clean_result.get("_cleaner_error")

    # ── Step 2: T5 LoRA SLM shortener ────────────────────────────────────────
    short_query, slm_error = call_aimodel(cleaned_text)
    if not short_query:
        short_query = cleaned_text
        trace["step2_slm_used"] = False
        trace["step2_slm_error"] = slm_error
    else:
        trace["step2_slm_used"]  = True
        trace["step2_short"]     = short_query

    # ── Step 3: Send shortened query to Groq ─────────────────────────────────
    if "history" not in session:
        session["history"] = []
    session["history"].append({"role": "user", "content": short_query})
    session.modified = True

    trace["step3_sent_to_groq"] = short_query
    trace["step3_model"]        = MODEL

    try:
        response = groq_client.chat.completions.create(
            model=MODEL,
            messages=session["history"],
        )
        output = response.choices[0].message.content
        trace["step4_groq_ok"] = True

        session["history"].append({"role": "assistant", "content": output})
        session.modified = True

        record_analytics({
            "raw_tokens":      clean_result.get("raw_tokens",      0),
            "clean_tokens":    clean_result.get("clean_tokens",    0),
            "saved_tokens":    clean_result.get("saved_tokens",    0),
            "reduction_pct":   clean_result.get("reduction_pct",   0),
            "saved_cost_usd":  clean_result.get("saved_cost_usd",  0.0),
            "saved_energy_wh": clean_result.get("saved_energy_wh", 0.0),
            "saved_co2_g":     clean_result.get("saved_co2_g",     0.0),
            "original_query":  query,
            "cleaned_query":   cleaned_text,
            "short_query":     short_query,
        })

        return jsonify({
            "output":      output,
            "cleaned":     cleaned_text,
            "short_query": short_query,
            "metrics": {
                "saved_tokens":    clean_result.get("saved_tokens",    0),
                "reduction_pct":   clean_result.get("reduction_pct",   0),
                "saved_cost_usd":  clean_result.get("saved_cost_usd",  0.0),
                "saved_energy_wh": clean_result.get("saved_energy_wh", 0.0),
                "saved_co2_g":     clean_result.get("saved_co2_g",     0.0),
            },
            "_trace": trace,   # debug info — remove in production
        })

    except Exception as e:
        trace["step4_groq_ok"]    = False
        trace["step4_groq_error"] = str(e)
        # Return the trace so the frontend can show exactly what failed
        return jsonify({
            "error":   str(e),
            "_trace":  trace,
        }), 500


@app.route("/api/new", methods=["POST"])
def new_conversation():
    session.pop("history", None)
    return jsonify({"status": "ok"})


@app.route("/api/metrics")
def proxy_metrics():
    try:
        r = http.get(f"{ANALYTICS_URL}/metrics", timeout=5)
        return jsonify(r.json())
    except Exception:
        return jsonify({"error": "Analytics service unavailable"}), 503


if __name__ == "__main__":
    port = int(os.getenv("CHAT_PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)