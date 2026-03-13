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
import tiktoken
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from collections import OrderedDict
import re
import os

load_dotenv()

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("SECRET_KEY is not set — check your .env or Docker env_file")

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


# ── Semantic similarity cache ─────────────────────────────────────────────────
# Eliminates redundant LLM invocations by caching responses for semantically
# near-identical queries. Uses TF-IDF + cosine similarity as a lightweight
# proxy for semantic equivalence.

CACHE_SIMILARITY_THRESHOLD = float(os.getenv("CACHE_SIMILARITY_THRESHOLD", "0.92"))
CACHE_MAX_SIZE = int(os.getenv("CACHE_MAX_SIZE", "200"))
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "10"))  # cap session cookie size

# ── Pricing / emissions constants (Groq Llama-3.1-8B, US EPA 2024) ──────────
COST_PER_1M_TOKENS  = float(os.getenv("COST_PER_1M_TOKENS",  "0.05"))   # USD
ENERGY_PER_1K_TOKENS = float(os.getenv("ENERGY_PER_1K_TOKENS", "0.001"))  # Wh
CO2_PER_WH           = float(os.getenv("CO2_PER_WH",           "0.4"))    # g CO2/Wh

# ── Service call timeouts ────────────────────────────────────────────────────
TIMEOUT_CLEANER   = int(os.getenv("TIMEOUT_CLEANER",   "5"))
TIMEOUT_AIMODEL   = int(os.getenv("TIMEOUT_AIMODEL",   "10"))
TIMEOUT_ANALYTICS = int(os.getenv("TIMEOUT_ANALYTICS", "3"))

# ── SLM over-compression guard ───────────────────────────────────────────────
SLM_MIN_LENGTH_RATIO = float(os.getenv("SLM_MIN_LENGTH_RATIO", "0.4"))


class SemanticCache:
    """
    In-memory semantic similarity cache.
    Stores (query, response) pairs and returns cached responses when an
    incoming query exceeds the cosine-similarity threshold against any
    previously cached query.
    """

    def __init__(self, threshold: float = CACHE_SIMILARITY_THRESHOLD, max_size: int = CACHE_MAX_SIZE):
        self.threshold = threshold
        self.max_size = max_size
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._vectorizer = TfidfVectorizer()
        self._fitted = False

    def _refit(self):
        """Refit the TF-IDF vectorizer on current cache keys."""
        keys = list(self._cache.keys())
        if keys:
            self._vectorizer = TfidfVectorizer()
            self._vectorizer.fit(keys)
            self._fitted = True
        else:
            self._fitted = False

    def lookup(self, query: str) -> tuple[str | None, float]:
        """
        Search for a semantically similar cached query.
        Returns (cached_response, similarity_score) or (None, 0.0).
        """
        if not query or not query.strip():
            return None, 0.0
        if not self._fitted or not self._cache:
            return None, 0.0

        keys = list(self._cache.keys())
        try:
            corpus_vectors = self._vectorizer.transform(keys)
            query_vector = self._vectorizer.transform([query])
            similarities = cosine_similarity(query_vector, corpus_vectors)[0]
            best_idx = int(np.argmax(similarities))
            best_score = float(similarities[best_idx])

            if best_score >= self.threshold:
                best_key = keys[best_idx]
                self._cache.move_to_end(best_key)
                return self._cache[best_key], best_score
        except Exception:
            pass

        return None, 0.0

    def store(self, query: str, response: str):
        """Cache a query-response pair and evict oldest if full."""
        if not query or not query.strip():
            return
        if query in self._cache:
            self._cache.move_to_end(query)
            self._cache[query] = response
        else:
            if len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            self._cache[query] = response
        self._refit()


semantic_cache = SemanticCache(
    threshold=CACHE_SIMILARITY_THRESHOLD,
    max_size=CACHE_MAX_SIZE,
)

# ── Tiktoken encoder (cl100k_base — close approximation for Llama 3.1) ──────
enc = tiktoken.get_encoding("cl100k_base")

# ── Token metric helpers (mirrors cleaner_service logic) ──────────────────────

def count_tokens(text: str) -> int:
    """Token count using tiktoken with cl100k_base encoding."""
    return max(1, len(enc.encode(text)))

def tokens_to_cost_usd(tokens: int) -> float:
    return (tokens / 1_000_000) * COST_PER_1M_TOKENS

def tokens_to_energy_wh(tokens: int) -> float:
    return (tokens / 1_000) * ENERGY_PER_1K_TOKENS

