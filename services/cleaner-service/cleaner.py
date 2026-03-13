"""
Cleaner Service
---------------
Responsible for receiving raw prompts, applying NLP-based cleaning,
and returning cleaned text + real token/cost/energy metrics.

Microservice boundary: this runs as its own Flask app on port 5001.
In production → Docker container, sits behind the API gateway.
"""

from flask import Flask, request, jsonify
import tiktoken
import re
import os

app = Flask(__name__)

# ── Pricing / emissions constants (Groq Llama-3.1-8B, US EPA 2024) ──────────
COST_PER_1M_TOKENS   = float(os.environ.get("COST_PER_1M_TOKENS",  "0.05"))   # USD
ENERGY_PER_1K_TOKENS = float(os.environ.get("ENERGY_PER_1K_TOKENS", "0.001"))  # Wh
CO2_PER_WH           = float(os.environ.get("CO2_PER_WH",           "0.4"))    # g CO2/Wh

# ── Tiktoken encoder (cl100k_base — close approximation for Llama 3.1) ──────
enc = tiktoken.get_encoding("cl100k_base")

# ── Filler phrase list (expandable) ──────────────────────────────────────────
FILLER_PHRASES = [
    # ── Greeting follow-ups ───────────────────────────────────────────────────
    # Broadened to cover "having a wonderful day" etc., not just "doing well"
    r"\bi hope (?:you'?re?|you are) (?:doing (?:well|good|okay)|having a \w+ (?:day|morning|evening|week|time))\b[,.]?\s*",

    # ── Full compound "wondering if" sentences ────────────────────────────────
    r"\bi (?:just )?was wondering if you (?:would|could)(?: be able to)? (?:help me out|help|assist)\b[^.?!]*[.!]?\s*",
    r"\byou would be able to help (?:me )?out\b[^.?!]*[.!]?\s*",
    # Fix: consume the full polite request clause including embedded "if it's not too much trouble"
    r"(?:(?:and|,)\s+)?i (?:just )?was wondering if you (?:would|could)(?: (?:just|potentially|perhaps|maybe))?\s*(?:,\s*if (?:it(?:'s| is)|that(?:'s| is)) not too much trouble\s*,\s*)?",

    # ── Politeness / request openers ─────────────────────────────────────────
    r"\bcan you please\b", r"\bcould you please\b", r"\bplease kindly\b",
    r"\bkindly\b",
    r"\bwould you be able to\b", r"\bi (?:just )?was wondering if\b", r"\bi was (?:just )?wondering if\b",
    r"\bi just wanted to ask,?\s*", r"\bi just wanted to\b",

    # ── Filler words ──────────────────────────────────────────────────────────
    r"\bjust\b(?=\s)", r"\bbasically\b", r"\bactually\b", r"\bliterally\b",
    r"\bkind of\b", r"\bsort of\b", r"\byou know\b", r"\bhonestly\b",
    # Fix: only remove "like" when comma-surrounded (discourse filler), not as preposition
    r"(?:,\s*like\s*,?|(?<=[a-z]{2}),\s*like\b)",

    # ── Idle context phrases — must run AFTER filler words (just/actually) are stripped ──
    # Use \s+ to tolerate extra spaces left by earlier removals
    r"\bi was\s+(?:just\s+)?(?:sitting|standing|lying)\s+(?:here|there)\s+(?:thinking|wondering)\b[^.!?]*?(?:\band\b\s*)?",

    # ── Politeness closers ────────────────────────────────────────────────────
    r"\bif you don't mind\b", r"\bif that's okay\b",
    r",?\s*\bif that makes sense\b[?]?",
    r"\bthanks (?:so |very )?much in advance\b",
    r"\bthanks in advance\b", r"\bthank you in advance\b", r"\bplease and thank you\b",
    r"\breally appreciate it\b",

    # ── Filler closers / hedges ───────────────────────────────────────────────
    r"\bas soon as possible\b", r"\bquickly\b(?=\s)",
    # Note: \bum\b removed from here — handled in pre-pass before opener detection (see clean_prompt)
    r"\buh\b", r"\bhmm+\b",
    # Fix: only strip "so" at sentence/clause boundaries, not mid-sentence (e.g. "so so much")
    r"(?:(?<=\. )|(?<=! )|(?<=\? )|^)so\b,?\s*",
    r"\bi hope you can help\b", r"\b(?:i\s+)?hope (?:this|that) makes sense\b",
    r"\bdoes that make sense\b",
    r"\blet me know if you need more\b[^.!?]*",

    # ── Redundant context / struggle phrases ─────────────────────────────────
    r",?\s*\bi'?ve been trying to (?:figure|work) it out\b[^.?!]*",
    r",?\s*\bbut i'?m (?:sort of |kind of )?struggling\b[^.?!]*",
    r"[,.]?\s*\bneed a quick (?:explanation|answer|overview)\b[^.?!]*",
    r"\bhelp me out with (?:something|this|that)\b[^.?!]*[.!]?\s*",
]

REDUNDANT_OPENERS = [
    r"^(hi|hello|hey)\s+there\b[,!.\s]*",   # "Hi there," before generic match
    r"^(hi|hello|hey|greetings|good\s+(morning|afternoon|evening))\b[,!.\s]*",
    r"^(okay|ok|alright|sure)\b[,.\s]+",
    r"^(so)\b[,.\s]+",
]

