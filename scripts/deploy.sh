#!/usr/bin/env bash
# deploy.sh — builds all images, injects secrets from .env, and deploys to Kubernetes
set -e

AUTO_INGRESS_PORT_FORWARD=0
if [ "${1:-}" = "--ingress-port-forward" ]; then
  AUTO_INGRESS_PORT_FORWARD=1
elif [ -n "${1:-}" ]; then
  echo "Usage: ./scripts/deploy.sh [--ingress-port-forward]"
  exit 1
fi

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

wait_for_ingress_admission_ready() {
  local timeout_seconds="${1:-180}"
  local deadline=$((SECONDS + timeout_seconds))

  while [ "$SECONDS" -lt "$deadline" ]; do
    endpoint_ip=$(kubectl get endpoints ingress-nginx-controller-admission -n ingress-nginx -o jsonpath='{.subsets[0].addresses[0].ip}' 2>/dev/null || true)
    if [ -n "$endpoint_ip" ]; then
      return 0
    fi
    sleep 3
  done

  return 1
}

# ── Ensure ingress-nginx controller exists before applying ingress resources ──
if ! kubectl get svc ingress-nginx-controller-admission -n ingress-nginx >/dev/null 2>&1; then
  echo "Installing ingress-nginx controller (first-time setup)..."
  kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.10.1/deploy/static/provider/cloud/deploy.yaml

  echo "Waiting for ingress controller and admission webhooks to be ready..."
  kubectl wait --namespace ingress-nginx --for=condition=Available deployment/ingress-nginx-controller --timeout=240s >/dev/null
  kubectl wait --namespace ingress-nginx --for=condition=complete job/ingress-nginx-admission-create --timeout=240s >/dev/null
  kubectl wait --namespace ingress-nginx --for=condition=complete job/ingress-nginx-admission-patch --timeout=240s >/dev/null
fi

echo "Waiting for ingress admission service endpoints..."
if ! wait_for_ingress_admission_ready 180; then
  echo "ERROR: Timed out waiting for ingress-nginx admission endpoints."
  exit 1
fi

# ── Deploy ────────────────────────────────────────────────────────────────────
echo "Deploying to Kubernetes..."
apply_succeeded=0
for attempt in 1 2 3; do
  if kubectl apply -k k8s/; then
    apply_succeeded=1
    break
  fi

  if [ "$attempt" -lt 3 ]; then
    echo "kubectl apply failed (attempt $attempt/3). Waiting before retry..."
    sleep 8
  fi
done

if [ "$apply_succeeded" -ne 1 ]; then
  echo "ERROR: Applying Kubernetes manifests failed."
  exit 1
fi

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

if [ "$AUTO_INGRESS_PORT_FORWARD" -eq 1 ]; then
  if ! kubectl get svc ingress-nginx-controller -n ingress-nginx >/dev/null 2>&1; then
    echo ""
    echo "WARNING: Ingress port-forward was requested, but ingress-nginx-controller service was not found."
    exit 0
  fi

  echo ""
  echo "Starting ingress port-forward in background..."
  nohup kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 8080:80 >/tmp/ecoprompt-ingress-port-forward.log 2>&1 &
  pf_pid=$!
  sleep 1

  if kill -0 "$pf_pid" >/dev/null 2>&1; then
    echo "  Ingress:   http://localhost:8080"
    echo "  Dashboard: http://localhost:8080/dashboard"
    echo "  PID: $pf_pid"
    echo "  Stop with: kill $pf_pid"
    echo "  Logs: /tmp/ecoprompt-ingress-port-forward.log"
  else
    echo "WARNING: Ingress port-forward process exited immediately. Port 8080 may already be in use."
    echo "Check logs: /tmp/ecoprompt-ingress-port-forward.log"
  fi
fi