def tokens_to_co2_g(energy_wh: float) -> float:
    return energy_wh * CO2_PER_WH

def compute_metrics(original: str, final: str) -> dict:
    """
    Compute savings between the raw original query and the final
    query sent to Groq (after cleaner + SLM). This captures the
    total reduction across both pipeline steps.
    """
    raw_tokens   = count_tokens(original)
    final_tokens = count_tokens(final)
    saved_tokens = max(0, raw_tokens - final_tokens)
    reduction_pct = round(saved_tokens / raw_tokens * 100, 1) if raw_tokens else 0
    saved_energy  = tokens_to_energy_wh(saved_tokens)
    return {
        "raw_tokens":      raw_tokens,
        "clean_tokens":    final_tokens,
        "saved_tokens":    saved_tokens,
        "reduction_pct":   reduction_pct,
        "saved_cost_usd":  round(tokens_to_cost_usd(saved_tokens), 8),
        "saved_energy_wh": round(saved_energy, 6),
        "saved_co2_g":     round(tokens_to_co2_g(saved_energy), 6),
    }


# ── Service calls ─────────────────────────────────────────────────────────────

def call_cleaner(text: str) -> dict:
    try:
        r = http.post(f"{CLEANER_URL}/clean", json={"text": text}, timeout=TIMEOUT_CLEANER)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        approx = count_tokens(text)
        return {
            "original": text, "cleaned": text,
            "raw_tokens": approx, "clean_tokens": approx,
            "saved_tokens": 0, "reduction_pct": 0,
            "saved_cost_usd": 0.0, "saved_energy_wh": 0.0, "saved_co2_g": 0.0,
            "_cleaner_error": str(e),
        }


def call_aimodel(text: str) -> tuple[str, str | None]:
    if not text.strip():
        return "", None
    try:
        r = http.post(f"{AIMODEL_URL}/infer", json={"text": text}, timeout=TIMEOUT_AIMODEL)
        r.raise_for_status()
        result = r.json().get("query", "")

        # Fallback: if output is less than 40% of input length, it over-compressed
        input_words = len(text.split())
        output_words = len(result.split())
        if output_words < input_words * SLM_MIN_LENGTH_RATIO:
            return text, None

        return result, None
    except Exception as e:
        return "", str(e)


def record_analytics(event: dict):
    try:
        http.post(f"{ANALYTICS_URL}/event", json=event, timeout=TIMEOUT_ANALYTICS)
    except Exception:
        pass


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/debug")
def debug():
    return jsonify({
        "model":         MODEL,
        "groq_key_set":  bool(GROQ_API_KEY),
        "cleaner_url":   CLEANER_URL,
        "aimodel_url":   AIMODEL_URL,
        "analytics_url": ANALYTICS_URL,
    })


@app.route("/api/inspect", methods=["GET", "POST"])
def inspect():
    """
    Dry-run endpoint: runs the full cleaning pipeline (cleaner → SLM)
    WITHOUT calling the LLM or recording analytics. Use this to inspect
    exactly what a prompt looks like at each stage before it reaches Groq.

    GET  /api/inspect?q=your+prompt+here
    POST /api/inspect  {"query": "your prompt here"}
    """
    if request.method == "GET":
        query = (request.args.get("q") or request.args.get("query") or "").strip()
    else:
        data  = request.get_json(silent=True) or {}
        query = data.get("query", "").strip()

    if not query:
        return jsonify({"error": "Provide query as ?q= (GET) or {\"query\":...} (POST)"}), 400

    # Stage 1 — rule-based cleaner
    clean_result  = call_cleaner(query)
    cleaned_text  = clean_result["cleaned"] or query

    # Stage 2 — SLM shortener
    short_query, slm_error = call_aimodel(cleaned_text)
    slm_used = bool(short_query)
    if not short_query:
        short_query = cleaned_text

    # Cache status (lookup only — don't store, this is a dry-run)
    cached_response, cache_score = semantic_cache.lookup(short_query)

    raw_tokens   = count_tokens(query)
    final_tokens = count_tokens(short_query)
    saved        = max(0, raw_tokens - final_tokens)

    return jsonify({
        "pipeline": {
            "stage0_original":  query,
            "stage1_rule_based": cleaned_text,
            "stage2_slm":        short_query,
            "slm_used":          slm_used,
            "slm_error":         slm_error,
            "would_be_cache_hit": cached_response is not None,
            "cache_similarity":   round(cache_score, 4) if cached_response else None,
        },
        "tokens": {
            "original":  raw_tokens,
            "after_stage1": count_tokens(cleaned_text),
            "after_stage2": final_tokens,
            "saved":     saved,
            "reduction_pct": round(saved / raw_tokens * 100, 1) if raw_tokens else 0,
        },
        "cleaner_detail": {k: v for k, v in clean_result.items()
                           if k not in ("original", "cleaned")},
    })


