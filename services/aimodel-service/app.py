"""
AI Model Service
----------------
Loads base model + LoRA adapter and exposes a /infer endpoint.

Microservice boundary: this runs as its own Flask app on port 5002.
In production → Docker container, sits behind the API gateway.
"""

import os
import re
import torch
from flask import Flask, request, jsonify

from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
from peft import PeftModel

app = Flask(__name__)

BASE_MODEL = os.environ.get("BASE_MODEL", "t5-small")
# IMPORTANT: point to a checkpoint folder, not the OUT_DIR root
LORA_PATH = os.environ.get("LORA_PATH", "../../slm-training/out_lora_t5_query_cleaner")
PORT = int(os.environ.get("AIMODEL_PORT", "5003"))

MAX_SOURCE_LEN = int(os.environ.get("MAX_SOURCE_LEN", "256"))
MAX_NEW_TOKENS = int(os.environ.get("MAX_NEW_TOKENS", "32"))

PROMPT_HEAD = os.environ.get(
    "PROMPT_HEAD",
    "query: "
)

def normalize_text(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

# ---- Load model once ----
tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, use_fast=True)
base = AutoModelForSeq2SeqLM.from_pretrained(BASE_MODEL)
model = PeftModel.from_pretrained(base, LORA_PATH)
model.eval()

device = torch.device("cpu")
model.to(device)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "aimodel"})

@app.route("/infer", methods=["POST"])
def infer():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "No text provided"}), 400

    src = normalize_text(PROMPT_HEAD + text)

    inputs = tokenizer(
        src,
        return_tensors="pt",
        max_length=MAX_SOURCE_LEN,
        truncation=True,
    ).to(device)

    with torch.no_grad():
        out_ids = model.generate(
            **inputs,
            max_new_tokens=MAX_NEW_TOKENS,
            no_repeat_ngram_size=3,
            repetition_penalty=1.2,
        )

    pred = tokenizer.decode(out_ids[0], skip_special_tokens=True)
    pred = normalize_text(pred)

    return jsonify({"query": pred})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=True)