# ── Context trimming: patterns that indicate verbose context padding ──────────
CONTEXT_TRIM_PATTERNS = [
    # Overly long preamble / role-setting instructions
    r"(?i)^(you are an? (?:helpful |expert |knowledgeable )?(?:ai |assistant|chatbot|language model)[^.]*\.\s*)+",
    # Repeated instruction framing
    r"(?i)(?:please )?(?:make sure|ensure|remember) (?:to |that )[^.]*\.\s*",
    # Verbose output format instructions that don't affect meaning
    r"(?i)(?:please )?(?:respond|reply|answer) (?:in |with )?(?:a )?(?:concise|brief|short|detailed|comprehensive) (?:manner|way|format|response)[^.]*\.\s*",
    # Excessive politeness wrapping
    r"(?i)^(?:i would (?:really |greatly )?appreciate it if you could |"
    r"it would be (?:really |very )?(?:helpful|great|nice) if you could |"
    r"i'd (?:really )?like (?:it )?if you could )",
]

# ── Token compression: common verbose phrases → compact equivalents ───────────
TOKEN_COMPRESSIONS = [
    (r"\bin order to\b", "to"),
    (r"\bdue to the fact that\b", "because"),
    (r"\bat this point in time\b", "now"),
    (r"\bin the event that\b", "if"),
    (r"\bfor the purpose of\b", "to"),
    (r"\bwith regard to\b", "regarding"),
    (r"\bin spite of the fact that\b", "although"),
    (r"\bin the near future\b", "soon"),
    (r"\bon a daily basis\b", "daily"),
    (r"\ba large number of\b", "many"),
    (r"\bthe majority of\b", "most"),
    (r"\bprior to\b", "before"),
    (r"\bsubsequent to\b", "after"),
    (r"\bin close proximity to\b", "near"),
    (r"\bis able to\b", "can"),
    (r"\bhas the ability to\b", "can"),
]


def count_tokens(text: str) -> int:
    """
    Token count using tiktoken with cl100k_base encoding.
    While cl100k_base was originally designed for GPT-4, Meta's Llama 3
    adopted tiktoken's BPE library for its own tokenizer, making cl100k_base
    a close approximation for Llama 3.1 with variance typically under 3%.
    """
    return max(1, len(enc.encode(text)))


def tokens_to_cost_usd(tokens: int) -> float:
    """
    Groq Llama-3.1-8B input pricing: ~$0.05 per 1M tokens (as of 2025).
    Override via COST_PER_1M_TOKENS env var.
    """
    return (tokens / 1_000_000) * COST_PER_1M_TOKENS


def tokens_to_energy_wh(tokens: int) -> float:
    """
    Estimate: ~0.001 Wh per 1000 input tokens processed on GPU inference.
    Source: rough average from MLPerf inference benchmarks.
    Override via ENERGY_PER_1K_TOKENS env var.
    """
    return (tokens / 1_000) * ENERGY_PER_1K_TOKENS


def tokens_to_co2_g(energy_wh: float) -> float:
    """
    ~0.4 kg CO2 per kWh (US average grid, EPA 2024).
    Override via CO2_PER_WH env var.
    """
    return energy_wh * CO2_PER_WH


def clean_prompt(text: str) -> dict:
    original = text.strip()
    cleaned = original

    # Step 1: Normalize whitespace and unicode junk
    cleaned = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", cleaned)   # zero-width chars
    cleaned = re.sub(r"\r\n|\r", "\n", cleaned)                      # normalize line endings
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)                     # collapse spaces
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)                     # max 2 consecutive newlines


    # Step 1b: Strip leading filler interjections (um/uh/hmm) BEFORE opener detection
    # so that "Um, hello there!" → "hello there!" → caught by REDUNDANT_OPENERS
    cleaned = re.sub(r"^(?:um|uh|hmm+)[,!\s]+", "", cleaned, flags=re.IGNORECASE).strip()

    # Step 2: Remove redundant openers (case-insensitive)
    for pattern in REDUNDANT_OPENERS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

    # Step 3: Strip filler phrases
    for pattern in FILLER_PHRASES:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    # Step 4: Context trimming — remove verbose preamble / role-setting padding
    for pattern in CONTEXT_TRIM_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned).strip()

    # Step 5: Token compression — replace wordy phrases with compact equivalents
    for pattern, replacement in TOKEN_COMPRESSIONS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

    # Step 6: Deduplicate repeated sentences
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    seen = []
    deduped = []
    for s in sentences:
        normalized = re.sub(r"\s+", " ", s.lower().strip())
        if normalized not in seen:
            seen.append(normalized)
            # Capitalize first letter of each kept sentence
            deduped.append(s[0].upper() + s[1:] if s else s)
    cleaned = " ".join(deduped)

    # Step 7: Clean up punctuation artifacts left after phrase removal
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s([,;:.!?])", r"\1", cleaned)
    cleaned = re.sub(r"^[,;:\s]+", "", cleaned)
    cleaned = re.sub(r"[,;:]\s*!", "!", cleaned)  
    cleaned = re.sub(r"[,;:]\s*\?", "?", cleaned)
    # Collapse double-punctuation after phrase removal (e.g. "?!" → "?", "!?" → "!")
    cleaned = re.sub(r"\?\s*!", "?", cleaned)
    cleaned = re.sub(r"!\s*\?", "!", cleaned)
    cleaned = re.sub(r"([.!?])\s*[.!?]+", r"\1", cleaned)
    cleaned = cleaned.strip()
    # Capitalize first letter
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]

    # Step 8: Compute metrics
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
