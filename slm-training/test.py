import re
import torch
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
from peft import PeftModel, PeftConfig
import json
import os

TRAINING_DATA_FILE = "collecttrainingdata.jsonl"

OUT_DIR = "out_lora_t5_query_cleaner"

PROMPT_HEAD = (
    "query: "
)

def post_clean(s: str) -> str:
    s = s.strip().lower()
    s = s.splitlines()[0]
    s = re.sub(r"\b(query|rewrite|rewritten|search|intent)\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def norm(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def load():
    peft_cfg = PeftConfig.from_pretrained(OUT_DIR)
    base_name = peft_cfg.base_model_name_or_path

    tok = AutoTokenizer.from_pretrained(base_name, use_fast=True)

    base = AutoModelForSeq2SeqLM.from_pretrained(base_name, torch_dtype=torch.float32)
    model = PeftModel.from_pretrained(base, OUT_DIR)
    model.eval()
    model.to("cpu")  # ✅ CPU only (stable)

    return tok, model

@torch.no_grad()
def run(tok, model, text: str) -> str:
    cleaned_input = norm(text)
    
    # Pass-through if input is too short
    if len(cleaned_input.split()) < 3 or len(cleaned_input) < 5:
        return cleaned_input
    

    prompt = PROMPT_HEAD + cleaned_input
    inputs = tok(prompt, return_tensors="pt", truncation=True, max_length=256).to("cpu")
    bad_words = ["rewrite", "query", "search", "intent"]
    bad_words_ids = [tok(x, add_special_tokens=False).input_ids for x in bad_words]
    bad_words_ids = [ids for ids in bad_words_ids if len(ids) > 0]
    out = model.generate(
        **inputs,
        max_new_tokens=64,
        do_sample=False,
        num_beams=4,
        repetition_penalty=1.2,
        no_repeat_ngram_size=3,
        bad_words_ids=bad_words_ids,
    )
    decoded = tok.decode(out[0], skip_special_tokens=True)
    cleaned = post_clean(decoded)

    # Fallback: if output is less than 40% of input length, it over-compressed
    if len(cleaned.split()) < len(cleaned_input.split()) * 0.4:
        return cleaned_input
    return cleaned
    
if __name__ == "__main__":
    tok, model = load()
    print("Loaded:", type(model).__name__, "| device=cpu")

    while True:
        q = input("\nInput> ").strip()
        if not q:
            break
        
        result = run(tok, model, q)
        print("Output>", result)

        # Save to training data file
        record = {"input": q, "output": result}
        with open(TRAINING_DATA_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")