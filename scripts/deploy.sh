#!/usr/bin/env bash
# deploy.sh — builds all images, injects secrets from .env, and deploys to Kubernetes
set -e

# ── Load .env ─────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  echo "ERROR: .env file not found. Copy .env.example to .env and fill in your values."
  exit 1
fi

# Export values from .env (ignores comments and blank lines)
export $(grep -v '^\s*#' .env | grep -v '^\s*$' | xargs)

# ── Validate required vars ────────────────────────────────────────────────────
for var in GROQ_API_KEY SECRET_KEY POSTGRES_PASSWORD; do
  if [ -z "${!var}" ]; then
    echo "ERROR: $var is not set in .env"
    exit 1
  fi
done

# ── Build images ──────────────────────────────────────────────────────────────
echo "Building images..."
docker build -t ecoprompt/eco-cleaner:latest   ./services/cleaner-service
docker build -t ecoprompt/eco-analytics:latest ./services/analytics-service

docker build -t ecoprompt/eco-aimodel:latest -f ./services/aimodel-service/Dockerfile .

docker build -t ecoprompt/eco-chat:latest      ./services/chat-service

# ── Inject secrets into k8s/secret.yaml ──────────────────────────────────────
echo "Injecting secrets..."

POSTGRES_PASSWORD_B64=$(echo -n "$POSTGRES_PASSWORD" | base64)
GROQ_API_KEY_B64=$(echo -n "$GROQ_API_KEY" | base64)
SECRET_KEY_B64=$(echo -n "$SECRET_KEY" | base64)
DATABASE_URL_B64=$(echo -n "postgresql://${POSTGRES_USER:-eco}:${POSTGRES_PASSWORD}@postgres-svc:5432/${POSTGRES_DB:-eco}" | base64)

sed -i \
  -e "s|REPLACE_WITH_BASE64_GROQ_API_KEY|$GROQ_API_KEY_B64|" \
  -e "s|REPLACE_WITH_BASE64_SECRET_KEY|$SECRET_KEY_B64|" \
  -e "s|REPLACE_WITH_BASE64_POSTGRES_PASSWORD|$POSTGRES_PASSWORD_B64|" \
  -e "s|REPLACE_WITH_BASE64_DATABASE_URL|$DATABASE_URL_B64|" \
  k8s/secret.yaml

# ── Deploy ────────────────────────────────────────────────────────────────────
echo "Deploying to Kubernetes..."
kubectl apply -k k8s/

# ── Restore secret.yaml placeholders (so it's safe to commit) ────────────────
sed -i \
  -e "s|$GROQ_API_KEY_B64|REPLACE_WITH_BASE64_GROQ_API_KEY|" \
  -e "s|$SECRET_KEY_B64|REPLACE_WITH_BASE64_SECRET_KEY|" \
  -e "s|$POSTGRES_PASSWORD_B64|REPLACE_WITH_BASE64_POSTGRES_PASSWORD|" \
  -e "s|$DATABASE_URL_B64|REPLACE_WITH_BASE64_DATABASE_URL|" \
  k8s/secret.yaml

echo ""
echo "Done! Check status with:"
echo "  kubectl get all -n ecoprompt"
