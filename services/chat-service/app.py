"""
Chat Service
------------
Main user-facing Flask app. Orchestrates:
  1. Cleaner Service  → clean the raw prompt
  2. Groq LLM API    → get the AI response
  3. Analytics Service → record the event

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

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# Service URLs — override via env vars in Docker/K8s
CLEANER_URL   = os.getenv("CLEANER_URL",   "http://localhost:5001")
ANALYTICS_URL = os.getenv("ANALYTICS_URL", "http://localhost:5002")


def call_cleaner(text: str) -> dict:
    """Call the cleaner microservice. Falls back gracefully if unreachable."""
    try:
        r = http.post(f"{CLEANER_URL}/clean", json={"text": text}, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[chat-service] Cleaner unreachable: {e}")
        # Graceful degradation: pass raw prompt through unchanged
        approx = max(1, round(len(text.split()) / 0.75))
        return {
            "original": text, "cleaned": text,
            "raw_tokens": approx, "clean_tokens": approx,
            "saved_tokens": 0, "reduction_pct": 0,
            "saved_cost_usd": 0.0, "saved_energy_wh": 0.0, "saved_co2_g": 0.0,
        }


def record_analytics(event: dict):
    """Fire-and-forget to analytics service."""
    try:
        http.post(f"{ANALYTICS_URL}/event", json=event, timeout=3)
    except Exception as e:
        print(f"[chat-service] Analytics unreachable: {e}")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/clean", methods=["POST"])
def clean():
    data  = request.get_json(silent=True) or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "No query provided"}), 400

    # ── Step 1: Clean the prompt ──────────────────────────────────────────────
    clean_result = call_cleaner(query)
    cleaned_text = clean_result["cleaned"]

    # ── Step 2: Maintain conversation history (use CLEANED text) ─────────────
    if "history" not in session:
        session["history"] = []
    session["history"].append({"role": "user", "content": cleaned_text})
    session.modified = True

    # ── Step 3: Call LLM ─────────────────────────────────────────────────────
    try:
        response = groq_client.chat.completions.create(
            model=MODEL,
            messages=session["history"]
        )
        output = response.choices[0].message.content
        usage  = response.usage  # actual token counts from Groq

        session["history"].append({"role": "assistant", "content": output})
        session.modified = True

        # ── Step 4: Record analytics event ───────────────────────────────────
        event = {
            "raw_tokens":      clean_result["raw_tokens"],
            "clean_tokens":    clean_result["clean_tokens"],
            "saved_tokens":    clean_result["saved_tokens"],
            "reduction_pct":   clean_result["reduction_pct"],
            "saved_cost_usd":  clean_result["saved_cost_usd"],
            "saved_energy_wh": clean_result["saved_energy_wh"],
            "saved_co2_g":     clean_result["saved_co2_g"],
            "llm_prompt_tokens":     getattr(usage, "prompt_tokens",     None),
            "llm_completion_tokens": getattr(usage, "completion_tokens", None),
        }
        record_analytics(event)

        return jsonify({
            "output":    output,
            "query":     query,
            "cleaned":   cleaned_text,
            # Pass all metrics back to the frontend
            "metrics": {
                "raw_tokens":      clean_result["raw_tokens"],
                "clean_tokens":    clean_result["clean_tokens"],
                "saved_tokens":    clean_result["saved_tokens"],
                "reduction_pct":   clean_result["reduction_pct"],
                "saved_cost_usd":  clean_result["saved_cost_usd"],
                "saved_energy_wh": clean_result["saved_energy_wh"],
                "saved_co2_g":     clean_result["saved_co2_g"],
            }
        })

    except Exception as e:
        print(f"[chat-service] LLM error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/new", methods=["POST"])
def new_conversation():
    session.pop("history", None)
    return jsonify({"status": "ok"})


@app.route("/api/metrics")
def proxy_metrics():
    """Proxy to analytics service so the chat frontend can poll it."""
    try:
        r = http.get(f"{ANALYTICS_URL}/metrics", timeout=5)
        return jsonify(r.json())
    except Exception:
        return jsonify({"error": "Analytics service unavailable"}), 503


if __name__ == "__main__":
    port = int(os.getenv("CHAT_PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
