import os
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel

# ===== CONFIG =====
BASE_MODEL = os.environ.get("BASE_MODEL", "Qwen/Qwen2.5-3B-Instruct")
ADAPTER_DIR = "out_lora_query_cleaner"
MERGED_DIR = "out_merged_query_cleaner"
# ==================

def main():
    print("Loading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(ADAPTER_DIR, use_fast=True)

    print("Loading base model...")
    base_model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        torch_dtype=torch.float16,
        device_map="mps"  # Mac. Change to "auto" for Linux/GPU
    )

    print("Loading LoRA adapter...")
    model = PeftModel.from_pretrained(base_model, ADAPTER_DIR)

    print("Merging LoRA into base model...")
    merged_model = model.merge_and_unload()

    print("Saving merged model...")
    merged_model.save_pretrained(
        MERGED_DIR,
        safe_serialization=True
    )

    tokenizer.save_pretrained(MERGED_DIR)

    print(f"✅ Merged model saved to: {MERGED_DIR}")


if __name__ == "__main__":
    main()