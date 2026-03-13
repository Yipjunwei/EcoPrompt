"""
EcoPrompt — End-to-End Test Script
===================================
Run this AFTER `docker compose up` has started all containers.

Usage:
    python test_all.py

Tests:
    1. Health checks on all 4 services
    2. Cleaner service — token reduction
    3. AI Model service — T5+LoRA inference
    4. Chat service — full pipeline (cleaner → SLM → cache → Groq)
    5. Analytics service — metrics recorded
    6. Cache hit — second near-identical query returns instantly
"""

import requests
import json
import time
import sys

BASE   = "http://localhost"
CHAT   = f"{BASE}:5000"
CLEAN  = f"{BASE}:5001"
ANALYT = f"{BASE}:5002"
AIMOD  = f"{BASE}:5003"

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
INFO = "\033[94m→\033[0m"

def check(label, ok, detail=""):
    mark = PASS if ok else FAIL
    print(f"  {mark} {label}" + (f"  [{detail}]" if detail else ""))
    return ok

results = []

print("\n" + "="*55)
print("  EcoPrompt End-to-End Test")
print("="*55)

# ── 1. Health checks ──────────────────────────────────────────
print("\n[1] Health checks")
for name, url in [("cleaner",   f"{CLEAN}/health"),
                  ("analytics", f"{ANALYT}/health"),
                  ("aimodel",   f"{AIMOD}/health"),
                  ("chat",      f"{CHAT}/api/debug")]:
    try:
        r = requests.get(url, timeout=5)
        ok = r.status_code == 200
        results.append(check(f"{name} service healthy", ok, f"HTTP {r.status_code}"))
    except Exception as e:
        results.append(check(f"{name} service healthy", False, str(e)))

# ── 2. Cleaner service ────────────────────────────────────────
print("\n[2] Cleaner service — rule-based NLP reduction")
try:
    prompt = "Hey! I was just wondering if you could please kindly help me to basically understand, like, what machine learning actually is? I hope that makes sense, thanks in advance!"
    r = requests.post(f"{CLEAN}/clean", json={"text": prompt}, timeout=5)
    d = r.json()
    saved = d.get("saved_tokens", 0)
    pct   = d.get("reduction_pct", 0)
    cleaned = d.get("cleaned", "")
    results.append(check("cleaner returns cleaned text", bool(cleaned)))
    results.append(check(f"token reduction achieved ({saved} tokens, {pct}%)", saved > 0, cleaned[:60]))
except Exception as e:
    results.append(check("cleaner /clean endpoint", False, str(e)))
    results.append(check("token reduction", False))

# ── 3. AI Model service ───────────────────────────────────────
print("\n[3] AI Model service — T5+LoRA shortener")
try:
    r = requests.post(f"{AIMOD}/infer",
                      json={"text": "could you please explain how a REST API works if that is okay"},
                      timeout=15)
    d = r.json()
    out = d.get("query", "")
    results.append(check("aimodel /infer returns query", bool(out), out[:60]))
except Exception as e:
    results.append(check("aimodel /infer endpoint", False, str(e)))

# ── 4. Chat pipeline inspect (dry run — no LLM call) ─────────
print("\n[4] Chat service — pipeline inspect (dry run)")
try:
    prompt = "Hi there, so basically I just wanted to ask, could you please explain how a REST API works if that's okay? Does that make sense? Let me know if you need more context."
    r = requests.post(f"{CHAT}/api/inspect", json={"query": prompt}, timeout=20)
    d = r.json()
    pipe = d.get("pipeline", {})
    toks = d.get("tokens", {})
    results.append(check("inspect endpoint responds",       r.status_code == 200))
    results.append(check("stage1 rule-based clean ran",    bool(pipe.get("stage1_rule_based"))))
    results.append(check("stage2 SLM ran",                 pipe.get("slm_used", False),
                         pipe.get("stage2_slm", "")[:50]))
    saved = toks.get("saved", 0)
    pct   = toks.get("reduction_pct", 0)
    results.append(check(f"combined token saving ({saved} tokens, {pct}%)", saved > 0))
except Exception as e:
    results.append(check("chat /api/inspect endpoint", False, str(e)))

# ── 5. Full chat → Groq (live LLM call) ──────────────────────
print("\n[5] Full chat pipeline — live Groq call")
try:
    r = requests.post(f"{CHAT}/api/clean",
                      json={"query": "What is photosynthesis?"},
                      timeout=30)
    d = r.json()
    output  = d.get("output", "")
    metrics = d.get("metrics", {})
    results.append(check("chat /api/clean returns LLM output", bool(output), output[:60]))
    results.append(check("metrics dict present", bool(metrics),
                         f"raw={metrics.get('raw_tokens',0)} final={metrics.get('clean_tokens',0)}"))
except Exception as e:
    results.append(check("chat /api/clean endpoint", False, str(e)))
    results.append(check("metrics present", False))

# ── 6. Cache hit test ─────────────────────────────────────────
print("\n[6] Semantic cache — near-duplicate query")
try:
    # Near-duplicate (no question mark)
    r = requests.post(f"{CHAT}/api/clean",
                      json={"query": "What is photosynthesis"},
                      timeout=15)
    d = r.json()
    hit = d.get("cache_hit", False)
    results.append(check("cache hit on near-duplicate", hit,
                         f"score={d.get('_trace', {}).get('cache_score', 'n/a')}"))
except Exception as e:
    results.append(check("cache hit test", False, str(e)))

# ── 7. Analytics service ──────────────────────────────────────
print("\n[7] Analytics — metrics recorded")
try:
    r = requests.get(f"{ANALYT}/metrics", timeout=5)
    d = r.json()
    total = d.get("total_requests", 0)
    saved_tokens = d.get("total_saved_tokens", 0)
    results.append(check(f"analytics has recorded events ({total} requests)", total > 0))
    results.append(check(f"total tokens saved tracked ({saved_tokens} tokens)", saved_tokens >= 0))
except Exception as e:
    results.append(check("analytics /metrics endpoint", False, str(e)))

# ── Summary ───────────────────────────────────────────────────
passed = sum(results)
total  = len(results)
print("\n" + "="*55)
color = "\033[92m" if passed == total else "\033[93m"
print(f"  {color}Results: {passed}/{total} passed\033[0m")
print("="*55)

if passed < total:
    print("\n  Tip: make sure all containers are healthy:")
    print("    docker compose ps")
    print("  View logs for a failing service:")
    print("    docker compose logs cleaner")
    print("    docker compose logs aimodel")
    print("    docker compose logs chat")
    sys.exit(1)
else:
    print("\n  All tests passed! Open the apps:")
    print("    Chat:      http://localhost:5000")
    print("    Dashboard: http://localhost:5002/dashboard")
    sys.exit(0)
