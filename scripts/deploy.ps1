# deploy.ps1 — builds all images, injects secrets from .env, and deploys to Kubernetes

# ── Load .env ─────────────────────────────────────────────────────────────────
if (-not (Test-Path ".env")) {
    Write-Error "ERROR: .env file not found. Copy .env.example to .env and fill in your values."
    exit 1
}

$env_vars = @{}
Get-Content ".env" | Where-Object { $_ -notmatch '^\s*#' -and $_ -match '=' } | ForEach-Object {
    $key, $value = $_ -split '=', 2
    $env_vars[$key.Trim()] = $value.Trim()
}

# ── Validate required vars ────────────────────────────────────────────────────
foreach ($var in @("GROQ_API_KEY", "SECRET_KEY", "POSTGRES_PASSWORD")) {
    if (-not $env_vars[$var]) {
        Write-Error "ERROR: $var is not set in .env"
        exit 1
    }
}

$GROQ_API_KEY      = $env_vars["GROQ_API_KEY"]
$SECRET_KEY        = $env_vars["SECRET_KEY"]
$POSTGRES_PASSWORD = $env_vars["POSTGRES_PASSWORD"]
$POSTGRES_USER     = if ($env_vars["POSTGRES_USER"]) { $env_vars["POSTGRES_USER"] } else { "eco" }
$POSTGRES_DB       = if ($env_vars["POSTGRES_DB"])   { $env_vars["POSTGRES_DB"] }   else { "eco" }

# ── Build images ──────────────────────────────────────────────────────────────
Write-Host "Building images..."
docker build -t ecoprompt/eco-cleaner:latest   ./services/cleaner-service
docker build -t ecoprompt/eco-analytics:latest ./services/analytics-service

docker build -t ecoprompt/eco-aimodel:latest -f ./services/aimodel-service/Dockerfile .

docker build -t ecoprompt/eco-chat:latest      ./services/chat-service

# ── Inject secrets ────────────────────────────────────────────────────────────
Write-Host "Injecting secrets..."

function To-Base64 ($s) { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($s)) }

$GROQ_B64     = To-Base64 $GROQ_API_KEY
$SECRET_B64   = To-Base64 $SECRET_KEY
$PG_PASS_B64  = To-Base64 $POSTGRES_PASSWORD
$DB_URL_B64   = To-Base64 "postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres-svc:5432/${POSTGRES_DB}"

$secret = Get-Content "k8s/secret.yaml" -Raw
$secret = $secret -replace "REPLACE_WITH_BASE64_GROQ_API_KEY",      $GROQ_B64
$secret = $secret -replace "REPLACE_WITH_BASE64_SECRET_KEY",         $SECRET_B64
$secret = $secret -replace "REPLACE_WITH_BASE64_POSTGRES_PASSWORD",  $PG_PASS_B64
$secret = $secret -replace "REPLACE_WITH_BASE64_DATABASE_URL",       $DB_URL_B64
$secret | Set-Content "k8s/secret.yaml" -NoNewline

# ── Deploy ────────────────────────────────────────────────────────────────────
Write-Host "Deploying to Kubernetes..."
kubectl apply -k k8s/

# ── Restore secret.yaml placeholders ─────────────────────────────────────────
$secret = Get-Content "k8s/secret.yaml" -Raw
$secret = $secret -replace $GROQ_B64,    "REPLACE_WITH_BASE64_GROQ_API_KEY"
$secret = $secret -replace $SECRET_B64,  "REPLACE_WITH_BASE64_SECRET_KEY"
$secret = $secret -replace $PG_PASS_B64, "REPLACE_WITH_BASE64_POSTGRES_PASSWORD"
$secret = $secret -replace $DB_URL_B64,  "REPLACE_WITH_BASE64_DATABASE_URL"
$secret | Set-Content "k8s/secret.yaml" -NoNewline

Write-Host ""
Write-Host "Done! Check status with:"
Write-Host "  kubectl get all -n ecoprompt"