@app.route("/api/clean", methods=["POST"])
def clean():
    data  = request.get_json(silent=True) or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "No query provided"}), 400

    trace = {}

    # ── Step 1: Rule-based NLP cleaner ───────────────────────────────────────
    clean_result = call_cleaner(query)
    cleaned_text = clean_result["cleaned"] or query
    trace["step1_cleaned"]       = cleaned_text
    trace["step1_cleaner_error"] = clean_result.get("_cleaner_error")

    # ── Step 2: T5 LoRA SLM shortener ────────────────────────────────────────
    short_query, slm_error = call_aimodel(cleaned_text)
    if not short_query:
        short_query = cleaned_text
        trace["step2_slm_used"]  = False
        trace["step2_slm_error"] = slm_error
    else:
        trace["step2_slm_used"] = True
        trace["step2_short"]    = short_query

    # ── Step 2.5: Semantic similarity cache ────────────────────────────────
    cached_response, cache_score = semantic_cache.lookup(short_query)
    if cached_response is not None:
        trace["cache_hit"]   = True
        trace["cache_score"] = round(cache_score, 4)

        if "history" not in session:
            session["history"] = []
        session["history"].append({"role": "user", "content": short_query})
        session["history"].append({"role": "assistant", "content": cached_response})
        session["history"] = session["history"][-MAX_HISTORY_MESSAGES:]
        session.modified = True

        metrics = compute_metrics(query, short_query)
        trace["total_saved_tokens"]  = metrics["saved_tokens"]
        trace["total_reduction_pct"] = metrics["reduction_pct"]

        record_analytics({
            **metrics,
            "original_query": query,
            "cleaned_query":  cleaned_text,
            "short_query":    short_query,
        })

        return jsonify({
            "output":      cached_response,
            "cleaned":     cleaned_text,
            "short_query": short_query,
            "metrics":     metrics,
            "cache_hit":   True,
            "_trace":      trace,
        })

    trace["cache_hit"] = False

    # ── Step 3: Send to Groq ──────────────────────────────────────────────────
    if "history" not in session:
        session["history"] = []
    session["history"].append({"role": "user", "content": short_query})
    session["history"] = session["history"][-MAX_HISTORY_MESSAGES:]
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
        session["history"] = session["history"][-MAX_HISTORY_MESSAGES:]
        session.modified = True

        # ── Cache this query-response pair for future similarity lookups ──────
        semantic_cache.store(short_query, output)

        # ── Combined metrics: raw original → final query sent to Groq ─────────
        # This captures savings from BOTH the cleaner and the SLM together
        metrics = compute_metrics(query, short_query)
        trace["total_saved_tokens"] = metrics["saved_tokens"]
        trace["total_reduction_pct"] = metrics["reduction_pct"]

        record_analytics({
            **metrics,
            "original_query": query,
            "cleaned_query":  cleaned_text,
            "short_query":    short_query,
        })

        return jsonify({
            "output":      output,
            "cleaned":     cleaned_text,
            "short_query": short_query,
            "metrics":     metrics,
            "cache_hit":   False,
            "_trace":      trace,
        })

    except Exception as e:
        trace["step4_groq_ok"]    = False
        trace["step4_groq_error"] = str(e)
        return jsonify({"error": str(e), "_trace": trace}), 500


@app.route("/api/new", methods=["POST"])
def new_conversation():
    session.pop("history", None)
    return jsonify({"status": "ok"})


@app.route("/api/metrics")
def proxy_metrics():
    try:
        r = http.get(f"{ANALYTICS_URL}/metrics", timeout=TIMEOUT_CLEANER)
        return jsonify(r.json())
    except Exception:
        return jsonify({"error": "Analytics service unavailable"}), 503


@app.route("/api/analytics/reset", methods=["POST"])
def proxy_analytics_reset():
    try:
        r = http.post(f"{ANALYTICS_URL}/reset", timeout=TIMEOUT_CLEANER)
        return jsonify(r.json())
    except Exception:
        return jsonify({"error": "Analytics service unavailable"}), 503


if __name__ == "__main__":
    port = int(os.getenv("CHAT_PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)