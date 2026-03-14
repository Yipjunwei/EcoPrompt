# ECO Prompt

> **Proof of concept**: AI companies waste money processing bloated user inputs.
> ECO Prompt cleans prompts *before* sending them to the LLM and shows you exactly how much was saved — in tokens, dollars, energy, and CO₂.

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [Prerequisites & Setup](#prerequisites--setup)
3. [Method 1 — Run Without Docker](#method-1--run-without-docker)
4. [Method 2 — Run With Docker Compose](#method-2--run-with-docker-compose)
5. [Method 3 — Kubernetes Deployment](#method-3--kubernetes-deployment)
6. [Sharing Images With Teammates](#sharing-images-with-teammates)
7. [Test Prompts — See the Savings](#test-prompts--see-the-savings)
8. [Useful Docker Commands](#useful-docker-commands)
9. [Troubleshooting](#troubleshooting)
10. [How Prompt Cleaning Works](#how-prompt-cleaning-works)
11. [Architecture](#architecture)

---

## Project Structure

Your folder should look exactly like this before running anything:

```
eco-prompt/
├── docker-compose.yml
├── .env                          <- YOU create this (see setup below)
├── .env.example                  <- template, do not edit
├── README.md
├── scripts/
│   ├── deploy.sh                 <- Linux/macOS k8s deploy script
│   ├── deploy.ps1                <- Windows k8s deploy script
│   └── test_all.py               <- end-to-end integration tests
├── k8s/                          <- Kubernetes manifests
├── services/
│   ├── chat-service/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── app.py
│   │   └── templates/
│   │       └── index.html
│   ├── cleaner-service/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── cleaner.py
│   ├── analytics-service/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── analytics.py
│   │   └── templates/
│   │       └── dashboard.html
│   └── aimodel-service/
│       ├── Dockerfile
│       ├── requirements.txt
│       └── app.py
└── slm-training/
    ├── train_lora.py
    ├── test.py
    ├── data/
    │   ├── train.jsonl
    │   └── val.jsonl
    └── out_lora_t5_query_cleaner/   <- trained LoRA adapter (mounted into Docker)
```

> Common mistake: index.html must be inside chat-service/templates/ and dashboard.html must be inside analytics-service/templates/. If they are in a root-level templates/ folder, move them now.

---

## Prerequisites & Setup

### 1. Install Python 3.11+

Download from https://www.python.org/downloads/ and install.

### 2. Get a free Groq API key

Go to https://console.groq.com, sign up (no credit card needed), and create an API key. It starts with `gsk_`.

### 3. Create your .env file

In the root eco-prompt/ folder, create a file named .env (no extension) with this content:

```
GROQ_API_KEY=gsk_your_actual_key_here
SECRET_KEY=any-random-string-like-mysecret123
GROQ_MODEL=llama-3.1-8b-instant

POSTGRES_DB=eco
POSTGRES_USER=eco
POSTGRES_PASSWORD=change-to-a-password
```

Never commit this file to Git. It is already in .gitignore. Each teammate creates their own .env with their own Groq key.

### 4. Fix the httpx version (important for Windows)

If you get a TypeError about 'proxies', run:
```
pip install httpx==0.27.2
```

---

## Method 1 — Run Without Docker

Use this for development. Fastest to start, no Docker needed. You need 4 terminal windows open at the same time.

### Terminal 1 — Start the Cleaner Service

```
cd services/cleaner-service
pip install -r requirements.txt
python cleaner.py
```

### Terminal 2 — Start the Analytics Service

```
cd services/analytics-service
pip install -r requirements.txt
python analytics.py
```

### Terminal 3 — Start the Aimodel Service

```
cd services/aimodel-service
pip install -r requirements.txt
python app.py
```

### Terminal 4 — Start the Chat Service

```
cd services/chat-service
pip install -r requirements.txt
python app.py
```

Open the app:
- Chat app: http://localhost:5000
- Analytics dashboard: http://localhost:5002/dashboard

Press Ctrl + C in each terminal to stop.

---

## Method 2 — Run With Docker Compose

Use this to share with teammates or simulate production. Requires Docker Desktop.

### Install Docker Desktop

Download from https://www.docker.com/products/docker-desktop and install.

- Windows: Enable WSL 2 when prompted during install
- Mac M1/M2/M3: Works natively, no extra steps
- Linux: Follow https://docs.docker.com/engine/install/

### First run — build and start everything

```
docker compose up --build
```

Wait until you see all four lines appear:
```
eco-cleaner    | * Running on http://0.0.0.0:5001
eco-analytics  | * Running on http://0.0.0.0:5002
eco-aimodel    | * Running on http://0.0.0.1:5003
eco-chat       | * Running on http://0.0.0.0:5000
```

Open the app:
- Chat app: http://localhost:5000
- Analytics dashboard: http://localhost:5002/dashboard

### Stop the app

Press Ctrl + C, then:
```
docker compose down
```

### Subsequent runs (no rebuild unless code changed)

```
docker compose up
```

### Run in the background (detached mode)

```
docker compose up -d
docker compose logs -f   # watch logs
docker compose down      # stop
```

---

## Method 3 — Kubernetes Deployment

Manifests live in `k8s/`. Deploy scripts live in `scripts/`. Requires Docker Desktop with Kubernetes enabled (or minikube/kind).

### Deploy (one command)

Make sure your `.env` file is filled in, then run:

```bash
# Linux / macOS
./scripts/deploy.sh

# Linux / macOS (also starts ingress port-forward on :8080)
./scripts/deploy.sh --ingress-port-forward

# Windows (PowerShell)
./scripts/deploy.ps1

# Windows (PowerShell, also starts ingress port-forward on :8080)
./scripts/deploy.ps1 -IngressPortForward
```

This builds all 4 images, injects secrets from your `.env`, and applies all k8s manifests. On first run, both deploy scripts install ingress-nginx automatically and wait for it to be ready. `secret.yaml` is restored to its placeholder state afterwards so it stays safe to commit.

Verify everything is running:
```
kubectl get all -n ecoprompt
```

### Access the app

Quick localhost access (recommended for dev):

```bash
kubectl port-forward -n ecoprompt svc/chat-svc 5000:5000
kubectl port-forward -n ecoprompt svc/analytics-svc 5002:5002
```

Open:
- http://localhost:5000
- http://localhost:5002/dashboard

Note: `kubectl port-forward` stays in the foreground by design. Keep those terminals open while using the app.

Ingress access:

```
kubectl get svc -n ingress-nginx ingress-nginx-controller
```

Open `http://<EXTERNAL-IP>/` for the chat app and `http://<EXTERNAL-IP>/dashboard` for analytics.

### Tear down

```
kubectl delete namespace ecoprompt
```

---

## Sharing Images With Teammates

Push your built images to Docker Hub so teammates can run the app without cloning the repo.

### Step 1 — Log in and build

```
docker login

docker build -t YOUR_USERNAME/eco-cleaner:latest   ./services/cleaner-service
docker build -t YOUR_USERNAME/eco-analytics:latest ./services/analytics-service
docker build -t YOUR_USERNAME/eco-aimodel:latest   ./services/aimodel-service
docker build -t YOUR_USERNAME/eco-chat:latest      ./services/chat-service
```

### Step 2 — Push to Docker Hub

```
docker push YOUR_USERNAME/eco-cleaner:latest
docker push YOUR_USERNAME/eco-analytics:latest
docker push YOUR_USERNAME/eco-aimodel:latest
docker push YOUR_USERNAME/eco-chat:latest
```

### Step 3 — Teammates pull and run

Teammates edit docker-compose.yml to use your images instead of building locally. Change each service from:

```yaml
services:
  cleaner:
    build: ./services/cleaner-service
```

To:

```yaml
services:
  cleaner:
    image: YOUR_USERNAME/eco-cleaner:latest
```

Then teammates only need to:

```
cp .env.example .env
# (fill in their own GROQ_API_KEY)
docker compose up
```

---

## Test Prompts — See the Savings

Use these prompts to demonstrate cleaning in action. Check http://localhost:5002/dashboard after all 6 to see the full session analytics.

---

### HIGH SAVINGS — Filler-heavy student prompt

Paste this in:

    Hey! I was just wondering if you could please kindly help me to basically understand, like, what machine learning actually is? I hope that makes sense, thanks in advance!

Cleaned to:

    What is machine learning?

Expected savings: ~25 tokens, ~75% reduction

---

### HIGH SAVINGS — Polite padding

Paste this in:

    Hi there, so basically I just wanted to ask, could you please explain how a REST API works if that's okay? Does that make sense? Let me know if you need more context. Thanks so much in advance!

Cleaned to:

    Explain how a REST API works.

Expected savings: ~30 tokens, ~70% reduction

---

### MEDIUM SAVINGS — Conversational filler

Paste this in:

    Okay so um, I kind of need help understanding the difference between a stack and a queue in data structures, you know? Sort of just need a quick explanation honestly.

Cleaned to:

    Explain the difference between a stack and a queue in data structures.

Expected savings: ~15 tokens, ~45% reduction

---

### MEDIUM SAVINGS — Copy-paste duplication

Paste this in:

    Explain the differences between SQL and NoSQL databases. Explain the differences between SQL and NoSQL databases. I need this for my project report.

Cleaned to:

    Explain the differences between SQL and NoSQL databases. I need this for my project report.

Expected savings: ~12 tokens, ~35% reduction

---

### ZERO SAVINGS — Already clean (important for demo honesty)

Paste this in:

    What is TCP/IP?

Expected savings: 0 tokens, 0% reduction

This one proves the system reports truthfully and does not inflate numbers when the prompt is already lean.

---

### HIGH SAVINGS — Long real-world example (best for demo)

Paste this in:

    Hello! I hope you're doing well. I was just wondering if you would be able to help me out with something. So basically, I kind of need to understand, like, what Docker actually is and how it works, if that makes sense? I've been trying to figure it out but I'm sort of struggling. Could you please kindly give me a simple explanation? Thanks so much in advance, really appreciate it!

Expected savings: ~40 tokens, ~60% reduction

---

### HIGH SAVINGS — Context trimming in action

Paste this in:

    You are a helpful AI assistant. Please make sure to respond in a concise manner. In order to understand machine learning, I need you to explain it to me due to the fact that I have an exam tomorrow.

Cleaned to:

    Explain machine learning to me because I have an exam tomorrow.

Expected savings: ~20 tokens, ~55% reduction (context preamble + token compression)

---

### CACHE HIT — Send a near-duplicate to demonstrate caching

First send:

    What is photosynthesis?

Then send:

    What is photosynthesis

(no question mark — semantically identical). The second query returns instantly from the semantic cache. The `_trace` in the API response will show `cache_hit: true`.

---

### What to check on the dashboard after all 6 prompts

Open http://localhost:5002/dashboard. You should see:

- Total prompts cleaned: 6
- Tokens saved: roughly 120 tokens across the session
- Avg reduction: around 45-50% per prompt
- Bar chart showing raw vs cleaned token counts for each request
- The zero-savings prompt visible in the table, proving the system is honest

---

## Useful Docker Commands

```
# See all running containers and their health status
docker compose ps

# View live logs from all services combined
docker compose logs -f

# View logs from one service only
docker compose logs -f chat

# Rebuild only one service after a code change (faster than full rebuild)
docker compose up --build chat

# Restart one service without touching the others
docker compose restart chat

# Open a terminal shell inside a running container
docker exec -it eco-chat bash

# Check what environment variables are loaded in a container
docker exec eco-chat env

# Stop and remove all containers (code and .env are untouched)
docker compose down

# Free up disk space from old images and stopped containers
docker system prune
```

---

## Troubleshooting

**TypeError: Client.__init__() got an unexpected keyword argument 'proxies'**

httpx version conflict with the groq package. Fix:
```
pip install httpx==0.27.2
```

---

**Port already in use (5000, 5001, or 5002)**

Mac / Linux:
```
lsof -i :5000
kill -9 <PID shown>
```

Windows PowerShell:
```
netstat -ano | findstr :5000
taskkill /PID <PID shown> /F
```

Or change the left number in the port mapping in docker-compose.yml (e.g. 5010:5000 uses port 5010 on your machine instead).

---

**eco-aimodel unhealthy / LoraConfig unexpected keyword argument**

PEFT version mismatch. Clean the adapter config:
```
python3 -c "
import json
path = 'slm-training/out_lora_t5_query_cleaner/adapter_config.json'
with open(path) as f:
    cfg = json.load(f)
safe = {
    'peft_type': cfg.get('peft_type', 'LORA'),
    'task_type': cfg.get('task_type', 'SEQ_2_SEQ_LM'),
    'r': cfg.get('r', 8),
    'lora_alpha': cfg.get('lora_alpha', 16),
    'lora_dropout': cfg.get('lora_dropout', 0.05),
    'bias': cfg.get('bias', 'none'),
    'target_modules': cfg.get('target_modules', ['q', 'v']),
    'base_model_name_or_path': cfg.get('base_model_name_or_path', 't5-small'),
    'inference_mode': True,
}
with open(path, 'w') as f:
    json.dump(safe, f, indent=2)
print('Done')
"
```

---

**401 Unauthorized from Groq**

Your API key is wrong or missing. Check:
```
cat .env
```
The GROQ_API_KEY value must start with gsk_. Confirm it is active at https://console.groq.com.

---

**Analytics dashboard shows no data**

The dashboard auto-refreshes every 5 seconds. Send at least one message in the chat first. If still empty, click the refresh button and check the browser console (F12) for errors.

---

**"relation 'events' does not exist"**

The analytics table was renamed from `events` to `event`. Wipe the old Postgres volume and rebuild:
```
docker compose down -v
docker compose up --build
```

---

**Chat works but cleaning is not happening**

The chat service gracefully falls back if the cleaner service is unreachable — it still sends the raw prompt to the LLM. Make sure the cleaner is running (check Terminal 1 or docker compose ps) and try again.

---

## How Prompt Cleaning Works

Every prompt passes through a 3-stage pipeline before reaching the LLM:

**Stage 1 — Rule-based cleaner (cleaner-service)**

1. Normalize — strips invisible unicode characters, collapses multiple spaces and newlines
2. Remove openers — strips conversational starters like "Hey!", "Hi there, so basically...", "Okay so..."
3. Strip filler phrases — removes ~25 patterns: "can you please", "just", "basically", "I was wondering if", "um", "kind of", "you know", "honestly", "sort of", "thanks in advance", and more
4. Context trimming — removes verbose preambles like "You are a helpful AI assistant. Please respond concisely."
5. Token compression — replaces wordy phrases: "in order to" → "to", "due to the fact that" → "because", etc.
6. Deduplicate sentences — removes repeated sentences from copy-paste
7. Fix artifacts — cleans up orphaned punctuation and double spaces

**Stage 2 — T5 + LoRA shortener (aimodel-service)**

The cleaned prompt is passed to a fine-tuned T5-small model for further shortening. If the output is less than 40% of the input length (over-compression), the system falls back to the Stage 1 result.

**Stage 3 — Semantic cache (chat-service)**

Before calling the LLM, the query is compared against cached queries using TF-IDF cosine similarity. If a match above 0.92 threshold is found, the cached response is returned immediately — no LLM call made.

### How savings are calculated

All metrics are real calculations, not estimates:

| Metric | Formula |
|---|---|
| saved_tokens | count_tokens(original) minus count_tokens(final_query) |
| saved_cost_usd | saved_tokens divided by 1,000,000 multiplied by $0.05 (Groq Llama 3.1 8B input pricing) |
| saved_energy_wh | saved_tokens divided by 1,000 multiplied by 0.001 Wh (MLPerf GPU inference average) |
| saved_co2_g | saved_energy_wh multiplied by 0.4 (US EPA average grid carbon intensity) |

Token count uses tiktoken with `cl100k_base` encoding (close approximation for Llama 3.1, variance under 3%).

---

## Architecture

```
Browser
  localhost:5000  (chat)
  localhost:5002  (dashboard)
       |
       v
chat-service :5000
  Serves chat UI
  Semantic cache (TF-IDF + cosine similarity)
  Orchestrates cleaning pipeline
  Manages conversation history
       |         |              |
       v         v              v
cleaner-      aimodel-      analytics-
service       service       service
:5001         :5003         :5002
  Rule-based    T5 + LoRA     Event store
  NLP cleaning  query         REST API
  Context       shortener     Dashboard HTML
  trimming
       |
       v
  (cache miss only)
Groq Cloud API (LLM)
```

