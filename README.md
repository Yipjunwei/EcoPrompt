# ECO Prompt

> **Proof of concept**: AI companies waste money processing bloated user inputs.
> ECO Prompt cleans prompts *before* sending them to the LLM and shows you exactly how much was saved — in tokens, dollars, energy, and CO₂.

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [Prerequisites & Setup](#prerequisites--setup)
3. [Method 1 — Run Without Docker](#method-1--run-without-docker)
4. [Method 2 — Run With Docker Compose](#method-2--run-with-docker-compose)
5. [Sharing Images With Teammates](#sharing-images-with-teammates)
6. [Test Prompts — See the Savings](#test-prompts--see-the-savings)
7. [Useful Docker Commands](#useful-docker-commands)
8. [Troubleshooting](#troubleshooting)
9. [How Prompt Cleaning Works](#how-prompt-cleaning-works)
10. [Architecture](#architecture)
11. [Kubernetes — Future Deployment](#kubernetes--future-deployment)

---

## Project Structure

Your folder should look exactly like this before running anything:

```
eco-prompt/
├── docker-compose.yml
├── .env                          <- YOU create this (see setup below)
├── .env.example                  <- template, do not edit
├── README.md
└── services/
    ├── chat-service/
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   ├── app.py
    │   └── templates/
    │       └── index.html
    ├── cleaner-service/
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   └── cleaner.py
    └── analytics-service/
        ├── Dockerfile
        ├── requirements.txt
        ├── analytics.py
        └── templates/
            └── dashboard.html
```

> Common mistake: index.html must be inside chat-service/templates/ and dashboard.html must be inside analytics-service/templates/. If they are in a root-level templates/ folder, move them now.

---

## Prerequisites & Setup

### 1. Install Python 3.11+

Download from https://www.python.org/downloads/ and install.

Verify in your terminal:
```
python --version
```
Expected: Python 3.11.x or higher

### 2. Get a free Groq API key

1. Go to https://console.groq.com
2. Sign up (no credit card needed)
3. Click API Keys then Create API Key
4. Copy the key — it starts with gsk_

### 3. Create your .env file

In the root eco-prompt/ folder, create a file named .env (no extension) with this content:

```
GROQ_API_KEY=gsk_your_actual_key_here
SECRET_KEY=any-random-string-like-mysecret123
GROQ_MODEL=llama-3.1-8b-instant
```

Never commit this file to Git. It is already in .gitignore. Each teammate creates their own .env with their own Groq key.

### 4. Fix the httpx version (important for Windows)

If you get a TypeError about 'proxies', run this:
```
pip install httpx==0.27.2
```

And make sure services/chat-service/requirements.txt contains exactly:
```
flask==3.0.3
groq==0.9.0
httpx==0.27.2
python-dotenv==1.0.1
requests==2.32.3
```

---

## Method 1 — Run Without Docker

Use this for development. Fastest to start, no Docker needed. You need 3 terminal windows open at the same time.

### Terminal 1 — Start the Cleaner Service

```
cd services/cleaner-service
pip install -r requirements.txt
python cleaner.py
```

You should see: Running on http://127.0.0.1:5001

### Terminal 2 — Start the Analytics Service

```
cd services/analytics-service
pip install -r requirements.txt
python analytics.py
```

You should see: Running on http://127.0.0.1:5002

### Terminal 3 — Start the Chat Service

```
cd services/chat-service
pip install -r requirements.txt
python app.py
```

You should see: Running on http://127.0.0.1:5000

### Open the app

- Chat app:           http://localhost:5000
- Analytics dashboard: http://localhost:5002/dashboard

### Stop the app

Press Ctrl + C in each terminal window.

---

## Method 2 — Run With Docker Compose

Use this to share with teammates or simulate production. One command starts all 3 services. Requires Docker Desktop.

### Install Docker Desktop

Download from https://www.docker.com/products/docker-desktop and install.

- Windows: Enable WSL 2 when prompted during install
- Mac M1/M2/M3: Works natively, no extra steps
- Linux: Follow https://docs.docker.com/engine/install/

Verify Docker is installed:
```
docker --version
docker compose version
```
Both should return a version number. If not, revisit the install guide.

### First run — build and start everything

Make sure your .env file exists in the root folder, then run from the eco-prompt/ root:

```
docker compose up --build
```

This will:
- Download the Python base image from Docker Hub (first time only, takes 1-2 minutes)
- Build all 3 service containers from your source code
- Start them in order: cleaner first, then analytics, then chat
- Show combined live logs from all 3 in your terminal

Wait until you see all three of these lines appear:
```
eco-cleaner    | * Running on http://0.0.0.0:5001
eco-analytics  | * Running on http://0.0.0.0:5002
eco-chat       | * Running on http://0.0.0.0:5000
```

### Open the app

- Chat app:            http://localhost:5000
- Analytics dashboard: http://localhost:5002/dashboard

### Stop the app

Press Ctrl + C in the terminal, then:
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

# Check everything started correctly
docker compose ps

# Watch live logs
docker compose logs -f

# Stop when done
docker compose down
```

---

## Sharing Images With Teammates

Push your built images to Docker Hub so teammates can run the app with zero setup — no Python, no source code, just Docker and their own .env file.

### Step 1 — Create a free Docker Hub account

Go to https://hub.docker.com and sign up. Your username becomes part of the image name (e.g. johndoe/eco-chat).

### Step 2 — Log in and build

```
docker login

docker build -t YOUR_USERNAME/eco-cleaner:latest   ./services/cleaner-service
docker build -t YOUR_USERNAME/eco-analytics:latest ./services/analytics-service
docker build -t YOUR_USERNAME/eco-chat:latest      ./services/chat-service
```

### Step 3 — Push to Docker Hub

```
docker push YOUR_USERNAME/eco-cleaner:latest
docker push YOUR_USERNAME/eco-analytics:latest
docker push YOUR_USERNAME/eco-chat:latest
```

### Step 4 — Teammates pull and run

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

Do the same for analytics and chat. Then teammates only need to:

```
cp .env.example .env
# (fill in their own GROQ_API_KEY)
docker compose up
```

Docker pulls the images automatically. No Python, no pip install, no source code needed.

> Apple Silicon users (M1/M2/M3): If teammates are on Windows/Linux Intel machines, build for both platforms:
>
> docker buildx build --platform linux/amd64,linux/arm64 -t YOUR_USERNAME/eco-cleaner:latest --push ./services/cleaner-service
>
> docker buildx build --platform linux/amd64,linux/arm64 -t YOUR_USERNAME/eco-analytics:latest --push ./services/analytics-service
>
> docker buildx build --platform linux/amd64,linux/arm64 -t YOUR_USERNAME/eco-chat:latest --push ./services/chat-service

---

## Test Prompts — See the Savings

Use these prompts in the chat to demonstrate cleaning in action. After each one, a toast notification pops up showing the original vs cleaned text. Check http://localhost:5002/dashboard after all 5 to see the full session analytics.

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

### What to check on the dashboard after all 6 prompts

Open http://localhost:5002/dashboard. You should see:

- Total prompts cleaned: 6
- Tokens saved: roughly 120 tokens across the session
- Cost saved: around $0.000006 USD (tiny individually, but multiplied across millions of users this becomes significant)
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

**401 Unauthorized from Groq**

Your API key is wrong or missing. Check:
```
cat .env
```
The GROQ_API_KEY value must start with gsk_. Confirm it is active at https://console.groq.com.

---

**Analytics dashboard shows no data**

The dashboard auto-refreshes every 5 seconds. Send at least one message in the chat first, then check the dashboard. If still empty, click the refresh button and check the browser developer console (F12) for errors.

---

**Chat works but cleaning is not happening**

The chat service gracefully falls back if the cleaner service is unreachable — it still sends the raw prompt to the LLM. Make sure the cleaner is running (check Terminal 1 or docker compose ps) and try again.

---

## How Prompt Cleaning Works

The cleaner-service runs every prompt through 5 steps before it ever reaches the LLM:

1. Normalize — strips invisible unicode characters (zero-width spaces, BOM markers), collapses multiple spaces and newlines into single ones
2. Remove openers — strips conversational starters like "Hey!", "Hi there, so basically...", "Okay so...", "Alright,"
3. Strip filler phrases — removes around 25 patterns including: "can you please", "just", "basically", "I was wondering if", "um", "kind of", "you know", "honestly", "sort of", "thanks in advance", "if that's okay", and more
4. Deduplicate sentences — removes repeated sentences, common when users copy-paste the same paragraph twice into a prompt
5. Fix artifacts — cleans up orphaned punctuation and double spaces left after the removals

### How savings are calculated

All metrics are real calculations, not estimates:

| Metric | Formula |
|---|---|
| saved_tokens | count_tokens(original) minus count_tokens(cleaned) |
| saved_cost_usd | saved_tokens divided by 1,000,000 multiplied by $0.05 (Groq Llama 3.1 8B input pricing) |
| saved_energy_wh | saved_tokens divided by 1,000 multiplied by 0.001 Wh (MLPerf GPU inference average) |
| saved_co2_g | saved_energy_wh multiplied by 0.4 (US EPA average grid carbon intensity) |

Token count uses a words divided by 0.75 approximation (industry rule of thumb). For production accuracy, replace with tiktoken and the exact model tokenizer.

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
  Calls cleaner before every LLM request
  Calls analytics after every response
  Manages conversation history
       |               |
       v               v
cleaner-service    analytics-service
:5001              :5002
  NLP cleaning       Event store
  Token metrics      REST API
  Cost/energy calc   Dashboard HTML
       |
       v
Groq Cloud API (LLM)
```

### Why three separate services?

| Service | Container | Scales independently? | Why? |
|---|---|---|---|
| chat-service | eco-chat | Yes | User-facing, owns session state |
| cleaner-service | eco-cleaner | Yes | CPU-bound NLP, run many replicas |
| analytics-service | eco-analytics | Yes | I/O-bound, shard by user/org |

Each service has its own Dockerfile and requirements.txt with zero shared code, ready for independent Kubernetes Deployment objects.

---

## Kubernetes — Future Deployment

Each service maps 1:1 to a K8s Deployment + Service. The only change needed is swapping Docker network hostnames for K8s ClusterIP service names via environment variables. No code changes required.

```yaml
env:
  - name: GROQ_API_KEY
    valueFrom:
      secretKeyRef:
        name: groq-secret
        key: api-key
  - name: CLEANER_URL
    value: "http://eco-cleaner-svc:5001"
  - name: ANALYTICS_URL
    value: "http://eco-analytics-svc:5002"
```

Recommended migration path:
1. Push images to Docker Hub or a private registry like AWS ECR or GCP Artifact Registry
2. Write Deployment and ClusterIP Service YAML for each of the 3 services
3. Store GROQ_API_KEY in a K8s Secret — never hardcode secrets in YAML files
4. Expose chat-service externally via a LoadBalancer or Ingress
5. Replace in-memory analytics store with Redis or Postgres for persistence across pod restarts
