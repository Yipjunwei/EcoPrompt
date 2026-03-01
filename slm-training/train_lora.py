import os
import re
import torch

from datasets import load_dataset
from transformers import (
    AutoTokenizer,
    AutoModelForSeq2SeqLM,
    DataCollatorForSeq2Seq,
    Seq2SeqTrainingArguments,
    Seq2SeqTrainer,
)
from peft import LoraConfig, get_peft_model, PeftModel


# ----------------------------
# Config
# ----------------------------
BASE_MODEL = os.environ.get("BASE_MODEL", "t5-small")
TRAIN_PATH = "data/train.jsonl"
VAL_PATH = "data/val.jsonl"
OUT_DIR = "out_lora_t5_query_cleaner"

# 🔥 NEW: resume checkpoint (optional)
RESUME_CKPT = os.environ.get("RESUME_CKPT")

MAX_SOURCE_LEN = 256
MAX_TARGET_LEN = 64

PROMPT_HEAD = (
    "query: "

)


def normalize_text(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def preprocess_fn(ex, tokenizer: AutoTokenizer):
    src = PROMPT_HEAD + ex["input"]
    src = normalize_text(src)
    tgt = normalize_text(ex["output"])

    model_inputs = tokenizer(
        src,
        max_length=MAX_SOURCE_LEN,
        truncation=True,
        padding=False,
    )

    labels = tokenizer(
        text_target=tgt,
        max_length=MAX_TARGET_LEN,
        truncation=True,
        padding=False,
    )

    model_inputs["labels"] = labels["input_ids"]
    return model_inputs


def main():
    print("Loading dataset...")
    ds = load_dataset("json", data_files={"train": TRAIN_PATH, "validation": VAL_PATH})

    print("Loading tokenizer...")
    tok = AutoTokenizer.from_pretrained(BASE_MODEL, use_fast=True)

    print("Tokenizing...")
    ds_tok = ds.map(
        lambda ex: preprocess_fn(ex, tok),
        remove_columns=ds["train"].column_names,
        desc="Tokenizing",
    )

    print("Loading base model...")
    model = AutoModelForSeq2SeqLM.from_pretrained(
        BASE_MODEL,
        torch_dtype=torch.float32,
        device_map="mps",
    )
    model.config.use_cache = False

    # ----------------------------
    # LoRA Resume Logic
    # ----------------------------
    if RESUME_CKPT and os.path.isdir(RESUME_CKPT):
        print(f"✅ Resuming LoRA from checkpoint: {RESUME_CKPT}")

        try:
            model = PeftModel.from_pretrained(
                model,
                RESUME_CKPT,
                is_trainable=True
            )
        except TypeError:
            model = PeftModel.from_pretrained(model, RESUME_CKPT)
            for n, p in model.named_parameters():
                if "lora_" in n:
                    p.requires_grad = True

        model.print_trainable_parameters()
        model.train()

    else:
        print("🆕 Creating fresh LoRA adapter")

        lora_cfg = LoraConfig(
            r=8,
            lora_alpha=16,
            lora_dropout=0.05,
            bias="none",
            task_type="SEQ_2_SEQ_LM",
            target_modules=["q", "v"],
        )

        model = get_peft_model(model, lora_cfg)
        model.print_trainable_parameters()
        model.train()

    # ----------------------------
    # Training args
    # ----------------------------
    args = Seq2SeqTrainingArguments(
    output_dir=OUT_DIR,
    per_device_train_batch_size=8,
    per_device_eval_batch_size=8,
    gradient_accumulation_steps=2,
    learning_rate=1e-4,     
    num_train_epochs=2,     
    weight_decay=0.01,      
    logging_steps=50,
    evaluation_strategy="steps",
    eval_steps=500,
    save_steps=500,
    save_total_limit=2,
    predict_with_generate=True,
    fp16=False,
    report_to="none",
)

    data_collator = DataCollatorForSeq2Seq(
        tokenizer=tok,
        model=model,
        padding="longest",
    )

    trainer = Seq2SeqTrainer(
        model=model,
        args=args,
        train_dataset=ds_tok["train"],
        eval_dataset=ds_tok["validation"],
        tokenizer=tok,
        data_collator=data_collator,
    )

    print("Starting training...")

    # 🔥 Resume Trainer state too
    if RESUME_CKPT:
        trainer.train(resume_from_checkpoint=RESUME_CKPT)
    else:
        trainer.train()

    print("Saving LoRA adapter + tokenizer...")
    trainer.save_model(OUT_DIR)
    tok.save_pretrained(OUT_DIR)

    print("✅ Training complete.")


if __name__ == "__main__":
    main()