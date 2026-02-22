"""
Cleaner Service
---------------
Responsible for receiving raw prompts, applying NLP-based cleaning,
and returning cleaned text + real token/cost/energy metrics.

Microservice boundary: this runs as its own Flask app on port 5001.
In production → Docker container, sits behind the API gateway.
"""

from flask import Flask, request, jsonify
import re
import os

app = Flask(__name__)

# ── Filler phrase list (expandable) ──────────────────────────────────────────
FILLER_PHRASES = [
    r"\bcan you please\b", r"\bcould you please\b", r"\bplease kindly\b",
    r"\bwould you be able to\b", r"\bi was wondering if\b", r"\bi just wanted to\b",
    r"\bjust\b(?=\s)", r"\bbasically\b", r"\bactually\b", r"\bliterally\b",
    r"\bkind of\b", r"\bsort of\b", r"\byou know\b", r"\blike\b(?=\s+[a-z])",
    r"\bif you don't mind\b", r"\bif that's okay\b", r"\bthanks in advance\b",
    r"\bthank you in advance\b", r"\bplease and thank you\b",
    r"\bas soon as possible\b", r"\bquickly\b(?=\s)",
    r"\bum\b", r"\buh\b", r"\bhmm+\b", r"\bso\b(?=,|\s+[a-z])",
    r"\bi hope you can help\b", r"\bhope this makes sense\b",
    r"\bdoes that make sense\b", r"\blet me know if you need more\b",
]

REDUNDANT_OPENERS = [
    r"^(hi|hello|hey|greetings|good\s+(morning|afternoon|evening))[,!.\s]*",
    r"^(okay|ok|alright|sure)[,.\s]+",
    r"^(so)[,.\s]+",
]


def count_tokens(text: str) -> int:
    """
    Approximate token count using word-boundary split.
    Rule of thumb: 1 token ≈ 0.75 words (OpenAI/Llama tokenizers are similar).
    For production, swap this with tiktoken or the model's actual tokenizer.
    """
    words = len(re.findall(r"\S+", text))
    return max(1, round(words / 0.75))


def tokens_to_cost_usd(tokens: int) -> float:
    """
    Groq Llama-3.1-8B input pricing: ~$0.05 per 1M tokens (as of 2025).
    """
    return (tokens / 1_000_000) * 0.05


def tokens_to_energy_wh(tokens: int) -> float:
    """
    Estimate: ~0.001 Wh per 1000 input tokens processed on GPU inference.
    Source: rough average from MLPerf inference benchmarks.
    """
    return (tokens / 1_000) * 0.001


def tokens_to_co2_g(energy_wh: float) -> float:
    """
    ~0.4 kg CO2 per kWh (US average grid, EPA 2024).
    """
    return energy_wh * 0.4


def clean_prompt(text: str) -> dict:
    original = text.strip()
    cleaned = original

    # Step 1: Normalize whitespace and unicode junk
    cleaned = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", cleaned)   # zero-width chars
    cleaned = re.sub(r"\r\n|\r", "\n", cleaned)                      # normalize line endings
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)                     # collapse spaces
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)                     # max 2 consecutive newlines

    # Step 2: Remove redundant openers (case-insensitive)
    for pattern in REDUNDANT_OPENERS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

    # Step 3: Strip filler phrases
    for pattern in FILLER_PHRASES:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    # Step 4: Deduplicate repeated sentences
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    seen = []
    deduped = []
    for s in sentences:
        normalized = re.sub(r"\s+", " ", s.lower().strip())
        if normalized not in seen:
            seen.append(normalized)
            deduped.append(s)
    cleaned = " ".join(deduped)

    # Step 5: Clean up punctuation artifacts left after phrase removal
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s([,;:.!?])", r"\1", cleaned)
    cleaned = re.sub(r"^[,;:\s]+", "", cleaned)
    cleaned = cleaned.strip()

    # Step 6: Compute metrics
    raw_tokens    = count_tokens(original)
    clean_tokens  = count_tokens(cleaned)
    saved_tokens  = max(0, raw_tokens - clean_tokens)
    reduction_pct = round((saved_tokens / raw_tokens) * 100, 1) if raw_tokens > 0 else 0

    raw_cost    = tokens_to_cost_usd(raw_tokens)
    clean_cost  = tokens_to_cost_usd(clean_tokens)
    saved_cost  = raw_cost - clean_cost

    raw_energy    = tokens_to_energy_wh(raw_tokens)
    clean_energy  = tokens_to_energy_wh(clean_tokens)
    saved_energy  = raw_energy - clean_energy
    saved_co2     = tokens_to_co2_g(saved_energy)

    return {
        "original":        original,
        "cleaned":         cleaned,
        "raw_tokens":      raw_tokens,
        "clean_tokens":    clean_tokens,
        "saved_tokens":    saved_tokens,
        "reduction_pct":   reduction_pct,
        "raw_cost_usd":    round(raw_cost,    8),
        "clean_cost_usd":  round(clean_cost,  8),
        "saved_cost_usd":  round(saved_cost,  8),
        "raw_energy_wh":   round(raw_energy,  6),
        "clean_energy_wh": round(clean_energy,6),
        "saved_energy_wh": round(saved_energy,6),
        "saved_co2_g":     round(saved_co2,   6),
    }


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "cleaner"})


@app.route("/clean", methods=["POST"])
def clean():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "No text provided"}), 400
    result = clean_prompt(text)
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.getenv("CLEANER_PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
